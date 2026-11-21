#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""R2a driver (called by run_r2.sh after the benches are built): runs V60X, upstream and V2 on the m1_r2.py
programs, checks every log against the INDEPENDENT expectations (m1_r2_check.py), runs the structural
dispatch-isolation checks, the bench probes, the mutant-catcher contract (m1_r2a_catchers.py) and the
infrastructure-level negative controls, and prints a PASS/FAIL summary. Exit status 0 only if everything
passes and every negative control is rejected.

Phase r2a (today): V2 traps every LDPR with `opcode` at the LDPR after the preceding instructions retire (the
expected PRE-R2b state; S0 reach stays 1, trap at 0x00100506), and that is a PASS. Phase r2b flips the V2
expectations to the planned behaviour; it is exercised in R2a only against the catcher model's synthetic logs.
"""
import argparse
import copy
import hashlib
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
import m1_r2_check as C  # noqa: E402

CONFIGS = RR.CONFIGS
results = RR.results
negctl = RR.negctl
note = RR.note
sh = RR.sh


def v2_text(x, name, m, cfg, run="std", mc=None, s0mc=None, extra_args=()):
    """One V2 run of a program; run: std | ack (fetches >= limit withheld for good) | delay (released by +ackdelay)."""
    if mc is None:
        mc = x.out / ("mc_probe" if m["mc"] == "probe" else "mc")
    syms = RR.symbols(mc if m["where"] == "low" else (s0mc or x.out / "mc.s0"))
    if m["where"] == "s0":
        extra = [f"+rom={x.progs / (name + '.rom.hex')}", "+maxinstr=1000", "+maxcyc=400000"] + cfg.split()
        sim, mcd = str(x.out / "obj_s0v2/sim"), s0mc or x.out / "mc.s0"
    else:
        extra = [f"+img={x.progs / (name + '.hex')}"] + cfg.split()
        sim, mcd = str(x.out / "obj_v2/sim"), mc
        if run == "ack":
            extra += ["+maxcyc=20000", f"+ackmax={m['extra']['limit']}"]
        elif run == "delay":
            extra += ["+maxcyc=60000", f"+ackmax={m['extra']['limit']}", f"+ackdelay={m['extra']['delay']}"]
        else:
            extra += ["+maxcyc=400000"]
    cmd = RR.v2_cmd(sim, mcd, extra + ["+privev"] + list(extra_args), syms)
    for k, opt in (("trap_priv", "trapprivpc"), ("trap_regid", "trapregidpc")):
        if k in syms:
            cmd.append(f"+{opt}={syms[k]}")
    if m.get("el"):
        cmd.append(f"+forceel={m['el']}")
    r = sh(cmd)
    return r.stdout + r.stderr


def ref_text(x, name, m, kind):
    if kind == "ext":
        cmd = ["python3", str(HERE / "m1_ref.py"), "--lockstep", "--counters", "--privev", "--ext"]
        if m["where"] == "s0":
            cmd += ["--pc", "0xFFFFFFF0", "--rom-mirror", "--segs", str(x.progs / f"{name}.segs"), "-"]
        else:
            cmd += [str(x.progs / f"{name}.bin")]
        r = sh(cmd)
        return r.stdout + (("\nREFERR " + r.stderr[-300:]) if r.returncode else "")
    if m["where"] == "s0":
        cmd = [str(x.out / "obj_s0up/sim"), f"+rom={x.progs / (name + '.rom.hex')}", "+maxinstr=1000", "+counters", "+privev"]
    else:
        cmd = [str(x.out / "obj_up/sim"), f"+img={x.progs / (name + '.hex')}", "+maxcyc=200000", "+lockstep", "+counters", "+privev"]
    r = sh(cmd)
    return r.stdout + r.stderr


def save(x, name, tag, text):
    (x.logs / f"{name}.{tag}").write_text(text)


def tagof(cfg, run="std"):
    return RR.tagof(cfg) + ("" if run == "std" else "_" + run)


# ------------------------------------------------------------------ stages
def stage_refs(x):
    print("== V60X and upstream on every positive program")
    pos = [(n, m) for n, m in x.man.items() if m["cls"] == "pos"]
    jobs = [(n, m, k) for n, m in pos for k in ("ext", "up")]

    def one(j):
        n, m, k = j
        t = ref_text(x, n, m, k)
        save(x, n, k, t)
        return n, m, k, C.check_ref2(m, t, k)
    with ThreadPoolExecutor(x.jobs) as ex:
        for n, m, k, f in ex.map(one, jobs):
            cat = f"{'V60X (ref_ext)' if k == 'ext' else 'upstream s32_v60'} ({'S0 harness' if m['where'] == 's0' else 'M1 bench'})"
            note(cat, not f, f"{n}: {f[:2]}")
    # the three agree with each other, not only with the expectation
    tot = lock = ev_ok = invis = 0
    for n, m in pos:
        de = C.parse2(C1.read_log(x.logs / f"{n}.ext"))
        du = C.parse2(C1.read_log(x.logs / f"{n}.up"))
        tot += 1
        lock += de["L"] == du["L"]
        # upstream's events must be EXACTLY V60X's events minus the unchanged-value writes (derived here from
        # V60X's own log and the reset values; the expectation check above is separate)
        cur, chg = {}, {}
        for i in sorted(de["P"]):
            for (rid, val) in de["P"][i]:
                if val != cur.get(rid, C.PRIV_RESET.get(rid, 0)):
                    chg.setdefault(i, []).append((rid, val))
                else:
                    invis += 1
                cur[rid] = val
        ev_ok += chg == du["P"]
    note("V60X == upstream (lock-step records)", lock == tot, f"{tot - lock} programs differ")
    note("upstream events == V60X events minus unchanged-value writes", ev_ok == tot, f"{tot - ev_ok} programs differ")
    x.invisible_writes = invis


def stage_v2(x):
    print(f"== V2, phase {x.phase}")
    jobs = []
    for n, m in x.man.items():
        for cfg in CONFIGS:
            jobs.append((n, m, cfg, "std"))
            if m.get("v2_ack"):
                jobs.append((n, m, cfg, "ack"))
                jobs.append((n, m, cfg, "delay"))
    # k = 8 without release: the LDPR completes (all 8 bytes present) but is not retired (the next instruction is
    # withheld), so its write event is legitimate; the plain "ack" run is excluded for it
    jobs = [j for j in jobs if not (j[3] == "ack" and j[1]["extra"].get("k") == 8 and x.phase == "r2b")]

    def one(j):
        n, m, cfg, run = j
        t = v2_text(x, n, m, cfg, run)
        save(x, n, "v2_" + tagof(cfg, run), t)
        return j, C.check_v2r2(m, t, x.phase, run)
    with ThreadPoolExecutor(x.jobs) as ex:
        for (n, m, cfg, run), f in ex.map(one, jobs):
            cat = (f"V2 {'probe image' if m['mc'] == 'probe' else 'production'} [{cfg or 'default'}]"
                   + {"std": "", "ack": " withheld fetch, never released", "delay": " withheld fetch, released by cycle count"}[run]
                   + (" S0 harness" if m["where"] == "s0" else ""))
            note(cat, not f, f"{n}: {f[:2]}")


def stage_smc(x):
    """SMC variant matrix (plan 9 #6): JMP-free positives, all negatives (incl. the SMC-only [Rn] cases) on the
    TEST-ONLY --smc-test builds, separate and merged store, M1 bench and reset-native S0 bench, 4 fetch
    configurations; the expectation is v2_smc (LDPR is dispatched in both builds in r2b). L5 / L7 (JMP) run on
    production builds only."""
    print("== SMC variant matrix (--smc-test builds)")
    for variant in ("smc", "smcm"):
        for bench in ("m1", "s0"):
            mc = x.out / (f"mc.{variant}" if bench == "m1" else f"mc.{variant}_s0")
            names = []
            for n, m in x.man.items():
                if m["mc"] != "prod" or m.get("v2_smc") is None or "jmp" in m["tags"]:
                    continue
                if (bench == "m1") == (m["where"] == "low") and (m["cls"] != "pos" or "L7" not in m["tags"] or m["where"] == "s0"):
                    if bench == "s0" and m["cls"] != "pos":
                        continue
                    names.append(n)
            for cfg in CONFIGS:
                def one(n):
                    m = x.man[n]
                    t = v2_text(x, n, m, cfg, "std", mc=mc if bench == "m1" else None, s0mc=mc)
                    return n, C.check_v2r2(m, t, x.phase, "smc")
                with ThreadPoolExecutor(x.jobs) as ex:
                    for n, f in ex.map(one, names):
                        note(f"SMC matrix [{variant}, {'M1 bench' if bench == 'm1' else 'S0 reset-native'}] [{cfg or 'default'}]",
                             not f, f"{n}: {f[:2]}")
                if bench == "m1":                  # L6 under the SMC builds: fetches withheld for good, and released by cycle count
                    l6 = [n for n in names if "L6" in x.man[n]["tags"]]
                    for run in ("ack", "delay"):
                        def one6(n, run=run):
                            m = x.man[n]
                            t = v2_text(x, n, m, cfg, run, mc=mc)
                            return n, C.check_v2r2(m, t, x.phase, run)
                        with ThreadPoolExecutor(x.jobs) as ex:
                            for n, f in ex.map(one6, [n for n in l6 if not (run == "ack" and x.man[n]["extra"]["k"] == 8 and x.phase == "r2b")]):
                                note(f"SMC matrix L6 [{variant}] [{cfg or 'default'}] " + ("withheld, never released" if run == "ack" else "withheld, released by cycle count"),
                                     not f, f"{n}: {f[:2]}")


# ------------------------------------------------------------------ structural
def _derelocate(sym, key, dtab, mtab):
    """R4b: map every routine address in the tables back to the pre-R4 fixture's address of the same symbol (m1_r4_fixture.py)"""
    fsym = json.loads((HERE / "results/r4a_pre_r4_fixture_2026-11-04.json").read_text())["configs"][key]["sym"]
    names = {}
    for n, a in sym.items():
        names.setdefault(int(a), []).append(n)

    def back(v):
        fa = {int(fsym[n]) for n in names.get(v & 0xFFF, []) if n in fsym}
        if not fa:                      # B1: a routine that did not exist in the pre-R4 fixture (movb, dbr, ...): marked, so the pin comparison FAILS cleanly
            return v | (1 << 30)
        assert len(fa) == 1, f"table entry {v:#x}: symbols {names.get(v & 0xFFF)} map to fixture addresses {fa}"
        return (v >> 12 << 12) | fa.pop()
    return {i: back(v) for i, v in dtab.items()}, {i: back(v) for i, v in mtab.items()}


def tables_hash(dtab, mtab):
    h = hashlib.sha256()
    for i in range(256):
        if i != 0x12:
            h.update(dtab[i].to_bytes(4, "little"))
    for i in range(512):
        h.update(mtab[i].to_bytes(4, "little"))
    return h.hexdigest()


# pins: the pre-R2 builder (R1b, sha256 86f2a302...) tables over all 8 option combinations, recorded in R2a
# (everything except dtab[0x12]); R2b must leave them unchanged and only retarget dtab[0x12]
def stage_struct(x):
    print("== dispatch isolation over the eight builder option combinations")
    import m1_m11_microcode as B
    pins = json.loads((HERE / "results/r2a_table_pins_2026-10-28.json").read_text()) if \
        (HERE / "results/r2a_table_pins_2026-10-28.json").exists() else None
    x.pin_rows = {}
    for st in (False, True):
        for sm in (False, True):
            for rn in (False, True):
                p, sym, dtab, mtab = B.build(st, sm, rn)      # build() runs check_tables
                key = f"smc_test={int(st)} smc_merged={int(sm)} reset_native={int(rn)}"
                trap = sym["trap"]
                if x.phase == "r2a":
                    ok = dtab[0x12] == trap
                    what = "dtab[0x12] is the opcode trap (pre-R2b state)"
                else:
                    # r2b: dtab[0x12] retargets to the ldpr routine with the privileged flag (fmt bit 7); the trap kinds exist
                    ok = ("ldpr" in sym and (dtab[0x12] & 0xFFF) == sym["ldpr"] and bool((dtab[0x12] >> 19) & 1)
                          and "trap_priv" in sym and "trap_regid" in sym)
                    what = "dtab[0x12] is the ldpr routine with the privileged flag; trap_priv / trap_regid exist"
                note(f"dispatch isolation ({what})", ok, key)
                # LDPR's operands are already in the production mode tables, in BOTH modm banks where they exist:
                # #q (E0-EF) and #imm32 (F4) are modm 0 entries with the immediate descriptor; nothing new
                q_ok = all(mtab[0xE0 | n] == ((1 << 1) << 12 | sym["mret"]) for n in range(16))
                f4_ok = mtab[0xF4] == (((1 << 1) | (3 << 2)) << 12 | sym["mret"])
                none_modm1_imm = all(not ((mtab[0x100 + i] >> 12) & 2) for i in range(256))
                note("mode tables: #q, #imm32 present (modm 0, immediate descriptors); no modm-1 immediate", q_ok and f4_ok and none_modm1_imm, key)
                # R3b adds dtab[0x40] = movea (production): the pre-R2 pins describe the tables with dtab[0x40] = trap
                # (and R4b: the PC-relative tag entry mtab[0x0F2] is trap_mode in the pins, and the jmp guard word RELOCATES every later routine and the
                #  trap labels, so the production tables are compared with every address mapped back through its symbol to the pre-R4 fixture's)
                dd, mm = {**dtab, 0x40: trap}, {**mtab, 0x0F2: sym["trap_mode"]}
                if "movea_pcrel" in sym:
                    dd, mm = _derelocate(sym, key, dd, mm)
                hsh = tables_hash(dd, mm)
                x.pin_rows[key] = hsh
                if pins is not None:
                    note("tables (dtab except 0x12, mtab) unchanged vs the pre-R2 pins", pins.get(key) == hsh, key)
                # the production 0xD6 / smc isolation of R1 still holds
                note("R1 isolation unchanged (production D6 = jmp; --smc-test D6 = trap)",
                     (dtab[0xD6] == sym["jmp"]) if not st else (dtab[0xD6] == trap), key)
                # privileged flag: no existing dispatch entry carries it (fmt bit 7 -> packed bit 19)
                flagged = [i for i in range(256) if (dtab[i] >> 19) & 1]
                note("privileged dispatch flag: " + ("no entry has it today" if x.phase == "r2a" else "only dtab[0x12] has it"),
                     flagged == ([] if x.phase == "r2a" else [0x12]), key)
    # SBR is privileged-file entry 5 (plan 3c): the layout comment of v2_regs.sv and the vector-address comment of v2_agu.sv
    regs_rtl = (x.root / "experiments/V2-datapath-floor/rtl/v2_regs.sv").read_text()
    agu_rtl = (x.root / "experiments/V2-datapath-floor/rtl/v2_agu.sv").read_text()
    note("file layout: ISP, L0SP-L3SP, SBR in manual ID order (SBR = entry 5); the vector address is SBR + 4*vector from the privileged file",
         "(ISP, L0SP-L3SP, SBR" in regs_rtl and "SBR + 4*vector" in agu_rtl, "v2_regs.sv, v2_agu.sv")


# ------------------------------------------------------------------ probe image
def build_probe(x, name="mc_probe", mut=None):
    """TEST-ONLY microcode image for the bench probes, built IN MEMORY from the production builder's output
    (the builder file is not edited, no LDPR routine and no dtab[0x12] entry): spare opcodes 0xFE (a
    stripped-down LDPR-shaped routine WITHOUT guards or an ID check, to measure WR_ASEL = 3), 0xFD / 0xFC (the
    `priv` / `regid` trap kinds) and 0xFB (a dispatch entry with the privileged flag, branching on ucond 16).
    mut: the probe-routine mutants of the catcher contract (imm_consume, imm_ofs, imm_sz, wr_asel, wr_dst,
    flag_upd, final_consume), applied to the words that carry the planned LDPR's immediate and write steps."""
    mut = mut or {}
    import m1_m11_microcode as B
    from microasm import word, A, ALU, RES, cond
    fx = json.loads((HERE / "results/r2a_pre_r2_fixture_2026-10-28.json").read_text())   # FROZEN pre-R2 tables, not the live builder
    p = [int(v, 16) for v in fx["cstore"]]
    sym = dict(fx["sym"])
    dtab = dict(enumerate(fx["dtab"]))
    mtab = dict(enumerate(fx["mtab"]))
    assert dtab[0x12] == sym["trap"], "fixture must be pre-R2"
    base = len(p)
    W = lambda **f: word(**dict(dict(SIZE_SEL=2, BE_MODE=1), **f))

    def poll(msel):
        here = len(p)
        p.append(word(NXT=2, CPOL=1, JADDR=here, IMM_OFS=0, MODM_SEL=msel, **cond(B.C_READY)))

    sym["probe_wr"] = len(p)
    p.append(word(CONSUME=2))
    poll(B.MS_B6)
    p.append(word(NXT=3, JADDR=sym["mstub1"], IMM_OFS=0, MODM_SEL=B.MS_B6))
    p.append(W(IMM_LD=1, IMM_OFS=mut.get("imm_ofs", 1), IMM_SZ=mut.get("imm_sz", 2), CONSUME=mut.get("imm_consume", 5),
               ASEL=A["IMM"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=B.WR["TEMP"], TADDR=B.T0))
    poll(B.MS_B5)
    p.append(word(NXT=3, JADDR=sym["mstub2"], IMM_OFS=0, MODM_SEL=B.MS_B5))
    wr = dict(WR_DST=mut.get("wr_dst", 3), WR_ASEL=mut.get("wr_asel", 3), CONSUME=mut.get("final_consume", 1))
    if "flag_upd" in mut:
        wr["FLAG_UPD"] = mut["flag_upd"]
    p.append(W(**dict(dict(TADDR=B.T0, ASEL=A["RT"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"]), **wr)))
    p.append(word(NXT=1, JADDR=sym["disp"]))
    for t in ("trap_priv", "trap_regid"):
        sym[t] = len(p)
        p.append(word(NXT=1, JADDR=len(p)))
    sym["probe_priv"] = len(p)
    p.append(word(CONSUME=1))
    p.append(word(NXT=2, CPOL=0, JADDR=sym["trap_priv"], **cond(16)))
    p.append(word(NXT=1, JADDR=sym["disp"]))
    dtab[0xFE] = sym["probe_wr"]
    dtab[0xFD] = sym["trap_priv"]
    dtab[0xFC] = sym["trap_regid"]
    dtab[0xFB] = (1 << 7) << 12 | sym["probe_priv"]          # fmt bit 7 = privileged -> dflags[0] -> ucond 16
    mc = RR.write_mc(x, name, B, p, sym, dtab, mtab)
    return mc, len(p) - base


# ------------------------------------------------------------------ infrastructure-level negative controls
def negctl_refs(x):
    """(a) V60X in-process mutants of LDPR: a wrong value / ID / extra / missing write, a flag or register change,
    a data read; each must be rejected by the independent expectations on the positive programs."""
    print("== negative controls (a): V60X mutants of LDPR (in memory only)")
    import m1_ref as MR
    import m1_ref_ext as X
    MR.LOCKSTEP = MR.COUNTERS = MR.PRIVEV = True
    pos = [(n, m) for n, m in x.man.items() if m["cls"] == "pos" and m["where"] == "low"]

    def run_mut(kind):
        orig = X.V60X.step

        def step(self):
            if self._b(0) == 0x12 and self._b(1) == 0x80 and self._b(2) == 0xF4:
                v = int.from_bytes(bytes(self._b(i) for i in range(3, 7)), "little")
                rid = self._b(7) & 15
                if kind == "value_ofs":
                    v = int.from_bytes(bytes(self._b(i) for i in range(2, 6)), "little")
                elif kind == "value_lo16":
                    v &= 0xFFFF
                elif kind == "wrong_id":
                    rid = 5 if rid != 5 else 4
                elif kind == "no_write":
                    self.pc += 8
                    return
                elif kind == "double_write":
                    self.priv[X.PRIV[rid]] = v
                elif kind == "wrong_len":
                    self.priv[X.PRIV[rid]] = v
                    self.pc += 7
                    return
                self.priv[X.PRIV[rid]] = v
                if kind == "flag":
                    self.cy ^= 1
                if kind == "reg":
                    self.r[6] = (self.r[6] + 1) & 0xFFFFFFFF
                if kind == "dataread":
                    self.mem.r(0x800, 2)
                if kind == "extra_id":
                    self.priv[X.PRIV[(rid % 4) + 2 if rid != 2 else 3]] = 0x12345678
                self.pc += 8
                return
            orig(self)
        return step, orig

    descs = {"value_ofs": "value read from byte offset 2 instead of 3", "value_lo16": "value truncated to 16 bits",
             "wrong_id": "writes the wrong register ID", "no_write": "no privileged write", "double_write": "the write happens twice",
             "wrong_len": "instruction length 7", "flag": "CY toggled", "reg": "R6 changed", "dataread": "an extra data read",
             "extra_id": "an additional write to another register"}
    for kind, desc in descs.items():
        step, orig = run_mut(kind)
        X.V60X.step = step
        caught, first = 0, None
        try:
            for n, m in pos:
                buf = io.StringIO()
                try:
                    with contextlib.redirect_stdout(buf):
                        cpu, mem, cnt = MR.run((x.progs / f"{n}.bin").read_bytes(), 20000, start_pc=0, ext=True)
                        pr = "".join(f"PR id={i} val={cpu.priv[nm] & 0xFFFFFFFF:08x}\n" for i, nm in X.PRIV.items())
                        t = buf.getvalue() + pr + MR.dump(cpu, mem, cnt)
                except Exception as e:
                    t = buf.getvalue() + f"\nREFERR {e}\n"
                f = C.check_ref2(m, t, "ext")
                if f:
                    caught += 1
                    first = first or f"{n}: {f[0][:100]}"
        finally:
            X.V60X.step = orig
        negctl.append(("a", f"V60X mutant: {desc}", caught > 0, f"{caught}/{len(pos)} programs fail; first: {first}"))
    MR.PRIVEV = False
    MR.COUNTERS = MR.LOCKSTEP = False


def negctl_events(x):
    """(b) injected wrong privileged events / PC / retire / flag changes into REAL logs: upstream, V60X, V2 (r2a
    traps) and a model-correct r2b log; each corruption must be rejected."""
    print("== negative controls (b): corrupted logs and expectations")
    for n in ("l2_ow_zero_to_zero", "l2_ow_three"):
        m = x.man[n]
        for kind in ("ext", "up"):
            t = C1.read_log(x.logs / f"{n}.{kind}")
            base = C.check_ref2(m, t, kind)
            negctl.append(("b0", f"control: the {kind} log of {n} passes", not base, f"{base[:1]}"))
            pev = [i for i, l in enumerate(t.splitlines()) if l.startswith("P ")]
            edits = [("final register dump wrong", lambda L: [re.sub(r"(PR id=4 val=)\w{8}", r"\g<1>00000009", l) for l in L]),
                     ("PSW changed on the LDPR", lambda L: [l + " PSW=10000001" if l.startswith("L 1 ") else l for l in L]),
                     ("wrong PC", lambda L: [re.sub(r"pc=\w{8}", "pc=0000beef", l) if l.startswith("L 2 ") else l for l in L]),
                     ("retired count + 1", lambda L: [re.sub(r"instrs=(\d+)", lambda mm: f"instrs={int(mm.group(1)) + 1}", l) if l.startswith("END") else l for l in L]),
                     ("extra event for another register", lambda L: L[:1] + ["P 1 id=2 val=00000001"] + L[1:])]
            if pev:
                edits += [("event value changed", lambda L: [re.sub(r"val=\w{8}", "val=0badf00d", l) if k == pev[0] else l for k, l in enumerate(L)]),
                          ("event ID changed", lambda L: [re.sub(r"id=\d+", "id=6", l) if k == pev[0] else l for k, l in enumerate(L)]),
                          ("event dropped", lambda L: [l for k, l in enumerate(L) if k != pev[0]]),
                          ("event duplicated", lambda L: L[:pev[0] + 1] + [L[pev[0]]] + L[pev[0] + 1:]),
                          ("event moved to the wrong instruction", lambda L: [re.sub(r"^P (\d+)", lambda mm: f"P {int(mm.group(1)) + 1}", l) if k == pev[0] else l for k, l in enumerate(L)])]
            for what, fn in edits:
                try:
                    f = C.check_ref2(m, "\n".join(fn(t.splitlines())) + "\n", kind)
                except Exception as e:                                # a checker crash is NOT a catch
                    negctl.append(("b", f"{kind} log of {n} corrupted: {what}", False, f"checker error {e!r}"))
                    continue
                negctl.append(("b", f"{kind} log of {n} corrupted: {what}", bool(f), f"{f[:1]}"))
    n = "l2_ow_zero_to_zero"
    m = x.man[n]
    # upstream's value-change-only visibility: the FULL event list must be rejected on the upstream log (the caveat is real)
    m2 = copy.deepcopy(m)
    m2["exp_events_up"] = m2["exp_events"]
    m2["exp_counters_up"] = m2["exp_counters"]
    f = C.check_ref2(m2, C1.read_log(x.logs / f"{n}.up"), "up")
    negctl.append(("b", "upstream checked against V60X's FULL event list (unchanged-value writes) is rejected: the caveat is real", bool(f), f"{f[:1]}"))
    f = C.check_ref2(m, C1.read_log(x.logs / f"{n}.ext"), "up")
    negctl.append(("b", "V60X's log checked with upstream's (value-change) expectation is rejected", bool(f), f"{f[:1]}"))
    # V2: wrong expected trap PC / kind / retired count; events and flags injected into the V2 trap log
    nt = "n1_id0"
    mt = x.man[nt]
    if x.phase == "r2a":
        t = C1.read_log(x.logs / f"{nt}.v2_default")
    else:                                       # r2b: the baseline is the unmutated model's correct log (see m1_r2a_catchers)
        import m1_r2a_catchers as K
        img = (x.progs / f"{nt}.bin").read_bytes() + bytes(16)
        t = K.run_model(img, 0, K.Tabs(False), {})
    expect_kind = mt["v2"][x.phase]["trap"]
    wrong_kind = next(k for k in ("opcode", "regid", "mode", "priv") if k != expect_kind)
    base = C.check_v2r2(mt, t, x.phase)
    negctl.append(("b0", f"control: V2's log of {nt} passes the phase-{x.phase} expectation", not base, f"{base[:1]}"))
    for what, edit_ in (("trap PC + 1", lambda e: e.update(pc=e["pc"] + 1)), (f"trap kind {wrong_kind} instead of {expect_kind}", lambda e: e.update(trap=wrong_kind)),
                        ("retired count + 1", lambda e: e.update(n=e["n"] + 1))):
        m3 = copy.deepcopy(mt)
        edit_(m3["v2"][x.phase])
        f = C.check_v2r2(m3, t, x.phase)
        negctl.append(("b", f"wrong expected V2 trap: {what}", bool(f), f"{f[:1]}"))
    for what, add in (("a privileged write by the trapped instruction (P)", "P 1 id=5 be=f val=5a5a1234"),
                      ("a privileged write by the trapped instruction (CU priv=1)", "CU 1 dr=0 dw=0 priv=1 psw=0"),
                      ("a PSW update by the trapped instruction (CU psw)", "CU 1 dr=0 dw=0 priv=0 psw=1"),
                      ("a data write", "W addr=00000800 size=4 data=deadbeef")):
        f = C.check_v2r2(mt, t + add + "\n", x.phase)
        negctl.append(("b", f"V2 trap exit: {what}", bool(f), f"{f[:1]}"))
    # the phase parameter: the OTHER phase's expectation rejects the current V2
    other = "r2b" if x.phase == "r2a" else "r2a"
    pos = [(nm, mm) for nm, mm in x.man.items() if mm["cls"] == "pos" and mm["where"] == "low"]
    rej = sum(1 for nm, mm in pos if C.check_v2r2(mm, C1.read_log(x.logs / f"{nm}.v2_default"), other))
    negctl.append(("g", f"phase {other} expectation rejects the current V2 (phase {x.phase}) on the LDPR positives", rej == len(pos), f"{rej}/{len(pos)} rejected"))
    # L6: a leaked event or a stray retire on the wait exit
    wn = "pr_l6_L16_k6"                          # phase-independent (probe image): a real wait exit
    wt = C1.read_log(x.logs / f"{wn}.v2_default_ack")
    mw = x.man[wn]
    base = C.check_v2r2(mw, wt, x.phase, "ack")
    f = C.check_v2r2(mw, wt + "P 4 id=2 be=f val=b6a59483\n", x.phase, "ack")
    negctl.append(("b", "L6 wait exit: control passes; a leaked privileged write of the unretired instruction is rejected", not base and bool(f), f"base={base[:1]} leak:{f[:1]}"))
    negctl_completeness(x)


def negctl_completeness(x):
    """Codex R2a round 1 #2 / #3: required fields; written IDs; consistent retirement; no shared-state race."""
    import concurrent.futures
    import m1_r2a_catchers as K
    # (1) the real FLAG_UPD probe mutant: complete output rejected, psw= fields stripped must STILL be rejected
    mc, _ = build_probe(x, "mc_probe_flag_upd", {"flag_upd": 15})
    m = x.man["pr_wrasel3_q5"]
    t = v2_text(x, "pr_wrasel3_q5", m, "", "std", mc=mc)
    full = C.check_v2r2(m, t, x.phase)
    stripped = C.check_v2r2(m, re.sub(r" psw=\d+", "", t), x.phase)
    negctl.append(("h", "FLAG_UPD probe mutant: rejected with complete output, and still rejected with the psw= fields removed",
                   bool(full) and any("no psw= field" in g for g in stripped), f"full:{full[:1]} stripped:{[g for g in stripped if 'psw=' in g][:1]}"))
    # (2) the written SBR's PR line deleted from l1_v01.ext
    mm = x.man["l1_v01"]
    t = C1.read_log(x.logs / "l1_v01.ext")
    f = C.check_ref2(mm, "\n".join(l for l in t.splitlines() if not l.startswith("PR id=5 ")) + "\n", "ext")
    negctl.append(("h", "l1_v01.ext with the SBR PR line deleted is rejected", bool(f), f"{f[:1]}"))
    # (3) a trap's END instrs=999
    nt = "n1_id0"
    mt = x.man[nt]
    if x.phase == "r2a":
        t = C1.read_log(x.logs / f"{nt}.v2_default")
    else:
        t = K.run_model((x.progs / f"{nt}.bin").read_bytes() + bytes(16), 0, K.Tabs(False), {})
    f = C.check_v2r2(mt, re.sub(r"instrs=\d+", "instrs=999", t), x.phase)
    negctl.append(("h", "trap log with END instrs=999 is rejected", bool(f), f"{f[:1]}"))
    # (4) no shared state: interleaved EL=0 / EL=1 checks from many threads all pass, deterministically
    logs = {}
    for nm in ("pr_getpsw_el1", "pr_getpsw_el2", "pr_getpsw_el3"):
        logs[nm] = C1.read_log(x.logs / f"{nm}.v2_default")
    m0 = x.man["pr_wrasel3_q5"]
    logs["pr_wrasel3_q5"] = C1.read_log(x.logs / "pr_wrasel3_q5.v2_default")
    jobs = [(nm, i) for i in range(150) for nm in logs]
    with concurrent.futures.ThreadPoolExecutor(8) as ex:
        res = list(ex.map(lambda j: C.check_v2r2(x.man[j[0]], logs[j[0]], x.phase), jobs))
    negctl.append(("h", "concurrent forced-EL checks (EL 0 and EL 1/2/3 interleaved, 600 checks on 8 threads) all pass",
                   not any(res), f"{sum(1 for r in res if r)} failures"))


def negctl_benches(x):
    """(c) non-vacuity of the new bench instrumentation: forced-zero mutants of the P-event printers (upstream and
    V2 observer), rebuilt; the positives must then FAIL, so the events are really observed."""
    print("== negative controls (c): forced-zero privileged-event instrumentation (rebuilt copies)")
    mut = x.out / "mut"
    mut.mkdir(exist_ok=True)
    root = x.root
    U = root / "references/s32/rtl/cpu/v60"
    tb = (HERE / "tb/tb_m1_up.sv").read_text()
    tb2 = tb.replace("if (privev && privv !== priv_prev)", "if (1'b0 && privv !== priv_prev)")
    assert tb2 != tb
    (mut / "tb_m1_up_noev.sv").write_text(tb2)
    flags = ["--binary", "--timing", "--threads", "1", "-Wno-fatal", "-Wno-WIDTHTRUNC", "-Wno-WIDTHEXPAND", "-Wno-UNOPTFLAT",
             "-Wno-CASEINCOMPLETE", "-Wno-BLKANDNBLK", "-Wno-MULTIDRIVEN", "-Wno-INITIALDLY", "-Wno-DECLFILENAME",
             "-Wno-PINMISSING", "-Wno-UNSIGNED", "-Wno-WIDTH", "-Wno-TIMESCALEMOD", "+define+SIMULATION", "+define+S32_V60_NO_FP=1"]
    r = sh(["verilator"] + flags + ["--top-module", "tb_m1_up", "--Mdir", str(mut / "obj_up_noev"), "-o", "sim",
                                   str(U / "s32_v60.sv"), str(U / "s32_v60_bus.sv"), str(HERE / "tb/m1_mem.sv"), str(mut / "tb_m1_up_noev.sv")])
    ps = [(n, m) for n, m in x.man.items() if m["cls"] == "pos" and m["where"] == "low"]
    if r.returncode:
        negctl.append(("c1", "no-event upstream bench", False, "mutant build failed: " + r.stderr[-200:]))
    else:
        bad = 0
        for n, m in ps[:20]:
            t = sh([str(mut / "obj_up_noev/sim"), f"+img={x.progs / (n + '.hex')}", "+maxcyc=200000", "+lockstep", "+counters", "+privev"]).stdout
            bad += bool(C.check_ref2(m, t, "up")) if m["exp_events_up"] and any(m["exp_events_up"].values()) else 0
        want = sum(1 for n, m in ps[:20] if any(m["exp_events_up"].values()))
        negctl.append(("c1", "upstream bench without events (rebuilt copy): every program with an expected change is rejected", bad == want and want > 0, f"{bad}/{want}"))
    ob = (HERE / "tb/v2_sim_observer.sv").read_text()
    ob2 = ob.replace("if (priv_we && privev)  $display", "if (1'b0 && priv_we && privev)  $display")
    assert ob2 != ob
    (mut / "v2_sim_observer_noev.sv").write_text(ob2)
    Rr = root / "experiments/V2-datapath-floor/rtl"
    files = [str(Rr / l.strip()) for l in (Rr / "files.f").read_text().split()]
    r = sh(["verilator", "--binary", "--timing", "--threads", "1", "-Wno-fatal", "-Wno-lint", "-Wno-style", "-Wno-TIMESCALEMOD",
            "+define+SIMULATION", f"-I{Rr}", "--top-module", "tb_m1_v2", "--Mdir", str(mut / "obj_v2_noev"), "-o", "sim"] + files +
           [str(U / "s32_v60_bus.sv"), str(HERE / "tb/m1_mem.sv"), str(mut / "v2_sim_observer_noev.sv"),
            str(HERE / "tb/v2_sim_checks.sv"), str(HERE / "tb/tb_m1_v2.sv")])
    if r.returncode:
        negctl.append(("c2", "no-event V2 observer", False, "mutant build failed: " + r.stderr[-200:]))
    else:
        m = x.man["pr_wrasel3_q5"]
        syms = RR.symbols(x.out / "mc_probe")
        cmd = RR.v2_cmd(str(mut / "obj_v2_noev/sim"), x.out / "mc_probe", [f"+img={x.progs / 'pr_wrasel3_q5.hex'}", "+maxcyc=400000", "+privev"], syms)
        t = sh(cmd).stdout
        f = C.check_v2r2(m, t, x.phase)
        negctl.append(("c2", "V2 observer without events (rebuilt copy): the WR_ASEL = 3 probe is rejected", bool(f), f"{f[:1]}"))


def stage_probes_report(x):
    """The WR_ASEL = 3 probe (plan 9 #8): operand 2's quick value reaches the E3 write address for every q = 0..15,
    the written file entry equals the immediate (PR dump), SBR = entry 5."""
    t = C1.read_log(x.logs / "pr_wrasel3_q.v2_default")
    d = C.parse2(t)
    ev = [(i, e) for i, es in sorted(d["P"].items()) for e in es]
    m = x.man["pr_wrasel3_q"]
    exp = [(i, tuple(e)) for i, es in sorted(m["exp_events"].items(), key=lambda kv: int(kv[0])) for e in es for i in [int(i)]]
    ok = [(i, (e[0], e[1])) for i, e in ev] == exp and all(e[2] == 0xF for _, e in ev)
    pr_ok = all(d["PR"].get(int(k)) == v for k, v in m["final_priv"].items()) and d["PR"].get(5) == m["final_priv"]["5"]
    note("WR_ASEL = 3 probe: E3 write address == operand 2's quick value for q = 0..15, byte enables f, file entry 5 holds the q = 5 value",
         ok and pr_ok, f"{len(ev)} events")
    x.wrasel = [(q, f"{v:08x}") for _, (q, v, _) in ev]


def main():
    global io, contextlib
    import io
    import contextlib
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--phase", default="r2a")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--negctl", type=int, default=1)
    ap.add_argument("--root", required=True)
    ap.add_argument("--sim-r2b", action="store_true")
    a = ap.parse_args()
    if a.sim_r2b:
        import m1_r2b_simbuilder as SB
        SB.install()
    x = RR.Ctx()
    x.out, x.phase, x.jobs, x.root = Path(a.out), a.phase, a.jobs, Path(a.root)
    x.progs = x.out / "progs"
    x.logs = x.out / "logs"
    x.logs.mkdir(exist_ok=True)
    x.man = json.load(open(x.progs / "r2_manifest.json"))
    classes = {}
    for m in x.man.values():
        k = f"{m['cls']}/{m['where']}"
        classes[k] = classes.get(k, 0) + 1
    print("programs: " + ", ".join(f"{k} {v}" for k, v in sorted(classes.items())) + f" (total {len(x.man)})")
    mc, nprobe = build_probe(x)
    print(f"probe image: {nprobe} extra control-store words (TEST-ONLY, in memory; builder untouched)")
    stage_refs(x)
    stage_v2(x)
    stage_smc(x)
    stage_struct(x)
    stage_probes_report(x)
    if a.negctl:
        negctl_refs(x)
        negctl_events(x)
        negctl_benches(x)
        import m1_r2a_catchers as K
        K.run(x, negctl, note)
    print("\n================ R2a SUMMARY (phase %s) ================" % x.phase)
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
    print(f"\nWR_ASEL=3 probe events (instruction, event): {getattr(x, 'wrasel', None)}")
    ok = bad == 0 and nbad == 0
    print(f"\nR2a {'PASS' if ok else 'FAIL'}: {len(results) - bad}/{len(results)} result groups pass"
          + (f", {len(negctl) - nbad}/{len(negctl)} negative controls/catchers caught" if a.negctl else ""))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
