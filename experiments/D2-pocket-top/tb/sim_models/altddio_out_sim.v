// SPDX-License-Identifier: GPL-3.0-or-later
// SIMULATION-ONLY behavioural stand-in for Intel's altddio_out, the subset used by the D2 top's SDRAM clock forward
// (width 1, constant datain_h / datain_l, no oe/aclr/aset/sclr/sset use): the output follows datain_h while outclock is
// high and datain_l while it is low. Real DDIO register/IOE delays are not modelled (STA's job).
module altddio_out #(
    parameter extend_oe_disable = "OFF",
    parameter intended_device_family = "Cyclone V",
    parameter invert_output = "OFF",
    parameter lpm_hint = "UNUSED",
    parameter lpm_type = "altddio_out",
    parameter oe_reg = "UNREGISTERED",
    parameter power_up_high = "OFF",
    parameter integer width = 1
) (
    input  [width-1:0] datain_h,
    input  [width-1:0] datain_l,
    input              outclock,
    input              outclocken,
    input              aclr,
    input              aset,
    input              sclr,
    input              sset,
    input              oe,
    output [width-1:0] dataout
);
assign dataout = outclock ? datain_h : datain_l;
endmodule
