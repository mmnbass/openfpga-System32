// SPDX-License-Identifier: GPL-3.0-or-later
//
// ga2_core_harness: the one place where the upstream System 32 core is
// instantiated for E1. Both measurement tops (virtual-pin and scan-register)
// use this module, so the core sees identical ties in every configuration.
//
// What is fixed here, and why:
//   * board   = Golden Axe: The Revenge of Death Adder descriptor, decoded
//               exactly as references/s32/rtl/mem/s32_rom_loader.sv decodes
//               the MRA's index-0 bytes 22 00 00 83 00 (has_v25, ga2 V25
//               opcode table, has_ppi, sprite banks valid, mask 3).
//   * fast_v60, in_portc_b, in_svc12_b, in_svc34_b = the constants the
//               upstream MiSTer top (Arcade-SegaSystem32.sv @ accd644) drives.
//   * out_lamps is left unconnected, as upstream leaves it.
// Everything else the upstream top drives dynamically is a harness input or
// output, so synthesis cannot treat it as constant or unobserved.
//
// The upstream core itself is compiled unmodified from references/s32.

import s32_pkg::*;

module ga2_core_harness #(
    parameter int N_IN  = 664,
    parameter int N_OUT = 361
) (
    input  wire              clk_sys,   // 48.317307 MHz
    input  wire              clk_ram,   // 96.634615 MHz, phase-aligned with clk_sys
    input  wire              clk_v25,   // 24.158653 MHz, asynchronous group
    input  wire [N_IN-1:0]   din,
    output wire [N_OUT-1:0]  dout
);

// ---------------------------------------------------------------------------
// GA2 board descriptor (constant)
// ---------------------------------------------------------------------------
board_desc_t ga2_board;
always_comb begin
    ga2_board                   = '0;
    ga2_board.multi32           = 1'b0;
    ga2_board.has_v25           = 1'b1;
    ga2_board.v25_table         = 1'b0;   // ga2 opcode table
    ga2_board.has_adc           = 1'b0;
    ga2_board.has_ppi           = 1'b1;   // players 3-4
    ga2_board.has_motor_hle     = 1'b0;
    ga2_board.dual_pcb          = 1'b0;
    ga2_board.prot_sel          = PROT_NONE;
    ga2_board.comm_link_hle     = 1'b0;
    ga2_board.sprite_bank_valid = 1'b1;
    ga2_board.sprite_bank_mask  = 2'd3;   // 16 MiB of sprite ROM
end

// ---------------------------------------------------------------------------
// Inputs, unpacked from din (LSB first, in declaration order)
// ---------------------------------------------------------------------------
wire         rst, video_rst, ce_cpu, ce_z80, ce_fm, ce_pcm, pause;
wire  [63:0] sdr_p0_dout;  wire sdr_p0_ack;
wire  [63:0] sdr_p1_dout;  wire sdr_p1_ack;
wire [127:0] sdr_p2_dout;  wire sdr_p2_ack;
wire  [15:0] sdr_p3_dout;  wire sdr_p3_ack;
wire  [15:0] sdr_p4_dout;  wire sdr_p4_ack;
wire         sdr_wave_wr_ack;
wire  [63:0] sdr_p5_dout;  wire sdr_p5_ack;
wire         fb_wr_busy, fb_er_ack, fb_rd_ack;
wire  [15:0] fb_rd_pix;
wire         v25_prg_wr;
wire  [15:0] v25_prg_waddr;
wire   [7:0] v25_prg_wdata;
wire         eep_ld_wr;
wire   [5:0] eep_ld_addr;
wire  [15:0] eep_ld_data;
wire   [5:0] eep_rd_addr;
wire         eep_upload;
wire   [7:0] in_p1a, in_p2a, in_portc, in_svc12, in_svc34, in_p1b, in_p2b;
wire  [63:0] adc_flat;
wire   [7:0] track_p1_x, track_p1_y, track_p2_x, track_p2_y, track_p3_x, track_p3_y;
wire   [3:0] track_p1_dir, track_p2_dir, track_p3_dir;
wire signed [8:0] track_mouse_dx, track_mouse_dy;
wire         track_mouse_strobe, trackball_y_invert;
wire   [7:0] ppi_pa, ppi_pb, ppi_pc;

assign {
    ppi_pc, ppi_pb, ppi_pa,
    trackball_y_invert, track_mouse_strobe, track_mouse_dy, track_mouse_dx,
    track_p3_dir, track_p2_dir, track_p1_dir,
    track_p3_y, track_p3_x, track_p2_y, track_p2_x, track_p1_y, track_p1_x,
    adc_flat,
    in_p2b, in_p1b, in_svc34, in_svc12, in_portc, in_p2a, in_p1a,
    eep_upload, eep_rd_addr, eep_ld_data, eep_ld_addr, eep_ld_wr,
    v25_prg_wdata, v25_prg_waddr, v25_prg_wr,
    fb_rd_pix, fb_rd_ack, fb_er_ack, fb_wr_busy,
    sdr_p5_ack, sdr_p5_dout,
    sdr_wave_wr_ack,
    sdr_p4_ack, sdr_p4_dout,
    sdr_p3_ack, sdr_p3_dout,
    sdr_p2_ack, sdr_p2_dout,
    sdr_p1_ack, sdr_p1_dout,
    sdr_p0_ack, sdr_p0_dout,
    pause, ce_pcm, ce_fm, ce_z80, ce_cpu, video_rst, rst
} = din;

wire [7:0] adc_ch [0:7];
genvar gi;
generate
    for (gi = 0; gi < 8; gi = gi + 1) begin : g_adc
        assign adc_ch[gi] = adc_flat[gi*8 +: 8];
    end
endgenerate

// ---------------------------------------------------------------------------
// Outputs, packed into dout
// ---------------------------------------------------------------------------
wire         sdr_p0_req, sdr_p0_burst;  wire [24:1] sdr_p0_addr;
wire         sdr_p1_req;                wire [24:3] sdr_p1_addr;
wire         sdr_p2_req;                wire [24:4] sdr_p2_addr;
wire         sdr_p3_req;                wire [24:1] sdr_p3_addr;
wire         sdr_p4_req;                wire [24:1] sdr_p4_addr;
wire         sdr_wave_wr_req;           wire [24:1] sdr_wave_wr_addr;
wire  [15:0] sdr_wave_wr_data;          wire  [1:0] sdr_wave_wr_be;
wire         sdr_p5_req;                wire [24:3] sdr_p5_addr;
wire         fb_wr_start, fb_wr_valid, fb_wr_end, fb_wr_shadow;
wire   [1:0] fb_wr_buf;  wire [8:0] fb_wr_x;  wire [7:0] fb_wr_y;  wire [15:0] fb_wr_pix;
wire         fb_er_req;  wire [1:0] fb_er_buf;  wire [7:0] fb_er_y;
wire         fb_rd_req;  wire [1:0] fb_rd_buf;  wire [7:0] fb_rd_y;  wire [8:0] fb_rd_x;
wire  [15:0] eep_rd_data;  wire eep_modified;
wire  [23:0] rgb_a, rgb_b;
wire         ce_pix, hs, vs, hb, vb, mode_416_active;
wire signed [15:0] audio_l, audio_r;
wire         wheel_load_ch0;

assign dout = {
    wheel_load_ch0, audio_r, audio_l,
    mode_416_active, vb, hb, vs, hs, ce_pix, rgb_b, rgb_a,
    eep_modified, eep_rd_data,
    fb_rd_x, fb_rd_y, fb_rd_buf, fb_rd_req,
    fb_er_y, fb_er_buf, fb_er_req,
    fb_wr_shadow, fb_wr_end, fb_wr_pix, fb_wr_valid, fb_wr_y, fb_wr_x, fb_wr_buf, fb_wr_start,
    sdr_p5_addr, sdr_p5_req,
    sdr_wave_wr_be, sdr_wave_wr_data, sdr_wave_wr_addr, sdr_wave_wr_req,
    sdr_p4_addr, sdr_p4_req,
    sdr_p3_addr, sdr_p3_req,
    sdr_p2_addr, sdr_p2_req,
    sdr_p1_addr, sdr_p1_req,
    sdr_p0_addr, sdr_p0_burst, sdr_p0_req
};

// ---------------------------------------------------------------------------
// The upstream core, unmodified
// ---------------------------------------------------------------------------
s32_core core (
    .clk_sys(clk_sys), .clk_ram(clk_ram), .clk_v25(clk_v25),
    .rst(rst), .video_rst(video_rst),
    .board(ga2_board),
    .ce_cpu(ce_cpu), .ce_z80(ce_z80), .ce_fm(ce_fm), .ce_pcm(ce_pcm),
    .pause(pause),
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
    .v25_prg_wr(v25_prg_wr), .v25_prg_waddr(v25_prg_waddr), .v25_prg_wdata(v25_prg_wdata),
    .eep_ld_wr(eep_ld_wr), .eep_ld_addr(eep_ld_addr), .eep_ld_data(eep_ld_data),
    .eep_rd_data(eep_rd_data), .eep_rd_addr(eep_rd_addr),
    .eep_upload(eep_upload), .eep_modified(eep_modified),
    .in_p1a(in_p1a), .in_p2a(in_p2a), .in_portc(in_portc),
    .in_svc12(in_svc12), .in_svc34(in_svc34),
    .in_p1b(in_p1b), .in_p2b(in_p2b),
    .in_portc_b(8'hff), .in_svc12_b(8'hff), .in_svc34_b(8'hff),
    .adc_ch(adc_ch),
    .track_p1_x(track_p1_x), .track_p1_y(track_p1_y),
    .track_p2_x(track_p2_x), .track_p2_y(track_p2_y),
    .track_p3_x(track_p3_x), .track_p3_y(track_p3_y),
    .track_p1_dir(track_p1_dir), .track_p2_dir(track_p2_dir), .track_p3_dir(track_p3_dir),
    .track_mouse_dx(track_mouse_dx), .track_mouse_dy(track_mouse_dy),
    .track_mouse_strobe(track_mouse_strobe),
    .trackball_y_invert(trackball_y_invert),
    .ppi_pa(ppi_pa), .ppi_pb(ppi_pb), .ppi_pc(ppi_pc),
    .rgb_a(rgb_a), .rgb_b(rgb_b),
    .ce_pix(ce_pix),
    .hs(hs), .vs(vs), .hb(hb), .vb(vb),
    .mode_416_active(mode_416_active),
    .audio_l(audio_l), .audio_r(audio_r),
    .out_lamps(),
    .wheel_load_ch0(wheel_load_ch0)
);

endmodule
