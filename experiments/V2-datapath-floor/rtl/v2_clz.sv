// SPDX-License-Identifier: GPL-3.0-or-later
//
// v2_clz32: count of leading zeros of a 32-bit word (32 when the word is 0),
// built as a balanced tree: bit pairs, then nibbles, bytes, halves and the
// word, each level a 2:1 choice between the upper and lower half's count.
// Depth is log2(32) = 5 mux levels, not a 32-stage chain.
//
// Used by the shifter (leading sign bits, for the SHA left-shift overflow)
// and the bit-field unit (first set bit = leading zeros of the bit-reversed
// word).

module v2_clz32 (
    input      [31:0] x,
    output      [5:0] n
);

// level 1: 16 pairs; z = both zero, c = leading zeros within the pair (0/1)
wire [15:0] z1;
wire [15:0] c1;
// levels 2-5: groups of 4, 8, 16, 32 bits
wire  [7:0] z2;  wire [1:0] c2 [0:7];
wire  [3:0] z3;  wire [2:0] c3 [0:3];
wire  [1:0] z4;  wire [3:0] c4 [0:1];
wire        z5;  wire [4:0] c5;

genvar g;
generate
    for (g = 0; g < 16; g = g + 1) begin : l1
        assign z1[g] = ~x[2*g+1] & ~x[2*g];
        assign c1[g] = ~x[2*g+1];
    end
    for (g = 0; g < 8; g = g + 1) begin : l2      // group g: upper pair 2g+1, lower pair 2g
        assign z2[g] = z1[2*g+1] & z1[2*g];
        assign c2[g] = z1[2*g+1] ? {1'b1, c1[2*g]} : {1'b0, c1[2*g+1]};
    end
    for (g = 0; g < 4; g = g + 1) begin : l3
        assign z3[g] = z2[2*g+1] & z2[2*g];
        assign c3[g] = z2[2*g+1] ? {1'b1, c2[2*g]} : {1'b0, c2[2*g+1]};
    end
    for (g = 0; g < 2; g = g + 1) begin : l4
        assign z4[g] = z3[2*g+1] & z3[2*g];
        assign c4[g] = z3[2*g+1] ? {1'b1, c3[2*g]} : {1'b0, c3[2*g+1]};
    end
endgenerate

assign z5 = z4[1] & z4[0];
assign c5 = z4[1] ? {1'b1, c4[0]} : {1'b0, c4[1]};
assign n  = z5 ? 6'd32 : {1'b0, c5};

endmodule
