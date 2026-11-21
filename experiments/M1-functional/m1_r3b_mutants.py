#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""R3b REAL microcode mutation testing (docs/r3-movea-plan.md section 6 "Mutation tests for R3b"; method of m1_r2b_mutants.py).

Each mutant is the ACTUAL MOVEA microcode / tables built by m1_m11_microcode.build(mut={"mv_...": ...}) (in memory, TEST-ONLY
knobs; the builder's default output is never altered), run on the real V2 benches (tb_m1_v2 / tb_s0_v2) over the R3 programs and
judged by the phase-r3b checker (m1_r3_check.check_v2r3). A mutant is KILLED when any program fails; the report lists
whether a program of a designated catcher tag (plan section 6) is among the failing ones. Mutants that break a builder
assertion (variant isolation, mtab[0x0F3], uniqueness) are killed STRUCTURALLY (build() raises AssertionError); where the plan names a
behavioural catcher too (N1, N5, A1) the same mutant is built with the assertions bypassed ("mv_no_checks") and run.

Program set per mutant: every production-table program (M1 bench at {default, +wide}, withheld-fetch runs, S0 harness) except the
511-entry N3 sweep, which is added only for mutants that name it; the SMC mutant runs the --smc-test programs (M1 and S0 reset-native).
The unmutated baseline runs everything, sweep included.

  m1_r3b_mutants.py <r3b-run-dir> <root> [out.json] [--jobs N]     (the run dir of run_r3.sh with R3_PHASE=r3b)
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
import m1_r3_check as C  # noqa: E402
import m1_r3_run as RUN  # noqa: E402

F = 15
MRET = None
# name: (description, knobs, designated catcher tags, equivalence reason | None, kind)   kind: prod | smc | struct
MUT = {
    "imm_ofs0": ("IMM_OFS 0 (reads the mode byte and three address bytes)", {"mv_imm_ofs": 0}, ["A1", "A2"], None, "prod"),
    "imm_ofs2": ("IMM_OFS 2 (address skewed by one byte)", {"mv_imm_ofs": 2}, ["A1", "A2"], None, "prod"),
    "imm_sz0": ("IMM_SZ 0 (byte read; the `.b`-role mutant)", {"mv_imm_sz": 0}, ["A1"], None, "prod"),
    "imm_sz1": ("IMM_SZ 1 (halfword read)", {"mv_imm_sz": 1}, ["A1"], None, "prod"),
    "imm_sx": ("IMM_SX set with IMM_SZ = 2", {"mv_imm_sx": True}, ["A1"],
               "v2_immx returns all 32 bits unchanged for a word-size immediate (v2_glue.sv imm_val): sign extension applies only to byte/half", "prod"),
    "mask8": ("result zero-extended from a byte (ZEXT, SIZE_SEL byte)", {"mv_mask8": True}, ["A1"], None, "prod"),
    "consume4": ("CONSUME 4 on the execute word", {"mv_consume": 4}, ["A6", "A7", "A8"], None, "prod"),
    "consume6": ("CONSUME 6 on the execute word", {"mv_consume": 6}, ["A6", "A7", "A8"], None, "prod"),
    "fmt_consume1": ("missing CONSUME on the format word (CONSUME 1)", {"mv_fmt_consume": 1}, ["A6", "A8"], None, "prod"),
    "wr_dst_temp": ("WR_DST temp instead of GPR", {"mv_wr_dst": B.WR["TEMP"]}, ["A2"], None, "prod"),
    "wr_dst_priv": ("WR_DST privileged instead of GPR", {"mv_wr_dst": B.WR["PRIV"]}, ["A2"], None, "prod"),
    "wr_asel_const": ("WR_ASEL const (RCONST 0)", {"mv_wr_asel": 0, "mv_rconst": 0}, ["A2"], None, "prod"),
    "wr_asel_fld2": ("WR_ASEL field 2 (the mode byte's low five bits = R19)", {"mv_wr_asel": 2}, ["A2"], None, "prod"),
    "wr_asel_mreg": ("WR_ASEL mode-byte register (R19)", {"mv_wr_asel": 3}, ["A2"], None, "prod"),
    "nowrite": ("omitted GPR write (the value goes to a temp); with the destination already equal to the address it is the omitted-unchanged write",
                {"mv_nowrite": True}, ["A2", "A3"], None, "prod"),
    "dup_write": ("duplicate GPR write in the executed word sequence", {"mv_dup": True}, ["A1", "A3"], None, "prod"),
    "transient_write": ("transient GPR write (a constant, then the address): two events, final state correct", {"mv_transient": True}, ["A1", "A3"], None, "prod"),
    "flag_upd": ("FLAG_UPD on the execute word", {"mv_flag_upd": F}, ["A5"], None, "prod"),
    "value_read": ("a data-bus read of the target (value instead of address; the v60_ref defect)", {"mv_value_read": True}, ["A3", "A4"], None, "prod"),
    "no_regdir_guard": ("C_REGDIR guard removed", {"mv_no_regdir_guard": True}, ["N4", "src_regdir"], None, "prod"),
    "no_imm_guard": ("C_IMM guard removed", {"mv_no_imm_guard": True}, ["N3", "N4", "src_imm"], None, "prod"),
    "no_f1_guard": ("C_F1 guard removed", {"mv_no_f1_guard": True}, ["N2", "f2"], None, "prod"),
    "no_d_guard": ("C_D guard removed", {"mv_no_d_guard": True}, ["N2", "d0"], None, "prod"),
    "dtab42": ("dtab[0x42] = movea", {"mv_dtab42": True}, ["N1"], None, "prod"),
    "dtab44": ("dtab[0x44] = movea", {"mv_dtab44": True}, ["N1"], None, "prod"),
    "dtab40_wrong": ("dtab[0x40] at a wrong routine (the NOP routine)", {"mv_dtab40_wrong": True, "mv_no_checks": True}, ["A1"], None, "prod"),
    "dtab40_trap": ("dtab[0x40] left trap", {"mv_dtab40_trap": True, "mv_no_checks": True}, ["A1"], None, "prod"),
    "no_disp_jmp": ("missing final jmp disp", {"mv_no_disp_jmp": True}, ["A8"], None, "prod"),
    "smc_dispatch": ("dtab[0x40] = movea in the --smc-test builds (behavioural, assertions bypassed)", {"mv_smc_dispatch": True, "mv_no_checks": True}, ["N5"], None, "smc"),
    "mtab_marked_imm": ("mtab[0x0F3] descriptor marked immediate (behavioural, assertions bypassed)", {"mv_mtab": {0x0F3: 0x14E << 12}, "mv_no_checks": True}, ["A1"], None, "prod"),
    "mtab_marked_reg": ("mtab[0x0F3] descriptor marked register (behavioural, assertions bypassed)", {"mv_mtab": {0x0F3: 0x14D << 12}, "mv_no_checks": True}, ["A1"], None, "prod"),
    "mtab_ofs16": ("mtab[0x0F3] descriptor offset size 16 (behavioural, assertions bypassed)", {"mv_mtab": {0x0F3: 0x148 << 12}, "mv_no_checks": True}, ["A1"],
                   "the routine's IMM_SZ is explicit, so the descriptor's offset-size field is never read (R3a ledger finding 1): identical V2 behaviour; "
                   "killed by the structural assertion only", "prod"),
}
# structural mutants: build() must raise AssertionError (the builder's own structural assertions)
STRUCT = {
    "struct_mtab_ofs16": ("mtab[0x0F3] descriptor offset size 16", {"mv_mtab": {0x0F3: 0x148 << 12}}, False),
    "struct_mtab_marked_imm": ("mtab[0x0F3] marked immediate", {"mv_mtab": {0x0F3: 0x14E << 12}}, False),
    "struct_mtab_marked_reg": ("mtab[0x0F3] marked register", {"mv_mtab": {0x0F3: 0x14D << 12}}, False),
    "struct_second_descriptor_pcdisp": ("a second memory descriptor (mtab[0x0F2], PC disp32) added", {"mv_mtab": {0x0F2: 0x14C << 12}}, False),
    "struct_second_descriptor_rn": ("a second memory descriptor ([R5], modm 0 0x65) added", {"mv_mtab": {0x065: 0x14C << 12}}, False),
    "struct_f3_m1_accepted": ("F3 with m = 1 accepted (mtab[0x1F3])", {"mv_mtab": {0x1F3: 0x14C << 12}}, False),
    "struct_smc_dispatch": ("dtab[0x40] = movea in an --smc-test build", {"mv_smc_dispatch": True}, True),
    "struct_dtab40_trap": ("dtab[0x40] left trap in production", {"mv_dtab40_trap": True}, False),
    "struct_dtab40_wrong": ("dtab[0x40] at a wrong routine in production", {"mv_dtab40_wrong": True}, False),
}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    run_dir, root = Path(args[0]), Path(args[1])
    outj = Path(args[2]) if len(args) > 2 and not args[2].isdigit() else run_dir / "r3b_mutants.json"
    jobs = int(sys.argv[sys.argv.index("--jobs") + 1]) if "--jobs" in sys.argv else 4
    only = None
    if "--only" in sys.argv:
        only = set(sys.argv[sys.argv.index("--only") + 1].split(","))
    x = RR.Ctx()
    x.out, x.phase, x.jobs, x.root = run_dir, "r3b", jobs, root
    x.progs, x.logs = x.out / "progs", x.out / "logs"
    x.man = json.load(open(x.progs / "r3_manifest.json"))
    prod = [(n, m) for n, m in x.man.items() if m["mc"] == "prod"]
    nonsweep = [(n, m) for n, m in prod if "sweep" not in m["tags"]]
    smcprogs = [(n, m) for n, m in prod if m.get("v2_smc") is not None and "jmp" not in m["tags"]]
    cfgs = ["", "+wide"]

    def image(name, mut, smc=False):
        p, sym, dtab, mtab = B.build(smc, False, False, mut)
        mc = RR.write_mc(x, f"mutr3b_{name}", B, p, sym, dtab, mtab)
        p, sym, dtab, mtab = B.build(smc, False, True, mut)
        s0 = RR.write_mc(x, f"mutr3b_{name}_s0", B, p, sym, dtab, mtab)
        return mc, s0

    def judge(name, mut, progs, smc=False):
        mc, s0 = image(name, mut, smc)
        work = []
        for n, m in progs:
            if smc:
                if m["where"] == "low" and False:
                    pass
                work.append((n, m, "", "smc"))
                continue
            for cfg in cfgs:
                for run in (["std"] if not m.get("v2_ack") else ["std", "ack", "delay"]):
                    if run == "ack" and m["extra"].get("k") == 7:
                        continue
                    work.append((n, m, cfg, run))

        def one(j):
            n, m, cfg, run = j
            t = RUN.v2_text(x, n, m, cfg, "std" if run == "smc" else run, mc=mc, s0mc=s0)
            return j, C.check_v2r3(m, t, "r3b", run)
        failing = {}
        with ThreadPoolExecutor(jobs) as ex:
            for (n, m, cfg, run), f in ex.map(one, work):
                if f:
                    failing.setdefault(n, []).append((cfg or "default", run, f[0][:140]))
        return len(work), failing

    out = {"runs_per_mutant": None, "mutants": {}, "structural": {}}
    if only is None:
        base_n, base_fail = judge("baseline", {}, prod)
        out["baseline"] = {"runs": base_n, "failing_programs": sorted(base_fail)}
        sbase_n, sbase_fail = judge("baseline_smc", {}, smcprogs, smc=True)
        out["baseline_smc"] = {"runs": sbase_n, "failing_programs": sorted(sbase_fail)}
        print(f"baseline (unmutated actual microcode): production {base_n} runs, {len(base_fail)} failing programs; "
              f"--smc-test {sbase_n} runs, {len(sbase_fail)} failing")
        ok = not base_fail and not sbase_fail
    else:
        ok = True
    # structural (builder assertions)
    for name, (desc, knobs, smc) in STRUCT.items():
        try:
            B.build(smc, False, False, knobs)
            res, why = "SURVIVED", "build() raised no assertion"
        except AssertionError as e:
            res, why = "KILLED_STRUCTURAL", str(e)[:140]
        out["structural"][name] = dict(desc=desc, status=res, assertion=why)
        print(f"{res:22s} {name:34s} {why}")
        ok &= res == "KILLED_STRUCTURAL"
    for name, (desc, knobs, tags, equiv, kind) in MUT.items():
        if only is not None and name not in only:
            continue
        if kind == "smc":
            n, failing = judge(name, knobs, smcprogs, smc=True)
        else:
            n, failing = judge(name, knobs, prod if set(tags) & {"N3", "sweep"} else nonsweep)
        desig = sorted(p for p in failing if set(x.man[p]["tags"]) & set(tags))
        first = (desig or sorted(failing) or [None])[0]
        row = dict(desc=desc, knobs={k: (v if not isinstance(v, dict) else {hex(a): hex(b) for a, b in v.items()}) for k, v in knobs.items()},
                   designated_tags=tags, runs=n, failing_programs=len(failing),
                   designated_failing=len(desig), first=first, first_evidence=(failing[first][0] if first else None),
                   status=("KILLED_BY_DESIGNATED" if desig else "KILLED_OTHER" if failing else "EQUIVALENT" if equiv else "SURVIVED"),
                   equivalence_reason=equiv)
        if name == "nowrite":
            a3 = [p for p in failing if "A3" in x.man[p]["tags"] and any("GPR write events" in e[2] for e in failing[p])]
            row["a3_gpr_event_failures"] = sorted(a3)
        if name == "consume4":
            row["availability_checker_fired_runs"] = sum("SIMCHECK" in e[2] for p in failing for e in failing[p])
        out["mutants"][name] = row
        print(f"{row['status']:22s} {name:22s} {n:5d} runs, {len(failing):3d} programs fail, designated {tags}: {len(desig):3d}; first {first}: "
              f"{row['first_evidence'] and row['first_evidence'][2]}")
        ok &= row["status"] in ("KILLED_BY_DESIGNATED", "EQUIVALENT")
        if row["status"] == "EQUIVALENT" and failing:
            ok = False
    out["all_ok"] = ok
    outj.write_text(json.dumps(out, indent=1))
    k = sum(r["status"].startswith("KILLED") for r in out["mutants"].values())
    e = sum(r["status"] == "EQUIVALENT" for r in out["mutants"].values())
    print("MUTANTS", "OK" if ok else "FAIL", f"behavioural: {k} killed + {e} equivalent of {len(out['mutants'])}; structural {sum(r['status'].startswith('KILLED') for r in out['structural'].values())}/{len(STRUCT)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
