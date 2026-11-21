// SPDX-License-Identifier: GPL-3.0-or-later
//
// P1 Pocket-top simulation (stage S2 of docs/pocket-first-pixels-plan.md), SIMULATION ONLY, exploratory.
// Module name wc_top and instance name `core` are kept so the unmodified WC-sim CPU wrapper (wc_v2_shim.sv, whose
// +irqlog/+linestat monitors use $root.wc_top.core) works unchanged.
//
// What is real here (Pocket-facing): upstream rtl/mem/sdram.sv (p0 = CPU program fetch, p1 = tiles, p5 = slot read-back
// verifier before reset release, write port = ROM download; p2-p4 tied idle at the controller), the SDRAM pins (a C++
// behavioural chip model in tb_p1.cpp), work RAM on an external asynchronous SRAM port (p1_sram_port + a C++ SRAM model)
// through the derived s32_core (rtl/sim_variants/s32_core_p1.sv), two verbatim data_loader.sv instances fed by an APF
// bridge BFM in clk_74a, and the ga2_video bridge (clk_vid = clk_sys/4).
// What is NOT a Pocket part: no core_bridge_cmd (the BFM drives dataslot_requestwrite/allcomplete directly), no PLL (the
// testbench makes phase-aligned clk_ram/clk_sys/clk_v25/clk_vid with the real ratios and an asynchronous clk_74a), the
// EEPROM default image is loaded through eep_ld_* as in WC-sim, and the s32_core client ports p2..p5/wave/framebuffer are
// answered by the same C++ stubs as WC-sim (zeros / transparent; they issued 0 requests in the milestone run).
// The CPU slot is the WC-sim V2 wrapper (production microcode, CS_ROM 0 loaded for 3000 clk_sys after power-up).
import s32_pkg::*;

module wc_top (
    input  wire         clk_sys,   // 48.317307 MHz
    input  wire         clk_ram,   // 96.634615 MHz, phase-aligned
    input  wire         clk_v25,   // 24.158653 MHz
    input  wire         clk_vid,   // 12.079327 MHz = clk_sys/4, phase-aligned (Pocket video_rgb_clock)
    input  wire         clk_74a,   // 74.25 MHz APF bridge clock, asynchronous
    input  wire         pll_locked,
    input  wire         ce_mode,          // 0: ce_cpu exactly every 3rd clk_sys (WC-sim); 1: upstream accumulator +21848/65536
    input  wire  [31:0] video_after_rst,  // clk_sys from core-reset release to video-reset release
    input  wire   [2:0] sram_rd_edges,    // SRAM read sample point: clk_sys edges after the address edge (1..7)
    // EEPROM default image (as WC-sim)
    input  wire         eep_ld_wr,
    input  wire   [5:0] eep_ld_addr,
    input  wire  [15:0] eep_ld_data,
    // APF bridge (clk_74a)
    input  wire         bridge_wr,
    input  wire         bridge_endian_little,
    input  wire  [31:0] bridge_addr,
    input  wire  [31:0] bridge_wr_data,
    input  wire         dataslot_requestwrite,
    input  wire         dataslot_allcomplete,
    input  wire  [24:0] slot1_bytes,       // sizes for the read-back verifier (APF reports them with requestwrite)
    input  wire  [24:0] slot2_bytes,
    output wire         status_setup_done_74,   // SDRAM ready, synchronised to clk_74a (APF may start sending)
    // SDRAM pins
    output wire  [12:0] dram_a,
    output wire   [1:0] dram_ba,
    output wire   [1:0] dram_dqm,
    output wire         dram_cs_n, dram_ras_n, dram_cas_n, dram_we_n, dram_cke,
    output wire  [15:0] dram_dq_o,         // resolved DQ bus (what the chip sees)
    input  wire  [15:0] dram_dq_i,         // chip read data
    input  wire         dram_dq_drive,     // chip drives DQ
    // SRAM pins
    output wire  [16:0] sram_a,
    output wire  [15:0] sram_dq_o,
    output wire         sram_dq_oe,
    input  wire  [15:0] sram_dq_i,
    output wire         sram_oe_n, sram_we_n, sram_ub_n, sram_lb_n,
    output wire         sram_sample_now,
    // s32_core client ports answered by the C++ stubs (as WC-sim)
    output wire         sdr_p2_req, output wire [24:4] sdr_p2_addr, input wire [127:0] sdr_p2_dout, input wire sdr_p2_ack,
    output wire         sdr_p3_req, output wire [24:1] sdr_p3_addr, input wire [15:0] sdr_p3_dout, input wire sdr_p3_ack,
    output wire         sdr_p4_req, output wire [24:1] sdr_p4_addr, input wire [15:0] sdr_p4_dout, input wire sdr_p4_ack,
    output wire         sdr_wave_wr_req, output wire [24:1] sdr_wave_wr_addr, output wire [15:0] sdr_wave_wr_data,
    output wire   [1:0] sdr_wave_wr_be, input wire sdr_wave_wr_ack,
    output wire         sdr_p5_req, output wire [24:3] sdr_p5_addr, input wire [63:0] sdr_p5_dout, input wire sdr_p5_ack,
    output wire         fb_wr_start, output wire [1:0] fb_wr_buf, output wire [8:0] fb_wr_x,
    output wire   [7:0] fb_wr_y, output wire fb_wr_valid, output wire [15:0] fb_wr_pix,
    output wire         fb_wr_end, output wire fb_wr_shadow, input wire fb_wr_busy,
    output wire         fb_er_req, output wire [1:0] fb_er_buf, output wire [7:0] fb_er_y, input wire fb_er_ack,
    output wire         fb_rd_req, output wire [1:0] fb_rd_buf, output wire [7:0] fb_rd_y,
    input  wire         fb_rd_ack, output wire [8:0] fb_rd_x, input wire [15:0] fb_rd_pix,
    // video: bridge output (clk_vid) and the core-side signals (reference capture)
    output wire  [23:0] video_rgb,
    output wire         video_de, video_skip, video_hs, video_vs, video_fallback,
    output wire  [15:0] video_blank_lines,
    output wire  [23:0] rgb_a,
    output wire         ce_pix, hs, vs, hb, vb, mode_416_active,
    // status / observation (drive nothing)
    output wire         core_rst,
    output wire         sdram_ready,
    output wire         verify_done,
    output wire  [31:0] v_sum0, v_fl0, v_sum1, v_fl1, v_cycles,
    output wire  [31:0] wq_in, wq_out, wq_overflow, wq_collide,
    output wire  [15:0] wq_max_fill,
    output wire  [31:0] sram_reads, sram_writes, sram_overlap,
    output wire         ob_p0_req, ob_p0_ack, ob_p1_req, ob_p1_ack, ob_p5_ack,
    output wire  [24:3] ob_p1_addr,
    output wire         ob_kick, ob_lstart, ob_ldone, ob_lbusy, ob_vbl_start,
    output wire   [8:0] ob_render_line,
    output wire  [31:0] ob_retired,
    output wire         ob_v2_rst,
    output wire         ob_ce_cpu
);

// ---------------------------------------------------------------- board descriptor (as WC-sim wc_top_v2.sv)
board_desc_t ga2_board;
always_comb begin
    ga2_board                   = '0;
    ga2_board.multi32           = 1'b0;
    ga2_board.has_v25           = 1'b1;
    ga2_board.v25_table         = 1'b0;
    ga2_board.has_adc           = 1'b0;
    ga2_board.has_ppi           = 1'b1;
    ga2_board.has_motor_hle     = 1'b0;
    ga2_board.dual_pcb          = 1'b0;
    ga2_board.prot_sel          = PROT_NONE;
    ga2_board.comm_link_hle     = 1'b0;
    ga2_board.sprite_bank_valid = 1'b1;
    ga2_board.sprite_bank_mask  = 2'd3;
end

// ---------------------------------------------------------------- APF loaders (clk_74a -> clk_sys)
wire        l1_en, l2_en;
wire [24:0] l1_addr, l2_addr;
wire [15:0] l1_data, l2_data;
data_loader #(.ADDRESS_MASK_UPPER_4(4'h1), .ADDRESS_SIZE(25), .OUTPUT_WORD_SIZE(2)) slot1_loader (
    .clk_74a(clk_74a), .clk_memory(clk_sys), .bridge_wr(bridge_wr), .bridge_endian_little(bridge_endian_little),
    .bridge_addr(bridge_addr), .bridge_wr_data(bridge_wr_data),
    .write_en(l1_en), .write_addr(l1_addr), .write_data(l1_data));
data_loader #(.ADDRESS_MASK_UPPER_4(4'h2), .ADDRESS_SIZE(25), .OUTPUT_WORD_SIZE(2)) slot2_loader (
    .clk_74a(clk_74a), .clk_memory(clk_sys), .bridge_wr(bridge_wr), .bridge_endian_little(bridge_endian_little),
    .bridge_addr(bridge_addr), .bridge_wr_data(bridge_wr_data),
    .write_en(l2_en), .write_addr(l2_addr), .write_data(l2_data));

// download-active latch and the all-complete flag, as the Jaguar core_top; synchronised into clk_ram
reg dl_active_74 = 1'b0, allc_74 = 1'b0;
always @(posedge clk_74a) begin
    if (dataslot_requestwrite) begin dl_active_74 <= 1'b1; allc_74 <= 1'b0; end
    else if (dataslot_allcomplete) begin dl_active_74 <= 1'b0; allc_74 <= 1'b1; end
end
reg [2:0] allc_s = 3'b000;
always @(posedge clk_ram) allc_s <= {allc_s[1:0], allc_74};

// ---------------------------------------------------------------- SDRAM controller (upstream, unmodified)
wire        wr_req, wr_ack; wire [24:1] wr_addr; wire [15:0] wr_din; wire [1:0] wr_be;
wire        p0_req, p0_burst, p0_ack; wire [24:1] p0_addr; wire [63:0] p0_dout;
wire        p1_req, p1_ack; wire [24:3] p1_addr; wire [63:0] p1_dout;
wire        p5_req, p5_ack; wire [24:3] p5_addr; wire [63:0] p5_dout;
wire        sd_ready;
wire [15:0] sd_dq;
assign sd_dq = dram_dq_drive ? dram_dq_i : 16'hzzzz;
assign dram_dq_o = sd_dq;
sdram sdram (
    .clk(clk_ram), .init(~pll_locked), .ready(sd_ready),
    .SDRAM_DQ(sd_dq), .SDRAM_A(dram_a), .SDRAM_BA(dram_ba),
    .SDRAM_DQML(dram_dqm[0]), .SDRAM_DQMH(dram_dqm[1]),
    .SDRAM_nCS(dram_cs_n), .SDRAM_nCAS(dram_cas_n), .SDRAM_nRAS(dram_ras_n), .SDRAM_nWE(dram_we_n), .SDRAM_CKE(dram_cke),
    .wr_req(wr_req), .wr_addr(wr_addr), .wr_din(wr_din), .wr_be(wr_be), .wr_ack(wr_ack),
    .p0_req(p0_req), .p0_burst(p0_burst), .p0_addr(p0_addr), .p0_dout(p0_dout), .p0_ack(p0_ack),
    .p1_req(p1_req), .p1_addr(p1_addr), .p1_dout(p1_dout), .p1_ack(p1_ack),
    .p2_req(1'b0), .p2_addr(21'd0), .p2_dout(), .p2_ack(),
    .p3_req(1'b0), .p3_addr(24'd0), .p3_dout(), .p3_ack(),
    .p4_req(1'b0), .p4_addr(24'd0), .p4_dout(), .p4_ack(),
    .p5_req(p5_req), .p5_addr(p5_addr), .p5_dout(p5_dout), .p5_ack(p5_ack)
);
assign sdram_ready = sd_ready;

reg [2:0] rdy74 = 3'b000;
always @(posedge clk_74a) rdy74 <= {rdy74[1:0], sd_ready};
assign status_setup_done_74 = rdy74[2] & pll_locked;

// ---------------------------------------------------------------- load write queue, read-back verifier, reset release
wire wq_empty;
p1_load_wq wq (
    .clk(clk_ram), .rst(~sd_ready),
    .a_en(l1_en), .a_addr(l1_addr), .a_data(l1_data),
    .b_en(l2_en), .b_addr(l2_addr), .b_data(l2_data),
    .a_base(SDR_MAINCPU_BASE), .b_base(SDR_TILES_BASE),
    .wr_req(wr_req), .wr_addr(wr_addr), .wr_din(wr_din), .wr_be(wr_be), .wr_ack(wr_ack),
    .empty(wq_empty), .n_in(wq_in), .n_out(wq_out), .n_overflow(wq_overflow), .n_collide(wq_collide), .max_fill(wq_max_fill));

// "loaders idle": all-complete seen, queue empty and no loader write for 1024 clk_ram (data_loader has no busy output)
reg [10:0] quiet = 0;
always @(posedge clk_ram)
    if (!allc_s[2] || !wq_empty || l1_en || l2_en || wr_req) quiet <= 0;
    else if (!quiet[10]) quiet <= quiet + 1'd1;
wire load_done = quiet[10] && sd_ready;

p1_slot_verify ver (
    .clk(clk_ram), .rst(~sd_ready), .start(load_done),
    .base0(SDR_MAINCPU_BASE), .bytes0(slot1_bytes), .base1(SDR_TILES_BASE), .bytes1(slot2_bytes),
    .p5_req(p5_req), .p5_addr(p5_addr), .p5_dout(p5_dout), .p5_ack(p5_ack),
    .done(verify_done), .sum0(v_sum0), .fl0(v_fl0), .sum1(v_sum1), .fl1(v_fl1), .cycles(v_cycles));

// core reset: held until both slots are in SDRAM, the loaders are idle and the read-back is done; video reset follows after
// video_after_rst clk_sys (a comparison aid for the vblank phase, as WC-sim's --video-delay)
reg [1:0]  vd_s = 2'b00;
always @(posedge clk_sys) vd_s <= {vd_s[0], verify_done};
reg        rst_r = 1'b1, vrst_r = 1'b1;
reg [31:0] vcnt_r = 0;
always @(posedge clk_sys) begin
    if (!vd_s[1] || !pll_locked) begin rst_r <= 1'b1; vrst_r <= 1'b1; vcnt_r <= 0; end
    else begin
        rst_r <= 1'b0;
        if (vcnt_r < video_after_rst) vcnt_r <= vcnt_r + 1; else vrst_r <= 1'b0;
    end
end
assign core_rst = rst_r;

// ---------------------------------------------------------------- ce_cpu
reg [1:0]  c3 = 2'd0;
reg        ce_div3 = 1'b0;
reg [15:0] acc_cpu = 16'd0;
reg        ce_acc = 1'b0;
always @(posedge clk_sys) begin
    c3 <= (c3 == 2'd2) ? 2'd0 : c3 + 2'd1;
    ce_div3 <= (c3 == 2'd2);
    if (rst_r) begin ce_acc <= 1'b0; acc_cpu <= 16'd0; end
    else begin
        logic [16:0] s;
        s = {1'b0, acc_cpu} + 17'd21848;   // Arcade-SegaSystem32.sv: GA2 (has_v25, v25_table 0) cpu_ce_inc
        ce_acc <= s[16]; acc_cpu <= s[15:0];
    end
end
wire ce_cpu = ce_mode ? ce_acc : ce_div3;
assign ob_ce_cpu = ce_cpu;

// ---------------------------------------------------------------- SRAM work-RAM port
wire [15:0] wx_addr, wx_wdata, wx_q; wire [1:0] wx_be; wire wx_we, wx_rd, wx_rdy;
p1_sram_port sram_port (
    .clk(clk_sys), .rd_edges(sram_rd_edges), .rst(rst_r), .addr(wx_addr), .wdata(wx_wdata), .be(wx_be), .we(wx_we), .rd(wx_rd), .q(wx_q), .rdy(wx_rdy),
    .sram_a(sram_a), .sram_dq_o(sram_dq_o), .sram_dq_oe(sram_dq_oe), .sram_dq_i(sram_dq_i),
    .sram_oe_n(sram_oe_n), .sram_we_n(sram_we_n), .sram_ub_n(sram_ub_n), .sram_lb_n(sram_lb_n),
    .sample_now(sram_sample_now), .n_reads(sram_reads), .n_writes(sram_writes), .overlap(sram_overlap));

// ---------------------------------------------------------------- the core (derived s32_core: V25 stub + external work RAM)
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
    .rst(rst_r), .video_rst(vrst_r),
    .board(ga2_board),
    .ce_cpu(ce_cpu), .ce_z80(1'b0), .ce_fm(1'b0), .ce_pcm(1'b0),
    .pause(1'b0),
    .fast_v60(1'b1),
    .sdr_p0_req(p0_req), .sdr_p0_burst(p0_burst), .sdr_p0_addr(p0_addr), .sdr_p0_dout(p0_dout), .sdr_p0_ack(p0_ack),
    .sdr_p1_req(p1_req), .sdr_p1_addr(p1_addr), .sdr_p1_dout(p1_dout), .sdr_p1_ack(p1_ack),
    .sdr_p2_req(sdr_p2_req), .sdr_p2_addr(sdr_p2_addr), .sdr_p2_dout(sdr_p2_dout), .sdr_p2_ack(sdr_p2_ack),
    .sdr_p3_req(sdr_p3_req), .sdr_p3_addr(sdr_p3_addr), .sdr_p3_dout(sdr_p3_dout), .sdr_p3_ack(sdr_p3_ack),
    .sdr_p4_req(sdr_p4_req), .sdr_p4_addr(sdr_p4_addr), .sdr_p4_dout(sdr_p4_dout), .sdr_p4_ack(sdr_p4_ack),
    .sdr_wave_wr_req(sdr_wave_wr_req), .sdr_wave_wr_addr(sdr_wave_wr_addr),
    .sdr_wave_wr_data(sdr_wave_wr_data), .sdr_wave_wr_be(sdr_wave_wr_be), .sdr_wave_wr_ack(sdr_wave_wr_ack),
    .sdr_p5_req(sdr_p5_req), .sdr_p5_addr(sdr_p5_addr), .sdr_p5_dout(sdr_p5_dout), .sdr_p5_ack(sdr_p5_ack),
    .fb_wr_start(fb_wr_start), .fb_wr_buf(fb_wr_buf), .fb_wr_x(fb_wr_x), .fb_wr_y(fb_wr_y),
    .fb_wr_valid(fb_wr_valid), .fb_wr_pix(fb_wr_pix), .fb_wr_end(fb_wr_end),
    .fb_wr_shadow(fb_wr_shadow), .fb_wr_busy(fb_wr_busy),
    .fb_er_req(fb_er_req), .fb_er_buf(fb_er_buf), .fb_er_y(fb_er_y), .fb_er_ack(fb_er_ack),
    .fb_rd_req(fb_rd_req), .fb_rd_buf(fb_rd_buf), .fb_rd_y(fb_rd_y), .fb_rd_ack(fb_rd_ack),
    .fb_rd_x(fb_rd_x), .fb_rd_pix(fb_rd_pix),
    .wram_ext_addr(wx_addr), .wram_ext_wdata(wx_wdata), .wram_ext_be(wx_be), .wram_ext_we(wx_we), .wram_ext_rd(wx_rd),
    .wram_ext_q(wx_q), .wram_ext_rdy(wx_rdy),
    .v25_prg_wr(1'b0), .v25_prg_waddr(16'd0), .v25_prg_wdata(8'd0),
    .eep_ld_wr(eep_ld_wr), .eep_ld_addr(eep_ld_addr), .eep_ld_data(eep_ld_data),
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
    .ppi_pa(8'hff), .ppi_pb(8'hff), .ppi_pc(8'h0f),
    .rgb_a(rgb_a), .rgb_b(rgb_b_unused),
    .ce_pix(ce_pix),
    .hs(hs), .vs(vs), .hb(hb), .vb(vb),
    .mode_416_active(mode_416_active),
    .audio_l(audio_l_unused), .audio_r(audio_r_unused),
    .out_lamps(),
    .wheel_load_ch0(wheel_unused)
);

// ---------------------------------------------------------------- video bridge
ga2_video vbridge (
    .clk_sys(clk_sys), .ce_pix(ce_pix), .rgb(rgb_a), .hs(hs), .vs(vs), .hblank(hb), .vblank(vb),
    .clk_vid(clk_vid), .video_rgb(video_rgb), .video_de(video_de), .video_skip(video_skip),
    .video_hs(video_hs), .video_vs(video_vs), .fallback(video_fallback), .n_blank_lines(video_blank_lines));

// ---------------------------------------------------------------- observation (simulation only; drives nothing)
assign ob_p0_req = p0_req; assign ob_p0_ack = p0_ack; assign ob_p1_req = p1_req; assign ob_p1_ack = p1_ack;
assign ob_p5_ack = p5_ack; assign ob_p1_addr = p1_addr;
assign ob_kick   = core.tm_render_kick;
assign ob_lstart = core.line_start_r;
assign ob_ldone  = core.tm_line_done;
assign ob_lbusy  = core.tm_line_busy;
assign ob_vbl_start = core.vbl_start;
assign ob_render_line = core.render_line;
assign ob_retired = core.v60.core.obs.retired;
assign ob_v2_rst  = core.v60.v2_rst;

endmodule
