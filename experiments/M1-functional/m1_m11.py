#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""M1.1 test programs: MOV.W in every F1/F2 form with the modes M1.1 supports,
BR8 (0x6A), NOP and HALT. Encodings as ref/v60_ref.py decodes them (see
m1_directed.py's header).

Reference behaviour established in M1.1a (both references agree unless
noted; docs/v2-m1.1-plan.md):
  * MOV.W to an immediate destination (F2 D=0 with #quick, F1 with #quick or
    #imm32 as operand 2): the REFERENCES DISAGREE. Both advance the PC by the
    full length and leave the registers alone, but upstream RTL treats the
    immediate as an address and writes the value there (#3 -> a word write to
    address 3, #imm32 -> a write to that address), while v60_ref discards the
    store. Documented probes; V2 traps them (kind immdest) until a decision.
  * modm 1, mode groups 6 and 7 (0xC0-0xFF): v60_ref raises
    NotImplementedError; upstream RTL executes group 6 as some operation (it
    wrote 0x00C5612D to R1 for 2D 61 C5) and takes an exception on group 7.
    Documented probes; V2 traps them as unsupported.
  * BR8 = 0x6A: target = instruction address + sign-extended disp8.

Three classes of program, listed in <out>/m11_list.txt:
  agree <name>                       both references agree; V2 must match them
  unsupported <name> <n> <kind> <pc> instruction n (0-based), at <pc>, is outside V2's
                                     M1.1 subset; V2 must trap there (kind:
                                     opcode or mode), after the first n
                                     instructions retire exactly as in the
                                     references
  probe <name> <n> <kind> <pc>       the references disagree (documented); V2
                                     traps at instruction n (kind: mode, or
                                     immdest for an immediate destination). <pc>
                                     comes from the program itself, not from a
                                     reference record (review C2-2: v60_ref
                                     raises before recording a reserved mode)
  truncated <name> <n> <limit> <r>   fetches in [<limit>, <limit> + 256) are never
                                     acknowledged (+ackmax): V2 must wait, with
                                     the reference's state after n instructions
                                     and r retired (see truncated()), no
                                     immediate latched early
  m11_smc_list.txt: self-modifying-code programs (agree), test-only microcode

Usage: m1_m11.py <out-dir> [nrandom]
"""
import random
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from m1_image import write  # noqa: E402

MOVW, NOP, HALT, BR8 = 0x2D, 0xCD, 0x00, 0x6A
EDGE = [0, 0xFFFFFFFF, 0x80000000, 0x7FFFFFFF, 1]


class P:
    """A program: bytes plus the instruction start offsets."""

    def __init__(self):
        self.b = bytearray()
        self.starts = []

    def ins(self, *bs):
        self.starts.append(len(self.b))
        self.b += bytes(bs)

    def raw(self, *bs):
        self.b += bytes(bs)

    # operand specifiers: (modm, bytes)
    @staticmethod
    def reg(r):
        return (1, [0x60 | r])

    @staticmethod
    def quick(n):
        return (0, [0xE0 | n])

    @staticmethod
    def imm(v):
        return (0, [0xF4] + list(struct.pack("<I", v & 0xFFFFFFFF)))

    def f2d1(self, src, rd, op=MOVW):        # OP <src>, Rd   (MOV.W by default)
        m, s = src
        self.ins(op, (m << 6) | 0x20 | rd, *s)

    def f2d0(self, rs, dst, op=MOVW):        # OP Rs, <dst>
        m, s = dst
        self.ins(op, (m << 6) | rs, *s)

    def f1(self, src, dst, op=MOVW):         # OP <src>, <dst>
        m1, s1 = src
        m2, s2 = dst
        self.ins(op, 0x80 | (m1 << 6) | (m2 << 5), *s1, *s2)

    def nop(self):
        self.ins(NOP)

    def halt(self):
        self.ins(HALT)

    def br8(self, target):                   # target: absolute offset
        here = len(self.b)
        d = target - here
        assert -128 <= d <= 127, d
        self.ins(BR8, d & 0xFF)

    def br8_fix(self):                       # BR8 with a displacement patched later
        here = len(self.b)
        self.ins(BR8, 0)
        return here

    def patch(self, at, target):
        d = target - at
        assert -128 <= d <= 127, d
        self.b[at + 1] = d & 0xFF


def directed():
    T = {}

    p = P()                                  # every register, imm32, edge values
    for r in range(31):
        p.f2d1(P.imm(EDGE[r % len(EDGE)] if r < 5 else 0x01010101 * r ^ 0xA5000000), r)
    p.halt()
    T["mov_imm32_all_regs"] = p

    p = P()                                  # quick 0..15
    for n in range(16):
        p.f2d1(P.quick(n), (n * 7) % 31)
    p.halt()
    T["mov_quick"] = p

    p = P()                                  # F2 D=1 and D=0, register direct
    for r in range(8):
        p.f2d1(P.imm(0x11111111 * (r + 1)), r)
    for r in range(8):
        p.f2d1(P.reg(r), r + 8)              # D=1: op1 = Rr, op2 = R(r+8)
    for r in range(8):
        p.f2d0(r + 8, P.reg(r + 16))         # D=0: op1 = R(r+8), op2 = R(r+16)
    p.f2d1(P.reg(3), 3)                      # to itself
    p.halt()
    T["mov_reg_f2"] = p

    p = P()                                  # F1: every source to a register (modm 1 and 1/0 mixes)
    p.f2d1(P.imm(0xCAFEF00D), 1)
    for i, s in enumerate([P.reg(1), P.quick(9), P.imm(0x13572468)]):
        p.f1(s, P.reg(5 + i))
    p.f1(P.reg(5), P.reg(5))
    p.halt()
    T["mov_f1_all"] = p

    p = P()                                  # R31
    p.f2d1(P.imm(0x00001000), 31)
    p.f2d1(P.reg(31), 3)
    p.f2d0(31, P.reg(4))
    p.halt()
    T["mov_r31"] = p

    # lengths 1, 2, 3, 4, 7, 8 back to back, starting at every alignment
    for a in range(8):
        p = P()
        for _ in range(a):
            p.nop()
        p.f2d1(P.imm(0xA0000000 + a), 1)                 # 7
        p.f1(P.imm(0xB0000000 + a), P.reg(2))            # 8
        p.f2d1(P.quick(a), 3)                            # 3
        p.f1(P.reg(1), P.reg(4))                         # 4
        p.nop()                                          # 1
        at = p.br8_fix(); p.patch(at, len(p.b))          # 2: BR8 to the next instruction
        p.f2d0(2, P.reg(5))                              # 3
        p.f1(P.quick(15), P.reg(6))                      # 4
        p.f2d1(P.imm(0xC0000000 + a), 7)                 # 7
        p.halt()
        T[f"lengths_align{a}"] = p

    p = P()                                  # BR8 forward, over invalid/unsupported bytes
    at = p.br8_fix()
    p.raw(0xFF, 0xE5, 0x2D, 0x61, 0xC5, 0x00)            # would trap / be reserved / HALT
    p.patch(at, len(p.b))
    p.f2d1(P.quick(7), 3)
    p.halt()
    T["br8_over_bad_bytes"] = p

    p = P()                                  # BR8 backward, loop-free: A -> C -> B -> D
    a = p.br8_fix()
    b = len(p.b); p.f2d1(P.quick(1), 1)
    bd = p.br8_fix()
    c = len(p.b); p.br8(b)
    d = len(p.b); p.f2d1(P.quick(2), 2)
    p.halt()
    p.patch(a, c); p.patch(bd, d)
    T["br8_backward"] = p

    p = P()                                  # BR8 back to an earlier HALT
    a = p.br8_fix()
    h = len(p.b); p.halt()
    p.patch(a, len(p.b))
    p.f2d1(P.quick(9), 9)
    p.br8(h)
    T["br8_back_to_halt"] = p

    for a in range(8):                       # BR8 targets at every alignment
        p = P()
        at = p.br8_fix()
        p.raw(*([0xFF] * (6 + a)))
        p.patch(at, len(p.b))
        p.f2d1(P.imm(0xD0000000 + a), 1)
        p.f1(P.quick(a), P.reg(2))
        p.halt()
        T[f"br8_target_align{a}"] = p

    p = P()                                  # BR8 chain, maximum forward reach
    at = p.br8_fix()
    p.raw(*([0xFF] * 125))
    p.patch(at, len(p.b))
    p.f2d1(P.quick(4), 4)
    at = p.br8_fix(); p.patch(at, len(p.b))
    p.halt()
    T["br8_far"] = p
    return T


def unsupported():
    """(name, program, index of the unsupported instruction, kind, probe?)"""
    U = []
    p = P(); p.f2d1(P.quick(1), 1); p.ins(0xB4, 0x21, 0xE2); p.halt()        # XOR.W (ADD.W became supported in M1.2)
    U.append(("unsup_opcode_xorw", p, 1, "opcode", False))
    p = P(); p.f2d1(P.quick(1), 1); p.ins(0x8C, 0x21, 0xE2); p.halt()        # OR.W
    U.append(("unsup_opcode_orw", p, 1, "opcode", False))
    p = P(); p.f2d1(P.imm(0x100), 1); p.ins(MOVW, 0x22, 0x61); p.halt()      # [R1]: M1.3
    U.append(("unsup_mode_regind", p, 1, "mode", False))
    p = P(); p.f2d1(P.imm(0x100), 1); p.ins(MOVW, 0x22, 0x01, 0x08); p.halt()  # disp8[R1]
    U.append(("unsup_mode_disp8", p, 1, "mode", False))
    p = P(); p.f2d1(P.imm(0x100), 1); p.ins(MOVW, 0x62, 0x81); p.halt()      # [R1+]
    U.append(("unsup_mode_autoinc", p, 1, "mode", False))
    p = P(); p.f2d1(P.imm(0x100), 1); p.ins(MOVW, 0x80 | 0x40, 0x61, 0x61); p.halt()  # F1 op2 [R1]
    U.append(("unsup_mode_f1_op2_regind", p, 1, "mode", False))
    # immediate destinations: the references disagree (see the header)
    p = P(); p.f2d1(P.imm(7), 1); p.f2d0(1, P.quick(5)); p.halt()
    U.append(("probe_immdest_f2d0_quick", p, 1, "immdest", True))
    p = P(); p.f2d1(P.imm(7), 1); p.f2d0(1, P.imm(0x00009990)); p.halt()
    U.append(("probe_immdest_f2d0_imm32", p, 1, "immdest", True))
    p = P(); p.f2d1(P.imm(7), 1); p.f1(P.quick(3), P.quick(4)); p.halt()
    U.append(("probe_immdest_f1_quick", p, 1, "immdest", True))
    p = P(); p.f2d1(P.imm(7), 1); p.f1(P.reg(1), P.imm(0x00009994)); p.halt()
    U.append(("probe_immdest_f1_imm32", p, 1, "immdest", True))
    p = P(); p.f2d1(P.quick(1), 1); p.ins(MOVW, 0x61, 0xC5); p.halt()        # modm 1 group 6
    U.append(("probe_modm1_group6", p, 1, "mode", True))
    p = P(); p.f2d1(P.quick(1), 1); p.ins(MOVW, 0x61, 0xE5); p.halt()        # modm 1 group 7
    U.append(("probe_modm1_group7", p, 1, "mode", True))
    return U


def truncated():
    """Truncated instructions (review C2-3): the image continues, but fetches at
    or above `limit` (8-aligned) are never acknowledged, so the last
    instruction's bytes from the limit on never arrive. It starts j bytes
    before the limit: j = 0 nothing of it, 1 the opcode, 2 the format byte
    too, 3 the mode byte (#imm32) too, 5 two immediate bytes too. V2 must not
    dispatch it before its format byte, latch the immediate early, or retire
    it.

    Expected, from V2's documented decode rule (an opcode dispatch waits for
    the opcode AND the next byte, even for a one-byte instruction): an
    instruction dispatches iff its first two bytes are below the limit; d =
    how many dispatch (in order); V2 retires d - 1 (the last one dispatched
    has no successor); its state equals the reference after the dispatched
    instructions that are complete. For j = 0 the NOP just below the limit
    therefore never executes: a real V60 would execute it (review C2-3;
    recorded in docs/reviews/C2-codex-review.md)."""
    T = []
    for j in (0, 1, 2, 3, 5):
        p = P()
        p.f2d1(P.quick(5), 2)                                # 3 bytes
        p.f2d1(P.imm(0x11223344), 4)                         # 7 bytes
        limit = 24
        while len(p.b) < limit - j:
            p.nop()
        n = len(p.starts)
        p.f2d1(P.imm(0xA5A5A5A5), 1)                         # the truncated instruction
        p.halt()
        ends = p.starts[1:] + [len(p.b)]
        d = 0
        while d < len(p.starts) and p.starts[d] + 1 < limit:
            d += 1
        state_n = sum(1 for k in range(d) if ends[k] <= limit)
        T.append((f"trunc_j{j}", p, state_n, limit, d - 1))
    # KNOWN LIMITATION, demonstrated (accepted provisionally, 2026-10-24): a
    # HALT is the last byte before the withheld fetches. Both references
    # halt; V2's opcode dispatch waits for the byte after the opcode, so the
    # HALT is never executed and V2 does not halt. This regression pins that
    # behaviour (it is not V60-faithful); revisit before the CPU is declared
    # functionally complete (docs/reviews/C2-codex-review.md).
    p = P()
    p.f2d1(P.quick(5), 2)
    limit = 8
    while len(p.b) < limit - 1:
        p.nop()
    p.halt()                                                 # at limit - 1: its next byte never arrives
    p.f2d1(P.quick(9), 3)                                    # beyond the limit, never fetched
    n_disp = sum(1 for st in p.starts if st + 1 < limit)
    T.append(("limitation_onebyte_halt", p, n_disp, limit, n_disp - 1))
    return T


def smc():
    """Self-modifying code (review C2-1), run with the TEST-ONLY --smc-test
    microcode: MOV.W Rs, [Rn] (F2 D=0, modm 0 0x60|n) stores a word over
    instruction bytes already in V2's fetch queue, right after the store or
    k bytes later. The old code is MOV.W #1, R3; the new code MOV.W #7, R3
    (after a NOP, for stores at the opcode), so executing the stale bytes
    leaves R3 = 1 or the wrong instruction boundaries. Both references
    execute the new bytes."""
    S = {}
    for k in range(7):
        for shift in (0, 1):                 # word store at the opcode, or one byte later (unaligned)
            p = P()
            # shift 0: a different opcode at the target (NOP, then MOV.W #7, R3), so
            # a stale dispatch shows as wrong boundaries; shift 1: same opcode,
            # new mode byte (#7)
            new = [0xCD, 0x2D, 0x23, 0xE7] if shift == 0 else [0x23, 0xE7, 0xCD, 0xCD]
            p.f2d1(P.imm(int.from_bytes(bytes(new), "little")), 2)
            at_r1 = len(p.b)
            p.f2d1(P.imm(0), 1)                              # target, patched below
            p.ins(MOVW, 0x02, 0x61)                          # MOV.W R2, [R1]
            for _ in range(k):
                p.nop()
            target = len(p.b)
            p.ins(MOVW, 0x23, 0xE1)                          # MOV.W #1, R3 (stale)
            p.nop(); p.nop()
            p.halt()
            p.b[at_r1 + 3:at_r1 + 7] = (target + shift).to_bytes(4, "little")
            S[f"smc_k{k}_shift{shift}"] = p
    return S


def rand_prog(seed):
    rng = random.Random(seed)
    p = P()
    for _ in range(rng.randrange(8)):
        p.nop()

    def val():
        return rng.choice(EDGE) if rng.random() < 0.2 else rng.getrandbits(32)

    def src():
        k = rng.random()
        return P.reg(rng.randrange(31)) if k < 0.4 else P.quick(rng.randrange(16)) if k < 0.7 else P.imm(val())

    def dst():                                # register only: immediate destinations are probes
        return P.reg(rng.randrange(31))

    for _ in range(rng.randrange(10, 40)):
        k = rng.random()
        if k < 0.25:
            p.f2d1(src(), rng.randrange(31))
        elif k < 0.45:
            p.f2d0(rng.randrange(31), dst())
        elif k < 0.75:
            p.f1(src(), dst())
        elif k < 0.82:
            p.nop()
        elif k < 0.92:                        # forward over garbage
            at = p.br8_fix()
            p.raw(*[rng.choice([0xFF, 0xE5, 0x2D, 0x61, 0xC5, 0x00, 0x84, rng.randrange(256)])
                    for _ in range(rng.randrange(7))])
            p.patch(at, len(p.b))
        else:                                 # loop-free backward: A -> C -> B -> D
            a = p.br8_fix()
            b = len(p.b); p.f2d1(src(), rng.randrange(31))
            bd = p.br8_fix()
            c = len(p.b); p.br8(b)
            p.patch(a, c); p.patch(bd, len(p.b))
    p.halt()
    return p


def main():
    out = Path(sys.argv[1])
    nrand = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    out.mkdir(parents=True, exist_ok=True)
    lines = []
    for name, p in directed().items():
        write(bytes(p.b), str(out / name)); lines.append(f"agree {name}")
    for name, p, n, kind, probe in unsupported():
        write(bytes(p.b), str(out / name))
        lines.append(f"{'probe' if probe else 'unsupported'} {name} {n} {kind} {p.starts[n]:08x}")
    for name, p, n, limit, retired in truncated():
        write(bytes(p.b), str(out / name))
        lines.append(f"truncated {name} {n} {limit} {retired}")
    for s in range(1, nrand + 1):
        name = f"rand{s}"
        write(bytes(rand_prog(s).b), str(out / name)); lines.append(f"agree {name}")
    (out / "m11_list.txt").write_text("\n".join(lines) + "\n")
    smc_lines = []
    for name, p in smc().items():
        write(bytes(p.b), str(out / name)); smc_lines.append(f"agree {name}")
    (out / "m11_smc_list.txt").write_text("\n".join(smc_lines) + "\n")
    print(f"m11: {len(lines)} programs ({nrand} random)")


if __name__ == "__main__":
    main()
