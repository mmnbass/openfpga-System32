#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tiny microassembler for the V2 control word (field layout: rtl/v2_pkg.svh).

Used only to (a) build the smoke-test microprogram and (b) generate the
pseudo-random contents for the ROM control-store variant. It is not a V60
microprogram: V2 implements no instruction semantics.

Usage:
  microasm.py smoke   <out_dir>   writes smoke_cstore.hex and smoke_expected.hex
  microasm.py random  <out_dir> [depth]   writes v2_cstore.hex, v2_dispatch.hex,
                                          v2_mode.hex (deterministic, seed 60)
"""
import random
import sys
from pathlib import Path

FIELDS = {  # name: (lsb, width)
    "NXT": (0, 3), "COND": (3, 4), "CPOL": (7, 1), "JADDR": (8, 12),
    "ASEL": (20, 4), "BSEL": (24, 4), "RA_SEL": (28, 2), "RB_SEL": (30, 2),
    "RCONST": (32, 5), "ALU_OP": (37, 4), "SIZE_SEL": (41, 2), "SH_OP": (43, 3),
    "RES_SEL": (46, 4), "FLAG_UPD": (50, 4), "WR_DST": (54, 3), "WR_ASEL": (57, 2),
    "TADDR": (59, 4), "BE_MODE": (63, 2), "AGU_BASE": (65, 3), "AGU_OFF": (68, 3),
    "AGU_IDX": (71, 1), "AGU_STEP": (72, 2), "BUS_OP": (74, 2), "BUS_SIZE": (76, 2),
    "MAR_LD": (78, 1), "FWD_EN": (79, 1), "CONSUME": (80, 4), "REDIRECT": (84, 1),
    "IMM_LD": (85, 1), "IMM_OFS": (86, 3), "IMM_SZ": (89, 2), "IMM_SX": (91, 1),
    "MD_START": (92, 1), "MD_SIGNED": (93, 1), "IRQ_ACK": (94, 1), "BCD_SUB": (95, 1),
    "COND_HI": (96, 1), "DEC_EA": (97, 1), "MODM_SEL": (98, 2), "RIDX": (100, 1),
}
CW_W = 101
# symbolic values
A = {"RA": 0, "RB": 1, "RP": 2, "RT": 3, "PC": 4, "MDR": 5, "IMM": 6, "PSW": 7,
     "SP": 8, "CONST": 9, "MAR": 10, "MDHI": 11, "MDLO": 12, "RES": 13, "VEC": 14, "ZERO": 15}
ALU = {"ADD": 0, "ADDC": 1, "SUB": 2, "SUBC": 3, "AND": 4, "OR": 5, "XOR": 6, "NOT": 7,
       "NEG": 8, "PASSA": 9, "PASSB": 10, "SEXT": 11, "ZEXT": 12, "TRUNC": 13, "INC": 14, "DEC": 15}
RES = {"ALU": 0, "SHIFT": 1, "AGU": 2, "AGUSTEP": 3, "BCD": 4, "UNPACK": 5, "PACK": 6,
       "BFINS": 7, "BFEXT": 8, "BFMASK": 9, "BFFIRST": 10, "BFBYTE": 11, "MDLO": 12,
       "MDHI": 13, "OPA": 14, "MDR": 15}
WR = {"NONE": 0, "GPR": 1, "TEMP": 2, "PRIV": 3, "PSW": 4, "MAR": 6, "MDR": 7}   # 5 reserved


def word(**f):
    w = 0
    for k, v in f.items():
        lsb, width = FIELDS[k]
        if not 0 <= v < (1 << width):
            raise ValueError(f"{k}={v} does not fit {width} bits")
        w |= v << lsb
    return w


def nop():
    return word()


def pat_word(addr):
    """ROM test pattern (must match tb_v2_smoke.sv): byte(a) = a[7:0] ^ 0xA5."""
    return sum(((((addr + i) & 0xFF) ^ 0xA5) << (8 * i)) for i in range(4))


def cond(c):
    """COND / COND_HI fields for micro-condition c (0..31)."""
    return {"COND": c & 15, "COND_HI": c >> 4}


def smoke():
    """Straight-line microprogram that exercises every V2 unit once and stores
    each result to RAM at 0x100, 0x104, ... (in order), then spins.

    Returns (words, expected_store_values, dispatch_table, mode_table). A result written back in E3 is
    readable by a microinstruction issued four steps later, so dependent
    steps are separated by three NOPs (gap()). Operations are done in place
    on temporaries: one control word names one temp (TADDR) for both its
    read and its write-back."""
    p = []

    def gap(n=3):
        p.extend([word()] * n)

    def emit(**f):
        f.setdefault("SIZE_SEL", 2)         # word
        f.setdefault("BE_MODE", 1)          # all four byte lanes
        p.append(word(**f))

    def temp_const(t, val):
        emit(ASEL=A["CONST"], RCONST=val, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"],
             WR_DST=WR["TEMP"], TADDR=t)

    def temp_from(t, src):                  # temp t <- A-bus source (no read of t)
        emit(ASEL=A[src], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=t)

    expected = []

    # constants
    for t, v in ((1, 7), (2, 12), (3, 7), (4, 29), (6, 10), (7, 8), (8, 2), (10, 16), (0, 16)):
        temp_const(t, v)
    emit(ASEL=A["CONST"], RCONST=5, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"],
         WR_DST=WR["GPR"], WR_ASEL=0)       # R5 = 5 (RCONST names value and register)
    gap()

    # ALU: temp1 = temp1 + 5 = 12, flags updated
    emit(ASEL=A["CONST"], RCONST=5, BSEL=A["RT"], TADDR=1, ALU_OP=ALU["ADD"],
         RES_SEL=RES["ALU"], FLAG_UPD=0xF, WR_DST=WR["TEMP"])
    expected.append(12)
    # shifter: temp2 = temp2 << 2 = 48
    emit(ASEL=A["RT"], TADDR=2, BSEL=A["CONST"], RCONST=2, SH_OP=0, RES_SEL=RES["SHIFT"],
         WR_DST=WR["TEMP"])
    expected.append(48)
    # pointer: temp0 = 16 << 4 = 256 (store base)
    emit(ASEL=A["RT"], TADDR=0, BSEL=A["CONST"], RCONST=4, SH_OP=0, RES_SEL=RES["SHIFT"],
         WR_DST=WR["TEMP"])
    # redirect target: temp10 = 16 << 2 = 64
    emit(ASEL=A["RT"], TADDR=10, BSEL=A["CONST"], RCONST=2, SH_OP=0, RES_SEL=RES["SHIFT"],
         WR_DST=WR["TEMP"])
    gap()

    # multiply: temp3 (7) * 5 = 35
    emit(ASEL=A["RT"], TADDR=3, BSEL=A["CONST"], RCONST=5, MD_START=1, ALU_OP=ALU["ADD"])
    gap(6)
    temp_from(3, "MDLO")
    expected.append(35)
    gap()

    # divide: temp4 (29) / 5 -> quotient 5 (temp4), remainder 4 (temp5)
    emit(ASEL=A["CONST"], RCONST=5, BSEL=A["RT"], TADDR=4, MD_START=1, ALU_OP=ALU["SUB"])
    gap(4)
    wait = len(p)
    p.append(word(NXT=2, COND=9, JADDR=wait + 2))    # divider done -> continue
    p.append(word(NXT=1, JADDR=wait))                # else poll again
    temp_from(4, "MDLO")
    temp_from(5, "MDHI")
    expected += [5, 4]
    gap()

    # address unit: temp6 (10) + 4 + (R5 << word) = 10 + 4 + 20 = 34
    emit(TADDR=6, RB_SEL=0, RCONST=5, AGU_BASE=6, AGU_OFF=5, AGU_IDX=1,
         RES_SEL=RES["AGU"], WR_DST=WR["TEMP"])
    expected.append(34)
    # BCD: 9 + temp7 (8) = 0x17 (decimal 17)
    emit(ASEL=A["CONST"], RCONST=9, BSEL=A["RT"], TADDR=7, RES_SEL=RES["BCD"],
         WR_DST=WR["TEMP"])
    expected.append(0x17)
    # bit-field mask: offset 4, length temp8 + 1 = 3 -> 0x70
    emit(BSEL=A["CONST"], RCONST=4, TADDR=8, RES_SEL=RES["BFMASK"], WR_DST=WR["TEMP"])
    expected.append(0x70)
    gap()

    # flags + conditional branch: 5 - 5 sets Z; branch on Z skips the fail marker
    emit(ASEL=A["CONST"], RCONST=5, BSEL=A["CONST"], ALU_OP=ALU["SUB"],
         RES_SEL=RES["ALU"], FLAG_UPD=0xF)
    gap(4)
    br = len(p)
    p.append(word(NXT=2, COND=2, JADDR=br + 3))
    temp_const(9, 31)                                # not taken: fail marker
    p.append(word(NXT=1, JADDR=br + 4))
    temp_const(9, 1)                                 # taken: pass marker
    expected.append(1)
    gap()

    # fetch window: two 4-byte immediates from the reset address, then redirect
    emit(IMM_LD=1, IMM_OFS=0, IMM_SZ=2, CONSUME=4)
    temp_from(11, "IMM")
    emit(IMM_LD=1, IMM_OFS=0, IMM_SZ=2, CONSUME=4)
    temp_from(12, "IMM")
    expected += [pat_word(0xFFFFFFF0), pat_word(0xFFFFFFF4)]
    emit(ASEL=A["RT"], TADDR=10, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], REDIRECT=1)
    gap(8)
    emit(IMM_LD=1, IMM_OFS=0, IMM_SZ=2, CONSUME=4)
    temp_from(13, "IMM")
    expected.append(pat_word(0x40))
    gap()

    # store temps 1..13 (temp6 was the AGU result etc.) to 0x100 + 4*i
    for t in (1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13):
        emit(ASEL=A["RT"], TADDR=0, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["MAR"])
        emit(TADDR=0, AGU_BASE=6, AGU_STEP=1, RES_SEL=RES["AGUSTEP"], WR_DST=WR["TEMP"])
        emit(ASEL=A["RT"], TADDR=t, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["MDR"])
        gap()
        emit(BUS_OP=2, BUS_SIZE=3)
        gap(1)

    # bus read-back of the first stored word, then store it once more
    temp_const(14, 16)
    gap()
    emit(ASEL=A["RT"], TADDR=14, BSEL=A["CONST"], RCONST=4, SH_OP=0, RES_SEL=RES["SHIFT"],
         WR_DST=WR["MAR"])
    gap()
    emit(BUS_OP=1, BUS_SIZE=3)
    gap(4)
    temp_from(15, "MDR")
    gap()
    emit(ASEL=A["RT"], TADDR=0, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["MAR"])
    emit(ASEL=A["RT"], TADDR=15, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["MDR"])
    gap()
    emit(BUS_OP=2, BUS_SIZE=3)
    expected.append(12)
    gap(1)

    # ---- decoder: opcode and addressing-mode dispatch with decoded fields ----
    # The fetch head is at 0x44 (the redirect test consumed 0x40-0x43), so the
    # window holds pattern bytes E1 E0 E3 E2 ED EC EF EE.
    dtab, mtab = {}, {}

    def store_from(src, **extra):           # next RAM word <- A-bus source
        emit(TADDR=0, AGU_BASE=6, AGU_STEP=1, RES_SEL=RES["AGUSTEP"], WR_DST=WR["TEMP"])
        gap()
        emit(ASEL=A["RT"], TADDR=0, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["MAR"])
        emit(ASEL=A[src], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["MDR"], **extra)
        gap()
        emit(BUS_OP=2, BUS_SIZE=3)
        gap(1)

    def br_check(t, c, taken):              # temp t <- 1 if micro-condition c behaves as expected
        b = len(p)
        p.append(word(NXT=2, JADDR=b + 3, **cond(c)))
        temp_const(t, 31 if taken else 1)       # fall-through
        p.append(word(NXT=1, JADDR=b + 4))
        temp_const(t, 1 if taken else 31)       # taken
        expected.append(1)

    # GPRs the decoded register fields will name: R1 = 1, R0 = temp7 (0x17)
    emit(ASEL=A["CONST"], RCONST=1, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"],
         WR_DST=WR["GPR"], WR_ASEL=0)
    emit(ASEL=A["RT"], TADDR=7, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"],
         WR_DST=WR["GPR"], WR_ASEL=0, RCONST=0)
    gap()

    # opcode 0xE1: size1 = half, size2 = word, field layout 2 (register in the
    # opcode byte: fld1 = byte0[4:0] = 1, fld2 = byte1[4:0] = 0), two operands,
    # operand 2 written
    fmt = 0x1 | (0x2 << 2) | (0b010 << 4) | (1 << 8) | (1 << 10)
    d = len(p)
    p.append(word(NXT=5))
    dtab[0xE1] = (fmt << 12) | (d + 1)
    emit(IMM_LD=1, IMM_OFS=0, IMM_SZ=3, SIZE_SEL=0)          # by decoded size1: half
    temp_from(1, "IMM")
    emit(IMM_LD=1, IMM_OFS=0, IMM_SZ=3, SIZE_SEL=1)          # by decoded size2: word
    temp_from(2, "IMM")
    emit(RA_SEL=1, ASEL=A["RA"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"],
         WR_DST=WR["TEMP"], TADDR=3)                         # R[fld1] = R1 = 1
    emit(RA_SEL=2, ASEL=A["RA"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"],
         WR_DST=WR["TEMP"], TADDR=4)                         # R[fld2] = R0 = 0x17
    expected += [0xE0E1, 0xE2E3E0E1, 1, 0x17]
    br_check(5, 17, True)                                    # two operands
    br_check(6, 18, False)                                   # operand 1 not written
    br_check(7, 19, True)                                    # operand 2 written
    gap()
    for t in (1, 2, 3, 4, 5, 6, 7):
        store_from("RT", TADDR=t)

    # format byte (M1.1 N1): byte 1 = E0, so F1 (bit 7) and D (bit 5) are set
    br_check(11, 24, True)
    br_check(12, 25, True)
    gap()
    store_from("RT", TADDR=11)
    store_from("RT", TADDR=12)

    # consume the opcode; the mode byte E0 is now at the head:
    # byte displacement, base = temp (AGU base 6), deferred
    emit(CONSUME=1)
    # instruction-start PC (M1.1 N5): the head has moved to 0x45; the PC
    # source is still the opcode's address, 0x44
    temp_from(13, "PC")
    expected.append(0x44)
    gap()
    store_from("RT", TADDR=13)
    gap(2)
    # mode-table bank (M1.1 N2): the lookup for this dispatch selects bank
    # ifl[6] = 1, i.e. entry 0x1E0; the bank-0 entry 0x0E0 is a decoy that
    # spins, so a lost bank bit ends in a timeout. The dispatch word repeats
    # the lookup's IMM_OFS / MODM_SEL: if it stalls, the table is re-read
    # with its own fields every cycle
    emit(MODM_SEL=1)
    desc = (1 << 2) | (6 << 6) | (1 << 11)
    m = len(p)
    p.append(word(NXT=6, MODM_SEL=1))
    mtab[0x100 | 0xE0] = (desc << 12) | (m + 1)
    br_check(1, 23, True)                                    # deferred
    br_check(2, 22, False)                                   # not double displacement
    br_check(3, 14, False)                                   # not register direct
    emit(IMM_LD=1, IMM_OFS=0, DEC_EA=1)                      # displacement: byte after the mode byte, signed
    temp_from(4, "IMM")
    gap()
    emit(DEC_EA=1, TADDR=14, RES_SEL=RES["AGU"], WR_DST=WR["TEMP"])   # temp14 (16) + disp (-29)
    emit(CONSUME=10)                                         # specifier header: mode byte + 1-byte disp
    # mode-byte register (M1.1 N4): latched at the mode dispatch (E0 -> R0),
    # still R0 after the specifier was consumed (the head byte is now E2)
    emit(RA_SEL=3, ASEL=A["RA"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"],
         WR_DST=WR["TEMP"], TADDR=15)
    emit(IMM_LD=1, IMM_OFS=0, IMM_SZ=0)                      # next byte at the head
    temp_from(5, "IMM")
    expected += [0xFFFFFFE3, 0xFFFFFFF3, 0xE2, 0x17]
    gap()
    for t in (1, 2, 3, 4):
        store_from("RT", TADDR=t)
    store_from("RT", TADDR=14)
    store_from("RT", TADDR=5)
    store_from("RT", TADDR=15)

    # mode-byte register lifetime (M1.1 N4), with a nonzero register: R2 = 2,
    # R3 = 3; mode dispatch on the head byte E2 (register 2, bank 0), consume
    # it, wait two cycles (a free-running register would have moved on to
    # the next byte, ED), then read RA_SEL = 3: must still be R2
    for r in (2, 3):
        emit(ASEL=A["CONST"], RCONST=r, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"],
             WR_DST=WR["GPR"], WR_ASEL=0)
    gap()
    m2 = len(p)
    p.append(word(NXT=6))
    mtab[0xE2] = m2 + 1
    emit(CONSUME=1)
    gap(2)
    emit(RA_SEL=3, ASEL=A["RA"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"],
         WR_DST=WR["TEMP"], TADDR=15)
    expected.append(2)
    gap()
    store_from("RT", TADDR=15)

    # register index from a temp (B3 RIDX): temp 9 = 3; a word with the same TADDR, then a RIDX word reading R[temp9[4:0]] = R3 = 3 into temp 14;
    # then a RIDX write: R[temp9] <- 21 (RCONST as the value), read back through R3 by constant address
    temp_const(9, 3)
    gap()
    emit(TADDR=9, ASEL=A["RT"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
    emit(TADDR=9, RIDX=1, ASEL=A["RA"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"]) 
    gap()
    expected.append(3)
    store_from("RT", TADDR=9)
    temp_const(9, 3)
    gap()
    emit(TADDR=9, ASEL=A["RT"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"])
    emit(TADDR=9, RIDX=1, ASEL=A["CONST"], RCONST=21, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["GPR"])
    gap()
    emit(RA_SEL=0, RCONST=3, ASEL=A["RA"], ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=14)
    expected.append(21)
    gap()
    store_from("RT", TADDR=14)

    spin = len(p)
    p.append(word(NXT=1, JADDR=spin))
    decoy = len(p)
    p.append(word(NXT=1, JADDR=decoy))
    mtab[0xE0] = decoy
    return p, expected, dtab, mtab


def write_hex(path, words, width_hex):
    path.write_text("\n".join(f"{w:0{width_hex}x}" for w in words) + "\n")


def main():
    mode, out = sys.argv[1], Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    if mode == "smoke":
        prog, expected, dtab, mtab = smoke()
        write_hex(out / "smoke_cstore.hex", prog, (CW_W + 3) // 4)
        write_hex(out / "smoke_dispatch.hex", [dtab.get(i, 0) for i in range(256)], 6)
        write_hex(out / "smoke_mode.hex", [mtab.get(i, 0) for i in range(512)], 6)
        write_hex(out / "smoke_expected.hex", expected, 8)
        write_hex(out / "smoke_count.hex", [len(expected)], 8)
        print(f"smoke microprogram: {len(prog)} words, {len(expected)} expected stores")
    elif mode == "random":
        depth = int(sys.argv[3]) if len(sys.argv) > 3 else 2048
        rng = random.Random(60)
        write_hex(out / "v2_cstore.hex", [rng.getrandbits(CW_W) for _ in range(depth)], (CW_W + 3) // 4)
        write_hex(out / "v2_dispatch.hex", [rng.getrandbits(24) for _ in range(256)], 6)
        write_hex(out / "v2_mode.hex", [rng.getrandbits(24) for _ in range(512)], 6)
        print(f"random ROM contents: control store {depth} x {CW_W}, tables 256 + 512 x 24")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
