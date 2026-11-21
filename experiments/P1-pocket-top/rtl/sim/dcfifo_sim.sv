// SPDX-License-Identifier: GPL-3.0-or-later
//
// SIMULATION-ONLY behavioural stand-in for Intel's dcfifo, just the subset data_loader.sv uses (showahead OFF, rdempty only,
// no aclr). Gray-coded pointers cross each way through a 2-flop synchroniser, so rdempty lags a write by about two to three
// rdclk edges as in the real part (rdsync_delaypipe is not modelled beyond that). Overflow and underflow are counted and
// reported (expected 0). P1 Pocket-top simulation (stage S2).
module dcfifo #(
    parameter clocks_are_synchronized = "FALSE",
    parameter intended_device_family = "Cyclone V",
    parameter integer lpm_numwords = 4,
    parameter lpm_showahead = "OFF",
    parameter lpm_type = "dcfifo",
    parameter integer lpm_width = 8,
    parameter integer lpm_widthu = 2,
    parameter overflow_checking = "OFF",
    parameter integer rdsync_delaypipe = 5,
    parameter underflow_checking = "OFF",
    parameter use_eab = "OFF",
    parameter integer wrsync_delaypipe = 5
) (
    input  [lpm_width-1:0] data,
    input                  rdclk,
    input                  rdreq,
    input                  wrclk,
    input                  wrreq,
    output reg [lpm_width-1:0] q = '0,
    output                 rdempty
);
localparam integer AW = lpm_widthu;
reg [lpm_width-1:0] mem [0:(1<<AW)-1];
reg [AW:0] wbin = 0, rbin = 0;
reg [AW:0] wgray = 0, rgray = 0;
reg [AW:0] wg_r1 = 0, wg_r2 = 0;   // write pointer (gray) in rdclk
reg [AW:0] rg_w1 = 0, rg_w2 = 0;   // read pointer (gray) in wrclk
function automatic [AW:0] g2b(input [AW:0] g);
    integer i; g2b[AW] = g[AW]; for (i = AW - 1; i >= 0; i = i - 1) g2b[i] = g2b[i + 1] ^ g[i];
endfunction
integer n_over = 0, n_under = 0;
wire [AW:0] rptr_w = g2b(rg_w2);
wire full_w = (wbin - rptr_w) == (AW + 1)'(1 << AW);
always @(posedge wrclk) begin
    rg_w1 <= rgray; rg_w2 <= rg_w1;
    if (wrreq) begin
        if (full_w) n_over = n_over + 1;
        else begin
            mem[wbin[AW-1:0]] <= data;
            wbin  <= wbin + 1'd1;
            wgray <= (wbin + 1'd1) ^ ((wbin + 1'd1) >> 1);
        end
    end
end
wire [AW:0] wptr_r = g2b(wg_r2);
assign rdempty = (wptr_r == rbin);
always @(posedge rdclk) begin
    wg_r1 <= wgray; wg_r2 <= wg_r1;
    if (rdreq) begin
        if (rdempty) n_under = n_under + 1;
        else begin
            q <= mem[rbin[AW-1:0]];
            rbin  <= rbin + 1'd1;
            rgray <= (rbin + 1'd1) ^ ((rbin + 1'd1) >> 1);
        end
    end
end
endmodule
