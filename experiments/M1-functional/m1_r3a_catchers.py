#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""R3a mutant-catcher contract (docs/r3-movea-plan.md section 6 "Mutation tests for R3b", as R2a's m1_r2a_catchers.py).

No R3 microcode exists, so the planned mutants cannot be built. Instead the catchers are proven to fire in three
independent ways, none of which needs MOVEA microcode in the production builder:

  (A) a MICROFLOW MODEL of the planned routine (the plan's section 5 sketch) executed over the REAL production and
      --smc-test mode tables, one knob per planned mutant. The model writes a synthetic V2 log (lock-step records,
      event counters, privileged and GPR write events, TRAP / END / CU lines) which the r3b checker (m1_r3_check.py)
      judges against the generator's independent expectations. The UNmutated model must pass every program; each mutant
      must be rejected, and by its DESIGNATED catcher (the program groups the plan names), not merely by any program.
  (B) REAL-RTL mutants of the TEST-ONLY probe image (build_probe3 in m1_r3_run.py): the probe 0xFE carries the plan's
      execute step (it is not MOVEA and has no guards), so the value / size / consume / write-target / flag / duplicate /
      omitted-write classes are mutated on the real V2 and caught by the same checker; CONSUME 4 under the A7
      arrangement (exactly four bytes at the five-byte specifier issue) must fire v2_sim_checks' availability check.
  (C) STRUCTURAL mutants: the builder invariants (m1_r3_run.invariants) applied to mutated copies of the real tables.

Each mutant ends as CAUGHT (by its designated catcher), EQUIVALENT (with a reason, and in (B) shown on the real RTL) or GAP.
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import m1_m11_microcode as B  # noqa: E402
import m1_r1 as R1  # noqa: E402
import m1_r3_check as C  # noqa: E402

M = 0xFFFFFFFF
IMM_SIZES = {0: 1, 1: 2, 2: 4}
MOVEA_OPS = (0x40, 0x42, 0x44)
PRIV_RESET = {7: 0x70, 8: 0xE000, 9: 0x6000, 15: 0xF002}


class Tabs:
    """the REAL production (smc = False) or --smc-test mode tables; mtab entries can be overridden by a mutant"""

    def __init__(self, smc, over=None):
        p, sym, dtab, mtab = B.build(smc_test=smc)
        self.smc, self.sym, self.dtab, self.mtab = smc, sym, dtab, dict(mtab)
        for k, v in (over or {}).items():
            self.mtab[k] = v

    def mode(self, modm, byte):
        e = self.mtab[(modm << 8) | byte]
        return (e & 0xFFF) not in (self.sym["trap"], self.sym["trap_mode"]), e >> 12


def is_reg(d):
    return bool(d & 1)


def is_imm(d):
    return bool(d & 2)


class Mem:
    def __init__(self, img):
        self.img = img

    def b(self, a):
        a &= M
        return self.img[a] if a < len(self.img) else 0


def sext(v, bits):
    return v - (1 << bits) if v & (1 << (bits - 1)) else v


def movea_flow(tabs, mem, pc, mut):
    """The planned routine (plan section 5), one knob per mutant. Returns trap (kind | None), nxt, gev
    [(reg, val) | ("maybe", reg, val)], priv, flags, psw_upd, dr, hang, regs (the architectural register writes)."""
    b = lambda i: mem.b(pc + i)
    opc = b(0)
    r = dict(trap=None, nxt=pc, gev=[], priv=[], flags=None, psw_upd=0, dr=0, hang=False, regs={})
    ifl = b(1)
    dst_orig = ifl & 31
    if mut.get("dtab40_wrong") and opc == 0x40:                    # dtab[0x40] at the NOP routine: consume 1, nothing else
        r["nxt"] = (pc + 1) & M
        return r

    def trap(kind):
        r["trap"] = kind
        if mut.get("transient_trap"):                                 # a write then a restore of the destination before the trap
            r["gev"] = [(dst_orig, 7), (dst_orig, None)]              # None = "the old value" (filled in by the caller)
        return r

    if not mut.get("no_f1_guard") and (ifl & 0x80):
        return trap("mode")
    if not mut.get("no_d_guard") and not (ifl & 0x20):
        return trap("mode")
    m6 = (ifl >> 6) & 1
    ok1, d1 = tabs.mode(m6, b(2))
    if not ok1:
        return trap("mode")
    if not mut.get("no_regdir_guard") and is_reg(d1):
        return trap("mode")
    if not mut.get("no_imm_guard") and is_imm(d1):
        return trap("mode")
    ofs, szk, cons = mut.get("imm_ofs", 1), mut.get("imm_sz", 2), mut.get("imm_consume", 5)
    nb = IMM_SIZES[szk]
    val = int.from_bytes(bytes(b(2 + ofs + i) for i in range(nb)), "little")
    if mut.get("imm_sx"):
        val = sext(val, 8 * nb) & M                                   # IMM_SX: identity for a word-size immediate
    if mut.get("mask8"):
        val &= 0xFF
    if mut.get("value_contents"):                                     # the v60_ref defect: the byte AT the address, zero-extended
        r["dr"] = 1
        val = mem.b(int.from_bytes(bytes(b(3 + i) for i in range(4)), "little"))
    dst = {0: mut.get("rconst", 0), 1: ifl & 31, 2: b(2) & 31, 3: b(2) & 31}[mut.get("wr_asel", 1)]
    wd = mut.get("wr_dst", "gpr")
    if wd == "gpr" and not mut.get("nowrite"):
        if mut.get("omit_unchanged"):
            r["gev"] = [("maybe", dst, val)]                          # resolved by the caller: dropped when the value is unchanged
        elif mut.get("dup_write"):
            r["gev"] = [(dst, val), (dst, val)]
        elif mut.get("transient_write"):
            r["gev"] = [(dst, 7), (dst, val)]
        else:
            r["gev"] = [(dst, val)]
        r["regs"][dst] = val
    elif wd == "priv":
        r["priv"] = [(dst, val)]
    if mut.get("flag_upd"):
        r["flags"] = (int(val == 0), val >> 31, 0, 0)
        r["psw_upd"] = 1
    if mut.get("no_disp_jmp"):
        r["hang"] = True
    r["nxt"] = (pc + (1 if mut.get("fmt_consume") == 1 else 2) + cons) & M
    return r


def run_model(img, el, tabs, mut, max_instrs=3000):
    """A V2 log (text) for the program image under the (mutated) model. Supported neighbours: HALT, NOP, mov.w / ALU
    #imm32 to Rn, ALU Rs,Rd, GETPSW, JMP /abs (production), LDPR #imm32,#q (correct), BR8; MOVEA by movea_flow."""
    mem = Mem(img)
    regs, fl, pc = [0] * 32, [0, 0, 0, 0], 0
    psw0 = 0x10000000 | (el << 24)
    psw = lambda: psw0 | fl[0] | (fl[1] << 1) | (fl[2] << 2) | (fl[3] << 3)
    priv_last = {}
    out, n, halted, trap, hung, nun = ["GPREV on"], 0, 0, None, False, 0
    movea_ok = (not tabs.smc) and not mut.get("dtab40_trap")
    if tabs.smc and mut.get("smc_dispatch"):
        movea_ok = True
    while n < max_instrs and not halted:
        op = mem.b(pc)
        r0, p0 = list(regs), psw()
        gev, ev, pupd, dr, nxt, kind = [], [], 0, 0, None, None
        if op == 0x00:
            halted, nxt = 1, pc
        elif op == 0xCD:
            nxt = pc + 1
        elif op in (0x2D, 0x84, 0xAC, 0xBC, 0xA4):
            b1, b2 = mem.b(pc + 1), mem.b(pc + 2)
            if b1 & 0xE0 == 0x20 and b2 == 0xF4:
                rr = b1 & 31
                v = int.from_bytes(bytes(mem.b(pc + 3 + i) for i in range(4)), "little")
                if op == 0x2D:
                    regs[rr] = v
                    gev = [(rr, v)]
                else:
                    res, z, s, ov, cy = R1.alu(op, regs[rr], v, fl[3])
                    if op != 0xBC:
                        regs[rr] = res
                        gev = [(rr, res)]
                    fl[:] = [z, s, ov, cy] if op != 0xA4 else [z, s, ov, fl[3]]
                    pupd = 1
                nxt = pc + 7
            elif b1 & 0xE0 == 0x60 and b2 & 0xE0 == 0x60 and op != 0x2D:
                rd, rs = b1 & 31, b2 & 31
                res, z, s, ov, cy = R1.alu(op, regs[rd], regs[rs], fl[3])
                if op != 0xBC:
                    regs[rd] = res
                    gev = [(rd, res)]
                fl[:] = [z, s, ov, cy] if op != 0xA4 else [z, s, ov, fl[3]]
                pupd = 1
                nxt = pc + 3
            else:
                kind = "mode"
        elif op == 0xF7:
            if mem.b(pc + 1) & 0xE0 == 0x60:
                rr = mem.b(pc + 1) & 31
                regs[rr] = psw()
                gev = [(rr, regs[rr])]
                nxt = pc + 2
            else:
                kind = "mode"
        elif op == 0xD6:
            if tabs.smc:
                kind = "opcode"
            elif mem.b(pc + 1) == 0xF3:
                nxt = int.from_bytes(bytes(mem.b(pc + 2 + i) for i in range(4)), "little")
            else:
                kind = "mode"
        elif op == 0x6A:
            d = mem.b(pc + 1)
            nxt = pc + (d - 256 if d >= 128 else d)
        elif op == 0x12:
            ifl = mem.b(pc + 1)
            v = int.from_bytes(bytes(mem.b(pc + 3 + i) for i in range(4)), "little")
            q = mem.b(pc + 7) & 15
            if el != 0:
                kind = "priv"
            elif ifl & 0x80 and mem.b(pc + 2) == 0xF4 and (mem.b(pc + 7) & 0xF0) == 0xE0 and q in (2, 3, 4, 5):
                ev = [(q, v)]
                nxt = pc + 8
            else:
                kind = "mode"
        elif movea_ok and (op == 0x40 or (op == 0x42 and mut.get("dtab42")) or (op == 0x44 and mut.get("dtab44"))):
            mf = movea_flow(tabs, mem, pc, mut)
            if mf["trap"]:
                kind = mf["trap"]
                gev = [(g[0], regs[g[0]] if g[1] is None else g[1]) for g in mf["gev"]]
            else:
                for g in mf["gev"]:
                    if g[0] == "maybe":
                        if regs[g[1]] != g[2]:
                            gev.append((g[1], g[2]))
                    else:
                        gev.append(g)
                for rr, v in mf["regs"].items():
                    regs[rr] = v
                ev, pupd, dr, nxt = mf["priv"], mf["psw_upd"], mf["dr"], mf["nxt"]
                if mf["flags"]:
                    fl[:] = list(mf["flags"])
                if mf["hang"]:
                    hung = True
        else:
            kind = "opcode"
        if kind or hung:
            trap = (kind, pc) if kind else None
            for (rid, v) in gev:
                out.append(f"G {n} id={rid} be=f val={v:08x}")
            nun = len(gev)
            break
        for (rid, v) in ev:
            out.append(f"P {n} id={rid} be=f val={v:08x}")
            priv_last[rid] = v
        for (rid, v) in gev:
            out.append(f"G {n} id={rid} be=f val={v:08x}")
        ch = "".join(f" R{i:02d}={regs[i]:08x}" for i in range(32) if regs[i] != r0[i])
        if psw() != p0:
            ch += f" PSW={psw():08x}"
        out.append(f"L {n} pc={pc & M:08x} op={op:02x}{ch}")
        out.append(f"C {n} dr={dr} dw=0 priv={len(ev)} psw={pupd} gpr={len(gev)}")
        n += 1
        pc = nxt & M
    if trap or hung:
        out.append(f"CU {n} dr=0 dw=0 priv=0 psw=0 gpr={nun}")
    out += [f"R{i:02d}={regs[i]:08x}" for i in range(32)]
    out.append(f"PSW={psw():08x}")
    out += [f"PR id={i} val={priv_last.get(i, PRIV_RESET.get(i, 0)):08x}" for i in range(32)]    # the bench dumps the whole file
    if trap:
        out.append(f"TRAP {trap[0]} ipc={trap[1] & M:08x}")
    out.append(f"END halted={halted} instrs={n} cycles=0")
    out.append("SIMCHECK errors=0 ipc_errors=0")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------- the mutants (plan section 6 table)
MUT = {}


def add(name, desc, knobs, tags, regex=None, equiv=None, smc_only=False):
    MUT[name] = (desc, knobs, tags, regex, equiv, smc_only)


LS = r"lock-step|GPR write events|final"
add("imm_ofs0", "IMM_OFS 0 (reads the mode byte and three address bytes)", {"imm_ofs": 0}, ["A1", "A2"], LS)
add("imm_ofs2", "IMM_OFS 2 (address skewed by one byte)", {"imm_ofs": 2}, ["A1", "A2"], LS)
add("imm_sz0", "IMM_SZ 0 (byte read; the `.b`-role mutant)", {"imm_sz": 0}, ["A1"], LS)
add("imm_sz1", "IMM_SZ 1 (halfword read)", {"imm_sz": 1}, ["A1"], LS)
add("imm_sx", "IMM_SX set with IMM_SZ = 2", {"imm_sx": True}, ["A1"], LS,
    equiv="v2_immx returns all 32 bits unchanged for a word-size immediate (v2_glue.sv imm_val): sign extension applies only to byte/half; "
          "IMM_SX together with a narrower IMM_SZ is the imm_sz0 / imm_sz1 rows")
add("mask8", "result masked to 8 bits (zero-extend `.b`)", {"mask8": True}, ["A1"], LS)
add("consume4", "CONSUME 4 on the execute word", {"imm_consume": 4}, ["A6", "A8"], r"lock-step|TRAP|END|opcode")
add("consume6", "CONSUME 6 on the execute word", {"imm_consume": 6}, ["A6", "A8"], r"lock-step|TRAP|END|opcode")
add("fmt_consume1", "missing CONSUME on the format word (CONSUME 1)", {"fmt_consume": 1}, ["A6", "A8"], r"lock-step|TRAP|END|opcode")
add("wr_dst_temp", "WR_DST temp instead of GPR", {"wr_dst": "temp"}, ["A2"], LS)
add("wr_dst_priv", "WR_DST privileged instead of GPR", {"wr_dst": "priv"}, ["A2"], r"lock-step|privileged events|MOVEA window")
add("wr_asel_const", "WR_ASEL const (RCONST 0) instead of the format-byte register", {"wr_asel": 0, "rconst": 0}, ["A2"], LS)
add("wr_asel_fld2", "WR_ASEL field 2 (the mode byte's low five bits = R19)", {"wr_asel": 2}, ["A2"], LS)
add("wr_asel_mreg", "WR_ASEL mode-byte register (R19)", {"wr_asel": 3}, ["A2"], LS)
add("nowrite", "omitted GPR write (the value changes: also visible in the lock-step record)", {"nowrite": True}, ["A2"], LS)
add("dup_write", "duplicate GPR write in the executed word sequence", {"dup_write": True}, ["A1", "A3"], r"GPR write events")
add("omit_unchanged", "omitted GPR write whose value is unchanged (destination already equals the address)", {"omit_unchanged": True}, ["A3"], r"GPR write events")
add("transient_write", "transient GPR write (a constant, then the address): two events, final state correct", {"transient_write": True}, ["A1", "A3"], r"GPR write events")
add("transient_trap", "transient write restored before a trap: unretired instruction with GPR events, state unchanged", {"transient_trap": True},
    ["N2", "N3", "N4", "N6", "d0", "f2", "sweep"], r"did not retire")
add("flag_upd", "FLAG_UPD on the execute word", {"flag_upd": True}, ["A5"], r"PSW|psw|lock-step")
add("value_contents", "a data-bus read of the target (value instead of address; the v60_ref defect)", {"value_contents": True}, ["A3", "A4"], r"MOVEA window|counters|lock-step")
add("no_regdir_guard", "C_REGDIR guard removed", {"no_regdir_guard": True}, ["N4", "src_regdir"], r"TRAP|lock-step|END")
add("no_imm_guard", "C_IMM guard removed", {"no_imm_guard": True}, ["N3", "N4", "src_imm"], r"TRAP|lock-step|END")
add("no_f1_guard", "C_F1 guard removed", {"no_f1_guard": True}, ["N2", "f2"], r"TRAP|lock-step|END")
add("no_d_guard", "C_D guard removed", {"no_d_guard": True}, ["N2", "d0"], r"TRAP|lock-step|END")
add("dtab42", "dtab[0x42] = movea", {"dtab42": True}, ["N1"], r"TRAP|lock-step|END")
add("dtab44", "dtab[0x44] = movea", {"dtab44": True}, ["N1"], r"TRAP|lock-step|END")
add("dtab40_wrong", "dtab[0x40] at a wrong routine (the NOP routine)", {"dtab40_wrong": True}, ["A1"], r"lock-step|TRAP|END")
add("dtab40_trap", "dtab[0x40] left trap", {"dtab40_trap": True}, ["A1"], r"TRAP|lock-step|END")
add("smc_dispatch", "dtab[0x40] = movea in the --smc-test builds", {"smc_dispatch": True}, ["N5"], r"TRAP|lock-step|END", smc_only=True)
add("no_disp_jmp", "missing final jmp disp (the routine does not return to the dispatch loop)", {"no_disp_jmp": True}, ["A8"], r"END|lock-step")
add("mtab_marked_imm", "mtab[0x0F3] descriptor marked immediate (C_IMM guard fires)", {"mtab": {0x0F3: (0x14E << 12)}}, ["A1"], r"TRAP|lock-step|END")
add("mtab_marked_reg", "mtab[0x0F3] descriptor marked register (C_REGDIR guard fires)", {"mtab": {0x0F3: (0x14D << 12)}}, ["A1"], r"TRAP|lock-step|END")


def model_programs(x, smc):
    out = []
    for n, m in x.man.items():
        if m["mc"] != "prod" or m["where"] != "low":
            continue
        if smc and ("jmp" in m["tags"] or m.get("v2_smc") is None):
            continue
        out.append((n, m))
    return out


def tabs_for(smc, mut):
    t = Tabs(smc)
    over = {k: (v & ~0xFFF) | t.sym["mret"] for k, v in (mut.get("mtab") or {}).items()}
    return Tabs(smc, over)


def judge(x, smc, mut, progs, imgs, phase="r3b"):
    """-> list of (program, failures) for the failing programs"""
    tabs = tabs_for(smc, mut)
    bad = []
    for n, m in progs:
        f = C.check_v2r3(m, run_model(imgs[n], m.get("el", 0), tabs, mut), phase, "smc" if smc else "std")
        if f:
            bad.append((n, f))
    return bad


def run(x, negctl, note):
    print("== mutant-catcher contract (A): microflow model over the real tables")
    imgs = {n: (x.progs / f"{n}.bin").read_bytes() + bytes(16) for n, m in x.man.items() if m["where"] == "low"}
    variants = {False: model_programs(x, False), True: model_programs(x, True)}
    for smc in (False, True):
        bad = judge(x, smc, {}, variants[smc], imgs)
        negctl.append(("m0", f"control: the UNmutated model passes every program in r3b ({'--smc-test' if smc else 'production'} tables, "
                             f"{len(variants[smc])} programs)", not bad, f"failing: {[(n, f[0][:80]) for n, f in bad[:3]]}"))
    for name, (desc, knobs, tags, regex, equiv, smc_only) in MUT.items():
        rows = {smc: judge(x, smc, knobs, variants[smc], imgs) for smc in ((True,) if smc_only else (False, True))}
        allbad = [t for r_ in rows.values() for t in r_]
        desig = [(n, f) for n, f in allbad if (set(x.man[n]["tags"]) & set(tags)) and (regex is None or any(re.search(regex, g) for g in f))]
        ev = (" ".join(f"{'--smc-test' if s else 'production'} {len(rows[s])}/{len(variants[s])} programs fail;" for s in rows)
              + f" designated catcher {tags}: {len(desig)} program(s)"
              + (f", e.g. {desig[0][0]}: {desig[0][1][0][:90]}" if desig else f"; failing programs: {sorted({n for n, _ in allbad})[:6]}"))
        if equiv and not allbad:
            negctl.append(("mE", f"model mutant {name} ({desc}): EQUIVALENT - {equiv}", True, ev))
        else:
            negctl.append(("m", f"model mutant {name}: {desc}", bool(desig), ev))
    # N5: movea.b [Rn], R must not execute in the smc build; the smc_dispatch mutant would execute it
    for mutk, want_bad in (({}, False), ({"smc_dispatch": True}, True)):
        ps = [(n, m) for n, m in model_programs(x, True) if n.startswith("n5_ind_") and not n.endswith("_h")]
        bad = sorted(n for n, _ in judge(x, True, mutk, ps, imgs))
        negctl.append(("m", f"N5 [Rn] cases under {'the smc_dispatch mutant' if mutk else 'the unmutated model'} in the smc build: "
                       + ("all rejected" if want_bad else "all pass (opcode trap, no execution)"),
                       (len(bad) == len(ps)) if want_bad else (not bad), f"rejected: {bad}"))
    # catcher precision: which N4 form catches each guard mutant (the crafted form is asserted; the short form is reported)
    for nm, mutk, short_name, crafted_name in (
            ("C_REGDIR guard", {"no_regdir_guard": True}, "n4_rn5_short", "n4_rn5_crafted"),
            ("C_IMM guard (#q)", {"no_imm_guard": True}, "n4_q5_short", "n4_q5_crafted")):
        sh = judge(x, False, mutk, [(short_name, x.man[short_name])], imgs)
        cr = judge(x, False, mutk, [(crafted_name, x.man[crafted_name])], imgs)
        negctl.append(("m", f"catcher precision, {nm} mutant: the crafted stream ({crafted_name}) catches it; the short form ({short_name}) "
                            f"{'also does' if sh else 'does NOT'}", bool(cr), f"short rejected={bool(sh)}; crafted rejected={bool(cr)}"))
    for nm, mutk in (("src_regdir", {"no_regdir_guard": True}), ("src_imm", {"no_imm_guard": True})):
        ps = [(n, m) for n, m in model_programs(x, False) if nm in m["tags"]]
        bad = sorted(n for n, _ in judge(x, False, mutk, ps, imgs))
        note(f"guard reach [{nm}]: every crafted/short N4 case rejects the guard mutant (production table)", len(bad) == len(ps), f"{len(bad)}/{len(ps)}: {bad[:6]}")
    # the mode-table sweep: all 511 entries other than (m = 0, 0xF3) trap `mode` in the unmutated model; a guard mutant is
    # caught by exactly the entries that guard protects
    sw = [(n, m) for n, m in model_programs(x, False) if "sweep" in m["tags"]]
    bad = judge(x, False, {}, sw, imgs)
    note("N3 sweep: all 511 (m, mode byte) entries other than (0, F3) trap `mode` in the unmutated model", not bad and len(sw) == 511, f"{len(sw)} programs, failing {[n for n, _ in bad][:5]}")
    for mname in ("no_imm_guard", "no_regdir_guard"):
        bad = sorted(n for n, _ in judge(x, False, MUT[mname][1], sw, imgs))
        want = {"no_imm_guard": {f"n3_m0_{0xE0 | q:02x}" for q in range(16)} | {"n3_m0_f4"},
                "no_regdir_guard": {f"n3_m1_{0x60 | r:02x}" for r in range(32)}}[mname]
        negctl.append(("m", f"N3 sweep under {mname}: exactly the {len(want)} entries that guard protects are rejected",
                       set(bad) == want, f"rejected {len(bad)}: {bad[:4]}..."))
    print("== mutant-catcher contract (C): structural mutants of the real tables")
    structural(x, negctl)
    print("== mutant-catcher contract (B): probe-image mutants on the real V2")
    probe_mutants(x, negctl, note)


def structural(x, negctl):
    import m1_r3_run as RUN
    p, sym, dtab, mtab, _fx = RUN.load_fixture()          # the FROZEN pre-R3 production tables (not the live builder)
    mret = sym["mret"]
    cases = {
        "mtab[0xF3] descriptor offset size 16 (dsz = 2)": {0x0F3: (0x148 << 12) | mret},
        "mtab[0xF3] marked immediate": {0x0F3: (0x14E << 12) | mret},
        "mtab[0xF3] marked register": {0x0F3: (0x14D << 12) | mret},
        "a second memory descriptor (mtab[0xF2], PC disp32) added": {0x0F2: (0x14C << 12) | mret},
        "a second memory descriptor ([R5], modm 0 0x65) added": {0x065: (0x14C << 12) | mret},
        "F3 with m = 1 accepted": {0x1F3: (0x14C << 12) | mret},
    }
    for desc, over in cases.items():
        m2 = dict(mtab)
        m2.update(over)
        bad = RUN.invariants(sym, dtab, m2, False, "r3a")
        negctl.append(("s", f"structural mutant: {desc}: the builder invariants fail", bool(bad), f"{bad[:2]}"))
    for op in (0x42, 0x44):
        d2 = dict(dtab)
        d2[op] = (2 << 12) | sym["nop"]
        bad = RUN.invariants(sym, d2, mtab, False, "r3a")
        negctl.append(("s", f"structural mutant: dtab[{op:#04x}] dispatched", bool(bad), f"{bad[:1]}"))
    p2, s2, d2, m2 = B.build(True, False, False)
    d3 = dict(d2)
    d3[0x40] = (2 << 12) | s2["nop"]
    bad = RUN.invariants(s2, d3, m2, True, "r3b")
    negctl.append(("s", "structural mutant: dtab[0x40] dispatched in an --smc-test build", bool(bad), f"{bad[:1]}"))
    bad = RUN.invariants(sym, dtab, mtab, False, "r3b")
    negctl.append(("s", "phase flip: the pre-R3 tables (dtab[0x40] = trap, no movea symbol) fail the r3b invariants", bool(bad), f"{bad[:1]}"))
    bad = RUN.invariants(sym, dtab, mtab, False, "r3a")
    negctl.append(("s0", "control: the pre-R3 tables pass the r3a invariants", not bad, f"{bad[:2]}"))


# ---------------------------------------------------------------------------- (B) real-RTL probe mutants
PROBE_MUT = {
    "imm_ofs0": ("IMM_OFS 0", {"imm_ofs": 0}), "imm_ofs2": ("IMM_OFS 2", {"imm_ofs": 2}),
    "imm_sz0": ("IMM_SZ 0 (byte read)", {"imm_sz": 0}), "imm_sz1": ("IMM_SZ 1 (halfword read)", {"imm_sz": 1}),
    "imm_sx": ("IMM_SX set with IMM_SZ = 2", {"imm_sx": True}),
    "consume4": ("CONSUME 4 on the execute word", {"imm_consume": 4}), "consume6": ("CONSUME 6 on the execute word", {"imm_consume": 6}),
    "fmt_consume1": ("missing CONSUME on the format word (CONSUME 1)", {"fmt_consume": 1}),
    "wr_dst_temp": ("WR_DST temp", {"wr_dst": 2}), "wr_dst_priv": ("WR_DST privileged", {"wr_dst": 3}),
    "wr_asel_const": ("WR_ASEL const (RCONST 0)", {"wr_asel": 0}), "wr_asel_fld2": ("WR_ASEL field 2", {"wr_asel": 2}),
    "wr_asel_mreg": ("WR_ASEL mode-byte register", {"wr_asel": 3}),
    "flag_upd": ("FLAG_UPD 15 on the execute word", {"flag_upd": 15}),
    "dup": ("duplicate GPR write (the same write word twice)", {"dup": True}),
    "nowrite": ("omitted GPR write (the value goes to a temp)", {"nowrite": True}),
}
PROBE_TAGS = {"imm_ofs0": ["A1", "A2"], "imm_ofs2": ["A1", "A2"], "imm_sz0": ["A1"], "imm_sz1": ["A1"], "imm_sx": ["A1"],
              "consume4": ["A6", "A7", "A8"], "consume6": ["A6", "A7", "A8"], "fmt_consume1": ["A6", "A8"],
              "wr_dst_temp": ["A2"], "wr_dst_priv": ["A2"], "wr_asel_const": ["A2"], "wr_asel_fld2": ["A2"], "wr_asel_mreg": ["A2"],
              "flag_upd": ["A5"], "dup": ["A1", "A3"], "nowrite": ["A2", "A3"]}


def probe_progs(x):
    return [n for n, m in x.man.items() if m["cls"] == "probe3" and "trapmid" not in m["tags"]]


def probe_mutants(x, negctl, note):
    import m1_r3_run as RUN
    cfgs = ["", "+wide"]
    progs = probe_progs(x)
    for name, (desc, mut) in PROBE_MUT.items():
        mc, _ = RUN.build_probe3(x, f"mc_probe3_{name}", mut)
        fails, simcheck, runs, sample = 0, 0, 0, None
        tagset = set()
        for pn in progs:
            m = x.man[pn]
            for cfg in cfgs:
                for run in (["std"] if not m.get("v2_ack") else ["ack", "delay"]):
                    if run == "ack" and m["extra"].get("k") == 7:
                        continue
                    t = RUN.v2_text(x, pn, m, cfg, run, mc=mc)
                    f = C.check_v2r3(m, t, "r3a", run)
                    runs += 1
                    if f:
                        fails += 1
                        tagset |= set(m["tags"])
                        sample = sample or f"{pn} [{cfg or 'default'}/{run}]: {f[0][:90]}"
                        simcheck += any("SIMCHECK" in g for g in f)
        if name == "imm_sx":
            negctl.append(("pE", f"probe-image mutant on the real V2: {desc}: EQUIVALENT (identical logs on all {runs} runs)", fails == 0,
                           f"{fails}/{runs} runs differ; v2_immx returns all 32 bits for a word-size immediate"))
            continue
        desig = bool(tagset & set(PROBE_TAGS[name]))
        ok = fails > 0 and desig
        extra = f"; failing program groups {sorted(tagset - {'probe3'})}"
        if name == "consume4":
            ok = ok and simcheck > 0
            extra += f"; the availability checker (SIMCHECK) fired on {simcheck} run(s)"
        negctl.append(("p", f"probe-image mutant on the real V2: {desc}", ok, f"{fails}/{runs} runs rejected{extra}; first: {sample}"))
    # the mode-table descriptor of /abs: the sketch's IMM_SZ is explicit, so the descriptor's offset-size field is unused
    mc, _ = RUN.build_probe3(x, "mc_probe3_mtab16", {"mtab_ofs16": True})
    fails = runs = 0
    for pn in progs:
        m = x.man[pn]
        if m.get("v2_ack"):
            continue
        t = RUN.v2_text(x, pn, m, "", "std", mc=mc)
        runs += 1
        fails += bool(C.check_v2r3(m, t, "r3a", "std"))
    negctl.append(("pE", "probe-image mutant on the real V2: mtab[0x0F3] descriptor offset size 16 (dsz = 2): EQUIVALENT for the sketch (IMM_SZ explicit; "
                         "not caught by A1, only by the structural assertion)", fails == 0, f"{fails}/{runs} runs differ"))
