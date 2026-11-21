#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""B2 targeted mutant (byte-lane merging): the generic MOV microcode with its memory write replaced by a WORD store (builder knob
`b2_mov_wr_word`). The directed vectors b2_mov0_st_* must FAIL against the V60X reference (the word store spills zeros over the neighbouring
bytes) while the unmutated microcode passes them. Exit 0 only if the control passes and the mutant fails.

  b2_lane_mutant.py <dv-dir> <v2-bench-dir> <out-dir>      (<dv-dir>: a run_m11.sh output dir of m1_b2.py with its .bin and .ref files)
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "ref"))
import m1_m11_microcode as B  # noqa: E402
import m1_r1_run as RR  # noqa: E402


def run(sim, mc, img):
    s = RR.symbols(mc)
    r = subprocess.run([str(sim), f"+img={img}", f"+mc={mc}", f"+haltpc={s['halt']}", f"+trappc={s['trap']}", f"+trapmodepc={s['trap_mode']}",
                        f"+trapimmpc={s['trap_immdest']}", "+lockstep", "+maxcyc=400000"], capture_output=True, text=True)
    return r.stdout + r.stderr


def main():
    dv, bench, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    out.mkdir(parents=True, exist_ok=False)
    ok = True
    names = sorted(p.stem for p in dv.glob("b2_mov0_st_*.bin"))
    for label, mut in (("control (unmutated)", {}), ("mutant: word store instead of the byte write", {"b2_mov_wr_word": True})):
        p, sym, dtab, mtab = B.build(False, False, False, mut)
        mc = RR.write_mc(type("X", (), {"out": out})(), "mc_" + ("mut" if mut else "ctl"), B, p, sym, dtab, mtab)
        bad = []
        for n in names:
            v = out / f"{n}.{'mut' if mut else 'ctl'}"
            v.write_text(run(bench / "obj_v2/sim", mc, dv / f"{n}.hex"))
            cmp = subprocess.run([sys.executable, str(HERE / "m1_compare.py"), str(dv / f"{n}.ref"), str(v), "--label-a", "ref", "--label-b", "v2"], capture_output=True, text=True)
            if cmp.returncode != 0:
                bad.append(n)
        print(f"{label}: {len(names) - len(bad)}/{len(names)} mov.b store vectors match V60X; failing: {bad}")
        ok &= (not bad) if not mut else bool(bad)
    print("LANE MUTANT", "KILLED (control passes, mutant fails)" if ok else "NOT KILLED / CONTROL FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
