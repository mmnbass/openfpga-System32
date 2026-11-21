#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""R3a: test programs for `movea.b /abs, Rn` (docs/r3-movea-plan.md sections 4, 6).

TEST INFRASTRUCTURE ONLY. No MOVEA microcode and no dtab[0x40] entry exist; today V2 traps every MOVEA
with kind `opcode` (dtab[0x40] = dtab[0x42] = dtab[0x44] = trap). The V2 expectation is a PHASE parameter
(r3a = today, r3b = after the R3b microcode) stored per program in the manifest, as in R1a / R2a.

Encoding (plan section 1):  MOVEA.B /abs, Rn  =  40 <0x20|n> F3 a0 a1 a2 a3   (7 bytes)
    byte 1 = {0, m = 0, d = 1, reg = n} (Format I of the manual; V2 "F2" with D = 1), mode byte F3 = /abs,
    value = the little-endian word at start + 3. The destination receives the 32-bit ADDRESS: no extension,
    no byte mask; flags unchanged; no data access; next PC = start + 7.

INDEPENDENT EXPECTATIONS. As in m1_r1.py / m1_r2.py, every program is an explicit execution path whose expected
effects are computed HERE from the encoding and a small ALU model, never from a reference run:
    MOVEA   next PC = start + 7; GPR[n] <- the address decoded from the bytes at start + 3; no flag change;
            exactly ONE GPR write event (also when the value is unchanged); zero data reads/writes, zero
            privileged events, zero PSW updates
    neighbours (setup mov.w / ALU / GETPSW, LDPR, JMP, NOP, HALT) have their own independent expectations:
            GPR write events (mov.w and ADD/SUB/AND: one, CMP: none, GETPSW: one), PSW updates (ALU: one),
            privileged events (LDPR: one)
GPR write EVENTS (the V2 observer's +gprev "G" lines and the corrected V60X's --gprev) are verified on V2 and the
corrected V60X; upstream shows value changes only (the lock-step records), as for the privileged registers (R2 9 #3).

Program classes (manifest "cls"; "tags" name the catcher groups of m1_r3a_catchers.py):
    pos      A1-A10 (+ A5 EL variants, N6 byte-1 variants that are legal MOVEAs): where "low" = M1 benches,
             "s0" = the S0 harness (reset start 0xFFFFFFF0); refs V60X (--ext, corrected) and upstream
    neg      N1-N4, N5 [Rn] cases and N6 mutated encodings: V2 only
    probe3   the same positives on the TEST-ONLY probe microcode image (opcode 0xFE stands for MOVEA; the
             stripped-down execute step of the plan's sketch, no guards), and the trap-in-the-middle probes
Usage: m1_r3.py <out-dir>        writes <name>.{bin,hex} (low), <name>.rom.hex + <name>.segs (s0), r3_manifest.json
       m1_r3.py <out-dir> --freeze <results.json>   also writes the frozen-expectation record
       m1_r3.py <out-dir> --verify-frozen <results.json>   regenerates and compares the manifest digest
"""
import hashlib
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m1_r1 as R1  # noqa: E402
import m1_r2 as R2  # noqa: E402
from m1_image import write  # noqa: E402
from m1_m11 import MOVW, NOP, HALT  # noqa: E402

M = 0xFFFFFFFF
ADD, SUB, CMP, AND = R1.ADD, R1.SUB, R1.CMP, R1.AND
le32 = R1.le32
MOVEA_B, MOVEA_H, MOVEA_W = 0x40, 0x42, 0x44
MODE_ABS = 0xF3
PROBE_OPC = 0xFE                       # TEST-ONLY probe opcode standing for MOVEA.B (stripped-down execute step)
PROBE_MID, PROBE_MID2 = 0xFC, 0xFB     # TEST-ONLY: trap in the middle (after CONSUME 2 / after the mode lookup)
CONFIGS = R2.CONFIGS
ALU_NAME = {ADD: "add", SUB: "sub", CMP: "cmp", AND: "and"}

# the plan's quoted first R3 pair (section 1): the generator re-encodes it, the bytes are asserted equal
GA2_PAIR = [(0x00210000, 31, "40 3F F3 00 00 21 00"), (0x00208000, 25, "40 39 F3 00 80 20 00")]
GA2_THIRD = "40 2A F2 70 00 00 00"      # movea.b 10058C[PC], R10: mode byte F2 (PC disp32): R4's, a `mode` trap in R3b


class MProg(R2.LProg):
    """R2.LProg plus MOVEA, GPR write events, ALU register-register and the PSW-update expectation."""

    def __init__(self, name, cls, where="low", start=0, el=0, mc="prod"):
        super().__init__(name, cls, where, start, el, mc)
        self.gev = {}                  # instruction index -> [(register, value)]  every GPR write
        self.movea_idx = []
        self.psw_upd = {}              # instruction index -> number of PSW update events (ALU: 1)
        self.opcs = {}                 # instruction index -> opcode

    # ---- neighbours with their GPR events ----
    def mov(self, r, v):
        super().mov(r, v)
        self.gev[len(self.recs) - 1] = [(r, v & M)]

    def alu_imm(self, op, r, v):
        super().alu_imm(op, r, v)
        i = len(self.recs) - 1
        self.psw_upd[i] = 1
        if op != CMP:
            self.gev[i] = [(r, self.regs[r])]

    def alu_rr(self, op, rd, rs):
        """ALU.W Rs, Rd  (Format I d = 1, modm 1, register-direct source): Rd <- Rd op Rs"""
        bs = [op, 0x60 | rd, 0x60 | rs]
        pc, r0, p0 = self._emit(bs, "setup")
        res, z, s, ov, cy = R1.alu(op, self.regs[rd], self.regs[rs], self.flags[3])
        if op != CMP:
            self.regs[rd] = res
        self.flags = [z, s, ov, cy]
        self._record(pc, op, r0, p0, "setup")
        self._after("setup")
        i = len(self.recs) - 1
        self.psw_upd[i] = 1
        if op != CMP:
            self.gev[i] = [(rd, res)]

    def getpsw(self, r):
        super().getpsw(r)
        self.gev[len(self.recs) - 1] = [(r, self.regs[r])]

    # ---- MOVEA ----
    def movea(self, addr, rd, opc=MOVEA_B, kind="movea", mode=MODE_ABS, byte1=None):
        """movea /abs, Rd, Format I d = 1. The value is DECODED from the emitted bytes (not taken from `addr`)."""
        b1 = (0x20 | rd) if byte1 is None else byte1
        bs = [opc, b1, mode] + le32(addr)
        pc, r0, p0 = self._emit(bs, kind)
        val = int.from_bytes(bytes(bs[3:7]), "little")
        dest = b1 & 31
        self.regs[dest] = val
        self._record(pc, opc, r0, p0, kind)
        i = len(self.recs) - 1
        self.gev[i] = [(dest, val)]
        self.opcs[i] = opc
        self._after(kind)
        self.movea_idx.append(i)
        return i

    def halt_tail(self, tail):
        """HALT followed by tail bytes that are never executed (an N6 opcode flip to 0x00)"""
        pc, r0, p0 = self._emit([HALT] + list(tail), "halt")
        self._record(pc, HALT, r0, p0, "halt")
        self.halt_pc = pc
        self._after("halt")

    def trap_raw(self, bs, opc_kind):
        self.trap_instr(bs)


def new(name, cls="pos", where="low", start=0, **kw):
    p = MProg(name, cls, where, start, **kw)
    p.tags = []
    return p


# ----------------------------------------------------------------------------- positives
A1_ADDRS = [0, 1, 0xFF, 0x100, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF, 0x00210000, 0x00208000, 0x00E00000,
            0xDEADBEEF, 0xAAAAAAAA, 0x55555555, 0x80, 0x8000, 0x00FF0000, 0xFF000000, 0x12345678, 0x000000FE]
PRE = lambda r: (0x10203040 + r * 0x01010101) & M


def positives(opc=MOVEA_B, pfx="", cls="pos", mc="prod", s0=True):
    """A1-A10 and the A5 EL variants. opc/pfx/cls/mc: the probe copies use opcode 0xFE (the M1-bench programs only)."""
    out = []
    A = out.append
    rng = random.Random(0xA3 + opc)

    def P(name, **kw):
        p = new(pfx + name, cls, mc=mc, **kw)
        return p

    # A1: address edges (no sign / zero extension, no byte mask)
    for i, a in enumerate(A1_ADDRS):
        p = P(f"a1_v{i:02d}"); p.tags = ["A1"]
        p.mov(1, 0x11111111); p.mov(5, 0x13572468)
        p.movea(a, 5, opc); p.halt(); A(p)
    # A2: all 32 destinations R0..R31, every register preloaded with a distinct value
    for rd in range(32):
        p = P(f"a2_r{rd:02d}"); p.tags = ["A2"]
        for r in range(32):
            p.mov(r, PRE(r))
        p.movea(0xC3D2E1F0 ^ (rd * 0x01030507), rd, opc); p.halt(); A(p)
    # A3: destination previously 0xFFFFFFFF / 0 / equal to the address (unchanged value) / address ^ 1, and the
    # target memory holds a pattern that must not be read (a read would give the pattern, or the byte 0x5A, zero-extended)
    TA = 0x2000
    for nm, prev in (("ffff", M), ("zero", 0), ("same", TA), ("near", TA ^ 1), ("pat", 0xA55AC33C)):
        p = P(f"a3_{nm}"); p.tags = ["A3"]
        p.put(TA, [0xA5, 0x5A, 0xC3, 0x3C, 0x5A, 0x00, 0x00, 0x00])
        p.mov(7, prev); p.movea(TA, 7, opc); p.halt(); A(p)
    p = P("a3_byte5a"); p.tags = ["A3"]
    p.put(0x20, [0x5A])
    p.mov(1, 0x11111111); p.movea(0x20, 1, opc); p.halt(); A(p)             # the plan's probe: R1 = 0x20, never 0x5A
    for nm, prev in (("same_twice", 0x2040), ("diff_twice", 0)):
        p = P(f"a3_{nm}"); p.tags = ["A3", "A8"]
        p.put(0x2040, [0x99, 0x99, 0x99, 0x99])
        p.mov(3, prev); p.movea(0x2040, 3, opc); p.movea(0x2040, 3, opc); p.halt(); A(p)
    # A4: the address points at: the instruction's own bytes / its address field, unmapped, I/O region, top of RAM
    p = P("a4_own"); p.tags = ["A4"]
    p.mov(1, 0x11111111); here = p.cur; p.movea(here, 2, opc); p.movea(p.cur + 3, 3, opc); p.halt(); A(p)
    for nm, a in (("unmapped", 0x00F00000), ("io", 0x00C00000), ("io_a", 0x00C0000A), ("last_word", 0x0001FFFC),
                  ("beyond", 0x7FFF0000), ("zero_page", 0)):
        p = P(f"a4_{nm}"); p.tags = ["A4"]
        p.mov(1, 0x11111111); p.movea(a, 4, opc); p.halt(); A(p)
    # A5: flags set by ADD (each of Z, S, OV, CY, and combinations), then MOVEA, then GETPSW: PSW unchanged
    for nm, (b, a) in {"z": (0, 0), "s": (0, 0x80000000), "ov": (0x7FFFFFFF, 1), "cy": (0xFFFFFFFF, 2),
                       "zcy": (0xFFFFFFFF, 1), "sov": (0x7FFFFFFF, 0x7FFFFFFF), "none": (1, 1)}.items():
        p = P(f"a5_flags_{nm}"); p.tags = ["A5"]
        p.mov(4, b); p.alu_imm(ADD, 4, a)
        p.getpsw(9); p.movea(0x44332211, 6, opc); p.getpsw(10); p.halt(); A(p)
    # A6: start alignments 0..7 mod 8 (and the next lines): k NOPs before; two MOVEAs back to back
    for k in range(24):
        p = P(f"a6_s{k:02d}"); p.tags = ["A6"]
        for _ in range(k):
            p.nop()
        p.movea(0xA1B2C3D4 ^ (k * 0x01010101), 2 + (k % 4), opc)
        p.movea(0x0D0C0B0A + k, 2 + ((k + 1) % 4), opc)
        p.halt(); A(p)
    # A8: sequences
    seqs = {
        "same_reg": [(0x11110001, 3), (0x22220002, 3), (0x33330003, 3)],
        "diff_reg": [(0x11110001, 3), (0x22220002, 4), (0x33330003, 31)],
        "same_val_reg": [(0x44440004, 5), (0x44440004, 5)],
        "same_val_regs": [(0x55550005, 5), (0x55550005, 6), (0x55550005, 5)],
        "zero_then_ff": [(0, 8), (M, 8), (0, 8)],
    }
    for nm, sq in seqs.items():
        p = P(f"a8_{nm}"); p.tags = ["A8"]
        p.mov(3, 0x33333333)
        for a, r in sq:
            p.movea(a, r, opc)
        p.halt(); A(p)
    for op in (ADD, SUB, CMP, AND):          # MOVEA then ALU.W Rn, Rm using the stored address (the value is the whole word)
        p = P(f"a8_{ALU_NAME[op]}_use"); p.tags = ["A8"]
        p.mov(4, 0x00000010); p.mov(2, 0xFFFFFFF0)
        p.movea(0xDEAD1234, 3, opc)
        p.alu_rr(op, 4, 3); p.alu_rr(op, 2, 3); p.getpsw(9); p.halt(); A(p)
    # ldpr / jmp neighbours (R1 / R2 interplay): LDPR is dispatched in every build, JMP in production only
    p = P("a8_ldpr_before"); p.tags = ["A8"]
    p.mov(1, 0x11111111); p.ldpr(0x00100000, 5); p.movea(0x00210000, 31, opc); p.halt(); A(p)
    p = P("a8_ldpr_after"); p.tags = ["A8"]
    p.mov(1, 0x11111111); p.movea(0x00210000, 31, opc); p.ldpr(0x00100000, 5); p.mov(2, 0x22222222); p.halt(); A(p)
    p = P("a8_ldpr_between"); p.tags = ["A8"]
    p.movea(0x00208000, 25, opc); p.ldpr(0x0020FE00, 2); p.movea(0x00210000, 31, opc); p.ldpr(0x00100000, 5); p.halt(); A(p)
    p = P("a8_jmp_after"); p.tags = ["A8", "jmp"]
    p.mov(1, 0x11111111); p.movea(0x00210000, 31, opc)
    here = p.cur; p.jmp(0x80); p.decoy(here + 6)
    p.org(0x80); p.movea(0x00208000, 25, opc); p.halt(); A(p)
    p = P("a8_jmp_before"); p.tags = ["A8", "jmp"]
    here = p.cur; p.jmp(0x91); p.decoy(here + 6)
    p.org(0x91); p.movea(0x00208000, 25, opc); p.mov(2, 0x22222222); p.halt(); A(p)
    p = P("a8_jmp_decoy_abs"); p.tags = ["A8", "jmp"]
    p.mov(1, 0x01010101); p.movea(0x12345678, 6, opc)
    here = p.cur; p.jmp(0x100)
    p.put(here + 6, [MOVEA_B, 0x20 | 9, MODE_ABS] + le32(0xBADBAD00) + [HALT])      # never executed (the queue is flushed)
    p.org(0x100); p.halt(); A(p)
    # random mixes (seeded): MOVEA with mov / ALU / register-register ALU / NOP / GETPSW / LDPR neighbours
    pool = [0, M, 0x80000000, 0x7FFFFFFF, 0x00210000, 0x2000] + [rng.getrandbits(32) for _ in range(6)]
    for s in range(30):
        p = P(f"a8_rand{s:02d}"); p.tags = ["A8"]
        for r in range(1, 7):
            p.mov(r, rng.getrandbits(32))
        nm = 0
        for _ in range(rng.randrange(6, 14)):
            c = rng.randrange(8)
            if c < 3:
                p.movea(rng.choice(pool), rng.randrange(1, 9) if rng.random() < 0.7 else rng.choice((0, 15, 30, 31)), opc); nm += 1
            elif c == 3:
                p.mov(rng.randrange(1, 9), rng.choice(pool))
            elif c == 4:
                p.alu_imm(rng.choice([ADD, SUB, AND, CMP]), rng.randrange(1, 9), rng.getrandbits(32))
            elif c == 5:
                p.alu_rr(rng.choice([ADD, SUB, AND, CMP]), rng.randrange(1, 9), rng.randrange(1, 9))
            elif c == 6:
                p.nop()
            else:
                p.ldpr(rng.choice(pool), rng.choice((2, 3, 4, 5)))
        if not nm:
            p.movea(rng.choice(pool), 2, opc)
        p.getpsw(12); p.halt(); A(p)
    # A10: the first R3 pair as GA2 encodes them, re-encoded by the generator (the bytes are asserted below)
    p = P("a10_pair"); p.tags = ["A10"]
    p.mov(1, 0x11111111)
    pc0 = p.cur
    for addr, rd, quoted in GA2_PAIR:
        p.movea(addr, rd, opc)
    if opc == MOVEA_B:
        got = [" ".join("%02X" % p.mem[pc0 + 7 * k + j] for j in range(7)) for k in range(2)]
        assert got == [q for _, _, q in GA2_PAIR], got
    p.halt(); A(p)
    # A5 EL variants (V2 only: the bench forces PSW[25:24]; MOVEA is not privileged, so EL != 0 changes nothing)
    for el in (1, 2, 3):
        p = P(f"a5_el{el}", el=el); p.tags = ["A5", "el"]
        p.mov(4, 0x7FFFFFFF); p.alu_imm(ADD, 4, 1); p.getpsw(9); p.movea(0x44332211, 6, opc); p.getpsw(10); p.halt(); A(p)
    # N6 byte-1 flips that are still legal MOVEAs (bits 4..0 change the destination register): positives
    if opc == MOVEA_B and cls == "pos":
        for bit in range(5):
            p = P(f"n6_b1_bit{bit}"); p.tags = ["N6"]
            p.mov(1, 0x11111111)
            p.movea(0x00210000 + bit, 31 ^ (1 << bit), opc, byte1=0x3F ^ (1 << bit)); p.halt(); A(p)
    # A9 / S0 harness (reset-native 0xFFFFFFF0, 24-bit mirror)
    if s0:
        def S(name, **kw):
            return new(pfx + name, cls, "s0", 0xFFFFFFF0, mc=mc, **kw)
        for nm, t in (("100506", 0x100506), ("hi", 0x1FFFC0), ("mirror", 0xF00123), ("upper_ff", 0xFF100506)):
            p = S(f"a9_jmp_{nm}"); p.tags = ["A9", "jmp"]
            p.jmp(t); p.decoy(0xFFFFFFF6)
            p.org(t); p.ldpr(0x00100000, 5); p.movea(0x00210000, 31, opc); p.movea(0x00208000, 25, opc); p.halt(); A(p)
        p = S("a9_ga2_boot"); p.tags = ["A9", "A10", "jmp"]                 # the S0 prefix shapes, re-encoded
        p.jmp(0x100506); p.decoy(0xFFFFFFF6)
        p.org(0x100506); p.ldpr(0x00100000, 5)
        p.movea(0x00210000, 31, opc); p.movea(0x00208000, 25, opc)
        p.movea(0x00E00000, 11, opc); p.mov(1, 8); p.halt(); A(p)
        p = S("a9_regions"); p.tags = ["A9", "A4", "jmp"]                    # ROM, own bytes, I/O, interrupt-ctl, work RAM, unmapped, mirror
        p.jmp(0x100506); p.decoy(0xFFFFFFF6)
        p.org(0x100506)
        p.movea(0x00100000, 3, opc); p.movea(p.cur, 4, opc); p.movea(0x00C00000, 5, opc); p.movea(0x00C0000A, 6, opc)
        p.movea(0x00D00000, 7, opc); p.movea(0x00200000, 8, opc); p.movea(0x00300000, 9, opc); p.movea(0x00F00000, 10, opc)
        p.movea(0xFFFFFFF0, 11, opc); p.halt(); A(p)
        p = S("a9_regions_sbr"); p.tags = ["A9", "A4", "jmp"]                  # data-region targets after LDPR (as in GA2)
        p.jmp(0x100506); p.decoy(0xFFFFFFF6)
        p.org(0x100506); p.ldpr(0x00100000, 5)
        p.movea(0x00C00000, 31, opc); p.movea(0x00800000, 25, opc); p.movea(0x00A00000, 24, opc); p.halt(); A(p)
        # reset-vector (JMP-free: they also run on the --smc-test S0 builds)
        p = S("a9_direct"); p.tags = ["A9"]
        p.movea(0x00210000, 31, opc); p.movea(0x00208000, 25, opc); p.halt(); A(p)
        p = S("a9_direct_regs"); p.tags = ["A9", "A4"]
        p.mov(1, 0x13131313); p.movea(0xFFFFFFF0, 2, opc); p.movea(0x00C00000, 3, opc); p.movea(0x00100000, 4, opc); p.halt(); A(p)
        for off in range(0xF9, 0x100):                # the 7 bytes cross 0xFFFFFFFF -> 0 at every offset
            p = S(f"a9_wrap_{off:02x}"); p.tags = ["A9", "wrap"]
            p.mov(1, 0x13131313)                      # FFFFFFF0..F6
            while p.cur < 0xFFFFFF00 + off:
                p.nop()
            assert p.cur == 0xFFFFFF00 + off, hex(p.cur)
            p.movea(0x00210000 + off, 2 + (off % 4), opc); p.halt(); A(p)
    return out


# ----------------------------------------------------------------------------- A7: withheld bytes
def l6_programs(opc=MOVEA_B, pfx="", cls="pos", mc="prod"):
    """A7 (R2 L6 style): fetches at or above LIM (a multiple of 8) are withheld; k = how many of the MOVEA's 7 bytes
    are below LIM (k = 2..6: operand bytes missing; k = 7: all present, only the NEXT instruction withheld).
    k = 6 is the plan's arrangement: the last address byte (start + 6) withheld, exactly four bytes (+2..+5) of the
    five-byte specifier available at the issue, so the availability checker catches a CONSUME / size under-availability."""
    out = []
    for LIM in (16, 24):
        for k in range(2, 8):
            p = new(f"{pfx}a7_L{LIM}_k{k}", cls, mc=mc)
            p.tags = ["A7"]
            p.mov(1, 0x16161616)
            while p.cur < LIM - k:
                p.nop()
            assert p.cur == LIM - k, (LIM, k, p.cur)
            s = p.cur
            p.movea(0xB6A59483 + k, 2 + (k % 4), opc)
            p.halt()
            p.extra = {"limit": LIM, "k": k, "s": s, "delay": 200}
            out.append(p)
    return out


# ----------------------------------------------------------------------------- negatives
def neg_prog(name, bs, kind, tags, opc, el=0, pre=True, smc_kind="opcode"):
    """A program whose last instruction (the one V2 must trap on in r3b with `kind`) is never executed"""
    p = new(name, "neg", el=el)
    p.tags = tags
    if pre:
        p.mov(1, 0x01010101)
    p.trap_instr(bs)
    p.extra = {"kind": kind, "opc": opc}
    return p


def neg_programs():
    out = []
    A = out.append
    abs_ = le32(0x00210000)
    # N1: opcodes 0x42 / 0x44 with /abs, Rn (any operands) -> opcode (not dispatched)
    for opc in (MOVEA_H, MOVEA_W):
        for nm, bs in (("abs", [opc, 0x3F, MODE_ABS] + abs_), ("abs_r5", [opc, 0x25, MODE_ABS] + abs_),
                       ("d0", [opc, 0x1F, MODE_ABS] + abs_), ("f2", [opc, 0xA0, MODE_ABS] + abs_ + [0x61]),
                       ("rn", [opc, 0x7F, 0x65])):
            A(neg_prog(f"n1_{opc:02x}_{nm}", bs, "opcode", ["N1"], opc))
    # N2: Format I d = 0 (the register is operand 1); Format II (byte 1 bit 7 set) with /abs and register direct -> mode
    for b1 in (0x1F, 0x05, 0x00, 0x45, 0x5F):          # d = 0 (bit 5 clear), m = 0 / 1, several registers
        A(neg_prog(f"n2_d0_{b1:02x}", [MOVEA_B, b1, MODE_ABS] + abs_, "mode", ["N2", "d0"], MOVEA_B))
    for nm, bs in (("f2_abs_rn", [MOVEA_B, 0xA0, MODE_ABS] + abs_ + [0x65]), ("f2_abs_rn31", [MOVEA_B, 0xA0, MODE_ABS] + abs_ + [0x7F]),
                   ("f2_abs_abs", [MOVEA_B, 0x80, MODE_ABS] + abs_ + [MODE_ABS] + le32(0x1234)),
                   ("f2_m1_rn", [MOVEA_B, 0xE0, 0x65, 0x65]), ("f2_80", [MOVEA_B, 0x80, MODE_ABS] + abs_ + [0x65]),
                   ("f2_bf", [MOVEA_B, 0xBF, MODE_ABS] + abs_ + [0x65]), ("f2_ff", [MOVEA_B, 0xFF, MODE_ABS] + abs_ + [0x65])):
        A(neg_prog(f"n2_{nm}", bs, "mode", ["N2", "f2"], MOVEA_B))
    # N3: the exhaustive sweep of the 512-entry (m, mode byte) table: every entry except (m = 0, 0xF3) as operand 1
    for m in (0, 1):
        for b in range(256):
            if m == 0 and b == MODE_ABS:
                continue
            A(neg_prog(f"n3_m{m}_{b:02x}", [MOVEA_B, 0x7F if m else 0x3F, b, 0x00, 0x00, 0x21, 0x00], "mode", ["N3", "sweep"], MOVEA_B))
    # N4: crafted streams (the bytes after the specifier look like an accepted /abs operand; HALT follows so a removed
    # guard visibly executes), plus the short forms
    for r in (2, 5, 9, 31):
        A(neg_prog(f"n4_rn{r}_crafted", [MOVEA_B, 0x7F, 0x60 | r] + abs_, "mode", ["N4", "src_regdir", "crafted"], MOVEA_B))
        A(neg_prog(f"n4_rn{r}_short", [MOVEA_B, 0x7F, 0x60 | r], "mode", ["N4", "src_regdir"], MOVEA_B))
    for q in (0, 5, 15):
        A(neg_prog(f"n4_q{q}_crafted", [MOVEA_B, 0x3F, 0xE0 | q] + abs_, "mode", ["N4", "src_quick", "src_imm", "crafted"], MOVEA_B))
        A(neg_prog(f"n4_q{q}_short", [MOVEA_B, 0x3F, 0xE0 | q], "mode", ["N4", "src_quick", "src_imm"], MOVEA_B))
    for nm, bs in (("imm32_crafted", [MOVEA_B, 0x3F, 0xF4] + abs_), ("imm_short", [MOVEA_B, 0x3F, 0xF4, 0x20]),
                   ("imm_r5", [MOVEA_B, 0x25, 0xF4] + abs_)):
        A(neg_prog(f"n4_{nm}", bs, "mode", ["N4", "src_imm"] + (["crafted"] if "crafted" in nm else []), MOVEA_B))
    # N5: JMP-free probes for the SMC builds ([Rn] is a register-indirect descriptor there, so movea.b [Rn], R must not execute)
    for r in (2, 7):
        A(neg_prog(f"n5_ind_{r}", [MOVEA_B, 0x25, 0x60 | r], "mode", ["N5"], MOVEA_B))
        A(neg_prog(f"n5_ind_{r}_h", [MOVEA_H, 0x25, 0x60 | r], "opcode", ["N5"], MOVEA_H))
    # N6: mutated encodings. opcode 0x40 ^ bit: every flip is an opcode V2 does not dispatch except 0x00 (HALT: halts)
    for bit in range(8):
        opc = MOVEA_B ^ (1 << bit)
        tail = [0x3F, MODE_ABS] + abs_
        if opc == 0x00:
            p = new("n6_opc_bit6_halt", "pos"); p.tags = ["N6"]
            p.mov(1, 0x01010101); p.halt_tail(tail); out.append(p)
        else:
            A(neg_prog(f"n6_opc_bit{bit}", [opc] + tail, "opcode", ["N6"], opc))
    # byte 1 flips: bits 7 / 6 / 5 -> mode (Format II, modm 1 on a /abs, d = 0); bits 4..0 are legal (positives above)
    for bit in (7, 6, 5):
        A(neg_prog(f"n6_b1_bit{bit}", [MOVEA_B, 0x3F ^ (1 << bit), MODE_ABS] + abs_ + ([0x65] if bit == 7 else []), "mode", ["N6"], MOVEA_B))
    for bit in range(8):                                                 # mode byte flips: every one is a `mode` trap
        A(neg_prog(f"n6_mode_bit{bit}", [MOVEA_B, 0x3F, MODE_ABS ^ (1 << bit), 0x00, 0x00, 0x21, 0x00], "mode", ["N6"], MOVEA_B))
    # the S0 prefix shape, third GA2 MOVEA (mode byte F2, PC disp32): the R3b reach-4 end
    q = bytes.fromhex(GA2_THIRD.replace(" ", ""))
    p = new("a10_s0_prefix_trap", "neg", "s0", 0xFFFFFFF0)
    p.tags = ["A10", "A9", "S0prefix", "jmp"]
    p.jmp(0x100506); p.decoy(0xFFFFFFF6)
    p.org(0x100506); p.ldpr(0x00100000, 5)
    for addr, rd, quoted in GA2_PAIR:
        p.movea(addr, rd)
    p.trap_instr(list(q))
    p.extra = {"kind": "mode", "opc": MOVEA_B}
    A(p)
    return out


# ----------------------------------------------------------------------------- probe programs (TEST-ONLY image)
def probe_programs():
    """Positives on the probe image (opcode 0xFE), the L6 arrangement on it, and the trap-in-the-middle probes."""
    out = []
    pos = [p for p in positives(PROBE_OPC, "pr_", "probe3", "probe", s0=False) if not p.name.startswith("pr_a5_el")]
    for p in pos:
        p.tags = p.tags + ["probe3"]
    out += pos
    for p in l6_programs(PROBE_OPC, "pr_", "probe3", "probe"):
        p.tags = p.tags + ["probe3"]
        out.append(p)
    # the trap-in-the-middle probes: 0xFC traps (mode) right after CONSUME 2, 0xFB after the operand lookup;
    # the instruction must report its start PC and leave no GPR / PSW / privileged / data effect (plan 9 ASSUMED #3)
    for opc, nm in ((PROBE_MID, "mid"), (PROBE_MID2, "mid2")):
        for rd in (1, 31):
            p = new(f"pr3_{nm}_r{rd}", "probe3", mc="probe")
            p.tags = ["probe3", "trapmid"]
            p.mov(rd, 0x5A5A1234)
            p.trap_instr([opc, 0x20 | rd, MODE_ABS] + le32(0x00210000))
            p.extra = {"kind": "mode", "opc": opc}
            out.append(p)
    return out


# ----------------------------------------------------------------------------- expectations
def v2_expect(p):
    """(std, ack, delay, smc, smc_ack, smc_delay): per-phase V2 expectations."""
    ph = ("r3a", "r3b")
    tr = lambda i, kind="opcode": {"kind": "trap", "trap": kind, "pc": p.recs[i][0], "n": i}
    if p.cls == "neg" or (p.cls == "probe3" and hasattr(p, "trap_pc")):
        kind = p.extra["kind"]
        t = lambda k: {"kind": "trap", "trap": k, "pc": p.trap_pc, "n": p.n_before}
        if p.cls == "probe3":                       # phase-independent (the probe image)
            return {x: t(kind) for x in ph}, None, None, None, None, None
        jmp_i = next((i for i, k in enumerate(p.kinds) if k == "jmp"), None)
        first_m = p.movea_idx[0] if p.movea_idx else None       # an earlier MOVEA (the S0 prefix) traps first in r3a
        r3a = tr(first_m) if first_m is not None else t("opcode")
        cands = [x for x in (jmp_i, first_m) if x is not None]
        smc1 = tr(min(cands)) if cands else t("opcode")          # SMC builds: no JMP, dtab[0x40/42/44] = trap
        return {"r3a": r3a, "r3b": t(kind)}, None, None, {"r3a": smc1, "r3b": smc1}, None, None
    halt = {"kind": "halt"}
    mi = p.movea_idx[0] if p.movea_idx else None
    jmp_i = next((i for i, k in enumerate(p.kinds) if k == "jmp"), None)
    if p.cls == "probe3":                           # the probe image supports 0xFE in both phases; JMP / LDPR as production
        std = {x: halt for x in ph}
        ack = delay = None
        if "limit" in p.extra:
            L = p.extra["limit"]
            wait = {"kind": "wait", "retired": mi, "state_n": mi, "limit": L}
            ack, delay = {x: wait for x in ph}, {x: {"kind": "halt", "limit": L} for x in ph}
        return std, ack, delay, None, None, None
    std = {"r3a": tr(mi) if mi is not None else halt, "r3b": halt}
    cands = [x for x in (jmp_i, mi) if x is not None]
    first = min(cands) if cands else None
    smc = {x: (tr(first) if first is not None else halt) for x in ph}
    ack = delay = sack = sdelay = None
    if "limit" in p.extra:
        L = p.extra["limit"]
        wait = {"kind": "wait", "retired": mi, "state_n": mi, "limit": L}
        ack = {"r3a": dict(tr(mi), limit=L), "r3b": wait}
        delay = {"r3a": dict(tr(mi), limit=L), "r3b": {"kind": "halt", "limit": L}}
        sack = {x: dict(tr(mi), limit=L) for x in ph}
        sdelay = {x: dict(tr(mi), limit=L) for x in ph}
    return std, ack, delay, smc, sack, sdelay


def rom_off(a):
    return R1.rom_off(a)


def emit(progs, out):
    man = {}
    for p in progs:
        info = {"name": p.name, "cls": p.cls, "where": p.where, "start_pc": p.start, "el": p.el, "mc": p.mc, "tags": p.tags}
        std, ack, delay, smc, sack, sdelay = v2_expect(p)
        info["v2"], info["v2_ack"], info["v2_delay"] = std, ack, delay
        info["v2_smc"], info["v2_smc_ack"], info["v2_smc_delay"] = smc, sack, sdelay
        trapcls = hasattr(p, "trap_pc")
        n = p.n_before if trapcls else len(p.recs)
        if trapcls:
            info["n_before"], info["trap_pc"] = p.n_before, p.trap_pc
        else:
            info["halt_pc"] = p.halt_pc
        info["exp_L"] = [r[2][2:] for r in p.recs[:n]]
        regs, psw = p.final_state(n)
        info["final_regs"], info["final_psw"] = regs, psw
        info["n_instrs"] = n
        info["exp_counters"] = {str(i): {"dr": 0, "dw": 0, "priv": len(p.ev.get(i, []))} for i in range(n)}
        info["exp_counters_up"] = {str(i): {"dr": 0, "dw": 0, "priv": len(p.ev_up.get(i, []))} for i in range(n)}
        info["exp_events"] = {str(i): [list(e) for e in p.ev.get(i, [])] for i in range(n)}
        info["exp_events_up"] = {str(i): [list(e) for e in p.ev_up.get(i, [])] for i in range(n)}
        info["exp_gevents"] = {str(i): [list(e) for e in p.gev.get(i, [])] for i in range(n)}
        info["exp_psw_upd"] = {str(i): p.psw_upd.get(i, 0) for i in range(n)}
        info["final_priv"] = {str(k): v for k, v in sorted(p.priv.items())}
        info["ldpr_idx"] = [i for i in p.ldpr_idx if i < n]
        info["wr_idx"] = [i for i in p.ev if i < n]
        info["movea_idx"] = [i for i in p.movea_idx if i < n]
        info["opcs"] = {str(i): o for i, o in p.opcs.items() if i < n}
        info["refs"] = ["ext", "up"] if (p.cls == "pos" and not p.el and p.name != "n6_opc_bit6_halt") else []
        info["extra"] = {k: v for k, v in p.extra.items() if k not in ("kind", "opc")}
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
        info["bytes"] = {f"{a:08x}": b for a, b in sorted(p.mem.items())} if p.cls == "neg" else None
        man[p.name] = info
    return man


def all_programs():
    return positives() + l6_programs() + neg_programs() + probe_programs()


def digest(man):
    return hashlib.sha256(json.dumps(man, sort_keys=True).encode()).hexdigest()


def main():
    argv = sys.argv[1:]
    out = Path(argv[0])
    out.mkdir(parents=True, exist_ok=True)
    progs = all_programs()
    names = [p.name for p in progs]
    assert len(names) == len(set(names)), "duplicate program names"
    man = emit(progs, out)
    (out / "r3_manifest.json").write_text(json.dumps(man, indent=0))
    cnt = {}
    for p in progs:
        k = f"{p.cls}/{p.where}"
        cnt[k] = cnt.get(k, 0) + 1
    print("r3: " + ", ".join(f"{k} {v}" for k, v in sorted(cnt.items())) + f" (total {len(progs)}), manifest sha256 {digest(man)[:16]}")
    if "--freeze" in argv or "--verify-frozen" in argv:
        path = Path(argv[argv.index("--freeze" if "--freeze" in argv else "--verify-frozen") + 1])
        rec = {"manifest_sha256": digest(man), "programs": len(man),
               "classes": cnt,
               "generator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               "a1_addresses": [f"{a:08x}" for a in A1_ADDRS],
               "a10_pair": [{"addr": f"{a:08x}", "rd": r, "bytes": q} for a, r, q in GA2_PAIR],
               "note": "expectations frozen before any MOVEA microcode exists (dtab[0x40] = trap in the builder); "
                       "computed from the encoding only (m1_r3.py imports no reference or RTL model)"}
        if "--freeze" in argv:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import m1_m11_microcode as B
            _, sym, dtab, _ = B.build()
            rec["builder_sha256"] = hashlib.sha256(Path(B.__file__).read_bytes()).hexdigest()
            rec["dtab_0x40_0x42_0x44_is_trap"] = all(dtab[o] == sym["trap"] for o in (0x40, 0x42, 0x44))
            assert rec["dtab_0x40_0x42_0x44_is_trap"], "a MOVEA dispatch entry already exists: freezing is meaningless"
            path.write_text(json.dumps(rec, indent=1) + "\n")
            print(f"frozen -> {path}")
        else:
            old = json.loads(path.read_text())
            ok = old["manifest_sha256"] == rec["manifest_sha256"] and old["programs"] == rec["programs"]
            print("frozen expectations " + ("MATCH" if ok else "MISMATCH") + f" ({old['manifest_sha256'][:16]} vs {rec['manifest_sha256'][:16]})")
            sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
