// SPDX-License-Identifier: GPL-3.0-or-later
//
// d2_rom_psram: the Z80 sound ROM (slot 4 sound.bin, 4 MiB = 2 M words) in Pocket PSRAM chip 1 (cram1, die 0), as one clk_sys
// client. GA2Audio3 = "option B" (experiments/AUDIO/B_PSRAM_ROM_NOTE.md, owner approval 2026-11-15). A sibling of d2_wave_psram
// (same pad conventions, same 5 + 1 clk_sys asynchronous access, same tPU wait); d2_wave_psram on cram0 is not touched.
//
// Three users, one sequencer, priority in this order:
//   1. LOAD: the slot-4 data_loader's 16-bit word stream (ld_en pulses, byte offset ld_addr, data {byte 2k+1, byte 2k}) goes
//      through a WQ_D-entry write queue; each queued word is one PSRAM write at word address ld_addr[21:1] (die 0, 0x000000 -
//      0x1FFFFF; bit 21 of the PSRAM word address is always 0, so the top address 0x3FFFFF is never reached).
//      The data_loader cannot be back-pressured (no ready input; its 4-entry dcfifo has overflow checking off), so the queue
//      is sized by a bound, not by flow control (GA2Audio3 RESULT, "loader"): one 32-bit bridge write = two words = 12 clk_sys
//      (248.4 ns) here, while the template's io_bridge_peripheral needs >= 8 SPI bytes per bridge write. A loader word that
//      finds the queue full (or arrives during the tPU wait) is DROPPED and sets the sticky flag ld_ovf (band cell 31), so a
//      loss can never be silent. ld_ovf is cleared by rst only (rst = !PLL lock, high from power-up: lock_sys initialises to 0),
//      so a word dropped during tPU stays visible; it is the one register not also repaired by the tPU wait.
//   2. Z80 ROM reads: s32_core's sdr_p3_req / sdr_p3_addr[24:1] (the s32_soundsys word cache's misses). The word address is
//      sdr_p3_addr - ROM_BASE[24:1] (21 bits). 4-phase as upstream's p3 contract: ack rises with the data, stays high until req
//      falls, then falls (s32_soundsys.sv: the cache consumes one ack per request and arms the next miss only after ack is low).
//   3. Read-back verification: once vf_start is high (the loaders have been quiet; the same load_done that starts the SDRAM
//      read-back of slots 1-3) and the write queue is empty, every word of the slot is read back and folded into the same two
//      checksums as d2_slot_verify (sum = sum of w[i] mod 2^32, fl = sum of the prefix sums mod 2^32), over the same word count:
//      4 * max(1, ceil(bytes / 8)) words (the size clamped to 4 MiB, so the word address stays below 0x200000). vf_done rises
//      with vf_sum / vf_fl final; the core holds the CPUs in reset until then (band cell 16 = these sums against EXP_SUM3 /
//      EXP_FL3, blue until vf_done).
//
// PSRAM protocol: asynchronous only, exactly d2_wave_psram's schedule (the PSRAM-probe conventions, HW-002: async read observed
// at 8 x 9.402 ns = 75.2 ns). No configuration access (cre = 0 constant), die 1 never selected (ce1_n = 1 constant), every strobe
// idle from power-up and while the tPU wait (TPU_CYCLES = 200 us) runs. One access = 5 clk_sys + 1 clk_sys with ce0_n high:
//   E0: ce0_n, adv_n low; address on {a, dq} (driven); ub_n / lb_n low
//   E1: adv_n high;                                         write: we_n low
//   E2: read: release dq;                                   write: data on dq
//   E3: read: oe_n low
//   E4:                                                     write: we_n high (tWP 3 clk, tAW / tCW / tBW 4 clk, tDW 2 clk)
//   E5: read: dq sampled into sdr_p3_dout (5 clk = 103.5 ns after adv_n fell); ce0_n, oe_n, ub_n, lb_n high, dq released
//   E6: earliest next E0 (ce0_n was high for one clock; the start condition is !busy, it does not read a pad register).
//
// Power-up rules (GA2Audio1 lessons, INTEGRATION_RESULT section 12):
//   * no set-only or never-assigned initialised register: every flag is cleared by rst (wd_p3 also during tPU) and set by its event;
//     ce1_n / cre / clk are continuous assignments, not initialised regs;
//   * while not busy and not starting (and throughout tPU) every strobe is driven idle (a no-op from every reachable state);
//   * nothing depends on an output register's power-up value: the start condition is busy / tpu_ok / rst / the request
//     sources only; the queue pointers, the verify state and the flags are held at their rst values during tPU, so an
//     arbitrary power-up is repaired even if rst never pulses; while tPU runs the sequencer is forced idle (no access, not even
//     a power-up "busy" one, reaches the pins before tPU has elapsed).
//   * An access in flight completes even if rst rises (no cut write pulse); it is then not acknowledged.
// Observation outputs (dbg_*) feed only the band and the bench.
module d2_rom_psram #(
    parameter [24:0]  ROM_BASE   = 25'h020_0000,   // SDR_SOUNDCPU_BASE (byte address; s32_pkg.sv:72): s32_core adds it to p3
    parameter integer TPU_CYCLES = 9664,           // 200 us at 48.317 MHz: pins idle after configuration
    parameter integer WD_LIMIT   = 1024
) (
    input             clk,                         // clk_sys
    input             rst,                         // !PLL lock: queue / verify / flags to their reset values
    // slot-4 data_loader (clk_sys write strobes)
    input             ld_en,
    input      [21:0] ld_addr,                     // byte offset in the slot (data_loader ADDRESS_SIZE 22)
    input      [15:0] ld_data,
    // read-back verification
    input             vf_start,                    // level: the loaders are idle (clk_ram load_done, synchronised)
    input      [24:0] vf_bytes,                    // the slot-4 size (dataslot_requestwrite_size; quasi-static)
    output reg        vf_done = 1'b0,
    output reg [31:0] vf_sum = 32'd0,
    output reg [31:0] vf_fl = 32'd0,
    // Z80 sound ROM (s32_core sdr_p3_*)
    input             sdr_p3_req,
    input      [24:1] sdr_p3_addr,
    output reg [15:0] sdr_p3_dout = 16'h0000,
    output reg        sdr_p3_ack = 1'b0,
    // status / diagnostics
    output reg        wd_p3 = 1'b0,                // sticky: sdr_p3_req high for more than WD_LIMIT clk_sys
    output reg        ld_ovf = 1'b0,               // sticky: a loader word was dropped (queue full, or during tPU)
    output            wq_empty,
    // PSRAM chip pins (cram1)
    output reg [21:16] cram_a = 6'd0,
    inout      [15:0] cram_dq,
    input             cram_wait,
    output            cram_clk,
    output reg        cram_adv_n = 1'b1,
    output            cram_cre,
    output reg        cram_ce0_n = 1'b1,
    output            cram_ce1_n,
    output reg        cram_oe_n = 1'b1,
    output reg        cram_we_n = 1'b1,
    output reg        cram_ub_n = 1'b1,
    output reg        cram_lb_n = 1'b1,
    // observation only
    output            dbg_tpu_ok,
    output reg        dbg_acc_tgl = 1'b0,          // toggles at the end of every PSRAM access
    output            dbg_busy
);

assign cram_clk   = 1'b0;                          // asynchronous mode: no clock
assign cram_ce1_n = 1'b1;                          // die 1 never selected
assign cram_cre   = 1'b0;                          // no configuration-register access, ever
wire unused_wait = cram_wait;

reg  [15:0] dq_o = 16'h0000;
reg         dq_oe = 1'b0;
assign cram_dq = dq_oe ? dq_o : 16'hZZZZ;

// ---- power-up wait (tPU) ------------------------------------------------------------------------------------
reg  [13:0] tpu_cnt = 14'(TPU_CYCLES);
wire        tpu_ok  = (tpu_cnt == 14'd0);
always @(posedge clk) if (!tpu_ok) tpu_cnt <= tpu_cnt - 14'd1;
wire        hold = rst || !tpu_ok;                 // queue, verify state and flags held at their reset values

// ---- loader write queue ---------------------------------------------------------------------------------------
localparam integer WQ_LOG2 = 2;                    // loader write queue: 4 words
localparam integer WQ_D = 1 << WQ_LOG2;
reg  [36:0] wq_mem [0:WQ_D-1];                     // {word address [20:0], data [15:0]}
reg   [2:0] wq_wp = 3'd0, wq_rp = 3'd0;
wire  [2:0] wq_fill = wq_wp - wq_rp;
assign wq_empty = (wq_wp == wq_rp);
wire        wq_full = (wq_fill >= 3'(WQ_D));
wire [36:0] wq_head = wq_mem[wq_rp[WQ_LOG2-1:0]];
reg         ld_d = 1'b0;
wire        ld_rise = ld_en && !ld_d;
wire        ld_push = ld_rise && !hold && !wq_full;
wire        ld_drop = ld_rise && !rst && (!tpu_ok || wq_full);

// ---- verification bookkeeping ---------------------------------------------------------------------------------
reg         vf_run = 1'b0;
reg  [21:0] vf_n = 22'd0, vf_iss = 22'd0, vf_fld = 22'd0;   // words to read / issued / folded
reg  [31:0] acc_s = 32'd0, acc_f = 32'd0;
reg         fold = 1'b0;                           // the verify word read at the last E5 is in sdr_p3_dout
wire [31:0] s_next = acc_s + 32'(sdr_p3_dout);
wire [31:0] f_next = acc_f + acc_s + 32'(sdr_p3_dout);
wire [24:0] vb_c   = (vf_bytes > 25'h040_0000) ? 25'h040_0000 : vf_bytes;   // clamp to the 4 MiB region
wire [21:0] vb_q   = 22'((vb_c + 25'd7) >> 3);                               // 8-byte bursts, as d2_slot_verify
wire [21:0] vf_n_w = ((vb_q == 22'd0) ? 22'd1 : vb_q) << 2;                  // <= 0x200000 words

// ---- access sequencer -----------------------------------------------------------------------------------------
reg         busy = 1'b0;
reg         acc_we = 1'b0;
reg   [1:0] acc_who = 2'd0;                        // 0 loader write, 1 Z80 read, 2 verify read
reg  [15:0] acc_wdata = 16'h0000;
reg   [2:0] t = 3'd0;
reg         acc_kill = 1'b0;                       // rst seen during this access: complete it, but no ack / fold

/* verilator lint_off UNUSEDSIGNAL */
wire [24:1] p3_off = sdr_p3_addr - ROM_BASE[24:1];  // only [21:1] is used: the 4 MiB region
/* verilator lint_on UNUSEDSIGNAL */
wire start_wr = !wq_empty;
wire start_rd = sdr_p3_req && !sdr_p3_ack;
wire start_vf = vf_run && (vf_iss != vf_n);

always @(posedge clk) begin
    ld_d <= ld_en;
    fold <= 1'b0;
    // 4-phase: ack falls once req has fallen
    if (sdr_p3_ack && !sdr_p3_req) sdr_p3_ack <= 1'b0;
    if (ld_push) begin
        wq_mem[wq_wp[WQ_LOG2-1:0]] <= {ld_addr[21:1], ld_data};
        wq_wp <= wq_wp + 3'd1;
    end

    if (!tpu_ok) begin                             // tPU: forced idle, every strobe inactive (no-op from the power-up state)
        busy <= 1'b0;
        cram_ce0_n <= 1'b1; cram_adv_n <= 1'b1; cram_oe_n <= 1'b1; cram_we_n <= 1'b1;
        cram_ub_n <= 1'b1; cram_lb_n <= 1'b1; dq_oe <= 1'b0;
    end
    else if (busy) begin
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
            if (!acc_we) sdr_p3_dout <= cram_dq;
            if (acc_who == 2'd1 && !acc_kill && !rst) sdr_p3_ack <= 1'b1;
            if (acc_who == 2'd2 && !acc_kill && !rst) fold <= 1'b1;
            dbg_acc_tgl <= !dbg_acc_tgl;
        end
        endcase
    end
    else if (!rst && (start_wr || start_rd || start_vf)) begin
        // E0 (busy was low for at least this clock, so ce0_n has been high since the previous E5)
        busy <= 1'b1; t <= 3'd0; acc_kill <= 1'b0;
        cram_ce0_n <= 1'b0; cram_adv_n <= 1'b0; dq_oe <= 1'b1;
        cram_ub_n <= 1'b0; cram_lb_n <= 1'b0;
        if (start_wr) begin                        // loader write first (the queue must never back up)
            acc_we <= 1'b1; acc_who <= 2'd0; acc_wdata <= wq_head[15:0];
            {cram_a, dq_o} <= {1'b0, wq_head[36:16]};
            wq_rp <= wq_rp + 3'd1;
        end
        else if (start_rd) begin
            acc_we <= 1'b0; acc_who <= 2'd1;
            {cram_a, dq_o} <= {1'b0, p3_off[21:1]};
        end
        else begin
            acc_we <= 1'b0; acc_who <= 2'd2;
            {cram_a, dq_o} <= {1'b0, vf_iss[20:0]};
            vf_iss <= vf_iss + 22'd1;
        end
    end
    else begin                                     // idle, no start: every strobe idle (no-op from reachable states)
        cram_ce0_n <= 1'b1; cram_adv_n <= 1'b1; cram_oe_n <= 1'b1; cram_we_n <= 1'b1;
        cram_ub_n <= 1'b1; cram_lb_n <= 1'b1; dq_oe <= 1'b0;
    end

    // verification: start once the loaders are quiet and the queue has drained; fold one word per verify access
    if (vf_start && !vf_run && !vf_done && wq_empty && !busy) begin
        vf_run <= 1'b1; vf_n <= vf_n_w; vf_iss <= 22'd0; vf_fld <= 22'd0; acc_s <= 32'd0; acc_f <= 32'd0;
    end
    if (fold) begin
        acc_s <= s_next; acc_f <= f_next; vf_fld <= vf_fld + 22'd1;
        if (vf_fld + 22'd1 == vf_n) begin vf_done <= 1'b1; vf_run <= 1'b0; vf_sum <= s_next; vf_fl <= f_next; end
    end

    if (hold) begin                                // rst, and the tPU wait (a no-op from the intended power-up state)
        wq_wp <= 3'd0; wq_rp <= 3'd0;
        vf_run <= 1'b0; vf_done <= 1'b0; vf_n <= 22'd0; vf_iss <= 22'd0; vf_fld <= 22'd0;
        acc_s <= 32'd0; acc_f <= 32'd0; vf_sum <= 32'd0; vf_fl <= 32'd0;
        if (busy) acc_kill <= 1'b1;
    end
    // dropped loader word: sticky until rst (not cleared by the tPU wait, so a word dropped during tPU stays visible)
    if (rst) ld_ovf <= 1'b0;
    else if (ld_drop) ld_ovf <= 1'b1;
end

// ---- p3 watchdog (sticky until rst; cleared during tPU) ----------------------------------------------------------
reg [10:0] wd_cnt = 11'd0;                         // consecutive clocks with req high, saturating at WD_LIMIT + 1
localparam [10:0] WD_SAT = 11'(WD_LIMIT + 1);
always @(posedge clk) begin
    wd_cnt <= !sdr_p3_req ? 11'd0 : (wd_cnt == WD_SAT) ? WD_SAT : wd_cnt + 11'd1;
    if (rst) wd_p3 <= 1'b0;
    else if (wd_cnt == WD_SAT) wd_p3 <= 1'b1;
    else if (!tpu_ok) wd_p3 <= 1'b0;
end

assign dbg_tpu_ok = tpu_ok;
assign dbg_busy   = busy;

endmodule
