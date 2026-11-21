// SPDX-License-Identifier: GPL-3.0-or-later
//
// v2_shift_naive: (UNIT_STYLE=1 variant) single-cycle barrel shifter built as
// separate shifters per operation; for the V60 shift group.
//
// The V60 count is signed (positive = left, negative = right) and the operand
// size is byte, half or word. Supported:
//   0 SHL  logical shift              (CY = last bit out)
//   1 SHA  arithmetic shift           (CY = last bit out, OV on left overflow)
//   2 ROT  rotate                     (CY = last bit rotated)
//   3 ROTC rotate through carry       (width+1 bits)
//   4 RVBIT reverse the bits of a byte
//   5 RVBYT reverse the bytes of a word
//   6 SHR  logical right by |count| (bit-field extract helper, count unsigned)
//   7 PASS
// A barrel unit is the deliberate, pessimistic floor choice; an iterative
// shifter (one bit per microstep) is priced separately as variant V2d.

module v2_shift_naive (
    input      [31:0] a,
    input       [7:0] count,
    input       [2:0] op,
    input       [1:0] size,
    input             cin,
    output reg [31:0] res,
    output reg        cy,
    output reg        ov
);

wire [5:0]  w     = (size == 2'd0) ? 6'd8 : (size == 2'd1) ? 6'd16 : 6'd32;
wire [31:0] mask  = (size == 2'd0) ? 32'h0000_00ff :
                    (size == 2'd1) ? 32'h0000_ffff : 32'hffff_ffff;
wire [31:0] v     = a & mask;
wire        vsign = (size == 2'd0) ? a[7] : (size == 2'd1) ? a[15] : a[31];
wire [31:0] vsx   = (size == 2'd0) ? {{24{a[7]}}, a[7:0]} :
                    (size == 2'd1) ? {{16{a[15]}}, a[15:0]} : a;

wire        left  = (op == 3'd6) ? 1'b0 : ~count[7];
wire [7:0]  mag   = (op == 3'd6) ? count : (count[7] ? (~count + 8'd1) : count);
wire [6:0]  n     = (mag > 8'd64) ? 7'd64 : mag[6:0];        // saturate

// logical and arithmetic shifts on a 64-bit field so the last bit out is kept
wire [63:0] shl64 = {32'd0, v} << n;
wire [63:0] shr64 = {v, 32'd0} >> n;
wire [63:0] sar64 = $signed({vsx, 32'd0}) >>> n;

// rotate amount modulo width (power of two) and modulo width+1 (ROTC)
wire [4:0]  rk   = mag[4:0] & (w[4:0] - 5'd1);
wire [5:0]  w1   = w + 6'd1;
wire [7:0]  rc0  = mag;
// mag < 129 and w1 in {9,17,33}: a small constant-divisor remainder
wire [7:0]  rck8 = (w1 == 6'd9)  ? (rc0 % 8'd9)  :
                   (w1 == 6'd17) ? (rc0 % 8'd17) : (rc0 % 8'd33);
wire [5:0]  rck  = rck8[5:0];

wire [31:0] rotl = ((v << rk) | (v >> (w - {1'b0, rk}))) & mask;
wire [31:0] rotr = ((v >> rk) | (v << (w - {1'b0, rk}))) & mask;

// ROTC: rotate the (w+1)-bit value {cin, v}
wire [32:0] cv   = (size == 2'd0) ? {24'd0, cin, a[7:0]} :
                   (size == 2'd1) ? {16'd0, cin, a[15:0]} : {cin, a};
wire [32:0] rcl  = (cv << rck) | (cv >> (w1 - rck));
wire [32:0] rcr  = (cv >> rck) | (cv << (w1 - rck));
wire [32:0] rcsel = left ? rcl : rcr;
wire [32:0] rcm  = (size == 2'd0) ? 33'h1ff : (size == 2'd1) ? 33'h1ffff : 33'h1_ffff_ffff;

wire [63:0] sha_top  = shl64 >> (w - 6'd1);
wire [64:0] sha_ones = (65'd1 << ({58'd0, n} + 65'd1)) - 65'd1;

reg [7:0] rv8;
integer i;
always @* begin
    for (i = 0; i < 8; i = i + 1) rv8[i] = a[7 - i];
end

always @* begin
    res = v; cy = 1'b0; ov = 1'b0;
    case (op)
        3'd0, 3'd6: begin
            if (left) begin
                res = shl64[31:0] & mask;
                cy  = (n == 0) ? 1'b0 : shl64[w];
            end else begin
                res = shr64[63:32] & mask;
                cy  = (n == 0) ? 1'b0 : shr64[31];
            end
        end
        3'd1: begin
            if (left) begin
                res = shl64[31:0] & mask;
                cy  = (n == 0) ? 1'b0 : shl64[w];
                // OV: the bits that passed through the sign position are
                // not all equal (SHIFTLEFT_OV)
                ov  = (sha_top != 64'd0) && (sha_top != sha_ones[63:0]);
            end else begin
                res = sar64[63:32] & mask;
                cy  = (n == 0) ? 1'b0 : sar64[31];
            end
        end
        3'd2: begin
            res = left ? rotl : rotr;
            cy  = left ? res[0] : (size == 2'd0 ? res[7] : size == 2'd1 ? res[15] : res[31]);
        end
        3'd3: begin
            res = rcsel[31:0] & rcm[31:0];
            cy  = rcsel[w];
        end
        3'd4: res = {24'd0, rv8};
        3'd5: res = {a[7:0], a[15:8], a[23:16], a[31:24]};
        default: res = a;
    endcase
end

endmodule
