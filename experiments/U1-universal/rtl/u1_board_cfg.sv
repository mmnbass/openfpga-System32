// SPDX-License-Identifier: GPL-3.0-or-later
//
// u1_board_cfg (U1 universal core, exploratory): the per-game configuration file as a register file.
//
// The file is one APF data slot (id 6, bridge 0x6000_0000), 64 bytes, delivered by data_loader as 32 little-endian
// 16-bit words (file bytes [b0, b1] -> word {b1, b0}; bridge_endian_little = 0, OUTPUT_WORD_SIZE = 2). Format
// version 1 (experiments/U1-universal/u1_cfg.py writes it):
//
//   word 0-1   magic "S32C" (bytes 53 33 32 43)          word 2   format version = 1         word 3  reserved 0
//   word 4-11  board descriptor bytes 0-15, verbatim from the game's MRA (rom index 0); decoded with the field map of
//              upstream rtl/mem/s32_rom_loader.sv (desc_r assignments)
//   word 12    input profile (bit 0; bits 15:1 0), or CB: {1, 0, cabinet word[5:0], cabinet low bit[3:0], 000, profile bit 0}
//              (the game's CABINET TYPE in its 93C46 EEPROM: 2-bit value at that word / low bit, 0 = 2P, 1 = 3P, 2 = 4P)
//              word 13 video options (bit 0)
//   word 14-15, 28-30  HS score-block persistence (u1_hiscore): word 14 = {enable, work-RAM word address[14:0]}, word 15 =
//              words - 1 (9 bits), word 28 = {marker 1 value, marker 0 value}, word 29 = marker 1 byte offset (10 bits),
//              word 30 = save-format tag (non-zero). With enable = 0 all five words must be 0 (the files of games without
//              the feature are unchanged).
//   word 16-27 expected slot sums for the diagnostic band: sum0, fl0, sum1, fl1, sum2, fl2 (32-bit little-endian each)
//   word 3     MP coin chute (else 0): bit 15 enable, bit 14 = the value of the EEPROM bit that means one chute per player
//              (individual), bits 11:8 the bit, bits 5:0 the 93C46 word index; bits 13:12 and 7:6 must be 0. The core reads that
//              bit of the game's own EEPROM (its operator setting) to wire the coin inputs; disabled = the regional default.
//   word 31    check word: the 16-bit sum of words 0-31 is 0
//
// Validation (reason code, first failing check wins; ok = 0):
//   1 incomplete: not every word 0-31 was written, or a write beyond byte 63 arrived (short / missing / oversize file)
//   2 bad magic or version
//   3 bad check word
//   4 a board feature outside this bitstream's compiled scope (SCOPE below): only has_v25 (byte 0 bit 1), has_ppi (byte 0
//     bit 5), the sprite-bank field (byte 3 = 0x80, 0x81 or 0x83), SN: the trackball analog profile (byte 1 = 0x30, i.e.
//     analog_profile = 3 with gun_aim / coin_swap 0) and the SegaSonic protection responder (byte 2 = 0x07, prot_sel = 7) may
//     be set; every other descriptor bit must be 0
//   5 bad input profile (not 0 or 1), a reserved word not 0 (MP: or an inconsistent coin-chute word 3; CB: or cabinet word 12), or an inconsistent score-block field (HS: with enable, a
//     reserved bit set, tag 0, the block past the end of the work RAM, or marker 1 outside the block)
// The CPU reset stays asserted unless ok (core top). Outputs are static after the load; with !ok the descriptor
// outputs are all 0.
//
// SCOPE (U1): the descriptor fields outside the mask stay constant 0 in the core, so synthesis still removes the
// upstream blocks they gate (protection HLEs, trackball, ADC, ...), exactly as the constant GA2 descriptor did.
`default_nettype none
module u1_board_cfg (
    input  wire         clk,
    input  wire         ld_en,             // data_loader write strobe (this clock domain)
    input  wire  [7:0]  ld_addr,           // byte address within the slot
    input  wire [15:0]  ld_data,
    output reg          cfg_seen = 1'b0,   // at least one word arrived
    output wire         cfg_ok,
    output wire  [3:0]  reason,
    output wire         has_v25,
    output wire         has_ppi,
    output wire         sprite_bank_valid,
    output wire  [1:0]  sprite_bank_mask,
    output wire  [1:0]  profile,
    output wire         spr_over_nbg0,     // DM: video options (word 13) bit 0
    output wire         trackball,         // SN: descriptor analog_profile = ANALOG_TRACKBALL
    output wire         prot_sonic,        // SN: descriptor prot_sel = PROT_SONIC
    output wire         hs_en,             // HS: score-block persistence (all 0 unless cfg_ok and enabled)
    output wire [14:0]  hs_base,
    output wire  [8:0]  hs_lenm1,
    output wire  [7:0]  hs_m0, hs_m1,
    output wire  [9:0]  hs_m1_off,
    output wire [15:0]  hs_tag,
    output wire         chute_en,          // MP: coin chute from the game's EEPROM (word 3)
    output wire         chute_pol,
    output wire  [3:0]  chute_bit,
    output wire  [5:0]  chute_word,
    output wire         cab_en,            // CB: cabinet type from the game's EEPROM (word 12)
    output wire  [3:0]  cab_bit,
    output wire  [5:0]  cab_word,
    output wire [31:0]  exp_sum0, exp_fl0, exp_sum1, exp_fl1, exp_sum2, exp_fl2
);
    reg  [15:0] w [0:31];
    reg  [31:0] written = 32'd0;
    reg         oversize = 1'b0;
    reg  [15:0] csum = 16'd0;              // running sum of the words as written (each word is written once per load)
    integer i;
initial for (i = 0; i < 32; i = i + 1) w[i] = 16'd0;
always @(posedge clk) if (ld_en) begin
    cfg_seen <= 1'b1;
    if (ld_addr[7:6] != 2'd0) oversize <= 1'b1;
    else begin
        w[ld_addr[5:1]] <= ld_data;
        written[ld_addr[5:1]] <= 1'b1;
        csum <= csum + ld_data;
    end
end
    wire [7:0] d0 = w[4][7:0], d1 = w[4][15:8], d2 = w[5][7:0], d3 = w[5][15:8];
    wire       complete  = (&written) && !oversize;
    wire       magic_ok  = w[0] == 16'h3353 && w[1] == 16'h4332 && w[2] == 16'd1;
    wire       sum_ok    = csum == 16'd0;
    wire       scope_ok  = (d0 & ~8'h22) == 8'h00 && (d1 == 8'h00 || d1 == 8'h30) && (d2 == 8'h00 || d2 == 8'h07) &&
                           (d3 == 8'h80 || d3 == 8'h81 || d3 == 8'h83) &&
                           w[6] == 16'd0 && w[7] == 16'd0 && w[8] == 16'd0 && w[9] == 16'd0 && w[10] == 16'd0 && w[11] == 16'd0;
    wire [15:0] hs_end   = {1'b0, w[14][14:0]} + {7'd0, w[15][8:0]};          // last word of the block
    wire       hs_ok     = w[14][15] ? (w[15][15:9] == 7'd0 && w[29][15:10] == 6'd0 && w[30] != 16'd0 && !hs_end[15] &&
                                        w[29][9:1] <= w[15][8:0])
                                     : (w[14] == 16'd0 && w[15] == 16'd0 && w[28] == 16'd0 && w[29] == 16'd0 && w[30] == 16'd0);
    wire       ch_ok     = w[3][15] ? (w[3][13:12] == 2'd0 && w[3][7:6] == 2'd0) : (w[3] == 16'd0);   // MP
    wire       cb_ok     = w[12][15] ? (w[12][14] == 1'b0 && w[12][3:1] == 3'd0 && w[3][15] && w[12][7:4] != 4'd15)   // CB: needs the chute
                                    : (w[12][15:1] == 15'd0);                                                        // field; low bit <= 14
    wire       rest_ok   = cb_ok && ch_ok && w[13][15:1] == 15'd0 && hs_ok;
assign reason = !complete ? 4'd1 : !magic_ok ? 4'd2 : !sum_ok ? 4'd3 : !scope_ok ? 4'd4 : !rest_ok ? 4'd5 : 4'd0;
assign cfg_ok = reason == 4'd0;
// decoded fields (upstream s32_rom_loader field map), gated by ok
assign has_v25           = cfg_ok && d0[1];
assign has_ppi           = cfg_ok && d0[5];
assign sprite_bank_valid = cfg_ok && d3[7];
assign sprite_bank_mask  = cfg_ok ? d3[1:0] : 2'd0;
assign profile           = cfg_ok ? w[12][1:0] : 2'd0;
assign spr_over_nbg0     = cfg_ok && w[13][0];
assign trackball         = cfg_ok && d1[5];
assign prot_sonic        = cfg_ok && d2[0];
assign hs_en     = cfg_ok && w[14][15];
assign hs_base   = hs_en ? w[14][14:0] : 15'd0;
assign hs_lenm1  = hs_en ? w[15][8:0] : 9'd0;
assign hs_m0     = hs_en ? w[28][7:0] : 8'd0;
assign hs_m1     = hs_en ? w[28][15:8] : 8'd0;
assign hs_m1_off = hs_en ? w[29][9:0] : 10'd0;
assign hs_tag    = hs_en ? w[30] : 16'd0;
assign chute_en   = cfg_ok && w[3][15];
assign chute_pol  = chute_en && w[3][14];
assign chute_bit  = chute_en ? w[3][11:8] : 4'd0;
assign chute_word = chute_en ? w[3][5:0] : 6'd0;
assign cab_en     = cfg_ok && w[12][15];   // CB
assign cab_bit    = cab_en ? w[12][7:4] : 4'd0;
assign cab_word   = cab_en ? w[12][13:8] : 6'd0;
assign exp_sum0 = {w[17], w[16]}; assign exp_fl0 = {w[19], w[18]};
assign exp_sum1 = {w[21], w[20]}; assign exp_fl1 = {w[23], w[22]};
assign exp_sum2 = {w[25], w[24]}; assign exp_fl2 = {w[27], w[26]};
endmodule
`default_nettype wire
