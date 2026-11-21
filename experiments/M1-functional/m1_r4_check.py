#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""R4a checks: compare a run log with the INDEPENDENT expectations of m1_r4.py (the manifest). Extends m1_r3_check.py (lock-step records,
GPR write events, counters, privileged events, trap / wait / halt exits, CU records, SIMCHECK) with the plan's section 6.2 rule R4-NR
("no resolved raw displacement") for the programs that carry a landing-pad record (manifest key "nr"):

    for the instruction expected to TRAP: no `L` (retire) record at any landing pad's address, no pad sentinel and no
    candidate address (the raw displacement, start + disp, next-PC + disp) in any register at the end, and no instruction-stream redirect
    other than the ones the RETIRED path legitimately made (REDIRECTS ... instr=N must equal the manifest's count of retired jmp / br8).
    The generic checks already cover the rest of R4-NR: no GPR / PSW / privileged event of the trapping instruction (its CU record is
    all zero), no data read / write, END instrs = the retired count, the final register file equal to the independent state.

Library: check_ref4 = m1_r3_check.check_ref3; check_v2r4(man, log, phase, run) -> failure list; check_nr(man, log) -> failure list.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m1_r1_check as C1  # noqa: E402
import m1_r3_check as C3  # noqa: E402

check_ref4 = C3.check_ref3


def check_nr(man, text):
    nr = man.get("nr")
    if not nr:
        return []
    f = []
    pads = {int(v) for v in nr["pads"].values()}
    sent = {int(v) for v in nr["sentinels"].values()}
    cand = {int(v) & 0xFFFFFFFF for v in nr["candidates"]}
    for line in text.splitlines():
        m = re.match(r"L (\d+) pc=([0-9a-f]{8})", line)
        if m and int(m.group(2), 16) in pads:
            f.append(f"R4-NR: an instruction retired at landing pad {m.group(2)} (record {m.group(1)}): a redirect used the displacement")
        m = re.match(r"R(\d+)=([0-9a-f]{8})$", line)
        if m:
            v = int(m.group(2), 16)
            if v in sent:
                f.append(f"R4-NR: register R{m.group(1)} = {v:08x} is a pad sentinel (a pad executed)")
            if v in cand and nr.get("check_regs", True):
                f.append(f"R4-NR: register R{m.group(1)} = {v:08x} equals a resolved-address candidate")
        m = re.match(r"REDIRECTS reset=\d+ instr=(\d+) ", line)
        if m and int(m.group(1)) != nr.get("redirects", 0):
            f.append(f"R4-NR: {m.group(1)} instruction redirects, expected {nr.get('redirects', 0)} (the retired jmp / br8 only)")
    return f


def check_v2r4(man, text, phase, run="std"):
    return C3.check_v2r3(man, text, phase, run) + check_nr(man, text)


def main():
    import json
    kind, mf, name, log = sys.argv[1:5]
    man = json.load(open(mf))[name]
    text = C1.read_log(log)
    if kind in ("ext", "up"):
        fails = check_ref4(man, text, kind)
    else:
        ph = sys.argv[sys.argv.index("--phase") + 1]
        run = sys.argv[sys.argv.index("--run") + 1] if "--run" in sys.argv else "std"
        fails = check_v2r4(man, text, ph, run)
    if fails:
        print(f"FAIL {name}: " + "; ".join(fails))
        return 1
    print(f"ok {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
