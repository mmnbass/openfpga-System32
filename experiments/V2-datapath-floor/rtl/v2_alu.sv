// SPDX-License-Identifier: GPL-3.0-or-later
//
// v2_alu: the single shared V60 integer ALU.
//
// One 33-bit adder serves ADD/ADDC/SUB/SUBC/CMP/NEG/INC/DEC. Logic ops,
// sign/zero extension and truncation-overflow share the same output mux.
// Flags are computed at the operand size (byte/half/word), as the V60 does:
// Z and S at the size, CY at the size's carry-out, OV from the size's sign
// bits. This replaces the per-opcode arithmetic in today's exec_op.
//
// op: 0 ADD  1 ADDC 2 SUB  3 SUBC 4 AND  5 OR   6 XOR  7 NOT
//     8 NEG  9 PASSA 10 PASSB 11 SEXT 12 ZEXT 13 TRUNCOV 14 INC 15 DEC

module v2_alu (
    input      [31:0] a,
    input      [31:0] b,
    input       [3:0] op,
    input       [1:0] size,     // 0 byte, 1 half, 2/3 word
    input             cin,
    output reg [31:0] res,
    output            z, s,
    output reg        ov, cy
);

wire [31:0] mask = (size == 2'd0) ? 32'h0000_00ff :
                   (size == 2'd1) ? 32'h0000_ffff : 32'hffff_ffff;
wire [31:0] am = a & mask;
wire [31:0] bm = b & mask;

// shared adder: x + (inv ? ~y : y) + c
reg  [31:0] x, y;
reg         inv, c;
always @* begin
    x = bm; y = am; inv = 1'b0; c = 1'b0;
    case (op)
        4'd0:  begin end                                  // b + a
        4'd1:  c = cin;
        4'd2:  begin inv = 1'b1; c = 1'b1; end            // b - a
        4'd3:  begin inv = 1'b1; c = ~cin; end            // b - a - cin
        4'd8:  begin x = 32'd0; inv = 1'b1; c = 1'b1; end // 0 - a
        4'd14: begin y = 32'd1; end                       // b + 1
        4'd15: begin y = 32'd1; inv = 1'b1; c = 1'b1; end // b - 1
        default: ;
    endcase
end
wire [32:0] sum = {1'b0, x} + {1'b0, (inv ? ~y : y) & mask} + {32'd0, c};

always @* begin
    case (op)
        4'd4:  res = am & bm;
        4'd5:  res = am | bm;
        4'd6:  res = am ^ bm;
        4'd7:  res = ~am & mask;
        4'd9:  res = a;
        4'd10: res = b;
        4'd11: res = (size == 2'd0) ? {{24{a[7]}}, a[7:0]} :
                     (size == 2'd1) ? {{16{a[15]}}, a[15:0]} : a;
        4'd12: res = am;
        4'd13: res = am;
        default: res = sum[31:0] & mask;
    endcase
end

wire rs = (size == 2'd0) ? res[7] : (size == 2'd1) ? res[15] : res[31];
wire xs = (size == 2'd0) ? x[7]   : (size == 2'd1) ? x[15]   : x[31];
wire ys = (size == 2'd0) ? y[7]   : (size == 2'd1) ? y[15]   : y[31];
wire co = (size == 2'd0) ? sum[8] : (size == 2'd1) ? sum[16] : sum[32];

assign z = ((res & mask) == 32'd0);
assign s = rs;

always @* begin
    case (op)
        4'd0, 4'd1, 4'd14:       begin cy = co;  ov = (xs == ys) && (rs != xs); end
        4'd2, 4'd3, 4'd8, 4'd15: begin cy = ~co; ov = (xs != ys) && (rs != xs); end
        // MOVT: OV when the discarded bits are not the sign extension
        4'd13: begin
            cy = 1'b0;
            ov = (size == 2'd0) ? (a[31:8]  != {24{a[7]}}) :
                 (size == 2'd1) ? (a[31:16] != {16{a[15]}}) : 1'b0;
        end
        default: begin cy = 1'b0; ov = 1'b0; end
    endcase
end

endmodule
