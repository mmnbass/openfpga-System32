// SPDX-License-Identifier: GPL-3.0-or-later
//============================================================================
//  sp_fit_top: STANDALONE FIT HARNESS ONLY (exp/sprite-path). Not a core.
//  Puts the three new SP blocks on the Pocket's FPGA (5CEBA4F23C8) at the D2
//  clock rates so a Quartus map/fit/sta measures their ALMs, M10Ks and
//  register-to-register timing in isolation:
//    sp_sdram (derived controller, all upstream ports + fw/fr)  clk_ram
//    sp_fb_sdram (sprite framebuffer client)                     clk_ram
//    sp_spriteram_win (32 KB sprite RAM window, CPU port clk_sys) clk_ram/clk_sys
//  Every client-side port is registered once here and brought to a VIRTUAL
//  pin (QSF), so no client logic is optimised away and the blocks' own paths
//  start and end at registers. The SDRAM pins are real (auto-placed) so the
//  IOE registers and tri-state are built as in D2; their board I/O timing is
//  D2's (upstream SDC profile) and is not constrained or measured here.
//============================================================================
module sp_fit_top (
    input             clk_ram,       // 96.634615 MHz (SDC)
    input             clk_sys,       // 48.317307 MHz, related (SDC)
    input      [255:0] vin,          // virtual: client request bits (registered below)
    output reg [255:0] vout,         // virtual: client response bits (registered)
    // SDRAM: real pins (auto-placed, D2's FAST_*_REGISTER assignments; I/O timing not constrained here)
    inout      [15:0] dram_dq,
    output     [12:0] dram_a,
    output      [1:0] dram_ba,
    output      [1:0] dram_dqm,
    output            dram_ras_n,
    output            dram_cas_n,
    output            dram_we_n,
    output            dram_cke
);
reg [255:0] vi_r, vi_s;
always @(posedge clk_ram) vi_r <= vin;
always @(posedge clk_sys) vi_s <= vin;

// ---------------------------------------------------------------- controller
wire [12:0] SA; wire [1:0] SBA; wire DQML, DQMH, nCS, nRAS, nCAS, nWE, CKE, ready;
assign dram_a = SA; assign dram_ba = SBA; assign dram_dqm = {DQMH, DQML};
assign dram_ras_n = nRAS; assign dram_cas_n = nCAS; assign dram_we_n = nWE; assign dram_cke = CKE;
wire [63:0] p0_dout, p1_dout, p5_dout; wire [127:0] p2_dout; wire [15:0] p3_dout, p4_dout;
wire p0_ack, p1_ack, p2_ack, p3_ack, p4_ack, p5_ack, wr_ack;
wire fw_req, fw_fill, fw_valid, fw_last, fw_pop, fw_done; wire [24:10] fw_page; wire [8:0] fw_c0, fw_c1, fw_col;
wire [15:0] fw_data; wire [1:0] fw_be;
wire fr_req, fr_urgent, fr_dv, fr_done; wire [24:10] fr_page; wire [8:0] fr_c0, fr_dcol; wire [9:0] fr_n; wire [15:0] fr_dout;


sp_sdram c (
    .clk(clk_ram), .init(vi_r[0]), .ready(ready),
    .SDRAM_DQ(dram_dq), .SDRAM_A(SA), .SDRAM_BA(SBA), .SDRAM_DQML(DQML), .SDRAM_DQMH(DQMH),
    .SDRAM_nCS(nCS), .SDRAM_nCAS(nCAS), .SDRAM_nRAS(nRAS), .SDRAM_nWE(nWE), .SDRAM_CKE(CKE),
    .wr_req(vi_r[1]), .wr_addr(vi_r[40:17]), .wr_din(vi_r[56:41]), .wr_be(vi_r[58:57]), .wr_ack(wr_ack),
    .p0_req(vi_r[2]), .p0_burst(vi_r[3]), .p0_addr(vi_r[82:59]), .p0_dout(p0_dout), .p0_ack(p0_ack),
    .p1_req(vi_r[4]), .p1_addr(vi_r[104:83]), .p1_dout(p1_dout), .p1_ack(p1_ack),
    .p2_req(vi_r[5]), .p2_addr(vi_r[125:105]), .p2_dout(p2_dout), .p2_ack(p2_ack),
    .p3_req(vi_r[6]), .p3_addr(vi_r[149:126]), .p3_dout(p3_dout), .p3_ack(p3_ack),
    .p4_req(vi_r[7]), .p4_addr(vi_r[173:150]), .p4_dout(p4_dout), .p4_ack(p4_ack),
    .p5_req(vi_r[8]), .p5_addr(vi_r[195:174]), .p5_dout(p5_dout), .p5_ack(p5_ack),
    .fw_req(fw_req), .fw_page(fw_page), .fw_fill(fw_fill), .fw_c0(fw_c0), .fw_c1(fw_c1),
    .fw_valid(fw_valid), .fw_col(fw_col), .fw_data(fw_data), .fw_be(fw_be), .fw_last(fw_last),
    .fw_pop(fw_pop), .fw_done(fw_done),
    .fr_req(fr_req), .fr_page(fr_page), .fr_c0(fr_c0), .fr_n(fr_n), .fr_urgent(fr_urgent),
    .fr_dv(fr_dv), .fr_dcol(fr_dcol), .fr_dout(fr_dout), .fr_done(fr_done));

// ---------------------------------------------------------------- framebuffer client
wire wr_busy, er_ack, rd_ack, fb_init_busy; wire [15:0] rd_pix, n_sh, n_es, n_ef, n_lp;
sp_fb_sdram f (
    .clk(clk_ram), .rst(vi_r[9]), .scan_wide(vi_r[10]),
    .wr_start(vi_r[11]), .wr_buf(vi_r[13:12]), .wr_x(vi_r[204:196]), .wr_y(vi_r[212:205]),
    .wr_valid(vi_r[14]), .wr_pix(vi_r[228:213]), .wr_end(vi_r[15]), .wr_shadow(vi_r[16]), .wr_busy(wr_busy),
    .er_req(vi_r[229]), .er_buf(vi_r[231:230]), .er_y(vi_r[239:232]), .er_ack(er_ack),
    .rd_req(vi_r[17]), .rd_buf(vi_r[19:18]), .rd_y(vi_r[27:20]), .rd_ack(rd_ack),
    .rd_x(vi_r[36:28]), .rd_pix(rd_pix),
    .fw_req(fw_req), .fw_page(fw_page), .fw_fill(fw_fill), .fw_c0(fw_c0), .fw_c1(fw_c1),
    .fw_valid(fw_valid), .fw_col(fw_col), .fw_data(fw_data), .fw_be(fw_be), .fw_last(fw_last),
    .fw_pop(fw_pop), .fw_done(fw_done),
    .fr_req(fr_req), .fr_page(fr_page), .fr_c0(fr_c0), .fr_n(fr_n), .fr_urgent(fr_urgent),
    .fr_dv(fr_dv), .fr_dcol(fr_dcol), .fr_dout(fr_dout), .fr_done(fr_done),
    .n_shadow_drop(n_sh), .n_erase_skip(n_es), .n_erase_fill(n_ef), .n_late_publish(n_lp), .init_busy(fb_init_busy));

// ---------------------------------------------------------------- sprite RAM window (D2 sprite_ram parameters)
wire [15:0] q_a, q_b, oow_w, oow_ra, oow_rb;
sp_spriteram_win #(
    .ADDR_WIDTH(16), .NUM_WORDS(65536), .MIXED_RDW_MODE("DONT_CARE"), .PORT_B_READ_ONLY(1'b1)
) win (
    .clock_a(clk_sys), .address_a(vi_s[255:240]), .data_a(vi_s[239:224]), .byteena_a(vi_s[223:222]),
    .wren_a(vi_s[221]), .q_a(q_a),
    .clock_b(clk_ram), .address_b(vi_r[239:224]), .data_b(16'h0000), .byteena_b(2'b00),
    .wren_b(1'b0), .q_b(q_b),
    .rd_strobe_a(vi_s[220]),
    .n_oow_wr_a(oow_w), .n_oow_rd_a(oow_ra), .n_oow_rd_b(oow_rb));

// ---------------------------------------------------------------- observe everything (XOR-folded, registered)
function automatic [63:0] fold128(input [127:0] x); fold128 = x[63:0] ^ x[127:64]; endfunction
always @(posedge clk_ram) begin
    vout[63:0]    <= p0_dout ^ p1_dout ^ p5_dout ^ fold128(p2_dout);
    vout[79:64]   <= p3_dout ^ p4_dout ^ rd_pix ^ q_b ^ n_sh ^ n_es ^ n_ef ^ n_lp ^ oow_rb;
    vout[95:80]   <= {SA, SBA, nCS} ^ {DQML, DQMH, nRAS, nCAS, nWE, CKE, ready, p0_ack, p1_ack, p2_ack,
                      p3_ack, p4_ack, p5_ack, wr_ack, wr_busy, er_ack};
    vout[96]      <= rd_ack ^ fb_init_busy;
    vout[127:97]  <= '0;
end
always @(posedge clk_sys) vout[159:128] <= {q_a ^ oow_w ^ oow_ra, 16'h0};
always @(posedge clk_ram) vout[255:160] <= '0;
endmodule
