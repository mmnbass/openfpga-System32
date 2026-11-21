#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Deterministic M1 test programs, restricted to what BOTH references
implement (upstream s32_v60 RTL and ref/v60_ref.py).

Encodings follow ref/v60_ref.py's decoder (decode_am, _exec_f12):
  F2 (byte1 bit7 = 0): byte1 = {0, modm, D, reg}; D=1: op1 = AM, op2 = reg
                                                   D=0: op1 = reg, op2 = AM
  F1 (byte1 bit7 = 1): byte1 = {1, modm1, modm2, 00000}; then AM1, AM2
  AM, modm 0: 0x00|r disp8[r]   0x20|r disp16[r]  0x40|r disp32[r]
              0x60|r [r]        0x80|r disp8[[r]] (deferred)
              0xE0|n #quick     0xF0/1/2 disp[PC] 0xF3 abs32  0xF4 #imm
  AM, modm 1: 0x60|r r          0x80|r [r+]       0xA0|r [-r]
              0x00|r d8[d8[r]] (double displacement)
Each program sets the registers it uses, runs, then stores R0-R15 and the
PSW (GETPSW) to scratch so every result is visible both in the final state
and in the ordered write stream, and ends with HALT.

Usage: m1_directed.py <out-dir>    writes <name>.bin/.hex for every test
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from m1_image import write  # noqa: E402

SCR = 0x8000        # scratch data
SP0 = 0xF000        # stack top
OUT = 0xC000        # final register / PSW dump

# opcode bases (B, H, W at +0, +2, +4) from ref F12 table
OP = {"MOV": (0x09, 0x1B, 0x2D), "ADD": (0x80, 0x82, 0x84), "SUB": (0xA8, 0xAA, 0xAC),
      "CMP": (0xB8, 0xBA, 0xBC), "AND": (0xA0, 0xA2, 0xA4), "OR": (0x88, 0x8A, 0x8C),
      "XOR": (0xB0, 0xB2, 0xB4), "NOT": (0x38, 0x3A, 0x3C), "NEG": (0x39, 0x3B, 0x3D),
      "MUL": (0x81, 0x83, 0x85), "MULU": (0x91, 0x93, 0x95), "DIV": (0xA1, 0xA3, 0xA5),
      "DIVU": (0xB1, 0xB3, 0xB5)}
SIZES = {"B": 0, "H": 1, "W": 2}
COND = {"V": 0, "NV": 1, "L": 2, "NL": 3, "Z": 4, "NZ": 5, "NH": 6, "H": 7,
        "N": 8, "P": 9, "BR": 10, "NOP": 11, "LT": 12, "GE": 13, "LE": 14, "GT": 15}


class A:
    def __init__(self):
        self.b = bytearray()
        self.labels, self.fix = {}, []

    def e(self, *bs):
        self.b += bytes(bs)

    def d32(self, v):
        self.b += struct.pack("<I", v & 0xFFFFFFFF)

    def label(self, n):
        self.labels[n] = len(self.b)

    # MOV.W #imm32, Rn   (F2 D=1, op1 = immediate)
    def li(self, rn, v):
        self.e(0x2D, 0x20 | rn, 0xF4)
        self.d32(v)

    # F2, D=1: OP.sz <AM>, Rd     am = bytes of the addressing mode, modm bit
    def f2_to_reg(self, op, sz, rd, am, modm=0):
        self.e(OP[op][SIZES[sz]], (modm << 6) | 0x20 | rd, *am)

    # F2, D=0: OP.sz Rs, <AM>
    def f2_from_reg(self, op, sz, rs, am, modm=0):
        self.e(OP[op][SIZES[sz]], (modm << 6) | rs, *am)

    # F1: OP.sz <AM1>, <AM2>
    def f1(self, op, sz, am1, modm1, am2, modm2):
        self.e(OP[op][SIZES[sz]], 0x80 | (modm1 << 6) | (modm2 << 5), *am1, *am2)

    def rr(self, op, sz, rs, rd):           # register-register via F2 D=1, modm 1
        self.f2_to_reg(op, sz, rd, [0x60 | rs], modm=1)

    def bcc8(self, cc, target):
        self.e(0x60 | COND[cc], 0)
        self.fix.append((len(self.b) - 2, target, 8))

    def bcc16(self, cc, target):
        self.e(0x70 | COND[cc], 0, 0)
        self.fix.append((len(self.b) - 3, target, 16))

    def getpsw_to(self, rn):                # GETPSW Rn (modm 1, register direct)
        self.e(0xF7, 0x60 | rn)

    def st_abs(self, rn, addr):             # MOV.W Rn, abs32
        self.e(0x2D, rn, 0xF3)
        self.d32(addr)

    def finish(self, nregs=16):
        self.getpsw_to(30)
        for i in range(nregs):
            self.st_abs(i, OUT + 4 * i)
        self.st_abs(30, OUT + 0xFC)
        self.e(0x00)                        # HALT
        for at, tgt, bits in self.fix:
            disp = self.labels[tgt] - at
            if bits == 8:
                assert -128 <= disp < 128, (tgt, disp)
                self.b[at + 1] = disp & 0xFF
            else:
                self.b[at + 1:at + 3] = struct.pack("<h", disp)
        return bytes(self.b)


CORNERS = [0, 1, 0x7F, 0x80, 0xFF, 0x7FFF, 0x8000, 0xFFFF, 0x7FFFFFFF, 0x80000000,
           0xFFFFFFFF, 0x12345678]


def t_alu_flags():
    """ADD/SUB/CMP/AND/OR/XOR/NOT/NEG at B/H/W on corner values, flags captured
    after every operation (GETPSW) and stored, so each flag result is checked."""
    a = A()
    a.li(31, SP0)
    k = 0
    for op in ("ADD", "SUB", "CMP", "AND", "OR", "XOR", "NOT", "NEG"):
        for sz in ("B", "H", "W"):
            for x, y in ((CORNERS[i], CORNERS[(i * 5 + 3) % len(CORNERS)]) for i in range(len(CORNERS))):
                a.li(1, x)
                a.li(2, y)
                a.rr(op, sz, 1, 2)          # R2 = R2 op R1 (op2 is the destination)
                a.getpsw_to(3)
                a.e(0x2D, 2, 0xF3); a.d32(SCR + 8 * k)
                a.e(0x2D, 3, 0xF3); a.d32(SCR + 8 * k + 4)
                k += 1
    return a.finish()


def t_muldiv():
    """MUL/MULU/DIV/DIVU at B/H/W, non-zero divisors."""
    a = A()
    a.li(31, SP0)
    k = 0
    vals = [(7, 3), (0xFFFFFFF9, 3), (0x12345678, 0x10), (0x80000000, 0xFFFFFFFF), (100, 0xFFFFFFF6),
            (0xFFFF, 0x7F), (0x7FFFFFFF, 2)]
    for op in ("MUL", "MULU", "DIV", "DIVU"):
        for sz in ("B", "H", "W"):
            for x, y in vals:
                a.li(1, y)                  # divisor / multiplier
                a.li(2, x)
                a.rr(op, sz, 1, 2)
                a.getpsw_to(3)
                a.e(0x2D, 2, 0xF3); a.d32(SCR + 8 * k)
                a.e(0x2D, 3, 0xF3); a.d32(SCR + 8 * k + 4)
                k += 1
    return a.finish()


def t_modes():
    """Loads and stores through every addressing mode both references
    implement, at B/H/W, including F1 memory-to-memory forms."""
    a = A()
    a.li(31, SP0)
    base = SCR + 0x400
    # seed a data block with distinct words
    for i in range(16):
        a.li(1, 0x11223344 + 0x01010101 * i)
        a.e(0x2D, 1, 0xF3); a.d32(base + 4 * i)
    a.li(4, base)                           # R4 = base pointer
    a.li(5, base + 0x20)                    # R5 = second pointer
    a.li(6, base + 0x80)                    # R6 = store area
    # disp8 / disp16 / disp32 [R4] loads at each size
    for sz, rd in (("B", 7), ("H", 8), ("W", 9)):
        a.f2_to_reg("MOV", sz, rd, [0x00 | 4, 0x05])                # disp8[R4]
        a.f2_to_reg("MOV", sz, rd + 3, [0x20 | 4] + list(struct.pack("<h", 0x0A)))   # disp16
        a.f2_to_reg("MOV", sz, rd + 6, [0x40 | 4] + list(struct.pack("<i", -2 + 0x10)))  # disp32
    # register indirect [R5], negative disp8, deferred d8[[R4]]-style via pointer
    a.f2_to_reg("MOV", "W", 1, [0x60 | 5])                          # [R5]
    a.f2_to_reg("MOV", "W", 2, [0x00 | 5, 0xFC])                    # -4[R5]
    a.li(3, base + 0x40)
    a.e(0x2D, 3, 0xF3); a.d32(base + 0x3C)                          # pointer cell -> base+0x40
    a.f2_to_reg("MOV", "W", 3, [0x80 | 4, 0x3C])                    # [disp8[R4]] deferred
    # autoincrement / autodecrement (modm 1)
    a.li(13, base)
    a.f2_to_reg("MOV", "W", 14, [0x80 | 13], modm=1)                # [R13+]
    a.f2_to_reg("MOV", "H", 15, [0x80 | 13], modm=1)
    a.f2_to_reg("MOV", "B", 0, [0xA0 | 13], modm=1)                 # [-R13]
    # stores: register to disp8[R6], [R6+], abs, at each size
    a.f2_from_reg("MOV", "B", 9, [0x00 | 6, 0x01])
    a.f2_from_reg("MOV", "H", 9, [0x00 | 6, 0x02])
    a.f2_from_reg("MOV", "W", 9, [0x00 | 6, 0x04])
    a.f2_from_reg("MOV", "W", 8, [0x80 | 6], modm=1)                # [R6+]
    a.f2_from_reg("MOV", "W", 7, [0xA0 | 6], modm=1)                # [-R6]
    # F1 memory to memory, and immediate quick / full to memory
    a.f1("MOV", "W", [0x00 | 4, 0x08], 0, [0x00 | 6, 0x10], 0)      # 8[R4] -> 16[R6]
    a.f1("ADD", "W", [0x00 | 4, 0x0C], 0, [0x00 | 6, 0x10], 0)      # 16[R6] += 12[R4] (RMW)
    a.f1("MOV", "W", [0xE7], 0, [0x00 | 6, 0x14], 0)                # #7 -> 20[R6]
    a.f1("MOV", "H", [0xF4, 0x34, 0x12], 0, [0x00 | 6, 0x18], 0)    # #0x1234 -> 24[R6]
    a.f1("SUB", "B", [0x00 | 4, 0x01], 0, [0x00 | 6, 0x1C], 0)      # byte RMW
    return a.finish()


def t_branches():
    """Every Bcc8 and Bcc16 condition, taken and not taken, forward and
    backward, plus a counted loop; R10 accumulates a path signature."""
    a = A()
    a.li(31, SP0)
    a.li(10, 0)
    flagsets = [(0, 0), (0, 1), (1, 0), (0xFFFFFFFF, 1), (0x7FFFFFFF, 0xFFFFFFFF), (0x80000000, 1)]
    n = 0
    for x, y in flagsets:
        for cc in [c for c in COND if c != "NOP"]:   # 0x6B/0x7B: see PROBES
            for wide in (False, True):
                a.li(1, y)
                a.li(2, x)
                a.rr("CMP", "W", 1, 2)          # flags from x - y
                tgt = f"t{n}"
                (a.bcc16 if wide else a.bcc8)(cc, tgt)
                a.li(11, 1 << (n % 31))
                a.rr("ADD", "W", 11, 10)        # not-taken path adds a bit
                a.label(tgt)
                n += 1
    # backward counted loop: R12 = 10 iterations, R13 accumulates
    a.li(12, 10)
    a.li(13, 0)
    a.li(14, 1)
    a.label("loop")
    a.rr("ADD", "W", 12, 13)
    a.rr("SUB", "W", 14, 12)
    a.bcc8("NZ", "loop")
    a.li(14, 3)
    a.label("loop16")
    a.rr("ADD", "W", 14, 13)
    a.rr("SUB", "W", 14, 14)                # R14 = 0 -> exits after one pass
    a.bcc16("NZ", "loop16")
    return a.finish()


def build_stack_calls():
    a = A()
    a.li(31, SP0)
    a.li(1, 0xAABBCCDD)
    a.e(0xEF, 0x60 | 1)                     # PUSH R1
    a.e(0xEE, 0xE5)                         # PUSH #5 (modm 0: immediate quick)
    a.e(0xE7, 0x60 | 2)                     # POP R2
    a.e(0xE7, 0x60 | 3)                     # POP R3
    a.e(0x48, 0, 0)                         # BSR disp16 -> sub
    a.fix.append((len(a.b) - 3, "sub", 16))
    a.li(5, 0x5555)
    a.bcc8("BR", "after")
    a.label("sub")
    a.li(4, 0x4444)
    a.e(0xE7, 0x60 | 20)                    # POP R20  (return address)
    a.e(0xD6, 0x60 | 20)                    # JMP [R20]  (RSR is not in v60_ref; RET pops AP too)
    a.label("after")
    a.e(0xE8, 0xF3)                         # JSR abs32 -> sub2
    a.d32(0)
    a.fix.append((len(a.b) - 6, "sub2", 32))
    a.li(8, 0x8888)
    a.bcc8("BR", "end")
    a.label("sub2")
    a.li(7, 0x7777)
    a.e(0xE7, 0x60 | 20)                    # POP R20
    a.e(0xD6, 0x60 | 20)                    # JMP [R20]
    a.label("end")
    a.li(9, 0)
    a.fix.append((len(a.b) - 6, "done", 32))
    a.e(0xD6, 0x60 | 9)                     # JMP [R9] (register indirect, modm 0)
    a.li(11, 0xDEAD)                        # skipped by the JMP
    a.label("done")
    # absolute fixups (32-bit) resolved here, others by finish()
    rel = [f for f in a.fix if f[2] != 32]
    for at, tgt, bits in [f for f in a.fix if f[2] == 32]:
        a.b[at + 2:at + 6] = struct.pack("<I", a.labels[tgt])
    a.fix = rel
    return a.finish()


def p_bcc_hole():
    """Opcodes 0x6B / 0x7B (condition field 11). Upstream RTL: reserved-
    instruction exception (vector 8), citing holes in MAME's primary dispatch
    table. v60_ref.py: Bcc 'never' (falls through). Expected to DIFFER."""
    a = A()
    a.li(31, SP0)
    a.li(1, 0x1111)
    a.e(0x6B, 0x02)                          # 'Bcc never' / reserved
    a.li(2, 0x2222)
    return a.finish()


def p_jmp_regdirect():
    """JMP with a register-direct operand (D7 69 = JMP R9). Upstream RTL
    (ReadAMAddress) uses the register *index* as the address; v60_ref.py uses
    the register's contents. Not a meaningful V60 form. Expected to DIFFER."""
    a = A()
    a.li(31, SP0)
    a.li(9, 0)
    tgt_fix = len(a.b) - 4
    a.e(0xD7, 0x60 | 9)
    a.li(1, 0xBAD)                           # reached only if the jump goes elsewhere
    a.label("t")
    a.li(2, 0x600D)
    a.b[tgt_fix:tgt_fix + 4] = struct.pack("<I", a.labels["t"])
    return a.finish()


def p_ret_frame():
    """BSR then RET #0. Upstream RTL: RET pops PC, then AP (R29), then adds the
    operand to SP (the CALL frame, per its comment and MAME's opRET). v60_ref.py:
    pops PC only. SP ends 4 apart. Expected to DIFFER."""
    a = A()
    a.li(31, SP0)
    a.li(1, 0x1234)
    a.e(0xEF, 0x60 | 1)                     # PUSH R1 (becomes the "AP" slot; modm 1)
    a.e(0x48, 0, 0)                         # BSR disp16 -> sub
    a.fix.append((len(a.b) - 3, "sub", 16))
    a.li(5, 0x5555)
    a.bcc8("BR", "end")
    a.label("sub")
    a.e(0xE2, 0xE0)                         # RET #0
    a.label("end")
    return a.finish()


def p_movea_value():
    """MOVEA with a direct-address source (R3a, docs/r3-movea-plan.md section 2): `movea.b /0x20, R1` with the byte 0x5A at
    address 0x20. The manual, MAME and upstream store the ADDRESS (R1 = 0x20, no data read); the pinned v60_ref.py decodes
    operand 1 as a VALUE (R1 = 0x5A, one data read). Expected to DIFFER. Retained, not fixed (v60_ref.py is a pinned
    copy); ENFORCED by m1_probe_movea.py (specific values, both runs successful), which run_refs.sh calls."""
    import m1_probe_movea as PV
    return PV.image()


# Programs whose references are documented to disagree (not part of the
# agreement suite; run_refs.sh re-checks that they still differ as recorded).
PROBES = {
    "probe_bcc_hole": p_bcc_hole,
    "probe_jmp_regdirect": p_jmp_regdirect,
    "probe_ret_frame": p_ret_frame,
    "probe_movea_value": p_movea_value,
}

TESTS = {
    "alu_flags": t_alu_flags,
    "muldiv": t_muldiv,
    "modes": t_modes,
    "branches": t_branches,
    "stack_calls": build_stack_calls,
}


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    for name, fn in TESTS.items():
        img = fn()
        write(img, str(out / name))
        print(f"{name}: {len(img)} bytes")
    pout = out / "probes"
    pout.mkdir(exist_ok=True)
    for name, fn in PROBES.items():
        img = fn()
        write(img, str(pout / name))
        print(f"{name}: {len(img)} bytes (probe)")


if __name__ == "__main__":
    main()
