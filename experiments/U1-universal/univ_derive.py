#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""U1 (universal core, stage 1: run-time configuration plumbing). Generates, by exact replacements (each must match the stated
number of times; the inputs are never modified):

  rtl/derived/d2_core_top_univ.sv  from experiments/D2-pocket-top/rtl/derived/d2_core_top_sp.sv (GA2Play8's sp top):
    1. board descriptor: the constant GA2 descriptor -> run_board from the configuration slot (u1_board_cfg); only has_v25,
       has_ppi and the sprite-bank field are run-time (the U1 scope); every other field stays constant 0, as before
    2. controls: d2_controls -> u1_controls with an input-wiring profile (0 = GA2 US ga2u wiring, exactly GA2Play8's; 1 = World
       two-button wiring, exactly SpidPlay1's sm_derive.py replacement 2)
    3. the band's expected slot sums: module parameters (GA2 constants) -> the configuration file's values
    4. Spider-Man's SM1.2 EEPROM persistence, verbatim from sm/spidman experiments/SM1-spidman/sm_derive.py replacement group 4
       (Save slot id 10 at 0x5000_0000, optional default id 4 at 0x4000_0000, sm_eep_bridge_rd, datatable entry 9 = 128),
       now for every game
    5. the configuration slot (id 6, bridge 0x6000_0000, 64 bytes) -> u1_board_cfg; its loads join the load-quiet window; the
       CPU reset stays asserted unless the file is valid and inside the compiled scope
  rtl/derived/s32_core_d2_univ.sv  from experiments/D2-pocket-top/rtl/derived/s32_core_d2_sp.sv:
    6. the V25 DPRAM stub answers only when the board has a V25 (cfg_has_v25); otherwise 0xA00000-0xA00FFF reads FFFF as
       MAME's unmapped range (upstream's own open-bus path)
Nothing else differs. Not changed: PLL, SDRAM, sprite path, V2, freeze readout, band layout, V25, audio (still off).
  univ_derive.py            write both files
  univ_derive.py --check    exit 1 unless the committed files equal the regenerated ones"""
import sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
D2 = HERE.parents[0] / "D2-pocket-top/rtl/derived"
SRC_TOP, SRC_CORE = D2 / "d2_core_top_sp.sv", D2 / "s32_core_d2_sp.sv"
OUT_TOP, OUT_CORE = HERE / "rtl/derived/d2_core_top_univ.sv", HERE / "rtl/derived/s32_core_d2_univ.sv"

REPS = [
    # 1. run-time board descriptor
    ("    board_desc_t ga2_board;\nalways_comb begin\n    ga2_board                   = '0;\n    ga2_board.multi32           = 1'b0;\n"
     "    ga2_board.has_v25           = 1'b1;\n    ga2_board.v25_table         = 1'b0;\n    ga2_board.has_adc           = 1'b0;\n"
     "    ga2_board.has_ppi           = 1'b1;\n    ga2_board.has_motor_hle     = 1'b0;\n    ga2_board.dual_pcb          = 1'b0;\n"
     "    ga2_board.prot_sel          = PROT_NONE;\n    ga2_board.comm_link_hle     = 1'b0;\n    ga2_board.sprite_bank_valid = 1'b1;\n"
     "    ga2_board.sprite_bank_mask  = 2'd3;\nend\n",
     "    board_desc_t run_board;   // U1: the run-time board descriptor (configuration slot, u1_board_cfg); fields outside the U1\n"
     "                              // scope stay constant 0, so synthesis still removes the upstream blocks they gate\nalways_comb begin\n"
     "    run_board                   = '0;\n    run_board.has_v25           = u1_has_v25;\n    run_board.has_ppi           = u1_has_ppi;\n"
     "    run_board.sprite_bank_valid = u1_spr_valid;\n    run_board.sprite_bank_mask  = u1_spr_mask;\nend\n", 1),
    ("    .board(ga2_board),\n", "    .board(run_board),   // U1\n", 1),
    # 2. controls with an input-wiring profile
    ("module d2_controls (\n    input  wire        clk,\n    input  wire [15:0] key,\n",
     "module u1_controls (   // U1: d2_controls + the input-wiring profile\n    input  wire        clk,\n    input  wire [15:0] key,\n"
     "    input  wire  [1:0] profile,      // 0: GA2 US (ga2u) wiring, as d2_controls; 1: World two-button wiring (SpidPlay1)\n", 1),
    ("assign svc12  = ~{3'b000, k_sta, 4'b0000};\nassign ppi_pc = {4'h0, ~k_sel, 3'b111};\n",
     "assign svc12  = profile[0] ? ~{3'b000, k_sta, 1'b0, k_sel, 2'b00} : ~{3'b000, k_sta, 4'b0000};   // 1: b4 1P Start, b2 Coin 1\n"
     "assign ppi_pc = profile[0] ? {4'h0, 4'b1111}                     : {4'h0, ~k_sel, 3'b111};      // 1: port C released\n", 1),
    ("d2_controls ctl (.clk(clk_sys), .key(cont1_key[15:0]), .p1a(g_p1a), .svc12(g_svc12), .ppi_pc(g_ppi_pc));\n",
     "u1_controls ctl (.clk(clk_sys), .key(cont1_key[15:0]), .profile(u1_profile), .p1a(g_p1a), .svc12(g_svc12), .ppi_pc(g_ppi_pc));\n", 1),
    # 3. expected sums from the configuration file
    ("    parameter [31:0]       EXP_SUM0 = 32'ha54f8e76, EXP_FL0 = 32'h9e152dfa,   // ga2u program slot (sum / position-weighted)\n"
     "    parameter [31:0]       EXP_SUM1 = 32'h5e9bf850, EXP_FL1 = 32'h39aeff9a,   // ga2u tile slot\n"
     "    parameter [31:0]       EXP_SUM2 = 32'hbcd81c86, EXP_FL2 = 32'hf5dfbf9c    // SP: ga2 sprite ROM image (slot 3, 16 MiB; checksums only)\n",
     "    parameter [31:0]       EXP_UNUSED = 32'd0     // U1: the band's expected slot sums come from the configuration file\n", 1),
    ("// sums against the expected values of the GA2 (ga2u) slot files (checksums, not ROM data; BASELINE_S2.md section 2)\n",
     "// sums against the expected values carried by the game's configuration file (U1; static after the load)\n", 1),
    ("    sums0_ok <= verify_done && v_sum0 == EXP_SUM0 && v_fl0 == EXP_FL0;\n"
     "    sums1_ok <= verify_done && v_sum1 == EXP_SUM1 && v_fl1 == EXP_FL1;\n"
     "    sums2_ok <= verify_done && v_sum2 == EXP_SUM2 && v_fl2 == EXP_FL2;   // SP: sprite ROM image\n",
     "    sums0_ok <= verify_done && v_sum0 == u1_exp_sum0 && v_fl0 == u1_exp_fl0;\n"
     "    sums1_ok <= verify_done && v_sum1 == u1_exp_sum1 && v_fl1 == u1_exp_fl1;\n"
     "    sums2_ok <= verify_done && v_sum2 == u1_exp_sum2 && v_fl2 == u1_exp_fl2;   // SP: sprite ROM image\n", 1),
    # 5. reset hold on the configuration
    ("    if (!vd_s[1] || !reset_n_s || !locked_s) begin rst_r <= 1'b1; vrst_r <= 1'b1; vcnt_r <= 32'd0; end\n",
     "    if (!vd_s[1] || !reset_n_s || !locked_s || !u1_ok) begin rst_r <= 1'b1; vrst_r <= 1'b1; vcnt_r <= 32'd0; end   // U1: + valid configuration\n", 1),
    ("    if (!allc_s[2] || !wq_empty || l1_en || l2_en || l3_en || wr_req) quiet <= 11'd0;\n",
     "    if (!allc_s[2] || !wq_empty || l1_en || l2_en || l3_en || ed_en || es_en || cf_en || wr_req) quiet <= 11'd0;   // U1: + EEPROM and configuration loads\n", 1),
]
CFG_BLOCK = (
    "// ============================================================================================ U1 board configuration (clk_74a -> clk_sys)\n"
    "// The game's 64-byte configuration file (APF data slot id 6, bridge 0x6000_0000; experiments/U1-universal/u1_cfg.py) selects the\n"
    "// board at run time: descriptor (has_v25, has_ppi, sprite banks), input-wiring profile and the band's expected slot sums. The\n"
    "// CPU reset stays asserted unless the file is complete, valid, and asks only for hardware this bitstream has (u1_board_cfg).\n"
    "    wire        cf_en; wire [7:0] cf_addr; wire [15:0] cf_data;\n"
    "data_loader #(.ADDRESS_MASK_UPPER_4(4'h6), .ADDRESS_SIZE(8), .OUTPUT_WORD_SIZE(2)) cfg_loader (\n"
    "    .clk_74a(clk_74a), .clk_memory(clk_sys), .bridge_wr(bridge_wr), .bridge_endian_little(bridge_endian_little),\n"
    "    .bridge_addr(bridge_addr), .bridge_wr_data(bridge_wr_data),\n"
    "    .write_en(cf_en), .write_addr(cf_addr), .write_data(cf_data));\n"
    "    wire        u1_seen, u1_ok, u1_has_v25, u1_has_ppi, u1_spr_valid; wire [3:0] u1_reason; wire [1:0] u1_spr_mask, u1_profile;\n"
    "    wire [31:0] u1_exp_sum0, u1_exp_fl0, u1_exp_sum1, u1_exp_fl1, u1_exp_sum2, u1_exp_fl2;\n"
    "u1_board_cfg cfg (\n"
    "    .clk(clk_sys), .ld_en(cf_en), .ld_addr(cf_addr), .ld_data(cf_data),\n"
    "    .cfg_seen(u1_seen), .cfg_ok(u1_ok), .reason(u1_reason),\n"
    "    .has_v25(u1_has_v25), .has_ppi(u1_has_ppi), .sprite_bank_valid(u1_spr_valid), .sprite_bank_mask(u1_spr_mask), .profile(u1_profile),\n"
    "    .exp_sum0(u1_exp_sum0), .exp_fl0(u1_exp_fl0), .exp_sum1(u1_exp_sum1), .exp_fl1(u1_exp_fl1),\n"
    "    .exp_sum2(u1_exp_sum2), .exp_fl2(u1_exp_fl2));\n\n")
SDRAM_HDR = "// ============================================================================================ SDRAM controller (upstream)\n"
REPS.append((SDRAM_HDR, CFG_BLOCK + SDRAM_HDR, 1))   # applied before the SM1.2 block, which inserts in front of this same header
# 4. SpidPlay1's EEPROM persistence (sm/spidman experiments/SM1-spidman/sm_derive.py, replacement group 4, verbatim)
SM_REPS = [
    ("    wire    [9:0]   datatable_addr = 0;\n    wire            datatable_wren = 0;\n    wire    [31:0]  datatable_data = 0;\n",
     "    wire    [9:0]   datatable_addr = 10'd9;      // SM1.2: data.json slot index 4 (Save, id 10): entry 2 * 4 + 1 = its size\n"
     "    wire            datatable_wren = 1'b1;\n"
     "    wire    [31:0]  datatable_data = 32'd128;    // SM1.2: 64 x 16-bit 93C46 words\n"),
    ("always @(*) begin\n    casex (bridge_addr)\n    default:      bridge_rd_data <= 0;\n    32'hF8xxxxxx: bridge_rd_data <= cmd_bridge_rd_data;\n",
     "    wire    [31:0]  eep_bridge_rd_data;          // SM1.2: Save slot read-back (sm_eep_bridge_rd below)\n"
     "always @(*) begin\n    casex (bridge_addr)\n    default:      bridge_rd_data <= 0;\n"
     "    32'h5xxxxxxx: bridge_rd_data <= eep_bridge_rd_data;   // SM1.2: Save slot (id 10)\n"
     "    32'hF8xxxxxx: bridge_rd_data <= cmd_bridge_rd_data;\n"),
    ("// ============================================================================================ SDRAM controller (upstream)\n",
     "// ============================================================================================ SM1.2 EEPROM persistence (clk_74a -> clk_sys)\n"
     "// The 93C46's 64 words are an APF nonvolatile Save slot (id 10, bridge 0x5000_0000, 128 bytes, read back over the bridge when\n"
     "// APF unloads the core) with an optional read-only factory-default image (id 4, bridge 0x4000_0000, 128 bytes). Both files use\n"
     "// MAME's nvram/spidman/mainpcb_eeprom format: 64 little-endian 16-bit words, not inverted. data_loader (bridge_endian_little = 0)\n"
     "// delivers each 16-bit word little-endian and sm_eep_bridge_rd returns it the same way, so words pass to and from the EEPROM's\n"
     "// load / read ports unchanged; the EEPROM stores them inverted internally (s32_eeprom_ram: zero-filled RAM = erased FFFF).\n"
     "// Selection: a save write always lands; a default write lands only while no save word has arrived, so the save wins in either\n"
     "// load order, the default applies when the save file is missing, and with neither the EEPROM stays erased (the game then\n"
     "// initialises it itself, slowly enough to lose its timer-0 race: the SM1.1 blocker). Every load ends before dataslot_allcomplete;\n"
     "// the CPU reset waits for allcomplete + 1024 quiet clk_ram with no loader write (EEPROM writes included) + the slot read-back.\n"
     "    wire        ed_en, es_en;\n"
     "    wire  [7:0] ed_addr, es_addr;\n"
     "    wire  [5:0] eu_addr;\n"
     "    wire [15:0] ed_data, es_data, eep_rd_data;\n"
     "data_loader #(.ADDRESS_MASK_UPPER_4(4'h4), .ADDRESS_SIZE(8), .OUTPUT_WORD_SIZE(2)) eepdef_loader (\n"
     "    .clk_74a(clk_74a), .clk_memory(clk_sys), .bridge_wr(bridge_wr), .bridge_endian_little(bridge_endian_little),\n"
     "    .bridge_addr(bridge_addr), .bridge_wr_data(bridge_wr_data),\n"
     "    .write_en(ed_en), .write_addr(ed_addr), .write_data(ed_data));\n"
     "data_loader #(.ADDRESS_MASK_UPPER_4(4'h5), .ADDRESS_SIZE(8), .OUTPUT_WORD_SIZE(2)) eepsave_loader (\n"
     "    .clk_74a(clk_74a), .clk_memory(clk_sys), .bridge_wr(bridge_wr), .bridge_endian_little(bridge_endian_little),\n"
     "    .bridge_addr(bridge_addr), .bridge_wr_data(bridge_wr_data),\n"
     "    .write_en(es_en), .write_addr(es_addr), .write_data(es_data));\n"
     "sm_eep_bridge_rd #(.ADDRESS_MASK_UPPER_4(4'h5), .RD_LAT(3)) eepsave_rd (   // not data_unloader: see the module header\n"
     "    .clk_74a(clk_74a), .clk_sys(clk_sys), .bridge_rd(bridge_rd), .bridge_addr(bridge_addr),\n"
     "    .bridge_rd_data(eep_bridge_rd_data), .eep_rd_addr(eu_addr), .eep_rd_data(eep_rd_data));\n"
     "    reg         save_seen = 1'b0;\n"
     "always @(posedge clk_sys) if (es_en) save_seen <= 1'b1;\n"
     "    wire        eep_ld_wr   = es_en | (ed_en & ~save_seen);\n"
     "    wire  [5:0] eep_ld_addr = es_en ? es_addr[6:1] : ed_addr[6:1];\n"
     "    wire [15:0] eep_ld_data = es_en ? es_data : ed_data;\n"
     "\n"
     "// ============================================================================================ SDRAM controller (upstream)\n"),
    ("    if (!allc_s[2] || !wq_empty || l1_en || l2_en || l3_en || wr_req) quiet <= 11'd0;\n",
     "    if (!allc_s[2] || !wq_empty || l1_en || l2_en || l3_en || ed_en || es_en || wr_req) quiet <= 11'd0;   // SM1.2: + EEPROM loads\n"),
    ("    wire [15:0]  eep_rd_data_unused;\n", ""),
    ("    .eep_ld_wr(1'b0), .eep_ld_addr(6'd0), .eep_ld_data(16'd0),       // power-up EEPROM = erased (FFFF), as S2's image\n"
     "    .eep_rd_data(eep_rd_data_unused), .eep_rd_addr(6'd0),\n",
     "    .eep_ld_wr(eep_ld_wr), .eep_ld_addr(eep_ld_addr), .eep_ld_data(eep_ld_data),   // SM1.2: Save / default slots (EEPROM persistence)\n"
     "    .eep_rd_data(eep_rd_data), .eep_rd_addr(eu_addr),\n"),

]
# the SM1.2 quiet-window line (+ ed_en || es_en) is superseded by replacement 5's line above (+ ed_en || es_en || cf_en)
SM_REPS = [r for r in SM_REPS if "quiet <= 11'd0" not in r[0]]
REPS += [(a, b, 1) for a, b in SM_REPS]
CORE_REPS = [
    ("wire        v25_stub_sel = m_req && !m_we && sel_v25;   // D2-play: whole DPRAM window (S2 variant: A[11:1] 080..0AF only)\n",
     "wire        v25_stub_sel = m_req && !m_we && sel_v25 && cfg_has_v25;   // U1: only on a board with a V25 (else open bus FFFF, as MAME)\n", 1),
    # DM (fit/s32-dmap, Michael 2026-11-22: one private fit): the Pocket configuration video option, OR'd with upstream's expression
    ("    input  board_desc_t board,\n", "    input  board_desc_t board,\n    input             cfg_sprite_over_nbg0,   // DM: Pocket configuration video option\n", 1),
    ("wire sprite_over_nbg0 = (cfg_prot_sel == PROT_SONIC) ||", "wire sprite_over_nbg0 = cfg_sprite_over_nbg0 || (cfg_prot_sel == PROT_SONIC) ||", 1),
    # SN (SegaSonic, Michael 2026-11-23): the work RAM's protection port on the external SRAM (u1_wram_pr time-shares the single
    # SRAM port between the CPU and upstream's protection client). With pr_req never raised (every board without a responder) the
    # module is transparent and the three edits below reduce to the previous text.
    ("reg         pr_ack;\n", "wire        pr_ack;      // SN: a pulse per completed access (u1_wram_pr)\nwire        wram_pr_busy, wram_cpu_rdy;\n"
     "wire [15:0] wram_cpu_addr, wram_cpu_wdata; wire [1:0] wram_cpu_be; wire wram_cpu_we, wram_cpu_rd;\n", 1),
    ("// P1-POCKET-TOP VARIANT: no on-chip work RAM. The CPU port goes to the external SRAM port (wram_ext_*). Port B\n"
     "// (protection) is unused on GA2 (PROT_NONE): it reads open bus; a write to it is reported in simulation.\n"
     "assign wram_ext_addr  = 16'(wram_a);\n"
     "assign wram_ext_wdata = m_wdata;\n"
     "assign wram_ext_be    = m_be;\n"
     "assign wram_ext_we    = m_req && m_we && sel_wram;\n"
     "assign wram_ext_rd    = m_req && !m_we && sel_wram;\n"
     "assign wram_q         = wram_ext_q;\n"
     "assign pr_q           = 16'hffff;\n"
     "`ifdef SIMULATION\n"
     "always @(posedge clk_sys) if (work_pr_we) $display(\"P1 VARIANT ERROR: write to work-RAM port B (protection), which this variant does not have\");\n"
     "`endif\n\n"
     "always @(posedge clk_sys)\n    pr_ack <= pr_req;\n",
     "// P1-POCKET-TOP VARIANT: no on-chip work RAM. The CPU port goes to the external SRAM port (wram_ext_*).\n"
     "// SN: port B (protection, pr_*) shares that port through u1_wram_pr; Burning Rival's byte port (br_pram_we) is not provided.\n"
     "assign wram_cpu_addr  = 16'(wram_a);\n"
     "assign wram_cpu_wdata = m_wdata;\n"
     "assign wram_cpu_be    = m_be;\n"
     "assign wram_cpu_we    = m_req && m_we && sel_wram;\n"
     "assign wram_cpu_rd    = m_req && !m_we && sel_wram;\n"
     "assign wram_q         = wram_ext_q;\n"
     "u1_wram_pr wram_pr (\n"
     "    .clk(clk_sys), .rst(rst),\n"
     "    .cpu_addr(wram_cpu_addr), .cpu_wdata(wram_cpu_wdata), .cpu_be(wram_cpu_be), .cpu_we(wram_cpu_we), .cpu_rd(wram_cpu_rd),\n"
     "    .cpu_rdy(wram_cpu_rdy),\n"
     "    .pr_req(pr_req), .pr_we(pr_we), .pr_addr(16'(pr_wram_a)), .pr_wdata(pr_wdata), .pr_be(pr_be), .pr_q(pr_q), .pr_ack(pr_ack),\n"
     "    .busy(wram_pr_busy),\n"
     "    .x_addr(wram_ext_addr), .x_wdata(wram_ext_wdata), .x_be(wram_ext_be), .x_we(wram_ext_we), .x_rd(wram_ext_rd),\n"
     "    .x_q(wram_ext_q), .x_rdy(wram_ext_rdy));\n"
     "`ifdef SIMULATION\n"
     "always @(posedge clk_sys) if (br_pram_we) $display(\"P1 VARIANT ERROR: write to work-RAM byte port (Burning Rival), which this variant does not have\");\n"
     "`endif\n", 1),
    ("        else if (!m_we && sel_wram && !wram_ext_rdy) begin end   // P1-POCKET-TOP VARIANT: wait for the external SRAM sample\n",
     "        else if (!m_we && sel_wram && !wram_cpu_rdy) begin end   // P1-POCKET-TOP VARIANT: wait for the external SRAM sample\n"
     "        else if (m_we && sel_wram && wram_pr_busy) begin end     // SN: a protection access owns the SRAM port; the write starts after it\n", 1),
    # HS (persistent score block, Michael 2026-11-23): a second client on the work RAM's second port (u1_pr_mux, in rtl/u1_hiscore.sv).
    # The protection responder is passed through combinationally; with hs_req never raised (a top that leaves the ports open, or a
    # game without the feature) the port behaves exactly as before.
    ("    input             cfg_sprite_over_nbg0,   // DM: Pocket configuration video option\n",
     "    input             cfg_sprite_over_nbg0,   // DM: Pocket configuration video option\n"
     "    input             hs_req, hs_we,          // HS: score-block engine (u1_hiscore), work-RAM second-port client\n"
     "    input      [15:0] hs_addr, hs_wdata,\n"
     "    output     [15:0] hs_q,\n"
     "    output            hs_ack,\n", 1),
    ("u1_wram_pr wram_pr (\n",
     "wire        mx_req, mx_we, mx_ack; wire [15:0] mx_addr, mx_wdata; wire [1:0] mx_be;   // HS\n"
     "u1_pr_mux pr_mux (.clk(clk_sys), .rst(rst),\n"
     "    .a_req(pr_req), .a_we(pr_we), .a_addr(16'(pr_wram_a)), .a_wdata(pr_wdata), .a_be(pr_be), .a_ack(pr_ack),\n"
     "    .b_req(hs_req), .b_we(hs_we), .b_addr(hs_addr), .b_wdata(hs_wdata), .b_ack(hs_ack),\n"
     "    .m_req(mx_req), .m_we(mx_we), .m_addr(mx_addr), .m_wdata(mx_wdata), .m_be(mx_be), .m_ack(mx_ack));\n"
     "assign hs_q = pr_q;\n"
     "u1_wram_pr wram_pr (\n", 1),
    ("    .pr_req(pr_req), .pr_we(pr_we), .pr_addr(16'(pr_wram_a)), .pr_wdata(pr_wdata), .pr_be(pr_be), .pr_q(pr_q), .pr_ack(pr_ack),\n",
     "    .pr_req(mx_req), .pr_we(mx_we), .pr_addr(mx_addr), .pr_wdata(mx_wdata), .pr_be(mx_be), .pr_q(pr_q), .pr_ack(mx_ack),\n", 1),
]
HDR_TOP = ("// *** U1 UNIVERSAL DERIVED COPY. Generated by experiments/U1-universal/univ_derive.py from\n"
           "// experiments/D2-pocket-top/rtl/derived/d2_core_top_sp.sv; edit the generator, never this file.\n")
HDR_CORE = ("// *** U1 UNIVERSAL DERIVED COPY. Generated by experiments/U1-universal/univ_derive.py from\n"
            "// experiments/D2-pocket-top/rtl/derived/s32_core_d2_sp.sv; edit the generator, never this file.\n")


def apply(t, reps, what):
    for a, b, n in reps:
        c = t.count(a)
        assert c == n, f"{what}: {a.strip()[:80]!r} found {c}x, expected {n}"
        t = t.replace(a, b)
    return t


def gen():
    return (HDR_TOP + apply(SRC_TOP.read_text(), REPS, "top"), HDR_CORE + apply(SRC_CORE.read_text(), CORE_REPS, "core"))


if __name__ == "__main__":
    top, core = gen()
    if "--check" in sys.argv:
        ok = OUT_TOP.is_file() and OUT_TOP.read_text() == top and OUT_CORE.is_file() and OUT_CORE.read_text() == core
        print("univ top and core equal regenerated:", ok); sys.exit(0 if ok else 1)
    OUT_TOP.parent.mkdir(parents=True, exist_ok=True); OUT_TOP.write_text(top); OUT_CORE.write_text(core); print("wrote", OUT_TOP, OUT_CORE)
