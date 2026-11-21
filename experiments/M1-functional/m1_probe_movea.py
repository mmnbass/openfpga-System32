#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""probe_movea_value (docs/r3-movea-plan.md section 2 "Reference defect", section 6 infrastructure #1, R3a exit 3).

The base reference v60_ref.py (pinned, unmodified) decodes MOVEA's operand 1 as a VALUE; the manual, MAME and upstream
store the ADDRESS. The probe is the 7-byte program `movea.b /0x20, R1` (40 21 F3 20 00 00 00) + HALT with the byte 0x5A at
address 0x20. The documented discrepancy D1 is ENFORCED, not merely printed:

    v60_ref (m1_ref.py, no --ext):  R1 = 0x0000005A and dr = 1 (a data read of the target)
    upstream s32_v60 (tb_m1_up):    R1 = 0x00000020 and dr = 0

The discrepancy counts as reproduced ONLY IF both executions succeed (exit code 0, non-empty output, an END record) AND
exactly these values appear AND each run is COMPLETE and, validated independently against the full expected execution, has
nothing else different: the two retirement records (PC, opcode, per-instruction register / PSW effects), all counters, no
data writes, the full R00-R31 and PSW dump, the END record. Unrecognised or duplicate lines are failures. A crash, a timeout, a missing log, a different value, or an
unrelated difference is a FAILURE (exit 1), as is the discrepancy disappearing (the references agreeing).

  m1_probe_movea.py --sim <tb_m1_up sim> [--ref-script <m1_ref.py>] [--workdir DIR]     exit 0 = reproduced and enforced
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from m1_image import write  # noqa: E402
import m1_r1_check as C1  # noqa: E402     (the Verilator banner shapes and the byte decoding are shared with the evaluator)

PROGRAM = [0x40, 0x21, 0xF3, 0x20, 0x00, 0x00, 0x00, 0x00]      # movea.b /0x20, R1 ; HALT
TARGET, TARGET_BYTE = 0x20, 0x5A
EXPECT_REF = {"R01": 0x5A, "dr": 1}
EXPECT_UP = {"R01": 0x20, "dr": 0}


def image():
    img = bytearray(0x40)
    img[:len(PROGRAM)] = bytes(PROGRAM)
    img[TARGET] = TARGET_BYTE
    return bytes(img)


def kv_pairs(tokens, errors, what):
    """'k=v' tokens -> ({k: int}, [duplicated k]); a duplicated field is an error, never silently overwritten (dict(...) keeps the
    last value, which let `C 0 dr=1 dr=0 dw=0 priv=0` pass as dr=0)"""
    d, dup = {}, []
    for x in tokens:
        k, _, v = x.partition("=")
        if not v.lstrip("-").isdigit():
            errors.append(f"{what}: non-numeric field {x!r}")
            continue
        if k in d:
            dup.append(k)
        d[k] = int(v)
    if dup:
        errors.append(f"{what} has duplicate field(s) {sorted(set(dup))}")
    return d, dup


END_STRICT = re.compile(r"^END halted=[01] instrs=\d+(?: cycles=\d+)?(?: ce=\d+)?$")
C_STRICT = re.compile(r"^C \d+ dr=\d+ dw=\d+ priv=\d+$")


def parse(text):
    """strict parse of one run's output. Every line must be a recognised record or one of the four Verilator banner shapes (each at
    most once); duplicates and anything unrecognised are collected in d['errors'], never silently dropped.
    R3a Codex-r5 fixes (should-fix 4 and 3): no exception any more. A `- ` line that is not a banner shape (`- FATAL: ...`,
    `- TRAP mode ...`), a blank line, a control character (CR, tab, NUL, ... ; the text is split on the newline only, never on
    str.splitlines), a non-ASCII character outside a banner path and a missing final newline are errors."""
    d = {"R": {}, "PSW": None, "W": [], "END": None, "C": {}, "L": {}, "errors": []}
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    elif lines:
        d["errors"].append(f"the output does not end with a newline (truncated?): last line {lines[-1][:40]!r}")
    seen_banner = set()
    for line in lines:
        hit = [i for i, b_ in enumerate(C1.BANNER_RES) if b_.match(line)]
        if hit:
            if hit[0] in seen_banner:
                d["errors"].append(f"duplicate Verilator banner line {line[:60]!r}")
            seen_banner.add(hit[0])
            continue
        if re.search(r"[^\x20-\x7e]", line):
            d["errors"].append(f"control or non-ASCII character in the line {line[:60]!r} (CR, tab, NUL, ... are never printed)")
            continue
        if not line.strip():
            d["errors"].append("blank line (no emitter prints one)")
            continue
        m = re.match(r"^R(\d\d)=([0-9a-f]{8})$", line)
        if m:
            if int(m.group(1)) in d["R"]:
                d["errors"].append(f"duplicate register dump line {line}")
            d["R"][int(m.group(1))] = int(m.group(2), 16)
        elif re.match(r"^PSW=[0-9a-f]{8}$", line):
            if d["PSW"] is not None:
                d["errors"].append("duplicate PSW dump line")
            d["PSW"] = int(line[4:], 16)
        elif line.startswith("W "):
            d["W"].append(line)
        elif line.startswith("END "):
            if d["END"] is not None:
                d["errors"].append("duplicate END record")
            kv, dup = kv_pairs(line.split()[1:], d["errors"], "END record")
            if not END_STRICT.match(line):          # R3a fixes 3: the whole line, in the printed grammar (no unknown or reordered field)
                d["errors"].append(f"malformed END record {line[:60]!r}")
            d["END"] = {k: str(v) for k, v in kv.items()}
        elif re.match(r"^C \d+( \w+=\d+)+$", line):
            t = line.split()
            if int(t[1]) in d["C"]:
                d["errors"].append(f"duplicate counter record for instruction {t[1]}")
            elif int(t[1]) != len(d["C"]):
                d["errors"].append(f"counter record C {t[1]} out of order (position {len(d['C'])}): retirement must be strictly 0, 1, ...")
            kv, dup = kv_pairs(t[2:], d["errors"], f"counter record C {t[1]}")
            if not C_STRICT.match(line):            # R3a fixes 3: exactly `C n dr= dw= priv=`, each once, in that order
                d["errors"].append(f"malformed counter record {line[:60]!r}")
            d["C"][int(t[1])] = kv
        elif re.match(r"^L \d+ pc=[0-9a-f]{8} op=[0-9a-f]{2}( (R\d\d|PSW)=[0-9a-f]{8})*$", line):
            t = line.split()
            n = int(t[1])
            if n in d["L"]:
                d["errors"].append(f"duplicate lock-step record {n}")
            elif n != len(d["L"]):
                d["errors"].append(f"lock-step record L {n} out of order (position {len(d['L'])}): retirement must be strictly 0, 1, ...")
            keys = [x.split("=")[0] for x in t[4:]]
            if len(keys) != len(set(keys)):
                d["errors"].append(f"lock-step record {n} has a duplicate register/PSW field")
            d["L"][n] = {"pc": int(t[2][3:], 16), "op": int(t[3][3:], 16), "regs": tuple(sorted(t[4:]))}
        else:
            d["errors"].append(f"unrecognised line {line[:60]!r}")
    return d


# the complete expected execution of the probe, per run (independent of the other run): two retirements (the MOVEA at 0, the
# HALT at 7), the MOVEA's single register write, no PSW / memory / privileged effect, zero counters but the MOVEA's data reads
EXPECT_STREAM = {0: {"pc": 0x00, "op": 0x40}, 1: {"pc": 0x07, "op": 0x00}}


def expected_for(which):
    e = EXPECT_REF if which == "v60_ref" else EXPECT_UP
    return {"L": {0: (e["R01"],), 1: ()}, "C": {0: {"dr": e["dr"], "dw": 0, "priv": 0}, 1: {"dr": 0, "dw": 0, "priv": 0}}, "R01": e["R01"]}


def full_check(name, d):
    """independent complete validation of one run against its own expectation -> list of problems"""
    e = expected_for(name)
    p = [f"{name}: {x}" for x in d["errors"]]
    if sorted(d["L"]) != [0, 1]:
        p.append(f"{name}: retirement records {sorted(d['L'])}, expected exactly [0, 1]")
    for n, want in EXPECT_STREAM.items():
        r = d["L"].get(n)
        if r is None:
            continue
        if (r["pc"], r["op"]) != (want["pc"], want["op"]):
            p.append(f"{name}: instruction {n} pc={r['pc']:08x} op={r['op']:02x}, expected pc={want['pc']:08x} op={want['op']:02x}")
        regs = tuple(sorted("R%02d=%08x" % (1, v) for v in e["L"][n]))
        if r["regs"] != regs:
            p.append(f"{name}: instruction {n} register/PSW effects {list(r['regs'])}, expected {list(regs)}")
    if sorted(d["C"]) != [0, 1]:
        p.append(f"{name}: counter records for instructions {sorted(d['C'])}, expected exactly [0, 1]")
    for n, want in e["C"].items():
        if n in d["C"] and d["C"][n] != want:
            p.append(f"{name}: counters of instruction {n} {d['C'][n]}, expected {want}")
    if d["W"]:
        p.append(f"{name}: data writes {d['W']}, expected none")
    if sorted(d["R"]) != list(range(32)):
        p.append(f"{name}: register dump has {len(d['R'])} registers, expected R00..R31")
    for k, v in d["R"].items():
        want = e["R01"] if k == 1 else 0
        if v != want:
            p.append(f"{name}: final R{k:02d} = {v:#010x}, expected {want:#010x}")
    if d["PSW"] != 0x10000000:
        p.append(f"{name}: final PSW {d['PSW'] if d['PSW'] is None else format(d['PSW'], '#010x')}, expected 0x10000000")
    en = d["END"]
    if not en or en.get("halted") != "1" or en.get("instrs") != "2":
        p.append(f"{name}: no clean END record (halted=1 instrs=2): {en}")
    return p


def evaluate(ref, up):
    """ref / up: (returncode, stdout). -> (ok, why). D1 holds only when BOTH runs succeed and each run, validated on its own
    against the complete expected execution, shows exactly its documented MOVEA value / data-read count; the only permitted
    difference between the runs is therefore v60_ref R1=0x5A dr=1 against upstream R1=0x20 dr=0 at the MOVEA."""
    for name, (rc, out) in (("v60_ref", ref), ("upstream", up)):
        if rc != 0:
            return False, f"{name} run failed (exit {rc})"
        if not out or not out.strip():
            return False, f"{name} run produced no output"
    a, b = parse(ref[1]), parse(up[1])
    for name, d in (("v60_ref", a), ("upstream", b)):          # R3a Codex-r5 fixes: a malformed line is reported as such, before any derived "no END record"
        if d["errors"]:
            return False, "unrecognised, malformed or duplicate record(s): " + " | ".join(f"{name}: {e}" for e in d["errors"])
    for name, d in (("v60_ref", a), ("upstream", b)):
        if not d["END"] or d["END"].get("halted") != "1" or d["END"].get("instrs") != "2":
            return False, f"{name}: no clean END record (halted=1 instrs=2): {d['END']}"
        if 0 not in d["C"] or 1 not in d["C"] or 1 not in d["R"]:
            return False, f"{name}: missing counter or register records"
    problems = []
    if a["R"][1] != EXPECT_REF["R01"]:
        problems.append(f"v60_ref R1 = {a['R'][1]:#010x}, expected {EXPECT_REF['R01']:#010x}")
    if a["C"][0].get("dr") != EXPECT_REF["dr"]:
        problems.append(f"v60_ref dr = {a['C'][0].get('dr')}, expected {EXPECT_REF['dr']}")
    if b["R"][1] != EXPECT_UP["R01"]:
        problems.append(f"upstream R1 = {b['R'][1]:#010x}, expected {EXPECT_UP['R01']:#010x}")
    if b["C"][0].get("dr") != EXPECT_UP["dr"]:
        problems.append(f"upstream dr = {b['C'][0].get('dr')}, expected {EXPECT_UP['dr']}")
    if problems:
        if a["R"][1] == b["R"][1] and a["C"][0].get("dr") == b["C"][0].get("dr"):
            return False, "the references now AGREE (D1 no longer reproduces): " + "; ".join(problems)
        return False, "unexpected values: " + "; ".join(problems)
    unrelated = full_check("v60_ref", a) + full_check("upstream", b)
    if unrelated:
        return False, "unrelated difference(s) / incomplete records: " + " | ".join(unrelated)
    return True, "v60_ref R1=0x0000005a dr=1; upstream R1=0x00000020 dr=0; nothing else differs (both runs complete: 2 retirements, 32 registers, PSW, counters, no writes)"


def default_runners(sim, ref_script=None):
    ref_script = Path(ref_script) if ref_script else HERE / "m1_ref.py"

    def run(cmd):
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=120)       # bytes: text=True would turn CRLF into LF before parse() sees it
            return r.returncode, C1.decode_log(r.stdout) + C1.decode_log(r.stderr)
        except subprocess.TimeoutExpired:
            return -1, ""
        except OSError as e:
            return -2, str(e)
    return {"ref": lambda binp, hexp: run([sys.executable, str(ref_script), "--lockstep", "--counters", str(binp)]),
            "up": lambda binp, hexp: run([str(sim), f"+img={hexp}", "+maxcyc=200000", "+lockstep", "+counters"])}


def run(workdir, runners):
    """-> (ok, why, detail)"""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    write(image(), str(workdir / "probe_movea_value"))
    binp, hexp = workdir / "probe_movea_value.bin", workdir / "probe_movea_value.hex"
    ref, up = runners["ref"](binp, hexp), runners["up"](binp, hexp)
    ok, why = evaluate(ref, up)
    return ok, why, {"ref_rc": ref[0], "up_rc": up[0]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", required=True)
    ap.add_argument("--ref-script")
    ap.add_argument("--workdir", default=None)
    a = ap.parse_args()
    wd = a.workdir or str(Path(a.sim).resolve().parent / "probe_movea_value")
    ok, why, _ = run(wd, default_runners(a.sim, a.ref_script))
    print(f"probe_movea_value: {'D1 reproduces, as documented' if ok else 'FAIL'}: {why}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
