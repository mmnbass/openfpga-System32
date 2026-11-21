#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""R1a: test programs for `jmp /abs` (docs/r1-jmp-abs-plan.md sections 3, 10, 11).

TEST INFRASTRUCTURE ONLY. No JMP microcode exists anywhere; today V2 traps
every JMP with kind `opcode`. The V2 expectation is a PHASE parameter (r1a =
today, r1b = after the R1b microcode) stored per program in the manifest, so
R1b flips one switch instead of rewriting tests.

Encoding (plan section 1): JMP /abs = D6 F3 a0 a1 a2 a3, target a3a2a1a0
(little endian). Setup instructions use the M1.1/M1.2 subset only.

INDEPENDENT EXPECTATIONS. Every program is built as an explicit execution
path of steps; the expected lock-step records (start PC, opcode, registers
changed, PSW) are computed HERE from the encoding and a three-line ALU model
(ADD / SUB / AND flags), never from a reference run:
    JMP    next PC = the 32-bit little-endian word at instruction start + 2;
           registers, PSW unchanged; zero data reads, zero data writes, zero
           privileged-register writes
    HALT   final PC = the HALT's address
A reference or RTL run is then CHECKED against these (m1_r1_check.py).

Program classes (manifest "cls"):
    pos      positive: J1-J13 (low-address: M1 benches; "where": "s0": the S0
             harness, reset start 0xFFFFFFF0, synthetic ROM, no GA2 data)
    neg      N1 (D6 with a non-/abs mode), N2 (D7)
    guard    G1: /abs through each of the 21 operand() call sites of
             m1_m11_microcode.py; must trap `mode` and never execute
    immdest  immediate destination at every writing destination (section 11
             row 1); must trap `immdest`
    nonvac   counter non-vacuity programs: data accesses (MOV.W [Rn] store and
             load); V2 runs the store under the TEST-ONLY --smc-test microcode

Usage: m1_r1.py <out-dir>      writes <name>.{bin,hex} (low), <name>.rom.hex +
                               <name>.segs (s0) and r1_manifest.json
"""
import json
import random
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from m1_image import write  # noqa: E402
from m1_m11 import P, MOVW, NOP, HALT  # noqa: E402

M = 0xFFFFFFFF
ADD, SUB, CMP, AND = 0x84, 0xAC, 0xBC, 0xA4
GETPSW = 0xF7
PSW0 = 0x10000000
JMP_OP, JMP_MODE = 0xD6, 0xF3
OPNAMES = {"add": ADD, "sub": SUB, "cmp": CMP, "and": AND}


def le32(v):
    return list(struct.pack("<I", v & M))


def alu(op, b, a, cy_in):
    """(result, z, s, ov, cy) for b OP a, as the plan's reference semantics (M1.2)."""
    if op == ADD:
        r = (b + a) & M
        return r, int(r == 0), r >> 31, ((b ^ r) & (a ^ r)) >> 31 & 1, int(b + a > M)
    if op in (SUB, CMP):
        r = (b - a) & M
        return r, int(r == 0), r >> 31, ((b ^ a) & (b ^ r)) >> 31 & 1, int(b < a)
    if op == AND:
        r = b & a
        return r, int(r == 0), r >> 31, 0, cy_in
    raise ValueError(op)


class Prog:
    """A program as an explicit execution path with independently computed effects."""

    def __init__(self, name, cls, where="low", start=0):
        self.name, self.cls, self.where = name, cls, where
        self.start = start
        self.mem = {}                 # virtual address -> byte (placed code)
        self.cur = start
        self.steps = []               # (pc, bytes, ('none'|'reg'|..., payload), kind)
        self.regs = [0] * 32
        self.flags = [0, 0, 0, 0]     # z s ov cy
        self.recs = []
        self.first_unsup = None       # index of the first JMP/negative (V2 r1a: opcode)
        self.extra = {}
        self.nxt = start              # expected pc of the next executed instruction

    # ---- placement ----
    def org(self, a):
        self.cur = a & M

    def put(self, a, bs):
        for i, b in enumerate(bs):
            k = (a + i) & M
            assert self.mem.get(k, b) == b, f"{self.name}: overlap at {k:08x}"
            self.mem[k] = b

    def psw(self):
        z, s, ov, cy = self.flags
        return PSW0 | z | (s << 1) | (ov << 2) | (cy << 3)

    # ---- executed instructions (emitted in EXECUTION order) ----
    def _emit(self, bs, kind, nextpc=None):
        assert self.cur == self.nxt, f"{self.name}: path discontinuity {self.cur:08x} vs {self.nxt:08x}"
        pc = self.cur
        r0, p0 = list(self.regs), self.psw()
        self.put(pc, bs)
        self.cur = (pc + len(bs)) & M
        self.nxt = self.cur if nextpc is None else nextpc & M
        return pc, r0, p0

    def _record(self, pc, op, r0, p0, kind):
        n = len(self.recs)
        ch = "".join(f" R{i:02d}={self.regs[i]:08x}" for i in range(32) if self.regs[i] != r0[i])
        if self.psw() != p0:
            ch += f" PSW={self.psw():08x}"
        self.recs.append((pc, op, f"L {n} pc={pc:08x} op={op:02x}{ch}", kind))

    def mov(self, r, v):
        bs = [MOVW, 0x20 | r, 0xF4] + le32(v)
        pc, r0, p0 = self._emit(bs, "setup")
        self.regs[r] = v & M
        self._record(pc, MOVW, r0, p0, "setup")

    def alu_imm(self, op, r, v):
        bs = [op, 0x20 | r, 0xF4] + le32(v)
        pc, r0, p0 = self._emit(bs, "setup")
        res, z, s, ov, cy = alu(op, self.regs[r], v & M, self.flags[3])
        if op != CMP:
            self.regs[r] = res
        self.flags = [z, s, ov, cy]
        self._record(pc, op, r0, p0, "setup")

    def getpsw(self, r):
        pc, r0, p0 = self._emit([GETPSW, 0x60 | r], "setup")
        self.regs[r] = self.psw()
        self._record(pc, GETPSW, r0, p0, "setup")

    def nop(self):
        pc, r0, p0 = self._emit([NOP], "setup")
        self._record(pc, NOP, r0, p0, "setup")

    def jmp(self, target):
        """JMP /abs: next PC = the little-endian word at pc + 2 (computed from the bytes)."""
        bs = [JMP_OP, JMP_MODE] + le32(target)
        pc = self.cur
        tgt = int.from_bytes(bytes(bs[2:6]), "little")
        pc, r0, p0 = self._emit(bs, "jmp", nextpc=tgt)
        if self.first_unsup is None:
            self.first_unsup = len(self.recs)
        self._record(pc, JMP_OP, r0, p0, "jmp")

    def br8(self, target):
        """BR8 (0x6A, supported by V2 today): next PC = instruction address + sign-extended disp8."""
        d = target - self.cur
        assert -128 <= d <= 127, d
        pc, r0, p0 = self._emit([0x6A, d & 0xFF], "br8", nextpc=target)
        self._record(pc, 0x6A, r0, p0, "br8")

    def halt(self):
        pc, r0, p0 = self._emit([HALT], "halt")
        self._record(pc, HALT, r0, p0, "halt")
        self.halt_pc = pc

    def decoy(self, a):
        """Code that must NOT execute (a data-visible effect, then HALT) at address a."""
        self.put(a, [MOVW, 0x20 | 30, 0xF4] + le32(0xDEC0DE00) + [HALT])

    def raw_at(self, a, bs):
        self.put(a, bs)

    # ---- the unexecuted instruction a V2 trap expectation is about (neg/guard/immdest) ----
    def trap_instr(self, bs):
        """Place an instruction at the current PC that V2 must trap on (never executed
        by V2). Not part of the reference path."""
        assert self.cur == self.nxt
        self.trap_pc = self.cur
        self.n_before = len(self.recs)
        self.put(self.cur, bs)
        self.put(self.cur + len(bs), [HALT])

    # ---- final images ----
    def final_state(self, n):
        """(regs, psw) after the first n path instructions, by replay of the records."""
        regs, psw = [0] * 32, PSW0
        for _, _, line, _ in self.recs[:n]:
            for tok in line.split()[4:]:
                k, v = tok.split("=")
                if k == "PSW":
                    psw = int(v, 16)
                else:
                    regs[int(k[1:])] = int(v, 16)
        return regs, psw


# ----------------------------------------------------------------------------- program set
def finish_pos(p):
    p.cls = "pos"
    return p


def pos_programs():
    out = []
    A = lambda p: out.append(p)

    # J1: forward, 4-aligned target; J2: targets at +1, +2, +3 mod 4
    for off in (0, 1, 2, 3):
        p = Prog(f"j1_fwd_t{off}" if off == 0 else f"j2_fwd_t{off}", "pos")
        p.mov(1, 0x11111111); p.mov(2, 0x22222222)
        t = 0x40 + off
        p.decoy(p.cur + 6) if False else None
        here = p.cur
        p.jmp(t)
        p.decoy(here + 6)
        p.org(t); p.halt()
        A(p)

    # J3: loop-free backward chain: forward over a block, back into it, forward to HALT
    p = Prog("j3_back_chain", "pos")
    p.mov(1, 0x13131313)
    a = p.cur
    p.jmp(0x60)                       # forward, over the block
    p.decoy(a + 6)
    p.org(0x60); p.jmp(0x30)          # back into the block
    p.org(0x30); p.mov(3, 7); p.jmp(0x80)
    p.org(0x80); p.halt()
    A(p)
    p = Prog("j3_back_chain_odd", "pos")
    p.mov(1, 0x14141414)
    p.jmp(0x63)
    p.org(0x63); p.jmp(0x33)
    p.org(0x33); p.mov(3, 9); p.jmp(0x85)
    p.org(0x85); p.halt()
    A(p)

    # J4: the JMP at each start alignment 0..3 mod 4 (6 bytes straddle fetch words differently)
    for k in range(4):
        p = Prog(f"j4_start_a{k}", "pos")
        p.mov(1, 0xA4A4A4A4)
        for _ in range(k):
            p.nop()
        here = p.cur
        assert here % 4 == (7 + k) % 4
        p.jmp(0x100)
        p.decoy(here + 6)
        p.org(0x100); p.halt()
        A(p)

    # J5: nothing after the JMP executes; the queue is flushed
    p = Prog("j5_after_mov", "pos")
    p.mov(1, 0x05050505)
    here = p.cur
    p.jmp(0x80)
    p.put(here + 6, [MOVW, 0x20 | 9, 0xF4] + le32(0x77777777) + [MOVW, 0x20 | 9, 0xF4] + le32(0x88888888))
    p.org(0x80); p.halt()
    A(p)
    p = Prog("j5_after_f3_bytes", "pos")
    p.mov(1, 0x05050506)
    here = p.cur
    p.jmp(0x84)
    p.put(here + 6, [0xF3, 0xFF, JMP_OP, JMP_MODE, 0xF3, 0xF3, 0xE5, 0xD6, 0xF3, 0x00, 0x00, 0x00, 0x00, HALT])
    p.org(0x84); p.halt()
    A(p)

    # J6: JMP -> JMP -> JMP chains (back-to-back redirects)
    p = Prog("j6_chain3", "pos")
    p.jmp(0x20)
    p.org(0x20); p.jmp(0x40)
    p.org(0x40); p.jmp(0x60)
    p.org(0x60); p.halt()
    A(p)
    p = Prog("j6_chain5_align", "pos")
    p.mov(2, 0x66666666)
    p.jmp(0x31)
    p.org(0x31); p.jmp(0x52)
    p.org(0x52); p.jmp(0x73)
    p.org(0x73); p.jmp(0x94)
    p.org(0x94); p.jmp(0xB0)
    p.org(0xB0); p.halt()
    A(p)

    # J7: the target is the next sequential address (+6)
    p = Prog("j7_next", "pos")
    p.mov(1, 0x07070707)
    p.jmp(p.cur + 6)
    p.halt()
    A(p)
    p = Prog("j7_next_first", "pos")
    p.jmp(6)
    p.halt()
    A(p)

    # J8: flags set by ADD (each of Z, S, OV, CY in its own program, plus combinations),
    # then JMP, then GETPSW at the target: the PSW survives the JMP
    for nm, (b, a) in {"z": (0, 0), "s": (0, 0x80000000), "ov": (0x7FFFFFFF, 1),
                       "cy": (0xFFFFFFFF, 2), "zcy": (0xFFFFFFFF, 1), "sov": (0x7FFFFFFF, 0x7FFFFFFF)}.items():
        p = Prog(f"j8_flags_{nm}", "pos")
        p.mov(4, b); p.alu_imm(ADD, 4, a)
        here = p.cur
        p.jmp(0x50)
        p.decoy(here + 6)
        p.org(0x50); p.getpsw(10); p.halt()
        A(p)

    # J9: JMP after a long ALU sequence; and as the first instruction after reset
    rng = random.Random(0x4A9)
    p = Prog("j9_long_alu", "pos")
    for i in range(1, 9):
        p.mov(i, rng.getrandbits(32))
    for _ in range(24):
        p.alu_imm(rng.choice([ADD, SUB, AND, CMP]), rng.randrange(1, 9), rng.getrandbits(32))
    here = p.cur
    p.jmp(0x400)
    p.decoy(here + 6)
    p.org(0x400); p.getpsw(11); p.halt()
    A(p)
    p = Prog("j9_first_instr", "pos")
    p.jmp(0x30)
    p.decoy(6)
    p.org(0x30); p.mov(1, 0x99999999); p.halt()
    A(p)

    # J11: start AND target offsets 0..7 mod 8 (V2's wide lines are 8 bytes)
    for so in range(8):
        for to in range(8):
            p = Prog(f"j11_s{so}_t{to}", "pos")
            for _ in range(so):
                p.nop()
            here = p.cur
            assert here % 8 == so
            t = 0x200 + to
            p.jmp(t)
            p.decoy(here + 6)
            p.org(t); p.halt()
            A(p)

    # J12: the JMP's last bytes withheld (+ackmax, fetches in [16, 272) never acknowledged).
    # k = how many of the JMP's 6 bytes are below the limit 16 (k = 6: all of them; the
    # sequential byte after the JMP is withheld for the whole run while target fetches
    # are served). The target HALT is at 0x300, outside the withheld window.
    for k in range(1, 7):
        p = Prog(f"j12_k{k}", "pos")
        p.mov(1, 0x12121212)                                  # 0..6
        while p.cur < 16 - k:
            p.nop()
        assert p.cur == 16 - k
        here = p.cur
        p.jmp(0x300)
        p.decoy(here + 6)
        p.org(0x300); p.halt()
        p.extra = {"limit": 16, "k": k, "s": here, "ackrel": here}
        A(p)
    return out


def br8_programs():
    """The mechanism proof for J12 with a supported redirect: BR8 whose sequential bytes are
    withheld (+ackmax=<limit>, released only when the BR8 at <s> redirects, +ackrel=<s>, returning
    poison). Layout: BR8(0) -> BR8(s) -> HALT(2); the second BR8 ends exactly at the limit, its
    target lies below the withheld window."""
    out = []
    for L in (16, 24):
        s_ = L - 2
        p = Prog(f"br8_ack{L}", "br8x")
        p.br8(s_)
        p.org(s_)
        p.br8(2)
        p.decoy(L)                                # sequential bytes after the BR8: must never execute
        p.org(2); p.halt()
        p.extra = {"limit": L, "s": s_, "ackrel": s_, "k": 2}
        out.append(p)
    return out


def s0_programs():
    """J10 / J13 on the S0 harness: reset start 0xFFFFFFF0, 24-bit physical bus, synthetic ROM."""
    out = []
    # J10: JMP at 0xFFFFFFF0 to a low address (GA2's own first instruction is jmp 100506)
    for nm, t in (("100506", 0x100506), ("rom0200", 0x000200), ("rom_hi", 0x1FFFF0), ("mirror", 0xF00123)):
        p = Prog(f"j10_{nm}", "pos", "s0", 0xFFFFFFF0)
        p.jmp(t)
        p.decoy(0xFFFFFFF6)
        p.org(t); p.mov(1, 0xA10A10A1); p.halt()
        out.append(p)
    # J13: targets with non-zero upper address bits (mirrored by the 24-bit bus)
    for nm, t in (("ff", 0xFF100506), ("80", 0x80100506), ("01", 0x01100506), ("a5", 0xA5000200)):
        p = Prog(f"j13_upper_{nm}", "pos", "s0", 0xFFFFFFF0)
        p.jmp(t)
        p.decoy(0xFFFFFFF6)
        p.org(t); p.mov(2, 0xB13B13B1); p.halt()
        out.append(p)
    # J13: the JMP's 6 bytes cross 0xFFFFFFFF -> 0 at every offset (start 0xFFFFFFFA .. 0xFFFFFFFF)
    for s in range(0xFA, 0x100):
        p = Prog(f"j13_wrap_{s:02x}", "pos", "s0", 0xFFFFFFF0)
        p.mov(1, 0x13131313)                                   # 0xFFFFFFF0..F6
        while p.cur < 0xFFFFFF00 + s:
            p.nop()
        assert p.cur == 0xFFFFFF00 + s, hex(p.cur)
        t = 0x7F010102                                         # a2 = 01 is read from after the wrap
        p.jmp(t)
        p.org(t); p.halt()
        out.append(p)
    # a JMP at 0xFFFFFFFC whose target is the wrap itself region start (bytes at 0)
    p = Prog("j13_wrap_to_zero", "pos", "s0", 0xFFFFFFF0)
    p.mov(1, 0x13131314)
    while p.cur < 0xFFFFFFFC:
        p.nop()
    p.jmp(0x00000002)
    p.org(0x00000002); p.halt()
    out.append(p)
    return out


# ----------------------------------------------------------------------------- negatives
def abs_op(a=0x00001230):
    return (0, [0xF3] + le32(a))


def neg_programs():
    out = []
    # N1: D6 with a mode that is not /abs: register indirect, #q, #imm32 (V2 r1b: trap_mode)
    for nm, bs in (("n1_regind", [JMP_OP, 0x61]), ("n1_quick", [JMP_OP, 0xE5]),
                   ("n1_imm32", [JMP_OP, 0xF4] + le32(0x1234)), ("n1_disp8", [JMP_OP, 0x01, 0x08])):
        p = Prog(nm, "neg")
        p.mov(1, 0x01010101)
        p.trap_instr(bs)
        p.extra = {"v2": {"r1a": "opcode", "r1b": "mode"}}
        out.append(p)
    # N2: D7 (modm 1): D7 F3 ... and D7 6x (JMP Rn, register direct): trap `opcode` (not dispatched)
    for nm, bs in (("n2_d7_abs", [0xD7, 0xF3] + le32(0x1230)), ("n2_d7_regdirect", [0xD7, 0x61])):
        p = Prog(nm, "neg")
        p.mov(1, 0x02020202)
        p.trap_instr(bs)
        p.extra = {"v2": {"r1a": "opcode", "r1b": "opcode"}}
        out.append(p)
    return out


def guard_programs():
    """G1: /abs through each of the 21 operand() call sites of m1_m11_microcode.py."""
    out = []
    a = abs_op()
    sites = []     # (name, bytes builder)
    for opn, op in (("movw", MOVW), ("add", ADD), ("sub", SUB), ("cmp", CMP), ("and", AND)):
        sites.append((f"{opn}_d0_dst", lambda op=op: _enc(lambda q: q.f2d0(2, a, op))))     # OP Rs, /abs
        sites.append((f"{opn}_d1_src", lambda op=op: _enc(lambda q: q.f2d1(a, 3, op))))     # OP /abs, Rd
        sites.append((f"{opn}_f1_src", lambda op=op: _enc(lambda q: q.f1(a, P.reg(4), op))))  # F1 op1
        sites.append((f"{opn}_f1_dst", lambda op=op: _enc(lambda q: q.f1(P.reg(1), a, op))))  # F1 op2
    sites.append(("getpsw", lambda: [GETPSW, 0xF3] + le32(0x1230)))                         # bank 1: F7 F3
    assert len(sites) == 21
    for nm, mk in sites:
        p = Prog(f"g1_{nm}", "guard")
        p.mov(1, 0x01010101)
        p.mov(2, 0x02020202)
        p.trap_instr(mk())
        p.extra = {"v2": {"r1a": "mode", "r1b": "mode"}, "site": nm}
        out.append(p)
    return out


def _enc(f):
    q = P()
    f(q)
    return list(q.b)


def immdest_programs():
    """Section 11 row 1: an immediate destination at every writing destination classifier."""
    out = []
    for opn, op in (("movw", MOVW), ("add", ADD), ("sub", SUB), ("and", AND)):
        for dn, dst in (("q", P.quick(5)), ("imm", P.imm(0x00009990))):
            for form, fn in (("d0", lambda q, op=op, dst=dst: q.f2d0(1, dst, op)),
                             ("f1", lambda q, op=op, dst=dst: q.f1(P.reg(1), dst, op))):
                p = Prog(f"id_{opn}_{form}_{dn}", "immdest")
                p.mov(1, 0x00000007)
                p.trap_instr(_enc(fn))
                p.extra = {"v2": {"r1a": "immdest", "r1b": "immdest"}, "site": f"{opn}_{form}_dst"}
                out.append(p)
    return out


def nonvac_programs():
    """Counter non-vacuity: data accesses. nv_store: MOV.W R2,[R1] (the TEST-ONLY --smc-test
    store on V2; upstream and the references execute it normally): dw = 1. nv_load:
    MOV.W [R1],R3 (not supported by V2): dr = 1 on upstream and the references."""
    out = []
    p = Prog("nv_store", "nonvac")
    p.mov(1, 0x00000800); p.mov(2, 0xDEADBEEF)
    pc, r0, p0 = p._emit([MOVW, 0x02, 0x61], "store")                 # MOV.W R2, [R1]
    p._record(pc, MOVW, r0, p0, "store")
    p.halt()
    # v60_ref's F12 path reads the destination operand of EVERY op, MOV included
    # (_exec_f12: b = mem.r(...)), so the Python references count dr = 1 there; upstream
    # and V2 do not read it: dr = 0
    p.extra = {"counters": {2: {"dr": 0, "dw": 1, "priv": 0}}, "v2mc": "smc",
               "ref_counters": {2: {"dr": 1, "dw": 1, "priv": 0}}}
    out.append(p)
    p = Prog("nv_load", "nonvac")
    p.mov(1, 0x00000800)
    p.org(0x800); p.put(0x800, le32(0xCAFEF00D)); p.org(7)
    pc, r0, p0 = p._emit([MOVW, 0x23, 0x61], "load")                  # MOV.W [R1], R3
    p.regs[3] = 0xCAFEF00D
    p._record(pc, MOVW, r0, p0, "load")
    p.halt()
    p.extra = {"counters": {1: {"dr": 1, "dw": 0, "priv": 0}}, "v2": None}
    out.append(p)
    # nv_ldpr: LDPR #imm32, #5 (SBR <- value): a privileged-register write. v60_ref has no
    # LDPR, so only m1_ref_ext (--ext) and upstream run it; V2 does not implement it.
    p = Prog("nv_ldpr", "nonvac")
    p.mov(1, 0x11111111)
    pc, r0, p0 = p._emit([0x12, 0x80, 0xF4] + le32(0x00123000) + [0xE5], "ldpr")
    p._record(pc, 0x12, r0, p0, "ldpr")
    p.halt()
    p.extra = {"counters": {1: {"dr": 0, "dw": 0, "priv": 1}}, "v2": None, "refs": ["ext", "up"]}
    out.append(p)
    return out


# ----------------------------------------------------------------------------- output
def v2_expect(p):
    """Per-phase V2 expectation: {"kind": "halt"|"trap"|"wait", ...}. Returns (standard, ackmax),
    each {"r1a": ..., "r1b": ...} (ackmax only for the J12 programs)."""
    ex = p.extra.get("v2")
    if p.cls in ("neg", "guard", "immdest"):
        return {ph: {"kind": "trap", "trap": ex[ph], "pc": p.trap_pc, "n": p.n_before} for ph in ("r1a", "r1b")}, None
    if p.cls == "nonvac":
        if p.extra.get("v2mc") == "smc":
            return {ph: {"kind": "halt"} for ph in ("r1a", "r1b")}, None
        return None, None
    if p.cls == "br8x":
        h = {"kind": "halt", "limit": p.extra["limit"]}
        return {ph: {"kind": "halt"} for ph in ("r1a", "r1b")}, {ph: h for ph in ("r1a", "r1b")}
    n_jmp = p.first_unsup
    jmp_pc = p.recs[n_jmp][0]
    std = {"r1a": {"kind": "trap", "trap": "opcode", "pc": jmp_pc, "n": n_jmp}, "r1b": {"kind": "halt"}}
    ack = None
    if "limit" in p.extra:
        k = p.extra["k"]
        # V2's documented decode rule (m1_m11.truncated): an opcode dispatches iff its first two
        # bytes are below the limit. k = bytes of the JMP below the limit.
        wait_nd = {"kind": "wait", "retired": n_jmp - 1, "state_n": n_jmp, "limit": p.extra["limit"]}   # JMP not dispatched
        wait_d = {"kind": "wait", "retired": n_jmp, "state_n": n_jmp, "limit": p.extra["limit"]}        # dispatched, operand missing
        a_r1a = dict(std["r1a"], limit=p.extra["limit"]) if k >= 2 else wait_nd
        a_r1b = dict(std["r1b"], limit=p.extra["limit"]) if k == 6 else (wait_d if k >= 2 else wait_nd)
        ack = {"r1a": a_r1a, "r1b": a_r1b}
    return std, ack


def rom_off(a):
    """S0 memory map: phys (a mod 2^24) -> ROM byte offset (0..0x1FFFFF direct; 0xF00000.. mirrors 0..0xFFFFF)."""
    a &= 0xFFFFFF
    if a < 0x200000:
        return a
    if a >= 0xF00000:
        return a & 0xFFFFF
    raise ValueError(f"{a:06x} is not ROM in the S0 map")


def emit(progs, out):
    man = {}
    for p in progs:
        info = {"name": p.name, "cls": p.cls, "where": p.where, "start_pc": p.start}
        if p.cls in ("pos", "nonvac", "br8x"):
            info["exp_L"] = [r[2][2:] for r in p.recs]
            regs, psw = p.final_state(len(p.recs))
            info["final_regs"] = regs
            info["final_psw"] = psw
            info["n_instrs"] = len(p.recs)
            info["halt_pc"] = p.halt_pc
            info["exp_counters"] = {str(i): p.extra.get("counters", {}).get(i, {"dr": 0, "dw": 0, "priv": 0})
                                    for i in range(len(p.recs))}
            info["jmp_idx"] = p.first_unsup
        else:
            info["n_before"] = p.n_before
            info["trap_pc"] = p.trap_pc
            info["exp_L"] = [r[2][2:] for r in p.recs[:p.n_before]]
            regs, psw = p.final_state(p.n_before)
            info["final_regs"], info["final_psw"] = regs, psw
        info["v2"], info["v2_ack"] = v2_expect(p)
        # --smc-test builds (variant isolation, plan section 10 #1, section 11 note 5): JMP is not
        # dispatched and /abs is not a mode, in BOTH phases
        if p.cls == "pos" and p.first_unsup is not None:
            t = {"kind": "trap", "trap": "opcode", "pc": p.recs[p.first_unsup][0], "n": p.first_unsup}
            info["v2_smc"] = {"r1a": t, "r1b": t}
        elif p.cls in ("guard", "immdest"):
            info["v2_smc"] = info["v2"]
        info["refs"] = p.extra.get("refs", ["ref", "ext", "up"])
        info["ref_counters"] = {str(k): v for k, v in p.extra.get("ref_counters", {}).items()}
        info["extra"] = {k: v for k, v in p.extra.items() if k not in ("v2", "counters", "ref_counters", "refs")}
        info["v2mc"] = p.extra.get("v2mc", "prod")
        # images
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
            with open(out / f"{p.name}.rom.hex", "w") as f:        # sparse $readmemh: @word-address then words
                words = {}
                for o, b in rom.items():
                    words.setdefault(o >> 1, [0, 0])[o & 1] = b
                last = None
                for w in sorted(words):
                    if last is None or w != last + 1:
                        f.write("@%x\n" % w)
                    f.write("%04x\n" % (words[w][0] | (words[w][1] << 8)))
                    last = w
            with open(out / f"{p.name}.segs", "w") as f:           # virtual addresses, for the Python references
                for a in sorted(p.mem):
                    pass
                runs, cur = [], None
                for a in sorted(p.mem):
                    if cur and a == cur[0] + len(cur[1]):
                        cur[1].append(p.mem[a])
                    else:
                        cur = [a, bytearray([p.mem[a]])]
                        runs.append(cur)
                for a, bs in runs:
                    f.write(f"{a:08x} {bytes(bs).hex()}\n")
        man[p.name] = info
    return man


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    progs = pos_programs() + br8_programs() + s0_programs() + neg_programs() + guard_programs() + immdest_programs() + nonvac_programs()
    names = [p.name for p in progs]
    assert len(names) == len(set(names))
    man = emit(progs, out)
    (out / "r1_manifest.json").write_text(json.dumps(man, indent=0))
    cnt = {}
    for p in progs:
        k = f"{p.cls}/{p.where}"
        cnt[k] = cnt.get(k, 0) + 1
    print("r1: " + ", ".join(f"{k} {v}" for k, v in sorted(cnt.items())) + f" (total {len(progs)})")


if __name__ == "__main__":
    main()
