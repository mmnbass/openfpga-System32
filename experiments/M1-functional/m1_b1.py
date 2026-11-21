#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""B1 (CPU batch 1) directed vectors: a handful per new form, against V60X (m1_ref.py --ext) and the pinned upstream s32_v60, on the M1
benches at 4 fetch configurations. Exploratory standard: no frozen-expectation generator; the references ARE the expectation
(agree = V2 halts with the same final state and the same lock-step records as both references; unsupported = V2 traps the stated kind
at the stated instruction after retiring the same prefix and leaving the same state).

New forms: MOV.B [Rn+],[Rm+] (09); DBR Rn,disp16 (C6 A1); PUSH #q (EE); MOVEA.B <abs|disp32[PC]>,[-Rn] (40, Format II); RETIS #q (FA);
JSR /abs (E8 F3).  Run via run_m11.sh:  M11_GEN=.../m1_b1.py M11_REF_FLAGS=--ext M11_OUT=<new dir> M11_LABEL=B1 run_m11.sh 0 <configs>

  m1_b1.py <out-dir> [nrandom]      writes <name>.{bin,hex}, m11_list.txt, m11_smc_list.txt (empty)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from m1_image import write  # noqa: E402
from m1_m11 import P, MOVW  # noqa: E402

ADD = 0x84
MOVB, DBR, PUSH, MOVEA, RETIS, JSR = 0x09, 0xC6, 0xEE, 0x40, 0xFA, 0xE8


def le32(v):
    return list((v & 0xFFFFFFFF).to_bytes(4, "little"))


def put(p, addr, bs):
    assert len(p.b) <= addr, (hex(len(p.b)), hex(addr))
    p.b += bytes(addr - len(p.b)) + bytes(bs)


def movb(p, r1, r2):                    # mov.b [Rr1+], [Rr2+]
    p.ins(MOVB, 0xE0, 0x80 | r1, 0x80 | r2)


def setr(p, r, v):
    p.f2d1(P.imm(v), r)


def directed():
    D = {}
    # ---- MOV.B [Rn+],[Rm+] ----
    p = P(); setr(p, 10, 0x200); setr(p, 11, 0x300); movb(p, 10, 11); p.halt(); put(p, 0x200, [0xA5]); D["b1_movb_basic"] = p
    p = P(); setr(p, 10, 0x200); setr(p, 11, 0x302)                     # the four byte lanes, values with the sign bit
    for _ in range(4):
        movb(p, 10, 11)
    p.halt(); put(p, 0x200, [0x00, 0x80, 0xFF, 0x7F]); D["b1_movb_lanes"] = p
    p = P(); setr(p, 10, 0x201); setr(p, 11, 0x203)                     # odd source, odd destination, then crossing a word
    for _ in range(3):
        movb(p, 10, 11)
    p.halt(); put(p, 0x200, [0x11, 0x22, 0x33, 0x44, 0x55]); D["b1_movb_odd"] = p
    p = P(); setr(p, 10, 0x200); setr(p, 11, 0x201)                     # overlapping copy: the first byte propagates
    for _ in range(4):
        movb(p, 10, 11)
    p.halt(); put(p, 0x200, [0x5A, 0, 0, 0, 0]); D["b1_movb_overlap"] = p
    p = P(); setr(p, 10, 0x200); setr(p, 11, 0x300); setr(p, 1, 4)      # copy loop: mov.b + dbr
    loop = len(p.b); movb(p, 10, 11)
    p.ins(DBR, 0xA1, (loop - len(p.b)) & 0xFF, 0xFF if loop - len(p.b) < 0 else 0x00); p.halt(); put(p, 0x200, [1, 2, 3, 4]); D["b1_movb_loop"] = p
    p = P(); setr(p, 31, 0x300); setr(p, 11, 0x400); setr(p, 20, 0x200)  # R31 (SP) and a high register as pointers
    movb(p, 20, 11); movb(p, 31, 11); p.halt(); put(p, 0x200, [0xC3]); put(p, 0x300, [0x3C]); D["b1_movb_sp_hi"] = p
    # ---- DBR ----
    p = P(); setr(p, 1, 3); setr(p, 2, 0); loop = len(p.b)
    p.f2d1(P.quick(1), 2, ADD)
    p.ins(DBR, 0xA1, (loop - len(p.b)) & 0xFF, 0xFF); p.halt(); D["b1_dbr_loop3"] = p
    p = P(); setr(p, 1, 1); setr(p, 2, 7); p.ins(DBR, 0xA1, 0x10, 0x00); p.f2d1(P.quick(5), 2); p.halt(); D["b1_dbr_fall"] = p
    p = P(); setr(p, 1, 0); setr(p, 2, 7)                              # 0 - 1 = 0xFFFFFFFF != 0: taken forward over the mov
    p.ins(DBR, 0xA1, 0x07, 0x00); p.f2d1(P.quick(5), 2); p.halt(); D["b1_dbr_wrap_fwd"] = p
    p = P(); setr(p, 0, 2); setr(p, 2, 0); p.ins(DBR, 0xA0, 0x07, 0x00); p.f2d1(P.quick(5), 2); p.halt(); D["b1_dbr_r0"] = p
    p = P(); setr(p, 30, 2); setr(p, 2, 0); p.ins(DBR, 0xBE, 0x07, 0x00); p.f2d1(P.quick(5), 2); p.halt(); D["b1_dbr_r30_fwd"] = p
    p = P(); setr(p, 1, 2); st = len(p.b); p.ins(DBR, 0xA1, 0x00, 0x01); p.f2d1(P.quick(5), 2); p.halt(); put(p, st + 0x100, [0x00]); D["b1_dbr_far"] = p   # disp +0x100
    # ---- PUSH #q ----
    p = P(); setr(p, 31, 0x400)
    for q in (0, 15, 7):
        p.ins(PUSH, 0xE0 | q)
    p.halt(); D["b1_push_basic"] = p
    p = P(); setr(p, 31, 4); p.ins(PUSH, 0xE5); p.halt(); D["b1_push_to_zero"] = p
    p = P(); setr(p, 31, 0x401); p.ins(PUSH, 0xE9); p.ins(PUSH, 0xEF); p.halt(); D["b1_push_unaligned"] = p
    # ---- MOVEA.B <src>, [-Rn] (Format II) ----
    p = P(); setr(p, 31, 0x400); p.ins(MOVEA, 0xA0, 0xF3, *le32(0x1234), 0xBF); p.halt(); D["b1_movea_abs_sp"] = p
    p = P(); setr(p, 31, 0x400); p.ins(MOVEA, 0xA0, 0xF3, *le32(0xDEADBEEF), 0xBF); p.halt(); D["b1_movea_abs_full_word"] = p
    p = P(); setr(p, 31, 0x400); p.ins(MOVEA, 0xA0, 0xF2, *le32(0x70), 0xBF); p.halt(); D["b1_movea_pcrel_sp"] = p
    p = P(); setr(p, 31, 0x400); setr(p, 5, 0x500); p.ins(MOVEA, 0xA0, 0xF2, *le32((-0x20) & 0xFFFFFFFF), 0xA5); p.halt(); D["b1_movea_pcrel_neg_r5"] = p
    p = P(); setr(p, 31, 0x400); p.ins(MOVEA, 0xA0, 0xF3, *le32(0x11111111), 0xBF); p.ins(MOVEA, 0xA0, 0xF3, *le32(0x22222222), 0xBF); p.halt(); D["b1_movea_twice"] = p
    # ---- RETIS #q ---- (the frame: [SP] = return PC, [SP+4] = PSW; code at 0x100)
    def retis_prog(q, psw, sp=0x500):
        p = P(); setr(p, 31, sp); p.ins(RETIS, 0xE0 | q)
        put(p, 0x100, []); p.ins(MOVW, 0x22, 0xE1); p.halt()                    # mov.w #1, R2; halt at 0x100
        put(p, sp, le32(0x100) + le32(psw))
        return p
    D["b1_retis_isp_to_l0"] = retis_prog(0, 0x00000000)                          # the boot's case: IS 1 -> 0, EL 0
    D["b1_retis_count3"] = retis_prog(3, 0x00000000)
    D["b1_retis_flags_ie"] = retis_prog(0, 0x0004000F)                           # flags and IE set; IS 0, EL 0
    D["b1_retis_same_bank"] = retis_prog(2, 0x10000005)                          # IS stays 1: no stack switch
    D["b1_retis_count15"] = retis_prog(15, 0x00000000)
    # ---- JSR /abs ----
    for k in (0, 3, 5):                                                          # start alignments (return address = start + 6)
        p = P(); setr(p, 31, 0x400)
        for _ in range(k):
            p.nop()
        p.ins(JSR, 0xF3, *le32(0x100)); p.halt()
        put(p, 0x100, []); p.ins(MOVW, 0x22, 0xE1); p.halt()
        D[f"b1_jsr_abs_a{k}"] = p
    p = P(); setr(p, 31, 0x400); p.ins(JSR, 0xF3, *le32(0x100)); p.halt()        # nested: the callee calls again
    put(p, 0x100, []); p.ins(JSR, 0xF3, *le32(0x180)); p.halt()
    put(p, 0x180, []); p.ins(MOVW, 0x22, 0xE1); p.halt(); D["b1_jsr_nested"] = p
    return D


def unsupported():
    """(name, program, index of the unsupported instruction, kind): V2 traps there with the references' state before it, no side effect"""
    U = []

    def pre(sp=0x400):
        p = P(); setr(p, 10, 0x200); setr(p, 11, 0x300); setr(p, 5, 0x500); setr(p, 31, sp)
        return p
    p = pre(); p.ins(MOVB, 0xE0, 0x8A, 0x65); p.halt(); U.append(("b1_u_movb_op2_reg", p, 4, "mode"))                       # mov.b [R10+], R5
    p = pre(); p.ins(MOVB, 0xE0, 0x65, 0x8B); p.halt(); U.append(("b1_u_movb_op1_reg", p, 4, "mode"))                       # mov.b R5, [R11+]
    p = pre(); p.ins(MOVB, 0xE0, 0xAA, 0x8B); p.halt(); U.append(("b1_u_movb_op1_autodec", p, 4, "mode"))                   # mov.b [-R10], [R11+]
    p = pre(); p.ins(MOVB, 0xE0, 0x8A, 0xAB); p.halt(); U.append(("b1_u_movb_op2_autodec", p, 4, "mode"))                   # mov.b [R10+], [-R11]
    p = pre(); p.ins(MOVB, 0xA0, 0x8A, 0x8B); p.halt(); U.append(("b1_u_movb_f1_bank0_op1", p, 4, "mode"))                  # op1 in bank 0 ([R10+] is not a bank-0 mode)
    p = pre(); p.ins(MOVB, 0x2A, 0x8B); p.halt(); U.append(("b1_u_movb_format1", p, 4, "mode"))                              # Format I (bit 7 clear)
    p = pre(); p.ins(DBR, 0x81, 0x00, 0x01); p.halt(); U.append(("b1_u_dbl", p, 4, "opcode"))                              # DBL (C6 sub-opcode 4)
    p = pre(); p.ins(0xC7, 0xA1, 0x00, 0x01); p.halt(); U.append(("b1_u_c7", p, 4, "opcode"))                              # C7 table
    p = pre(); p.ins(PUSH, 0xF4, *le32(0x12345678)); p.halt(); U.append(("b1_u_push_imm32", p, 4, "mode"))                   # push #imm32
    p = pre(); p.ins(0xEF, 0x65); p.halt(); U.append(("b1_u_push_modm1", p, 4, "opcode"))                                    # EF
    p = pre(); p.ins(MOVEA, 0xA0, 0xF3, *le32(0x1234), 0x85); p.halt(); U.append(("b1_u_movea_dst_autoinc", p, 4, "mode"))   # movea.b /abs, [R5+]
    p = pre(); p.ins(MOVEA, 0xA0, 0xF3, *le32(0x1234), 0x65); p.halt(); U.append(("b1_u_movea_dst_reg", p, 4, "mode"))       # movea.b /abs, R5 (Format II)
    p = pre(); p.ins(MOVEA, 0xE0, 0x65, 0xBF); p.halt(); U.append(("b1_p_movea_src_reg", p, 4, "mode"))                      # movea.b R5, [-SP]
    p = pre(); p.ins(MOVEA, 0xE0, 0x8A, 0xBF); p.halt(); U.append(("b1_u_movea_src_autoinc_f2", p, 4, "mode"))               # movea.b [R10+], [-SP]
    p = pre(); p.ins(MOVEA, 0x6A, 0x8A); p.halt(); U.append(("b1_u_movea_f1fmt_src_autoinc", p, 4, "mode"))                  # movea.b [R10+], R10 (Format I, m = 1)
    p = pre(); p.ins(0x40, 0x20 | 10, 0xE0 | 3); p.halt(); U.append(("b1_p_movea_src_quick", p, 4, "mode"))                  # movea.b #3, R10
    p = pre(0x500); p.ins(RETIS, 0xE0); put(p, 0x500, le32(0x100) + le32(0x01000000)); U.append(("b1_u_retis_to_l1", p, 4, "mode"))   # IS 1 -> 0, EL 1: L1SP bank
    p = pre(0x500); p.ins(RETIS, 0xE0); put(p, 0x500, le32(0x100) + le32(0x13000000)); U.append(("b1_u_retis_is_el", p, 4, "mode"))  # IS 1 -> 1, EL changes
    p = pre(0x500); p.ins(RETIS, 0x65); put(p, 0x500, le32(0x100) + le32(0)); U.append(("b1_u_retis_reg", p, 4, "mode"))               # retis R5
    p = pre(); p.ins(JSR, 0xF2, *le32(0x70)); p.halt(); U.append(("b1_u_jsr_pcrel", p, 4, "mode"))                         # jsr d[PC]
    p = pre(); p.ins(0xE9, 0x65); p.halt(); U.append(("b1_p_jsr_modm1", p, 4, "opcode"))                                    # E9
    p = pre(); p.ins(JSR, 0xF4, *le32(0x100)); p.halt(); U.append(("b1_p_jsr_imm", p, 4, "mode"))                           # jsr #imm32
    return U


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    lines = []
    for name, p in directed().items():
        write(bytes(p.b), str(out / name)); lines.append(f"agree {name}")
    for name, p, n, kind in unsupported():
        write(bytes(p.b), str(out / name))
        # b1_p_*: the references (V60X / MAME semantics vs upstream) DISAGREE on the unsupported instruction itself (Rn / #q sources of MOVEA, JSR with
        # Rn / #imm: the manual marks them illegal, S/R3 plan D2 / D3), so they are documented probes; V2 must still trap the stated kind with the same prefix
        lines.append(f"{'probe' if name.startswith('b1_p_') else 'unsupported'} {name} {n} {kind} {p.starts[n]:08x}")
    (out / "m11_list.txt").write_text("\n".join(lines) + "\n")
    (out / "m11_smc_list.txt").write_text("")
    print(f"b1: {len(lines)} programs")


if __name__ == "__main__":
    main()
