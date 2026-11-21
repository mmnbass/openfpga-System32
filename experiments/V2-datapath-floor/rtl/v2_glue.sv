// SPDX-License-Identifier: GPL-3.0-or-later
//
// Datapath glue of v2_core, split into modules only so that the fitter reports
// each block as its own entity (Quartus synthesizes across the hierarchy, so
// the split does not change the logic):
//
//   v2_opmux   E1  A- and B-bus source multiplexers plus the result bypass,
//                  feeding the operand latches
//   v2_resmux  E2  result-bus multiplexer and the flag sources that go with it
//   v2_immx    E0  immediate / displacement extraction from the fetch window and
//                  the effective fetch-consume count, including the decoded
//                  operand-specifier forms (DEC_EA, CONSUME 9-11)

`include "v2_pkg.svh"

module v2_opmux (
    input       [3:0] asel,
    input       [3:0] bsel,
    input             fwd_en,
    input      [31:0] ra, rb, rp, rt, pc, mdr, imm, psw, sp, mar, md_hi, md_lo, res_q,
    input       [4:0] rconst,
    input       [7:0] vector,
    input      [31:0] result,        // E2 result, bypassed into the operand latches
    output     [31:0] a_next,
    output     [31:0] b_next
);

function automatic [31:0] bus_src(input [3:0] sel);
    case (sel)
        4'd0:  bus_src = ra;
        4'd1:  bus_src = rb;
        4'd2:  bus_src = rp;
        4'd3:  bus_src = rt;
        4'd4:  bus_src = pc;
        4'd5:  bus_src = mdr;
        4'd6:  bus_src = imm;
        4'd7:  bus_src = psw;
        4'd8:  bus_src = sp;
        4'd9:  bus_src = {27'd0, rconst};
        4'd10: bus_src = mar;
        4'd11: bus_src = md_hi;
        4'd12: bus_src = md_lo;
        4'd13: bus_src = res_q;            // result bypass (registered)
        4'd14: bus_src = {24'd0, vector};
        default: bus_src = 32'd0;
    endcase
endfunction

wire [31:0] abus = bus_src(asel);
wire [31:0] bbus = bus_src(bsel);
assign a_next = (fwd_en && asel == 4'd13) ? result : abus;
assign b_next = (fwd_en && bsel == 4'd13) ? result : bbus;

endmodule

module v2_resmux (
    input       [3:0] sel,
    input      [31:0] alu_res,
    input             alu_z, alu_s, alu_ov, alu_cy,
    input      [31:0] sh_res,
    input             sh_cy, sh_ov,
    input      [31:0] agu_addr, agu_step,
    input       [7:0] bcd_res,
    input             bcd_cout,
    input      [15:0] bcd_unpack,
    input       [7:0] bcd_pack,
    input      [31:0] bf_ins, bf_ext, bf_mask, bf_boff,
    input       [5:0] bf_first,
    input      [31:0] md_lo, md_hi,
    input             md_dz,
    input      [31:0] opa, mdr,
    output reg [31:0] result,
    output reg        nz, ns, nov, ncy
);

always @* begin
    nz = alu_z; ns = alu_s; nov = alu_ov; ncy = alu_cy;
    case (sel)
        4'd0:  result = alu_res;
        4'd1:  begin result = sh_res; nz = (sh_res == 32'd0); ncy = sh_cy; nov = sh_ov; end
        4'd2:  result = agu_addr;
        4'd3:  result = agu_step;
        4'd4:  begin result = {24'd0, bcd_res}; ncy = bcd_cout; nz = (bcd_res == 8'd0); end
        4'd5:  result = {16'd0, bcd_unpack};
        4'd6:  result = {24'd0, bcd_pack};
        4'd7:  result = bf_ins;
        4'd8:  result = bf_ext;
        4'd9:  result = bf_mask;
        4'd10: begin result = {26'd0, bf_first}; nz = (bf_first == 6'd32); end
        4'd11: result = bf_boff;
        4'd12: begin result = md_lo; nov = md_dz; end
        4'd13: result = md_hi;
        4'd14: result = opa;
        default: result = mdr;
    endcase
end

endmodule

module v2_immx (
    input      [63:0] window,
    input             imm_ld,
    input       [2:0] imm_ofs,
    input       [1:0] imm_sz,
    input             imm_sx,
    input       [3:0] consume,
    input             dec_ea,        // displacement controls from the mode descriptor
    input       [1:0] size0,         // operand size of this step
    input      [11:0] ea_desc,
    output            imm_load,
    output     [31:0] imm_val,
    output reg  [3:0] consume_eff
);

wire [1:0] dsz = ea_desc[3:2];       // displacement: 0 none, 1 byte, 2 half, 3 word
wire       idx = ea_desc[5];         // an index byte precedes the specifier's mode byte
wire       dbl = ea_desc[4];         // double displacement

// a decoded displacement follows the mode byte (and the index byte, if any)
wire [2:0] ofs = dec_ea ? imm_ofs + 3'd1 + {2'd0, idx} : imm_ofs;
wire [1:0] isz = dec_ea ? dsz - 2'd1 : (imm_sz == 2'd3) ? size0 : imm_sz;
wire       sx  = dec_ea | imm_sx;    // V60 displacements are signed

wire [63:0] win_sh = window >> (ofs * 8);
assign imm_val = (isz == 2'd0) ? {{24{sx & win_sh[7]}},  win_sh[7:0]} :
                 (isz == 2'd1) ? {{16{sx & win_sh[15]}}, win_sh[15:0]} :
                                 win_sh[31:0];
assign imm_load = imm_ld && (!dec_ea || dsz != 2'd0);

wire [3:0] dbytes = (dsz == 2'd0) ? 4'd0 : (dsz == 2'd1) ? 4'd1 : (dsz == 2'd2) ? 4'd2 : 4'd4;
wire [3:0] ibytes = (isz == 2'd0) ? 4'd1 : (isz == 2'd1) ? 4'd2 : 4'd4;
always @* begin
    case (consume)
        4'd9:    consume_eff = ibytes;                          // the immediate just taken
        4'd10:   consume_eff = 4'd1 + {3'd0, idx} + dbytes;     // specifier header, <= 6 bytes
        4'd11:   consume_eff = dbl ? dbytes : 4'd0;             // second displacement
        default: consume_eff = (consume <= 4'd8) ? consume : 4'd0;
    endcase
end

endmodule
