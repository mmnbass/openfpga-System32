// SPDX-License-Identifier: GPL-3.0-or-later
//
// v2_muldiv: V60 multiply and divide.
//
// Multiply: 33 x 33 signed product (covers MUL/MULU/MULX 32x32->64 signed and
// unsigned) on DSP blocks, two pipeline registers so it closes at the
// microstep clock. Divide: restoring 64/32 divider, one quotient bit per
// microstep (DIV/DIVU/REM/REMU 32/32 and DIVX/DIVUX 64/32), with sign fix-up
// and divide-by-zero / overflow detection. The divider's iteration count is
// what upstream's xdiv already does; only its storage is shared here.

module v2_muldiv (
    input             clk,
    input             start,
    input             is_div,
    input             signed_op,
    input             wide,          // DIVX: 64-bit dividend {hi_in, b}
    input      [31:0] a,             // multiplicand / divisor
    input      [31:0] b,             // multiplier / dividend (low)
    input      [31:0] hi_in,         // dividend high word (DIVX)
    output     [31:0] lo,
    output     [31:0] hi,
    output reg        busy,
    output reg        done,
    output reg        dz             // divide by zero
);

// ---------------- multiplier (DSP, 2-stage) ----------------
reg  signed [32:0] ma, mb;
reg  signed [65:0] mp;
reg         [1:0]  mvalid;
always @(posedge clk) begin
    if (start && !is_div) begin            // capture operands once; product then holds
        ma <= {signed_op & a[31], a};
        mb <= {signed_op & b[31], b};
    end
    mp <= ma * mb;
    mvalid <= {mvalid[0], start & ~is_div};
end

// ---------------- divider (restoring, 1 bit/step) ----------------
reg  [63:0] dshift;      // dividend shifting into the remainder
reg  [32:0] drem;
reg  [31:0] dden;
reg   [6:0] dcnt;
reg         qneg, rneg, ddiv;
wire [32:0] trial = {drem[31:0], dshift[63]};
wire        take  = trial >= {1'b0, dden};
wire [32:0] rnext = take ? trial - {1'b0, dden} : trial;
wire [63:0] snext = {dshift[62:0], take};

wire        an = signed_op & a[31];
wire        bn = signed_op & (wide ? hi_in[31] : b[31]);
wire [31:0] aabs = an ? (~a + 32'd1) : a;
wire [63:0] bfull = wide ? {hi_in, b} : {32'd0, b};
wire [63:0] babs  = bn ? (wide ? (~{hi_in, b} + 64'd1) : {32'd0, ~b + 32'd1}) : bfull;

reg  [31:0] q_out, r_out;
always @(posedge clk) begin
    done <= 1'b0;
    if (start && is_div) begin
        dz     <= (a == 32'd0);
        dden   <= aabs;
        dshift <= babs;
        drem   <= 33'd0;
        dcnt   <= wide ? 7'd64 : 7'd32;
        if (!wide) dshift <= {babs[31:0], 32'd0};
        qneg   <= an ^ bn;
        rneg   <= bn;
        busy   <= (a != 32'd0);
        done   <= (a == 32'd0);
    end else if (busy) begin
        drem   <= rnext;
        dshift <= snext;
        dcnt   <= dcnt - 7'd1;
        if (dcnt == 7'd1) begin
            busy  <= 1'b0;
            done  <= 1'b1;
            q_out <= qneg ? (~snext[31:0] + 32'd1) : snext[31:0];
            r_out <= rneg ? (~rnext[31:0] + 32'd1) : rnext[31:0];
        end
    end
    if (mvalid[1]) done <= 1'b1;
    ddiv  <= start ? is_div : ddiv;
end

assign lo = ddiv ? q_out : mp[31:0];
assign hi = ddiv ? r_out : mp[63:32];

endmodule
