// SPDX-License-Identifier: GPL-3.0-or-later
//
// E1 measurement top, configuration "sr" (cross-check for "vp"): the harness
// inputs come from a serial-in shift chain and its outputs are captured into a
// parallel-load/serial-out chain. Only six real pins exist (three clocks,
// sr_in, sr_load, sr_out), and no virtual pins at all.
//
// Purpose: an independent way to keep every core input unknown and every core
// output observed. If the per-entity numbers for s32_core agree between "vp"
// and "sr", the virtual-pin method is not distorting the core measurement.
// The chains cost about N_IN + N_OUT registers, reported under this top
// entity, and are false-pathed in the SDC so they do not distort timing.
//
// Shift-register-to-RAM inference is disabled on the chain registers only;
// the core keeps the project-wide default.

module ga2_e1_sr_top #(
    parameter int N_IN  = 664,
    parameter int N_OUT = 361
) (
    input  wire clk_sys,
    input  wire clk_ram,
    input  wire clk_v25,
    input  wire sr_in,
    input  wire sr_load,
    output wire sr_out
);

(* altera_attribute = "-name AUTO_SHIFT_REGISTER_RECOGNITION OFF" *)
reg [N_IN-1:0]  in_chain  = '0;
(* altera_attribute = "-name AUTO_SHIFT_REGISTER_RECOGNITION OFF" *)
reg [N_OUT-1:0] out_chain = '0;

wire [N_OUT-1:0] dout;

always @(posedge clk_ram)
    in_chain <= {in_chain[N_IN-2:0], sr_in};

always @(posedge clk_ram)
    if (sr_load) out_chain <= dout;
    else         out_chain <= {out_chain[N_OUT-2:0], 1'b0};

assign sr_out = out_chain[N_OUT-1];

ga2_core_harness #(.N_IN(N_IN), .N_OUT(N_OUT)) harness (
    .clk_sys(clk_sys), .clk_ram(clk_ram), .clk_v25(clk_v25),
    .din(in_chain), .dout(dout)
);

endmodule
