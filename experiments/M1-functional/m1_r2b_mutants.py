#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""R2b REAL microcode mutation testing (docs/r2-ldpr-plan.md section 4 'Mutants' and section 9 #4).

Each mutant is the ACTUAL LDPR microcode built by m1_m11_microcode.build(mut=...) (in memory, TEST-ONLY knobs; the
default output of the builder is never altered), run on the real V2 benches (tb_m1_v2 / tb_s0_v2) over every
production-table R2a program (M1 bench at 2 fetch configurations, withheld-fetch runs, S0 harness programs) and
judged by the phase-r2b checker (m1_r2_check.py). A mutant is KILLED when any program fails; the report lists the
failing programs and whether a designated catcher (plan 9 #4) is among them.

  m1_r2b_mutants.py <r2b-run-dir> <root> [--jobs N]     (the run dir of run_r2.sh with R2_PHASE=r2b)
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "ref"))
import m1_m11_microcode as B  # noqa: E402
import m1_r1_run as RR  # noqa: E402
import m1_r2_check as C  # noqa: E402
import m1_r2_run as RUN  # noqa: E402

F = 15   # FLAG_UPD mask used by the flag mutants
# name: (description, knobs, designated catcher tags, equivalence reason or None)
MUT = {
    "imm_ofs0": ("IMM_OFS 0 on the immediate word", {"imm_ofs": 0}, ["L1", "L2"], None),
    "imm_ofs2": ("IMM_OFS 2 on the immediate word", {"imm_ofs": 2}, ["L1", "L2"], None),
    "imm_sz1": ("IMM_SZ 1 (16-bit) on the immediate word", {"imm_sz": 1}, ["L1", "L2"], None),
    "consume4": ("CONSUME 4 on the immediate word", {"imm_consume": 4}, ["L1", "L2", "L3"], None),
    "consume6": ("CONSUME 6 on the immediate word", {"imm_consume": 6}, ["L1", "L2", "L3"], None),
    "no_final_consume": ("missing final CONSUME 1 (the #q byte)", {"final_consume": 0}, ["L1", "L2", "L3"], None),
    "wr_asel_const0": ("WR_ASEL const (RCONST 0)", {"wr_asel": 0, "wr_rconst": 0}, ["L1", "L2"], None),
    "wr_asel_const5": ("WR_ASEL const with RCONST 5 (right for SBR only)", {"wr_asel": 0, "wr_rconst": 5}, ["L2"], None),
    "wr_asel_fld1": ("WR_ASEL operand field 1", {"wr_asel": 1}, ["L1", "L2"], None),
    "wr_asel_fld2": ("WR_ASEL operand field 2 [extra]", {"wr_asel": 2}, ["L1", "L2"], None),
    "wr_dst_gpr": ("WR_DST general register", {"wr_dst": B.WR["GPR"]}, ["L1", "L2"], None),
    "wr_dst_temp": ("WR_DST temp", {"wr_dst": B.WR["TEMP"]}, ["L1", "L2"], None),
    "flag_upd_imm": ("FLAG_UPD on the immediate word", {"flag_upd_imm": F}, ["L4", "L1"], None),
    "flag_upd_q": ("FLAG_UPD on the quick-extraction word", {"flag_upd_q": F}, ["L4", "L1"], None),
    "flag_upd_add": ("FLAG_UPD on the ID-check ADD word", {"flag_upd_add": F}, ["L4", "L1"], None),
    "flag_upd_and": ("FLAG_UPD on the ID-check AND word", {"flag_upd_and": F}, ["L4", "L1"], None),
    "flag_upd_wr": ("FLAG_UPD on the write word", {"flag_upd_wr": F}, ["L4", "L1"], None),
    "id_allows1": ("regID check off by one, allows 1 (ADD 31: window 1..4)", {"id_add": 31}, ["N1"], None),
    "id_allows6": ("regID check off by one, allows 6 (ADD 29: window 3..6)", {"id_add": 29}, ["N1"], None),
    "id_mask24": ("regID check mask 24 (window 2..9) [extra]", {"id_mask": 24}, ["N1"], None),
    "no_id_check": ("regID check removed", {"no_id_check": True}, ["N1"], None),
    "no_regid_quick_guard": ("C_QUICK regID guard removed", {"no_regid_quick_guard": True}, ["regid_quick"], None),
    "no_src_imm_guard": ("C_IMM source guard removed", {"no_src_imm_guard": True}, ["src_imm"], None),
    "no_src_quick_guard": ("C_QUICK source guard removed", {"no_src_quick_guard": True}, ["src_quick"], None),
    "no_priv_check": ("privilege check removed", {"no_priv_check": True}, ["N5"], None),
    "dtab_flag_cleared": ("dtab privileged flag cleared", {"dtab_flag_cleared": True}, ["N5"], None),
    "dtab_wrong_routine": ("dtab[0x12] at a wrong routine (the NOP routine)", {"dtab_wrong_routine": True}, ["L1", "L2"], None),
    "no_f1_guard": ("format-I guard removed [extra]", {"no_f1_guard": True}, ["N4"], None),
}


def main():
    run_dir, root = Path(sys.argv[1]), Path(sys.argv[2])
    jobs = int(sys.argv[sys.argv.index("--jobs") + 1]) if "--jobs" in sys.argv else 4
    x = RR.Ctx()
    x.out, x.phase, x.jobs, x.root = run_dir, "r2b", jobs, root
    x.progs, x.logs = x.out / "progs", x.out / "logs"
    x.man = json.load(open(x.progs / "r2_manifest.json"))
    progs = [(n, m) for n, m in x.man.items() if m["mc"] == "prod" and m["cls"] != "probe"]
    cfgs = ["", "+wide"]

    def image(name, mut):
        p, sym, dtab, mtab = B.build(False, False, False, mut)
        mc = RR.write_mc(x, f"mutr2b_{name}", B, p, sym, dtab, mtab)
        p, sym, dtab, mtab = B.build(False, False, True, mut)
        s0 = RR.write_mc(x, f"mutr2b_{name}_s0", B, p, sym, dtab, mtab)
        return mc, s0

    def judge(name, mut):
        mc, s0 = image(name, mut)
        work = []
        for n, m in progs:
            for cfg in cfgs:
                for run in (["std"] if not m.get("v2_ack") else ["std", "ack", "delay"]):
                    if run == "ack" and m["extra"].get("k") == 8:
                        continue
                    work.append((n, m, cfg, run))

        def one(j):
            n, m, cfg, run = j
            t = RUN.v2_text(x, n, m, cfg, run, mc=mc, s0mc=s0)
            return j, C.check_v2r2(m, t, "r2b", run)
        failing = {}
        with ThreadPoolExecutor(jobs) as ex:
            for (n, m, cfg, run), f in ex.map(one, work):
                if f:
                    failing.setdefault(n, []).append((cfg or "default", run, f[0][:110]))
        return len(work), failing

    out = {"runs_per_mutant": None, "mutants": {}}
    base_n, base_fail = judge("baseline", {})
    out["baseline"] = {"runs": base_n, "failing_programs": sorted(base_fail)}
    out["runs_per_mutant"] = base_n
    print(f"baseline (unmutated actual microcode): {base_n} runs, {len(base_fail)} failing programs")
    ok = not base_fail
    for name, (desc, knobs, tags, equiv) in MUT.items():
        n, failing = judge(name, knobs)
        desig = sorted(p for p in failing if set(x.man[p]["tags"]) & set(tags))
        first = (desig or sorted(failing) or [None])[0]
        row = dict(desc=desc, knobs=knobs, designated_tags=tags, failing_programs=len(failing),
                   designated_failing=len(desig), first=first,
                   first_evidence=(failing[first][0] if first else None),
                   status=("KILLED_BY_DESIGNATED" if desig else "KILLED_OTHER" if failing else
                           "EQUIVALENT" if equiv else "SURVIVED"), equivalence_reason=equiv)
        out["mutants"][name] = row
        print(f"{row['status']:22s} {name:22s} {len(failing):3d} programs fail, designated {tags}: {len(desig):3d}; first {first}: "
              f"{row['first_evidence'] and row['first_evidence'][2]}")
        ok &= row["status"] in ("KILLED_BY_DESIGNATED", "EQUIVALENT")
    out["all_ok"] = ok
    Path(sys.argv[3] if len(sys.argv) > 3 and not sys.argv[3].startswith("--") else x.out / "r2b_mutants.json").write_text(json.dumps(out, indent=1))
    print("MUTANTS", "OK" if ok else "FAIL", f"{sum(r['status'].startswith('KILLED') for r in out['mutants'].values())}/{len(MUT)} killed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
