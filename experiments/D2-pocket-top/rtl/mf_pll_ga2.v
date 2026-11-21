// SPDX-License-Identifier: GPL-3.0-or-later
//
// GA2 first-pixels PLL (D2 Pocket top, exploratory): ONE fractional PLL from clk_74a, five outputs, all integer
// divisions of one VCO:
//   outclk_0  96.634615 MHz   clk_ram      (VCO / 8)
//   outclk_1  48.317307 MHz   clk_sys      (VCO / 16)
//   outclk_2  24.158653 MHz   clk_v25      (VCO / 32)
//   outclk_3  12.079326 MHz   clk_vid      (VCO / 64)  -> video_rgb_clock
//   outclk_4  12.079326 MHz   clk_vid_90   (VCO / 64, +90 deg = 16 VCO periods = 20696 ps) -> video_rgb_clock_90
// Intended VCO 773.076923 MHz = 74.25 MHz x 10.4118104 (fractional M, N = 1). Counter plan and the alternatives:
// experiments/D2-pocket-top/pll_plan.py and README.md (PLL section). Form: the direct altera_pll instance of
// experiments/HW-cpu-test/pocket/mf_pll_sys.v and pocket_jaguar mf_pllbase.v (both fitted and run on the Pocket).
// The output frequency strings are the project's 6-decimal values (HW-001's "48.317307 MHz" was accepted by the fitter
// for VCO 628.125 / 13). LEGALITY IS NOT KNOWN UNTIL A QUARTUS FIT: HW-001 run 40311704414 passed synthesis and failed
// in the FITTER on an illegal output frequency; a map-only run does not confirm this PLL.
// Fallback if the fitter rejects it: HW-001's two-PLL scheme (mf_pll_sys-style PLL for 96.63/48.32/24.16, a second PLL
// for the video clock) with an asynchronous video crossing in ga2_video (README).
`timescale 1 ps / 1 ps

module mf_pll_ga2 (
    input  wire  refclk,
    input  wire  rst,
    output wire  outclk_0,
    output wire  outclk_1,
    output wire  outclk_2,
    output wire  outclk_3,
    output wire  outclk_4,
    output wire  locked
);

    mf_pll_ga2_0002 pll_inst (
        .refclk   (refclk),
        .rst      (rst),
        .outclk_0 (outclk_0),
        .outclk_1 (outclk_1),
        .outclk_2 (outclk_2),
        .outclk_3 (outclk_3),
        .outclk_4 (outclk_4),
        .locked   (locked)
    );

endmodule


module mf_pll_ga2_0002 (
    input  wire refclk,
    input  wire rst,
    output wire outclk_0,
    output wire outclk_1,
    output wire outclk_2,
    output wire outclk_3,
    output wire outclk_4,
    output wire locked
);

    altera_pll #(
        .fractional_vco_multiplier  ("true"),
        .reference_clock_frequency  ("74.25 MHz"),
        .operation_mode             ("normal"),
        .number_of_clocks           (5),

        .output_clock_frequency0    ("96.634615 MHz"),
        .phase_shift0               ("0 ps"),
        .duty_cycle0                (50),

        .output_clock_frequency1    ("48.317307 MHz"),
        .phase_shift1               ("0 ps"),
        .duty_cycle1                (50),

        .output_clock_frequency2    ("24.158653 MHz"),
        .phase_shift2               ("0 ps"),
        .duty_cycle2                (50),

        .output_clock_frequency3    ("12.079326 MHz"),
        .phase_shift3               ("0 ps"),
        .duty_cycle3                (50),

        .output_clock_frequency4    ("12.079326 MHz"),
        .phase_shift4               ("20696 ps"),
        .duty_cycle4                (50),

        .output_clock_frequency5    ("0 MHz"), .phase_shift5 ("0 ps"), .duty_cycle5 (50),
        .output_clock_frequency6    ("0 MHz"), .phase_shift6 ("0 ps"), .duty_cycle6 (50),
        .output_clock_frequency7    ("0 MHz"), .phase_shift7 ("0 ps"), .duty_cycle7 (50),
        .output_clock_frequency8    ("0 MHz"), .phase_shift8 ("0 ps"), .duty_cycle8 (50),
        .output_clock_frequency9    ("0 MHz"), .phase_shift9 ("0 ps"), .duty_cycle9 (50),
        .output_clock_frequency10   ("0 MHz"), .phase_shift10("0 ps"), .duty_cycle10(50),
        .output_clock_frequency11   ("0 MHz"), .phase_shift11("0 ps"), .duty_cycle11(50),
        .output_clock_frequency12   ("0 MHz"), .phase_shift12("0 ps"), .duty_cycle12(50),
        .output_clock_frequency13   ("0 MHz"), .phase_shift13("0 ps"), .duty_cycle13(50),
        .output_clock_frequency14   ("0 MHz"), .phase_shift14("0 ps"), .duty_cycle14(50),
        .output_clock_frequency15   ("0 MHz"), .phase_shift15("0 ps"), .duty_cycle15(50),
        .output_clock_frequency16   ("0 MHz"), .phase_shift16("0 ps"), .duty_cycle16(50),
        .output_clock_frequency17   ("0 MHz"), .phase_shift17("0 ps"), .duty_cycle17(50),

        .pll_type                   ("General"),
        .pll_subtype                ("General")
    ) altera_pll_i (
        .rst        (rst),
        .outclk     ({outclk_4, outclk_3, outclk_2, outclk_1, outclk_0}),
        .locked     (locked),
        .fboutclk   ( ),
        .fbclk      (1'b0),
        .refclk     (refclk)
    );

endmodule
