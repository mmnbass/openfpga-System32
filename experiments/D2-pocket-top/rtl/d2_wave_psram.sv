// SPDX-License-Identifier: GPL-3.0-or-later
//
// d2_wave_psram: the RF5C68's 64 KB wave RAM in Pocket PSRAM chip 0 (cram0, die 0), as one clk_sys client.
// AUDIO plan section 2c, owner decision D1 (a). It replaces d2_ack_stub st_p4 / st_wv.
//
// Client side, exactly as s32_core presents the upstream F2-style external wave path (s32_core.sv:935-944):
//   * sdr_p4_req / sdr_p4_addr[24:1]: RF5C68 wave reads (voice fetches and Z80 reads share this port). The word
//     address is SDR_MULTIPCM_BASE[24:1] + wave_byte_addr[15:1]; only the 15-bit offset from WAVE_BASE is used.
//     sdr_p4_dout returns the whole 16-bit stored word; the RF5C68 picks its lane and inverts it.
//   * sdr_wave_wr_req / addr[24:1] / data[15:0] / be[1:0]: Z80 wave writes, one byte, data already inverted by
//     s32_core (:995-997). be -> ub_n / lb_n, so only the addressed byte is written.
//   * 4-phase handshake (s32_rf5c68.sv:140-181): ack rises with the data valid, stays high until req falls, then
//     falls; the RF5C68 arms its next request only after ack is low.
//   * Write posting (AUDIO A1 rev 2, owner decision after A1): one-entry write buffer. When it is free (and the clear
//     is done, rst low), a write request is latched (address, data, byte enables) and acknowledged on the next
//     clk_sys, so the Z80 is not held in WAIT for the PSRAM cycle (upstream's internal M10K wave RAM had no such
//     wait). A write arriving while the buffer is occupied waits and is acknowledged only once accepted. The
//     buffered write has priority over every read (and a read is not started in the clock a write is accepted), so
//     any read requested after a write was acknowledged is served after that write: read-after-write order holds
//     for every address. The buffer is freed when its PSRAM write starts.
//   * rst drops a posted write that has not started: the clear restarts and rewrites every word anyway, and the
//     RF5C68 / Z80 are reset with it, so nothing can depend on the dropped byte; performing it after the restarted
//     clear would instead corrupt the cleared RAM. A posted write already in flight completes (no cut pulse).
// Storage is inverted (the RF5C68 stores ~byte, s32_rf5c68.sv:68-72, 106-108), so the clear writes 0x0000 to all
// 32,768 words: zero storage reads back as logical 0xFF, the device's power-up state, exactly like upstream's
// zero-initialised M10K. clear_done rises when the clear is complete; until then no request is served, and the
// core must hold the sound subsystem (Z80 / RF5C68) in reset.
//
// PSRAM protocol: asynchronous only, the conventions of experiments/PSRAM-probe (hardware-proven in HW-002:
// async read observed at 8 x 9.402 ns = 75.2 ns). No configuration access is ever made (cre = 0 always), the top
// address 0x3FFFFF is never reached (32K words from PS_BASE = 0), die 1 is never selected, and every strobe is idle
// from power-up and for TPU_CYCLES clocks (200 us, tPU >= 150 us) before the first access.
// One access is 5 clk_sys (103.5 ns at 48.317 MHz, the design basis of plan section 2b) plus one clock with
// ce_n high (tCPH, and the bus-turnaround gap before the FPGA drives dq again):
//   edge E0: ce0_n, adv_n low; address on a/dq (driven); ub_n/lb_n per byte enable (reads: both low)
//   E1:      adv_n high (tVP = tCVS = tAVS = 1 clk);              write: we_n low (tAS = 1 clk)
//   E2:      read: release dq (tAVH = 1 clk);                     write: write data on dq
//   E3:      read: oe_n low (dq released 1 clk earlier)
//   E4:                                                           write: we_n high (tWP 3 clk, tAW/tCW/tBW 4 clk,
//                                                                         tDW 2 clk)
//   E5:      read: dq sampled into the data register (5 clk = 103.5 ns after adv_n fell); ack.
//            ce0_n, oe_n, ub_n, lb_n high, dq released (write tDH = 1 clk); ack.
//   E6:      earliest next access (ce_n high 1 clk, oe/ce high to drive 1 clk).
// Every pad output is a register (power-up values = idle). An access in flight completes even if rst rises, so a
// reset never cuts a write pulse short; a read in flight then is not acknowledged (the client was reset).
//
// Diagnostics: wd_rd / wd_wr are sticky, cleared by rst: set when sdr_p4_req / sdr_wave_wr_req has been high for
// more than WD_LIMIT consecutive clk_sys (upstream's own advice for this path, s32_soundsys.sv:248-274).
// GA2Audio2 (exp/audio1, after the GA2Audio1 seed-3 hardware result "clear never completes"):
//   * wd_rd / wd_wr are cleared by rst. They were set-only with an initial value of 0, and with Power-Up Don't Care = On
//     Quartus made both constant 1 ("Stuck at VCC due to stuck port data_in", seed-3 map report).
//   * cram_ce1_n is a continuous 1. It was an initialised reg that nothing assigned; Quartus ignores the initial value of
//     an undriven reg (Warning 10034 "has no driver") and tied the pin to GND, i.e. die 1 was selected all the time
//     (seed-3 map report, Warning 13410 "cram0_ce1_n is stuck at GND"). Verilator kept the initial value, so no
//     simulation could see it.
//   * Power-up robustness: (a) while the sequencer is idle and does not start an access, every strobe is driven to its
//     idle level (a no-op from every reachable state: an access always ends with all strobes idle); before, a power-up
//     of cram_ce0_n = 0 with busy = 0 blocked every start for ever (the start needs cram_ce0_n = 1). (b) While the tPU
//     wait runs (tpu_cnt powers up to TPU_CYCLES; non-zero initial values are kept, the seed-3 map report lists the
//     inverted tpu_cnt bits), the clear state is held at its rst value, so an arbitrary power-up of clear_done / clr_idx
//     is repaired even if rst never pulses. Both are no-ops from the intended power-up state.
//   * Observation outputs dbg_* for the band (tpu_ok, clr_idx[15:11], a bit that toggles at the end of every PSRAM access,
//     busy). They feed nothing else.

module d2_wave_psram #(
    parameter [24:0]  WAVE_BASE  = 25'h0A0_0000,   // SDR_MULTIPCM_BASE (byte address; s32_pkg.sv:74)
    parameter [21:0]  PS_BASE    = 22'h000000,     // PSRAM word address of wave word 0 (die 0)
    parameter integer TPU_CYCLES = 9664,           // 200 us at 48.317 MHz: pins idle after configuration
    parameter integer WD_LIMIT   = 1024
) (
    input             clk,                         // clk_sys
    input             rst,                         // restarts the clear; in-flight access completes
    // RF5C68 wave reads (s32_core sdr_p4_*)
    input             sdr_p4_req,
    input      [24:1] sdr_p4_addr,
    output reg [15:0] sdr_p4_dout = 16'h0000,
    output reg        sdr_p4_ack = 1'b0,
    // RF5C68 wave writes (s32_core sdr_wave_wr_*)
    input             sdr_wave_wr_req,
    input      [24:1] sdr_wave_wr_addr,
    input      [15:0] sdr_wave_wr_data,
    input       [1:0] sdr_wave_wr_be,
    output reg        sdr_wave_wr_ack = 1'b0,
    // status / diagnostics
    output reg        clear_done = 1'b0,
    output reg        wd_rd = 1'b0,
    output reg        wd_wr = 1'b0,
    // PSRAM chip pins (cram0)
    output reg [21:16] cram_a = 6'd0,
    inout      [15:0] cram_dq,
    input             cram_wait,
    output            cram_clk,
    output reg        cram_adv_n = 1'b1,
    output reg        cram_cre = 1'b0,
    output reg        cram_ce0_n = 1'b1,
    output            cram_ce1_n,                  // die 1 never selected (continuous 1; see the header)
    output reg        cram_oe_n = 1'b1,
    output reg        cram_we_n = 1'b1,
    output reg        cram_ub_n = 1'b1,
    output reg        cram_lb_n = 1'b1,
    // observation only (GA2Audio2 band cells 21-28)
    output            dbg_tpu_ok,
    output     [15:11] dbg_clr_idx,
    output reg        dbg_acc_tgl = 1'b0,          // toggles at the end of every PSRAM access
    output            dbg_busy
);

assign cram_clk = 1'b0;                            // asynchronous mode: no clock
assign cram_ce1_n = 1'b1;
wire unused_wait = cram_wait;

reg  [15:0] dq_o = 16'h0000;
reg         dq_oe = 1'b0;
assign cram_dq = dq_oe ? dq_o : 16'hZZZZ;

wire [24:1] base_w = WAVE_BASE[24:1];
/* verilator lint_off UNUSEDSIGNAL */
wire [24:1] rd_off = sdr_p4_addr - base_w;         // only [15:1] is used: the 64 KB aperture
wire [24:1] wr_off = sdr_wave_wr_addr - base_w;
/* verilator lint_on UNUSEDSIGNAL */

// ---- power-up wait (tPU), then the clear ---------------------------------------------------------------------
reg  [13:0] tpu_cnt = 14'(TPU_CYCLES);
wire        tpu_ok  = (tpu_cnt == 14'd0);
always @(posedge clk) if (!tpu_ok) tpu_cnt <= tpu_cnt - 14'd1;

reg  [15:0] clr_idx = 16'd0;                       // next word to clear; bit 15 = all issued

// ---- access sequencer ---------------------------------------------------------------------------------------
reg         busy = 1'b0;
reg         acc_we = 1'b0;                         // 1 = write
reg   [1:0] acc_who = 2'd0;                        // 0 clear, 1 Z80 write, 2 read
reg  [15:0] acc_wdata = 16'h0000;
reg   [2:0] t = 3'd0;
reg         acc_kill = 1'b0;                       // rst seen during this access: complete it, but do not ack
reg         wb_valid = 1'b0;                       // posted-write buffer
reg  [14:0] wb_word = 15'd0;
reg  [15:0] wb_data = 16'h0000;
reg   [1:0] wb_be = 2'b00;

wire start_clr = !clear_done && !clr_idx[15];
wire accept_wr = clear_done && !rst && !wb_valid && sdr_wave_wr_req && !sdr_wave_wr_ack;
wire start_wr  = clear_done && wb_valid;
wire start_rd  = clear_done && sdr_p4_req && !sdr_p4_ack && !accept_wr;

always @(posedge clk) begin
    // 4-phase: ack falls once req has fallen
    if (sdr_p4_ack && !sdr_p4_req) sdr_p4_ack <= 1'b0;
    if (sdr_wave_wr_ack && !sdr_wave_wr_req) sdr_wave_wr_ack <= 1'b0;
    if (accept_wr) begin                           // post the write and acknowledge it on the next clock
        wb_valid <= 1'b1; wb_word <= wr_off[15:1]; wb_data <= sdr_wave_wr_data; wb_be <= sdr_wave_wr_be;
        sdr_wave_wr_ack <= 1'b1;
    end

    if (busy) begin
        t <= t + 3'd1;
        case (t)
        3'd0: begin cram_adv_n <= 1'b1; if (acc_we) cram_we_n <= 1'b0; end
        3'd1: if (acc_we) dq_o <= acc_wdata; else dq_oe <= 1'b0;
        3'd2: if (!acc_we) cram_oe_n <= 1'b0;
        3'd3: if (acc_we) cram_we_n <= 1'b1;
        default: begin                             // t == 4: end of the access (edge E5)
            busy <= 1'b0;
            cram_ce0_n <= 1'b1; cram_oe_n <= 1'b1; cram_we_n <= 1'b1;
            cram_ub_n <= 1'b1; cram_lb_n <= 1'b1; dq_oe <= 1'b0;
            if (acc_who == 2'd2) sdr_p4_dout <= cram_dq;
            if (acc_who == 2'd2 && !acc_kill && !rst) sdr_p4_ack <= 1'b1;
            if (acc_who == 2'd0 && clr_idx[15] && !rst) clear_done <= 1'b1;
            dbg_acc_tgl <= !dbg_acc_tgl;
        end
        endcase
    end
    else if (cram_ce0_n && tpu_ok && !rst && (start_clr || start_wr || start_rd)) begin
        // E0 (cram_ce0_n is high here: at least one clock since the previous access ended)
        busy <= 1'b1; t <= 3'd0; acc_kill <= 1'b0;
        cram_ce0_n <= 1'b0; cram_adv_n <= 1'b0; dq_oe <= 1'b1; cram_cre <= 1'b0;
        if (start_clr) begin
            acc_we <= 1'b1; acc_who <= 2'd0; acc_wdata <= 16'h0000;
            {cram_a, dq_o} <= PS_BASE + {7'd0, clr_idx[14:0]};
            cram_ub_n <= 1'b0; cram_lb_n <= 1'b0;
            clr_idx <= clr_idx + 16'd1;
        end
        else if (start_wr) begin                   // posted write before voice/CPU read
            acc_we <= 1'b1; acc_who <= 2'd1; acc_wdata <= wb_data;
            {cram_a, dq_o} <= PS_BASE + {7'd0, wb_word};
            cram_ub_n <= !wb_be[1]; cram_lb_n <= !wb_be[0];
            wb_valid <= 1'b0;                      // buffer free once its write has started
        end
        else begin
            acc_we <= 1'b0; acc_who <= 2'd2;
            {cram_a, dq_o} <= PS_BASE + {7'd0, rd_off[15:1]};
            cram_ub_n <= 1'b0; cram_lb_n <= 1'b0;
        end
    end
    else begin                                     // idle, no start: every strobe idle (no-op from reachable states)
        cram_ce0_n <= 1'b1; cram_adv_n <= 1'b1; cram_oe_n <= 1'b1; cram_we_n <= 1'b1;
        cram_ub_n <= 1'b1; cram_lb_n <= 1'b1; dq_oe <= 1'b0;
    end

    if (rst || !tpu_ok) begin                      // restart the clear (the access in flight completes, unacked);
                                                   // also held during tPU (a no-op from the intended power-up state)
        clear_done <= 1'b0;
        clr_idx <= 16'd0;
        if (busy) acc_kill <= 1'b1;
        wb_valid <= 1'b0;                          // drop a posted write not yet started (see the header)
    end
end

// ---- watchdogs (sticky until rst) ----------------------------------------------------------------------------
reg [10:0] wd_rd_cnt = 11'd0, wd_wr_cnt = 11'd0;    // consecutive clocks with req high, saturating at WD_LIMIT + 1
localparam [10:0] WD_SAT = 11'(WD_LIMIT + 1);
always @(posedge clk) begin
    wd_rd_cnt <= !sdr_p4_req ? 11'd0 : (wd_rd_cnt == WD_SAT) ? WD_SAT : wd_rd_cnt + 11'd1;
    wd_wr_cnt <= !sdr_wave_wr_req ? 11'd0 : (wd_wr_cnt == WD_SAT) ? WD_SAT : wd_wr_cnt + 11'd1;
    if (rst) begin wd_rd <= 1'b0; wd_wr <= 1'b0; end
    else begin
        if (wd_rd_cnt == WD_SAT) wd_rd <= 1'b1;     // req high for more than WD_LIMIT clocks
        if (wd_wr_cnt == WD_SAT) wd_wr <= 1'b1;
    end
end

assign dbg_tpu_ok  = tpu_ok;
assign dbg_clr_idx = clr_idx[15:11];
assign dbg_busy    = busy;

endmodule
