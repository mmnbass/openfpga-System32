#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""R4b REAL microcode mutation testing (docs/r4-movea-pcrel-plan.md section 6.3; method of m1_r3b_mutants.py).

Each mutant is the ACTUAL microcode / tables built by m1_m11_microcode.build(mut={"pc_..." / "mv_...": ...}) (in memory, TEST-ONLY knobs; the
builder's default output is never altered), run on the real V2 benches over the R4 programs (phase r4b) and judged by m1_r4_check.
A mutant is KILLED when any program fails; the report says whether a program of a designated catcher tag (plan 6.3) is among the failing
ones. Mutants that break a builder assertion (tag isolation, mtab[0x0F2], the descriptor set) are killed STRUCTURALLY (build() raises
AssertionError); where the plan names a behavioural catcher too, the same mutant is built with the assertions bypassed ("mv_no_checks")
and run. A mutant that is behaviourally identical is reported EQUIVALENT with its reason (and must then be killed structurally).

Program set per mutant: every production-table program except the 510-entry sweep (added only for mutants that name C2 / sweep); the SMC
mutants run the --smc-test programs. The unmutated baseline runs everything.

  m1_r4b_mutants.py <r4b-run-dir> <root> <out.json> [--jobs N] [--only a,b]     (the run dir of run_r4.sh with R4_PHASE=r4b)
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
import m1_r4_check as C  # noqa: E402
import m1_r3_run as RUN  # noqa: E402

F = 15
NC = {"mv_no_checks": True}        # bypass the builder's structural assertions for the behavioural run
POS = ["B1", "B2", "B3"]
# name: (description, knobs, designated catcher tags, equivalence reason | None, kind)   kind: prod | smc
# (a condition that is TRUE for both descriptors makes /abs MOVEA take the PC-relative word: caught by B1/B2 (plan 6.3) or by the programs that
#  execute an /abs MOVEA, B6 / B10; the smc mutants are caught by the PC-relative programs of the --smc-test matrix, C8)
MUT = {
    "base_next_pc": ("wrong base = next PC (start + 7)", {"pc_addk": 7}, POS, None, "prod"),
    "base_field": ("wrong base = field address (start + 3)", {"pc_addk": 3}, POS, None, "prod"),
    "base_head": ("wrong base = fetch head after the format byte (start + 2)", {"pc_addk": 2}, POS, None, "prod"),
    "base_zero": ("wrong base = 0 (the /abs behaviour: the raw displacement)", {"pc_base0": True}, POS, None, "prod"),
    "sub": ("SUB instead of ADD", {"pc_sub": True}, POS, None, "prod"),
    "swap_operands": ("ASEL / BSEL swapped on the ADD", {"pc_swap": True}, POS,
                      "ADD is commutative: ipc + imm == imm + ipc (the swapped word is architecturally identical)", "prod"),
    "imm_ofs0": ("IMM_OFS 0 (reads the mode byte and three field bytes)", {"pc_imm_ofs": 0}, POS, None, "prod"),
    "imm_ofs2": ("IMM_OFS 2 (field skewed by one byte)", {"pc_imm_ofs": 2}, POS, None, "prod"),
    "imm_sz0": ("IMM_SZ 0 (displacement read as 8 bits)", {"pc_imm_sz": 0}, POS, None, "prod"),
    "imm_sz1": ("IMM_SZ 1 (displacement read as 16 bits)", {"pc_imm_sz": 1}, POS, None, "prod"),
    "imm_sx": ("IMM_SX set with IMM_SZ = 2", {"pc_imm_sx": True}, POS,
               "v2_immx returns all 32 bits unchanged for a word-size immediate (v2_glue.sv imm_val): sign extension of a disp32 is a no-op "
               "in 32-bit arithmetic (plan 2.2)", "prod"),
    "mask8": ("result zero-extended from 8 bits", {"pc_mask": 8}, POS, None, "prod"),
    "mask16": ("result zero-extended from 16 bits", {"pc_mask": 16}, POS, None, "prod"),
    "consume4": ("CONSUME 4 on the execute word", {"pc_consume": 4}, ["B9", "B2", "B6"], None, "prod"),
    "consume6": ("CONSUME 6 on the execute word", {"pc_consume": 6}, ["B9", "B2", "B6"], None, "prod"),
    "consume8": ("CONSUME 8 on the execute word", {"pc_consume": 8}, ["B9", "B2", "B6"], None, "prod"),
    "fmt_consume1": ("missing CONSUME on the format word (CONSUME 1)", {"mv_fmt_consume": 1}, ["B9", "B2", "B6"], None, "prod"),
    "wr_dst_temp": ("WR_DST temp instead of GPR", {"pc_wr_dst": B.WR["TEMP"]}, ["B4"], None, "prod"),
    "wr_dst_priv": ("WR_DST privileged instead of GPR", {"pc_wr_dst": B.WR["PRIV"]}, ["B4"], None, "prod"),
    "wr_asel_const": ("WR_ASEL const (RCONST 0)", {"pc_wr_asel": 0, "pc_rconst": 0}, ["B4"], None, "prod"),
    "wr_asel_fld2": ("WR_ASEL field 2 (the mode byte's low five bits = R18)", {"pc_wr_asel": 2}, ["B4"], None, "prod"),
    "wr_asel_mreg": ("WR_ASEL mode-byte register (R18)", {"pc_wr_asel": 3}, ["B4"], None, "prod"),
    "nowrite": ("omitted GPR write (the value goes to a temp); destination already equal to the result = the omitted-unchanged write",
                {"pc_nowrite": True}, ["B4", "B5"], None, "prod"),
    "dup_write": ("duplicate GPR write in the executed word sequence", {"pc_dup": True}, ["B5", "B1"], None, "prod"),
    "transient_write": ("transient GPR write (a constant, then the address): two events, final state correct", {"pc_transient": True}, ["B5", "B1"], None, "prod"),
    "flag_upd": ("FLAG_UPD on the execute word", {"pc_flag_upd": F}, ["B8"], None, "prod"),
    "value_read": ("a data-bus read of the target (value instead of address; the v60_ref defect)", {"pc_value_read": True}, ["B7"], None, "prod"),
    "dummy_read": ("an extra bus READ of the target whose result is discarded", {"pc_dummy_read": True}, ["B7"], None, "prod"),
    "dummy_store": ("an extra bus WRITE (a store of a constant) to the target", {"pc_dummy_store": True}, ["B7"], None, "prod"),
    "no_f1_guard": ("C_F1 guard removed", {"mv_no_f1_guard": True}, ["C3", "f2"], None, "prod"),
    "no_d_guard": ("C_D guard removed", {"mv_no_d_guard": True}, ["C4", "d0"], None, "prod"),
    "no_regdir_guard": ("C_REGDIR guard removed", {"mv_no_regdir_guard": True}, ["C2", "sweep", "C7"], None, "prod"),
    "no_imm_guard": ("C_IMM guard removed", {"mv_no_imm_guard": True}, ["C2", "sweep"], None, "prod"),
    "pc_cond22": ("movea branch tests condition 22 (double displacement) instead of 23", dict(NC, pc_cond=22), POS, None, "prod"),
    "pc_cond24": ("movea branch tests condition 24 (C_F1) instead of 23", dict(NC, pc_cond=24), POS, None, "prod"),
    "pc_cond25": ("movea branch tests condition 25 (C_D) instead of 23", dict(NC, pc_cond=25), POS + ["B6", "B10"], None, "prod"),
    "pc_cond26": ("movea branch tests condition 26 (quick) instead of 23", dict(NC, pc_cond=26), POS, None, "prod"),
    "pc_cond27": ("movea branch tests condition 27 (decode ready) instead of 23", dict(NC, pc_cond=27), POS + ["B6", "B10"], None, "prod"),
    "pc_cond6": ("movea branch tests condition 6 (result zero) instead of 23", dict(NC, pc_cond=6), POS + ["B6", "B10"], None, "prod"),
    "pc_pol_inverted": ("movea branch polarity inverted (/abs takes the PC-relative word, the tagged descriptor takes /abs)", dict(NC, pc_pol=1), ["B1", "B6"], None, "prod"),
    "pc_never_taken": ("movea branch removed (the tagged descriptor falls through to the /abs word: the raw displacement)", dict(NC, pc_no_branch=True), POS, None, "prod"),
    "jmp_guard_removed": ("jmp guard removed (jmp d[PC] executes as /abs and jumps to the raw displacement)", dict(NC, pc_no_jmp_guard=True), ["C7a"], None, "prod"),
    "jmp_cond22": ("jmp guard tests condition 22", dict(NC, pc_jmp_cond=22), ["C7a"], None, "prod"),
    "jmp_cond24": ("jmp guard tests condition 24", dict(NC, pc_jmp_cond=24), ["C7a"], None, "prod"),
    "jmp_cond25": ("jmp guard tests condition 25", dict(NC, pc_jmp_cond=25), ["C7a"], None, "prod"),
    "jmp_cond26": ("jmp guard tests condition 26", dict(NC, pc_jmp_cond=26), ["C7a"], None, "prod"),
    "jmp_cond27": ("jmp guard tests condition 27", dict(NC, pc_jmp_cond=27), ["C7a"], None, "prod"),
    "jmp_pol_inverted": ("jmp guard polarity inverted (jmp /abs traps, jmp d[PC] executes)", dict(NC, pc_jmp_pol=1), ["C7a", "C7j", "B6"], None, "prod"),
    "jmp_guard_early": ("jmp guard placed BEFORE operand(): it reads the previous instruction's descriptor", dict(NC, pc_jmp_guard_early=True), ["C7a", "C7j"], None, "prod"),
    "tag_on_abs": ("tag (bit 11) also on the /abs descriptor mtab[0x0F3]", dict(NC, mv_mtab={0x0F3: (0x14C | 0x800) << 12}), ["B6", "B2", "B10"], None, "prod"),
    "tag_on_regdir": ("tag on a register-direct entry (mtab[0x160])", dict(NC, mv_mtab={0x160: (1 | 0x800) << 12}), ["C2", "sweep", "B1"],
                      "the C_REGDIR guard precedes the tag test in movea and jmp and no other routine tests condition 23: behaviourally unreachable "
                      "(plan 6.3); killed by the structural assertion only", "prod"),
    "tag_on_quick": ("tag on a #q entry (mtab[0x0E0])", dict(NC, mv_mtab={0x0E0: (2 | 0x800) << 12}), ["C2", "sweep"],
                     "the C_IMM guard precedes the tag test: behaviourally unreachable; killed by the structural assertion only", "prod"),
    "tag_on_imm": ("tag on the #imm32 entry (mtab[0x0F4])", dict(NC, mv_mtab={0x0F4: ((1 << 1) | (3 << 2) | 0x800) << 12}), ["C2", "sweep"],
                   "the C_IMM guard precedes the tag test: behaviourally unreachable; killed by the structural assertion only", "prod"),
    "tag_removed": ("tag removed from mtab[0x0F2] (bit 11 clear): movea_pcrel is never taken", dict(NC, mv_mtab={0x0F2: 0x08C << 12}), ["B1", "B10"], None, "prod"),
    "f2_marked_reg": ("mtab[0x0F2] carries the tag and the register-direct bit (0x88D)", dict(NC, mv_mtab={0x0F2: 0x88D << 12}), ["B1"], None, "prod"),
    "f2_marked_imm": ("mtab[0x0F2] carries the tag and the immediate bit (0x88E)", dict(NC, mv_mtab={0x0F2: 0x88E << 12}), ["B1"], None, "prod"),
    "f2_dsz1": ("mtab[0x0F2] descriptor displacement-size field 16 bits (0x884)", dict(NC, mv_mtab={0x0F2: 0x884 << 12}), ["B1"],
                "the routine's IMM_SZ is explicit, so the descriptor's size / base fields are never read (and no DEC_EA word exists): identical V2 "
                "behaviour; killed by the structural assertion only", "prod"),
    "f2_base5": ("mtab[0x0F2] descriptor AGU base 5 (0x8CC)", dict(NC, mv_mtab={0x0F2: 0x8CC << 12}), ["B1"],
                 "the AGU base field is never read (no DEC_EA word): identical V2 behaviour; killed by the structural assertion only", "prod"),
    "f0_populated": ("mtab[0x0F0] (disp8[PC]) populated with the tagged descriptor", dict(NC, mv_mtab={0x0F0: 0x88C << 12}), ["C1", "C2"], None, "prod"),
    "f1_populated": ("mtab[0x0F1] (disp16[PC]) populated with the tagged descriptor", dict(NC, mv_mtab={0x0F1: 0x88C << 12}), ["C1", "C2"], None, "prod"),
    "f8_populated": ("mtab[0x0F8] ([disp[PC]], PC indirect) populated with the tagged descriptor", dict(NC, mv_mtab={0x0F8: 0x88C << 12}), ["C2", "sweep"], None, "prod"),
    "m1_f2_populated": ("mtab[0x1F2] (F2 with m = 1) populated with the tagged descriptor", dict(NC, mv_mtab={0x1F2: 0x88C << 12}), ["C5", "C2"], None, "prod"),
    "third_c23": ("a third word branching on condition 23 (a guard in movw_d1)", dict(NC, pc_third_c23=True), ["C7c"],
                  "the added guard branches to trap_mode exactly where the existing C_IMM guard already traps a memory descriptor: identical V2 "
                  "behaviour; killed by the structural assertion (condition-23 words == 2)", "prod"),
    "dec_ea_word": ("a DEC_EA word added (the 0x88C base / size / step bits become live)", dict(NC, pc_dec_ea_word=True), ["B1"],
                    "the added word is unreachable (it follows the routine's last jmp): identical V2 behaviour; killed by the structural assertion", "prod"),
    "pc_no_disp_jmp": ("missing final jmp disp on movea_pcrel", {"pc_no_disp_jmp": True}, ["B1", "B6"], None, "prod"),
    "dtab42": ("dtab[0x42] = movea", {"mv_dtab42": True}, ["C6", "N1"], None, "prod"),
    "dtab44": ("dtab[0x44] = movea", {"mv_dtab44": True}, ["C6", "N1"], None, "prod"),
    "dtab40_wrong": ("dtab[0x40] at a wrong routine (the NOP routine)", dict(NC, mv_dtab40_wrong=True), ["B1"], None, "prod"),
    "dtab40_trap": ("dtab[0x40] left trap", dict(NC, mv_dtab40_trap=True), ["B1"], None, "prod"),
    "smc_dispatch": ("dtab[0x40] = movea in the --smc-test builds (behavioural, assertions bypassed)", dict(NC, mv_smc_dispatch=True), POS, None, "smc"),
    "smc_f2_tagged": ("mtab[0x0F2] = 0x88C and dtab[0x40] = movea in the --smc-test builds (behavioural, assertions bypassed)",
                      dict(NC, mv_smc_dispatch=True, mv_mtab={0x0F2: 0x88C << 12}), POS, None, "smc"),
}
# structural mutants: build() must raise AssertionError (the builder's own structural assertions)
STRUCT = {
    "struct_tag_on_abs": ("bit 11 on mtab[0x0F3]", {"mv_mtab": {0x0F3: (0x14C | 0x800) << 12}}, False),
    "struct_tag_on_regdir": ("bit 11 on a register-direct entry", {"mv_mtab": {0x160: (1 | 0x800) << 12}}, False),
    "struct_tag_on_quick": ("bit 11 on a #q entry", {"mv_mtab": {0x0E0: (2 | 0x800) << 12}}, False),
    "struct_tag_on_imm": ("bit 11 on the #imm32 entry", {"mv_mtab": {0x0F4: ((1 << 1) | (3 << 2) | 0x800) << 12}}, False),
    "struct_tag_removed": ("mtab[0x0F2] without the tag", {"mv_mtab": {0x0F2: 0x08C << 12}}, False),
    "struct_f2_marked_reg": ("mtab[0x0F2] marked register", {"mv_mtab": {0x0F2: 0x88D << 12}}, False),
    "struct_f2_marked_imm": ("mtab[0x0F2] marked immediate", {"mv_mtab": {0x0F2: 0x88E << 12}}, False),
    "struct_f2_dsz1": ("mtab[0x0F2] displacement-size field 16", {"mv_mtab": {0x0F2: 0x884 << 12}}, False),
    "struct_f2_base5": ("mtab[0x0F2] AGU base 5", {"mv_mtab": {0x0F2: 0x8CC << 12}}, False),
    "struct_f2_trap_mode": ("mtab[0x0F2] left trap_mode in production", {"mv_mtab": {0x0F2: 0}}, False),
    "struct_f0_populated": ("mtab[0x0F0] populated", {"mv_mtab": {0x0F0: 0x88C << 12}}, False),
    "struct_f1_populated": ("mtab[0x0F1] populated", {"mv_mtab": {0x0F1: 0x88C << 12}}, False),
    "struct_f8_populated": ("mtab[0x0F8] populated", {"mv_mtab": {0x0F8: 0x88C << 12}}, False),
    "struct_m1_f2_populated": ("mtab[0x1F2] populated", {"mv_mtab": {0x1F2: 0x88C << 12}}, False),
    "struct_f3_m1_accepted": ("mtab[0x1F3] accepted", {"mv_mtab": {0x1F3: 0x14C << 12}}, False),
    "struct_second_descriptor_rn": ("a second memory descriptor ([R5], modm 0 0x65) added", {"mv_mtab": {0x065: 0x14C << 12}}, False),
    "struct_f3_changed": ("mtab[0x0F3] descriptor offset size 16", {"mv_mtab": {0x0F3: 0x148 << 12}}, False),
    "struct_third_c23": ("a third condition-23 word", {"pc_third_c23": True}, False),
    "struct_no_jmp_guard": ("jmp guard removed (condition-23 words == 1)", {"pc_no_jmp_guard": True}, False),
    "struct_jmp_guard_early": ("jmp guard before operand()", {"pc_jmp_guard_early": True}, False),
    "struct_jmp_pol_inverted": ("jmp guard polarity inverted", {"pc_jmp_pol": 1}, False),
    "struct_movea_cond22": ("movea branch on condition 22 (condition-23 words == 1)", {"pc_cond": 22}, False),
    "struct_movea_pol_inverted": ("movea branch polarity inverted", {"pc_pol": 1}, False),
    "struct_dec_ea_word": ("a DEC_EA word", {"pc_dec_ea_word": True}, False),
    "struct_smc_dispatch": ("dtab[0x40] = movea in an --smc-test build", {"mv_smc_dispatch": True}, True),
    "struct_smc_f2_tagged": ("mtab[0x0F2] = 0x88C in an --smc-test build", {"mv_mtab": {0x0F2: 0x88C << 12}}, True),
    "struct_dtab40_trap": ("dtab[0x40] left trap in production", {"mv_dtab40_trap": True}, False),
    "struct_dtab40_wrong": ("dtab[0x40] at a wrong routine in production", {"mv_dtab40_wrong": True}, False),
}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--") and not a.isdigit() and "," not in a]
    run_dir, root, outj = Path(args[0]), Path(args[1]), Path(args[2])
    jobs = int(sys.argv[sys.argv.index("--jobs") + 1]) if "--jobs" in sys.argv else 4
    only = set(sys.argv[sys.argv.index("--only") + 1].split(",")) if "--only" in sys.argv else None
    assert not outj.exists(), f"{outj} exists: every run needs a NEW output file"
    x = RR.Ctx()
    x.out, x.phase, x.jobs, x.root = run_dir, "r4b", jobs, root
    x.progs, x.logs = x.out / "progs", x.out / "logs"
    x.man = json.load(open(x.progs / "r4_manifest.json"))
    prod = [(n, m) for n, m in x.man.items() if m["mc"] == "prod"]
    nonsweep = [(n, m) for n, m in prod if "sweep" not in m["tags"]]
    smcprogs = [(n, m) for n, m in prod if m.get("v2_smc") is not None and "jmp" not in m["tags"]]
    cfgs = ["", "+wide"]

    def image(name, mut, smc=False):
        p, sym, dtab, mtab = B.build(smc, False, False, mut)
        mc = RR.write_mc(x, f"mutr4b_{name}", B, p, sym, dtab, mtab)
        p, sym, dtab, mtab = B.build(smc, False, True, mut)
        s0 = RR.write_mc(x, f"mutr4b_{name}_s0", B, p, sym, dtab, mtab)
        return mc, s0

    def judge(name, mut, progs, smc=False):
        mc, s0 = image(name, mut, smc)
        work = []
        for n, m in progs:
            if smc:
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
            return j, C.check_v2r4(m, t, "r4b", run)
        failing = {}
        with ThreadPoolExecutor(jobs) as ex:
            for (n, m, cfg, run), f in ex.map(one, work):
                if f:
                    failing.setdefault(n, []).append((cfg or "default", run, f[0][:140]))
        return len(work), failing

    out = {"runs_per_mutant": None, "mutants": {}, "structural": {}}
    ok = True
    if only is None:
        base_n, base_fail = judge("baseline", {}, prod)
        sbase_n, sbase_fail = judge("baseline_smc", {}, smcprogs, smc=True)
        out["baseline"] = {"runs": base_n, "failing_programs": sorted(base_fail)}
        out["baseline_smc"] = {"runs": sbase_n, "failing_programs": sorted(sbase_fail)}
        print(f"baseline (unmutated actual microcode): production {base_n} runs, {len(base_fail)} failing programs; "
              f"--smc-test {sbase_n} runs, {len(sbase_fail)} failing")
        ok = not base_fail and not sbase_fail
    for name, (desc, knobs, smc) in STRUCT.items():
        if only is not None and name not in only:
            continue
        try:
            B.build(smc, False, False, knobs)
            res, why = "SURVIVED", "build() raised no assertion"
        except AssertionError as e:
            res, why = "KILLED_STRUCTURAL", str(e)[:150]
        out["structural"][name] = dict(desc=desc, status=res, assertion=why)
        print(f"{res:22s} {name:34s} {why}")
        ok &= res == "KILLED_STRUCTURAL"
    for name, (desc, knobs, tags, equiv, kind) in MUT.items():
        if only is not None and name not in only:
            continue
        if kind == "smc":
            n, failing = judge(name, knobs, smcprogs, smc=True)
        else:
            n, failing = judge(name, knobs, prod if set(tags) & {"C2", "sweep"} else nonsweep)
        desig = sorted(p for p in failing if set(x.man[p]["tags"]) & set(tags))
        first = (desig or sorted(failing) or [None])[0]
        row = dict(desc=desc, knobs={k: (v if not isinstance(v, dict) else {hex(a): hex(b) for a, b in v.items()}) for k, v in knobs.items()},
                   designated_tags=tags, runs=n, failing_programs=len(failing),
                   designated_failing=len(desig), first=first, first_evidence=(failing[first][0] if first else None),
                   status=("KILLED_BY_DESIGNATED" if desig else "KILLED_OTHER" if failing else "EQUIVALENT" if equiv else "SURVIVED"),
                   equivalence_reason=equiv)
        if name == "nowrite":
            row["b5_gpr_event_failures"] = sorted(p for p in failing if "B5" in x.man[p]["tags"] and any("GPR write events" in e[2] for e in failing[p]))
        if name == "consume4":
            row["availability_checker_fired_runs"] = sum("SIMCHECK" in e[2] for p in failing for e in failing[p])
        # an EQUIVALENT mutant must also be killed structurally (the assertion is its catcher)
        if equiv and row["status"] == "EQUIVALENT":
            sk = f"struct_{name}"
            row["structural_catcher"] = sk if sk in STRUCT else None
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
    print("MUTANTS", "OK" if ok else "FAIL", f"behavioural: {k} killed + {e} equivalent of {len(out['mutants'])}; "
          f"structural {sum(r['status'].startswith('KILLED') for r in out['structural'].values())}/{len(out['structural'])}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
