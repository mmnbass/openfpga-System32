// SPDX-License-Identifier: GPL-3.0-or-later
//
// d2_diag_band: a tiny on-screen status band over the APF video stream (clk_vid). D2 Pocket top, exploratory.
// Pattern of pocket_jaguar target/pocket/core_top.sv's diagnostic overlay (proven on the Pocket): position counted on the
// written pixels of the outgoing stream, so the band is also drawn on ga2_video's fallback raster when the core makes
// no video at all.
//
// Placement: DE lines ROW0..ROW0+3 (default 220-223, the bottom four of 224), 16 cells of 8 pixels from x = 8; in each
// cell only pixels 1..6 are drawn, everything else is the game's pixel. Cells, left to right:
//   0 PLL locked          1 SDRAM ready           2 all slots received (dataslot_allcomplete)   3 read-back done
//   4 program-ROM sums    5 tile-ROM sums         (4/5: green = equal to the expected sums, red = different, blue = not
//                                                   yet read back)
//   6 CPU out of reset    7 heartbeat (white/grey; toggles with bit HB_BIT of the work-RAM access counter)
//   8-15 IRQ 64 count, bits 7..0 (white = 1, grey = 0): V2 acknowledges of the per-frame interrupt (vector 0x00)
//   flags 0-3, 6: green = yes, red = no.
// Inputs from other clock domains are display-only, quasi-static or slowly counting: each bit passes a two-flop
// synchronizer (a multi-bit count can show a mixed value for one frame; it is never used for control).
module d2_diag_band #(
    parameter int ROW0 = 220,
    parameter int X0   = 8
) (
    input             clk_vid,
    input             en,               // band shown
    // APF stream in (from ga2_video) and out (to the pins)
    input      [23:0] rgb_in,
    input             de, skip, vs,
    output     [23:0] rgb_out,
    output            in_band,          // observation: this pixel is a drawn band pixel
    // status (any domain)
    input             pll_locked, sdram_ready, all_complete, verify_done, sums0_ok, sums1_ok, cpu_running, heartbeat,
    input       [7:0] irq64
);
reg [15:0] s1 = 16'd0, s2 = 16'd0;
always @(posedge clk_vid) begin
    s1 <= {irq64, heartbeat, cpu_running, sums1_ok, sums0_ok, verify_done, all_complete, sdram_ready, pll_locked};
    s2 <= s1;
end
wire [7:0] cnt = s2[15:8];
reg  [9:0] dx = 10'd0;
reg  [8:0] dy = 9'd0;
reg        dde = 1'b0;
always @(posedge clk_vid) begin
    dde <= de;
    if (vs) dy <= 9'd0;
    else if (dde && !de) dy <= dy + 1'b1;
    if (!de) dx <= 10'd0;
    else if (!skip) dx <= dx + 1'b1;
end
localparam [23:0] GRN = 24'h00C000, RED = 24'hE00000, BLU = 24'h0000C0, WHT = 24'hFFFFFF, GRY = 24'h404040;
wire [9:0] rx    = dx - 10'(X0);
wire [3:0] cix  = rx[6:3];
wire       box   = en && de && !skip && (dy >= 9'(ROW0)) && (dy < 9'(ROW0 + 4)) && (dx >= 10'(X0)) && (dx < 10'(X0 + 128))
                   && (rx[2:0] >= 3'd1) && (rx[2:0] <= 3'd6);
function automatic [23:0] flag(input b); flag = b ? GRN : RED; endfunction
reg [23:0] col;
always @(*) begin
    case (cix)
    4'd0: col = flag(s2[0]);
    4'd1: col = flag(s2[1]);
    4'd2: col = flag(s2[2]);
    4'd3: col = flag(s2[3]);
    4'd4: col = !s2[3] ? BLU : flag(s2[4]);
    4'd5: col = !s2[3] ? BLU : flag(s2[5]);
    4'd6: col = flag(s2[6]);
    4'd7: col = s2[7] ? WHT : GRY;
    default: col = cnt[3'(4'd15 - cix)] ? WHT : GRY;
    endcase
end
assign in_band = box;
assign rgb_out = box ? col : rgb_in;
endmodule
