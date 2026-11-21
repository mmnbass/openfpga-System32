// SPDX-License-Identifier: GPL-3.0-or-later
//
// v2_decode: opcode dispatch table and addressing-mode table, plus the
// instruction field latches (register fields, sizes, condition code, the
// format byte) captured at opcode dispatch, and the operand's mode-byte
// register captured at mode dispatch.
//
// V60 F1/F2 (M1.1): byte 1 of an F1/F2 instruction (`ifl`) holds the format
// (bit 7: F1), D (bit 5, F2: which operand the register field names) and the
// operands' modm bits (bit 6 operand 1 / the F2 specifier, bit 5 operand 2 in
// F1); short formats keep modm in opcode bit 0. The mode table has a bank per
// modm value: it is addressed {modm, mode byte}, modm chosen per lookup by
// the control word's MODM_SEL (0: 0, 1: ifl[6], 2: ifl[5], 3: opcode bit 0).
// Both tables are registered reads of the current window: an entry is used
// one cycle after the bytes it was read from (see v2_core ucond[27]).
//
// v2_useq: microsequencer and control store.
//
// Control store and decode tables are either
//   CS_ROM = 0  writable RAM with a load port (contents unknown to synthesis:
//               nothing downstream can be pruned; the pessimistic floor), or
//   CS_ROM = 1  fixed ROM initialized from a hex file (what a finished CPU
//               would ship; contents known to synthesis).
// Both are forced into M10K so a small table is never turned into LUTs.

`include "v2_pkg.svh"

module v2_decode #(
    parameter CS_ROM = 1'b0,
    parameter TBL_INIT_D = "v2_dispatch.hex",
    parameter TBL_INIT_M = "v2_mode.hex"
) (
    input             clk,
    input             rst,
    input      [63:0] window,
    input       [2:0] mode_ofs,       // which window byte holds the mode byte
    input       [1:0] modm_sel,       // mode-table bank: 0 zero, 1 ifl[6], 2 ifl[5], 3 opcode bit 0
    input             dispatch,       // opcode dispatch: latch the instruction's fields
    input             mode_dispatch,  // mode dispatch: latch the addressing-mode descriptor
    // table load port (CS_ROM = 0)
    input             tbl_we,
    input             tbl_sel,        // 0 dispatch table, 1 mode table
    input       [8:0] tbl_addr,       // dispatch table: [7:0]; mode table: {modm, mode byte}
    input      [23:0] tbl_wdata,
    // results
    output reg [23:0] disp_entry,     // {fmt[11:0], uaddr[11:0]} for window byte 0
    output reg [23:0] mode_entry,     // {desc[11:0], uaddr[11:0]} for the mode byte
    output reg  [4:0] fld1, fld2,     // register fields of operand 1 / 2
    output reg  [4:0] mreg,           // register of the operand at the last mode dispatch
    output reg  [7:0] ifl,            // byte 1 (F1/F2 format byte) at the last opcode dispatch
    output reg        op0,            // opcode bit 0 at the last opcode dispatch
    output reg  [1:0] size1, size2,
    output reg  [3:0] cc,
    output reg  [4:0] dflags,         // {trailing, op2 written, op1 written, two-operand, privileged}
    output reg [11:0] ea_desc         // latched addressing-mode descriptor (see v2_pkg.svh)
);

(* ramstyle = "M10K" *) reg [23:0] dtab [0:255];
(* ramstyle = "M10K" *) reg [23:0] mtab [0:511];

generate if (CS_ROM) begin : rom
    initial $readmemh(TBL_INIT_D, dtab);
    initial $readmemh(TBL_INIT_M, mtab);
end endgenerate

wire  [7:0] mode_byte = window[mode_ofs*8 +: 8];
wire        modm      = (modm_sel == 2'd1) ? ifl[6] : (modm_sel == 2'd2) ? ifl[5] :
                        (modm_sel == 2'd3) ? op0 : 1'b0;
wire  [8:0] mt_addr   = {modm, mode_byte};
reg   [7:0] mbyte_q;                  // the mode byte mode_entry was read from
wire [11:0] fmt = disp_entry[23:12];

// instruction bytes 0-3 (two-byte opcodes shift every field by one byte)
wire [31:0] ib = fmt[6] ? window[39:8] : window[31:0];

always @(posedge clk) begin
    if (!CS_ROM && tbl_we) begin
        if (tbl_sel) mtab[tbl_addr] <= tbl_wdata;
        else         dtab[tbl_addr[7:0]] <= tbl_wdata;
    end
    disp_entry <= dtab[window[7:0]];
    mode_entry <= mtab[mt_addr];
    mbyte_q    <= mode_byte;
end

always @(posedge clk) begin
    if (rst) begin
        fld1 <= 5'd0; fld2 <= 5'd0; mreg <= 5'd0; ifl <= 8'd0; op0 <= 1'b0;
        size1 <= 2'd0; size2 <= 2'd0; cc <= 4'd0;
        dflags <= 5'd0; ea_desc <= 12'd0;
    end else begin
        if (dispatch) begin
            ifl <= ib[15:8];
            op0 <= window[0];
            // operand sizes and decoded properties come from the opcode map;
            // the field layout selects where the register fields and the
            // condition code sit for this instruction format
            size1  <= fmt[1:0];
            size2  <= fmt[3:2];
            dflags <= fmt[11:7];
            case (fmt[5:4])
                2'd0:    begin fld1 <= ib[12:8];  fld2 <= ib[20:16]; cc <= ib[3:0];   end
                2'd1:    begin fld1 <= ib[12:8];  fld2 <= ib[28:24]; cc <= ib[11:8];  end
                2'd2:    begin fld1 <= ib[4:0];   fld2 <= ib[12:8];  cc <= ib[11:8];  end
                default: begin fld1 <= ib[20:16]; fld2 <= ib[12:8];  cc <= ib[3:0];   end
            endcase
        end
        if (mode_dispatch) begin
            ea_desc <= mode_entry[23:12];
            mreg    <= mbyte_q[4:0];      // stable while the specifier is consumed (M1.1 N4)
        end
    end
end

endmodule

module v2_useq #(
    parameter CS_DEPTH = 2048,
    parameter CS_ROM   = 1'b0,
    parameter CS_INIT  = "v2_cstore.hex"
) (
    input             clk,
    input             rst,
    input             ce,
    input             stall,
    input      [31:0] ucond,          // micro-conditions (see v2_core)
    input      [11:0] disp_uaddr,
    input      [11:0] mode_uaddr,
    input       [1:0] size,
    // control store load port (CS_ROM = 0)
    input             cs_we,
    input      [11:0] cs_waddr,
    input      [`CW_W-1:0] cs_wdata,
    output reg [`CW_W-1:0] cw,
    output reg [11:0] upc
);

localparam int CAW = $clog2(CS_DEPTH);

(* ramstyle = "M10K" *) reg [`CW_W-1:0] cstore [0:CS_DEPTH-1];
generate if (CS_ROM) begin : rom
    initial $readmemh(CS_INIT, cstore);
end endgenerate

reg [11:0] stack [0:3];
reg  [1:0] sp;
reg [11:0] next;

wire [2:0]  nxt   = cw[2:0];
wire [4:0]  csel  = {cw[`CW_COND_HI], cw[`CW_COND]};
wire        cpol  = cw[7];
wire [11:0] jaddr = cw[19:8];
wire        ctrue = ucond[csel] ^ cpol;

always @* begin
    case (nxt)
        3'd0: next = upc + 12'd1;
        3'd1: next = jaddr;
        3'd2: next = ctrue ? jaddr : upc + 12'd1;
        3'd3: next = jaddr;                         // call
        3'd4: next = stack[sp - 2'd1];              // return
        3'd5: next = disp_uaddr;
        3'd6: next = mode_uaddr;
        default: next = jaddr | {10'd0, size};
    endcase
    if (stall || !ce) next = upc;                   // re-issue the same word
    if (rst) next = 12'd0;                          // first word after reset is word 0
end

always @(posedge clk) begin
    if (!CS_ROM && cs_we) cstore[cs_waddr[CAW-1:0]] <= cs_wdata;
    if (rst) begin
        upc <= 12'd0;
        sp  <= 2'd0;
    end else begin
        upc <= next;
        if (!stall && ce) begin
            if (nxt == 3'd3) begin stack[sp] <= upc + 12'd1; sp <= sp + 2'd1; end
            if (nxt == 3'd4) sp <= sp - 2'd1;
        end
    end
    cw <= cstore[next[CAW-1:0]];
end

endmodule
