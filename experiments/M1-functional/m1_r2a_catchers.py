#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""R2a mutant-catcher contract (docs/r2-ldpr-plan.md section 4 "Mutants" and section 9 #4).

No R2 microcode exists, so the planned mutants cannot be built. Instead the catchers are proven to fire in two
independent ways, both without any LDPR microcode:

  (A) a MICROFLOW MODEL of the planned LDPR routine (the section 3 sketch) executed over the REAL production and
      --smc-test mode/dispatch tables, one knob per planned mutant (IMM_OFS 0/2, IMM_SZ 1, CONSUME 4/6 on the
      immediate word, missing final CONSUME, WR_ASEL const/field1, WR_DST GPR/temp, FLAG_UPD, ID check off by one /
      removed, regID C_QUICK guard, source C_IMM / C_QUICK guards, privilege check, dtab privileged flag, dtab[0x12]
      at a wrong routine, plus the F1 guard). The model writes a synthetic V2 log (lock-step records, event
      counters, privileged events, TRAP / END / CU lines) which the r2b checker (m1_r2_check.py) judges against the
      generator's independent expectations. The UNmutated model must pass every program (the r2b expectation is
      satisfiable, production and SMC builds); each mutant must be rejected, and by its DESIGNATED catcher
      (plan 9 #4), not merely by any program.
  (B) REAL-RTL mutants of the TEST-ONLY probe image (build_probe in m1_r2_run.py): the probe 0xFE carries the
      planned LDPR's operand, immediate and write steps (it is not LDPR), so the value / size / consume / write-target
      / flag classes are mutated on the real V2 and caught by the same checker; CONSUME 4 under the L6 arrangement
      (exactly four bytes at the five-byte immediate issue) must fire v2_sim_checks' availability check.

Each mutant ends as CAUGHT (by its designated catcher), EQUIVALENT (with a reason) or GAP.
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import m1_m11_microcode as B  # noqa: E402
import m1_r1 as R1  # noqa: E402
import m1_r2_check as C  # noqa: E402

M = 0xFFFFFFFF
IMM_SIZES = {0: 1, 1: 2, 2: 4}


class Tabs:
    def __init__(self, smc):
        p, sym, dtab, mtab = B.build(smc_test=smc)
        self.smc, self.sym, self.dtab, self.mtab = smc, sym, dtab, mtab

    def mode(self, modm, byte):
        """(accepted by the mode TABLE, descriptor); trap_mode / trap entries are rejected by the table itself"""
        e = self.mtab[(modm << 8) | byte]
        return (e & 0xFFF) not in (self.sym["trap"], self.sym["trap_mode"]), e >> 12


def is_imm(d):
    return bool(d & 2)


def is_quick(d):
    return bool(d & 2) and ((d >> 2) & 3) == 0


# ---------------------------------------------------------------------------- the model
class Mem:
    def __init__(self, img):
        self.img = img

    def b(self, a):
        a &= M
        return self.img[a] if a < len(self.img) else 0


def ldpr_flow(tabs, mem, pc, el, mut):
    """The planned LDPR routine (plan section 3), one knob per mutant. Returns a dict: trap (kind | None), nxt,
    events [(id, value)], regs {r: v}, flags (new z s ov cy | None), psw_upd."""
    b = lambda i: mem.b(pc + i)
    r = dict(trap=None, nxt=pc, events=[], regs={}, flags=None, psw_upd=0)
    if mut.get("dtab_wrong_routine"):                       # dtab[0x12] -> the NOP routine: consume 1, nothing else
        r["nxt"] = (pc + 1) & M
        return r
    if el != 0 and not mut.get("no_priv_check") and not mut.get("dtab_flag_cleared"):
        r["trap"] = "priv"
        return r
    ifl = b(1)
    if not (ifl & 0x80) and not mut.get("no_f1_guard"):
        r["trap"] = "mode"
        return r
    m6, m5 = (ifl >> 6) & 1, (ifl >> 5) & 1
    ok1, d1 = tabs.mode(m6, b(2))
    if not ok1:
        r["trap"] = "mode"
        return r
    if not mut.get("no_src_imm_guard") and not is_imm(d1):
        r["trap"] = "mode"
        return r
    if not mut.get("no_src_quick_guard") and is_quick(d1):
        r["trap"] = "mode"
        return r
    ofs, sz, cons = mut.get("imm_ofs", 1), IMM_SIZES[mut.get("imm_sz", 2)], mut.get("imm_consume", 5)
    val = int.from_bytes(bytes(b(2 + ofs + i) for i in range(sz)), "little")      # zero-extended (IMM_SX = 0)
    head = pc + 2 + cons                                                             # operand 2's specifier
    byte2 = mem.b(head)
    ok2, d2 = tabs.mode(m5, byte2)
    if not ok2:
        r["trap"] = "mode"
        return r
    if not mut.get("no_regid_quick_guard") and not is_quick(d2):
        r["trap"] = "mode"
        return r
    q = byte2 & 15                                                                   # the quick-extraction word
    if not mut.get("no_id_check") and not (mut.get("id_lo", 2) <= q <= mut.get("id_hi", 5)):
        r["trap"] = "regid"
        return r
    mreg = byte2 & 31                                                                # operand 2's mode-byte low five bits
    addr = {"asel3": mreg, "const": mut.get("rconst", 0), "fld1": ifl & 31}[mut.get("wr_asel", "asel3")]
    dst = mut.get("wr_dst", "priv")
    if dst == "priv":
        r["events"].append((addr, val))
    elif dst == "gpr":
        r["regs"][addr] = val
    if mut.get("flag_upd") == "check":              # FLAG_UPD on the ID-check word: ADD (q + 30)
        res = (q + 30) & M
        r["flags"] = (int(res == 0), res >> 31, 0, 0)
        r["psw_upd"] = 1
    elif mut.get("flag_upd") == "write":            # FLAG_UPD on the value / write word: PASSA of the value
        r["flags"] = (int(val == 0), val >> 31, 0, 0)
        r["psw_upd"] = 1
    r["nxt"] = (head + (0 if mut.get("no_final_consume") else 1)) & M
    return r


def run_model(img, el, tabs, mut, max_instrs=3000):
    """A V2 log (text) for the program image under the (mutated) model; unsupported opcodes trap `opcode`,
    malformed operand bytes of the setup instructions trap `mode`. Only the instruction forms the generator emits."""
    mem = Mem(img)
    regs, fl, pc = [0] * 32, [0, 0, 0, 0], 0
    psw0 = 0x10000000 | (el << 24)
    psw = lambda: psw0 | fl[0] | (fl[1] << 1) | (fl[2] << 2) | (fl[3] << 3)
    out, n, halted, trap = [], 0, 0, None
    while n < max_instrs and not halted:
        op = mem.b(pc)
        r0, p0 = list(regs), psw()
        ev, pupd, nxt, kind = [], 0, None, None
        if op == 0x00:
            halted, nxt = 1, pc
        elif op == 0xCD:
            nxt = pc + 1
        elif op in (0x2D, 0x84, 0xAC, 0xBC, 0xA4):
            if mem.b(pc + 1) & 0xE0 == 0x20 and mem.b(pc + 2) == 0xF4:
                rr = mem.b(pc + 1) & 31
                v = int.from_bytes(bytes(mem.b(pc + 3 + i) for i in range(4)), "little")
                if op == 0x2D:
                    regs[rr] = v
                else:
                    res, z, s, ov, cy = R1.alu(op, regs[rr], v, fl[3])
                    if op != 0xBC:
                        regs[rr] = res
                    fl[:] = [z, s, ov, cy] if op != 0xA4 else [z, s, ov, fl[3]]
                    pupd = 1
                nxt = pc + 7
            else:
                kind = "mode"
        elif op == 0xF7:
            if mem.b(pc + 1) & 0xE0 == 0x60:
                regs[mem.b(pc + 1) & 31] = psw()
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
            lf = ldpr_flow(tabs, mem, pc, el, mut)
            if lf["trap"]:
                kind = lf["trap"]
            else:
                for rr, v in lf["regs"].items():
                    regs[rr] = v
                if lf["flags"]:
                    fl[:] = list(lf["flags"])
                ev, pupd, nxt = lf["events"], lf["psw_upd"], lf["nxt"]
        else:
            kind = "opcode"
        if kind:
            trap = (kind, pc)
            break
        for (rid, v) in ev:
            out.append(f"P {n} id={rid} be=f val={v:08x}")
        ch = "".join(f" R{i:02d}={regs[i]:08x}" for i in range(32) if regs[i] != r0[i])
        if psw() != p0:
            ch += f" PSW={psw():08x}"
        out.append(f"L {n} pc={pc & M:08x} op={op:02x}{ch}")
        out.append(f"C {n} dr=0 dw=0 priv={len(ev)} psw={pupd}")
        n += 1
        pc = nxt & M
    if trap:
        out.append(f"CU {n} dr=0 dw=0 priv=0 psw=0")
    out += [f"R{i:02d}={regs[i]:08x}" for i in range(32)]
    out.append(f"PSW={psw():08x}")
    # the final privileged file the real bench dumps: the written IDs with their last values
    last = {}
    for line in out:
        m = re.match(r"P \d+ id=(\d+) be=f val=([0-9a-f]{8})", line)
        if m:
            last[int(m.group(1))] = m.group(2)
    out += [f"PR id={i} val={v}" for i, v in sorted(last.items())]
    if trap:
        out.append(f"TRAP {trap[0]} ipc={trap[1] & M:08x}")
    out.append(f"END halted={halted} instrs={n} cycles=0")
    out.append("SIMCHECK errors=0 ipc_errors=0")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------- the mutants (plan section 4 list)
# name: (description, knobs, designated catcher tags, designated failure regex or None, equivalence reason or None)
MUT = {}


def add(name, desc, knobs, tags, regex=None, equiv=None):
    MUT[name] = (desc, knobs, tags, regex, equiv)


add("imm_ofs0", "IMM_OFS 0 on the immediate word (reads the descriptor byte and 3 value bytes)", {"imm_ofs": 0}, ["L1", "L2"], r"privileged events")
add("imm_ofs2", "IMM_OFS 2 (value skewed by one byte; includes the regID byte)", {"imm_ofs": 2}, ["L1", "L2"], r"privileged events")
add("imm_sz1", "IMM_SZ 1 (16-bit immediate)", {"imm_sz": 1}, ["L1", "L2"], r"privileged events")
add("consume4", "CONSUME 4 on the immediate word", {"imm_consume": 4}, ["L1", "L2", "L3"], r"lock-step|TRAP|END")
add("consume6", "CONSUME 6 on the immediate word", {"imm_consume": 6}, ["L1", "L2", "L3"], r"lock-step|TRAP|END")
add("no_final_consume", "missing final CONSUME 1 (the #q byte)", {"no_final_consume": True}, ["L1", "L2", "L3"], r"lock-step|TRAP|END")
add("wr_asel_const0", "WR_ASEL const (RCONST 0) instead of operand 2's register", {"wr_asel": "const", "rconst": 0}, ["L1", "L2"], r"privileged events")
add("wr_asel_const5", "WR_ASEL const with RCONST 5 (right for SBR only)", {"wr_asel": "const", "rconst": 5}, ["L2"], r"privileged events")
add("wr_asel_fld1", "WR_ASEL field 1 (the format byte's register field)", {"wr_asel": "fld1"}, ["L1", "L2"], r"privileged events")
add("wr_dst_gpr", "WR_DST general register instead of privileged", {"wr_dst": "gpr"}, ["L1", "L2"], r"lock-step|final state|privileged events")
add("wr_dst_temp", "WR_DST temp instead of privileged", {"wr_dst": "temp"}, ["L1", "L2"], r"privileged events")
add("flag_upd_check", "FLAG_UPD on the ID-check word", {"flag_upd": "check"}, ["L4", "L1"], r"PSW|psw")
add("flag_upd_write", "FLAG_UPD on the value / write word", {"flag_upd": "write"}, ["L4", "L1"], r"PSW|psw")
add("id_lo", "ID check off by one (allows 1)", {"id_lo": 1}, ["N1"], r"TRAP|lock-step|END")
add("id_hi", "ID check off by one (allows 6)", {"id_hi": 6}, ["N1"], r"TRAP|lock-step|END")
add("no_id_check", "ID check removed", {"no_id_check": True}, ["N1"], r"TRAP|lock-step|END")
add("no_regid_quick_guard", "C_QUICK regID guard removed", {"no_regid_quick_guard": True}, ["regid_quick"], r"TRAP|lock-step|END")
add("no_src_imm_guard", "C_IMM source guard removed", {"no_src_imm_guard": True}, ["src_imm"], r"TRAP|lock-step|END")
add("no_src_quick_guard", "C_QUICK source guard removed", {"no_src_quick_guard": True}, ["src_quick"], r"TRAP|lock-step|END")
add("no_priv_check", "privilege check removed", {"no_priv_check": True}, ["N5"], r"TRAP|lock-step|END")
add("dtab_flag_cleared", "dtab privileged flag cleared", {"dtab_flag_cleared": True}, ["N5"], r"TRAP|lock-step|END")
add("dtab_wrong_routine", "dtab[0x12] at a wrong routine (the NOP routine)", {"dtab_wrong_routine": True}, ["L1", "L2"], r"lock-step|privileged events|TRAP|END")
add("no_f1_guard", "format-I (byte 1 bit 7 = 0) guard removed [extra]", {"no_f1_guard": True}, ["N4"], r"TRAP|lock-step|END")


def model_programs(x, smc):
    out = []
    for n, m in x.man.items():
        if m["mc"] != "prod" or m["where"] != "low" or m["cls"] == "probe":
            continue
        if smc and ("jmp" in m["tags"] or m.get("v2_smc") is None):
            continue
        out.append((n, m))
    return out


def judge(x, smc, mut, progs, imgs):
    """-> list of (program, failures) for the failing programs"""
    tabs = Tabs(smc)
    bad = []
    for n, m in progs:
        f = C.check_v2r2(m, run_model(imgs[n], m.get("el", 0), tabs, mut), "r2b", "smc" if smc else "std")
        if f:
            bad.append((n, f))
    return bad


def run(x, negctl, note):
    print("== mutant-catcher contract (A): microflow model over the real tables")
    imgs = {n: (x.progs / f"{n}.bin").read_bytes() + bytes(16) for n, m in x.man.items() if m["where"] == "low"}
    variants = {False: model_programs(x, False), True: model_programs(x, True)}
    for smc in (False, True):
        bad = judge(x, smc, {}, variants[smc], imgs)
        negctl.append(("m0", f"control: the UNmutated model passes every program in r2b ({'--smc-test' if smc else 'production'} tables, "
                             f"{len(variants[smc])} programs)", not bad, f"failing: {[(n, f[0][:80]) for n, f in bad[:3]]}"))
    nd = 0
    for name, (desc, knobs, tags, regex, equiv) in MUT.items():
        rows = {smc: judge(x, smc, knobs, variants[smc], imgs) for smc in (False, True)}
        allbad = rows[False] + rows[True]
        desig = [(n, f) for n, f in allbad if (set(x.man[n]["tags"]) & set(tags))
                 and (regex is None or any(re.search(regex, g) for g in f))]
        ev = (f"production {len(rows[False])}/{len(variants[False])} programs fail, --smc-test {len(rows[True])}/{len(variants[True])}; "
              f"designated catcher {tags}: {len(desig)} program(s)"
              + (f", e.g. {desig[0][0]}: {desig[0][1][0][:90]}" if desig else f"; failing programs: {sorted({n for n, _ in allbad})[:6]}"))
        if equiv and not allbad:
            negctl.append(("mE", f"model mutant {name} ({desc}): EQUIVALENT - {equiv}", True, ev))
        else:
            negctl.append(("m", f"model mutant {name}: {desc}", bool(desig), ev))
            nd += bool(desig)
    # extra catcher-contract assertions (plan 9 #4)
    for nm, mutk, short_name, crafted_name in (
            ("source C_QUICK guard", {"no_src_quick_guard": True}, "n3_quick_short", "n3_quick_crafted"),
            ("source C_IMM guard (Rn)", {"no_src_imm_guard": True}, "n3_rn_short", "n3_rn_crafted")):
        sh = judge(x, False, mutk, [(short_name, x.man[short_name])], imgs)
        cr = judge(x, False, mutk, [(crafted_name, x.man[crafted_name])], imgs)
        negctl.append(("m", f"catcher precision, {nm} mutant: the ordinary short form ({short_name}) is not enough, the crafted one ({crafted_name}) catches it",
                       bool(cr), f"short form rejected by the mutant run={bool(sh)}; crafted rejected={bool(cr)}"))
    for nm, mutk in (("regid_quick", {"no_regid_quick_guard": True}), ("src_imm", {"no_src_imm_guard": True}), ("src_quick", {"no_src_quick_guard": True})):
        for smc in (False, True):
            ps = [(n, m) for n, m in model_programs(x, smc) if nm in m["tags"]]
            bad = sorted(n for n, _ in judge(x, smc, mutk, ps, imgs))
            note(f"guard reach [{nm}]: the guard mutant is caught by a crafted case ({'--smc-test' if smc else 'production'} table)", bool(bad), f"{bad[:6]} of {len(ps)}")
    for mutk in ({"no_priv_check": True}, {"dtab_flag_cleared": True}):
        names = ("n5_el0", "n5_el1", "n5_el2", "n5_el3", "n5_el2_id3", "n5_el1_badid")
        bad = {n for n, _ in judge(x, False, mutk, [(n, x.man[n]) for n in names], imgs)}
        negctl.append(("m", f"N5 pairing under {list(mutk)[0]}: the EL 0 success passes, every EL 1/2/3 case fails",
                       "n5_el0" not in bad and bad == set(names) - {"n5_el0"}, f"rejected: {sorted(bad)}"))
    print("== mutant-catcher contract (B): probe-image mutants on the real V2")
    probe_mutants(x, negctl, note)


# ---------------------------------------------------------------------------- (B) real-RTL probe mutants
PROBE_MUT = {
    "imm_ofs0": ("IMM_OFS 0", {"imm_ofs": 0}), "imm_ofs2": ("IMM_OFS 2", {"imm_ofs": 2}), "imm_sz1": ("IMM_SZ 1", {"imm_sz": 1}),
    "consume4": ("CONSUME 4 on the immediate word", {"imm_consume": 4}), "consume6": ("CONSUME 6 on the immediate word", {"imm_consume": 6}),
    "no_final_consume": ("missing final CONSUME 1", {"final_consume": 0}),
    "wr_asel_const": ("WR_ASEL const (RCONST 0)", {"wr_asel": 0}), "wr_asel_fld1": ("WR_ASEL field 1", {"wr_asel": 1}),
    "wr_dst_gpr": ("WR_DST general register", {"wr_dst": 1}), "wr_dst_temp": ("WR_DST temp", {"wr_dst": 2}),
    "flag_upd": ("FLAG_UPD 15 on the write word", {"flag_upd": 15}),
}


def probe_mutants(x, negctl, note):
    import m1_r2_run as RUN
    cfgs = ["", "+wide"]
    progs = ["pr_wrasel3_q", "pr_wrasel3_q5"] + [n for n in x.man if n.startswith("pr_l6_")]
    for name, (desc, mut) in PROBE_MUT.items():
        mc, _ = RUN.build_probe(x, f"mc_probe_{name}", mut)
        fails, simcheck, runs, sample = 0, 0, 0, None
        for pn in progs:
            m = x.man[pn]
            for cfg in cfgs:
                for run in (["std"] if not m.get("v2_ack") else ["ack", "delay"]):
                    t = RUN.v2_text(x, pn, m, cfg, run, mc=mc)
                    f = C.check_v2r2(m, t, "r2a", run)
                    runs += 1
                    if f:
                        fails += 1
                        sample = sample or f"{pn} [{cfg or 'default'}/{run}]: {f[0][:90]}"
                        simcheck += any("SIMCHECK" in g for g in f)
        ok = fails > 0
        extra = ""
        if name == "consume4":
            ok = ok and simcheck > 0
            extra = f"; the availability checker (SIMCHECK) fired on {simcheck} run(s)"
        negctl.append(("p", f"probe-image mutant on the real V2: {desc}", ok, f"{fails}/{runs} runs rejected{extra}; first: {sample}"))
