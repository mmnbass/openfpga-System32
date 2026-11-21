// SPDX-License-Identifier: GPL-3.0-or-later
//
// v2_shift: single-cycle V60 shifter built as ONE 64->32 funnel shifter, the
// standard CPU construction (UNIT_STYLE=0). Every shift and rotate is
// expressed as out = ({hi, lo} >> k)[31:0] with a per-operation choice of
// {hi, lo} and k:
//
//   SHL/SHA left n   {v, 0}          k = 32 - n
//   SHL right n      {0, v}          k = n
//   SHA right n      {sign, vsx}     k = n
//   ROT left r       {vrep, vrep}    k = 32 - r   (vrep = v replicated to 32
//   ROT right r      {vrep, vrep}    k = r         bits, so 8/16-bit rotates
//                                                  come out right in the low bits)
//   SHR (unsigned)   {0, v}          k = count     (bit-field extract helper)
//
// Last-bit-out uses one bit select; SHA left overflow compares n with the
// count of leading sign bits. ROTC (rotate through carry, w+1 bits) is NOT
// done in one step here: microcode composes it from two funnel shifts and an
// OR. That adds microsteps to a rare instruction and is recorded as a known
// cost. RVBIT/RVBYT are wiring.
//
// op: 0 SHL 1 SHA 2 ROT 3 (ROTC: handled by microcode; behaves as ROT)
//     4 RVBIT 5 RVBYT 6 SHR 7 PASS

module v2_shift (
    input      [31:0] a,
    input       [7:0] count,
    input       [2:0] op,
    input       [1:0] size,
    input             cin,
    output reg [31:0] res,
    output reg        cy,
    output reg        ov
);

wire [5:0]  w    = (size == 2'd0) ? 6'd8 : (size == 2'd1) ? 6'd16 : 6'd32;
wire [31:0] mask = (size == 2'd0) ? 32'h0000_00ff :
                   (size == 2'd1) ? 32'h0000_ffff : 32'hffff_ffff;
wire [31:0] v    = a & mask;
wire        sgn  = (size == 2'd0) ? a[7] : (size == 2'd1) ? a[15] : a[31];
wire [31:0] vsx  = (size == 2'd0) ? {{24{a[7]}}, a[7:0]} :
                   (size == 2'd1) ? {{16{a[15]}}, a[15:0]} : a;
wire [31:0] vrep = (size == 2'd0) ? {4{a[7:0]}} : (size == 2'd1) ? {2{a[15:0]}} : a;

wire        rot   = (op == 3'd2) || (op == 3'd3);
wire        left  = (op == 3'd6) ? 1'b0 : ~count[7];
wire [7:0]  mag   = (op == 3'd6) ? count : (count[7] ? (~count + 8'd1) : count);
wire [5:0]  n     = (mag > 8'd32) ? 6'd32 : mag[5:0];          // saturate
wire [4:0]  r     = mag[4:0] & (w[4:0] - 5'd1);                // rotate mod w

reg  [63:0] f;
reg  [5:0]  k;
always @* begin
    if (rot) begin
        f = {vrep, vrep};
        k = left ? (6'd32 - {1'b0, r}) : {1'b0, r};
    end else if (left) begin
        f = {v, 32'd0};
        k = 6'd32 - n;
    end else if (op == 3'd1) begin
        f = {{32{sgn}}, vsx};
        k = n;
    end else begin
        f = {32'd0, v};
        k = n;
    end
end
wire [63:0] fo  = f >> k;
wire [5:0]  nm1w = n - 6'd1;
wire [4:0]  nm1  = nm1w[4:0];        // n in 1..32 here, so n-1 fits 5 bits
wire [32:0] o33 = fo[32:0];

// count of leading bits equal to the sign below the sign bit (0..31): the
// leading zeros of (bits 30..0 XOR sign), from a balanced tree. (Until
// 2026-10-23 this was a loop whose 31 iterations each compared and
// incremented the previous count: a serial chain that local structural
// analysis placed on the longest path behind run 39960323008's timing
// failure.)
wire [30:0] sdiff = vsx[30:0] ^ {31{vsx[31]}};
wire  [5:0] cls;
v2_clz32 clsu (.x({sdiff, 1'b1}), .n(cls));
wire [5:0] cls_w = cls - (6'd32 - w);              // restrict to the operand size

reg [7:0] rv8;
integer j;
always @* for (j = 0; j < 8; j = j + 1) rv8[j] = a[7 - j];

always @* begin
    res = o33[31:0] & mask; cy = 1'b0; ov = 1'b0;
    case (op)
        3'd0, 3'd1, 3'd6: begin
            if (n == 6'd0)  cy = 1'b0;
            else if (left)  cy = o33[w];               // last bit shifted past the size
            else            cy = (n > w) ? ((op == 3'd1) ? sgn : 1'b0) : v[nm1];
            if (op == 3'd1 && left) ov = (n > cls_w) && (v != 32'd0);
        end
        3'd2, 3'd3: cy = left ? res[0] :
                         (size == 2'd0 ? res[7] : size == 2'd1 ? res[15] : res[31]);
        3'd4: res = {24'd0, rv8};
        3'd5: res = {a[7:0], a[15:8], a[23:16], a[31:24]};
        3'd7: res = a;
        default: ;
    endcase
end

// cin is consumed only by ROTC, which microcode composes from funnel shifts
wire unused_cin = cin;

endmodule
