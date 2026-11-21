// SPDX-License-Identifier: GPL-3.0-or-later
//
// p1_slot_verify: after both slots are loaded (and before the core leaves reset), reads both regions back from SDRAM through
// upstream sdram.sv's otherwise idle p5 port (4-word bursts) and forms two 32-bit sums per region over the 16-bit
// little-endian SDRAM words w[i]:  sum = sum(w[i]) mod 2^32,  fl = sum over i of sum(w[0..i]) mod 2^32 (position-sensitive,
// so swapped or misplaced words change it). These are the numbers a diagnostic band would show on hardware; the testbench
// computes the same from the slot files. P1 Pocket-top simulation (stage S2), clk_ram.
module p1_slot_verify (
    input              clk,
    input              rst,
    input              start,          // level; sampled once
    input       [24:0] base0, input [24:0] bytes0,
    input       [24:0] base1, input [24:0] bytes1,
    output reg         p5_req = 1'b0,
    output reg  [24:3] p5_addr = 0,
    input       [63:0] p5_dout,
    input              p5_ack,
    output reg         done = 1'b0,
    output reg  [31:0] sum0 = 0, fl0 = 0, sum1 = 0, fl1 = 0,
    output reg  [31:0] cycles = 0
);
reg [1:0]  st = 0;
reg        region = 0;
reg [24:0] off = 0;
reg        ack_d = 1'b0;
reg [31:0] s, f;
always @(posedge clk) begin
    ack_d <= p5_ack;
    p5_req <= 1'b0;
    if (rst) begin
        st <= 0; done <= 1'b0; region <= 0; off <= 0; sum0 <= 0; fl0 <= 0; sum1 <= 0; fl1 <= 0; cycles <= 0;
    end else begin
        if (st != 0 && !done) cycles <= cycles + 1;
        case (st)
        2'd0: if (start && !done) begin st <= 2'd1; region <= 0; off <= 0; end
        2'd1: begin   // issue one burst (one-cycle pulse = one rising edge)
            p5_addr <= 22'(((region ? base1 : base0) + off) >> 3);
            p5_req  <= 1'b1;
            st <= 2'd2;
        end
        2'd2: if (p5_ack && !ack_d) begin
            s = region ? sum1 : sum0; f = region ? fl1 : fl0;
            for (int k = 0; k < 4; k++) begin s = s + 32'(p5_dout[16*k +: 16]); f = f + s; end
            if (region) begin sum1 <= s; fl1 <= f; end else begin sum0 <= s; fl0 <= f; end
            if (off + 25'd8 >= (region ? bytes1 : bytes0)) begin
                if (region) begin done <= 1'b1; st <= 2'd0; end
                else begin region <= 1'b1; off <= 0; st <= 2'd3; end
            end else begin off <= off + 25'd8; st <= 2'd3; end
        end
        2'd3: if (!p5_ack) st <= 2'd1;   // let the stretched ack fall before the next request edge
        endcase
    end
end
endmodule
