#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""R2a checks: compare a run log with the INDEPENDENT expectations of m1_r2.py (the manifest). Extends
m1_r1_check.py (lock-step records, event counters, trap/wait/halt exits, CU records, SIMCHECK) with the
privileged-state checks of docs/r2-ldpr-plan.md section 9 #3:

    P n id=<regID> [be=<byte enables>] val=<v>    one privileged-write EVENT of instruction n (+privev):
                                                  V2: every E3 write (be=f for a word); V60X: every store;
                                                  upstream: VALUE CHANGES only (no write strobe)
    PR id=<regID> val=<v>                         final privileged-register dump (+privev)

Per instruction, the observed event list must equal the generator's expected list EXACTLY (IDs, values, byte
enables, multiplicity, order): that is the "no other privileged register is written" assertion. Upstream is
compared with the independently derived value-change set (exp_events_up): an unchanged-value write is
invisible there, so those events are verified on V2 and V60X only. A V2 instruction that never retired (a
trap or a wait) must have no events at all, and no PSW update is allowed on an LDPR (C psw=0).

Library: check_ref2(man, log, kind) kind "ext" | "up"; check_v2r2(man, log, phase, run) run "std" | "ack" |
"delay" | "smc". Each returns a list of failure strings (empty = pass).
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m1_r1_check as C  # noqa: E402

PRIV_RESET = {7: 0x70, 8: 0xE000, 9: 0x6000, 15: 0xF002}


def parse2(text):
    d = C.parse(text)
    d["P"], d["PR"], d["BADREC"] = {}, {}, []
    for line in text.splitlines():
        if line.startswith("P "):
            m = re.match(r"P (\d+) id=(\d+)(?: be=([0-9a-f]))? val=([0-9a-f]{8})$", line)
            if m:
                d["P"].setdefault(int(m.group(1)), []).append(
                    (int(m.group(2)), int(m.group(4), 16)) if m.group(3) is None
                    else (int(m.group(2)), int(m.group(4), 16), int(m.group(3), 16)))
            else:
                d.setdefault("BADP", []).append(line)
        elif line.startswith("PR ") or line == "PR":
            # R3a fixes 3: a malformed, out-of-range or repeated PR record is an error (it used to be ignored or overwritten)
            m = re.match(r"PR id=(\d+) val=([0-9a-f]{8})$", line)
            if not m or int(m.group(1)) > 31:
                d["BADREC"].append(f"malformed final privileged dump line {line[:60]!r}")
            elif int(m.group(1)) in d["PR"]:
                d["BADREC"].append(f"duplicate final privileged dump line PR id={m.group(1)}")
            else:
                d["PR"][int(m.group(1))] = int(m.group(2), 16)
    d["BADREC"] += C.event_order_errors(text, "P")
    return d


def _events(man, d, n, key, fails, v2=False):
    exp = man[key]
    for i in range(n):
        got = d["P"].get(i, [])               # (id, val) for V60X / upstream, (id, val, be) for V2
        if v2:
            want = [(e[0], e[1], 0xF) for e in exp.get(str(i), [])]
        else:
            want = [(e[0], e[1]) for e in exp.get(str(i), [])]
        if got != want:
            fails.append(f"privileged events of instruction {i}: got {_fmt(got)} expected {_fmt(want)}")
    for i in sorted(d["P"]):
        if i >= n:
            fails.append(f"privileged events of instruction {i}, which did not retire: {_fmt(d['P'][i])}")
    if d.get("BADP"):
        fails.append(f"unparsable P line {d['BADP'][0]!r}")


def _fmt(ev):
    return "[" + ", ".join("(" + ",".join(f"{x:x}" if j else str(x) for j, x in enumerate(e)) + ")" for e in ev) + "]"


def _final_priv(man, d, fails, ids=None, expect_reset=True):
    fp = {int(k): v for k, v in man["final_priv"].items()}
    if not d["PR"]:
        fails.append("no PR dump (run without +privev?)")
        return
    for i in sorted(set(ids if ids is not None else d["PR"]) | set(fp)):     # every expected-written ID must be dumped
        if i not in d["PR"]:
            fails.append(f"PR dump has no ID {i}")
            continue
        want = fp.get(i, PRIV_RESET.get(i, 0)) if (expect_reset or i in fp) else None
        if want is not None and d["PR"][i] != want:
            fails.append(f"final privileged register {i}: {d['PR'][i]:08x} expected {want:08x}")


def check_ref2(man, text, kind):
    """V60X ("ext") or upstream ("up") on a positive program."""
    m2 = dict(man)
    m2["exp_counters"] = man["exp_counters"] if kind == "ext" else man["exp_counters_up"]
    m2["ref_counters"] = {}
    f = C.check_ref(m2, text, ref=False)
    d = parse2(text)
    n = man["n_instrs"]
    f += d["BADREC"]
    _events(man, d, n, "exp_events" if kind == "ext" else "exp_events_up", f)
    # upstream resets every register to the same values as V60X; both must end at reset value / expected
    _final_priv(man, d, f)
    return f


EL_MASK = 0x03000000


def _norm_el(text, el):
    """Remove the forced EL (PSW[25:24]) from every PSW value of a log after checking it IS the forced EL; a local
    transformation, no shared state. Returns (normalised text, failures)."""
    bad = []

    def fix(m):
        v = int(m.group(2), 16)
        if (v & EL_MASK) != (el << 24):
            bad.append(f"PSW {v:08x} does not carry the forced EL {el}")
        return m.group(1) + f"{v & ~EL_MASK:08x}"
    return re.sub(r"(\bPSW=)([0-9a-f]{8})", fix, text), bad


def check_v2r2(man, text, phase, run="std"):
    key = {"std": "v2", "ack": "v2_ack", "delay": "v2_delay", "smc": "v2_smc"}[run]
    ex = man.get(key)
    if ex is None:
        return [f"no V2 expectation ({key}) for this program"]
    m2 = dict(man)
    m2["v2"] = ex
    m2["v2_ack"] = None
    m2["jmp_idx"] = None
    el = man.get("el", 0)
    pre = []
    if el:      # compare with the EL removed on both sides (the inherited replay assumes EL 0); EL itself checked in _norm_el
        text, pre = _norm_el(text, el)
        m2["exp_L"] = [re.sub(r"PSW=([0-9a-f]{8})", lambda m: "PSW=%08x" % (int(m.group(1), 16) & ~EL_MASK), l) for l in man["exp_L"]]
        m2["final_psw"] = man["final_psw"] & ~EL_MASK
    f = pre + C.check_v2(m2, text, phase, False)
    d = parse2(text)
    e = ex[phase]
    kind = e["kind"]
    R = man["n_instrs"] if kind == "halt" else (e["n"] if kind == "trap" else e["retired"])
    if kind == "trap" and (not d["END"] or d["END"].get("instrs") != str(R)):
        f.append(f"END instrs={d['END'] and d['END'].get('instrs')} expected {R} at the trap")
    # counter records must carry the psw field (a missing field is not a zero)
    for c in d["C"] + d["CU"]:
        if "psw" not in c:
            f.append(f"counter record {c['n']} has no psw= field")
            break
    f += d["BADREC"]
    _events(man, d, R, "exp_events", f, v2=True)
    for c in d["CU"]:                      # an instruction that started but never retired updated no PSW (R1 checked dr/dw/priv)
        if c.get("psw", 0) != 0:
            f.append(f"unretired instruction {c['n']} updated the PSW {c['psw']} time(s)")
    for i in set(man.get("ldpr_idx", [])) | set(man.get("wr_idx", [])):
        if i < R and i < len(d["C"]) and d["C"][i].get("psw", 0) != 0:
            f.append(f"the privileged write (instruction {i}) updated the PSW {d['C'][i]['psw']} time(s)")
    if kind == "halt":
        _final_priv(man, d, f, ids=[int(k) for k in man["final_priv"]], expect_reset=False)
    return f


def main():
    import json
    kind, mf, name, log = sys.argv[1:5]
    man = json.load(open(mf))[name]
    text = C.read_log(log)          # R3a fixes 4/5: bytes, no newline translation, bad UTF-8 -> U+FFFD
    if kind in ("ext", "up"):
        fails = check_ref2(man, text, kind)
    else:
        ph = sys.argv[sys.argv.index("--phase") + 1]
        run = sys.argv[sys.argv.index("--run") + 1] if "--run" in sys.argv else "std"
        fails = check_v2r2(man, text, ph, run)
    if fails:
        print(f"FAIL {name}: " + "; ".join(fails))
        return 1
    print(f"ok {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
