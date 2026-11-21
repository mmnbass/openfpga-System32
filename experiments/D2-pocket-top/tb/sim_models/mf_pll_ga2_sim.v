// SPDX-License-Identifier: GPL-3.0-or-later
// SIMULATION-ONLY behavioural stand-in for rtl/mf_pll_ga2.v (D2 Pocket top). Verilator has no PLL: the C++ testbench
// (tb_d2.cpp) generates the five outputs with the intended exact ratios (clk_ram, /2, /4, /8 and /8 shifted by 90 deg,
// phase-aligned) and the `locked` output on top-level inputs of d2_tb, which this model forwards. It says nothing about
// whether the real altera_pll configuration is legal: that is only known from a Quartus fit.
`timescale 1 ps / 1 ps
module mf_pll_ga2 (
    input  wire refclk,
    input  wire rst,
    output wire outclk_0,
    output wire outclk_1,
    output wire outclk_2,
    output wire outclk_3,
    output wire outclk_4,
    output wire locked
);
assign outclk_0 = $root.d2_tb.pll_c0;
assign outclk_1 = $root.d2_tb.pll_c1;
assign outclk_2 = $root.d2_tb.pll_c2;
assign outclk_3 = $root.d2_tb.pll_c3;
assign outclk_4 = $root.d2_tb.pll_c4;
assign locked   = $root.d2_tb.pll_lock;
endmodule
