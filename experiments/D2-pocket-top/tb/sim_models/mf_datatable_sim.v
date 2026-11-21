// SPDX-License-Identifier: GPL-3.0-or-later
// SIMULATION-ONLY stand-in for the core template's apf/mf_datatable.v (an altsyncram 256 x 32 true dual-port RAM with
// registered outputs, initialised from build_id.mif in Quartus): a behavioural dual-port RAM, initial contents zero.
// The D2 core does not use the data table (datatable_* tied 0).
module mf_datatable (
    input  [7:0]  address_a, input [7:0] address_b, input clock_a, input clock_b,
    input  [31:0] data_a, input [31:0] data_b, input wren_a, input wren_b,
    output reg [31:0] q_a = 32'd0, output reg [31:0] q_b = 32'd0
);
reg [31:0] m [0:255];
integer i;
initial for (i = 0; i < 256; i = i + 1) m[i] = 32'd0;
always @(posedge clock_a) begin if (wren_a) m[address_a] <= data_a; q_a <= m[address_a]; end
always @(posedge clock_b) begin if (wren_b) m[address_b] <= data_b; q_b <= m[address_b]; end
endmodule
