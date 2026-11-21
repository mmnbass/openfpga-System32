#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""R2a: test programs for `ldpr #imm32, #q` (docs/r2-ldpr-plan.md sections 4 and 9).

TEST INFRASTRUCTURE ONLY. No LDPR microcode and no dispatch-table entry exist; today V2 traps
every LDPR with kind `opcode` (dtab[0x12] = trap). The V2 expectation is a PHASE parameter (r2a =
today, r2b = after the R2b microcode) stored per program in the manifest, as in R1a.

Encoding (plan section 1): LDPR #imm32,#q = 12 80 F4 v0 v1 v2 v3 E<q>   (8 bytes; byte 1 = 0x80:
format II, both modm bits 0), value = the little-endian word at start + 3, regID = the quick
descriptor's low 4 bits (E0..EF). In scope: IDs 2 3 4 5 (L1SP L2SP L3SP SBR).

INDEPENDENT EXPECTATIONS. As in m1_r1.py, every program is an explicit execution path whose
expected effects are computed HERE from the encoding, never from a reference run:
    LDPR    next PC = start + 8; privileged register regID <- the immediate; no GPR, no PSW change;
            zero data reads/writes, zero PSW updates
    EVENTS  per instruction, the privileged write events (regID, value) in order. V2 (E3 writes) and
            V60X (every store) see every write; upstream has no write strobe and sees VALUE CHANGES
            only (reset values: stacks 0, SBR 0), so an unchanged-value write (a zero to a zero
            register, a repeated value) is invisible there: its expected set is derived separately
            (exp_events_up) from the same independent model (plan section 9 #3)
A reference or RTL run is CHECKED against these (m1_r2_check.py); nothing reads a run as truth.

Program classes (manifest "cls"; "tags" name the catcher groups of m1_r2a_catchers.py):
    pos      L1-L7 positives (where "low": M1 benches; "s0": the S0 harness, reset start 0xFFFFFFF0)
             run on V60X (m1_ref --ext), upstream and V2
    neg      N1-N4 and N6, crafted guard-reach cases: V2 only (the references disagree on some)
    elneg    N5: EL forced to 1/2/3 (bench plusarg +forceel, V2 only) plus the EL 0 success pair
    probe    bench probes on a TEST-ONLY microcode image (WR_ASEL = 3, trap kinds priv/regid, the EL
             force reaching the privilege condition); never on the production builder

Usage: m1_r2.py <out-dir>     writes <name>.{bin,hex} (low), <name>.rom.hex + <name>.segs (s0), r2_manifest.json
"""
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m1_r1 as R1  # noqa: E402
from m1_image import write  # noqa: E402
from m1_m11 import MOVW, NOP, HALT  # noqa: E402

M = 0xFFFFFFFF
ADD, SUB, CMP, AND = R1.ADD, R1.SUB, R1.CMP, R1.AND
le32 = R1.le32
PSW0 = R1.PSW0
LDPR, PROBE_WR, PROBE_TRAP_PRIV, PROBE_TRAP_REGID, PROBE_PRIV = 0x12, 0xFE, 0xFD, 0xFC, 0xFB
# reset values of the privileged registers in both references (upstream s32_v60.sv S_RESET, MAME reset):
# only the non-zero ones; every other register resets to 0
PRIV_RESET = {7: 0x70, 8: 0xE000, 9: 0x6000, 15: 0xF002}
CONFIGS = ["", "+memwait=2", "+memwait=5 +memjit=4", "+wide"]


def ldpr_bytes(val, rid):
    return [LDPR, 0x80, 0xF4] + le32(val) + [0xE0 | rid]


class LProg(R1.Prog):
    """R1.Prog plus LDPR, privileged-event bookkeeping and a forced EL."""

    def __init__(self, name, cls, where="low", start=0, el=0, mc="prod"):
        super().__init__(name, cls, where, start)
        self.el = el
        self.mc = mc
        self.priv = {}                 # regID -> current value (the model's view; reset values implicit)
        self.ev = {}                   # instruction index -> [(regID, value)]  (every write)
        self.ev_up = {}                # instruction index -> [(regID, value)]  (value changes only)
        self.ldpr_idx = []             # indices of LDPR instructions
        self.tags = []
        self.kinds = []                # per executed instruction: its kind (setup, ldpr, jmp, ...)

    def psw(self):
        z, s, ov, cy = self.flags
        return PSW0 | (self.el << 24) | z | (s << 1) | (ov << 2) | (cy << 3)

    def final_state(self, n):
        regs, psw = [0] * 32, PSW0 | (self.el << 24)
        for _, _, line, _ in self.recs[:n]:
            for tok in line.split()[4:]:
                k, v = tok.split("=")
                if k == "PSW":
                    psw = int(v, 16)
                else:
                    regs[int(k[1:])] = int(v, 16)
        return regs, psw

    def _after(self, kind):
        self.kinds.append(kind)

    def mov(self, r, v):
        super().mov(r, v); self._after("setup")

    def alu_imm(self, op, r, v):
        super().alu_imm(op, r, v); self._after("setup")

    def getpsw(self, r):
        super().getpsw(r); self._after("setup")

    def nop(self):
        super().nop(); self._after("setup")

    def jmp(self, target):
        super().jmp(target); self._after("jmp")

    def halt(self):
        super().halt(); self._after("halt")

    def _wr(self, bs, op, rid, val, kind):
        pc, r0, p0 = self._emit(bs, kind)
        self._record(pc, op, r0, p0, kind)
        i = len(self.recs) - 1
        self.ev[i] = [(rid, val & M)]
        self.ev_up[i] = [(rid, val & M)] if (val & M) != self.priv.get(rid, PRIV_RESET.get(rid, 0)) else []
        self.priv[rid] = val & M
        self._after(kind)
        if kind == "ldpr":
            self.ldpr_idx.append(i)
        return i

    def ldpr(self, val, rid):
        assert rid in (2, 3, 4, 5)
        return self._wr(ldpr_bytes(val, rid), LDPR, rid, val, "ldpr")

    def probe_wr(self, val, q):
        """TEST-ONLY probe instruction 0xFE (never LDPR): same 8-byte shape, writes priv[q] <- imm via WR_ASEL = 3"""
        return self._wr([PROBE_WR, 0x80, 0xF4] + le32(val) + [0xE0 | q], PROBE_WR, q, val, "probe")

    def probe_priv(self):
        """TEST-ONLY probe 0xFB: a one-byte instruction whose dispatch entry carries the privileged flag"""
        pc, r0, p0 = self._emit([PROBE_PRIV], "probe")
        self._record(pc, PROBE_PRIV, r0, p0, "probe")
        self._after("probe")

    def trap_instr(self, bs):
        super().trap_instr(bs)
        self.n_before = len(self.recs)


# ----------------------------------------------------------------------------- positives
VALS = [0, 0x100000, 0xFFFFFFFF, 0xAAAAAAAA, 0x55555555, 0x80000000, 1, 0x20FE00, 0x01020304, 0xDEADBEEF,
        0x7FFFFFFF, 0x000000FF, 0x0000FF00, 0x00FF0000, 0xFF000000, 0x12345678]


def pos_programs():
    out = []
    A = out.append
    rng = random.Random(0x2A1)

    # L1: ldpr #imm32,#5 with several values (the first one preceded by a mov so it is not the first instruction;
    # the first-instruction case follows)
    for i, v in enumerate(VALS):
        p = LProg(f"l1_v{i:02d}", "pos")
        p.tags = ["L1"]
        p.mov(1, 0x11111111)
        p.ldpr(v, 5)
        p.halt()
        A(p)
    p = LProg("l1_first", "pos"); p.tags = ["L1"]
    p.ldpr(0xC0FFEE11, 5); p.halt(); A(p)

    # L2: IDs 2, 3, 4 each (3 values), then all four in sequence, then overwrites
    for rid in (2, 3, 4, 5):
        for j, v in enumerate((0x20FE00, 0xF0E1D2C3, 0)):
            p = LProg(f"l2_id{rid}_v{j}", "pos"); p.tags = ["L2"]
            p.mov(2, 0x22222222)
            p.ldpr(v, rid)
            p.halt()
            A(p)
    for nm, order in (("up", (2, 3, 4, 5)), ("down", (5, 4, 3, 2)), ("mix", (4, 2, 5, 3))):
        p = LProg(f"l2_seq_{nm}", "pos"); p.tags = ["L2"]
        for k, rid in enumerate(order):
            p.ldpr(0x01010101 * (rid + 1) ^ (0x0F0F0000 >> k), rid)
        p.halt()
        A(p)
    # overwrite the same ID: different values; the same value twice (invisible to upstream); zero to a
    # zero register (invisible); nonzero then zero; alternating IDs with a repeated value
    cases = {
        "ow_diff": [(0xAAAA0001, 5), (0x5555FFFE, 5)],
        "ow_same": [(0x13572468, 3), (0x13572468, 3)],
        "ow_zero_to_zero": [(0, 4), (0, 4)],
        "ow_zero_fresh": [(0, 2)],
        "ow_nz_then_zero": [(0x7F7F7F7F, 2), (0, 2)],
        "ow_three": [(0x11111111, 5), (0x22222222, 5), (0x22222222, 5), (0x33333333, 5)],
        "ow_alt_rep": [(0x99999999, 2), (0x99999999, 3), (0x99999999, 2), (0x99999999, 3)],
        "ow_zero_after_other": [(0xABCD0123, 5), (0, 4), (0, 4), (0, 5)],
    }
    for nm, seq in cases.items():
        p = LProg(f"l2_{nm}", "pos"); p.tags = ["L2"]
        p.mov(3, 0x33333333)
        for v, rid in seq:
            p.ldpr(v, rid)
        p.halt()
        A(p)
    # random sequences: LDPR mixed with ALU / MOV / NOP / GETPSW (values drawn with repeats and zeros)
    pool = [0, 0x100000, 0x20FE00, 0xFFFFFFFF] + [rng.getrandbits(32) for _ in range(4)]
    for s in range(40):
        p = LProg(f"l2_rand{s:02d}", "pos"); p.tags = ["L2"]
        for _ in range(rng.randrange(4, 10)):
            c = rng.randrange(6)
            if c < 3:
                p.ldpr(rng.choice(pool), rng.choice((2, 3, 4, 5)))
            elif c == 3:
                p.mov(rng.randrange(1, 9), rng.getrandbits(32))
            elif c == 4:
                p.alu_imm(rng.choice([ADD, SUB, AND, CMP]), rng.randrange(1, 9), rng.getrandbits(32))
            else:
                p.nop()
        if not p.ldpr_idx:
            p.ldpr(rng.choice(pool), rng.choice((2, 3, 4, 5)))
        p.getpsw(12)
        p.halt()
        A(p)

    # L3: start alignments 0..7 mod 8 and the next lines (k NOPs before the LDPR): the 8 bytes cross
    # fetch-word and wide-line boundaries differently
    for k in range(24):
        p = LProg(f"l3_s{k:02d}", "pos"); p.tags = ["L3"]
        for _ in range(k):
            p.nop()
        p.ldpr(0xA1B2C3D4 ^ (k * 0x01010101), 2 + (k % 4))
        p.ldpr(0x0D0C0B0A + k, 2 + ((k + 1) % 4))      # a second one: the next alignment (+8) is the same
        p.halt()
        A(p)

    # L4: flags set by ADD (Z, S, OV, CY separately and combined) before LDPR, GETPSW after: unchanged
    for nm, (b, a) in {"z": (0, 0), "s": (0, 0x80000000), "ov": (0x7FFFFFFF, 1), "cy": (0xFFFFFFFF, 2),
                       "zcy": (0xFFFFFFFF, 1), "sov": (0x7FFFFFFF, 0x7FFFFFFF), "none": (1, 1)}.items():
        p = LProg(f"l4_flags_{nm}", "pos"); p.tags = ["L4"]
        p.mov(4, b); p.alu_imm(ADD, 4, a)
        p.getpsw(9)
        p.ldpr(0x40404040, 4)
        p.getpsw(10)
        p.ldpr(0x50505050, 5)
        p.halt()
        A(p)

    # L5: LDPR between mov.w #imm32 and jmp /abs (neighbouring immediate decoders and the redirect); production only
    p = LProg("l5_mov_ldpr_jmp", "pos"); p.tags = ["L5", "jmp"]
    p.mov(1, 0xAAAA5555); p.ldpr(0x00100000, 5)
    here = p.cur
    p.jmp(0x80)
    p.decoy(here + 6)
    p.org(0x80); p.mov(2, 0x2222AAAA); p.halt()
    A(p)
    p = LProg("l5_jmp_ldpr_mov", "pos"); p.tags = ["L5", "jmp"]
    p.mov(1, 0x01010101)
    here = p.cur
    p.jmp(0x91)
    p.decoy(here + 6)
    p.org(0x91); p.ldpr(0xFEEDF00D, 3); p.mov(2, 0x77777777); p.halt()
    A(p)
    p = LProg("l5_ldpr_jmp_ldpr", "pos"); p.tags = ["L5", "jmp"]
    p.ldpr(0x0000AAAA, 2)
    here = p.cur
    p.jmp(0x100)
    p.decoy(here + 6)
    p.org(0x100); p.ldpr(0x0000BBBB, 2); p.halt()
    A(p)
    p = LProg("l5_imm_neighbours", "pos"); p.tags = ["L5", "jmp"]
    p.mov(1, 0x11111111); p.alu_imm(ADD, 1, 0x22222222); p.ldpr(0x33333333, 4); p.alu_imm(SUB, 1, 0x1)
    p.mov(2, 0x44444444); p.ldpr(0x55555555, 5); p.mov(3, 0x66666666)
    here = p.cur
    p.jmp(0x180)
    p.decoy(here + 6)
    p.org(0x180); p.halt()
    A(p)

    # L7 (reset-native S0 harness): reset vector 0xFFFFFFF0; jmp /abs -> LDPR -> HALT at a high address
    # (24-bit mirror), the GA2 boot shapes, and LDPR directly at the reset vector (JMP-free: SMC S0 builds)
    for nm, t in (("100506", 0x100506), ("hi", 0x1FFFF0), ("mirror", 0xF00123), ("upper_ff", 0xFF100506)):
        p = LProg(f"l7_jmp_{nm}", "pos", "s0", 0xFFFFFFF0); p.tags = ["L7", "jmp"]
        p.jmp(t)
        p.decoy(0xFFFFFFF6)
        p.org(t); p.ldpr(0x00100000, 5); p.halt()
        A(p)
    p = LProg("l7_ga2_boot", "pos", "s0", 0xFFFFFFF0); p.tags = ["L7", "jmp"]
    p.jmp(0x100506)
    p.decoy(0xFFFFFFF6)
    p.org(0x100506); p.ldpr(0x100000, 5)             # the four boot LDPRs' operands, made sequential
    p.ldpr(0x20FE00, 2); p.ldpr(0x20FE00, 3); p.ldpr(0x20FE00, 4); p.halt()
    A(p)
    for rid in (2, 3, 4, 5):
        p = LProg(f"l7_direct_id{rid}", "pos", "s0", 0xFFFFFFF0); p.tags = ["L7"]
        p.ldpr(0x0F00D000 + rid, rid); p.halt()
        A(p)
    p = LProg("l7_direct_pair", "pos", "s0", 0xFFFFFFF0); p.tags = ["L7"]
    p.mov(1, 0x13131313)
    p.ldpr(0x77777777, 3)                      # 0xFFFFFFF7..FE
    p.nop()
    p.halt()
    A(p)
    for s in range(0xF8, 0x100):               # the 8 bytes cross 0xFFFFFFFF -> 0 at every offset
        p = LProg(f"l7_wrap_{s:02x}", "pos", "s0", 0xFFFFFFF0); p.tags = ["L7", "wrap"]
        p.mov(1, 0x13131313)                    # FFFFFFF0..F6
        while p.cur < 0xFFFFFF00 + s:
            p.nop()
        assert p.cur == 0xFFFFFF00 + s, hex(p.cur)
        p.ldpr(0x7F010102, 2 + (s % 4))
        p.halt()
        A(p)
    return out


# ----------------------------------------------------------------------------- L6: withheld bytes
def l6_programs():
    """L6 (plan 9 #5): the fetches at or above LIM (a multiple of 8, so the data-bus and the wide path agree) are
    withheld; k = how many of the LDPR's 8 bytes are below LIM (k = 2..7: operand bytes missing, k = 8: all
    present, only the NEXT instruction withheld). Run (a) with +ackdelay (release by cycle count, no redirect):
    the LDPR must stall until the byte is available, then retire with the right write and PC + 8; and (b) with no
    release: V2 waits (no trap), the LDPR has no privileged event. k = 6 is the plan's arrangement: the last
    immediate byte (start + 6) and the regID byte withheld, exactly four bytes (+2..+5) available at the
    five-byte immediate issue, so the availability checker catches an under-availability (early issue)."""
    out = []
    for LIM in (16, 24):
        for k in range(2, 9):
            p = LProg(f"l6_L{LIM}_k{k}", "pos"); p.tags = ["L6"]
            p.mov(1, 0x16161616)                                        # 0..6
            while p.cur < LIM - k:
                p.nop()
            assert p.cur == LIM - k, (LIM, k, p.cur)
            s = p.cur
            p.ldpr(0xB6A59483, 2 + (k % 4))
            p.halt()
            p.extra = {"limit": LIM, "k": k, "s": s, "delay": 200}
            out.append(p)
    return out


# ----------------------------------------------------------------------------- negatives
def trap_prog(name, bs, kind, tags, el=0, mc="prod", pre=True, tail=None):
    """A program whose last instruction (the unexecuted one) V2 must trap on in r2b with `kind`."""
    p = LProg(name, "neg" if not el else "elneg", el=el, mc=mc)
    p.tags = tags
    if pre:
        p.mov(1, 0x01010101)
    p.trap_instr(bs)
    p.extra = {"kind": kind}
    return p


def neg_programs():
    out = []
    A = out.append
    v = 0x5A5A1234
    # N1: regID #0, #1, #6..#15 -> regid
    for rid in (0, 1) + tuple(range(6, 16)):
        A(trap_prog(f"n1_id{rid}", ldpr_bytes(v, rid), "regid", ["N1"]))
    # N2: regID not quick: #imm32 (F4), Rn (OI-5; modm 1 of operand 2), /abs, [Rn] -> mode
    for r in (2, 5, 9):
        A(trap_prog(f"n2_imm32_{r}", [LDPR, 0x80, 0xF4] + le32(v) + [0xF4] + le32(r), "mode", ["N2", "regid_quick"]))
    for r in (2, 3, 4, 5, 7):       # Rn regID: byte 1 bit 5 selects modm 1 for operand 2; 0x60|r register direct
        A(trap_prog(f"n2_rn_{r}", [LDPR, 0xA0, 0xF4] + le32(v) + [0x60 | r], "mode", ["N2", "regid_quick"]))
    A(trap_prog("n2_abs", [LDPR, 0x80, 0xF4] + le32(v) + [0xF3] + le32(0x1230), "mode", ["N2", "regid_quick"]))
    for r in (3, 5):
        A(trap_prog(f"n2_ind_{r}", [LDPR, 0x80, 0xF4] + le32(v) + [0x60 | r], "mode", ["N2", "regid_quick"]))
    # N3: src not #imm32: #q (OI-3), Rn, /abs, [Rn]; the ordinary short form and the CRAFTED 8-byte form whose byte at
    # start + 7 encodes an accepted regID, so a mutant without the guard would execute (plan 9 #4)
    for fill in ((), (0x11, 0x22, 0x33, 0x44)):
        sfx = "crafted" if fill else "short"
        A(trap_prog(f"n3_quick_{sfx}", [LDPR, 0x80, 0xE7] + list(fill) + [0xE5], "mode", ["N3", "src_quick"] + (["crafted"] if fill else [])))
        A(trap_prog(f"n3_rn_{sfx}", [LDPR, 0xC0, 0x64] + list(fill) + [0xE5], "mode", ["N3", "src_imm"] + (["crafted"] if fill else [])))
        A(trap_prog(f"n3_ind_{sfx}", [LDPR, 0x80, 0x63] + list(fill) + [0xE3], "mode", ["N3", "src_imm"] + (["crafted"] if fill else [])))
    A(trap_prog("n3_abs", [LDPR, 0x80, 0xF3] + le32(0x1230) + [0xE5], "mode", ["N3", "src_imm", "crafted"]))
    A(trap_prog("n3_abs_hi", [LDPR, 0x80, 0xF3] + le32(0x00F0F0F0) + [0xE2], "mode", ["N3", "src_imm", "crafted"]))
    # N4: format I encodings (byte 1 bit 7 = 0): out of scope -> mode
    for nm, fb in (("f1_0", 0x05), ("f1_d", 0x25), ("f1_m", 0x45), ("f1_dm", 0x65), ("f1_max", 0x7F)):
        A(trap_prog(f"n4_{nm}", [LDPR, fb, 0xF4] + le32(v) + [0xE5], "mode", ["N4"]))
    # N5: EL forced 1, 2, 3 (the V2 bench force) -> priv; and the EL 0 success pair
    for el in (1, 2, 3):
        A(trap_prog(f"n5_el{el}", ldpr_bytes(v, 5), "priv", ["N5"], el=el))
    A(trap_prog("n5_el2_id3", ldpr_bytes(v, 3), "priv", ["N5"], el=2))
    # privilege takes precedence over the regID: an invalid ID at EL != 0 is still `priv` (the check is first)
    A(trap_prog("n5_el1_badid", ldpr_bytes(v, 1), "priv", ["N5", "prec"], el=1))
    p = LProg("n5_el0", "pos"); p.tags = ["N5"]
    p.mov(1, 0x01010101); p.ldpr(v, 5); p.halt()
    out.append(p)
    return out


def probe_programs():
    """TEST-ONLY bench probes (microcode image build/M1/r2a/run/mc_probe; the production builder is untouched)."""
    out = []
    p = LProg("pr_wrasel3_q", "probe", mc="probe"); p.tags = ["probe"]
    p.mov(1, 0x01010101)
    for q in range(16):
        p.probe_wr(0x0A0B0C0D + q * 0x01010101, q)
    p.halt()
    out.append(p)
    p = LProg("pr_wrasel3_q5", "probe", mc="probe"); p.tags = ["probe"]
    p.probe_wr(0xCAFE0005, 5); p.halt()
    out.append(p)
    for nm, op, kind in (("priv", PROBE_TRAP_PRIV, "priv"), ("regid", PROBE_TRAP_REGID, "regid")):
        p = LProg(f"pr_trap_{nm}", "probe", mc="probe"); p.tags = ["probe"]
        p.mov(1, 0x01010101)
        p.trap_instr([op])
        p.extra = {"kind": kind}
        out.append(p)
    # the EL force reaches the dispatch-flag condition: probe 0xFB retires at EL 0, traps `priv` at EL 1..3
    for el in (0, 1, 2, 3):
        p = LProg(f"pr_el{el}", "probe", el=el, mc="probe"); p.tags = ["probe"]
        p.mov(1, 0x01010101)
        if el == 0:
            p.probe_priv(); p.probe_priv(); p.halt()
        else:
            p.trap_instr([PROBE_PRIV])
            p.extra = {"kind": "priv"}
        out.append(p)
    # L6 arrangement on the probe (real RTL): the probe's operand sequence is the planned LDPR's, so the withheld-byte
    # arrangement (limit multiple of 8; k bytes below it) is exercised on V2 now: k = 5 (two immediate bytes + regID
    # withheld), 6 (the plan's: exactly four bytes at the five-byte immediate issue), 7 (only the regID byte)
    for LIM in (16, 24):
        for k in (5, 6, 7):
            p = LProg(f"pr_l6_L{LIM}_k{k}", "probe", mc="probe"); p.tags = ["probe", "L6"]
            p.mov(1, 0x16161616)
            while p.cur < LIM - k:
                p.nop()
            assert p.cur == LIM - k
            s_ = p.cur
            p.probe_wr(0xB6A59483, 2 + (k % 4))
            p.halt()
            p.extra = {"limit": LIM, "k": k, "s": s_, "delay": 200}
            out.append(p)
    # the EL force is visible to GETPSW (works on the production microcode too)
    for el in (1, 2, 3):
        p = LProg(f"pr_getpsw_el{el}", "probe", el=el); p.tags = ["probe", "elforce"]
        p.getpsw(1); p.halt()
        out.append(p)
    return out


# ----------------------------------------------------------------------------- expectations
def v2_expect(p):
    """(std, ack, delay, smc): per-phase V2 expectations. std: the plain run; ack: fetches >= limit withheld
    forever; delay: withheld, released by cycle count; smc: the --smc-test builds (same programs)."""
    ph = ("r2a", "r2b")
    if p.cls in ("neg", "elneg") or (p.cls == "probe" and hasattr(p, "trap_pc")):
        kind = p.extra["kind"]
        t = lambda tr: {"kind": "trap", "trap": tr, "pc": p.trap_pc, "n": p.n_before}
        if p.cls == "probe":
            std = {x: t(kind) for x in ph}
        else:
            std = {"r2a": t("opcode"), "r2b": t(kind)}
        return std, None, None, dict(std)
    n = len(p.recs)
    if p.cls == "probe":
        e = {x: {"kind": "halt"} for x in ph}
        if "limit" in p.extra:        # phase-independent: the probe image has no LDPR
            ti = next(i for i, k in enumerate(p.kinds) if k == "probe")
            L = p.extra["limit"]
            wait = {"kind": "wait", "retired": ti, "state_n": ti, "limit": L}
            return e, {x: wait for x in ph}, {x: {"kind": "halt", "limit": L} for x in ph}, None
        return e, None, None, None
    jmp_i = next((i for i, k in enumerate(p.kinds) if k == "jmp"), None)
    ldpr_i = p.ldpr_idx[0] if p.ldpr_idx else None
    def trap(i):
        return {"kind": "trap", "trap": "opcode", "pc": p.recs[i][0], "n": i}
    # production: JMP exists (R1b); LDPR is unsupported in r2a
    std = {"r2a": trap(ldpr_i), "r2b": {"kind": "halt"}}
    # --smc-test: no JMP; LDPR unsupported in r2a, supported in r2b (dispatch entry in both builds)
    first_r2a = min(x for x in (jmp_i, ldpr_i) if x is not None)
    smc = {"r2a": trap(first_r2a), "r2b": trap(jmp_i) if jmp_i is not None else {"kind": "halt"}}
    ack = delay = None
    if "limit" in p.extra:
        L, k, s = p.extra["limit"], p.extra["k"], p.extra["s"]
        wait = {"kind": "wait", "retired": ldpr_i, "state_n": ldpr_i, "limit": L}
        # r2a: the LDPR is dispatched as soon as its 2 opcode bytes are below the limit (always: k >= 2)
        ack = {"r2a": dict(trap(ldpr_i), limit=L), "r2b": wait}
        delay = {"r2a": dict(trap(ldpr_i), limit=L), "r2b": {"kind": "halt", "limit": L}}
    return std, ack, delay, smc


def rom_off(a):
    return R1.rom_off(a)


def emit(progs, out):
    man = {}
    for p in progs:
        info = {"name": p.name, "cls": p.cls, "where": p.where, "start_pc": p.start, "el": p.el, "mc": p.mc,
                "tags": p.tags}
        std, ack, delay, smc = v2_expect(p)
        info["v2"], info["v2_ack"], info["v2_delay"], info["v2_smc"] = std, ack, delay, smc
        trapcls = hasattr(p, "trap_pc")
        if not trapcls:
            n = len(p.recs)
            info["exp_L"] = [r[2][2:] for r in p.recs]
            regs, psw = p.final_state(n)
            info["final_regs"], info["final_psw"] = regs, psw
            info["n_instrs"] = n
            info["halt_pc"] = p.halt_pc
        else:
            info["n_before"], info["trap_pc"] = p.n_before, p.trap_pc
            info["exp_L"] = [r[2][2:] for r in p.recs[:p.n_before]]
            regs, psw = p.final_state(p.n_before)
            info["final_regs"], info["final_psw"] = regs, psw
            info["n_instrs"] = p.n_before
        n = len(info["exp_L"])
        info["exp_counters"] = {str(i): {"dr": 0, "dw": 0, "priv": len(p.ev.get(i, []))} for i in range(n)}
        info["exp_counters_up"] = {str(i): {"dr": 0, "dw": 0, "priv": len(p.ev_up.get(i, []))} for i in range(n)}
        info["exp_events"] = {str(i): [list(e) for e in p.ev.get(i, [])] for i in range(n)}
        info["exp_events_up"] = {str(i): [list(e) for e in p.ev_up.get(i, [])] for i in range(n)}
        info["final_priv"] = {str(k): v for k, v in sorted(p.priv.items())}
        info["ldpr_idx"] = [i for i in p.ldpr_idx if i < n]
        info["wr_idx"] = [i for i in p.ev if i < n]
        info["refs"] = ["ext", "up"] if p.cls == "pos" else []
        info["extra"] = {k: v for k, v in p.extra.items() if k != "kind"}
        if p.where == "low":
            hi = max(p.mem) + 1
            assert hi < 0x10000, p.name
            img = bytearray(hi + 8)
            for a, b in p.mem.items():
                img[a] = b
            write(bytes(img), str(out / p.name))
        else:
            rom = {}
            for a, b in p.mem.items():
                o = rom_off(a)
                assert rom.get(o, b) == b, f"{p.name}: ROM alias conflict at {o:06x}"
                rom[o] = b
            with open(out / f"{p.name}.rom.hex", "w") as f:
                words = {}
                for o, b in rom.items():
                    words.setdefault(o >> 1, [0, 0])[o & 1] = b
                last = None
                for w in sorted(words):
                    if last is None or w != last + 1:
                        f.write("@%x\n" % w)
                    f.write("%04x\n" % (words[w][0] | (words[w][1] << 8)))
                    last = w
            with open(out / f"{p.name}.segs", "w") as f:
                runs, cur = [], None
                for a in sorted(p.mem):
                    if cur and a == cur[0] + len(cur[1]):
                        cur[1].append(p.mem[a])
                    else:
                        cur = [a, bytearray([p.mem[a]])]
                        runs.append(cur)
                for a, bs in runs:
                    f.write(f"{a:08x} {bytes(bs).hex()}\n")
        info["bytes"] = {f"{a:08x}": b for a, b in sorted(p.mem.items())} if p.cls in ("neg", "elneg") else None
        man[p.name] = info
    return man


def all_programs():
    return pos_programs() + l6_programs() + neg_programs() + probe_programs()


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    progs = all_programs()
    names = [p.name for p in progs]
    assert len(names) == len(set(names))
    man = emit(progs, out)
    (out / "r2_manifest.json").write_text(json.dumps(man, indent=0))
    cnt = {}
    for p in progs:
        k = f"{p.cls}/{p.where}"
        cnt[k] = cnt.get(k, 0) + 1
    print("r2: " + ", ".join(f"{k} {v}" for k, v in sorted(cnt.items())) + f" (total {len(progs)})")


if __name__ == "__main__":
    main()
