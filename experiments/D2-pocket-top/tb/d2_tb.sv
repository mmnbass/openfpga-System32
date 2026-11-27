// SPDX-License-Identifier: GPL-3.0-or-later
//
// TESTBENCH ONLY: Verilator top for the D2 Pocket top (rtl/d2_core_top.sv, module core_top, instance `ic` as in APF's
// apf_top.v). The C++ bench (tb_d2.cpp) drives the PLL output clocks and lock (through tb/sim_models/mf_pll_ga2_sim.v),
// clk_74a, the APF bridge bus, and the SDRAM / SRAM chip models' drive of the two bidirectional DQ buses; it reads the
// pins and the observation outputs below (hierarchical references into the design; they drive nothing).
`default_nettype none
module d2_tb #(
    parameter int CE_MODE  = 1,    // passed to core_top (Verilator -G at build time)
    parameter int RD_EDGES = 3
) (
    input  wire         clk_74a,
    input  wire  [31:0] cont1_key_in,    // Pocket controller 1 key bitmap, driven by the C++ bench (--keys), clk_74a domain
    input  wire  [31:0] cont2_key_in, cont3_key_in, cont4_key_in,   // MP: controllers 2-4 (--keys2/3/4; type nibble 2 = docked game controller)
    input  wire         pll_c0, pll_c1, pll_c2, pll_c3, pll_c4, pll_lock,
    // APF bridge (clk_74a)
    input  wire  [31:0] bridge_addr,
    input  wire         bridge_rd,
    input  wire         bridge_wr,
    input  wire  [31:0] bridge_wr_data,
    output wire  [31:0] bridge_rd_data,
    output wire         bridge_endian_little,
    // SDRAM pins
    output wire  [12:0] dram_a,
    output wire   [1:0] dram_ba,
    output wire   [1:0] dram_dqm,
    output wire         dram_clk, dram_cke, dram_ras_n, dram_cas_n, dram_we_n,
    output wire  [15:0] dram_dq_bus,      // resolved DQ bus
    input  wire  [15:0] dram_dq_chip,     // chip model read data
    input  wire         dram_dq_drive,    // chip model drives DQ
    // SRAM pins
    output wire  [16:0] sram_a,
    output wire  [15:0] sram_dq_bus,
    input  wire  [15:0] sram_dq_chip,
    input  wire         sram_dq_drive,
    output wire         sram_oe_n, sram_we_n, sram_ub_n, sram_lb_n,
    // video pins
    output wire  [23:0] video_rgb,
    output wire         video_rgb_clock, video_rgb_clock_90, video_de, video_skip, video_vs, video_hs,
    // ---- observation (hierarchical, drives nothing) ----
    output wire         core_rst, sdram_ready, verify_done, reset_n_s, allc, sums0_ok, sums1_ok,
    output wire         status_boot_done, dataslot_requestwrite, dataslot_allcomplete,
    output wire  [15:0] dataslot_requestwrite_id,
    output wire  [31:0] dataslot_requestwrite_size,
    output wire  [24:0] size1_r, size2_r,
    output wire  [31:0] v_sum0, v_fl0, v_sum1, v_fl1, v_cycles,
    output wire  [31:0] v_sum2, v_fl2, size3_r_o,     // SP: slot-3 verifier sums / latched size (0 in other configurations)
    output wire         sums2_ok,
    output wire  [31:0] wq_in, wq_out, wq_overflow, wq_collide,
    output wire  [15:0] wq_max_fill,
    output wire  [31:0] sram_reads, sram_writes, sram_overlap,
    output wire         sram_sample_now, sram_core_oe,
    output wire         ob_p0_req, ob_p0_ack, ob_p1_req, ob_p1_ack,
    output wire  [24:3] ob_p1_addr,
    output wire         ob_kick, ob_lstart, ob_ldone, ob_lbusy, ob_vbl_start,
    output wire  [31:0] ob_retired,
    output wire  [23:0] rgb_a,
    output wire         ce_pix, hs, vs, hb, vb, mode_416_active,
    output wire  [23:0] vid_rgb_pre,
    output wire         video_fallback, band_px,
    output wire  [15:0] video_blank_lines,
    output wire   [7:0] irq64,
    output wire   [7:0] ob_in_p1a, ob_in_p2a, ob_in_portc, ob_in_svc12, ob_in_svc34, ob_ppi_pa, ob_ppi_pb, ob_ppi_pc,   // s32_core input ports
    output wire  [15:0] n_p2, n_p3, n_p4, n_p5, n_wave, n_fb_er, n_fb_rd,
    // ---- U1 configuration status (`define U1_UNIV from gen_d2.py --config univ; constant 0 otherwise) ----
    output wire         ob_u1, ob_u1_seen, ob_u1_ok, ob_u1_v25, ob_u1_ppi, ob_u1_sprv,
    output wire   [3:0] ob_u1_reason,
    output wire   [1:0] ob_u1_sprm, ob_u1_prof,
    // ---- SP configuration observation (exp/sprite-int; `define D2_SP from gen_d2.py --config sp; constant 0 otherwise) ----
    output wire         ob_sp,                                   // 1 = SP build
    output wire         ob_p2_req, ob_p2_ack, ob_fr_req, ob_fr_urgent, ob_fr_done,
    output wire   [4:0] ob_spr_rs,                               // s32_sprite renderer FSM state (R_IDLE 0, R_DONE 22, R_DELAY 23)
    output wire         ob_spr_vbl_pending, ob_spr_ready_valid,
    output wire  [15:0] ob_spr_retry,                            // s32_sprite sprite-ROM verify retries
    output wire  [15:0] ob_oow_wr, ob_oow_rd, ob_oow_eng,        // sprite-RAM window out-of-window counters
    output wire  [15:0] ob_fb_shadow_drop, ob_fb_erase_skip, ob_fb_erase_fill, ob_fb_late_publish,
    // ---- GA2Play5 freeze readout (sp only; 0 otherwise): the clk_vid-domain values the readout draws, its pixel flag, V2's ipc ----
    output wire         ob_ro_px, ob_ro_we,
    output wire   [2:0] ob_ro_st,
    output wire  [11:0] ob_ro_upc,
    output wire  [23:0] ob_ro_pc, ob_ro_da, ob_ro_fa,
    output wire   [7:0] ob_ro_fl, ob_ro_age,
    output wire  [31:0] ob_ro_op, ob_ipc,
    // ---- AU configuration (exp/audio1; `define D2_AU from gen_d2.py --config au; constant 0 / unconnected otherwise) ----
    // PSRAM chip 0 pins (the C++ bench's PSRAM model drives cram0 dq through these, as for SRAM / SDRAM) and the I2S pins
    output wire  [21:16] cram0_a,
    output wire         cram0_adv_n, cram0_cre, cram0_ce0_n, cram0_ce1_n, cram0_oe_n, cram0_we_n, cram0_ub_n, cram0_lb_n,
    output wire  [15:0] cram0_dq_bus,
    input  wire  [15:0] cram0_dq_chip,
    input  wire         cram0_dq_drive,
    output wire         audio_mclk, audio_lrck, audio_dac,
    output wire         ob_au, ob_cram0_fpga_oe, ob_wv_clear_done, ob_wv_wd_rd, ob_wv_wd_wr, sums3_ok,
    output wire  [31:0] v_sum3, v_fl3, size4_r_o,
    output wire  [15:0] ob_aud_l, ob_aud_r,
    output wire         ob_sclk,                      // sound_i2s SCLK (clk_74a domain)
    output wire         ob_wv_sample,                 // d2_wave_psram samples cram0 dq at the next clk_sys edge (read, edge E5)
    output wire         ob_wclr_to, ob_wv_ce1_n,      // AU2: clear-wait timeout taken; cram0 die-1 select pin
    // ---- AU3 (exp/audio3; `define D2_AU3 from gen_d2.py --config au3): PSRAM chip 1 = the Z80 sound ROM (d2_rom_psram rmps) ----
    output wire  [21:16] cram1_a,
    output wire         cram1_adv_n, cram1_cre, cram1_ce0_n, cram1_ce1_n, cram1_oe_n, cram1_we_n, cram1_ub_n, cram1_lb_n,
    output wire  [15:0] cram1_dq_bus,
    input  wire  [15:0] cram1_dq_chip,
    input  wire         cram1_dq_drive,
    output wire         ob_au3, ob_cram1_fpga_oe, ob_rp_sample, ob_rp_vf_done, ob_rp_wd_p3, ob_rp_ld_ovf,
    // RC1 (exp/ga2-rc1, au3 only): pause-on-menu observation
    output wire         ob_menu, ob_pause, ob_ce_cpu, ob_ce_z80, ob_ce_fm, ob_ce_pcm, ob_mreq, ob_wrstb, ob_zm1, ob_wave_wr, ob_t0_run, ob_t1_run,
    output wire   [4:0] ob_intc_pend
);
wire [15:0] dram_dq, sram_dq, cram0_dq, cram1_dq;
assign dram_dq = dram_dq_drive ? dram_dq_chip : 16'hzzzz;
assign sram_dq = sram_dq_drive ? sram_dq_chip : 16'hzzzz;
assign cram0_dq = cram0_dq_drive ? cram0_dq_chip : 16'hzzzz;   // AU (sp: the core leaves cram0 dq at Z)
assign cram0_dq_bus = cram0_dq;
assign cram1_dq = cram1_dq_drive ? cram1_dq_chip : 16'hzzzz;   // AU3 (other configurations: the core leaves cram1 dq at Z)
assign cram1_dq_bus = cram1_dq;
assign dram_dq_bus = dram_dq;
assign sram_dq_bus = sram_dq;
wire clk_74b = clk_74a;

core_top #(.CE_MODE(CE_MODE), .RD_EDGES(RD_EDGES)) ic (
    .clk_74a(clk_74a), .clk_74b(clk_74b),
    .cart_tran_bank2(), .cart_tran_bank2_dir(), .cart_tran_bank3(), .cart_tran_bank3_dir(),
    .cart_tran_bank1(), .cart_tran_bank1_dir(), .cart_tran_bank0(), .cart_tran_bank0_dir(),
    .cart_tran_pin30(), .cart_tran_pin30_dir(), .cart_pin30_pwroff_reset(),
    .cart_tran_pin31(), .cart_tran_pin31_dir(),
    .port_ir_rx(1'b0), .port_ir_tx(), .port_ir_rx_disable(),
    .port_tran_si(), .port_tran_si_dir(), .port_tran_so(), .port_tran_so_dir(),
    .port_tran_sck(), .port_tran_sck_dir(), .port_tran_sd(), .port_tran_sd_dir(),
    .cram0_a(cram0_a), .cram0_dq(cram0_dq), .cram0_wait(1'b0), .cram0_clk(), .cram0_adv_n(cram0_adv_n), .cram0_cre(cram0_cre),
    .cram0_ce0_n(cram0_ce0_n), .cram0_ce1_n(cram0_ce1_n), .cram0_oe_n(cram0_oe_n), .cram0_we_n(cram0_we_n), .cram0_ub_n(cram0_ub_n), .cram0_lb_n(cram0_lb_n),
    .cram1_a(cram1_a), .cram1_dq(cram1_dq), .cram1_wait(1'b0), .cram1_clk(), .cram1_adv_n(cram1_adv_n), .cram1_cre(cram1_cre),
    .cram1_ce0_n(cram1_ce0_n), .cram1_ce1_n(cram1_ce1_n), .cram1_oe_n(cram1_oe_n), .cram1_we_n(cram1_we_n), .cram1_ub_n(cram1_ub_n), .cram1_lb_n(cram1_lb_n),
    .dram_a(dram_a), .dram_ba(dram_ba), .dram_dq(dram_dq), .dram_dqm(dram_dqm), .dram_clk(dram_clk), .dram_cke(dram_cke),
    .dram_ras_n(dram_ras_n), .dram_cas_n(dram_cas_n), .dram_we_n(dram_we_n),
    .sram_a(sram_a), .sram_dq(sram_dq), .sram_oe_n(sram_oe_n), .sram_we_n(sram_we_n), .sram_ub_n(sram_ub_n), .sram_lb_n(sram_lb_n),
    .vblank(1'b0), .dbg_tx(), .dbg_rx(1'b1), .user1(), .user2(1'b0), .aux_sda(), .aux_scl(),
    .vpll_feed(),
    .video_rgb(video_rgb), .video_rgb_clock(video_rgb_clock), .video_rgb_clock_90(video_rgb_clock_90),
    .video_de(video_de), .video_skip(video_skip), .video_vs(video_vs), .video_hs(video_hs),
    .audio_mclk(audio_mclk), .audio_adc(1'b0), .audio_dac(audio_dac), .audio_lrck(audio_lrck),
    .bridge_endian_little(bridge_endian_little), .bridge_addr(bridge_addr), .bridge_rd(bridge_rd),
    .bridge_rd_data(bridge_rd_data), .bridge_wr(bridge_wr), .bridge_wr_data(bridge_wr_data),
    .cont1_key(cont1_key_in), .cont2_key(cont2_key_in), .cont3_key(cont3_key_in), .cont4_key(cont4_key_in),   // MP
    .cont1_joy(32'd0), .cont2_joy(32'd0), .cont3_joy(32'd0), .cont4_joy(32'd0),
    .cont1_trig(16'd0), .cont2_trig(16'd0), .cont3_trig(16'd0), .cont4_trig(16'd0)
);

d2_v2_monitor mon (.clk(ic.clk_sys));
`ifdef D2_AU
d2_au_monitor aumon (.clk(ic.clk_sys));   // AU: sound-subsystem event / per-frame lines
assign ob_au = 1'b1; assign ob_cram0_fpga_oe = ic.wvps.dq_oe; assign ob_wv_clear_done = ic.wv_clear_done;
assign ob_wv_wd_rd = ic.wv_wd_rd; assign ob_wv_wd_wr = ic.wv_wd_wr; assign sums3_ok = ic.sums3_ok;
assign v_sum3 = ic.v_sum3; assign v_fl3 = ic.v_fl3; assign size4_r_o = 32'(ic.size4_r);
assign ob_aud_l = ic.aud_l_r; assign ob_aud_r = ic.aud_r_r; assign ob_sclk = ic.sound_i2s.audgen_sclk;
assign ob_wv_sample = ic.wvps.busy && ic.wvps.t == 3'd4 && !ic.wvps.acc_we;
assign ob_wclr_to = ic.wclr_to; assign ob_wv_ce1_n = cram0_ce1_n;
`else
assign ob_au = 1'b0; assign ob_cram0_fpga_oe = 1'b0; assign ob_wv_clear_done = 1'b0; assign ob_wv_wd_rd = 1'b0; assign ob_wv_wd_wr = 1'b0;
assign sums3_ok = 1'b0; assign v_sum3 = 32'd0; assign v_fl3 = 32'd0; assign size4_r_o = 32'd0; assign ob_aud_l = 16'd0; assign ob_aud_r = 16'd0; assign ob_sclk = 1'b0; assign ob_wv_sample = 1'b0;
assign ob_wclr_to = 1'b0; assign ob_wv_ce1_n = 1'b1;
`endif
`ifdef D2_AU3
assign ob_au3 = 1'b1; assign ob_cram1_fpga_oe = ic.rmps.dq_oe;
assign ob_rp_sample = ic.rmps.busy && ic.rmps.t == 3'd4 && !ic.rmps.acc_we;   // d2_rom_psram samples cram1 dq at the next clk_sys edge
assign ob_rp_vf_done = ic.rp_vf_done; assign ob_rp_wd_p3 = ic.rp_wd_p3; assign ob_rp_ld_ovf = ic.rp_ld_ovf;
assign ob_menu = ic.osnotify_inmenu; assign ob_pause = ic.pause_r;   // RC1
assign ob_ce_cpu = ic.ce_cpu; assign ob_ce_z80 = ic.ce_z80; assign ob_ce_fm = ic.ce_fm; assign ob_ce_pcm = ic.ce_pcm;
assign ob_mreq = ic.core.m_req; assign ob_wrstb = ic.core.wr_stb;
assign ob_zm1 = !ic.core.sound.z_m1_n && !ic.core.sound.z_mreq_n && !ic.core.sound.z_rd_n;
assign ob_wave_wr = ic.core.sound.wave_wr_req;
assign ob_t0_run = ic.core.intc.t0_run; assign ob_t1_run = ic.core.intc.t1_run; assign ob_intc_pend = ic.core.intc.pending;
`else
assign ob_au3 = 1'b0; assign ob_cram1_fpga_oe = 1'b0; assign ob_rp_sample = 1'b0; assign ob_rp_vf_done = 1'b0; assign ob_rp_wd_p3 = 1'b0; assign ob_rp_ld_ovf = 1'b0;
assign ob_menu = 1'b0; assign ob_pause = 1'b0; assign ob_ce_cpu = 1'b0; assign ob_ce_z80 = 1'b0; assign ob_ce_fm = 1'b0; assign ob_ce_pcm = 1'b0;
assign ob_mreq = 1'b0; assign ob_wrstb = 1'b0; assign ob_zm1 = 1'b0; assign ob_wave_wr = 1'b0; assign ob_t0_run = 1'b0; assign ob_t1_run = 1'b0;
assign ob_intc_pend = 5'd0;
`endif

assign core_rst = ic.rst_r;            assign sdram_ready = ic.sd_ready;     assign verify_done = ic.verify_done;
assign reset_n_s = ic.reset_n_s;       assign allc = ic.allc_s[2];           assign sums0_ok = ic.sums0_ok;
assign sums1_ok = ic.sums1_ok;         assign status_boot_done = ic.status_boot_done;
assign dataslot_requestwrite = ic.dataslot_requestwrite;  assign dataslot_allcomplete = ic.dataslot_allcomplete;
assign dataslot_requestwrite_id = ic.dataslot_requestwrite_id;  assign dataslot_requestwrite_size = ic.dataslot_requestwrite_size;
assign size1_r = ic.size1_r;           assign size2_r = ic.size2_r;
assign v_sum0 = ic.v_sum0; assign v_fl0 = ic.v_fl0; assign v_sum1 = ic.v_sum1; assign v_fl1 = ic.v_fl1; assign v_cycles = ic.v_cycles;
assign wq_in = ic.wq_in; assign wq_out = ic.wq_out; assign wq_overflow = ic.wq_overflow; assign wq_collide = ic.wq_collide;
assign wq_max_fill = ic.wq_max_fill;
assign sram_reads = ic.sram_reads; assign sram_writes = ic.sram_writes; assign sram_overlap = ic.sram_overlap;
assign sram_sample_now = ic.sram_sample_now; assign sram_core_oe = ic.sram_dq_oe;
assign ob_p0_req = ic.p0_req; assign ob_p0_ack = ic.p0_ack; assign ob_p1_req = ic.p1_req; assign ob_p1_ack = ic.p1_ack;
assign ob_p1_addr = ic.p1_addr;
assign ob_kick = ic.core.tm_render_kick; assign ob_lstart = ic.core.line_start_r; assign ob_ldone = ic.core.tm_line_done;
assign ob_lbusy = ic.core.tm_line_busy; assign ob_vbl_start = ic.core.vbl_start;
assign ob_retired = ic.core.v60.core.obs.retired;
assign rgb_a = ic.rgb_a; assign ce_pix = ic.ce_pix; assign hs = ic.hs; assign vs = ic.vs; assign hb = ic.hb; assign vb = ic.vb;
assign mode_416_active = ic.mode_416_active;
assign vid_rgb_pre = ic.vid_rgb_pre; assign video_fallback = ic.vid_fallback; assign band_px = ic.band_px;
assign ob_in_p1a = ic.core.in_p1a; assign ob_in_p2a = ic.core.in_p2a; assign ob_in_portc = ic.core.in_portc;
assign ob_in_svc12 = ic.core.in_svc12; assign ob_in_svc34 = ic.core.in_svc34;
assign ob_ppi_pa = ic.core.ppi_pa; assign ob_ppi_pb = ic.core.ppi_pb; assign ob_ppi_pc = ic.core.ppi_pc;
assign video_blank_lines = ic.vid_blank_lines; assign irq64 = ic.irq64_cnt;
assign n_p2 = ic.n_p2; assign n_p3 = ic.n_p3; assign n_p4 = ic.n_p4; assign n_p5 = ic.n_p5; assign n_wave = ic.n_wave;
assign n_fb_er = ic.n_fb_er; assign n_fb_rd = ic.n_fb_rd;
`ifdef U1_UNIV   // U1 configuration status (observation only)
assign ob_u1 = 1'b1; assign ob_u1_seen = ic.cfg.cfg_seen; assign ob_u1_ok = ic.cfg.cfg_ok; assign ob_u1_reason = ic.cfg.reason;
assign ob_u1_v25 = ic.cfg.has_v25; assign ob_u1_ppi = ic.cfg.has_ppi; assign ob_u1_sprv = ic.cfg.sprite_bank_valid;
assign ob_u1_sprm = ic.cfg.sprite_bank_mask; assign ob_u1_prof = ic.cfg.profile;
`else
assign ob_u1 = 1'b0; assign ob_u1_seen = 1'b0; assign ob_u1_ok = 1'b0; assign ob_u1_reason = 4'd0; assign ob_u1_v25 = 1'b0;
assign ob_u1_ppi = 1'b0; assign ob_u1_sprv = 1'b0; assign ob_u1_sprm = 2'd0; assign ob_u1_prof = 2'd0;
`endif
`ifdef D2_SP
assign v_sum2 = ic.v_sum2; assign v_fl2 = ic.v_fl2; assign size3_r_o = 32'(ic.size3_r); assign sums2_ok = ic.sums2_ok;
assign ob_sp = 1'b1;
assign ob_p2_req = ic.sp_p2_req; assign ob_p2_ack = ic.sp_p2_ack;
assign ob_fr_req = ic.fr_req; assign ob_fr_urgent = ic.fr_urgent; assign ob_fr_done = ic.fr_done;
assign ob_spr_rs = ic.core.sprite.rs; assign ob_spr_vbl_pending = ic.core.sprite.vblank_pending;
assign ob_spr_ready_valid = ic.core.sprite.ready_valid; assign ob_spr_retry = ic.core.sprite.srom_retry_count;
assign ob_oow_wr = ic.spr_oow_wr; assign ob_oow_rd = ic.spr_oow_rd; assign ob_oow_eng = ic.spr_oow_eng;
assign ob_fb_shadow_drop = ic.fb_n_shadow_drop; assign ob_fb_erase_skip = ic.fb_n_erase_skip;
assign ob_fb_erase_fill = ic.fb_n_erase_fill; assign ob_fb_late_publish = ic.fb_n_late_publish;
assign ob_ro_px = ic.ro_px; assign ob_ro_we = ic.roband.v_we; assign ob_ro_st = ic.roband.v_st; assign ob_ro_upc = ic.roband.v_upc;
assign ob_ro_pc = ic.roband.v_pc; assign ob_ro_da = ic.roband.v_da; assign ob_ro_fa = ic.roband.v_fa; assign ob_ro_fl = ic.roband.v_fl;
assign ob_ro_age = ic.roband.v_age; assign ob_ro_op = ic.roband.v_op; assign ob_ipc = ic.core.v60.core.ipc;
`else
assign v_sum2 = 32'd0; assign v_fl2 = 32'd0; assign size3_r_o = 32'd0; assign sums2_ok = 1'b0;
assign ob_sp = 1'b0;
assign ob_p2_req = 1'b0; assign ob_p2_ack = 1'b0; assign ob_fr_req = 1'b0; assign ob_fr_urgent = 1'b0; assign ob_fr_done = 1'b0;
assign ob_spr_rs = 5'd0; assign ob_spr_vbl_pending = 1'b0; assign ob_spr_ready_valid = 1'b0; assign ob_spr_retry = 16'd0;
assign ob_oow_wr = 16'd0; assign ob_oow_rd = 16'd0; assign ob_oow_eng = 16'd0;
assign ob_fb_shadow_drop = 16'd0; assign ob_fb_erase_skip = 16'd0; assign ob_fb_erase_fill = 16'd0; assign ob_fb_late_publish = 16'd0;
assign ob_ro_px = 1'b0; assign ob_ro_we = 1'b0; assign ob_ro_st = 3'd0; assign ob_ro_upc = 12'd0; assign ob_ro_pc = 24'd0; assign ob_ro_da = 24'd0;
assign ob_ro_fa = 24'd0; assign ob_ro_fl = 8'd0; assign ob_ro_age = 8'd0; assign ob_ro_op = 32'd0; assign ob_ipc = 32'd0;
`endif
endmodule
`default_nettype wire
