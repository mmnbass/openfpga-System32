#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Plausible incorrect front-end implementations for M1.1.

RTL mutants change one decoder / sequencing behaviour of v2_core; microcode
mutants change one rule of m1_m11_microcode.py. run_m11_mutants.sh requires
the M1.1 V2 suite (run_m11.sh: lock-step against both references, trap
kinds and PCs, SIMCHECK) to fail on every one, so the suite is shown to tell
the correct front end from these near misses. The smoke-level wiring
mutations are in experiments/V2-datapath-floor/sim/run_wiring_mutations.py.

    m11_mutants.py <rtl-dir> <out-dir> [--set m12]   writes <out-dir>/<name>/rtl or mcgen.py
    m11_mutants.py --list [--set m12]                prints "<name> <rtl|mc> <what>"

--set m12 selects the M1.2 mutants (ALU and PSW properties), run against
the M1.2 program set (m1_m12.py); the default set is M1.1's front end.
"""
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LATCH = "            mreg    <= mbyte_q[4:0];      // stable while the specifier is consumed (M1.1 N4)"

# Mutants that are equivalent under the M1.1 microcode (the suite cannot and
# need not catch them); listed with the reason and where they ARE caught.
EQUIVALENT = {
    "mreg_free": "every M1.1 operand routine reads the mode-byte register in the word that "
                 "consumes its specifier, so a free-running register still holds it; N4 "
                 "matters once microcode uses it after consumption (M1.3 autoincrement). "
                 "Caught by the smoke test (run_wiring_mutations.py).",
}

# name: (kind, file, what, [(old, new), ...]); every old must occur exactly once
MUTANTS = {
    # ---- RTL ----
    "ifl_byte2": ("rtl", "v2_useq.sv", "N1 format byte latched from byte 2",
                  [("            ifl <= ib[15:8];", "            ifl <= ib[23:16];")]),
    "f1_bit6": ("rtl", "v2_core.sv", "N1 F1 condition reads bit 6",
                [("assign ucond[24] = ifl[7];", "assign ucond[24] = ifl[6];")]),
    "d_bit6": ("rtl", "v2_core.sv", "N1 D condition reads bit 6",
               [("assign ucond[25] = ifl[5];", "assign ucond[25] = ifl[6];")]),
    "quick_any_imm": ("rtl", "v2_core.sv", "N1 quick condition true for every immediate",
                      [("assign ucond[26] = ea_desc[1] & (ea_desc[3:2] == 2'd0);", "assign ucond[26] = ea_desc[1];")]),
    "modm_swapped": ("rtl", "v2_useq.sv", "N2 MODM_SEL 1 and 2 swapped",
                     [("wire        modm      = (modm_sel == 2'd1) ? ifl[6] : (modm_sel == 2'd2) ? ifl[5] :",
                       "wire        modm      = (modm_sel == 2'd1) ? ifl[5] : (modm_sel == 2'd2) ? ifl[6] :")]),
    "ready_offset0": ("rtl", "v2_core.sv", "N3 decode-ready ignores IMM_OFS (offset 0)",
                      [("assign ucond[27] = rdy_q[cw[`CW_IMM_OFS]];", "assign ucond[27] = rdy_q[0];")]),
    "ready_always": ("rtl", "v2_core.sv", "N3 decode-ready always true",
                     [("assign ucond[27] = rdy_q[cw[`CW_IMM_OFS]];", "assign ucond[27] = 1'b1;")]),
    "ready_prev_word": ("rtl", "v2_core.sv", "N3 decode-ready from the previous word's IMM_OFS (the defect found in M1.1d)",
                        [("        rdy_q[rk] <= !rst && settled && (avail > rk[5:0]);",
                          "        rdy_q[rk] <= !rst && settled && (avail > {3'd0, cw[`CW_IMM_OFS]});")]),
    "ready_unsettled": ("rtl", "v2_core.sv", "N3 decode-ready ignores consume/redirect (head moving)",
                        [("wire settled = (consume_eff == 4'd0) && !(adv && cw3[`CW_REDIRECT]) && !data_busy\n"
                          "               && cw[`CW_BUS_OP] == 2'd0\n"
                          "               && cw1[`CW_BUS_OP] == 2'd0 && cw2[`CW_BUS_OP] == 2'd0 && cw3[`CW_BUS_OP] == 2'd0\n"
                          "               && !bus_start_q;",
                          "wire settled = 1'b1;")]),
    "ready_ignores_pending_store": ("rtl", "v2_core.sv",
                                    "N3 decode-ready ignores a data access still on its way to the bus (the C2-1 defect)",
                                    [("               && cw[`CW_BUS_OP] == 2'd0\n"
                                      "               && cw1[`CW_BUS_OP] == 2'd0 && cw2[`CW_BUS_OP] == 2'd0 && cw3[`CW_BUS_OP] == 2'd0\n"
                                      "               && !bus_start_q;", "               ;")]),
    "ready_ignores_issuing_store": ("rtl", "v2_core.sv",
                                    "N3 decode-ready ignores a store in the issuing word (C2 round 2)",
                                    [("               && cw[`CW_BUS_OP] == 2'd0\n", "")]),
    "mreg_never": ("rtl", "v2_useq.sv", "N4 mode-byte register never latched", [(LATCH, "            mreg    <= mreg;")]),
    "mreg_free": ("rtl", "v2_useq.sv", "N4 mode-byte register free-running (pre-M1.1)",
                  [(LATCH, ""), ("        if (dispatch) begin\n            ifl <= ib[15:8];",
                                 "        mreg <= mode_byte[4:0];\n        if (dispatch) begin\n            ifl <= ib[15:8];")]),
    "ipc_every_cycle": ("rtl", "v2_core.sv", "N5 instruction-start PC follows the head every cycle",
                        [("    else if (adv && cw[`CW_NXT] == 3'd5) ipc <= head_pc;", "    else ipc <= head_pc;")]),
    "pc_is_head": ("rtl", "v2_core.sv", "N5 PC source is the fetch head (BR8 relative to the head)",
                   [(".rt(rt_data), .pc(ipc), .mdr(mdr),", ".rt(rt_data), .pc(head_pc), .mdr(mdr),")]),
    # ---- microcode ----
    "mc_disp_ofs0": ("mc", None, "dispatch waits for the opcode only (format byte not awaited)",
                     [("    poll(1, MS_ZERO)\n    p.append(word(NXT=5, IMM_OFS=1))",
                       "    poll(0, MS_ZERO)\n    p.append(word(NXT=5, IMM_OFS=0))")]),
    "mc_disp_no_poll": ("mc", None, "dispatch without waiting for decode-ready",
                        [("    poll(1, MS_ZERO)\n    p.append(word(NXT=5, IMM_OFS=1))", "    p.append(word(NXT=5, IMM_OFS=1))")]),
    "mc_stub_no_msel": ("mc", None, "mode stub does not repeat MODM_SEL",
                        [("        p.append(word(NXT=6, IMM_OFS=0, MODM_SEL=msel))", "        p.append(word(NXT=6, IMM_OFS=0))")]),
    "mc_f1_dst_bit6": ("mc", None, "F1 destination looked up with bit 6 (operand 1's modm)",
                       [("    at(\"f1_dst\")\n    operand(MS_B5)", "    at(\"f1_dst\")\n    operand(MS_B6)")]),
    "mc_quick_unmasked": ("mc", None, "immediate quick not masked to 4 bits (F2 D=1)",
                          [("    w(IMM_LD=1, IMM_OFS=0, IMM_SZ=0, CONSUME=1, ASEL=A[\"IMM\"], BSEL=A[\"CONST\"], RCONST=15,\n"
                            "      ALU_OP=ALU[\"AND\"], RES_SEL=RES[\"ALU\"], WR_DST=WR[\"GPR\"], WR_ASEL=1)",
                            "    w(IMM_LD=1, IMM_OFS=0, IMM_SZ=0, CONSUME=1, ASEL=A[\"IMM\"], BSEL=A[\"CONST\"], RCONST=15,\n"
                            "      ALU_OP=ALU[\"PASSA\"], RES_SEL=RES[\"ALU\"], WR_DST=WR[\"GPR\"], WR_ASEL=1)")]),
    "mc_imm32_len4": ("mc", None, "#imm32 consumes 4 bytes instead of 5 (F2 D=1)",
                      [("    w(IMM_LD=1, IMM_OFS=1, IMM_SZ=2, CONSUME=5, ASEL=A[\"IMM\"], ALU_OP=ALU[\"PASSA\"],\n"
                        "      RES_SEL=RES[\"ALU\"], WR_DST=WR[\"GPR\"], WR_ASEL=1)",
                        "    w(IMM_LD=1, IMM_OFS=1, IMM_SZ=2, CONSUME=4, ASEL=A[\"IMM\"], ALU_OP=ALU[\"PASSA\"],\n"
                        "      RES_SEL=RES[\"ALU\"], WR_DST=WR[\"GPR\"], WR_ASEL=1)")]),
    "mc_br8_unsigned": ("mc", None, "BR8 displacement not sign-extended",
                        [("    w(IMM_LD=1, IMM_OFS=1, IMM_SZ=0, IMM_SX=1, ASEL=A[\"PC\"], BSEL=A[\"IMM\"],",
                          "    w(IMM_LD=1, IMM_OFS=1, IMM_SZ=0, IMM_SX=0, ASEL=A[\"PC\"], BSEL=A[\"IMM\"],")]),
    "mc_d0_no_lookup": ("mc", None, "F2 D=0 writes the destination without looking it up (also a RAW hazard on T0)",
                        [("    operand(MS_B6)\n    jmp(\"dst\")", "    jmp(\"dst_reg\")")]),
    "mc_immdest_writes": ("mc", None, "an immediate destination treated as a register (no trap)",
                          [("    jmp(\"trap_immdest\")\n    at(\"dst_reg\")", "    jmp(\"dst_reg\")\n    at(\"dst_reg\")")]),
}


# M1.2: ALU and PSW properties. Microcode mutants patch m1_m11_microcode.py;
# RTL mutants patch copies of the ALU / PSW units (the RTL itself is unchanged).
MUTANTS_M12 = {
    "sub_operands_swapped": ("mc", None, "SUB.W computes a - b (operands swapped on the buses)",
        [("        def exe(dst_sel, **f):                          # the execute word: b (B bus) op a (A bus) -> operand 2\n",
          "        def exe(dst_sel, **f):                          # the execute word: b (B bus) op a (A bus) -> operand 2\n"
          "            if n == \"subw\": f = dict(f, ASEL=f.get(\"BSEL\"), BSEL=f.get(\"ASEL\"))\n")]),
    "cmp_operands_swapped": ("mc", None, "CMP.W compares a - b",
        [("        def exe(dst_sel, **f):                          # the execute word: b (B bus) op a (A bus) -> operand 2\n",
          "        def exe(dst_sel, **f):                          # the execute word: b (B bus) op a (A bus) -> operand 2\n"
          "            if n == \"cmpw\": f = dict(f, ASEL=f.get(\"BSEL\"), BSEL=f.get(\"ASEL\"))\n")]),
    "add_is_addc": ("mc", None, "ADD.W adds the carry in (ADDC)",
        [("ALU_OPS = {\"addw\": (0x84, ALU[\"ADD\"], True, 0b1111)", "ALU_OPS = {\"addw\": (0x84, ALU[\"ADDC\"], True, 0b1111)")]),
    "add_is_sub": ("mc", None, "ADD.W subtracts",
        [("ALU_OPS = {\"addw\": (0x84, ALU[\"ADD\"], True, 0b1111)", "ALU_OPS = {\"addw\": (0x84, ALU[\"SUB\"], True, 0b1111)")]),
    "cmp_writes": ("mc", None, "CMP.W writes its result to operand 2",
        [("\"cmpw\": (0xBC, ALU[\"SUB\"], False, 0b1111)", "\"cmpw\": (0xBC, ALU[\"SUB\"], True, 0b1111)")]),
    "and_updates_cy": ("mc", None, "AND.W updates CY (clears it)",
        [("\"andw\": (0xA4, ALU[\"AND\"], True, 0b0111)", "\"andw\": (0xA4, ALU[\"AND\"], True, 0b1111)")]),
    "and_keeps_ov": ("mc", None, "AND.W leaves OV unchanged (does not clear it)",
        [("\"andw\": (0xA4, ALU[\"AND\"], True, 0b0111)", "\"andw\": (0xA4, ALU[\"AND\"], True, 0b0011)")]),
    "add_no_ov": ("mc", None, "ADD.W does not update OV",
        [("ALU_OPS = {\"addw\": (0x84, ALU[\"ADD\"], True, 0b1111)", "ALU_OPS = {\"addw\": (0x84, ALU[\"ADD\"], True, 0b1011)")]),
    "sub_no_cy": ("mc", None, "SUB.W does not update CY",
        [("\"subw\": (0xAC, ALU[\"SUB\"], True, 0b1111)", "\"subw\": (0xAC, ALU[\"SUB\"], True, 0b0111)")]),
    "dst_from_fld1": ("mc", None, "F2 D=0 / F1: the destination value read from the format byte's register",
        [("        exe(3, TADDR=T0, ASEL=A[\"RT\"], RB_SEL=3, BSEL=A[\"RB\"], CONSUME=1)",
          "        exe(3, TADDR=T0, ASEL=A[\"RT\"], RB_SEL=1, BSEL=A[\"RB\"], CONSUME=1)")]),
    "f1_dst_bank_b6": ("mc", None, "ALU F1 destination looked up with operand 1's modm (bit 6)",
        [("        at(f\"{n}_f1_dst\")\n        operand(MS_B5)", "        at(f\"{n}_f1_dst\")\n        operand(MS_B6)")]),
    "alu_quick_unmasked": ("mc", None, "ALU #quick source not masked to 4 bits",
        [("RCONST=15, ALU_OP=ALU[\"AND\"], RES_SEL=RES[\"ALU\"])     # no write: bypassed",
          "RCONST=15, ALU_OP=ALU[\"PASSA\"], RES_SEL=RES[\"ALU\"])     # no write: bypassed")]),
    "cmp_imm_dst_traps": ("mc", None, "CMP.W with an immediate operand 2 trapped instead of compared",
        [("            br(C_QUICK, f\"{n}_dst_quick\")\n", "            jmp(\"trap_immdest\")\n")]),
    "mov_sets_flags": ("mc", None, "MOV.W to a register updates the flags",
        [("    w(TADDR=T0, ASEL=A[\"RT\"], ALU_OP=ALU[\"PASSA\"], RES_SEL=RES[\"ALU\"],\n      WR_DST=WR[\"GPR\"], WR_ASEL=3, CONSUME=1)",
          "    w(TADDR=T0, ASEL=A[\"RT\"], ALU_OP=ALU[\"PASSA\"], RES_SEL=RES[\"ALU\"], FLAG_UPD=15,\n      WR_DST=WR[\"GPR\"], WR_ASEL=3, CONSUME=1)")]),
    "getpsw_zero": ("mc", None, "GETPSW writes 0 instead of the PSW",
        [("    w(ASEL=A[\"PSW\"], ALU_OP=ALU[\"PASSA\"]", "    w(ASEL=A[\"ZERO\"], ALU_OP=ALU[\"PASSA\"]")]),
    "alu_add_ov_inverted": ("rtl", "v2_alu.sv", "ALU: ADD overflow uses the subtract rule",
        [("4'd0, 4'd1, 4'd14:       begin cy = co;  ov = (xs == ys) && (rs != xs); end",
          "4'd0, 4'd1, 4'd14:       begin cy = co;  ov = (xs != ys) && (rs != xs); end")]),
    "alu_sub_carry": ("rtl", "v2_alu.sv", "ALU: SUB/CMP CY is the carry, not the borrow",
        [("4'd2, 4'd3, 4'd8, 4'd15: begin cy = ~co;", "4'd2, 4'd3, 4'd8, 4'd15: begin cy = co;")]),
    "alu_sign_bit30": ("rtl", "v2_alu.sv", "ALU: S from bit 30 at word size",
        [("wire rs = (size == 2'd0) ? res[7] : (size == 2'd1) ? res[15] : res[31];",
          "wire rs = (size == 2'd0) ? res[7] : (size == 2'd1) ? res[15] : res[30];")]),
    "alu_z_ignores_bit31": ("rtl", "v2_alu.sv", "ALU: Z ignores bit 31",
        [("assign z = ((res & mask) == 32'd0);", "assign z = ((res & mask & 32'h7fff_ffff) == 32'd0);")]),
    "r31_write_dropped": ("rtl", "v2_regs.sv", "GPR writes to R31 dropped (review C3-1)",
        [("        if (gpr_we && w_be[k]) begin\n            gpr_a[w_addr]",
          "        if (gpr_we && w_be[k] && w_addr != 5'd31) begin\n            gpr_a[w_addr]")]),
    "r31_read_zero": ("rtl", "v2_regs.sv", "GPR reads of R31 return 0 on both ports (review C3-4)",
        [("    ra_data <= gpr_a[ra_addr];\n    rb_data <= gpr_b[rb_addr];",
          "    ra_data <= (ra_addr == 5'd31) ? 32'd0 : gpr_a[ra_addr];\n    rb_data <= (rb_addr == 5'd31) ? 32'd0 : gpr_b[rb_addr];")]),
    "psw_cy_from_ov": ("rtl", "v2_psw_irq.sv", "PSW: CY written from the overflow flag",
        [("if (flag_upd[3]) f_cy <= ncy;", "if (flag_upd[3]) f_cy <= nov;")]),
}


def main():
    global MUTANTS
    if "--set" in sys.argv:
        i = sys.argv.index("--set")
        if sys.argv[i + 1] == "m12":
            MUTANTS = MUTANTS_M12
        del sys.argv[i:i + 2]
    if sys.argv[1:] == ["--list"]:
        for name, (kind, _, what, _) in MUTANTS.items():
            print(name, kind + ("-equiv" if name in EQUIVALENT else ""), what)
        return
    rtl, out = Path(sys.argv[1]), Path(sys.argv[2])
    for name, (kind, f, what, reps) in MUTANTS.items():
        d = out / name
        d.mkdir(parents=True, exist_ok=True)
        if kind == "rtl":
            shutil.copytree(rtl, d / "rtl", dirs_exist_ok=True)
            p = d / "rtl" / f
        else:
            p = d / "mcgen.py"
            src = (HERE / "m1_m11_microcode.py").read_text()
            src = src.replace("HERE = Path(__file__).resolve().parent", f"HERE = Path({str(HERE)!r})", 1)
            p.write_text(src)
        s = p.read_text()
        for old, new in reps:
            if s.count(old) != 1:
                sys.exit(f"mutant {name}: pattern occurs {s.count(old)} times: {old[:70]!r}")
            s = s.replace(old, new)
        p.write_text(s)


if __name__ == "__main__":
    main()
