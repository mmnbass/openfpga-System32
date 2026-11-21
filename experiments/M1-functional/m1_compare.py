#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Compare two canonical M1 dumps (m1_ref.py / tb_m1_up / tb_m1_v2 output).

Usage: m1_compare.py <a> <b> [--label-a A --label-b B] [--unhalted]
  --no-instrs do not compare the retired-instruction count (checked separately)
  --unhalted  both runs stopped before HALT (a V2 trap after n instructions,
              compared with the reference after n instructions); the halted
              flags must still agree, but need not be 1
Compares, separately: R00-R31, PSW, the ordered data-write list, the halted
flag and the retired-instruction count. Prints one line per category and
the first few differences; exit 0 only if every category matches.
"""
import re
import sys


def parse(path):
    d = {"R": {}, "PSW": None, "W": [], "halted": None, "instrs": None}
    for line in open(path):
        line = line.strip()
        m = re.match(r"R(\d\d)=([0-9a-fx]{8})$", line)
        if m:
            d["R"][int(m.group(1))] = m.group(2)
            continue
        m = re.match(r"PSW=([0-9a-fx]{8})$", line)
        if m:
            d["PSW"] = m.group(1)
            continue
        if line.startswith("W addr="):
            d["W"].append(line)
            continue
        m = re.match(r"END halted=(\d) instrs=(\d+)", line)
        if m:
            d["halted"], d["instrs"] = int(m.group(1)), int(m.group(2))
    return d


def main():
    a, b = parse(sys.argv[1]), parse(sys.argv[2])
    la = sys.argv[sys.argv.index("--label-a") + 1] if "--label-a" in sys.argv else "a"
    lb = sys.argv[sys.argv.index("--label-b") + 1] if "--label-b" in sys.argv else "b"
    ok = True
    rdiff = [i for i in range(32) if a["R"].get(i) != b["R"].get(i)]
    if rdiff:
        parts = ["R%d %s=%s %s=%s" % (i, la, a["R"].get(i), lb, b["R"].get(i)) for i in rdiff[:6]]
        print("regs   : DIFF " + ", ".join(parts))
    else:
        print("regs   : match")
    ok &= not rdiff
    if a["PSW"] == b["PSW"]:
        print("psw    : match")
    else:
        print("psw    : DIFF %s=%s %s=%s" % (la, a["PSW"], lb, b["PSW"]))
    ok &= a["PSW"] == b["PSW"]
    wd = next((i for i, (x, y) in enumerate(zip(a["W"], b["W"])) if x != y), None)
    if wd is None and len(a["W"]) == len(b["W"]):
        print("writes : match (%d)" % len(a["W"]))
    else:
        ok = False
        i = wd if wd is not None else min(len(a["W"]), len(b["W"]))
        print("writes : DIFF at #%d (counts %s=%d %s=%d)" % (i, la, len(a["W"]), lb, len(b["W"])))
        print("         %s: %s" % (la, a["W"][i] if i < len(a["W"]) else "-"))
        print("         %s: %s" % (lb, b["W"][i] if i < len(b["W"]) else "-"))
    hs = "match" if a["halted"] == b["halted"] else "DIFF %s=%s %s=%s" % (la, a["halted"], lb, b["halted"])
    print("halted : %s (%s)" % (hs, a["halted"]))
    ok &= a["halted"] == b["halted"] and (a["halted"] == 1 or "--unhalted" in sys.argv)
    ins = "match" if a["instrs"] == b["instrs"] else "DIFF %s=%s %s=%s" % (la, a["instrs"], lb, b["instrs"])
    print("instrs : %s (%s)" % (ins, a["instrs"]))
    ok &= a["instrs"] == b["instrs"] or "--no-instrs" in sys.argv
    print("RESULT", "MATCH" if ok else "MISMATCH")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
