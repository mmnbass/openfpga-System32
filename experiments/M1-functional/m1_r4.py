#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""R4a: test programs for `movea.b disp32[PC], Rn` (docs/r4-movea-pcrel-plan.md section 6).

TEST INFRASTRUCTURE ONLY. Written while the builder is the R3b one (sha256 4ac7fa34..., mtab[0x0F2] = trap_mode): today V2 traps
every PC-relative MOVEA with kind `mode` (mtab[0x0F2] is trap_mode). The V2 expectation is a PHASE parameter (r4a = today,
r4b = after the R4b microcode) stored per program in the manifest, as in R1a / R2a / R3a.

Encoding (plan section 1):  MOVEA.B disp32[PC], Rn  =  40 <0x20|n> F2 d0 d1 d2 d3   (7 bytes)
    byte 1 = {0, m = 0, d = 1, reg = n} (Format I of the manual; V2 "F2" with D = 1), mode byte F2 = disp32[PC],
    value = (address of the INSTRUCTION'S FIRST BYTE + the signed 32-bit displacement) mod 2^32 -- the 32-bit effective ADDRESS,
    no extension, no byte mask; flags unchanged; no data access; next PC = start + 7.

INDEPENDENT EXPECTATIONS. As m1_r3.py: every expectation is computed HERE from the encoding (the sum is
`(start + signed disp) mod 2^32`, decoded from the emitted bytes), never from a reference run or an RTL model. This module imports no
reference and no V2 model; it reuses only the program-path classes of m1_r1 / m1_r2 / m1_r3 (placement, retire records, event model).

Program classes (manifest "cls"; "tags" name the catcher groups of m1_r4_catchers / m1_r4b_mutants):
    pos   B1-B11 (+ GA2 shapes, sequence controls C7i / C7j, operand-2 coverage sibling for CMP): "low" = M1 benches, "s0" = S0 harness;
          refs V60X (--ext) and upstream where no EL is forced
    neg   C1-C10, C7a-C7j (V2 only), operand-2 coverage siblings (kind `immdest`), the 512-entry sweep minus (m=0, F3) and (m=0, F2)
    sup   the supersession replacements (plan 7.1): the R3 programs whose `mode` expectation R4b flips, in their R4b form

Usage: m1_r4.py <out-dir>                                  writes <name>.{bin,hex} (low), <name>.rom.hex + <name>.segs (s0), r4_manifest.json
       m1_r4.py <out-dir> --freeze <results.json>          also writes the frozen-expectation record (builder must be the pre-R4 one)
       m1_r4.py <out-dir> --verify-frozen <results.json>   regenerates and compares the manifest digest
"""
import hashlib
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m1_r1 as R1  # noqa: E402
import m1_r3 as R3  # noqa: E402
from m1_m11 import MOVW, NOP, HALT  # noqa: E402

M = 0xFFFFFFFF
ADD, SUB, CMP, AND = R1.ADD, R1.SUB, R1.CMP, R1.AND
le32 = R1.le32
JMP_OP, JMP_ABS = R1.JMP_OP, R1.JMP_MODE
MOVEA_B, MOVEA_H, MOVEA_W = 0x40, 0x42, 0x44
MODE_ABS, MODE_PC32, MODE_PC16, MODE_PC8 = 0xF3, 0xF2, 0xF1, 0xF0
GETPSW, LDPR = 0xF7, 0x12
ALU_NAME = R3.ALU_NAME
PHASES = ("r4a", "r4b")
SMC_OPAQUE = (0x40, 0x42, 0x44, JMP_OP, JMP_OP + 1)       # opcodes that are not dispatched in the --smc-test builds

# the quoted GA2 shapes (plan section 1): the generator re-encodes them, the bytes are asserted equal
GA2_PCREL = [(0x0010051C, 0x70, 10, "40 2A F2 70 00 00 00", 0x0010058C), (0x0013346E, 0x24BA, 2, "40 22 F2 BA 24 00 00", 0x00135928)]
B1_DISPS = [0, 1, M, 0x70, 0x7F, 0x80, 0xFF, 0x100, (-0x80) & M, (-0x81) & M, 0x7FFF, 0x8000, (-0x8000) & M, (-0x8001) & M,
            0x7FFFFFFF, 0x80000000, 0xDEADBEEF, 0xAAAAAAAA, 0x55555555, 0x00FF0000, 0xFF000000, 0x12345678, (-2) & M, 0x2000]
PRE = R3.PRE


def sx32(v):
    v &= M
    return v - (1 << 32) if v >> 31 else v


class P4(R3.MProg):
    """R3.MProg plus the PC-relative MOVEA (value = start + signed displacement decoded from the emitted bytes)"""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.pcrel_idx = []
        self.nr = None

    def pcrel(self, disp, rd, opc=MOVEA_B, byte1=None, kind="pcrel"):
        b1 = (0x20 | rd) if byte1 is None else byte1
        bs = [opc, b1, MODE_PC32] + le32(disp)
        pc, r0, p0 = self._emit(bs, kind)
        val = (pc + sx32(int.from_bytes(bytes(bs[3:7]), "little"))) & M       # start + signed disp32, decoded from the bytes
        dest = b1 & 31
        self.regs[dest] = val
        self._record(pc, opc, r0, p0, kind)
        i = len(self.recs) - 1
        self.gev[i] = [(dest, val)]
        self.opcs[i] = opc
        self._after(kind)
        self.movea_idx.append(i)
        self.pcrel_idx.append(i)
        return i

    def mov_quick(self, r, q):
        """mov.w #quick, Rr (Format I d = 1, quick descriptor E0|q): the value is q (4 bits)"""
        pc, r0, p0 = self._emit([MOVW, 0x20 | r, 0xE0 | (q & 15)], "setup")
        self.regs[r] = q & 15
        self._record(pc, MOVW, r0, p0, "setup")
        self.gev[len(self.recs) - 1] = [(r, q & 15)]
        self._after("setup")

    def f1_cmp_reg_quick(self, rs, q):
        """cmp.w Rs, #q (Format II / V2 F1: op1 bank 1 register direct, op2 bank 0 quick): flags of (q - Rs); no register written"""
        pc, r0, p0 = self._emit([CMP, 0x80 | 0x40, 0x60 | rs, 0xE0 | (q & 15)], "setup")
        res, z, s, ov, cy = R1.alu(CMP, q & 15, self.regs[rs], self.flags[3])
        self.flags = [z, s, ov, cy]
        self._record(pc, CMP, r0, p0, "setup")
        self._after("setup")
        self.psw_upd[len(self.recs) - 1] = 1

    def pad(self, addr, reg, sentinel):
        self.put(addr, [MOVW, 0x20 | reg, 0xF4] + le32(sentinel) + [HALT])


def new(name, cls="pos", where="low", start=0, **kw):
    p = P4(name, cls, where, start, **kw)
    p.tags = []
    return p


def S(name, cls="pos", **kw):
    return new(name, cls, "s0", 0xFFFFFFF0, **kw)


# ----------------------------------------------------------------------------- positives (B1-B11)
def positives():
    out = []
    A = out.append
    rng = random.Random(0xB4)
    # B1: displacement edges at a fixed start (the instruction sits at 14 after two 7-byte setup instructions)
    for i, d in enumerate(B1_DISPS):
        p = new(f"b1_v{i:02d}"); p.tags = ["B1"]
        p.mov(1, 0x11111111); p.mov(5, 0x13572468)
        p.pcrel(d, 5); p.halt(); A(p)
    # B2: start-PC dependence: the same displacement at starts 0..23 (alignments 0..7 mod 8 and the next lines); a second pcrel after it
    for k in range(24):
        p = new(f"b2_s{k:02d}"); p.tags = ["B2", "B9"]
        for _ in range(k):
            p.nop()
        p.pcrel(0x1234 + k, 2 + (k % 4))
        p.pcrel((-0x40) & M, 2 + ((k + 1) % 4))
        p.halt(); A(p)
    for st in (0x100, 0x1F0, 0x7F3):
        p = new(f"b2_at{st:x}"); p.tags = ["B2", "jmp"]
        p.mov(1, 0x11111111)
        here = p.cur; p.jmp(st); p.decoy(here + 6); p.org(st)
        p.pcrel(0x1000, 3); p.pcrel((-0x100) & M, 4); p.halt(); A(p)
    # B3: negative displacement and 32-bit wrap (low): start 0x10, disp -0x20 -> 0xFFFFFFF0; sums crossing 0x80000000
    for nm, st, d in (("neg", 0x10, (-0x20) & M), ("wrap_up", 0x10, 0xFFFFFFF0), ("cross", 0x10, 0x7FFFFFF8), ("cross2", 0x10, 0x7FFFFFF0),
                      ("minint", 0x10, 0x80000000), ("minint_plus", 0x20, 0x80000010)):
        p = new(f"b3_{nm}"); p.tags = ["B3"]
        while p.cur < st:
            p.nop()
        assert p.cur == st
        p.pcrel(d, 6); p.halt(); A(p)
    # B4: all 32 destinations, each pre-loaded with a distinct value; exactly one register changes
    for rd in range(32):
        p = new(f"b4_r{rd:02d}"); p.tags = ["B4"]
        for r in range(32):
            p.mov(r, PRE(r))
        p.pcrel(0x0C3D2E1F ^ (rd * 0x01030507), rd); p.halt(); A(p)
    # B5: destination previously 0xFFFFFFFF / 0 / equal to the result (unchanged value) / result ^ 1 / a pattern
    for nm in ("ffff", "zero", "same", "near", "pat"):
        p = new(f"b5_{nm}"); p.tags = ["B5"]
        D = 0x2000
        st = 7                                              # after one 7-byte mov
        res = (st + D) & M
        prev = {"ffff": M, "zero": 0, "same": res, "near": res ^ 1, "pat": 0xA55AC33C}[nm]
        p.mov(7, prev)
        assert p.cur == st
        p.pcrel(D, 7); p.halt(); A(p)
    for nm, prev in (("same_twice", None), ("diff_twice", 0)):
        p = new(f"b5_{nm}"); p.tags = ["B5", "B6"]
        p.mov(3, 0)
        st = p.cur
        p.pcrel(0x2000, 3); p.pcrel(0x2000 - 7, 3)           # the second lands on the same result: an unchanged value, still a write event
        p.halt(); A(p)
    # B6: sequences / same-register / neighbours
    p = new("b6_two_same"); p.tags = ["B6"]
    p.pcrel(0x100, 3); p.pcrel(0x200, 3); p.pcrel(0x300, 3); p.halt(); A(p)
    p = new("b6_pc_then_abs"); p.tags = ["B6", "B7"]
    p.pcrel(0x100, 3); p.movea(0x00210000, 3); p.halt(); A(p)
    p = new("b6_abs_then_pc"); p.tags = ["B6"]
    p.movea(0x00210000, 3); p.pcrel(0x100, 3); p.halt(); A(p)
    p = new("b6_abs_pc_abs"); p.tags = ["B6"]
    p.movea(0x00210000, 3); p.pcrel(0x100, 4); p.movea(0x00208000, 5); p.pcrel((-0x10) & M, 3); p.halt(); A(p)
    for op in (ADD, SUB, CMP, AND):
        p = new(f"b6_{ALU_NAME[op]}_use"); p.tags = ["B6"]
        p.mov(4, 0x00000010); p.mov(2, 0xFFFFFFF0)
        p.pcrel(0x1234, 3)
        p.alu_rr(op, 4, 3); p.alu_rr(op, 2, 3); p.getpsw(9); p.halt(); A(p)
    p = new("b6_read_then_write"); p.tags = ["B6"]
    p.mov(4, 0x11111111); p.mov(5, 0x22222222); p.alu_rr(ADD, 4, 5); p.pcrel(0x100, 5); p.alu_rr(ADD, 4, 5); p.halt(); A(p)
    p = new("b6_ldpr_neighbours"); p.tags = ["B6"]
    p.ldpr(0x00100000, 5); p.pcrel(0x100, 31); p.movea(0x00208000, 25); p.ldpr(0x0020FE00, 2); p.pcrel(0x200, 25); p.halt(); A(p)
    p = new("b6_jmp_neighbours"); p.tags = ["B6", "jmp"]
    p.mov(1, 0x11111111); p.pcrel(0x1000, 3)
    here = p.cur; p.jmp(0x80); p.decoy(here + 6)
    p.org(0x80); p.pcrel(0x1000, 4); p.movea(0x00210000, 31); p.halt(); A(p)
    p = new("b6_r3_pair"); p.tags = ["B6"]
    p.movea(0x00210000, 31); p.movea(0x00208000, 25); p.pcrel(0x70, 10); p.movea(0x00E00000, 11); p.mov_quick(1, 8); p.halt(); A(p)
    # B7: the contents at the effective address (and at the three wrong-base addresses and at the raw displacement) must not be read
    for nm, D in (("pat", 0x2000), ("zero", 0), ("back", (-0x7) & M), ("own_field", 3), ("head", 2), ("next", 7)):
        p = new(f"b7_{nm}"); p.tags = ["B7"]
        p.mov(1, 0x11111111)
        st = p.cur
        eff = (st + sx32(D)) & M
        cand = {(eff + off + k) & M for off in (0, 2, 3, 7) for k in range(4)} | {((D & M) + k) & M for k in range(4)}
        for a in sorted(cand):
            if a < 0x10000 and a not in p.mem and not (st <= a < st + 8):
                p.mem[a] = 0xA5 ^ (a & 0x3F)
        p.pcrel(D, 6); p.halt(); A(p)
    for nm, tgt in (("unmapped", 0x00F00000), ("io", 0x00C00000), ("io_a", 0x00C0000A), ("beyond", 0x7FFF0000), ("last_word", 0x0001FFFC), ("zero_page", 0)):
        p = new(f"b7_{nm}"); p.tags = ["B7"]
        p.mov(1, 0x11111111)
        st = p.cur
        p.pcrel((tgt - st) & M, 4); p.halt(); A(p)
    # B8: flags set by ADD, then the PC-relative MOVEA, then GETPSW: PSW unchanged; EL 1..3 (V2 only)
    for nm, (b, a) in {"z": (0, 0), "s": (0, 0x80000000), "ov": (0x7FFFFFFF, 1), "cy": (0xFFFFFFFF, 2), "zcy": (0xFFFFFFFF, 1),
                       "sov": (0x7FFFFFFF, 0x7FFFFFFF), "none": (1, 1)}.items():
        p = new(f"b8_flags_{nm}"); p.tags = ["B8"]
        p.mov(4, b); p.alu_imm(ADD, 4, a)
        p.getpsw(9); p.pcrel(0x1234, 6); p.getpsw(10); p.halt(); A(p)
    for el in (1, 2, 3):
        p = new(f"b8_el{el}", el=el); p.tags = ["B8", "el"]
        p.mov(4, 0x7FFFFFFF); p.alu_imm(ADD, 4, 1); p.getpsw(9); p.pcrel(0x1234, 6); p.getpsw(10); p.halt(); A(p)
    # legal byte-1 flips (bits 4..0 change the destination)
    for bit in range(5):
        p = new(f"c9_b1_bit{bit}"); p.tags = ["C9"]
        p.mov(1, 0x11111111)
        p.pcrel(0x70 + bit, 10 ^ (1 << bit), byte1=0x2A ^ (1 << bit)); p.halt(); A(p)
    # B10: GA2 shapes re-encoded (asserted), S0 harness (reset-native, 24-bit mirror)
    for (st, d, rd, quoted, target) in GA2_PCREL:
        p = S(f"b10_ga2_{st:06x}"); p.tags = ["B10", "jmp"]
        p.jmp(st); p.decoy(0xFFFFFFF6)
        p.org(st)
        assert p.cur == st
        p.pcrel(d, rd)
        got = " ".join("%02X" % p.mem[st + j] for j in range(7))
        assert got == quoted, got
        assert p.regs[rd] == target, hex(p.regs[rd])
        p.halt(); A(p)
    p = S("b10_ga2_prefix", "neg"); p.tags = ["B10", "jmp", "S0prefix"]            # the whole S0 prefix, ending on the shape R4 does not support
    p.jmp(0x100506); p.decoy(0xFFFFFFF6)
    p.org(0x100506); p.ldpr(0x00100000, 5)
    for addr, rd, quoted in R3.GA2_PAIR:
        p.movea(addr, rd)
    assert p.cur == 0x10051C
    p.pcrel(0x70, 10)
    assert p.cur == 0x100523
    p.movea(0x00E00000, 11)
    assert p.cur == 0x10052A
    p.mov_quick(1, 8)
    assert p.cur == 0x10052D
    p.trap_instr([0x09, 0xE0, 0x8A, 0x8B])                                    # mov.b [R10+], [R11+]: opcode 0x09 is not dispatched (R5)
    p.extra = {"kind": "opcode", "opc": 0x09}
    A(p)
    for nm, t in (("100506", 0x100506), ("hi", 0x1FFFC0), ("mirror", 0xF00123), ("upper_ff", 0xFF100506)):
        p = S(f"b2_s0_jmp_{nm}"); p.tags = ["B2", "jmp"]
        p.jmp(t); p.decoy(0xFFFFFFF6)
        p.org(t); p.pcrel(0x70, 10); p.pcrel((-0x20) & M, 11); p.pcrel(0x7FFFFFFF, 12); p.halt(); A(p)
    p = S("b3_s0_reset"); p.tags = ["B3"]                                       # start 0xFFFFFFF0 + 0x20 wraps to 0x10
    p.pcrel(0x20, 2); p.pcrel((-0x20) & M, 3); p.pcrel(0x7FFFFFFF, 4); p.pcrel(0x80000000, 5); p.halt(); A(p)
    p = S("b3_s0_regs"); p.tags = ["B3", "B7"]
    p.mov(1, 0x13131313); p.pcrel((0x00C00000 - 0xFFFFFFF7) & M, 2); p.pcrel(0x00100000, 3); p.halt(); A(p)
    for off in range(0xF9, 0x100):                                              # the 7 bytes cross 0xFFFFFFFF -> 0 at every offset
        p = S(f"b3_s0_wrap_{off:02x}"); p.tags = ["B3", "wrap"]
        p.mov(1, 0x13131313)
        while p.cur < 0xFFFFFF00 + off:
            p.nop()
        assert p.cur == 0xFFFFFF00 + off, hex(p.cur)
        p.pcrel(0x70 + off, 2 + (off % 4)); p.halt(); A(p)
    p = S("b7_s0_regions"); p.tags = ["B7", "jmp"]                               # ROM, own bytes, I/O, interrupt-ctl, work RAM, unmapped, mirror
    p.jmp(0x100506); p.decoy(0xFFFFFFF6)
    p.org(0x100506)
    for k, tgt in enumerate((0x00100000, None, 0x00C00000, 0x00C0000A, 0x00D00000, 0x00200000, 0x00300000, 0x00F00000, 0xFFFFFFF0)):
        here = p.cur
        p.pcrel(((tgt if tgt is not None else here) - here) & M, 3 + k)
    p.halt(); A(p)
    p = S("b8_s0_flags"); p.tags = ["B8"]
    p.mov(4, 0x7FFFFFFF); p.alu_imm(ADD, 4, 1); p.getpsw(9); p.pcrel(0x1234, 6); p.getpsw(10); p.halt(); A(p)
    # random mixes (seeded): pcrel / abs MOVEA with mov / ALU / NOP / GETPSW / LDPR neighbours
    pool = [0, M, 0x80000000, 0x7FFFFFFF, 0x2000, 0x100, (-0x100) & M] + [rng.getrandbits(32) for _ in range(6)]
    for s in range(30):
        p = new(f"b6_rand{s:02d}"); p.tags = ["B6"]
        for r in range(1, 7):
            p.mov(r, rng.getrandbits(32))
        nm = 0
        for _ in range(rng.randrange(6, 14)):
            c = rng.randrange(9)
            if c < 3:
                p.pcrel(rng.choice(pool), rng.randrange(1, 9) if rng.random() < 0.7 else rng.choice((0, 15, 30, 31))); nm += 1
            elif c == 3:
                p.movea(rng.choice(pool), rng.randrange(1, 9)); nm += 1
            elif c == 4:
                p.mov(rng.randrange(1, 9), rng.choice(pool))
            elif c == 5:
                p.alu_imm(rng.choice([ADD, SUB, AND, CMP]), rng.randrange(1, 9), rng.getrandbits(32))
            elif c == 6:
                p.alu_rr(rng.choice([ADD, SUB, AND, CMP]), rng.randrange(1, 9), rng.randrange(1, 9))
            elif c == 7:
                p.nop()
            else:
                p.ldpr(rng.choice(pool), rng.choice((2, 3, 4, 5)))
        if not nm:
            p.pcrel(rng.choice(pool), 2)
        p.getpsw(12); p.halt(); A(p)
    # C7i: BR8 with a following F2 executes as before (the disp8 is the byte F2 = -14)
    p = new("c7i_br8_f2"); p.tags = ["C7i", "jmp"]
    p.mov(1, 0x11111111)
    here = p.cur; p.jmp(0x60); p.decoy(here + 6)
    p.org(0x60); p.br8(0x60 - 14)                                              # byte pair 6A F2 (F2 = -14)
    p.org(0x60 - 14); p.mov(3, 0x33333333); p.halt(); A(p)
    # C7j: stale-descriptor sequences (the tag lives in ea_desc until the next mode dispatch): successful parts retire normally
    p = new("c7j_pc_then_jmp"); p.tags = ["C7j", "jmp"]
    p.pcrel(0x1000, 3); here = p.cur; p.jmp(0x80); p.decoy(here + 6); p.org(0x80); p.movea(0x00208000, 25); p.halt(); A(p)
    p = new("c7j_jmp_then_pc"); p.tags = ["C7j", "jmp"]
    here = p.cur; p.jmp(0x91); p.decoy(here + 6); p.org(0x91); p.pcrel(0x1000, 3); p.halt(); A(p)
    p = new("c7j_pc_then_abs_then_pc"); p.tags = ["C7j"]
    p.pcrel(0x1000, 3); p.movea(0x00210000, 4); p.pcrel(0x1000, 5); p.movea(0x00208000, 6); p.halt(); A(p)
    p = new("c7j_pc_getpsw_ldpr"); p.tags = ["C7j"]
    p.pcrel(0x1000, 3); p.getpsw(9); p.ldpr(0x00100000, 5); p.pcrel(0x1000, 4); p.alu_imm(ADD, 4, 5); p.mov(8, 0x1234); p.halt(); A(p)
    p = new("c7j_jmp_pc_jmp"); p.tags = ["C7j", "jmp"]
    here = p.cur; p.jmp(0x91); p.decoy(here + 6); p.org(0x91)
    p.pcrel(0x1000, 3); here = p.cur; p.jmp(0xC0); p.decoy(here + 6); p.org(0xC0); p.pcrel(0x1000, 4); p.halt(); A(p)
    # C7e sibling (positive): cmp.w R5, #q reaches the operand-2 lookup with a valid first operand and accepts #q (no register written, PSW updated)
    for q in (0, 5, 15):
        p = new(f"c7e_cov_cmp_q{q}"); p.tags = ["C7e", "cov"]
        p.mov(5, 0x00000005); p.f1_cmp_reg_quick(5, q); p.getpsw(9); p.halt(); A(p)
    return out


# ----------------------------------------------------------------------------- A7-style withheld bytes (B9)
def l6_programs():
    """fetches at or above LIM (a multiple of 8) are withheld; k = how many of the PC-relative MOVEA's 7 bytes are below LIM"""
    out = []
    for LIM in (16, 24):
        for k in range(2, 8):
            p = new(f"b9_L{LIM}_k{k}"); p.tags = ["B9"]
            p.mov(1, 0x16161616)
            while p.cur < LIM - k:
                p.nop()
            assert p.cur == LIM - k, (LIM, k, p.cur)
            s = p.cur
            p.pcrel(0x0B6A5 + k, 2 + (k % 4))
            p.halt()
            p.extra = {"limit": LIM, "k": k, "s": s, "delay": 200}
            out.append(p)
    return out


# ----------------------------------------------------------------------------- negatives (C1-C10, C7a-C7j)
def neg_prog(name, bs, kind, tags, opc=None, el=0, pre=True, prefix=None, smc_kind=None, nr=None, where="low"):
    """a program whose last instruction (the one V2 must trap on in r4b with `kind`) is never executed"""
    p = new(name, "neg", where, 0 if where == "low" else 0xFFFFFFF0, el=el)
    p.tags = tags
    if prefix is not None:
        prefix(p)
    elif pre:
        p.mov(1, 0x01010101)
    p.trap_instr(bs)
    first = bs[0]
    opcode_in_smc = first in SMC_OPAQUE
    p.extra = {"kind": kind, "opc": first if opc is None else opc,
               "smc_kind": smc_kind or ("opcode" if opcode_in_smc else kind)}
    if nr is not None:
        p.nr = nr
        p.extra["nr"] = nr
    return p


def with_pads(p, bs, disp_width, D):
    """landing pads with distinct sentinels at the RAW displacement, at start + disp and at next-PC + disp (when distinct and free; data pattern
    included: the pad code itself); returns the nr record for the R4-NR checker"""
    st = p.trap_pc
    sd = D if D < (1 << (8 * disp_width - 1)) else D - (1 << (8 * disp_width))
    raw = D & M
    eff = (st + sd) & M
    nxt = (st + len(bs) + sd) & M
    pads = {}
    for tag, addr, reg, sent in (("raw", raw, 20, 0xA0A00001), ("eff", eff, 21, 0xA0A00002), ("next", nxt, 22, 0xA0A00003)):
        if any(abs(addr - a) < 12 for a in pads.values()):
            continue
        if addr < 0x10000 and not any((addr + j) in p.mem for j in range(10)):
            p.pad(addr, reg, sent)
            pads[tag] = addr
    return {"pads": pads, "sentinels": {20: 0xA0A00001, 21: 0xA0A00002, 22: 0xA0A00003}, "candidates": [raw, eff, nxt], "trap_pc": st}


def build_c7(name, bs, kind, tags, D, width, el=0, prefix=None, where="low", smc_kind=None, cov_of=None):
    p = neg_prog(name, bs, kind, tags, el=el, prefix=prefix, where=where, smc_kind=smc_kind)
    p.nr = with_pads(p, bs, width, D)
    p.extra["nr"] = p.nr
    if cov_of:
        p.extra["cov_of"] = cov_of
    return p


def pcrel_bytes(width, D):
    mode = {4: MODE_PC32, 2: MODE_PC16, 1: MODE_PC8}[width]
    return [mode] + list(int(D & ((1 << (8 * width)) - 1)).to_bytes(width, "little"))


def neg_programs():
    out = []
    A = out.append
    f2 = lambda d: [MODE_PC32] + le32(d)
    # C1: F0 (disp8) and F1 (disp16), d = 1, with crafted streams (the bytes look like a disp32 instruction, so a mis-sized descriptor would execute)
    for nm, b1 in (("r31", 0x3F), ("r5", 0x25), ("r10", 0x2A)):
        A(neg_prog(f"c1_f0_{nm}", [MOVEA_B, b1, MODE_PC8, 0x70, 0, 0, 0], "mode", ["C1"]))
        A(neg_prog(f"c1_f1_{nm}", [MOVEA_B, b1, MODE_PC16, 0x70, 0, 0, 0], "mode", ["C1"]))
    A(neg_prog("c1_f0_short", [MOVEA_B, 0x3F, MODE_PC8, 0x70], "mode", ["C1"]))
    A(neg_prog("c1_f1_short", [MOVEA_B, 0x3F, MODE_PC16, 0x70, 0x00], "mode", ["C1"]))
    A(neg_prog("c1_f0_neg", [MOVEA_B, 0x3F, MODE_PC8, 0xF0, 0xFF, 0xFF, 0xFF], "mode", ["C1"]))
    # C2: the exhaustive sweep of the 512-entry (m, mode byte) table: every entry except (m = 0, F3) and (m = 0, F2)
    for m in (0, 1):
        for b in range(256):
            if m == 0 and b in (MODE_ABS, MODE_PC32):
                continue
            A(neg_prog(f"c2_m{m}_{b:02x}", [MOVEA_B, 0x7F if m else 0x3F, b, 0x00, 0x00, 0x21, 0x00], "mode", ["C2", "sweep"]))
    # C3: Format II with F2: the real shape (movea.b 10055B[PC], [-SP]) and register-destination variants
    for nm, bs in (("real", [MOVEA_B, 0xA0, MODE_PC32, 0x0A, 0, 0, 0]), ("real_rn", [MOVEA_B, 0xA0, MODE_PC32, 0x0A, 0, 0, 0, 0x65]),
                   ("m1", [MOVEA_B, 0xE0, MODE_PC32, 0x0A, 0, 0, 0, 0x65]), ("80", [MOVEA_B, 0x80, MODE_PC32, 0x0A, 0, 0, 0, 0x65]),
                   ("bf", [MOVEA_B, 0xBF, MODE_PC32, 0x0A, 0, 0, 0, 0x65]), ("ff", [MOVEA_B, 0xFF, MODE_PC32, 0x0A, 0, 0, 0, 0x65]),
                   ("pc_pc", [MOVEA_B, 0x80, MODE_PC32, 0x0A, 0, 0, 0, MODE_PC32, 0x0A, 0, 0, 0])):
        A(neg_prog(f"c3_{nm}", bs, "mode", ["C3", "f2"]))
    # C4: Format I d = 0 with F2 (the register is operand 1)
    for b1 in (0x1F, 0x05, 0x00, 0x45, 0x5F):
        A(neg_prog(f"c4_d0_{b1:02x}", [MOVEA_B, b1, MODE_PC32, 0x70, 0, 0, 0], "mode", ["C4", "d0"]))
    # C5: F2 with m = 1 (mtab[0x1F2])
    for b1 in (0x7F, 0x6A, 0x40):
        A(neg_prog(f"c5_m1_{b1:02x}", [MOVEA_B, b1, MODE_PC32, 0x70, 0, 0, 0], "mode", ["C5"]))
    # C6: opcodes 0x42, 0x44 with F2
    for opc in (MOVEA_H, MOVEA_W):
        for nm, bs in (("f2", [opc, 0x3F, MODE_PC32, 0x70, 0, 0, 0]), ("f2_r5", [opc, 0x25, MODE_PC32, 0x70, 0, 0, 0]),
                       ("d0", [opc, 0x1F, MODE_PC32, 0x70, 0, 0, 0]), ("fmt2", [opc, 0xA0, MODE_PC32, 0x0A, 0, 0, 0, 0x65])):
            A(neg_prog(f"c6_{opc:02x}_{nm}", bs, "opcode", ["C6", "N1"], opc))
    # C9: mutated encodings of the quoted instruction 40 2A F2 70 00 00 00 (the positives with a legal byte-1 flip are above)
    quoted = [MOVEA_B, 0x2A, MODE_PC32, 0x70, 0, 0, 0]
    for bit in range(8):
        opc = MOVEA_B ^ (1 << bit)
        if opc == 0x00:
            p = new("c9_opc_bit6_halt", "pos"); p.tags = ["C9"]
            p.mov(1, 0x01010101); p.halt_tail(quoted[1:]); A(p)
        else:
            A(neg_prog(f"c9_opc_bit{bit}", [opc] + quoted[1:], "opcode", ["C9"], opc))
    for bit in (7, 6, 5):
        A(neg_prog(f"c9_b1_bit{bit}", [MOVEA_B, 0x2A ^ (1 << bit), MODE_PC32, 0x70, 0, 0, 0] + ([0x65] if bit == 7 else []), "mode", ["C9"]))
    for bit in range(8):
        mb = MODE_PC32 ^ (1 << bit)
        if mb == MODE_ABS:                                  # F3 = /abs: executes as R3 (a positive: the address is the field, 0x70)
            p = new("c9_mode_bit0_abs", "pos"); p.tags = ["C9"]
            p.mov(1, 0x01010101); p.movea(0x70, 10, byte1=0x2A); p.halt(); A(p)
        else:
            A(neg_prog(f"c9_mode_bit{bit}", [MOVEA_B, 0x2A, mb, 0x70, 0, 0, 0], "mode", ["C9"]))
    # C7a: jmp: D6 F2 / F0 / F1 (mode: the guard, and today's trap_mode), D7 (opcode)
    for nm, w, D in (("f2", 4, 0x300), ("f1", 2, 0x300), ("f0", 1, 0x70)):
        A(build_c7(f"c7a_jmp_{nm}", [JMP_OP] + pcrel_bytes(w, D), "mode", ["C7a", "C7"], D, w))
    A(_c7a_s0())
    A(build_c7("c7b_d7_f2", [JMP_OP + 1] + f2(0x300), "opcode", ["C7b", "C7"], 0x300, 4))
    # C7c / C7d / C7e: mov.w and the ALU forms (source and destination), Format I and II, with operand-2 coverage siblings
    for opn, op in (("mov", MOVW), ("add", ADD), ("sub", SUB), ("and", AND), ("cmp", CMP)):
        tg = ["C7c" if op == MOVW else ("C7e" if op == CMP else "C7d"), "C7"]
        D = 0x300
        shapes = {
            "src_d1": [op, 0x21] + f2(D),                      # Format I d = 1, source F2
            "src_f1_m0": [op, 0xA0] + f2(D),                   # Format II, op1 bank 0 F2
            "src_f1_m1": [op, 0xE0] + f2(D),                   # Format II, op1 bank 1 F2 (mtab[0x1F2])
            "dst_d0": [op, 0x01] + f2(D),                      # Format I d = 0: F2 is the destination
            "dst_f1": [op, 0xC0, 0x65] + f2(D),                # Format II: supported bank-0... register-direct source (bank 1), destination F2
        }
        for sn, bs in shapes.items():
            A(build_c7(f"{tg[0].lower()}_{opn}_{sn}", bs, "mode", tg + [opn], D, 4))
        # operand-2 coverage: the same first operand with a #q destination reaches the destination lookup: immdest for the writing forms
        if op != CMP:
            A(build_c7(f"cov_{opn}_dst_f1_q", [op, 0xC0, 0x65, 0xE3], "immdest", tg + [opn, "cov"], D, 4, cov_of=f"{tg[0].lower()}_{opn}_dst_f1"))
    # C7f: getpsw
    for nm, bs, kind in (("f7_f2", [GETPSW] + f2(0x300), "mode"), ("f7_f1", [GETPSW] + pcrel_bytes(2, 0x300), "mode"),
                         ("f7_f0", [GETPSW] + pcrel_bytes(1, 0x70), "mode"), ("f6_f2", [GETPSW - 1] + f2(0x300), "opcode")):
        A(build_c7(f"c7f_getpsw_{nm}", bs, kind, ["C7f", "C7"], 0x300 if "f0" not in nm else 0x70, 4 if "f2" in nm else (2 if "f1" in nm else 1)))
    # C7g: ldpr (privileged, EL 0): F2 as the source operand, as the register-ID operand, Format I
    imm = [0xF4] + le32(0x00100000)
    for nm, bs in (("src_f2", [LDPR, 0x80] + f2(0x300)), ("regid_f2", [LDPR, 0x80] + imm + f2(0x300)), ("src_m1", [LDPR, 0xC0] + f2(0x300)),
                   ("fmt1", [LDPR, 0x20] + f2(0x300)), ("regid_m1", [LDPR, 0xA0] + imm + f2(0x300)),
                   ("regid_f1w", [LDPR, 0x80] + imm + pcrel_bytes(2, 0x300))):
        A(build_c7(f"c7g_ldpr_{nm}", bs, "mode", ["C7g", "C7"], 0x300, 4))
    # C7h: movea other forms (as C3 / C4 / C6) with landing pads
    for nm, bs, kind in (("fmt2", [MOVEA_B, 0xA0] + f2(0x300) + [0x65], "mode"), ("d0", [MOVEA_B, 0x1F] + f2(0x300), "mode"),
                         ("h", [MOVEA_H, 0x3F] + f2(0x300), "opcode"), ("w", [MOVEA_W, 0x3F] + f2(0x300), "opcode")):
        A(build_c7(f"c7h_movea_{nm}", bs, kind, ["C7h", "C7"], 0x300, 4))
    # C7i: branches / DBcc / calls with an operand: not dispatched, opcode (the table pin asserts dtab = trap except the listed opcodes)
    for opc in (0xC6, 0xC7, 0xC4, 0xCA, 0x48, 0x49, 0x4A, 0x4C, 0x59, 0x7A):
        A(build_c7(f"c7i_op{opc:02x}_f2", [opc, 0xA1] + f2(0x300), "opcode", ["C7i", "C7"], 0x300, 4))
    # C7j: a failing instruction after a successful PC-relative MOVEA: the stale descriptor must not matter (r4b: mode at the later instruction)
    pre = lambda p: (p.mov(1, 0x01010101), p.pcrel(0x1000, 3))
    A(build_c7("c7j_pc_then_mov_abs_src", [MOVW, 0x21, MODE_ABS] + le32(0x1234), "mode", ["C7j", "C7"], 0x300, 4, prefix=pre))
    A(build_c7("c7j_pc_then_add_abs_src", [ADD, 0x21, MODE_ABS] + le32(0x1234), "mode", ["C7j", "C7"], 0x300, 4, prefix=pre))
    A(build_c7("c7j_pc_then_jmp_f2", [JMP_OP] + f2(0x300), "mode", ["C7j", "C7"], 0x300, 4, prefix=pre))
    A(build_c7("c7j_pc_then_getpsw_f2", [GETPSW] + f2(0x300), "mode", ["C7j", "C7"], 0x300, 4, prefix=pre))
    A(build_c7("c7j_pc_then_movea_f1", [MOVEA_B, 0x3F, MODE_PC16, 0x70, 0, 0, 0], "mode", ["C7j", "C7"], 0x300, 2, prefix=pre))
    A(build_c7("c7j_pc_then_movea_h", [MOVEA_H, 0x3F] + f2(0x300), "opcode", ["C7j", "C7"], 0x300, 4, prefix=pre))
    return out


def _c7a_s0():
    """JMP D6 F2 at a high PC on the S0 harness (reset-native): the pads sit at the raw displacement and at start + disp"""
    p = S("c7a_jmp_f2_s0", "neg")
    p.tags = ["C7a", "C7", "jmp"]
    p.jmp(0x100506); p.decoy(0xFFFFFFF6)
    p.org(0x100506); p.ldpr(0x00100000, 5)
    bs = [JMP_OP, MODE_PC32] + le32(0x300)
    p.trap_instr(bs)
    p.extra = {"kind": "mode", "opc": JMP_OP, "smc_kind": "opcode"}
    st = p.trap_pc
    raw, eff = 0x300, (st + 0x300) & M
    p.pad(raw, 20, 0xA0A00001)
    p.pad(eff, 21, 0xA0A00002)
    p.nr = {"pads": {"raw": raw, "eff": eff}, "sentinels": {20: 0xA0A00001, 21: 0xA0A00002}, "candidates": [raw, eff], "trap_pc": st}
    p.extra["nr"] = p.nr
    return p


# ----------------------------------------------------------------------------- supersession replacements (plan 7.1)
SUPERSEDED = [
    # (R3 program, R3b expectation, R4b expectation, the R4 program that carries the new expectation, plan section)
    ("n3_m0_f2", "TRAP mode at the instruction (R3 sweep entry m = 0, mode byte F2)", "retires: R31 <- start + 0x210000, then HALT", "sup_n3_m0_f2", "7.1"),
    ("n6_mode_bit0", "TRAP mode (mode byte F3 ^ 1 = F2)", "retires: R31 <- start + 0x210000, then HALT", "sup_n6_mode_bit0", "7.1"),
    ("a10_s0_prefix_trap", "TRAP mode ipc=0010051c (the reach-4 end)", "retires R10 <- 0x0010058c; the next byte is HALT", "sup_a10_s0_prefix", "7.1"),
]


def sup_programs():
    out = []
    p = new("sup_n3_m0_f2", "pos"); p.tags = ["sup"]
    p.mov(1, 0x01010101); p.pcrel(0x00210000, 31); p.halt(); out.append(p)
    p = new("sup_n6_mode_bit0", "pos"); p.tags = ["sup"]
    p.mov(1, 0x01010101); p.pcrel(0x00210000, 31); p.halt(); out.append(p)
    p = S("sup_a10_s0_prefix", "pos"); p.tags = ["sup", "jmp"]
    p.jmp(0x100506); p.decoy(0xFFFFFFF6)
    p.org(0x100506); p.ldpr(0x00100000, 5)
    for addr, rd, quoted in R3.GA2_PAIR:
        p.movea(addr, rd)
    p.pcrel(0x70, 10); p.halt(); out.append(p)
    return out


# ----------------------------------------------------------------------------- expectations
def v2_expect(p):
    """(std, ack, delay, smc, smc_ack, smc_delay): per-phase V2 expectations"""
    tr = lambda i, kind="opcode": {"kind": "trap", "trap": kind, "pc": p.recs[i][0], "n": i}
    halt = {"kind": "halt"}
    jmp_i = next((i for i, k in enumerate(p.kinds) if k == "jmp"), None)
    first_pc = min(p.pcrel_idx) if p.pcrel_idx else None
    first_m = min(p.movea_idx) if p.movea_idx else None
    smc_c = [x for x in (jmp_i, first_m) if x is not None]
    if p.cls == "neg" or hasattr(p, "trap_pc"):
        kind = p.extra["kind"]
        t = lambda k: {"kind": "trap", "trap": k, "pc": p.trap_pc, "n": p.n_before}
        r4a = tr(first_pc, "mode") if first_pc is not None else t(kind)
        smc1 = tr(min(smc_c)) if smc_c else t(p.extra.get("smc_kind", kind))
        return {"r4a": r4a, "r4b": t(kind)}, None, None, {"r4a": smc1, "r4b": smc1}, None, None
    std = {"r4a": tr(first_pc, "mode") if first_pc is not None else halt, "r4b": halt}
    smc = {x: (tr(min(smc_c)) if smc_c else halt) for x in PHASES}
    ack = delay = sack = sdelay = None
    if "limit" in p.extra:
        L, k = p.extra["limit"], p.extra["k"]
        mi = first_pc
        wait = {"kind": "wait", "retired": mi, "state_n": mi, "limit": L}
        # r4a: the trap `mode` needs the mode byte (start + 2): withheld for k = 2 (then V2 waits), present for k >= 3
        a_r4a = dict(tr(mi, "mode"), limit=L) if k >= 3 else wait
        ack = {"r4a": a_r4a, "r4b": wait}
        delay = {"r4a": dict(tr(mi, "mode"), limit=L), "r4b": {"kind": "halt", "limit": L}}
        # smc: dtab[0x40] = trap: the opcode trap needs opcode + format byte only (k >= 2)
        sack = {x: dict(tr(mi), limit=L) for x in PHASES}
        sdelay = {x: dict(tr(mi), limit=L) for x in PHASES}
    return std, ack, delay, smc, sack, sdelay


def emit(progs, out):
    R3.v2_expect = v2_expect
    man = R3.emit(progs, out)
    for p in progs:
        info = man[p.name]
        info["pcrel_idx"] = [i for i in p.pcrel_idx if i < info["n_instrs"]]
        info["refs"] = ["ext", "up"] if (p.cls == "pos" and not p.el and p.name != "c9_opc_bit6_halt") else []
        if p.nr:
            info["nr"] = {"pads": p.nr["pads"], "sentinels": {str(k): v for k, v in p.nr["sentinels"].items()}, "candidates": p.nr["candidates"],
                          "trap_pc": p.nr["trap_pc"],
                          "redirects": sum(1 for k in p.kinds[:info["n_instrs"]] if k in ("jmp", "br8"))}
        info["extra"] = {k: v for k, v in info["extra"].items() if k != "nr"}
    return man


def all_programs():
    return positives() + l6_programs() + neg_programs() + sup_programs()


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
    (out / "r4_manifest.json").write_text(json.dumps(man, indent=0))
    cnt = {}
    for p in progs:
        k = f"{p.cls}/{p.where}"
        cnt[k] = cnt.get(k, 0) + 1
    print("r4: " + ", ".join(f"{k} {v}" for k, v in sorted(cnt.items())) + f" (total {len(progs)}), manifest sha256 {digest(man)[:16]}")
    if "--freeze" in argv or "--verify-frozen" in argv:
        path = Path(argv[argv.index("--freeze" if "--freeze" in argv else "--verify-frozen") + 1])
        rec = {"manifest_sha256": digest(man), "programs": len(man), "classes": cnt,
               "generator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               "b1_displacements": [f"{d:08x}" for d in B1_DISPS],
               "ga2_pcrel": [{"start": f"{s:08x}", "disp": f"{d:08x}", "rd": r, "bytes": q, "target": f"{t:08x}"} for s, d, r, q, t in GA2_PCREL],
               "supersession": [list(x) for x in SUPERSEDED],
               "note": "expectations frozen before any R4 microcode exists (mtab[0x0F2] = trap_mode in the builder, builder sha256 4ac7fa34...); "
                       "computed from the encoding only: (start + signed disp) mod 2^32 (m1_r4.py imports no reference or RTL model)"}
        if "--freeze" in argv:
            import m1_m11_microcode as B
            _, sym, dtab, mtab = B.build()
            rec["builder_sha256"] = hashlib.sha256(Path(B.__file__).read_bytes()).hexdigest()
            rec["mtab_0x0F2_is_trap_mode"] = (mtab[0x0F2] & 0xFFF) == sym["trap_mode"]
            assert rec["builder_sha256"].startswith("4ac7fa34"), "freezing needs the pre-R4 (R3b) builder"
            assert rec["mtab_0x0F2_is_trap_mode"], "mtab[0x0F2] is already a supported mode: freezing is meaningless"
            path.write_text(json.dumps(rec, indent=1) + "\n")
            print(f"frozen -> {path}")
        else:
            old = json.loads(path.read_text())
            ok = old["manifest_sha256"] == rec["manifest_sha256"] and old["programs"] == rec["programs"]
            print("frozen expectations " + ("MATCH" if ok else "MISMATCH") + f" ({old['manifest_sha256'][:16]} vs {rec['manifest_sha256'][:16]})")
            sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
