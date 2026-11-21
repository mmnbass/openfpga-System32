#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""R3a checks: compare a run log with the INDEPENDENT expectations of m1_r3.py (the manifest). Extends
m1_r2_check.py (lock-step records, event counters, privileged events, trap / wait / halt exits, CU records,
SIMCHECK) with the general-register write-event checks of docs/r3-movea-plan.md section 6 #2:

    GPREV on                                        the option was enabled (a log without it proves nothing)
    G n id=<reg> [be=<byte enables>] val=<v>        one GPR write EVENT of instruction n:
                                                    V2 (+gprev): every write at the register file's E3 port, be=f for a
                                                    word, whether or not the value changes, a restored write is 2 events;
                                                    corrected V60X (--gprev): every store, no byte enables
                                                    upstream: no write strobe, NOT checked (value changes only)
    C n dr= dw= priv= psw= gpr=<k>                  V2 with +gprev: k = the number of events of instruction n

Per instruction, the observed event list must equal the generator's expected list EXACTLY (register, value, byte
enables, multiplicity, order). Events of an instruction that never retired (a trap, a wait) are checked AFTER the
pipeline has drained (the bench's +drain): there must be none. Within each MOVEA instruction's own window the
counters dr = dw = priv = 0 (psw = 0 on V2), checked separately from the whole-program totals.

Library: check_ref3(man, log, kind) kind "ext" | "up"; check_v2r3(man, log, phase, run) run "std" | "ack" | "delay" |
"smc" | "smc_ack" | "smc_delay". Each returns a list of failure strings (empty = pass).
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m1_r2_check as C2  # noqa: E402


def parse3(text):
    d = C2.parse2(text)
    d["G"], d["BADG"], d["BADREC3"] = {}, [], []
    lines = text.splitlines()
    d["GPREV"] = any(l.strip() == "GPREV on" for l in lines)
    # R3a fixes 3: the marker is printed once, exactly (not a variant, not repeated)
    marks = [l for l in lines if l.startswith("GPREV")]
    if any(l != "GPREV on" for l in marks):
        d["BADREC3"].append(f"malformed GPREV record {next(l for l in marks if l != 'GPREV on')[:60]!r}")
    if len(marks) > 1:
        d["BADREC3"].append(f"{len(marks)} GPREV records")
    d["BADREC3"] += C2.C.event_order_errors(text, "G")
    for line in text.splitlines():
        if line.startswith("G "):
            m = re.match(r"G (\d+) id=(\d+)(?: be=([0-9a-f]))? val=([0-9a-f]{8})$", line)
            if m:
                d["G"].setdefault(int(m.group(1)), []).append(
                    (int(m.group(2)), int(m.group(4), 16)) if m.group(3) is None
                    else (int(m.group(2)), int(m.group(4), 16), int(m.group(3), 16)))
            else:
                d["BADG"].append(line)
    return d


def _fmt(ev):
    return "[" + ", ".join("(" + ",".join(f"{x:x}" if j else str(x) for j, x in enumerate(e)) + ")" for e in ev) + "]"


def _gevents(man, d, R, fails, v2):
    if not d["GPREV"]:
        fails.append("no 'GPREV on' line (run without +gprev / --gprev?): the GPR write events are not observed")
        return
    exp = man["exp_gevents"]
    for i in range(R):
        got = d["G"].get(i, [])
        want = [(e[0], e[1], 0xF) if v2 else (e[0], e[1]) for e in exp.get(str(i), [])]
        if got != want:
            fails.append(f"GPR write events of instruction {i}: got {_fmt(got)} expected {_fmt(want)}")
    for i in sorted(d["G"]):
        if i >= R:
            fails.append(f"GPR write events of instruction {i}, which did not retire: {_fmt(d['G'][i])}")
    if d["BADG"]:
        fails.append(f"unparsable G line {d['BADG'][0]!r}")


def _windows(man, d, R, fails, v2):
    """dr = dw = priv = 0 (and psw = 0 on V2) inside each MOVEA instruction's own window, and exactly one GPR event"""
    for i in man.get("movea_idx", []):
        if i >= R or i >= len(d["C"]):
            continue
        c = d["C"][i]
        for k in ("dr", "dw", "priv") + (("psw",) if v2 else ()):
            if c.get(k, 0) != 0:
                fails.append(f"MOVEA window (instruction {i}): {k}={c.get(k)} expected 0")


def check_ref3(man, text, kind):
    """V60X ("ext", corrected) or upstream ("up") on a positive program"""
    f = C2.check_ref2(man, text, kind)
    d = parse3(text)
    n = man["n_instrs"]
    f += d["BADREC3"]
    _windows(man, d, n, f, False)
    if kind == "ext":
        _gevents(man, d, n, f, False)
    return f


def check_v2r3(man, text, phase, run="std"):
    key = {"std": "v2", "ack": "v2_ack", "delay": "v2_delay", "smc": "v2_smc",
           "smc_ack": "v2_smc_ack", "smc_delay": "v2_smc_delay"}[run]
    ex = man.get(key)
    if ex is None:
        return [f"no V2 expectation ({key}) for this program"]
    m2 = dict(man)
    base_run = {"smc_ack": "smc", "smc_delay": "smc"}.get(run, run)
    if run in ("smc_ack", "smc_delay"):
        m2["v2_smc"] = ex
    f = C2.check_v2r2(m2, text, phase, base_run)
    d = parse3(text)
    f += d["BADREC3"]
    e = ex[phase]
    kind = e["kind"]
    R = man["n_instrs"] if kind == "halt" else (e["n"] if kind == "trap" else e["retired"])
    _gevents(man, d, R, f, True)
    _windows(man, d, R, f, True)
    if d["GPREV"]:
        # the per-record count of events (C ... gpr=k) agrees with the printed events, retired and unretired alike
        for c in d["C"][:R]:
            if c.get("gpr") != len(d["G"].get(c["n"], [])):
                f.append(f"counter record {c['n']}: gpr={c.get('gpr')} but {len(d['G'].get(c['n'], []))} G events")
        for c in d["CU"]:
            if c.get("gpr") != len(d["G"].get(c["n"], [])):
                f.append(f"unretired record {c['n']}: gpr={c.get('gpr')} but {len(d['G'].get(c['n'], []))} G events")
            if c.get("gpr", 0) != 0:
                f.append(f"unretired instruction {c['n']} wrote a general register {c['gpr']} time(s) (after the drain)")
        pu = man.get("exp_psw_upd", {})
        for c in d["C"][:R]:
            if c.get("psw") != pu.get(str(c["n"]), 0):
                f.append(f"counter record {c['n']}: psw={c.get('psw')} expected {pu.get(str(c['n']), 0)}")
    return f


def main():
    import json
    kind, mf, name, log = sys.argv[1:5]
    man = json.load(open(mf))[name]
    text = C2.C.read_log(log)          # R3a fixes 4/5: bytes, no newline translation, bad UTF-8 -> U+FFFD
    if kind in ("ext", "up"):
        fails = check_ref3(man, text, kind)
    else:
        ph = sys.argv[sys.argv.index("--phase") + 1]
        run = sys.argv[sys.argv.index("--run") + 1] if "--run" in sys.argv else "std"
        fails = check_v2r3(man, text, ph, run)
    if fails:
        print(f"FAIL {name}: " + "; ".join(fails))
        return 1
    print(f"ok {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
