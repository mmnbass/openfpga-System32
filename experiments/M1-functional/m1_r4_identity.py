#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""R4b default-output identity (docs/r4-movea-pcrel-plan.md section 6.5): the V2 benches run every existing program with the PRE-R4 microcode
(the frozen fixture's images, built by m1_r4_fixbuilder.py) and with the R4b microcode (the live builder's images), same benches, default
plusargs (+lockstep), four fetch configurations (production, smc, smcm and S0 images):

  * pairs whose normalised output is byte-identical (the Verilator banner `- ` lines removed): identical
  * the others must be ENUMERATED by one of three classes, each asserted:
      pcrel      a program that executes the PC-relative MOVEA (a R4 program with pcrel_idx, or one of the three superseded R3 programs):
                 the pre-R4 log traps `mode` there, the new log executes it: differs by design
      timing     a program that retires an /abs MOVEA (op 0x40) or a JMP (op 0xd6): ARCHITECTURAL identity (every L / P / G / W / R / PSW / PR
                 record, the TRAP kind and PC, END halted and instrs, the C / CU counters' dr dw priv psw gpr fields, SIMCHECK) and a cycle delta
                 (END cycles) that equals the number of such instructions retired (one added branch word each), measured per program
      f2_operand a R4 negative program whose trapping instruction carries the mode byte F2 (a descriptor consumer given disp32[PC]): it traps in
                 BOTH microcodes with the same kind at the same PC (architectural identity); the new tagged descriptor passes the mode lookup
                 and the consumer's own guard traps, so END cycles differ
      anything else: a FAILURE
  the report lists the classes with counts and the cycle-delta histogram.

  m1_r4_identity.py <pre-mc-dir> <new-mc-dir> <run-dir> <out.json>      (<run-dir>: the run_r4.sh dir with the benches, progs, r3progs)
  <pre-mc-dir> / <new-mc-dir> hold mc, mc.smc, mc.smcm, mc.s0 (the same names run_r4.sh writes)
"""
import json
import os
import re
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path.home() / "ga2-pocket-research"
CFGS = ["", "+memwait=2", "+memwait=5 +memjit=4", "+wide"]
SUPERSEDED_R3 = {"n3_m0_f2", "n6_mode_bit0", "a10_s0_prefix_trap"}


def sym(mc):
    return {l.split("=")[0]: l.split("=")[1] for l in (mc / "m1_symbols.txt").read_text().split()}


def run(sim, mc, args):
    s = sym(mc)
    a = [str(sim)] + args + [f"+mc={mc}", f"+haltpc={s['halt']}", f"+trappc={s['trap']}", f"+trapmodepc={s['trap_mode']}", f"+trapimmpc={s['trap_immdest']}", "+lockstep"]
    o = subprocess.run(a, capture_output=True, text=True)
    return "\n".join(l for l in (o.stdout + o.stderr).splitlines() if not l.startswith("- "))


def arch(text):
    """the architectural content of a log: everything but cycle counts and cost counters"""
    out = []
    for l in text.splitlines():
        if l.startswith(("COUNTERS", "REDIRECTS", "DRAIN", "MEMDRAIN")):
            continue
        if re.match(r"^MEMSTAT read\s+(rom|rom-mirror)\s+\d+$", l):      # instruction-prefetch read counts depend on timing; writes / other regions stay compared
            continue
        l = re.sub(r"^END (.*?) cycles=\d+", r"END \1", l)
        l = re.sub(r"( ce=\d+)", "", l) if l.startswith("END") else l
        out.append(l)
    return out


def cycles(text):
    m = re.search(r"^END .* cycles=(\d+)", text, re.M)
    return int(m.group(1)) if m else None


def retired_ops(text):
    return [m.group(1) for m in re.finditer(r"^L \d+ pc=[0-9a-f]+ op=([0-9a-f]{2})", text, re.M)]


def has_f2(m, tag):
    """an R4 negative program whose trapping instruction has the byte 0xF2 among its first 12 bytes"""
    if tag != "r4" or m["cls"] != "neg" or not m.get("bytes") or m.get("trap_pc") is None:
        return False
    return any(m["bytes"].get(f"{(m['trap_pc'] + i) & 0xFFFFFFFF:08x}") == 0xF2 for i in range(12))


def main():
    pre, new, rd, outj = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
    assert not outj.exists()
    jobs = []
    man4 = json.load(open(rd / "progs/r4_manifest.json"))
    man3 = json.load(open(rd / "r3progs/r3_manifest.json"))
    for tag, man, pd in (("r4", man4, rd / "progs"), ("r3", man3, rd / "r3progs")):
        for n, m in man.items():
            if m["mc"] != "prod":
                continue
            for cfg in CFGS:
                if m["where"] == "s0":
                    jobs.append((f"{tag}:{n}", rd / "obj_s0v2/sim", "mc.s0", [f"+rom={pd}/{n}.rom.hex", "+maxinstr=1000", "+maxcyc=400000"] + cfg.split(),
                                 (n in SUPERSEDED_R3 and tag == "r3") or (tag == "r4" and bool(m["pcrel_idx"])), has_f2(m, tag)))
                else:
                    for mcn in ("mc", "mc.smc", "mc.smcm"):
                        if mcn != "mc" and (m.get("v2_smc") is None or "jmp" in m["tags"]):
                            continue
                        jobs.append((f"{tag}:{n}[{mcn}]", rd / "obj_v2/sim", mcn, [f"+img={pd}/{n}.hex", "+maxcyc=400000"] + cfg.split(),
                                     (n in SUPERSEDED_R3 and tag == "r3") or (tag == "r4" and bool(m["pcrel_idx"])), has_f2(m, tag)))
    extra = [ROOT / "build/M1/r1b/run/progs", ROOT / "build/M1/r2b/run/progs", ROOT / "build/M1/r3b/reg/m11/progs", ROOT / "build/M1/r3b/reg/m12/progs"]
    for d in extra:
        tag = d.parent.name + "/" + d.name if "reg" in str(d) else d.parent.name
        for f in sorted(d.glob("*.hex")):
            if f.name.endswith(".rom.hex") or not f.with_suffix(".bin").exists():
                continue
            for cfg in (CFGS if "reg" not in str(d) else ["", "+wide"]):
                jobs.append((f"{tag}:{f.stem}", rd / "obj_v2/sim", "mc", [f"+img={f}", "+maxcyc=400000"] + cfg.split(), False, False))

    if os.environ.get("IDENT_ONLY"):      # re-evaluate only the programs named (one per line: `<name> <args...>`) in this file, e.g. a previous run's unexpected list
        only = {l.split(" ")[0] for l in open(os.environ["IDENT_ONLY"]).read().splitlines() if l.strip()}
        jobs = [j for j in jobs if j[0] in only]

    def one(j):
        name, sim, mcn, args, is_pc, f2 = j
        a0, a1 = run(sim, pre / mcn, args), run(sim, new / mcn, args)
        return j, a0, a1
    ident = 0
    classes = {"pcrel": [], "timing": [], "f2_operand": [], "unexpected": []}
    per_program = []
    deltas = Counter()
    bad_delta = []
    with ThreadPoolExecutor(4) as ex:
        for j, a0, a1 in ex.map(one, jobs):
            name, sim, mcn, args, is_pc, f2 = j
            if a0 == a1:
                ident += 1
                continue
            if is_pc:
                classes["pcrel"].append(name + " " + " ".join(args[1:]))
                continue
            ops = retired_ops(a1)
            k = sum(1 for o in ops if o in ("40", "d6"))
            cfg = " ".join(a for a in args if a.startswith("+mem") or a == "+wide") or "default"
            if f2 and arch(a0) == arch(a1):
                c0, c1 = cycles(a0), cycles(a1)
                classes["f2_operand"].append(name + " " + cfg)
                per_program.append({"program": name, "config": cfg, "class": "f2_operand", "cycles_pre": c0, "cycles_new": c1})
                continue
            if k and arch(a0) == arch(a1):
                c0, c1 = cycles(a0), cycles(a1)
                d = (c1 - c0) if (c0 is not None and c1 is not None) else None
                classes["timing"].append(name + " " + " ".join(args[1:]))
                per_program.append({"program": name, "config": cfg, "class": "timing", "retired_abs_movea_and_jmp": k, "cycles_pre": c0, "cycles_new": c1, "delta": d})
                deltas[(cfg, d, k)] += 1
                if d is None or d < 0:
                    bad_delta.append((name, d, k))
                continue
            classes["unexpected"].append(name + " " + " ".join(args[1:]))
    rep = {"pairs": len(jobs), "identical": ident, "pcrel_differ_by_design": len(classes["pcrel"]), "timing_only": len(classes["timing"]),
           "f2_operand_traps_identical_kind_pc": len(classes["f2_operand"]),
           "unexpected": len(classes["unexpected"]), "unexpected_list": classes["unexpected"][:30], "enumerated": per_program,
           "cycle_delta_vs_retired_abs_movea_and_jmp": {f"{c} delta={d} k={k}": n for (c, d, k), n in sorted(deltas.items(), key=str)},
           "negative_or_missing_delta": bad_delta[:10]}
    rep["only_filter"] = os.environ.get("IDENT_ONLY")
    outj.write_text(json.dumps(rep, indent=1))
    print(f"IDENTITY pre-R4 vs R4b microcode: {len(jobs)} log pairs; {ident} byte-identical; {len(classes['pcrel'])} PC-relative programs (differ by design); "
          f"{len(classes['timing'])} /abs-MOVEA / JMP programs and {len(classes['f2_operand'])} F2-operand trap programs with architectural identity and a cycle difference; {len(classes['unexpected'])} UNEXPECTED")
    for k, v in rep["cycle_delta_vs_retired_abs_movea_and_jmp"].items():
        print("  ", k, v)
    return 1 if classes["unexpected"] or bad_delta else 0


if __name__ == "__main__":
    sys.exit(main())
