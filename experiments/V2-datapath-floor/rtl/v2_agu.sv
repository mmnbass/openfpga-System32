// SPDX-License-Identifier: GPL-3.0-or-later
//
// v2_agu: the shared V60 address-generation unit.
//
//   addr = base + offset + (idx_en ? index << scale : 0)
//   step = +/- operand size (autoincrement/decrement) or +8
//
// Base, offset and index cover every V60 addressing mode, including indexed
// and double-displacement forms (the second displacement re-enters through
// MAR as the next base). X1 showed today's 33 distinct address expressions
// are all of this shape. The vector address SBR + 4*vector uses base = A-bus
// (SBR from the privileged file) and offset = {vector, 2'b00}.
//
// base: 0 A-bus 1 B-bus 2 PC(fetch head) 3 MAR 4 SP shadow 5 zero 6 temp 7 MDR
// off : 0 zero 1 imm/disp latch 2 B-bus 3 +size 4 -size 5 +4 6 -4 7 vector*4

module v2_agu (
    input      [31:0] abus,
    input      [31:0] bbus,
    input      [31:0] pc,
    input      [31:0] mar,
    input      [31:0] sp,
    input      [31:0] tdata,
    input      [31:0] mdr,
    input      [31:0] imm,
    input      [31:0] index,
    input       [7:0] vector,
    input       [2:0] base_sel,
    input       [2:0] off_sel,
    input             idx_en,
    input       [1:0] size,
    input       [1:0] step_sel,
    output     [31:0] addr,
    output reg [31:0] stepped      // base + step, for autoinc/autodec writeback
);

wire [31:0] sz = (size == 2'd0) ? 32'd1 : (size == 2'd1) ? 32'd2 : 32'd4;

reg [31:0] base, off;
always @* begin
    case (base_sel)
        3'd0: base = abus;
        3'd1: base = bbus;
        3'd2: base = pc;
        3'd3: base = mar;
        3'd4: base = sp;
        3'd5: base = 32'd0;
        3'd6: base = tdata;
        default: base = mdr;
    endcase
    case (off_sel)
        3'd0: off = 32'd0;
        3'd1: off = imm;
        3'd2: off = bbus;
        3'd3: off = sz;
        3'd4: off = ~sz + 32'd1;
        3'd5: off = 32'd4;
        3'd6: off = 32'hffff_fffc;
        default: off = {22'd0, vector, 2'b00};
    endcase
end

wire [31:0] scaled = idx_en ? (index << size) : 32'd0;
assign addr = base + off + scaled;

always @* begin
    case (step_sel)
        2'd1: stepped = base + sz;
        2'd2: stepped = base - sz;
        2'd3: stepped = base + 32'd8;
        default: stepped = base;
    endcase
end

endmodule
