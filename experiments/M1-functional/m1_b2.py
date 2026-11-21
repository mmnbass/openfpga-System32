#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""B2 (CPU batch 2) directed vectors: a handful per new form, against V60X (m1_ref.py --ext) and the pinned upstream s32_v60, on the M1
benches at 4 fetch configurations (exploratory standard, as m1_b1.py: the references ARE the expectation).

New forms: generic MOV / MOVZ / NOT / ADD / SUB / CMP / AND / OR / XOR (b / h / w) with register, #quick, #imm, memory operands (/abs, d8 / d16 /
d32[Rn], [Rn], [Rn+], [-Rn]); SHL / ROT (count #imm, register value); INC / TEST (register); Bcc disp8 / disp16; BSR / RSR; MOVEA d[Rn], [Rn],
d8[Rn](Rx); MOVCU.H.  Run via run_m11.sh:  M11_GEN=.../m1_b2.py M11_REF_FLAGS=--ext M11_OUT=<new dir> M11_LABEL=B2 run_m11.sh 0 <configs>

  m1_b2.py <out-dir> [nrandom]      writes <name>.{bin,hex}, m11_list.txt, m11_smc_list.txt (empty)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from m1_image import write  # noqa: E402
from m1_m11 import P, MOVW  # noqa: E402

MOV = {0: 0x09, 1: 0x1B, 2: 0x2D}
ADD = {0: 0x80, 1: 0x82, 2: 0x84}
SUB = {0: 0xA8, 1: 0xAA, 2: 0xAC}
CMP = {0: 0xB8, 1: 0xBA, 2: 0xBC}
AND = {0: 0xA0, 1: 0xA2, 2: 0xA4}
OR = {0: 0x88, 1: 0x8A, 2: 0x8C}
XOR = {0: 0xB0, 1: 0xB2, 2: 0xB4}
NOT = {0: 0x38, 1: 0x3A, 2: 0x3C}
SHL = {0: 0xA9, 1: 0xAB, 2: 0xAD}
ROT = {0: 0x89, 1: 0x8B, 2: 0x8D}
INC = {0: 0xD9, 1: 0xDB, 2: 0xDD}
TEST = {0: 0xF1, 1: 0xF3, 2: 0xF5}
MOVZBW, MOVZHW, MOVEA, MOVCUH, BSR, RSR = 0x0D, 0x1D, 0x40, 0x5A, 0x48, 0xCA
GETPSW = 0xF7


def le32(v):
    return list((v & 0xFFFFFFFF).to_bytes(4, "little"))


def put(p, addr, bs):
    assert len(p.b) <= addr, (hex(len(p.b)), hex(addr))
    p.b += bytes(addr - len(p.b)) + bytes(bs)


def setr(p, r, v):
    p.f2d1(P.imm(v), r)


def imm(v, sz):                          # #imm of an operand of size sz (0 b, 1 h, 2 w)
    return (0, [0xF4] + list((v & ((1 << (8 << sz)) - 1)).to_bytes(1 << sz, "little")))


def mabs(a): return (0, [0xF3] + le32(a))
def d8(r, d): return (0, [r, d & 0xFF])
def d16(r, d): return (0, [0x20 | r, d & 0xFF, (d >> 8) & 0xFF])
def d32(r, d): return (0, [0x40 | r] + le32(d))
def ind(r): return (0, [0x60 | r])
def pinc(r): return (1, [0x80 | r])
def pdec(r): return (1, [0xA0 | r])


def getpsw(p, r):
    p.ins(GETPSW, 0x60 | r)


def directed():
    D = {}
    DATA = [0x11, 0x82, 0xF3, 0x74, 0x85, 0x96, 0xA7, 0x08, 0x19, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF, 0x00]

    def base(p, regs=(10,), at=0x200):
        for i, r in enumerate(regs):
            setr(p, r, at + 0x20 * i)
    # ---- MOV.b/h/w memory -> register, every mode ----
    for sz in (0, 1, 2):
        for nm, src, rs in (("ind", ind(10), 10), ("d8", d8(10, 4), 10), ("d8n", d8(10, -2), 10), ("d16", d16(10, 0x30), 10), ("d32", d32(10, 8), 10),
                            ("abs", mabs(0x206), 10)):
            p = P(); base(p); setr(p, 2, 0xCAFEBABE)
            p.f2d1(src, 2, MOV[sz]); p.halt(); put(p, 0x1FE, DATA * 6); D[f"b2_mov{sz}_ld_{nm}"] = p
    # ---- MOV register -> memory, every mode; MOV #imm / #quick -> memory ----
    for sz in (0, 1, 2):
        for nm, dst in (("ind", ind(10)), ("d8", d8(10, 6)), ("d16", d16(10, 0x44)), ("d32", d32(10, -4 & 0xFFFFFFFF)), ("abs", mabs(0x250))):
            p = P(); base(p); setr(p, 3, 0xA5C3E1F7); p.f2d0(3, dst, MOV[sz]); p.halt(); put(p, 0x1F0, [0x77] * 0x80); D[f"b2_mov{sz}_st_{nm}"] = p
        p = P(); base(p); p.f1(imm(0x8182838485, sz) if sz == 2 else imm(0x8182, sz), ind(10), MOV[sz]); p.halt(); put(p, 0x1F0, [0x55] * 0x40); D[f"b2_mov{sz}_imm_ind"] = p
        p = P(); base(p); p.f1(P.quick(9), d8(10, 3), MOV[sz]); p.halt(); put(p, 0x1F0, [0x55] * 0x40); D[f"b2_mov{sz}_quick_d8"] = p
    # ---- mem -> mem (F1), stepped modes ----
    for sz in (0, 1, 2):
        p = P(); base(p, (10, 11)); p.f1(pinc(10), pinc(11), MOV[sz]); p.f1(pinc(10), pinc(11), MOV[sz]); p.halt(); put(p, 0x1FE, DATA * 6); D[f"b2_mov{sz}_postinc2"] = p
        p = P(); base(p, (10, 11)); setr(p, 10, 0x210); p.f1(pdec(10), pdec(11), MOV[sz]); p.f1(pdec(10), pinc(11), MOV[sz]); p.halt(); put(p, 0x1FE, DATA * 6); D[f"b2_mov{sz}_predec"] = p
    p = P(); base(p, (10, 11)); p.f1(mabs(0x204), d8(11, 2), MOV[2]); p.halt(); put(p, 0x1FE, DATA * 6); D["b2_mov2_abs_to_d8"] = p
    p = P(); base(p, (10,)); setr(p, 25, 0x300); p.f1(d16(25, 0x10), ind(10), MOV[1]); p.halt(); put(p, 0x1FE, DATA * 12); D["b2_mov1_d16_to_ind"] = p
    # ---- MOVZ ----
    p = P(); setr(p, 2, 0xFFFFFFFF); p.f2d1(imm(0xA5, 0), 2, MOVZBW); p.halt(); D["b2_movzbw_imm"] = p
    p = P(); setr(p, 2, 0xFFFFFFFF); p.f2d1(imm(0xF234, 1), 2, MOVZHW); p.halt(); D["b2_movzhw_imm"] = p
    p = P(); base(p); setr(p, 2, 0xFFFFFFFF); p.f2d1(ind(10), 2, MOVZBW); p.halt(); put(p, 0x1FE, DATA * 6); D["b2_movzbw_mem"] = p
    p = P(); base(p); setr(p, 2, 0xFFFFFFFF); p.f2d1(d8(10, 1), 2, MOVZHW); p.halt(); put(p, 0x1FE, DATA * 6); D["b2_movzhw_mem"] = p
    p = P(); setr(p, 3, 0x12345678); setr(p, 2, 0xFFFFFFFF); p.f2d1(P.reg(3), 2, MOVZBW); p.halt(); D["b2_movzbw_reg"] = p
    p = P(); setr(p, 3, 0x12348765); setr(p, 2, 0x0); p.f2d1(P.reg(3), 2, MOVZHW); p.halt(); D["b2_movzhw_reg"] = p
    # ---- MOV register size merges ----
    for sz in (0, 1):
        p = P(); setr(p, 3, 0x99887766); setr(p, 4, 0x11223344); p.f2d1(P.reg(3), 4, MOV[sz]); p.halt(); D[f"b2_mov{sz}_reg_merge"] = p
        p = P(); setr(p, 4, 0x11223344); p.f2d1(imm(0x80FF, sz), 4, MOV[sz]); p.halt(); D[f"b2_mov{sz}_imm_merge"] = p
    # ---- ALU, register / immediate / memory, flags (final PSW) ----
    ops = {"add": ADD, "sub": SUB, "cmp": CMP, "and": AND, "or": OR, "xor": XOR}
    vals = {0: (0x7F, 0x01, 0x80, 0xFF), 1: (0x7FFF, 1, 0x8000, 0xFFFF), 2: (0x7FFFFFFF, 1, 0x80000000, 0xFFFFFFFF)}
    for on, ot in ops.items():
        for sz in (0, 1, 2):
            a, b, c, d = vals[sz]
            p = P(); setr(p, 1, 0xA1B2C3D4); setr(p, 2, 0x1F2E3D4C)                       # reg, reg (F2 D=1), upper bits garbage
            p.f2d1(P.reg(1), 2, ot[sz]); p.halt(); D[f"b2_{on}{sz}_rr"] = p
            p = P(); setr(p, 2, a); p.f2d1(imm(b, sz), 2, ot[sz]); p.halt(); D[f"b2_{on}{sz}_ov"] = p        # overflow / carry edge
            p = P(); setr(p, 2, c); p.f2d1(imm(c, sz), 2, ot[sz]); p.halt(); D[f"b2_{on}{sz}_eq"] = p        # zero result
            p = P(); setr(p, 2, d); p.f2d1(imm(b, sz), 2, ot[sz]); p.halt(); D[f"b2_{on}{sz}_wrap"] = p
            p = P(); base(p); setr(p, 2, 0x13572468); p.f2d1(d8(10, 2), 2, ot[sz]); p.halt(); put(p, 0x1FE, DATA * 6); D[f"b2_{on}{sz}_mem_src"] = p
            p = P(); base(p); setr(p, 3, 0xDEADBEEF); p.f2d0(3, ind(10), ot[sz]); p.halt(); put(p, 0x1FE, DATA * 6); D[f"b2_{on}{sz}_mem_dst"] = p
            p = P(); base(p, (10, 11)); p.f1(pinc(10), pdec(11), ot[sz]); p.halt(); put(p, 0x1FE, DATA * 6); D[f"b2_{on}{sz}_mem_mem"] = p
    p = P(); setr(p, 2, 0xFFFFFF00); p.f2d1(P.quick(5), 2, ADD[0]); p.halt(); D["b2_add0_quick"] = p
    # ---- NOT ----
    for sz in (0, 1, 2):
        p = P(); setr(p, 2, 0x12345678); p.f2d1(imm(0x55AA33CC, sz), 2, NOT[sz]); p.halt(); D[f"b2_not{sz}_imm"] = p
        p = P(); base(p); p.f2d1(ind(10), 2, NOT[sz]); p.halt(); put(p, 0x1FE, DATA * 6); D[f"b2_not{sz}_mem"] = p
        p = P(); base(p); p.f1(imm(0, sz), d8(10, 2), NOT[sz]); p.halt(); put(p, 0x1FE, DATA * 6); D[f"b2_not{sz}_zero_mem"] = p
    # ---- SHL / ROT (count #imm byte, register value) ----
    for sz in (0, 1, 2):
        w = 8 << sz
        for cnt in (1, 3, w - 1, w, w + 1, 0x100 - 1, 0x100 - 3, 0x100 - w, 0):
            p = P(); setr(p, 2, 0x8C5A96E3); p.f2d1(imm(cnt & 0xFF, 0), 2, SHL[sz]); p.halt(); D[f"b2_shl{sz}_{cnt}"] = p
        for cnt in (1, 5, w - 1, 0x100 - 1, 0x100 - 5, w + 3):
            p = P(); setr(p, 2, 0x8C5A96E3); p.f2d1(imm(cnt & 0xFF, 0), 2, ROT[sz]); p.halt(); D[f"b2_rot{sz}_{cnt}"] = p
    # ---- INC / TEST ----
    for sz in (0, 1, 2):
        for v in (0, (1 << (8 << sz)) - 1, 1 << ((8 << sz) - 1), ((1 << ((8 << sz) - 1)) - 1)):
            p = P(); setr(p, 4, 0xAB000000 | (v & 0xFFFFFF) if sz < 2 else v); p.ins(INC[sz], 0x60 | 4); p.halt(); D[f"b2_inc{sz}_{v:x}"] = p
            p = P(); setr(p, 4, 0xAB000000 | (v & 0xFFFFFF) if sz < 2 else v); p.ins(TEST[sz], 0x60 | 4); p.halt(); D[f"b2_test{sz}_{v:x}"] = p
    # ---- Bcc (disp8 / disp16), taken and not taken, per flag state ----
    for cc in range(16):
        if cc in (0x0A, 0x0B):
            continue
        for fl, (a, b) in (("eq", (5, 5)), ("lt", (3, 7)), ("gt", (9, 2)), ("ovf", (0x7FFFFFFF, 0xFFFFFFFF)), ("big", (0xFFFFFFF0, 2))):
            p = P(); setr(p, 1, a); setr(p, 2, b); p.f2d1(P.reg(2), 1, CMP[2]); st = len(p.b)
            p.ins(0x60 | cc, 0x05); p.f2d1(P.quick(5), 3); p.f2d1(P.quick(6), 4); p.halt()   # taken: skip the first mov (3 bytes)
            D[f"b2_bcc8_{cc:x}_{fl}"] = p
            p = P(); setr(p, 1, a); setr(p, 2, b); p.f2d1(P.reg(2), 1, CMP[2]); p.ins(0x70 | cc, 0x06, 0x00); p.f2d1(P.quick(5), 3); p.f2d1(P.quick(6), 4); p.halt()
            D[f"b2_bcc16_{cc:x}_{fl}"] = p
    p = P(); st = len(p.b); p.ins(0x7A, 0x07, 0x00); p.halt(); p.halt(); p.halt(); p.halt(); p.f2d1(P.quick(9), 3); p.halt(); D["b2_br16_fwd"] = p
    p = P(); setr(p, 1, 3); lp = len(p.b); p.f2d1(P.quick(1), 2, ADD[2]); p.f2d1(P.quick(1), 1, SUB[2]); p.ins(0x65, (lp - len(p.b)) & 0xFF); p.halt(); D["b2_bne_loop"] = p
    # ---- BSR / RSR ----
    def bsr_prog(n_calls):                                  # n_calls BSRs to a subroutine "mov #3,R3; rsr" placed after the final halt
        p = P(); setr(p, 31, 0x400)
        starts = []
        for _ in range(n_calls):
            starts.append(len(p.b)); p.ins(BSR, 0, 0)
        p.f2d1(P.quick(7), 4); p.halt()
        sub = len(p.b); p.f2d1(P.quick(3), 3); p.ins(RSR)
        for st in starts:
            d = sub - st
            p.b[st + 1] = d & 0xFF; p.b[st + 2] = (d >> 8) & 0xFF
        return p
    D["b2_bsr_rsr"] = bsr_prog(1)
    D["b2_bsr_twice"] = bsr_prog(2)
    # ---- MOVEA ----
    for nm, src in (("d8", d8(10, 0x20)), ("d8n", d8(10, -0x20)), ("d16", d16(10, 0x1234)), ("d32", d32(10, 0x12345678)), ("ind", ind(10))):
        p = P(); setr(p, 10, 0x1000); setr(p, 2, 0x5A5A5A5A); p.f2d1(src, 2, MOVEA); p.halt(); D[f"b2_movea_{nm}"] = p
    p = P(); setr(p, 10, 0x1000); setr(p, 0, 0x200); setr(p, 2, 0x5A5A5A5A); p.ins(MOVEA, 0x62, 0xC0, 0x0A, 0x20); p.halt(); D["b2_movea_idx_d8"] = p
    p = P(); setr(p, 10, 0x1000); setr(p, 7, 0xFFFFFFF0); setr(p, 2, 0x5A5A5A5A); p.ins(MOVEA, 0x62, 0xC7, 0x2A, 0x00, 0x10); p.halt(); D["b2_movea_idx_d16_neg"] = p
    p = P(); setr(p, 10, 0x1000); setr(p, 7, 5); setr(p, 2, 0x5A5A5A5A); p.ins(MOVEA, 0x62, 0xC7, 0x6A); p.halt(); D["b2_movea_idx_ind"] = p
    # ---- MOVCU.H ----
    def movcu(p, src, l1, dst, l2):
        (m1, s1), (m2, s2) = src, dst
        p.ins(MOVCUH, 0x88 | (m1 << 6) | (m2 << 5), *s1, l1, *s2, l2)
    for nm, l1, l2 in (("eq", 5, 5), ("lt", 3, 6), ("gt", 7, 2), ("one", 1, 1), ("zero1", 0, 4), ("zero2", 4, 0)):
        p = P(); setr(p, 10, 0x200); setr(p, 11, 0x300); movcu(p, ind(10), l1, ind(11), l2); p.halt(); put(p, 0x1FE, [0x31, 0x41, 0x59, 0x26, 0x53, 0x58, 0x97, 0x93, 0x23, 0x84, 0x62, 0x64] * 3); D[f"b2_movcu_{nm}"] = p
    p = P(); setr(p, 10, 0x1F0); setr(p, 11, 0x300); movcu(p, d8(10, 0x10), 4, d16(11, 0x20), 4); p.halt(); put(p, 0x1FE, [0x31, 0x41, 0x59, 0x26, 0x53, 0x58, 0x97, 0x93] * 3); D["b2_movcu_disp"] = p
    p = P(); setr(p, 10, 0x201); setr(p, 11, 0x301); movcu(p, ind(10), 3, ind(11), 3); p.halt(); put(p, 0x1FE, [0x31, 0x41, 0x59, 0x26, 0x53, 0x58, 0x97, 0x93] * 3); D["b2_movcu_odd"] = p
    p = P(); setr(p, 10, 0x200); movcu(p, ind(10), 4, mabs(0x300), 4); p.halt(); put(p, 0x1FE, [0x31, 0x41, 0x59, 0x26, 0x53, 0x58, 0x97, 0x93] * 3); D["b2_movcu_abs_dst"] = p
    # register lengths, a memory count operand, a memory TEST operand (were `mode` traps before the register-length / memory-count work)
    p = P(); setr(p, 10, 0x200); setr(p, 11, 0x300); setr(p, 9, 3); p.ins(MOVCUH, 0x88, *ind(10)[1], 0x89, *ind(11)[1], 0x89); p.halt()
    put(p, 0x1FE, [0x31, 0x41, 0x59, 0x26, 0x53, 0x58, 0x97, 0x93] * 3); D["b2_movcu_reglen"] = p
    p = P(); setr(p, 10, 0x200); setr(p, 9, 2); setr(p, 11, 0x300); p.ins(MOVCUH, 0x88, *ind(10)[1], 0x89, *ind(11)[1], 0x05); p.halt()
    put(p, 0x1FE, [0x31, 0x41, 0x59, 0x26, 0x53, 0x58, 0x97, 0x93] * 3); D["b2_movcu_reglen_mixed"] = p
    p = P(); setr(p, 10, 0x200); setr(p, 2, 0x8C5A96E3); p.f2d1(ind(10), 2, SHL[1]); p.halt(); put(p, 0x1FE, [0x01, 0x00, 0x03, 0x00] * 3); D["b2_shl1_mem_count"] = p
    for sz in (0, 1, 2):
        p = P(); setr(p, 10, 0x200); p.ins(TEST[sz] - 1, *ind(10)[1]); p.halt(); put(p, 0x1FE, DATA_T * 3); D[f"b2_test{sz}_mem"] = p
        p = P(); setr(p, 10, 0x200); p.ins(INC[sz] - 1, *d8(10, 2)[1]); p.halt(); put(p, 0x1FE, DATA_T * 3); D[f"b2_inc{sz}_mem"] = p
    # ---- B3: general PUSHM / POPM (masks: R0..R30 = bits 0..30, bit 31 = PSW; never R31) ----
    PUSHM, POPM = 0xEC, 0xE4
    def pm_prog(masks, restore=True, sp=0x500, psw_set=False, clobber=True):
        p = P()
        for r in range(31):
            setr(p, r, 0x01010101 * (r + 1) ^ 0x5A000000)
        setr(p, 31, sp)
        if psw_set:
            p.f1(imm(0x9, 2), imm(0xF, 2), 0x13)                  # UPDPSW.W: set CY and Z
        for m in masks:
            p.ins(PUSHM, 0xF4, *le32(m))
        if restore:
            if clobber:
                for r in range(31):
                    setr(p, r, 0)
            for m in reversed(masks):
                p.ins(POPM, 0xF4, *le32(m))
        p.halt()
        return p
    for nm, m in (("full", 0x1FFFFFFF), ("zero", 0), ("r0", 1), ("r28", 1 << 28), ("r30", 1 << 30), ("sparse", 0x15A5A5A5), ("odd", 0x2AAAAAAA), ("allregs", 0x7FFFFFFF),
                  ("two", 0x00000011), ("hi3", 0x70000000)):
        D[f"b3_pm_{nm}"] = pm_prog([m])
        D[f"b3_pm_{nm}_nopop"] = pm_prog([m], restore=False)
    D["b3_pm_psw"] = pm_prog([0x80000000], psw_set=True)
    D["b3_pm_psw_regs"] = pm_prog([0x80000123], psw_set=True)
    D["b3_pm_all32"] = pm_prog([0xFFFFFFFF], psw_set=True)
    D["b3_pm_nested"] = pm_prog([0x1FFFFFFF, 0x00000F0F, 0x80000001], psw_set=True)
    p = P(); setr(p, 31, 0x400); setr(p, 5, 0x77); p.ins(PUSHM + 1, 0x60 | 5); p.halt(); D["b3_pm_regmask"] = p
    p = P(); setr(p, 31, 0x400); setr(p, 0, 0x11); setr(p, 3, 0x33); p.ins(PUSHM, 0xE0 | 9); p.ins(POPM, 0xE0 | 9); p.halt(); D["b3_pm_quick"] = p
    # ---- B3: JSR with register-based and indirect memory operands (EA = target) ----
    JSRO = 0xE8
    def jsr_prog(spec, regs):
        p = P(); setr(p, 31, 0x400)
        for r, v in regs.items():
            setr(p, r, v)
        p.ins(JSRO, *spec); p.halt()
        put(p, 0x100, []); p.f2d1(P.quick(7), 4); p.halt()
        return p
    D["b3_jsr_ind"] = jsr_prog([0x60 | 10], {10: 0x100})
    D["b3_jsr_d8"] = jsr_prog([10, 0x10], {10: 0xF0})
    D["b3_jsr_d16"] = jsr_prog([0x20 | 10, 0x34, 0x12], {10: 0x100 - 0x1234 & 0xFFFFFFFF})
    D["b3_jsr_d32"] = jsr_prog([0x40 | 10, 0x00, 0x01, 0x00, 0x00], {10: 0})
    for nm, spec, rr in (("ig8", [0x80 | 10, 0x08], {10: 0x1F8}), ("ig16", [0xA0 | 10, 0x00, 0x01], {10: 0x100}), ("ig32", [0xC0 | 10, 0x10, 0x00, 0x00, 0x00], {10: 0x1F0})):
        p = jsr_prog(spec, rr); put(p, 0x200, le32(0x100)); D[f"b3_jsr_{nm}"] = p
    # ---- B3 (continued): DIV.W, DEC, TB, double displacement, MOVEA.H/.W F2 and indexed scale, JMP with register-based / indexed operands, TEST1 F2 ----
    DIVW = 0xA5
    for a, b in ((100, 7), (0xFFFFFF9C, 7), (100, 0xFFFFFFF9), (0xFFFFFF9C, 0xFFFFFFF9), (7, 100), (0, 5), (5, 0), (0x80000000, 0xFFFFFFFF), (0x80000000, 2),
                 (0x7FFFFFFF, 0xFFFFFFFF), (0x80000001, 0xFFFFFFFF), (1, 1), (0xFFFFFFFF, 0xFFFFFFFF), (0x80000000, 1)):
        p = P(); setr(p, 2, a); setr(p, 3, b); p.f2d1(P.reg(3), 2, DIVW); p.halt(); D[f"b3_div_{a:x}_{b:x}"] = p
    p = P(); setr(p, 2, 40); p.f2d1(P.quick(6), 2, DIVW); p.halt(); D["b3_div_quick"] = p
    p = P(); setr(p, 2, 1000); p.f2d1(imm(7, 2), 2, DIVW); p.halt(); D["b3_div_imm"] = p
    p = P(); setr(p, 10, 0x200); setr(p, 2, 1000); p.f2d1(ind(10), 2, DIVW); p.halt(); put(p, 0x200, [9, 0, 0, 0]); D["b3_div_mem"] = p
    for sz in (0, 1, 2):
        for v in (0, 1, (1 << (8 << sz)) - 1, 1 << ((8 << sz) - 1)):
            p = P(); setr(p, 4, 0xAB000000 | (v & 0xFFFFFF) if sz < 2 else v); p.ins((0xD1, 0xD3, 0xD5)[sz], 0x60 | 4); p.halt(); D[f"b3_dec{sz}_reg_{v:x}"] = p
            p = P(); setr(p, 10, 0x200); p.ins((0xD0, 0xD2, 0xD4)[sz], *d8(10, 2)[1]); p.halt(); put(p, 0x200, [0] * 2 + list((v & ((1 << (8 << sz)) - 1)).to_bytes(1 << sz, "little")) + [0x55] * 4); D[f"b3_dec{sz}_mem_{v:x}"] = p
    for rv, nm in ((0, "zero"), (1, "one"), (0xFFFFFFFF, "neg")):
        p = P(); setr(p, 3, rv); st = len(p.b); p.ins(0xC7, 0xA0 | 3, 0x07, 0x00); p.f2d1(P.quick(5), 4); p.halt(); D[f"b3_tb_{nm}"] = p
    for grp, nm in ((0x00, "dd8"), (0x20, "dd16"), (0x40, "dd32")):
        for sz in (0, 1, 2):
            dl = {0x00: [4, 3], 0x20: [4, 0, 3, 0], 0x40: [4, 0, 0, 0, 3, 0, 0, 0]}[grp]
            p = P(); setr(p, 10, 0x1FC); setr(p, 2, 0xCAFEBABE)
            p.ins(MOV[sz], 0x60 | 0x20 | 2 if False else 0x60 | 2, grp | 10, *dl)          # F2 D=1, modm 1 (byte1 bit 6): mov.<sz> dd[R10], R2
            p.halt(); put(p, 0x200, le32(0x300) + [0] * 12); put(p, 0x300, [0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88]); D[f"b3_{nm}_ld{sz}"] = p
    for sz, opc in enumerate((0x40, 0x42, 0x44)):
        p = P(); setr(p, 10, 0x1000); setr(p, 7, 5); setr(p, 2, 0x5A5A5A5A); p.ins(opc, 0x62, 0xC7, 0x2A, 0x00, 0x10); p.halt(); D[f"b3_movea{sz}_idx_scale"] = p
        p = P(); setr(p, 10, 0x1000); setr(p, 2, 0x5A5A5A5A); p.ins(opc, 0x22, 0x2A, 0x34, 0x12); p.halt(); D[f"b3_movea{sz}_d16"] = p
    for nm, spec, rr in (("ind", [0x6A | 0], {10: 0x100}), ("d8", [0x0A, 0x10], {10: 0xF0}), ("idx", [0xC3, 0x2A, 0x10, 0x00, 0x00, 0x00], {10: 0xE0, 3: 4})):
        p = P(); setr(p, 31, 0x400)
        for r_, v_ in rr.items():
            setr(p, r_, v_)
        p.ins(0xD7 if nm == "idx" else 0xD6, *([0xC0 | 3, 0x40 | 10, 0x10, 0x00, 0x00, 0x00] if nm == "idx" else spec)); p.halt()
        put(p, 0x100, []); p.f2d1(P.quick(7), 4); p.halt(); D[f"b3_jmp_{nm}"] = p
    for bit in (0, 7, 31):
        p = P(); setr(p, 2, 0x80000081); p.ins(0x87, 0x20 | 2, 0xE0 | bit if bit < 16 else 0xF4, *([] if bit < 16 else le32(bit))); p.halt(); D[f"b3_test1_d1_{bit}"] = p
        p = P(); setr(p, 2, bit); setr(p, 10, 0x200); p.ins(0x87, 0x02, *ind(10)[1]); p.halt(); put(p, 0x200, [0x81, 0, 0, 0x80]); D[f"b3_test1_d0_{bit}"] = p
    # ---- C1: byte-element MOVCU.B / MOVCD.B (0x58 sub-opcodes 8 / 9) ----
    for nm, sub, l1, l2 in (("u_eq", 0x88, 5, 5), ("u_lt", 0x88, 3, 6), ("u_gt", 0x88, 7, 2), ("u_zero", 0x88, 0, 4), ("d_eq", 0x89, 6, 6), ("d_one", 0x89, 1, 1), ("d_zero", 0x89, 0, 0)):
        p = P(); setr(p, 10, 0x201); setr(p, 11, 0x301); (m1, s1), (m2, s2) = ind(10), ind(11)
        p.ins(0x58, sub | (m1 << 6) | (m2 << 5), *s1, l1, *s2, l2); p.halt(); put(p, 0x1F0, [0x31, 0x41, 0x59, 0x26, 0x53, 0x58, 0x97, 0x93] * 12); D[f"c1_movcb_{nm}"] = p
    # ---- B3 forms: ROTC.W, MULU.W, RVBYT, TEST1 / SET1 / CLR1 / NOT1, XCHW, UPDPSW, MOVCD ----
    for cnt in (0, 1, 5, 31, 32, 33, 40, 0xFF, 0xFD, 0xE0, 0xDF):
        for cy in (0, 1):
            p = P(); setr(p, 2, 0x8000A5C3); setr(p, 3, 0xFFFFFFFF if cy else 0)
            if cy:
                p.f2d1(imm(0xFF, 0), 5, ADD[0]); setr(p, 5, 0x80); p.f2d1(imm(0x80, 0), 5, ADD[0])        # a byte add that carries: CY = 1
            p.f2d1(imm(cnt, 0), 2, 0x9D); p.halt(); D[f"b3_rotc_{cnt:x}_{cy}"] = p
    for a, b in ((3, 5), (0, 9), (0xFFFFFFFF, 2), (0x10000, 0x10000), (0x7FFFFFFF, 2), (1, 0xFFFFFFFF)):
        p = P(); setr(p, 2, a); setr(p, 3, b); p.f2d1(P.reg(3), 2, 0x95); p.halt(); D[f"b3_mulu_rr_{a:x}_{b:x}"] = p
        p = P(); setr(p, 2, a); setr(p, 3, b); p.f2d0(3, P.reg(2), 0x95); p.halt(); D[f"b3_mulu_d0_{a:x}_{b:x}"] = p
    p = P(); setr(p, 2, 0x12345678); p.f2d1(P.quick(7), 2, 0x95); p.halt(); D["b3_mulu_quick"] = p
    p = P(); setr(p, 2, 0x12345678); p.f2d1(imm(0x1000, 2), 2, 0x95); p.halt(); D["b3_mulu_imm"] = p
    p = P(); setr(p, 10, 0x200); setr(p, 2, 3); p.f2d1(ind(10), 2, 0x95); p.halt(); put(p, 0x200, [5, 0, 0, 0]); D["b3_mulu_mem"] = p
    for v in (0x11223344, 0x80000001, 0):
        p = P(); setr(p, 2, v); setr(p, 3, 0xCC); p.f2d1(P.reg(2), 3, 0x2C); p.halt(); D[f"b3_rvbyt_{v:x}"] = p
    p = P(); setr(p, 10, 0x200); p.f2d1(ind(10), 3, 0x2C); p.halt(); put(p, 0x200, [1, 2, 3, 4]); D["b3_rvbyt_mem"] = p
    for bit in (0, 7, 31, 32, 5):
        for opn, opc in (("test1", 0x87), ("set1", 0x97), ("clr1", 0xA7), ("not1", 0xB7)):
            p = P(); setr(p, 2, 0x80000081); p.f1(imm(bit, 2), P.reg(2), opc); p.halt(); D[f"b3_{opn}_reg_{bit}"] = p
            p = P(); setr(p, 10, 0x200); p.f1(imm(bit, 2), ind(10), opc); p.halt(); put(p, 0x200, [0x81, 0, 0, 0x80]); D[f"b3_{opn}_mem_{bit}"] = p
    p = P(); setr(p, 2, 0x1111); setr(p, 3, 0x2222); p.f2d0(2, P.reg(3), 0x45); p.halt(); D["b3_xchw_rr"] = p
    p = P(); setr(p, 10, 0x200); setr(p, 2, 0x1111); p.f2d0(2, ind(10), 0x45); p.halt(); put(p, 0x200, [0x78, 0x56, 0x34, 0x12]); D["b3_xchw_mem"] = p
    p = P(); setr(p, 10, 0x200); setr(p, 2, 0x1111); p.f2d1(d8(10, 4), 2, 0x45); p.halt(); put(p, 0x200, [0] * 4 + [0x78, 0x56, 0x34, 0x12]); D["b3_xchw_d1_mem"] = p
    for v, m in ((0x4, 0x4), (0x0, 0x4), (0xF, 0xF), (0x10000, 0xFFFF), (0xFFFFFFFF, 0x3), (0x0, 0xFFFFFFFF)):
        p = P(); p.f1(imm(v, 2), imm(m, 2), 0x13); getpsw(p, 2); p.halt(); D[f"b3_updpswW_{v:x}_{m:x}"] = p
        p = P(); p.f1(imm(v & 0xFFFF, 1), imm(m & 0xFFFF, 1), 0x4A); getpsw(p, 2); p.halt(); D[f"b3_updpswH_{v & 0xFFFF:x}_{m & 0xFFFF:x}"] = p
    for nm, l in (("eq", 5), ("one", 1), ("zero", 0), ("big", 40)):
        p = P(); setr(p, 10, 0x200); setr(p, 11, 0x300); (m1, s1), (m2, s2) = ind(10), ind(11)
        p.ins(MOVCUH, 0x89 | (m1 << 6) | (m2 << 5), *s1, l, *s2, l); p.halt(); put(p, 0x1F0, [0x31, 0x41, 0x59, 0x26, 0x53, 0x58, 0x97, 0x93] * 12); D[f"b3_movcd_{nm}"] = p
    return D


DATA_T = [0x00, 0x7F, 0xFF, 0xFF, 0x80, 0x00, 0x01, 0x00]


def unsupported():
    """(name, program, index of the unsupported instruction, kind): V2 traps there with the references' state before it, no side effect"""
    U = []

    def pre():
        p = P(); setr(p, 10, 0x200); setr(p, 11, 0x300); setr(p, 5, 0x500); setr(p, 31, 0x400)
        return p
    p = pre(); p.f2d1(pinc(10), 2, MOVEA); p.halt(); U.append(("b2_u_movea_postinc", p, 4, "mode"))
    p = pre(); p.f2d1(pdec(10), 2, MOVEA); p.halt(); U.append(("b2_u_movea_predec", p, 4, "mode"))
    p = pre(); p.ins(MOVEA, 0x62, 0xC0, 0xF3, *le32(0x100)); p.halt(); U.append(("b2_u_movea_idx_abs", p, 4, "mode"))
    p = pre(); p.f2d0(3, P.imm(5), MOV[2]); p.halt(); U.append(("b2_u_mov_imm_dst", p, 4, "immdest"))
    return U


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    lines = []
    for name, p in directed().items():
        write(bytes(p.b), str(out / name)); lines.append(f"agree {name}")
    for name, p, n, kind in unsupported():
        write(bytes(p.b), str(out / name))
        lines.append(f"unsupported {name} {n} {kind} {p.starts[n]:08x}")
    (out / "m11_list.txt").write_text("\n".join(lines) + "\n")
    (out / "m11_smc_list.txt").write_text("")
    print(f"b2: {len(lines)} programs")


if __name__ == "__main__":
    main()
