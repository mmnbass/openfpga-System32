// SPDX-License-Identifier: GPL-3.0-or-later
//
// E1 measurement top, configuration "vp": every harness input and output is a
// top-level port marked VIRTUAL_PIN in the generated QSF. Inputs are therefore
// unknown to synthesis (nothing folds to a constant) and outputs are observed
// (nothing is swept as unloaded). Only the three clocks are real pins.
//
// Known artefact being measured, not assumed: Quartus implements each virtual
// pin as a logic cell. Those cells live in this top entity, but the fitter can
// pack them into ALMs shared with s32_core logic. The scan-register
// configuration (ga2_e1_sr_top.sv) is the cross-check.

module ga2_e1_vp_top #(
    parameter int N_IN  = 664,
    parameter int N_OUT = 361
) (
    input  wire              clk_sys,
    input  wire              clk_ram,
    input  wire              clk_v25,
    input  wire [N_IN-1:0]   din,
    output wire [N_OUT-1:0]  dout
);

ga2_core_harness #(.N_IN(N_IN), .N_OUT(N_OUT)) harness (
    .clk_sys(clk_sys), .clk_ram(clk_ram), .clk_v25(clk_v25),
    .din(din), .dout(dout)
);

endmodule
