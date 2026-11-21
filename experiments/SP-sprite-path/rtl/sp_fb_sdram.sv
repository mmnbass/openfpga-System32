// SPDX-License-Identifier: GPL-3.0-or-later
//============================================================================
//  sp_fb_sdram: System 32 sprite framebuffer in SDRAM (exp/sprite-path).
//
//  Drop-in for upstream s32_fb_if's renderer-facing ports (wr_*, er_*, rd_*,
//  same handshakes and the same 1-cycle rd_x -> rd_pix latency); the DDR3
//  port is replaced by the fw/fr row-burst clients of sp_sdram.
//
//  Layout: slot b, line y = one 1 KB SDRAM page (one row in upstream's
//  mapping) at FB_BASE + b*256 KB + y*1 KB, 512 pixels x 16 bit, as upstream.
//
//  Write path (sprite runs): pixels land in a 512 x 16 run RAM at their own x
//  (any order, gaps allowed) with a 512-bit written mask, exactly as upstream
//  captures runs. The flush walks the mask in ascending x and streams only the
//  written pixels to fw (one WRITE each, own column, DQM 00): no read-modify-
//  write, no masked-out cycles. wr_busy holds the renderer until the run is in
//  SDRAM (upstream contract).
//
//  Erase (lazy span): a 1024 x 10 span table (one M10K) keeps, per slot and
//  line, the 16-pixel units written since that line was last erased. An erase
//  request fills only that span with FFFF, or acknowledges at once if the line
//  is clean. At reset the table is swept to "whole line dirty", so the first
//  erase of every line clears the full 512 pixels (SDRAM power-up content).
//  Invariant: outside its span every pixel of a line is FFFF.
//
//  Read path (scanout): the requested line (scan_words = 320 or 416 pixels)
//  is fetched into one half of a 2 x 512 x 16 line RAM while the other half
//  is displayed; publication at rd_x == 0 exactly as upstream. A fetch still
//  pending URG_T cycles after its request becomes urgent (sp_sdram serves it
//  ahead of the background rotation in <= FB_URG_CAP-word chunks).
//
//  Not implemented: shadow runs (dest &= 0x7FFF read-modify-write). GA2 issued
//  none in 2,236 measured frames (FB-traffic RESULTS.md); a shadow run is
//  dropped and counted in n_shadow_drop so a game that uses them is visible.
//============================================================================

module sp_fb_sdram #(
    parameter [24:0] FB_BASE = 25'h0C0_0000,   // 1 MB: 4 slots x 256 KB
    parameter integer URG_T  = 2048
)(
    input             clk,      // clk_ram
    input             rst,
    input             scan_wide,              // 1: fetch 416 pixels, else 320

    // renderer write port (as s32_fb_if)
    input             wr_start,
    input       [1:0] wr_buf,
    input       [8:0] wr_x,
    input       [7:0] wr_y,
    input             wr_valid,
    input      [15:0] wr_pix,
    input             wr_end,
    input             wr_shadow,
    output            wr_busy,

    // erase port (as s32_fb_if)
    input             er_req,
    input       [1:0] er_buf,
    input       [7:0] er_y,
    output reg        er_ack,

    // line read port (as s32_fb_if)
    input             rd_req,
    input       [1:0] rd_buf,
    input       [7:0] rd_y,
    output reg        rd_ack,
    input       [8:0] rd_x,
    output     [15:0] rd_pix,

    // sp_sdram fw / fr clients
    output reg        fw_req,
    output reg [24:10] fw_page,
    output reg        fw_fill,
    output reg  [8:0] fw_c0,
    output reg  [8:0] fw_c1,
    output            fw_valid,
    output      [8:0] fw_col,
    output     [15:0] fw_data,
    output      [1:0] fw_be,
    output            fw_last,
    input             fw_pop,
    input             fw_done,

    output reg        fr_req,
    output reg [24:10] fr_page,
    output      [8:0] fr_c0,
    output      [9:0] fr_n,
    output            fr_urgent,
    input             fr_dv,
    input       [8:0] fr_dcol,
    input      [15:0] fr_dout,
    input             fr_done,

    // diagnostics (saturating)
    output reg [15:0] n_shadow_drop,
    output reg [15:0] n_erase_skip,
    output reg [15:0] n_erase_fill,
    output reg [15:0] n_late_publish,
    output            init_busy
);

function automatic [24:10] page_of(input [1:0] b, input [7:0] y);
    page_of = {FB_BASE[24:20], b, y};   // slot = 256 KB (bits 19:18), line = 1 KB (17:10)
endfunction

// ------------------------------------------------------------------ run capture
// Two run slots (ping-pong): the renderer captures run k+1 while run k is
// flushed, so the flush is hidden behind production. Runs are flushed strictly
// in capture order (a later run may overwrite an earlier one's pixels).
reg [511:0] msk0, msk1;
reg  [8:0]  x0 [0:1];
reg  [8:0]  xe [0:1];
reg  [7:0]  ry [0:1];
reg  [1:0]  rb [0:1];
reg  [1:0]  rsh, rany, full;
reg         cs;                        // capture slot
reg         fs;                        // flush slot (oldest full)
reg         cap_active;
reg         span_upd_req;              // flush -> span engine: widen the dirty span
reg         fw_done_seen;              // the flush job's done was seen

wire [9:0]  rr_addr;
wire [15:0] rr_q;
sp_ram_sdp #(.AW(10), .DW(16)) run_ram (
    .clk(clk), .we(wr_valid && cap_active), .waddr({cs, wr_x}), .wdata(wr_pix),
    .raddr(rr_addr), .q(rr_q));

// flush producer. Timing: the 512-bit mask is read through ONE registered
// 4-bit word mask (wm, loaded from the NEXT word's register fw_word_n), so the
// 128:1 mask mux ends at a flop; the lane pick and the RAM address use only wm.
typedef enum logic [1:0] { F_IDLE, F_SCAN, F_WAIT } fstate_t;
fstate_t fst;
reg  [6:0] fw_word, fw_word_n;          // current / next 64-bit word (4 pixels)
reg  [3:0] wm;                          // lanes of fw_word still to send
reg        wfirst;                      // no word loaded yet in this flush
reg        at_last;                     // fw_word is the run's last word (registered compare)
reg  [8:0] xe_r;                        // last written x of the run being flushed
reg        rd_inflight;
reg  [8:0] inflight_col;
reg        inflight_last;
// 4-entry show-ahead FIFO {last, col, data} as a shift register: the head
// (entry 0) is a flop, so fw_col/fw_data/fw_last leave this module registered.
reg [25:0] fe [0:3];
reg  [2:0] f_cnt;
wire       f_push = rd_inflight;
wire       f_pop  = fw_pop;
assign fw_valid = (f_cnt != 3'd0) && !fw_fill;
assign {fw_last, fw_col, fw_data} = fe[0];
assign fw_be = 2'b11;

wire [511:0] fmsk    = fs ? msk1 : msk0;
// two-stage mask read: a registered 32-pixel group (8 words) tagged with its
// group index, then an 8:1 word select from that register into wm
reg  [31:0] grp_r;
reg  [3:0]  grp_tag;
reg         grp_ok;
always @(posedge clk) begin
    grp_r   <= fmsk[{fw_word_n[6:3], 5'b00000} +: 32];
    grp_tag <= fw_word_n[6:3];
    grp_ok  <= (fst == F_SCAN);       // the group register follows fw_word_n only while scanning
end
wire        grp_hit  = grp_ok && (grp_tag == fw_word_n[6:3]);
wire [1:0] cur_lane  = wm[0] ? 2'd0 : wm[1] ? 2'd1 : wm[2] ? 2'd2 : 2'd3;
wire       room      = (f_cnt + {2'b00, rd_inflight}) < 3'd3;
wire       issue_rd  = (fst == F_SCAN) && (wm != 4'b0000) && room;
assign rr_addr = {fs, fw_word, cur_lane};

// erase / span engine state (declared here for the flush interlock)
typedef enum logic [2:0] { S_INIT, S_IDLE, S_UPD_RD, S_UPD_WR, S_ER_RD, S_ER_DEC, S_ER_FILL, S_ER_CLR } sstate_t;
sstate_t sst;
wire       erase_active = (sst == S_ER_RD) || (sst == S_ER_DEC) || (sst == S_ER_FILL) || (sst == S_ER_CLR);
wire       flush_start = (fst == F_IDLE) && full[fs] && !erase_active && (sst == S_IDLE) && !span_upd_req;
wire       flush_end   = (fst == F_WAIT) && (fw_done || fw_done_seen || !rany[fs] || rsh[fs]);

always @(posedge clk) begin
    if (wr_start) begin
        x0[cs]  <= 9'd511;
        xe[cs]  <= 9'd0;
        ry[cs]  <= wr_y;
        rb[cs]  <= wr_buf;
        rsh[cs] <= wr_shadow;
        rany[cs] <= 1'b0;
        if (cs) msk1 <= 512'b0; else msk0 <= 512'b0;
        cap_active <= 1'b1;
    end
    if (wr_valid && cap_active) begin
        if (cs) msk1[wr_x] <= 1'b1; else msk0[wr_x] <= 1'b1;
        rany[cs] <= 1'b1;
        if (wr_x < x0[cs]) x0[cs] <= wr_x;
        if (wr_x > xe[cs]) xe[cs] <= wr_x;
    end
    if (wr_end && cap_active) begin
        full[cs]   <= 1'b1;
        cs         <= ~cs;
        cap_active <= 1'b0;
    end
    if (flush_end) begin
        full[fs] <= 1'b0;
        fs <= ~fs;
    end
    if (rst) begin cap_active <= 1'b0; full <= 2'b00; cs <= 1'b0; fs <= 1'b0; end
end

always @(posedge clk) begin
    if (rst) begin
        fst <= F_IDLE; rd_inflight <= 1'b0; f_cnt <= 3'd0;
        span_upd_req <= 1'b0;
        n_shadow_drop <= 16'd0;
    end
    else begin
        // FIFO (shift register; push lands at the first free entry after the pop)
        for (int i = 0; i < 4; i++) begin
            if (f_push && 3'(i) == f_cnt - {2'b00, f_pop}) fe[i] <= {inflight_last, inflight_col, rr_q};
            else if (f_pop && i < 3) fe[i] <= fe[i + 1];
        end
        f_cnt <= f_cnt + {2'b00, f_push} - {2'b00, f_pop};
        rd_inflight <= issue_rd;
        if (issue_rd) begin
            inflight_col  <= {fw_word, cur_lane};
            inflight_last <= ({fw_word, cur_lane} == xe_r);
            wm            <= wm & ~(4'b0001 << cur_lane);
        end
        case (fst)
        F_IDLE: if (flush_start) begin
            if (rsh[fs] && rany[fs] && n_shadow_drop != 16'hFFFF) n_shadow_drop <= n_shadow_drop + 1'd1;
            fw_word_n <= x0[fs][8:2];
            xe_r      <= xe[fs];
            wm        <= 4'b0000;
            wfirst    <= 1'b1;
            if (rany[fs] && !rsh[fs]) span_upd_req <= 1'b1;
            fst <= (rany[fs] && !rsh[fs]) ? F_SCAN : F_WAIT;
        end
        F_SCAN: if (wm == 4'b0000) begin
            if (!wfirst && at_last) fst <= F_WAIT;
            else if (grp_hit) begin            // else wait one cycle for the group register
                wm        <= grp_r[{fw_word_n[2:0], 2'b00} +: 4];
                fw_word   <= fw_word_n;
                at_last   <= (fw_word_n == xe_r[8:2]);
                fw_word_n <= fw_word_n + 1'd1;
                wfirst    <= 1'b0;
            end
        end
        F_WAIT: if (flush_end) fst <= F_IDLE;
        default: fst <= F_IDLE;
        endcase
        if (sst == S_UPD_WR) span_upd_req <= 1'b0;
    end
end

// busy when the slot the next run would use is still waiting for its flush
assign wr_busy = wr_end ? full[~cs] : full[cs];
wire   flush_req = full[0] | full[1];

// ------------------------------------------------------------------ span table
// entry = {lo[4:0], hi[4:0]} in 16-pixel units; clean when lo > hi.
reg  [9:0] sp_addr;
reg  [9:0] sp_wdata;
reg        sp_we;
wire [9:0] sp_q;
sp_ram_sdp #(.AW(10), .DW(10)) span_ram (
    .clk(clk), .we(sp_we), .waddr(sp_addr), .wdata(sp_wdata), .raddr(sp_addr), .q(sp_q));

wire [4:0] q_lo = sp_q[9:5], q_hi = sp_q[4:0];
wire       q_clean = q_lo > q_hi;
wire [4:0] r_lo = x0[fs][8:4], r_hi = xe[fs][8:4];

always @(posedge clk) begin
    sp_we <= 1'b0;
    if (rst) begin
        sst <= S_INIT; sp_addr <= 10'd0; er_ack <= 1'b0;
        n_erase_skip <= 16'd0; n_erase_fill <= 16'd0;
    end
    else begin
        case (sst)
        S_INIT: begin                              // whole line dirty
            sp_we <= 1'b1; sp_wdata <= {5'd0, 5'd31};
            if (sp_we && sp_addr == 10'h3FF) sst <= S_IDLE;
            else if (sp_we) sp_addr <= sp_addr + 1'd1;
        end
        S_IDLE: begin
            if (span_upd_req) begin
                sp_addr <= {rb[fs], ry[fs]}; sst <= S_UPD_RD;
            end
            else if (er_req && !er_ack && fst == F_IDLE && !flush_req && !cap_active) begin
                sp_addr <= {er_buf, er_y}; sst <= S_ER_RD;
            end
        end
        S_UPD_RD: sst <= S_UPD_WR;                 // q valid next cycle
        S_UPD_WR: begin
            sp_we <= 1'b1;
            sp_wdata <= q_clean ? {r_lo, r_hi}
                                : {(r_lo < q_lo ? r_lo : q_lo), (r_hi > q_hi ? r_hi : q_hi)};
            sst <= S_IDLE;
        end
        S_ER_RD: sst <= S_ER_DEC;
        S_ER_DEC: begin
            if (q_clean) begin
                er_ack <= 1'b1;
                if (n_erase_skip != 16'hFFFF) n_erase_skip <= n_erase_skip + 1'd1;
                sst <= S_IDLE;
            end
            else begin
                fw_c0 <= {q_lo, 4'h0};
                fw_c1 <= {q_hi, 4'hF};
                if (n_erase_fill != 16'hFFFF) n_erase_fill <= n_erase_fill + 1'd1;
                sst <= S_ER_FILL;
            end
        end
        S_ER_FILL: if (fw_done) sst <= S_ER_CLR;
        S_ER_CLR: begin
            sp_we <= 1'b1; sp_wdata <= {5'd31, 5'd0};
            er_ack <= 1'b1;
            sst <= S_IDLE;
        end
        default: sst <= S_IDLE;
        endcase
        if (!er_req) er_ack <= 1'b0;   // four-phase, as s32_fb_if
    end
end
assign init_busy = (sst == S_INIT);

// fw job: one at a time (flush stream or erase fill)
always @(posedge clk) begin
    if (rst) begin fw_req <= 1'b0; fw_fill <= 1'b0; end
    else begin
        if (fw_done) fw_req <= 1'b0;
        else if (!fw_req) begin
            if (sst == S_ER_FILL) begin
                fw_req <= 1'b1; fw_fill <= 1'b1; fw_page <= page_of(er_buf, er_y);
            end
            else if (fst == F_SCAN || (fst == F_WAIT && rany[fs] && !rsh[fs] && !fw_done_seen)) begin
                fw_req <= 1'b1; fw_fill <= 1'b0; fw_page <= page_of(rb[fs], ry[fs]);
            end
        end
    end
end
// a flush job is requested once; fw_done_seen guards the cycle after its done
always @(posedge clk) begin
    if (rst || fst == F_IDLE) fw_done_seen <= 1'b0;
    else if (fw_done) fw_done_seen <= 1'b1;
end

// ------------------------------------------------------------------ scanout
reg        display_bank, fill_bank, line_ready;
reg [15:0] fr_age;
wire       rd_line_publish = (rd_x == 9'd0) && line_ready;
wire       scan_bank = rd_line_publish ? fill_bank : display_bank;
wire       read_pending = rd_req && !rd_ack && !line_ready;
assign fr_c0 = 9'd0;
assign fr_n  = scan_wide ? 10'd416 : 10'd320;
reg fr_urg_r;
assign fr_urgent = fr_urg_r;            // registered (feeds sp_sdram's arbitration)

sp_ram_sdp #(.AW(10), .DW(16)) line_ram (
    .clk(clk), .we(fr_dv), .waddr({fill_bank, fr_dcol}), .wdata(fr_dout),
    .raddr({scan_bank, rd_x}), .q(rd_pix));

always @(posedge clk) begin
    if (rst) begin
        display_bank <= 1'b0; fill_bank <= 1'b1; line_ready <= 1'b0;
        fr_req <= 1'b0; rd_ack <= 1'b0; fr_age <= 16'd0; n_late_publish <= 16'd0; fr_urg_r <= 1'b0;
    end
    else begin
        if (rd_line_publish) begin
            display_bank <= fill_bank;
            line_ready <= 1'b0;
        end
        else if (rd_x == 9'd0 && fr_req && n_late_publish != 16'hFFFF)
            n_late_publish <= n_late_publish + 1'd1;  // x=0 reached, fetch not done
        fr_urg_r <= fr_req && !fr_done && (fr_age >= 16'(URG_T - 1));
        if (fr_req) begin
            if (fr_age != 16'hFFFF) fr_age <= fr_age + 1'd1;
            if (fr_done) begin
                fr_req <= 1'b0; line_ready <= 1'b1; rd_ack <= 1'b1;
            end
        end
        else if (read_pending) begin
            fr_req <= 1'b1; fr_age <= 16'd0;
            fr_page <= page_of(rd_buf, rd_y);
            fill_bank <= ~display_bank;
        end
        if (!rd_req) rd_ack <= 1'b0;
    end
end

endmodule

// ---------------------------------------------------------------------------
// Simple dual-port RAM, registered read (one M10K-class block per instance in
// Quartus inference; Yosys maps it with memory_bram / synth_intel_alm).
// ---------------------------------------------------------------------------
module sp_ram_sdp #(parameter integer AW = 9, parameter integer DW = 16) (
    input               clk,
    input               we,
    input      [AW-1:0] waddr,
    input      [DW-1:0] wdata,
    input      [AW-1:0] raddr,
    output reg [DW-1:0] q
);
reg [DW-1:0] mem [0:(1<<AW)-1];
always @(posedge clk) begin
    if (we) mem[waddr] <= wdata;
    q <= mem[raddr];
end
endmodule
