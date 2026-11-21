#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""R3a driver (called by run_r3.sh after the benches are built): runs the corrected V60X, upstream and V2 on the
m1_r3.py programs, checks every log against the INDEPENDENT expectations (m1_r3_check.py), runs the structural
dispatch checks, the probe-image runs, the mutant-catcher contract (m1_r3a_catchers.py), the probe_movea_value
enforcement and the infrastructure-level negative controls, and prints a PASS/FAIL summary. Exit status 0 only if
everything passes and every negative control is rejected.

Phase r3a (today): V2 traps every MOVEA (opcodes 0x40 / 0x42 / 0x44) with `opcode` at the MOVEA itself after the
preceding instructions retire (the expected PRE-R3b state; S0 reach stays 2), and that is a PASS. Phase r3b flips the
V2 expectations to the planned behaviour; it is exercised in R3a only against the catcher model's synthetic logs and
(R3_SIM_R3B=1) against an in-memory table-only stub (m1_r3b_simbuilder.py).
"""
import argparse
import contextlib
import copy
import hashlib
import io
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "ref"))
import m1_r1_check as C1  # noqa: E402
import m1_r1_run as RR  # noqa: E402
import m1_r2_check as C2  # noqa: E402
import m1_r3_check as C  # noqa: E402

CONFIGS = RR.CONFIGS
results = RR.results
negctl = RR.negctl
note = RR.note
sh = RR.sh
M = 0xFFFFFFFF
FIXTURE = HERE / "results/r3a_pre_r3_fixture_2026-10-29.json"
PINS = HERE / "results/r3a_table_pins_2026-10-29.json"


def v2_text(x, name, m, cfg, run="std", mc=None, s0mc=None, extra_args=()):
    """One V2 run of a program; run: std | ack (fetches >= limit withheld for good) | delay (released by +ackdelay)"""
    base = run.replace("smc_", "")
    if mc is None:
        mc = x.out / ("mc_probe3" if m["mc"] == "probe" else "mc")
    syms = RR.symbols(mc if m["where"] == "low" else (s0mc or x.out / "mc.s0"))
    if m["where"] == "s0":
        extra = [f"+rom={x.progs / (name + '.rom.hex')}", "+maxinstr=1000", "+maxcyc=400000"] + cfg.split()
        sim, mcd = str(x.out / "obj_s0v2/sim"), s0mc or x.out / "mc.s0"
    else:
        extra = [f"+img={x.progs / (name + '.hex')}"] + cfg.split()
        sim, mcd = str(x.out / "obj_v2/sim"), mc
        if base == "ack":
            extra += ["+maxcyc=20000", f"+ackmax={m['extra']['limit']}"]
        elif base == "delay":
            extra += ["+maxcyc=60000", f"+ackmax={m['extra']['limit']}", f"+ackdelay={m['extra']['delay']}"]
        else:
            extra += ["+maxcyc=400000"]
    cmd = RR.v2_cmd(sim, mcd, extra + ["+privev", "+gprev"] + list(extra_args), syms)
    if m.get("el"):
        cmd.append(f"+forceel={m['el']}")
    r = sh(cmd)
    return r.stdout + r.stderr


def ref_text(x, name, m, kind):
    if kind == "ext":
        cmd = ["python3", str(HERE / "m1_ref.py"), "--lockstep", "--counters", "--privev", "--gprev", "--ext"]
        if m["where"] == "s0":
            cmd += ["--pc", "0xFFFFFFF0", "--rom-mirror", "--segs", str(x.progs / f"{name}.segs"), "-"]
        else:
            cmd += [str(x.progs / f"{name}.bin")]
        r = sh(cmd)
        return r.stdout + (("\nREFERR " + r.stderr[-300:]) if r.returncode else "")
    if m["where"] == "s0":
        cmd = [str(x.out / "obj_s0up/sim"), f"+rom={x.progs / (name + '.rom.hex')}", "+maxinstr=1000", "+counters", "+privev", "+memlog"]
    else:
        cmd = [str(x.out / "obj_up/sim"), f"+img={x.progs / (name + '.hex')}", "+maxcyc=200000", "+lockstep", "+counters", "+privev"]
    r = sh(cmd)
    return r.stdout + r.stderr


def memstat_bad(text):
    """the S0 memory model's per-region access counts (MEMSTAT lines): any access outside the program ROM and its mirror is
    a bus access the MOVEA must not make (an /abs target in I/O, work RAM, video ... must cause none)"""
    return [l for l in text.splitlines() if re.match(r"^MEMSTAT (read|write)\s+(?!rom\b|rom-mirror\b)\S+\s+\d+", l)]


def save(x, name, tag, text):
    (x.logs / f"{name}.{tag}").write_text(text)


def tagof(cfg, run="std"):
    return RR.tagof(cfg) + ("" if run == "std" else "_" + run)


# ------------------------------------------------------------------ stages
def stage_refs(x):
    print("== corrected V60X and upstream on every positive program")
    pos = [(n, m) for n, m in x.man.items() if m["refs"]]
    jobs = [(n, m, k) for n, m in pos for k in m["refs"]]

    def one(j):
        n, m, k = j
        t = ref_text(x, n, m, k)
        save(x, n, k, t)
        return n, m, k, C.check_ref3(m, t, k)
    with ThreadPoolExecutor(x.jobs) as ex:
        for n, m, k, f in ex.map(one, jobs):
            cat = f"{'V60X (corrected, ref_ext)' if k == 'ext' else 'upstream s32_v60'} ({'S0 harness' if m['where'] == 's0' else 'M1 bench'})"
            note(cat, not f, f"{n}: {f[:2]}")
    tot = lock = ev_ok = nonvac = 0
    for n, m in pos:
        de = C2.parse2(C1.read_log(x.logs / f"{n}.ext"))
        du = C2.parse2(C1.read_log(x.logs / f"{n}.up"))
        tot += 1
        lock += de["L"] == du["L"]
        cur, chg = {}, {}
        for i in sorted(de["P"]):
            for (rid, val) in de["P"][i]:
                if val != cur.get(rid, C2.PRIV_RESET.get(rid, 0)):
                    chg.setdefault(i, []).append((rid, val))
                cur[rid] = val
        ev_ok += chg == du["P"]
    note("V60X == upstream (lock-step records)", lock == tot, f"{tot - lock} programs differ")
    note("upstream privileged events == V60X events minus unchanged-value writes", ev_ok == tot, f"{tot - ev_ok} programs differ")
    # S0 harness: no access outside ROM / work RAM at all by a MOVEA window (the upstream +memlog "M" lines)
    sx = [(n, m) for n, m in pos if m["where"] == "s0"]
    for n, m in sx:
        t = C1.read_log(x.logs / f"{n}.up")
        d = C1.parse(t)
        mlines = [l for l in t.splitlines() if l.startswith("M ")]
        bad = [l for l in mlines if not re.match(r"M R rom", l)]      # the reset-vector area reads are all ROM / mirror
        note("upstream S0: no memory access outside ROM / work RAM / mirror during a MOVEA program (+memlog)", not mlines, f"{n}: {mlines[:2]}")
        ms = memstat_bad(t)
        note("upstream S0: the memory model's per-region counters show reads / writes only in ROM and its mirror (no I/O, work-RAM, video ... access by the MOVEA targets)", not ms, f"{n}: {ms[:2]}")
    x.n_refs_programs = tot


def stage_v2(x):
    print(f"== V2, phase {x.phase}")
    jobs = []
    for n, m in x.man.items():
        if m["v2"] is None:
            continue
        for cfg in CONFIGS:
            jobs.append((n, m, cfg, "std"))
            if m.get("v2_ack"):
                jobs.append((n, m, cfg, "ack"))
                jobs.append((n, m, cfg, "delay"))
    # k = 7 without release: the MOVEA completes (all 7 bytes present, only the next instruction is withheld) but does not
    # retire, so its write event is legitimate; the plain "ack" run is excluded for it in r3b
    jobs = [j for j in jobs if not (j[3] == "ack" and j[1]["extra"].get("k") == 7 and (x.phase == "r3b" or j[1]["mc"] == "probe"))]
    if x.sample:                       # stub runs: a bounded subset (the stub hangs by design)
        jobs = [j for j in jobs if (j[0].startswith(("a1_v0", "n1_", "n2_", "n3_m0_0", "a7_L16_k6", "a10", "a9_ga2")) or j[1]["mc"] == "probe")
                and j[2] in ("", "+wide")]

    def one(j):
        n, m, cfg, run = j
        t = v2_text(x, n, m, cfg, run)
        save(x, n, "v2_" + tagof(cfg, run), t)
        f = C.check_v2r3(m, t, x.phase, run)
        if m["where"] == "s0" and memstat_bad(t):
            f = f + [f"S0 memory model: access outside ROM / mirror {memstat_bad(t)[:1]}"]
        return j, f
    with ThreadPoolExecutor(x.jobs) as ex:
        for (n, m, cfg, run), f in ex.map(one, jobs):
            cat = (f"V2 {'probe image' if m['mc'] == 'probe' else 'production'} [{cfg or 'default'}]"
                   + {"std": "", "ack": " withheld fetch, never released", "delay": " withheld fetch, released by cycle count"}[run]
                   + (" S0 harness" if m["where"] == "s0" else ""))
            note(cat, not f, f"{n}: {f[:2]}")


def stage_smc(x):
    """SMC variant matrix (plan section 6 N5): JMP-free programs, all negatives (incl. the SMC-only [Rn] cases), the
    TEST-ONLY --smc-test builds, separate and merged store, M1 bench and reset-native S0 bench, 4 fetch configurations;
    the expectation is v2_smc (dtab[0x40] = trap in every smc build, in both phases). Programs with a JMP run on
    production builds only."""
    print("== SMC variant matrix (--smc-test builds)")
    for variant in ("smc", "smcm"):
        for bench in ("m1", "s0"):
            mc = x.out / (f"mc.{variant}" if bench == "m1" else f"mc.{variant}_s0")
            names = []
            for n, m in x.man.items():
                if m["mc"] != "prod" or m.get("v2_smc") is None or "jmp" in m["tags"]:
                    continue
                if (bench == "m1") == (m["where"] == "low"):
                    names.append(n)
            if x.sample:
                names = [n for n in names if n.startswith(("a1_v0", "n5_", "a9_direct", "a7_L16_k6", "n1_"))]
            for cfg in CONFIGS:
                def one(n):
                    m = x.man[n]
                    t = v2_text(x, n, m, cfg, "std", mc=mc if bench == "m1" else None, s0mc=mc)
                    return n, C.check_v2r3(m, t, x.phase, "smc")
                with ThreadPoolExecutor(x.jobs) as ex:
                    for n, f in ex.map(one, names):
                        note(f"SMC matrix [{variant}, {'M1 bench' if bench == 'm1' else 'S0 reset-native'}] [{cfg or 'default'}]",
                             not f, f"{n}: {f[:2]}")
                if bench == "m1":
                    l6 = [n for n in names if "A7" in x.man[n]["tags"]]
                    for run in ("smc_ack", "smc_delay"):
                        def one6(n, run=run):
                            m = x.man[n]
                            t = v2_text(x, n, m, cfg, run, mc=mc)
                            return n, C.check_v2r3(m, t, x.phase, run)
                        with ThreadPoolExecutor(x.jobs) as ex:
                            for n, f in ex.map(one6, l6):
                                note(f"SMC matrix A7 [{variant}] [{cfg or 'default'}] " + ("withheld, never released" if run == "smc_ack" else "withheld, released by cycle count"),
                                     not f, f"{n}: {f[:2]}")


# ------------------------------------------------------------------ structural
def tables_hash(dtab, mtab, skip_dtab=()):
    h = hashlib.sha256()
    for i in range(256):
        if i not in skip_dtab:
            h.update(dtab[i].to_bytes(4, "little"))
    for i in range(512):
        h.update(mtab[i].to_bytes(4, "little"))
    return h.hexdigest()


def cstore_hash(p, n=None):
    h = hashlib.sha256()
    for w in (p if n is None else p[:n]):
        h.update(int(w).to_bytes(16, "little"))
    return h.hexdigest()


def invariants(sym, dtab, mtab, smc, phase):
    """The structural facts of plan sections 5 and 6 (N5 isolation, the uniqueness invariant), as a list of violations
    (empty = all hold). Used on the real builder tables and on mutated copies by the catcher contract."""
    bad = []
    trap, tmode, mret = sym["trap"], sym["trap_mode"], sym["mret"]
    if phase == "r3a" or smc:
        for op in (0x40, 0x42, 0x44):
            if dtab[op] != trap:
                bad.append(f"dtab[{op:#04x}] is not the opcode trap ({'smc build' if smc else 'r3a'})")
    else:
        if "movea" not in sym or (dtab[0x40] & 0xFFF) != sym["movea"]:
            bad.append("dtab[0x40] is not the movea routine (production r3b)")
        for op in (0x42, 0x44):
            if dtab[op] != trap:
                bad.append(f"dtab[{op:#04x}] is not the opcode trap (movea.h / movea.w are out of scope)")
    if not smc:
        if mtab[0x0F3] != (0x14C << 12) | mret:
            bad.append("mtab[0x0F3] is not (0x14C << 12) | mret")
        odd = [i for i, v in mtab.items() if (v & 0xFFF) not in (trap, tmode) and not (((v >> 12) & 1) or ((v >> 12) & 2))]
        if odd != [0x0F3]:
            bad.append(f"non-register non-immediate supported mode-table entries {[hex(i) for i in odd]} (must be exactly [0xf3])")
        for n in range(16):                                         # C_IMM / C_QUICK source: #q
            if mtab[0xE0 | n] != ((1 << 1) << 12 | mret):
                bad.append(f"mtab[{0xE0 | n:#x}] is not the #q descriptor")
        if mtab[0xF4] != (((1 << 1) | (3 << 2)) << 12 | mret):
            bad.append("mtab[0xF4] is not the #imm32 descriptor")
        for r in range(32):                                         # C_REGDIR: modm 1 register direct
            if mtab[0x100 | 0x60 | r] != (1 << 12 | mret):
                bad.append(f"mtab[{0x100 | 0x60 | r:#x}] is not the register-direct descriptor")
        # F3 with m = 1 is not an accepted mode
        if (mtab[0x1F3] & 0xFFF) != tmode:
            bad.append("mtab[0x1F3] (m = 1, mode byte F3) is not trap_mode")
    else:
        if mtab[0x0F3] != tmode:
            bad.append("smc: mtab[0x0F3] must stay trap_mode")
    return bad


def accepted_by_table(sym, mtab):
    """the (m, mode byte) entries the mode TABLE accepts (not trap / trap_mode): the sweep's accepted set"""
    return sorted(i for i, v in mtab.items() if (v & 0xFFF) not in (sym["trap"], sym["trap_mode"]))


def stage_struct(x):
    print("== dispatch isolation and table invariants over the eight builder option combinations")
    import m1_m11_microcode as B
    pins = json.loads(PINS.read_text()) if PINS.exists() else None
    x.pin_rows = {}
    for st in (False, True):
        for sm in (False, True):
            for rn in (False, True):
                p, sym, dtab, mtab = B.build(st, sm, rn)      # build() runs check_tables / check_ldpr
                key = f"smc_test={int(st)} smc_merged={int(sm)} reset_native={int(rn)}"
                bad = invariants(sym, dtab, mtab, st, x.phase)
                what = ("dtab[0x40/0x42/0x44] are the opcode trap (pre-R3b state)" if x.phase == "r3a" or st else
                        "dtab[0x40] = movea, 0x42/0x44 trap") + "; mode-table classification and uniqueness invariants"
                note(f"dispatch isolation and invariants ({what})", not bad, f"{key}: {bad[:2]}")
                acc = accepted_by_table(sym, mtab)
                exp_acc = ([] if st else sorted([0xF3] + [0xE0 | n for n in range(16)] + [0xF4] + [0x100 | 0x60 | r for r in range(32)]))
                if st:       # smc: [Rn] register indirect (modm 0, 0x60|r) is accepted by the table
                    exp_acc = sorted([0x60 | r for r in range(32)] + [0xE0 | n for n in range(16)] + [0xF4] + [0x100 | 0x60 | r for r in range(32)])
                note("mode table accepts exactly: /abs, #q (16), #imm, modm-1 register direct (32) [smc: [Rn] instead of /abs]",
                     acc == exp_acc, f"{key}: {len(acc)} entries, expected {len(exp_acc)}")
                hs = {"tables_full": tables_hash(dtab, mtab), "tables_except_dtab40": tables_hash(dtab, mtab, (0x40,)),
                      "cstore_len": len(p), "cstore_sha": cstore_hash(p)}
                x.pin_rows[key] = hs
                if pins is not None and key in pins:
                    pr = pins[key]
                    if x.phase == "r3a":
                        ok = pr["tables_full"] == hs["tables_full"] and pr["cstore_sha"] == hs["cstore_sha"]
                        what = "tables and control store identical to the pre-R3 pins"
                    else:
                        ok = (pr["tables_except_dtab40"] == hs["tables_except_dtab40"]
                              and cstore_hash(p, pr["cstore_len"]) == pr["cstore_sha"])
                        what = "tables except dtab[0x40], and the first words of the control store, identical to the pre-R3 pins"
                    note(f"builder state ({what})", ok, key)
    # the privileged-flag / R1 / R2 isolation of earlier steps still holds
    regs_rtl = (x.root / "experiments/V2-datapath-floor/rtl/v2_glue.sv").read_text()
    note("V2 immediate extraction: imm_val is zero/sign-extended only for byte/half sizes; a word-size immediate returns all 32 bits (IMM_SX equivalence basis)",
         "(isz == 2'd0) ? {{24{sx & win_sh[7]}},  win_sh[7:0]}" in regs_rtl and "win_sh[31:0];" in regs_rtl, "v2_glue.sv imm_val")


# ------------------------------------------------------------------ probe image
def load_fixture():
    fx = json.loads(FIXTURE.read_text())
    return ([int(v, 16) for v in fx["cstore"]], dict(fx["sym"]), dict(enumerate(fx["dtab"])), dict(enumerate(fx["mtab"])), fx)


def build_probe3(x, name="mc_probe3", mut=None):
    """TEST-ONLY microcode image for the bench probes, built IN MEMORY from the FROZEN pre-R3 tables (the builder file
    is not edited, no movea routine, no dtab[0x40] entry). Spare opcodes:
      0xFE  probe_movea: the plan's sketch WITHOUT its guards: CONSUME 2; operand lookup; the execute word
            (IMM_LD, IMM_OFS 1, IMM_SZ 2, CONSUME 5, ASEL = IMM, PASSA, WR_DST GPR, WR_ASEL 1); jump to dispatch
      0xFC  trap `mode` right after CONSUME 2;  0xFB  trap `mode` after the operand lookup (plan 9 ASSUMED #3)
    mut: probe mutants (imm_ofs, imm_sz, imm_sx, imm_consume, fmt_consume, wr_dst, wr_asel, flag_upd, dup, nowrite,
    transient (the 0xFC routine writes a GPR and restores it before the trap), dflags (dispatch format bits))."""
    mut = mut or {}
    import m1_m11_microcode as B
    from microasm import word, A, ALU, RES, cond
    p, sym, dtab, mtab, fx = load_fixture()
    assert dtab[0x40] == sym["trap"], "fixture must be pre-R3"
    base = len(p)
    W = lambda **f: word(**dict(dict(SIZE_SEL=2, BE_MODE=1), **f))

    def poll(msel):
        here = len(p)
        p.append(word(NXT=2, CPOL=1, JADDR=here, IMM_OFS=0, MODM_SEL=msel, **cond(B.C_READY)))

    sym["probe_movea"] = len(p)
    p.append(word(CONSUME=mut.get("fmt_consume", 2)))
    poll(B.MS_B6)
    p.append(word(NXT=3, JADDR=sym["mstub1"], IMM_OFS=0, MODM_SEL=B.MS_B6))
    imm = dict(IMM_LD=1, IMM_OFS=mut.get("imm_ofs", 1), IMM_SZ=mut.get("imm_sz", 2), CONSUME=mut.get("imm_consume", 5),
               ASEL=A["IMM"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
    if mut.get("imm_sx"):
        imm["IMM_SX"] = 1
    wr = dict(WR_DST=mut.get("wr_dst", B.WR["GPR"]), WR_ASEL=mut.get("wr_asel", 1))
    if "flag_upd" in mut:
        wr["FLAG_UPD"] = mut["flag_upd"]
    if mut.get("nowrite"):
        p.append(W(**dict(imm, WR_DST=B.WR["TEMP"], TADDR=B.T0)))                 # value to T0, nothing written to a GPR
    elif mut.get("dup"):
        p.append(W(**dict(imm, WR_DST=B.WR["TEMP"], TADDR=B.T0)))
        p.extend([word()] * 3)                                                    # a temp is readable four steps after its write
        wd = W(TADDR=B.T0, ASEL=A["RT"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], **wr)
        p.append(wd)
        p.append(wd)                                                              # the same write twice
    else:
        p.append(W(**imm, **wr))
    p.append(word(NXT=1, JADDR=sym["disp"]))
    # traps in the middle
    sym["probe_mid"] = len(p)
    p.append(word(CONSUME=2))
    if mut.get("transient"):          # save the old value, write a constant, restore: the value never changes, then trap
        p.append(W(RA_SEL=1, ASEL=A["RA"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=B.WR["TEMP"], TADDR=B.T0))
        p.extend([word()] * 3)
        p.append(W(ASEL=A["CONST"], RCONST=7, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=B.WR["GPR"], WR_ASEL=1))
        p.append(W(TADDR=B.T0, ASEL=A["RT"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=B.WR["GPR"], WR_ASEL=1))
    p.append(word(NXT=1, JADDR=sym["trap_mode"]))
    sym["probe_mid2"] = len(p)
    p.append(word(CONSUME=2))
    poll(B.MS_B6)
    p.append(word(NXT=3, JADDR=sym["mstub1"], IMM_OFS=0, MODM_SEL=B.MS_B6))
    p.append(word(NXT=1, JADDR=sym["trap_mode"]))
    if mut.get("mtab_ofs16"):          # the /abs descriptor with a 16-bit offset-size field (unused by the sketch: IMM_SZ is explicit)
        mtab[0x0F3] = (0x148 << 12) | sym["mret"]
    dfl = mut.get("dflags", 0)
    dtab[0xFE] = (dfl << 12) | sym["probe_movea"]
    dtab[0xFC] = sym["probe_mid"]
    dtab[0xFB] = sym["probe_mid2"]
    mc = RR.write_mc(x, name, B, p, sym, dtab, mtab)
    return mc, len(p) - base


def stage_probe_report(x):
    """Probe results that are not pass/fail groups of their own: the execute word of the plan's sketch on the real V2 (every
    probe3 program passes in stage_v2), the trap-in-the-middle probes, the dispatch-format-bits probe."""
    # (1) trap in the middle: the trapped instruction reports its start PC and left no side effect (checked by stage_v2:
    #     TRAP ipc, no G / P / data events of the unretired instruction after the drain, counters zero)
    n = sum(1 for m in x.man.values() if "trapmid" in m["tags"])
    x.trapmid = n


# ------------------------------------------------------------------ probe_movea_value
def stage_probe_value(x):
    import m1_probe_movea as PV
    print("== probe_movea_value (D1) enforcement")
    d = x.out / "probe_value"
    d.mkdir(exist_ok=True)
    ok, why, detail = PV.run(d, PV.default_runners(x.out / "obj_up/sim"))
    note("probe_movea_value reproduces D1: v60_ref R1=0x5A dr=1 / upstream R1=0x20 dr=0, both runs successful, no unrelated difference", ok, why)
    x.probe_detail = detail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--phase", default="r3a")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--negctl", type=int, default=1)
    ap.add_argument("--root", required=True)
    ap.add_argument("--sim-r3b", action="store_true")
    ap.add_argument("--builder-module", default=None, help="R4 tooling: a module whose install() replaces the live builder (m1_r4_fixbuilder)")
    a = ap.parse_args()
    if a.sim_r3b:
        import m1_r3b_simbuilder as SB
        SB.install()
    if a.builder_module:
        import importlib
        importlib.import_module(a.builder_module).install()
    x = RR.Ctx()
    x.out, x.phase, x.jobs, x.root = Path(a.out), a.phase, a.jobs, Path(a.root)
    x.sample = a.sim_r3b
    x.sim_stub = a.sim_r3b
    x.progs = x.out / "progs"
    x.logs = x.out / "logs"
    x.logs.mkdir(exist_ok=True)
    x.man = json.load(open(x.progs / "r3_manifest.json"))
    classes = {}
    for m in x.man.values():
        k = f"{m['cls']}/{m['where']}"
        classes[k] = classes.get(k, 0) + 1
    print("programs: " + ", ".join(f"{k} {v}" for k, v in sorted(classes.items())) + f" (total {len(x.man)})")
    mc, nprobe = build_probe3(x)
    print(f"probe image: {nprobe} extra control-store words (TEST-ONLY, in memory; builder untouched)")
    stage_refs(x)
    stage_v2(x)
    stage_smc(x)
    stage_struct(x)
    stage_probe_report(x)
    stage_probe_value(x)
    if a.negctl:
        import m1_r3a_negctl as N
        N.run(x, negctl, note)
        import m1_r3a_catchers as K
        K.run(x, negctl, note)
        import m1_r3a_s0 as S0
        S0.run(x, negctl, note)
    print("\n================ R3a SUMMARY (phase %s) ================" % x.phase)
    bad = 0
    for cat, (p, t, fl) in results.items():
        print(f"{'PASS' if p == t else 'FAIL'}  {cat}: {p}/{t}")
        for f in fl[:5]:
            print(f"      fail: {f}")
        bad += (p != t)
    nbad = 0
    if a.negctl:
        print("\nnegative controls and catchers (each must be REJECTED / caught):")
        for cid, desc, caught, ev in negctl:
            lab = "EQUIV  " if (cid.endswith("E") and caught) else "CAUGHT " if caught else "MISSED "
            print(f"{lab} ({cid}) {desc}\n         {ev}")
            nbad += (not caught)
    ok = bad == 0 and nbad == 0
    print(f"\nR3a {'PASS' if ok else 'FAIL'}: {len(results) - bad}/{len(results)} result groups pass"
          + (f", {len(negctl) - nbad}/{len(negctl)} negative controls/catchers caught" if a.negctl else ""))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
