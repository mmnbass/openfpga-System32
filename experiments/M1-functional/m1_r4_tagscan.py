#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""R4 tag scan (docs/r4-movea-pcrel-plan.md section 5.5 item 7, C7k): a word-level scan of the builder's mode tables and
control stores in all eight configurations (smc_test x smc_merged x reset_native) for the S-D tag (descriptor bit 11 =
ea_desc[11] = micro-condition 23, "deferred" in the RTL) and for every word that could consume the 0x88C base / size / step bits.

Reported per configuration:
  tagged     supported mode-table entries (not trap / trap_mode) whose descriptor has bit 11 set
  c23        control-store words that branch (NXT = 2) on condition 23, with the routine they sit in, their target and whether an
             operand() call (a call to an mstub*) precedes them inside that routine (the descriptor is re-latched at every
             mode dispatch, so a guard placed before the lookup would read the previous instruction's descriptor)
  live       words that set DEC_EA, any AGU_* field, MAR_LD, an AGU / AGU-step RES_SEL, or CONSUME 9..11

Expectations: --expect pre   (the pre-R4 builder): no tagged entry, no condition-23 word, nothing live, in all eight;
              --expect r4b   production: tagged == {0x0F2} (descriptor 0x88C), exactly two condition-23 words (the jmp guard -> trap_mode,
                             the movea branch -> movea_pcrel) each after an operand() call; --smc-test: none of either; live nothing in all eight.
Library: scan(p, sym, dtab, mtab) -> dict; problems(scan, smc_test, expect) -> list of violations.
CLI:  m1_r4_tagscan.py [--expect pre|r4b] [--json out.json]      (scans the LIVE builder module)
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0] / "V2-datapath-floor" / "sim"))
from microasm import FIELDS, RES  # noqa: E402

TAG_BIT = 11
COND_TAG = 23


def f(w, k):
    lsb, wd = FIELDS[k]
    return (w >> lsb) & ((1 << wd) - 1)


def scan(p, sym, dtab, mtab):
    trap, tmode = sym["trap"], sym["trap_mode"]
    supported = {i: v for i, v in mtab.items() if (v & 0xFFF) not in (trap, tmode)}
    tagged = sorted(i for i, v in supported.items() if (v >> 12) >> TAG_BIT & 1)
    # routine boundaries: sorted symbol addresses
    starts = sorted((a, n) for n, a in sym.items())
    def routine(ix):
        r = None
        for a, n in starts:
            if a <= ix:
                r = n
        return r
    names = {a: n for n, a in sym.items()}
    c23 = []
    for ix, w in enumerate(p):
        if f(w, "NXT") == 2 and (f(w, "COND") | (f(w, "COND_HI") << 4)) == COND_TAG:
            r = routine(ix)
            r0 = sym[r]
            after_operand = any(f(p[j], "NXT") == 3 and names.get(f(p[j], "JADDR"), "").startswith("mstub") for j in range(r0, ix))
            c23.append({"word": ix, "routine": r, "target": names.get(f(w, "JADDR"), f(w, "JADDR")), "cpol": f(w, "CPOL"),
                        "after_operand": after_operand})
    live = []
    for ix, w in enumerate(p):
        why = []
        if f(w, "DEC_EA"):
            why.append("DEC_EA")
        for k in ("AGU_BASE", "AGU_OFF", "AGU_IDX", "AGU_STEP", "MAR_LD"):
            if f(w, k):
                why.append(k)
        if f(w, "RES_SEL") in (RES["AGU"], RES["AGUSTEP"]):
            why.append("RES_SEL=AGU")
        if 9 <= f(w, "CONSUME") <= 11:
            why.append("CONSUME 9-11")
        if why:
            live.append({"word": ix, "why": why})
    return {"tagged": [hex(i) for i in tagged], "tag_descriptors": {hex(i): hex(supported[i] >> 12) for i in tagged},
            "c23": c23, "live": live, "words": len(p)}


def problems(s, smc_test, expect):
    bad = []
    if s["live"]:
        bad.append(f"words that could consume the descriptor's base/size/step bits: {s['live'][:3]}")
    if expect == "pre" or smc_test:
        if s["tagged"]:
            bad.append(f"tagged (bit 11) supported entries {s['tagged']}, expected none")
        if s["c23"]:
            bad.append(f"condition-23 words {s['c23']}, expected none")
        return bad
    if s["tagged"] != ["0xf2"]:
        bad.append(f"tagged entries {s['tagged']}, expected exactly ['0xf2']")
    elif s["tag_descriptors"]["0xf2"] != "0x88c":
        bad.append(f"mtab[0x0F2] descriptor {s['tag_descriptors']['0xf2']}, expected 0x88c")
    got = sorted((c["routine"], c["target"]) for c in s["c23"])
    if got != [("jmp", "trap_mode"), ("movea", "movea_pcrel")]:
        bad.append(f"condition-23 words {got}, expected exactly [('jmp','trap_mode'), ('movea','movea_pcrel')]")
    for c in s["c23"]:
        if not c["after_operand"]:
            bad.append(f"condition-23 word {c['word']} in {c['routine']} is not preceded by an operand() call")
        if c["cpol"] != 0:
            bad.append(f"condition-23 word {c['word']} in {c['routine']} has inverted polarity")
    return bad


def scan_all(B, expect):
    out, bad = {}, []
    for st in (False, True):
        for sm in (False, True):
            for rn in (False, True):
                key = f"smc_test={int(st)} smc_merged={int(sm)} reset_native={int(rn)}"
                p, sym, dtab, mtab = B.build(st, sm, rn)
                s = scan(p, sym, dtab, mtab)
                pr = problems(s, st, expect)
                out[key] = dict(s, problems=pr)
                bad += [f"{key}: {x}" for x in pr]
    return out, bad


def main():
    import m1_m11_microcode as B
    expect = sys.argv[sys.argv.index("--expect") + 1] if "--expect" in sys.argv else "r4b"
    out, bad = scan_all(B, expect)
    for k, s in out.items():
        print(f"{k}: {s['words']} words; tagged {s['tagged']}; c23 words {[(c['routine'], c['target']) for c in s['c23']]}; live words {len(s['live'])}"
              + ("" if not s["problems"] else f"  PROBLEMS {s['problems']}"))
    if "--json" in sys.argv:
        Path(sys.argv[sys.argv.index("--json") + 1]).write_text(json.dumps(out, indent=1))
    print(f"TAGSCAN expect={expect}: " + ("OK" if not bad else f"FAIL {bad[:4]}"))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
