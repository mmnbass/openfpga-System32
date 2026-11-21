// SPDX-License-Identifier: GPL-3.0-or-later
//
// wc_top: Verilator top for the WC-sim whole-chip video simulation. It
// instantiates the UNMODIFIED upstream s32_core with the Golden Axe 2 board
// descriptor (decoded as in experiments/E1-baseline/rtl/ga2_core_harness.sv) and
// the constant ties upstream's MiSTer top uses. Memories behind the SDRAM and
// framebuffer ports are modelled in the C++ testbench. The CPU slot holds
// wc_replay_v60 (module name s32_v60).
//
// Unused inputs (controls, EEPROM load, V25 program load, audio enables) are
// tied to idle values: the sound CPUs are not the subject of this sim.

import s32_pkg::*;

module wc_top (
    input  wire         clk_sys,   // 48.317307 MHz
    input  wire         clk_ram,   // 96.634615 MHz, phase-aligned
    input  wire         clk_v25,   // 24.158653 MHz
    input  wire         rst,
    input  wire         video_rst,
    input  wire         ce_cpu,
    // SDRAM client ports (modelled in C++)
    output wire         sdr_p0_req, output wire sdr_p0_burst, output wire [24:1] sdr_p0_addr,
    input  wire  [63:0] sdr_p0_dout, input wire sdr_p0_ack,
    output wire         sdr_p1_req, output wire [24:3] sdr_p1_addr,
    input  wire  [63:0] sdr_p1_dout, input wire sdr_p1_ack,
    output wire         sdr_p2_req, output wire [24:4] sdr_p2_addr,
    input  wire [127:0] sdr_p2_dout, input wire sdr_p2_ack,
    output wire         sdr_p3_req, output wire [24:1] sdr_p3_addr,
    input  wire  [15:0] sdr_p3_dout, input wire sdr_p3_ack,
    output wire         sdr_p4_req, output wire [24:1] sdr_p4_addr,
    input  wire  [15:0] sdr_p4_dout, input wire sdr_p4_ack,
    output wire         sdr_wave_wr_req, output wire [24:1] sdr_wave_wr_addr,
    output wire  [15:0] sdr_wave_wr_data, output wire [1:0] sdr_wave_wr_be,
    input  wire         sdr_wave_wr_ack,
    output wire         sdr_p5_req, output wire [24:3] sdr_p5_addr,
    input  wire  [63:0] sdr_p5_dout, input wire sdr_p5_ack,
    // sprite framebuffer ports (stubbed in C++: no sprite pixels)
    output wire         fb_wr_start, output wire [1:0] fb_wr_buf, output wire [8:0] fb_wr_x,
    output wire   [7:0] fb_wr_y, output wire fb_wr_valid, output wire [15:0] fb_wr_pix,
    output wire         fb_wr_end, output wire fb_wr_shadow, input wire fb_wr_busy,
    output wire         fb_er_req, output wire [1:0] fb_er_buf, output wire [7:0] fb_er_y,
    input  wire         fb_er_ack,
    output wire         fb_rd_req, output wire [1:0] fb_rd_buf, output wire [7:0] fb_rd_y,
    input  wire         fb_rd_ack, output wire [8:0] fb_rd_x, input wire [15:0] fb_rd_pix,
    // video out
    output wire  [23:0] rgb_a,
    output wire         ce_pix, hs, vs, hb, vb, mode_416_active
);

board_desc_t ga2_board;
always_comb begin
    ga2_board                   = '0;
    ga2_board.multi32           = 1'b0;
    ga2_board.has_v25           = 1'b1;
    ga2_board.v25_table         = 1'b0;   // ga2 opcode table
    ga2_board.has_adc           = 1'b0;
    ga2_board.has_ppi           = 1'b1;
    ga2_board.has_motor_hle     = 1'b0;
    ga2_board.dual_pcb          = 1'b0;
    ga2_board.prot_sel          = PROT_NONE;
    ga2_board.comm_link_hle     = 1'b0;
    ga2_board.sprite_bank_valid = 1'b1;
    ga2_board.sprite_bank_mask  = 2'd3;
end

wire [7:0] adc_ch [0:7];
genvar gi;
generate for (gi = 0; gi < 8; gi = gi + 1) begin : g_adc
    assign adc_ch[gi] = 8'h80;
end endgenerate

wire [23:0] rgb_b_unused;
wire [15:0] eep_rd_data_unused;
wire        eep_modified_unused;
wire signed [15:0] audio_l_unused, audio_r_unused;
wire        wheel_unused;

s32_core core (
    .clk_sys(clk_sys), .clk_ram(clk_ram), .clk_v25(clk_v25),
    .rst(rst), .video_rst(video_rst),
    .board(ga2_board),
    .ce_cpu(ce_cpu), .ce_z80(1'b0), .ce_fm(1'b0), .ce_pcm(1'b0),
    .pause(1'b0),
    .fast_v60(1'b1),
    .sdr_p0_req(sdr_p0_req), .sdr_p0_burst(sdr_p0_burst), .sdr_p0_addr(sdr_p0_addr),
    .sdr_p0_dout(sdr_p0_dout), .sdr_p0_ack(sdr_p0_ack),
    .sdr_p1_req(sdr_p1_req), .sdr_p1_addr(sdr_p1_addr), .sdr_p1_dout(sdr_p1_dout),
    .sdr_p1_ack(sdr_p1_ack),
    .sdr_p2_req(sdr_p2_req), .sdr_p2_addr(sdr_p2_addr), .sdr_p2_dout(sdr_p2_dout),
    .sdr_p2_ack(sdr_p2_ack),
    .sdr_p3_req(sdr_p3_req), .sdr_p3_addr(sdr_p3_addr), .sdr_p3_dout(sdr_p3_dout),
    .sdr_p3_ack(sdr_p3_ack),
    .sdr_p4_req(sdr_p4_req), .sdr_p4_addr(sdr_p4_addr), .sdr_p4_dout(sdr_p4_dout),
    .sdr_p4_ack(sdr_p4_ack),
    .sdr_wave_wr_req(sdr_wave_wr_req), .sdr_wave_wr_addr(sdr_wave_wr_addr),
    .sdr_wave_wr_data(sdr_wave_wr_data), .sdr_wave_wr_be(sdr_wave_wr_be),
    .sdr_wave_wr_ack(sdr_wave_wr_ack),
    .sdr_p5_req(sdr_p5_req), .sdr_p5_addr(sdr_p5_addr), .sdr_p5_dout(sdr_p5_dout),
    .sdr_p5_ack(sdr_p5_ack),
    .fb_wr_start(fb_wr_start), .fb_wr_buf(fb_wr_buf), .fb_wr_x(fb_wr_x), .fb_wr_y(fb_wr_y),
    .fb_wr_valid(fb_wr_valid), .fb_wr_pix(fb_wr_pix), .fb_wr_end(fb_wr_end),
    .fb_wr_shadow(fb_wr_shadow), .fb_wr_busy(fb_wr_busy),
    .fb_er_req(fb_er_req), .fb_er_buf(fb_er_buf), .fb_er_y(fb_er_y), .fb_er_ack(fb_er_ack),
    .fb_rd_req(fb_rd_req), .fb_rd_buf(fb_rd_buf), .fb_rd_y(fb_rd_y), .fb_rd_ack(fb_rd_ack),
    .fb_rd_x(fb_rd_x), .fb_rd_pix(fb_rd_pix),
    .v25_prg_wr(1'b0), .v25_prg_waddr(16'd0), .v25_prg_wdata(8'd0),
    .eep_ld_wr(1'b0), .eep_ld_addr(6'd0), .eep_ld_data(16'd0),
    .eep_rd_data(eep_rd_data_unused), .eep_rd_addr(6'd0),
    .eep_upload(1'b0), .eep_modified(eep_modified_unused),
    .in_p1a(8'hff), .in_p2a(8'hff), .in_portc(8'hff),
    .in_svc12(8'hff), .in_svc34(8'hff),
    .in_p1b(8'hff), .in_p2b(8'hff),
    .in_portc_b(8'hff), .in_svc12_b(8'hff), .in_svc34_b(8'hff),
    .adc_ch(adc_ch),
    .track_p1_x(8'd0), .track_p1_y(8'd0), .track_p2_x(8'd0), .track_p2_y(8'd0),
    .track_p3_x(8'd0), .track_p3_y(8'd0),
    .track_p1_dir(4'd0), .track_p2_dir(4'd0), .track_p3_dir(4'd0),
    .track_mouse_dx(9'sd0), .track_mouse_dy(9'sd0), .track_mouse_strobe(1'b0),
    .trackball_y_invert(1'b0),
    .ppi_pa(8'hff), .ppi_pb(8'hff), .ppi_pc(8'hff),
    .rgb_a(rgb_a), .rgb_b(rgb_b_unused),
    .ce_pix(ce_pix),
    .hs(hs), .vs(vs), .hb(hb), .vb(vb),
    .mode_416_active(mode_416_active),
    .audio_l(audio_l_unused), .audio_r(audio_r_unused),
    .out_lamps(),
    .wheel_load_ch0(wheel_unused)
);

endmodule
