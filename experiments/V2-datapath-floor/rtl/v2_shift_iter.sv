// SPDX-License-Identifier: GPL-3.0-or-later
//
// v2_shift_iter: iterative alternative to v2_shift (variant V2d). One bit per
// microstep for SHL/SHA/ROT/ROTC; RVBIT/RVBYT/PASS stay single-cycle. Same
// result/flag outputs; `busy` holds microcode until the count is exhausted.
// Prices the area/throughput trade-off of dropping the barrel shifter.

module v2_shift_iter (
    input             clk,
    input             start,
    input      [31:0] a,
    input       [7:0] count,
    input       [2:0] op,
    input       [1:0] size,
    input             cin,
    output reg [31:0] res,
    output reg        cy,
    output reg        ov,
    output            busy
);

reg  [6:0] n;
reg        left;
reg [31:0] v;
reg        c;
reg  [2:0] o;
reg  [1:0] sz;

wire [31:0] mask = (sz == 2'd0) ? 32'h0000_00ff : (sz == 2'd1) ? 32'h0000_ffff : 32'hffff_ffff;
wire        msb  = (sz == 2'd0) ? v[7] : (sz == 2'd1) ? v[15] : v[31];
wire [31:0] topb = (sz == 2'd0) ? 32'h80 : (sz == 2'd1) ? 32'h8000 : 32'h8000_0000;
assign busy = (n != 7'd0);

reg [7:0] rv8;
integer i;
always @* for (i = 0; i < 8; i = i + 1) rv8[i] = a[7 - i];

// one shift step of the current value
reg  [31:0] nv;
reg         nc, novf;
always @* begin
    nv = v; nc = c; novf = 1'b0;
    case (o)
        3'd0, 3'd6: if (left) begin nc = msb;  nv = (v << 1) & mask; end
                    else      begin nc = v[0]; nv = v >> 1; end
        3'd1: if (left) begin
                  nc = msb; nv = (v << 1) & mask;
                  novf = (msb != ((sz == 2'd0) ? v[6] : (sz == 2'd1) ? v[14] : v[30]));
              end else begin
                  nc = v[0]; nv = (v >> 1) | (msb ? topb : 32'd0);
              end
        3'd2: if (left) begin nc = msb;  nv = ((v << 1) | {31'd0, msb}) & mask; end
              else      begin nc = v[0]; nv = (v >> 1) | (v[0] ? topb : 32'd0); end
        3'd3: if (left) begin nc = msb;  nv = ((v << 1) | {31'd0, c}) & mask; end
              else      begin nc = v[0]; nv = (v >> 1) | (c ? topb : 32'd0); end
        default: ;
    endcase
end

wire [31:0] a_m = a & ((size == 2'd0) ? 32'hff : (size == 2'd1) ? 32'hffff : 32'hffff_ffff);

always @(posedge clk) begin
    if (start) begin
        left <= ~count[7];
        n    <= count[7] ? (~count[6:0] + 7'd1) : count[6:0];
        v    <= a_m;
        c    <= cin;
        o    <= op;
        sz   <= size;
        ov   <= 1'b0;
        cy   <= 1'b0;
        if (op == 3'd4)      res <= {24'd0, rv8};
        else if (op == 3'd5) res <= {a[7:0], a[15:8], a[23:16], a[31:24]};
        else                 res <= a_m;          // zero count: value unchanged
    end else if (n != 7'd0) begin
        n   <= n - 7'd1;
        v   <= nv;
        c   <= nc;
        res <= nv;                                // the value after this step
        cy  <= nc;
        if (novf) ov <= 1'b1;
    end
end

endmodule
