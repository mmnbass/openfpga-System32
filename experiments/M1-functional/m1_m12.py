#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""M1.2 test programs: ADD.W 0x84, SUB.W 0xAC, CMP.W 0xBC, AND.W 0xA4 in every
F1/F2 direction with the M1.1 operand modes, GETPSW 0xF7 to a register, plus
MOV.W, BR8, NOP and HALT. Same list format and classes as m1_m11.py.

Reference behaviour established in M1.2a (docs/v2-m1.2-plan.md; both
references agree unless noted):
  * operand 1 is the source a, operand 2 the destination b, read, operated
    on and written back: ADD b+a, SUB b-a, CMP b-a (flags only), AND b&a.
    Z/S from the result; ADD CY = carry, SUB/CMP CY = borrow (b < a
    unsigned); OV = signed overflow; AND: OV = 0, CY unchanged.
  * CMP with an immediate operand 2 (F2 D=0 #quick/#imm32, F1 operand 2
    #quick/#imm32): the references AGREE - the immediate's value is used,
    nothing is written. Implemented (agree programs).
  * ADD/SUB/AND to an immediate destination: the references DISAGREE as for
    MOV (upstream writes to the immediate taken as an address, v60_ref
    discards). Probes; V2 traps immdest (decision deferred to M1.3).
  * GETPSW (0xF7, modm 1) to a register writes the whole PSW.

Usage: m1_m12.py <out-dir> [nrandom]
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from m1_image import write  # noqa: E402
from m1_m11 import P, MOVW, NOP  # noqa: E402

ADD, SUB, CMP, AND = 0x84, 0xAC, 0xBC, 0xA4
OPS = {"add": ADD, "sub": SUB, "cmp": CMP, "and": AND}
GETPSW = 0xF7
M = 0xFFFFFFFF
EDGE = [0, 1, M, 0x7FFFFFFF, 0x80000000, 0x7FFFFFFE, 0x80000001, M - 1, 0x12345678, 0xF0F0F0F0]
# (b, a): destination value, source value
PAIRS = [(0, 0), (0, 1), (1, M), (M, 1), (0x7FFFFFFF, 1), (0x80000000, 1),
         (0x80000000, 0x80000000), (0x7FFFFFFF, M), (0x7FFFFFFF, 0x7FFFFFFF),
         (0x80000000, 0x7FFFFFFF), (M, M), (0, 0x80000000), (0x7FFFFFFE, 0x80000001),
         (5, 5), (3, 5), (5, 3), (0x12345678, 0xF0F0F0F0), (M, 0), (0x80000001, 0x80000000),
         (0x7FFFFFFF, 0x80000000)]
QUICK = [0, 1, 7, 15]


def getpsw(p, r):
    p.ins(GETPSW, 0x60 | r)


def directed():
    T = {}
    # every operation x form x edge pair; GETPSW after each result
    for on, op in OPS.items():
        for form in ("f2d1_reg", "f2d1_quick", "f2d1_imm", "f2d0_reg", "f1_reg", "f1_quick", "f1_imm"):
            p = P()
            pairs = [(b, a) for b, a in PAIRS] if "quick" not in form else \
                    [(b, q) for b in EDGE[:6] for q in QUICK]
            for b, a in pairs:
                p.f2d1(P.imm(b), 4)                              # destination value
                if form == "f2d1_reg":
                    p.f2d1(P.imm(a), 5); p.f2d1(P.reg(5), 4, op)
                elif form == "f2d1_quick":
                    p.f2d1(P.quick(a), 4, op)
                elif form == "f2d1_imm":
                    p.f2d1(P.imm(a), 4, op)
                elif form == "f2d0_reg":
                    p.f2d1(P.imm(a), 5); p.f2d0(5, P.reg(4), op)
                elif form == "f1_reg":
                    p.f2d1(P.imm(a), 5); p.f1(P.reg(5), P.reg(4), op)
                elif form == "f1_quick":
                    p.f1(P.quick(a), P.reg(4), op)
                else:
                    p.f1(P.imm(a), P.reg(4), op)
                getpsw(p, 6)
            p.halt()
            T[f"{on}_{form}"] = p

    # AND keeps CY and clears OV; CMP writes nothing; flags survive MOV/BR8/NOP
    p = P()
    p.f2d1(P.imm(M), 1); p.f2d1(P.quick(1), 1, ADD); getpsw(p, 2)          # CY=1, Z=1
    p.f2d1(P.imm(0xF0), 3); p.f2d1(P.imm(0x0F), 4); p.f2d1(P.reg(3), 4, AND); getpsw(p, 5)   # CY kept
    p.f2d1(P.imm(0x7FFFFFFF), 1); p.f2d1(P.quick(1), 1, ADD); getpsw(p, 6)  # OV=1, S=1, CY=0
    p.f2d1(P.imm(M), 7); p.f2d1(P.reg(7), 7, AND); getpsw(p, 8)            # OV cleared, CY kept 0
    p.f2d1(P.imm(M), 1); p.f2d1(P.quick(1), 1, ADD)                        # CY=1 again
    p.f2d1(P.imm(0x80000000), 9); p.f1(P.reg(9), P.reg(9), AND); getpsw(p, 10)   # S=1, CY kept 1
    p.halt()
    T["and_cy_ov"] = p

    p = P()                                                                # CMP: no write, all forms
    p.f2d1(P.imm(10), 1); p.f2d1(P.imm(20), 2)
    for src, dst in ((P.reg(1), P.reg(2)), (P.reg(2), P.reg(1)), (P.reg(1), P.reg(1)),
                     (P.quick(10), P.reg(1)), (P.imm(20), P.reg(2)), (P.imm(0x80000000), P.reg(1))):
        p.f1(src, dst, CMP); getpsw(p, 3)
    p.f2d0(1, P.quick(10), CMP); getpsw(p, 4)                             # immediate operand 2
    p.f2d0(2, P.imm(30), CMP); getpsw(p, 5)
    p.f1(P.quick(3), P.quick(5), CMP); getpsw(p, 6)
    p.f1(P.imm(7), P.imm(0x9994), CMP); getpsw(p, 7)
    p.f1(P.reg(2), P.quick(15), CMP); getpsw(p, 8)
    p.f2d1(P.reg(2), 1, CMP); p.f2d1(P.quick(9), 1, CMP); getpsw(p, 9)
    p.halt()
    T["cmp_no_write"] = p

    p = P()                                                                # flag preservation
    p.f2d1(P.imm(0x7FFFFFFF), 1); p.f2d1(P.quick(1), 1, ADD); getpsw(p, 2)  # S, OV
    p.f2d1(P.quick(0), 3); getpsw(p, 4)                                     # MOV keeps flags
    at = p.br8_fix(); p.patch(at, len(p.b)); getpsw(p, 5)                   # BR8 keeps flags
    p.nop(); getpsw(p, 6)                                                    # NOP keeps flags
    getpsw(p, 7)                                                             # GETPSW keeps flags
    p.f2d1(P.imm(0), 8); p.f2d1(P.imm(0), 9); p.f2d1(P.reg(9), 8, SUB); getpsw(p, 10)   # Z
    p.f2d1(P.imm(5), 11); getpsw(p, 12)
    p.halt()
    T["flags_preserved"] = p

    p = P()                                                                # sequential dependencies
    p.f2d1(P.imm(3), 1); p.f2d1(P.imm(5), 2)
    for _ in range(6):
        p.f2d1(P.reg(2), 1, ADD)            # R1 += R2
        p.f2d1(P.reg(1), 2, SUB)            # R2 -= R1
        p.f2d1(P.reg(1), 2, CMP)
        getpsw(p, 3)
        p.f1(P.reg(3), P.reg(1), AND)       # R1 &= R3
        p.f2d0(1, P.reg(2), ADD)            # R2 += R1
    p.f2d1(P.reg(1), 1, ADD); p.f2d1(P.reg(1), 1, SUB); getpsw(p, 4)   # same register: R1 + R1, then 0
    p.f2d1(P.imm(0xA5A5A5A5), 6); p.f2d1(P.reg(6), 6, AND); p.f2d1(P.reg(6), 6, CMP); getpsw(p, 7)
    p.halt()
    T["dependencies"] = p

    p = P()                                                                # GETPSW to every register
    for r in range(31):
        p.f2d1(P.imm(EDGE[r % len(EDGE)]), 1); p.f2d1(P.imm(EDGE[(r * 3 + 1) % len(EDGE)]), 2)
        p.f2d1(P.reg(2), 1, (ADD, SUB, CMP, AND)[r % 4]); getpsw(p, r)
    p.halt()
    T["getpsw_all_regs"] = p

    p = P()                                                                # R31 (review C3-1)
    p.f2d1(P.imm(0x00001000), 31); p.f2d1(P.imm(0x10), 1)
    p.f2d1(P.reg(1), 31, ADD); getpsw(p, 2)                               # R31 += R1 (F2 D=1, R31 destination)
    p.f2d1(P.quick(8), 31, SUB); getpsw(p, 3)                             # R31 -= 8
    p.f2d1(P.imm(0x7FFFFFFF), 31, ADD); getpsw(p, 4)                      # R31 + max positive: OV
    p.f2d0(31, P.reg(5), ADD); getpsw(p, 6)                               # R5 += R31 (R31 source)
    p.f1(P.reg(31), P.reg(7), SUB); getpsw(p, 8)                          # F1, R31 source
    p.f1(P.imm(0xFFFF0000), P.reg(31), AND); getpsw(p, 9)                 # F1, R31 destination
    p.f2d1(P.reg(31), 31, CMP); getpsw(p, 10)                             # CMP R31, R31: Z; R31 unchanged
    p.f2d1(P.quick(0), 31, CMP); getpsw(p, 11)                            # R31 unchanged
    p.f2d0(31, P.imm(0x8000), CMP); getpsw(p, 12)                         # CMP with immediate operand 2
    p.f2d1(P.reg(31), 13); getpsw(p, 31)                                  # GETPSW to R31
    p.f2d1(P.imm(0xFFFFFFFF), 14)                                          # (review C3-4) R14 all ones, so
    p.f2d1(P.reg(31), 14, AND); getpsw(p, 15)                             # R14 & R31 = R31: the R31 read shows
    p.f1(P.quick(7), P.reg(31), ADD); getpsw(p, 16)                       # F1 #quick with R31 destination
    p.f1(P.quick(3), P.reg(31), SUB); p.f1(P.quick(1), P.reg(31), AND); getpsw(p, 17)
    p.f1(P.quick(9), P.reg(31), CMP); getpsw(p, 18)
    p.halt()
    T["r31_alu_getpsw"] = p

    for a in range(8):                                                     # lengths 3/4/7/8 at all alignments
        p = P()
        for _ in range(a):
            p.nop()
        p.f2d1(P.imm(0x40000000 + a), 1)
        p.f2d1(P.imm(0x7FFFFFFF), 1, ADD); getpsw(p, 2)                  # 7
        p.f1(P.imm(0x80000000), P.reg(1), SUB); getpsw(p, 3)             # 8
        p.f2d1(P.quick(a), 1, CMP); getpsw(p, 4)                         # 3
        p.f1(P.reg(1), P.reg(5), AND); getpsw(p, 6)                      # 4
        p.halt()
        T[f"lengths_align{a}"] = p
    return T


def unsupported():
    U = []
    for on, op in (("add", ADD), ("sub", SUB), ("and", AND)):
        p = P(); p.f2d1(P.imm(7), 1); p.f2d0(1, P.quick(5), op); p.halt()
        U.append((f"probe_immdest_{on}_f2d0_quick", p, 1, "immdest", True))
        p = P(); p.f2d1(P.imm(7), 1); p.f1(P.reg(1), P.imm(0x9990), op); p.halt()
        U.append((f"probe_immdest_{on}_f1_imm32", p, 1, "immdest", True))
    p = P(); p.f2d1(P.quick(1), 1); p.ins(0x8C, 0x21, 0xE2); p.halt()       # OR.W
    U.append(("unsup_opcode_orw", p, 1, "opcode", False))
    p = P(); p.f2d1(P.quick(1), 1); p.ins(0xF6, 0x81); p.halt()             # GETPSW modm 0 (memory)
    U.append(("unsup_opcode_getpsw_modm0", p, 1, "opcode", False))
    p = P(); p.f2d1(P.imm(0x100), 1); p.ins(ADD, 0x22, 0x61); p.halt()      # ADD.W [R1], R2
    U.append(("unsup_mode_add_regind", p, 1, "mode", False))
    return U


def rand_prog(seed):
    rng = random.Random(seed + 120000)
    p = P()

    def val():
        return rng.choice(EDGE) if rng.random() < 0.2 else rng.getrandbits(32)

    def src():
        k = rng.random()
        return P.reg(rng.randrange(31)) if k < 0.45 else P.quick(rng.randrange(16)) if k < 0.7 else P.imm(val())

    for _ in range(rng.randrange(5)):
        p.f2d1(P.imm(val()), rng.randrange(31))
    for _ in range(rng.randrange(15, 45)):
        k = rng.random()
        op = rng.choice((ADD, SUB, CMP, AND, MOVW))
        if k < 0.25:
            p.f2d1(src(), rng.randrange(31), op)
        elif k < 0.45:
            dst = P.reg(rng.randrange(31))
            if op == CMP and rng.random() < 0.3:
                dst = P.quick(rng.randrange(16)) if rng.random() < 0.5 else P.imm(val())
            p.f2d0(rng.randrange(31), dst, op)
        elif k < 0.70:
            dst = P.reg(rng.randrange(31))
            if op == CMP and rng.random() < 0.3:
                dst = P.quick(rng.randrange(16)) if rng.random() < 0.5 else P.imm(val())
            p.f1(src(), dst, op)
        elif k < 0.80:
            getpsw(p, rng.randrange(31))
        elif k < 0.85:
            p.nop()
        elif k < 0.93:
            at = p.br8_fix()
            p.raw(*[rng.choice([0xFF, 0xE5, 0x84, 0xBC, 0x00, rng.randrange(256)]) for _ in range(rng.randrange(6))])
            p.patch(at, len(p.b))
        else:
            a = p.br8_fix()
            b = len(p.b); p.f2d1(src(), rng.randrange(31), rng.choice((ADD, SUB, AND)))
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
    for s in range(1, nrand + 1):
        name = f"rand{s}"
        write(bytes(rand_prog(s).b), str(out / name)); lines.append(f"agree {name}")
    (out / "m11_list.txt").write_text("\n".join(lines) + "\n")
    (out / "m11_smc_list.txt").write_text("")
    print(f"m12: {len(lines)} programs ({nrand} random)")


if __name__ == "__main__":
    main()
