// SPDX-License-Identifier: GPL-3.0-or-later
//
// V2 datapath floor: the 101-bit microinstruction ("control word") layout.
//
// Every datapath select in the skeleton comes from one of these fields, which
// come from the control store. That is what makes the measurement honest:
// with the control store's contents unknown to synthesis (writable RAM) or
// pseudo-random (ROM variant), no select is constant, so no unit or mux input
// can be pruned. Field widths are sized for the full V60 integer ISA.
//
// bit  width field          meaning
// ---  ----- -------------  ---------------------------------------------------
//   0    3   NXT            next-address: 0 seq, 1 jump, 2 cond-jump, 3 call,
//                           4 return, 5 dispatch-opcode, 6 dispatch-mode,
//                           7 dispatch-size (jaddr | size)
//   3    4   COND           condition select, low 4 bits (32 conditions with
//                           COND_HI, bit 96; see v2_core ucond)
//   7    1   CPOL           condition polarity
//   8   12   JADDR          jump / call target, dispatch base
//  20    4   ASEL           A-bus source
//  24    4   BSEL           B-bus source
//  28    2   RA_SEL         GPR read address A: 0 const, 1 op field 1,
//                           2 op field 2, 3 mode-byte register
//  30    2   RB_SEL         GPR read address B (same encoding)
//  32    5   RCONST         constant register number / privileged reg number
//  37    4   ALU_OP         ALU operation (v2_alu)
//  41    2   SIZE_SEL       operand size: 0 decoded size 1, 1 decoded size 2,
//                           2 word, 3 from RCONST[1:0]
//  43    3   SH_OP          shifter operation (v2_shift)
//  46    4   RES_SEL        result-bus source
//  50    4   FLAG_UPD       which of Z,S,OV,CY to update from the result unit
//  54    3   WR_DST         write-back: 0 none, 1 GPR, 2 temp, 3 privileged,
//                           4 PSW, 5 reserved (redirect is REDIRECT, bit 84),
//                           6 MAR, 7 MDR
//  57    2   WR_ASEL        write address: 0 const, 1 op field 1, 2 op field 2,
//                           3 mode-byte register
//  59    4   TADDR          temp register number (16 temps)
//  63    2   BE_MODE        byte enables: 0 by size, 1 word, 2 low half, 3 high half
//  65    3   AGU_BASE       address-unit base select
//  68    3   AGU_OFF        address-unit offset select
//  71    1   AGU_IDX        add scaled index (GPR B << size)
//  72    2   AGU_STEP       autoinc/dec step: 0 none, 1 +size, 2 -size, 3 +8
//  74    2   BUS_OP         0 none, 1 read, 2 write, 3 read-modify-write
//  76    2   BUS_SIZE       0 by SIZE_SEL, 1 byte, 2 half, 3 word
//  78    1   MAR_LD         load MAR from address unit
//  79    1   FWD_EN         allow result bypass into operand latches
//  80    4   CONSUME        fetch-queue bytes to consume: 0..8 literal, 9 = by
//                           immediate size, 10 = decoded specifier header (mode
//                           byte, index byte, first displacement), 11 = decoded
//                           second displacement (double-displacement modes)
//  84    1   REDIRECT       redirect fetch to the result in E3 (branch/jump);
//                           independent of WR_DST, so a step can also write back
//  85    1   IMM_LD         latch immediate/displacement from the fetch window
//  86    3   IMM_OFS        window byte offset of the immediate
//  89    2   IMM_SZ         immediate size: 0 byte, 1 half, 2 word, 3 by size
//  91    1   IMM_SX         sign-extend immediate
//  92    1   MD_START       start divider / multiplier
//  93    1   MD_SIGNED      signed multiply/divide
//  94    1   IRQ_ACK        acknowledge the pending interrupt
//  95    1   BCD_SUB        decimal subtract (else add)
//  96    1   COND_HI        condition select, bit 4
//  97    1   DEC_EA         take the address-unit controls (base, index, step,
//                           offset = displacement or zero) and the displacement
//                           extraction (offset IMM_OFS+1+index byte, size, sign
//                           extension) from the latched addressing-mode descriptor
//                           instead of this word's AGU_* / IMM_SZ / IMM_SX
//  98    2   MODM_SEL       mode-table bank for this step's lookup of the byte at
//                           IMM_OFS: 0 bank 0, 1 format byte bit 6, 2 format
//                           byte bit 5, 3 opcode bit 0 (v2_decode; M1.1)
// 100    1   RIDX           (B3) register index from a temp: the GPR read ports
//                           (A, B) and the GPR write address take rt_data[4:0]
//                           instead of the RA_SEL / RB_SEL / WR_ASEL choice.
//                           rt_data is the registered temp read port, so it
//                           holds temp[TADDR] of the word issued one cycle
//                           earlier (or of this word, when the pipeline holds):
//                           microcode gives this word and its predecessor the
//                           same TADDR (the index temp)
//
// Dispatch table entry (24 bits): {fmt[11:0], uaddr[11:0]}
//   fmt[1:0] size1, [3:2] size2 (operand sizes, from the opcode map)
//   fmt[6:4] field layout: [6] two-byte opcode (fields one byte later),
//            [5:4] which bytes hold register fields 1/2 and the condition code
//   fmt[7] privileged, [8] two general operands, [9] operand 1 is written,
//   fmt[10] operand 2 is written, [11] trailing operand byte (length/offset)
// Mode table entry (24 bits): {desc[11:0], uaddr[11:0]}
//   desc[0] register direct, [1] immediate, [3:2] displacement size (0 none,
//   1 byte, 2 half, 3 word), [4] double displacement, [5] indexed (index byte
//   precedes the mode byte's specifier), [8:6] AGU base select, [10:9] AGU
//   step (autoincrement/decrement), [11] deferred (memory indirect)
//   For immediates ([1]), [3:2] = 0 marks immediate quick (the value is in the
//   mode byte) and nonzero an immediate with extension bytes (ucond[26]).
// Mode table: 512 entries, addressed {modm, mode byte} (MODM_SEL).

`ifndef V2_PKG_SVH
`define V2_PKG_SVH

`define CW_NXT       2:0
`define CW_COND      6:3
`define CW_CPOL      7
`define CW_JADDR     19:8
`define CW_ASEL      23:20
`define CW_BSEL      27:24
`define CW_RA_SEL    29:28
`define CW_RB_SEL    31:30
`define CW_RCONST    36:32
`define CW_ALU_OP    40:37
`define CW_SIZE_SEL  42:41
`define CW_SH_OP     45:43
`define CW_RES_SEL   49:46
`define CW_FLAG_UPD  53:50
`define CW_WR_DST    56:54
`define CW_WR_ASEL   58:57
`define CW_TADDR     62:59
`define CW_BE_MODE   64:63
`define CW_AGU_BASE  67:65
`define CW_AGU_OFF   70:68
`define CW_AGU_IDX   71
`define CW_AGU_STEP  73:72
`define CW_BUS_OP    75:74
`define CW_BUS_SIZE  77:76
`define CW_MAR_LD    78
`define CW_FWD_EN    79
`define CW_CONSUME   83:80
`define CW_REDIRECT  84
`define CW_IMM_LD    85
`define CW_IMM_OFS   88:86
`define CW_IMM_SZ    90:89
`define CW_IMM_SX    91
`define CW_MD_START  92
`define CW_MD_SIGNED 93
`define CW_IRQ_ACK   94
`define CW_BCD_SUB   95
`define CW_COND_HI   96
`define CW_DEC_EA    97
`define CW_MODM_SEL  99:98
`define CW_RIDX     100

`define CW_W 101

`endif
