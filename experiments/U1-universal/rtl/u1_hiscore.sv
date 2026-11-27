// SPDX-License-Identifier: GPL-3.0-or-later
//
// u1_hiscore (HS): persistence of one work-RAM block (a game's score table) in the APF Save slot, after the 93C46 image.
// System 32 boards have no battery RAM: the score table lives in work RAM and is rebuilt from defaults at every boot. This
// engine does what MAME's hiscore plugin does, in hardware, for the block the game's configuration file names.
//
// Configuration (static, from u1_board_cfg; en = 0 makes the whole module inert): base (work-RAM word address), lenm1
// (words - 1, up to 512 words), tag (save-format identifier, non-zero), and two marker bytes: m0 = the value the game's own
// initialisation writes to the block's first byte, m1 = the value it writes to byte m1_off of the block.
//
// Save file words after the EEPROM's 64 (word index idx = file word - 64):
//   idx 0 magic 0x5348   idx 1 tag   idx 2 base   idx 3 words   idx 4.. the block, in address order (little-endian words)
//   idx 516 end mark 0x4548 (after the largest block; the file is 1,164 bytes for every configuration)
// The end mark is what makes a truncated file detectable: the bridge delivers whole 32-bit words, so a file cut inside the
// block's last bridge word would otherwise still deliver the block's last word index (review HS finding 1). A file without
// the end mark at its place is not a complete save, whatever its header says.
// A file without exactly this header for THIS configuration (an EEPROM-only 128-byte save, another game or region, another
// format) is ignored: nothing is restored and the EEPROM part is unaffected.
// The Pocket loads the Save slot BEFORE the Configuration slot (data.json order), so while a save arrives the configuration
// inputs are still those of "no configuration" (en 0, lenm1 0, ...). Loading therefore depends on nothing configured: every
// file word is stored at its plain offset and the header is kept; ld_ok compares the header with the configuration only once
// it has arrived (sn5 hardware regression 2026-11-25, which stored only the words at offsets <= the lenm1 of that moment).
//
// Sequence after the CPU reset is released:
//   WAIT     the engine watches the SRAM port's writes. It never touches the block until the GAME has written m0 and m1 to
//            their bytes since this reset (stale RAM contents cannot trigger it) and then left the block alone for QUIET_VBL
//            frames. That the game's default initialisation is then over, and never resumes, is a PROPERTY OF THE GAME that
//            the configuration asserts (markers = bytes the initialisation writes last, shown per game in simulation); the
//            RTL only establishes "both markers written, then 32 quiet frames". A game that wrote the marker values as
//            ordinary data and paused before its real initialisation would defeat it.
//   RESTORE  with a valid saved block: the buffer is written to the work RAM, word by word, through the work RAM's second
//            port (u1_wram_pr; the CPU is never blocked for more than one access).
//   CAPTURE  without one: the work RAM block is read into the buffer the same way.
//   LIVE     the buffer holds the COMMITTED copy of the block. A write to the block marks it dirty; once the block has been
//            quiet for COMMIT_VBL frames the engine re-captures it (CAPTURE, through the second port) into the buffer's
//            other half and, when that capture completes, makes it the committed copy. So the committed copy is the latest
//            table, at most COMMIT_VBL frames behind it, and it is never a table the game is in the middle of rewriting.
//            A capture during which the block was written is not committed (it could hold a later write but miss an earlier
//            one, a table that never existed; Codex sn5 review finding 5): the committed copy stays, and the block, still
//            dirty, is captured again after the next COMMIT_VBL quiet frames.
//            A core reset (the Pocket's quit sends Reset Enter before it reads the Save slot) abandons an uncommitted change:
//            the save holds the most recent COMPLETED capture, i.e. the table as it was once the block had last been quiet for
//            COMMIT_VBL frames and a clean scan had finished. The engine keeps running while the Pocket menu is open (the pause
//            gates only the CPU and sound), so with the game paused a pending commit completes within COMMIT_VBL frames plus
//            one scan (shown in simulation, run F_menu_quit 2026-11-25); a quit made sooner than that after the last block
//            write, or a game that writes its table every frame, saves the previous capture (Codex sn5 review round 2).
//            KNOWN LIMITATION (docs/backlog.md B1, deferred by Michael 2026-11-25): the Pocket menu opened DURING a game's
//            start-up RAM clear after its operator menu (about one frame), and kept open for COMMIT_VBL frames, lets a commit
//            capture start, which resets the sweep count; the rest of the clear is then not seen as a re-initialisation, so
//            the scores held before the operator-menu visit are not restored and the factory table can be saved over them.
//            Blocks of more than 256 words have no second half: their capture overwrites the committed copy in place, so an
//            interrupted or rejected capture (or a sweep) can leave a mixture there. Every configured game uses at most 64.
// The buffer is never written from the SRAM port: any block write outside WAIT (during a transfer as well, and the engine's
// own restore writes) marks the block dirty, so the next commit picks it up.
//
// Re-initialisation without a reset (review 2026-11-25): Spider-Man and SegaSonic leave their test menu by jumping into their
// start-up code without a CPU reset. That code first writes 0x0000 to every word of work RAM in ascending order, the score
// block included, then rebuilds the default table. The engine recognises exactly that: a full-word 0x0000 write to every
// block word, from word 0 to the last, in ascending order with no other value written into the block in between (a word
// written several clocks in a row counts once; writes outside the block do not interrupt it). Ordinary gameplay writes cannot
// produce it: they write non-zero data, or not every word, or not in this order. On a complete sweep the engine goes back to
// WAIT with the markers forgotten, i.e. it behaves as after a CPU reset: when the game's initialisation has written both
// markers and the block has been quiet, the committed copy (the scores before the sweep) is restored. The sweep is counted
// only while LIVE or capturing (the engine's own restore writes never count). A capture that completes while a sweep is
// under way is not committed (it is retried later), and one during which the sweep completes is discarded, so the committed
// copy never holds a half-cleared table.
// An explicit clear that rewrites the table WITHOUT such a sweep is ordinary data: it is committed like any other write.
//
// Double buffering uses the two halves of the one 512-word buffer, so it needs no further memory; it applies to blocks of
// up to 256 words (every configured game). A larger block uses the whole buffer and is re-captured in place (a sweep that
// starts during such a capture would then reach the committed copy).
//
// Read-back (rb_*): the header is reported only when the buffer holds a valid block: a loaded save not yet restored (so an
// exit before the restore keeps the old scores), or a restored / captured block. Otherwise the score words read 0 and the
// next load ignores them. A core reset keeps the buffer: the block is restored again after the game re-initialises.
`default_nettype none
module u1_hiscore #(
    parameter [5:0] QUIET_VBL  = 6'd32,
    parameter [5:0] COMMIT_VBL = 6'd2
) (
    input  wire        clk,
    input  wire        rst,                // CPU reset (level)
    input  wire        vbl,                // one pulse per frame
    input  wire        en,
    input  wire [14:0] base,
    input  wire  [8:0] lenm1,
    input  wire [15:0] tag,
    input  wire  [7:0] m0, m1,
    input  wire  [9:0] m1_off,
    // the SRAM port as driven (after u1_wram_pr): every write to the work RAM, the engine's own included
    input  wire        x_we,
    input  wire [15:0] x_addr, x_wdata,
    input  wire  [1:0] x_be,
    // work-RAM second-port client (through u1_pr_mux)
    output reg         hs_req = 1'b0,
    output reg         hs_we = 1'b0,
    output wire [15:0] hs_addr, hs_wdata,
    input  wire [15:0] hs_q,
    input  wire        hs_ack,
    // Save slot load (data_loader, this clock): word index past the EEPROM image
    input  wire        ld_start,           // the Save slot's first word (file word 0) is being written: a new load begins
    input  wire        ld_en,
    input  wire  [9:0] ld_idx,
    input  wire [15:0] ld_data,
    // Save slot read-back
    input  wire        rb_act,             // a read-back owns the buffer's read port
    input  wire  [9:0] rb_idx,
    output reg  [15:0] rb_data = 16'd0,
    output wire  [2:0] dbg_state,
    output wire        dbg_have
);
    localparam [2:0] S_WAIT = 3'd0, S_RS_RD = 3'd1, S_RS_REQ = 3'd2, S_CP_REQ = 3'd3, S_LIVE = 3'd4;
    reg  [2:0] st = S_WAIT;
    reg  [8:0] cur = 9'd0;
    reg [15:0] wd = 16'd0;
    reg  [1:0] rdw = 2'd0;
    reg        m0_ok = 1'b0, m1_ok = 1'b0;
    reg  [5:0] qcnt = 6'd0;
    reg        have_cap = 1'b0;
    // header of the loaded file
    reg        l_magic = 1'b0, l_data = 1'b0, l_end = 1'b0;
    reg [15:0] l_tag = 16'd0, l_base = 16'd0, l_len = 16'd0;
    reg  [8:0] l_last = 9'd0;
    wire       ld_ok = l_magic && l_data && l_tag == tag && l_base == {1'b0, base} && l_len == {6'd0, lenm1} + 16'd1 && l_last >= lenm1 && l_end;
    wire       have  = en && (have_cap || ld_ok);
assign dbg_state = st;
assign dbg_have  = have;

// ---- the SRAM port's writes, registered once (keeps the range compare off the port's address path)
    reg        s_we = 1'b0;
    reg [15:0] s_addr = 16'd0, s_data = 16'd0;
    reg  [1:0] s_be = 2'd0;
always @(posedge clk) begin s_we <= x_we && en; s_addr <= x_addr; s_data <= x_wdata; s_be <= x_be; end
    wire [15:0] s_off = s_addr - {1'b0, base};
    wire        s_in  = s_we && !s_addr[15] && s_addr[14:0] >= base && s_off[14:0] <= {6'd0, lenm1};
    wire  [8:0] s_idx = s_off[8:0];
    // double buffering: csel = the committed half, tsel = the half a capture fills
    reg         csel = 1'b0, dirty = 1'b0, rearm = 1'b0;
    reg  [9:0]  zc = 10'd0;                                   // zero sweep: block words 0 .. zc-1 swept so far
    wire        dbl   = !lenm1[8];
    wire        tsel  = dbl ? !csel : csel;
    function automatic [8:0] adr(input h, input [8:0] o); adr = dbl ? {h, o[7:0]} : o; endfunction
    wire        zw     = s_in && s_be == 2'b11 && s_data == 16'h0000;   // counted only while LIVE / capturing (zc is held at 0 otherwise)
    wire        z_adv  = zw && {1'b0, s_idx} == zc;
    wire        z_rep  = zw && zc != 10'd0 && {1'b0, s_idx} == zc - 10'd1;
    wire        sweep_done = z_adv && s_idx == lenm1;

// ---- buffer: 512 x 16, byte lanes; port A write, port B registered read
    (* ramstyle = "M10K" *) reg [1:0][7:0] mem [0:511];
    reg [15:0] mem_q = 16'd0;
    // Load window (review HS round 2): a Save load is taken only before the first capture / restore of this configuration
    // (have_cap = 0) and while the engine waits. Later loads change nothing: the buffer then belongs to the running game.
    // Every load starts with ld_start, which forgets the previous load's header, so an EEPROM-only file loaded after a
    // file with scores leaves no valid block.
    wire        ld_open = !have_cap && st == S_WAIT;
    // A load is accepted or rejected AS A WHOLE, at its ld_start (review HS round 3): ld_acc is set only by an ld_start seen
    // inside the window and dropped as soon as the engine leaves WAIT, so a load whose start was rejected, or that was cut by
    // the first transfer, cannot deliver its remaining words after a core reset has brought the engine back to WAIT.
    reg         ld_acc = 1'b0;
    wire        ld_e    = ld_en && ld_acc && ld_open;
    wire        ld_dat  = ld_e && ld_idx >= 10'd4 && ld_idx < 10'd516;
    wire  [9:0] ld_off  = ld_idx - 10'd4;
    wire        cap_wr  = (st == S_CP_REQ) && hs_ack;
    // A loaded word is stored at its plain file offset, independent of the configuration (sn5 hardware regression 2026-11-25:
    // the Pocket loads the Save slot BEFORE the Configuration slot, so lenm1 / dbl are still 0 / 1 while the save arrives; sn5
    // stored only offsets <= lenm1 = 0 and restored a zeroed block). The block length is interpreted only by ld_ok, once the
    // configuration has arrived. Plain offsets are the committed half's addresses: inside the load window csel is always 0
    // (csel changes only when a capture completes, which sets have_cap and closes the window for good), and adr(0, o) = o for
    // every block word o <= lenm1 in both layouts. File words past a short block land in the other half, which a capture
    // overwrites completely before it is ever read.
    wire        ld_wr   = ld_dat;
    wire        a_wr    = ld_wr || cap_wr;
    wire  [8:0] a_addr  = ld_wr ? ld_off[8:0] : adr(tsel, cur);
    wire [15:0] a_data  = ld_wr ? ld_data : hs_q;
    wire  [9:0] rb_off  = rb_idx - 10'd4;
    wire  [8:0] b_addr  = rb_act ? adr(csel, rb_off[8:0]) : adr(csel, cur);
always @(posedge clk) begin
    if (a_wr) mem[a_addr] <= a_data;
    mem_q <= mem[b_addr];
end

// ---- Save slot load: header words and the "whole block arrived" flag
always @(posedge clk) begin
    if (ld_start) ld_acc <= ld_open; else if (st != S_WAIT) ld_acc <= 1'b0;
    if (ld_start && ld_open) begin l_magic <= 1'b0; l_data <= 1'b0; l_end <= 1'b0; end
  if (ld_e) begin
    if (ld_idx == 10'd0) begin l_magic <= ld_data == 16'h5348; l_data <= 1'b0; l_end <= 1'b0; end
    if (ld_idx == 10'd1) l_tag  <= ld_data;
    if (ld_idx == 10'd2) l_base <= ld_data;
    if (ld_idx == 10'd3) l_len  <= ld_data;
    if (ld_dat) begin l_data <= 1'b1; l_last <= ld_off[8:0]; end
    if (ld_idx == 10'd516) l_end <= ld_data == 16'h4548;
  end
end

// ---- read-back data (registered; rb_idx is held by the reader for several clocks)
    reg  [9:0] rb_idx_q = 10'd0;
always @(posedge clk) begin
    rb_idx_q <= rb_idx;
    rb_data  <= !have ? 16'd0 :
                rb_idx_q == 10'd0 ? 16'h5348 : rb_idx_q == 10'd1 ? tag : rb_idx_q == 10'd2 ? {1'b0, base} :
                rb_idx_q == 10'd3 ? {6'd0, lenm1} + 16'd1 : rb_idx_q == 10'd516 ? 16'h4548 :
                (rb_idx_q >= 10'd4 && rb_idx_q - 10'd4 <= {1'b0, lenm1}) ? mem_q : 16'd0;
end

// ---- markers, quiet timer, sequence
    wire hit0 = s_in && s_idx == 9'd0 && s_be[0];
    wire hit1 = s_in && s_idx == m1_off[9:1] && s_be[m1_off[0]];
    wire [7:0] d1 = m1_off[0] ? s_data[15:8] : s_data[7:0];
assign hs_addr  = {1'b0, base} + {7'd0, cur};
assign hs_wdata = wd;
always @(posedge clk) begin
    if (rst || !en) begin
        st <= S_WAIT; m0_ok <= 1'b0; m1_ok <= 1'b0; qcnt <= 6'd0; hs_req <= 1'b0; hs_we <= 1'b0; cur <= 9'd0; rdw <= 2'd0;
        zc <= 10'd0; rearm <= 1'b0; dirty <= 1'b0;
    end else begin
        if (hit0) m0_ok <= s_data[7:0] == m0;
        if (hit1) m1_ok <= d1 == m1;
        if (s_in) qcnt <= 6'd0; else if (vbl && qcnt != 6'd63) qcnt <= qcnt + 6'd1;
        // zero sweep (see the header): only while LIVE or capturing
        if (st != S_LIVE && st != S_CP_REQ) zc <= 10'd0;
        else if (s_in) zc <= z_adv ? zc + 10'd1 : z_rep ? zc : (zw && s_idx == 9'd0) ? 10'd1 : 10'd0;
        // The sweep itself writes 0 over both marker bytes, so the markers count again only once the initialisation that
        // follows has written them, also when the engine is still finishing a capture then (the re-arm is taken after it).
        if (sweep_done) rearm <= 1'b1;
        // any block write outside WAIT (during a transfer too, the engine's own restore writes included) is committed later
        if (st != S_WAIT && s_in) dirty <= 1'b1;
        case (st)
        S_WAIT: if (m0_ok && m1_ok && qcnt >= QUIET_VBL && !s_in) begin
            cur <= 9'd0; rdw <= 2'd0;
            if (have) st <= S_RS_RD; else begin hs_req <= 1'b1; hs_we <= 1'b0; st <= S_CP_REQ; end
        end
        S_RS_RD: if (rb_act) rdw <= 2'd0;                       // the read-back owns the read port: start the read again after it
                 else if (rdw != 2'd2) rdw <= rdw + 2'd1;
                 else begin wd <= mem_q; hs_req <= 1'b1; hs_we <= 1'b1; st <= S_RS_REQ; end
        S_RS_REQ: if (hs_ack) begin
            hs_req <= 1'b0; hs_we <= 1'b0; rdw <= 2'd0;
            if (cur == lenm1) begin have_cap <= 1'b1; st <= S_LIVE; end else begin cur <= cur + 9'd1; st <= S_RS_RD; end
        end
        S_CP_REQ: if (hs_ack) begin
            if (cur == lenm1) begin
                hs_req <= 1'b0;
                if (rearm || sweep_done) begin                    // the block was swept during the capture: discard it, re-arm
                    st <= S_WAIT; rearm <= 1'b0; dirty <= 1'b0;
                end else if (dirty || s_in) begin                 // the block was written during the scan (Codex sn5 review finding 5):
                    st <= S_LIVE; dirty <= 1'b1;                  // the copy may mix two moments, keep the committed copy, retry.
                    // This also covers a sweep under way (zc != 0 or z_adv): zc is 0 when a scan starts and only advances on a
                    // block write during it, which sets dirty, so the former separate sweep deferral was redundant (it survived
                    // as an equivalent mutant, no_defer, 2026-11-25) and is removed.
                end else begin
                    have_cap <= 1'b1; csel <= tsel; st <= S_LIVE;
                end
            end else cur <= cur + 9'd1;
        end
        S_LIVE: if (rearm || sweep_done) begin                    // re-initialisation without a reset: as after a reset
                    st <= S_WAIT; rearm <= 1'b0; dirty <= 1'b0;
                end else if (dirty && !s_in && qcnt >= COMMIT_VBL) begin
                    cur <= 9'd0; hs_req <= 1'b1; hs_we <= 1'b0; dirty <= 1'b0; st <= S_CP_REQ;
                    zc <= 10'd0;   // a sweep never stops for COMMIT_VBL frames: a partial one that has is not a sweep
                end
        default: st <= S_WAIT;
        endcase
    end
end
`ifdef SIMULATION
integer n_mir = 0, n_pre = 0, n_commit = 0, n_rearm = 0;
always @(posedge clk) if (ld_wr && csel) $display("HS ERROR: Save load write while csel = 1");
always @(posedge clk) begin
    if (!rst && en && st == S_CP_REQ && hs_ack && cur == lenm1 && have_cap) begin
        n_commit <= n_commit + 1;
        if (n_commit < 50) $display("HS %s", (rearm || sweep_done) ? "COMMIT discarded (sweep)" : (zc != 10'd0 || z_adv) ? "COMMIT deferred (sweep in progress)" : (dirty || s_in) ? "COMMIT deferred (written during the scan)" : "COMMIT");
    end
    if (!rst && en && (st == S_LIVE || st == S_CP_REQ) && sweep_done) begin n_rearm <= n_rearm + 1; $display("HS REARM (zero sweep of the block, state %0d)", st); end
    if (!rst && en && st == S_WAIT && m0_ok && m1_ok && qcnt >= QUIET_VBL && !s_in) $display("HS %s start", have ? "RESTORE" : "CAPTURE");
    if (st != S_LIVE && !rst && en && ((st == S_RS_REQ && hs_ack && cur == lenm1) || (st == S_CP_REQ && hs_ack && cur == lenm1))) $display("HS live words=%0d", cur + 1);
    if (s_in && st != S_WAIT && n_mir < 200) begin n_mir <= n_mir + 1; $display("HS write idx=%0d data=%04x be=%0d state=%0d", s_idx, s_data, s_be, st); end
    if (s_in && st == S_WAIT && n_pre < 600) begin n_pre <= n_pre + 1; $display("HS prewrite idx=%0d data=%04x be=%0d", s_idx, s_data, s_be); end
end
final $display("HSSUM en=%0d state=%0d have=%0d ld_ok=%0d m0_ok=%0d m1_ok=%0d commits=%0d rearms=%0d", en, st, have, ld_ok, m0_ok, m1_ok, n_commit, n_rearm);
`endif
endmodule

// u1_pr_mux: two clients on the work RAM's second port. Client A (upstream's protection responder) is passed through
// combinationally, so with client B idle the port behaves exactly as without this module. Client B (u1_hiscore) takes the
// port only in a clock where A is not requesting (A holds its request until its acknowledge, so no A access is in flight
// then) and keeps it until its own acknowledge; A's request simply waits meanwhile.
module u1_pr_mux (
    input  wire        clk, rst,
    input  wire        a_req, a_we,
    input  wire [15:0] a_addr, a_wdata,
    input  wire  [1:0] a_be,
    output wire        a_ack,
    input  wire        b_req, b_we,
    input  wire [15:0] b_addr, b_wdata,
    output wire        b_ack,
    output wire        m_req, m_we,
    output wire [15:0] m_addr, m_wdata,
    output wire  [1:0] m_be,
    input  wire        m_ack
);
    reg b_own = 1'b0;
always @(posedge clk)
    if (rst) b_own <= 1'b0;
    else if (b_own) begin if (m_ack) b_own <= 1'b0; end
    else if (b_req && !a_req) b_own <= 1'b1;
assign m_req   = b_own ? b_req : a_req;
assign m_we    = b_own ? b_we : a_we;
assign m_addr  = b_own ? b_addr : a_addr;
assign m_wdata = b_own ? b_wdata : a_wdata;
assign m_be    = b_own ? 2'b11 : a_be;
assign a_ack   = m_ack && !b_own;
assign b_ack   = m_ack && b_own;
endmodule
`default_nettype wire
