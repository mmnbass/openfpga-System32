//============================================================================
//  DERIVED COPY (exp/sprite-path, experiments/SP-sprite-path) of upstream
//  references/s32 rtl/mem/sdram.sv at accd644 (sha256 dca0d505...3edb).
//  The pinned upstream file is not modified. sp_sdram.diff (same directory)
//  is the exact unified diff against it; tools/check_derived.py verifies that
//  the diff reproduces this file from the pinned upstream file.
//
//  SP additions (everything else is upstream's text and behaviour):
//    * two framebuffer clients, both clk_ram-domain, level-request /
//      done-pulse (no mailboxes: the client holds its parameters stable):
//        fw: row-burst WRITE stream (one 16-bit word per cycle, each word its
//            own column and DQM, so masked pixels cost nothing) or FILL
//            (constant FFFF over a column range = erase);
//        fr: row-burst READ (scanout), data returned with its column.
//      Both stay inside one 1 KB page (= one 512-pixel line = one SDRAM row in
//      upstream's address mapping), so a grant is one ACT + n commands + AP.
//    * the arbitration decision is registered one cycle ahead of ST_IDLE
//      (timing: the rotation left the command cone), the port in service masked.
//    * arbitration: the FB pair is a 7th member of upstream's background
//      round-robin (so it cannot starve and cannot starve anyone), plus an
//      URGENT class for a late scanout read, served right after p1's deadline
//      credit. p1's credit, refresh and the download port are unchanged.
//    * preemption: a normal FB grant stops at the next word once p0, p1 or a
//      refresh is pending and at least FB_MINW (write stream) / FB_MINW_RD
//      (read, fill) words were issued (the stopping
//      command carries auto-precharge), and never runs past FB_CAP words.
//      An urgent read runs at most FB_URG_CAP words and is not preempted.
//      Worst-case blocking of p1 by an FB grant is therefore bounded by a
//      constant comparable to upstream's own 8-word p2 burst (README).
//    * the state enum grows from 4 to 5 bits.
//============================================================================
//  Sega System 32 for MiSTer — SDRAM controller
//  16-bit SDR SDRAM @ clk_ram (96.6 MHz), CL2, strictly serialized
//  transactions. ROM-download writes keep a row open for same-row words and
//  explicitly precharge before row changes, reads, or refreshes.
//  Six request ports with bounded tile-deadline priority and round-robin
//  background fairness (DESIGN.md §4.2):
//    p0: V60 fetch/data (16-bit exact word or aligned 64-bit cache line)
//    p1: tile fetch      (64-bit burst = 4 words)
//    p2: sprite fetch    (128-bit burst = 8 words)
//    p3: Z80 ROM         (16-bit single, byte laned)
//    p4: MultiPCM        (16-bit single)
//    p5: V25 program ROM  (64-bit burst = 4 words)
//  Write port (ROM download only): highest priority while ioctl_download.
//
//  REQUEST CONTRACT (all ports, including wr): one transaction per req
//  RISING EDGE; the address (and write data/be) is sampled on that edge.
//  A request held high is serviced exactly ONCE — a requester expecting
//  re-service per ack from a held level will hang.  Requesters must be
//  single-outstanding: drop req after (or pulse it before) each ack, and
//  produce a fresh rising edge for the next transaction.  ack is stretched
//  to 2 clk_ram cycles so clk_sys-domain requesters sample it exactly once.
//  (SP: the fw/fr ports use the level/done contract documented at the ports.)
//============================================================================

module sp_sdram #(
    parameter integer FB_CAP     = 64,  // SP: max words per normal FB grant
    parameter integer FB_URG_CAP = 16,  // SP: max words per urgent read grant
    parameter integer FB_MINW    = 4,   // SP: write words issued before preemption
    parameter integer FB_MINW_RD = 16,  // SP: read / fill words issued before preemption
    parameter bit     FB_PREEMPT = 1'b1 // SP: 0 = negative control only
)(
    input             clk,          // clk_ram
    input             init,         // reset/init request
    output reg        ready,

    // SDRAM chip interface
    inout      [15:0] SDRAM_DQ,
    output reg [12:0] SDRAM_A,
    output reg  [1:0] SDRAM_BA,
    output            SDRAM_DQML,
    output            SDRAM_DQMH,
    output reg        SDRAM_nCS,
    output reg        SDRAM_nCAS,
    output reg        SDRAM_nRAS,
    output reg        SDRAM_nWE,
    output            SDRAM_CKE,

    // download/write port (word writes, ROM load)
    input             wr_req,
    input      [24:1] wr_addr,
    input      [15:0] wr_din,
    input       [1:0] wr_be,
    output reg        wr_ack,

    // p0: V60
    input             p0_req,
    input             p0_burst,
    input      [24:1] p0_addr,
    output reg [63:0] p0_dout,
    output reg        p0_ack,

    // p1: tiles — 4-word burst, aligned to 8 bytes
    input             p1_req,
    input      [24:3] p1_addr,
    output reg [63:0] p1_dout,
    output reg        p1_ack,

    // p2: sprites — 8-word burst, aligned to 16 bytes
    input             p2_req,
    input      [24:4] p2_addr,
    output reg [127:0] p2_dout,
    output reg        p2_ack,

    // p3: Z80
    input             p3_req,
    input      [24:1] p3_addr,
    output reg [15:0] p3_dout,
    output reg        p3_ack,

    // p4: MultiPCM
    input             p4_req,
    input      [24:1] p4_addr,
    output reg [15:0] p4_dout,
    output reg        p4_ack,

    // p5: V25 program ROM - 4-word burst, aligned to 8 bytes
    input             p5_req,
    input      [24:3] p5_addr,
    output reg [63:0] p5_dout,
    output reg        p5_ack,

    // SP fw: framebuffer write job (clk_ram). Hold fw_req and the job fields
    // stable until fw_done (1-cycle pulse); drop fw_req for at least one
    // cycle before the next job. Stream mode (fw_fill=0): a show-ahead
    // stream of {fw_col, fw_data, fw_be, fw_last}; fw_pop (combinational)
    // consumes the head on the clock edge it is high. The job ends with the
    // word marked fw_last. Fill mode: columns fw_c0..fw_c1 <- FFFF.
    input             fw_req,
    input      [24:10] fw_page,
    input             fw_fill,
    input       [8:0] fw_c0,
    input       [8:0] fw_c1,
    input             fw_valid,
    input       [8:0] fw_col,
    input      [15:0] fw_data,
    input       [1:0] fw_be,
    input             fw_last,
    output            fw_pop,
    output reg        fw_done,

    // SP fr: framebuffer read job (clk_ram). Hold fr_req and the fields
    // until fr_done; words fr_c0 .. fr_c0+fr_n-1 (fr_n >= 1, same page)
    // return on fr_dv with their column. fr_urgent may rise while pending.
    input             fr_req,
    input      [24:10] fr_page,
    input       [8:0] fr_c0,
    input       [9:0] fr_n,
    input             fr_urgent,
    output reg        fr_dv,
    output reg  [8:0] fr_dcol,
    output reg [15:0] fr_dout,
    output reg        fr_done
);

reg [15:0] dq_out;
reg        dq_oe;
reg  [1:0] dqm;

assign SDRAM_CKE  = 1'b1;
assign SDRAM_DQML = dqm[0];
assign SDRAM_DQMH = dqm[1];

localparam BURST_1   = 3'b000;
localparam CL        = 3'd2;

// commands {nCS,nRAS,nCAS,nWE}
localparam CMD_NOP   = 4'b0111;
localparam CMD_ACT   = 4'b0011;
localparam CMD_READ  = 4'b0101;
localparam CMD_WRITE = 4'b0100;
localparam CMD_PRE   = 4'b0010;
localparam CMD_REF   = 4'b0001;
localparam CMD_MRS   = 4'b0000;

reg  [3:0] cmd = CMD_NOP;
assign {SDRAM_nCS, SDRAM_nRAS, SDRAM_nCAS, SDRAM_nWE} = cmd;

assign SDRAM_DQ = dq_oe ? dq_out : 16'hZZZZ;

// init sequencer
reg [15:0] init_cnt = 16'hffff;

// refresh: 8192 rows / 64 ms @96.6MHz -> every ~755 cycles
reg [9:0]  ref_cnt;
reg        ref_pend;

typedef enum logic [4:0] {
    ST_IDLE, ST_DISPATCH, ST_ACT, ST_RCD1, ST_RCD2, ST_RD, ST_RDW,
    ST_WR, ST_WRRC, ST_PRE_XFER, ST_PRE_REF, ST_REF, ST_REFW,
    ST_FBW, ST_FBW_END, ST_FBW_CLOSE, ST_FBR
} state_t;
state_t state = ST_IDLE;

reg [2:0]  grant;
reg [2:0]  rr_next;
reg [3:0]  rd_total;        // words to read (1/4/8)
reg [3:0]  rd_issued;
reg [3:0]  rd_captured;
reg [24:1] xfer_addr;
reg        is_write;
reg        reuse_open_write_row;
reg [15:0] din_r;
reg [1:0]  be_r;
reg [2:0]  wrrc_cnt;
reg [2:0]  refw_cnt;
reg [1:0]  pre_cnt;
reg        row_open;
reg  [1:0] open_bank;
reg [12:0] open_row;
reg [1:0]  ack_stretch;     // acks held 2 clk_ram cycles (clk_sys is /2 sync)

// Request mailboxes.  Capture every transaction's metadata with its request;
// arbitration may delay a lower-priority port long after the producer pulses
// req or moves on to its next address.  This mirrors the latched per-slot
// request interfaces used by mature MiSTer SDRAM frameworks.
reg p0_pend, p1_pend, p2_pend, p3_pend, p4_pend, p5_pend, wr_pend;
reg p0_burst_p;
reg [24:1] p0_addr_p, p3_addr_p, p4_addr_p, wr_addr_p;
reg [24:3] p1_addr_p;
reg [24:3] p5_addr_p;
reg [24:4] p2_addr_p;
reg [15:0] wr_din_p;
reg  [1:0] wr_be_p;

// SP: framebuffer job state. *_wait_low blocks a re-grant between a job's
// done pulse and the client dropping its level request.
reg        fw_wait_low, fr_wait_low;
reg        fill_act;                 // fill job started (fill_ptr valid)
reg  [8:0] fill_ptr;
reg        fill_last;                // registered (fill_ptr == fw_c1)
reg        rdj_act;                  // read job started (rd_ptr/rd_rem valid)
reg  [8:0] rd_ptr;
reg  [9:0] rd_rem;
reg        fb_is_rd;                 // current FB grant: read (1) / write (0)
reg        fb_urg;                   // current FB grant is an urgent read
reg [24:10] fb_page;
reg  [6:0] fb_cnt;                   // words issued in the current grant
reg  [1:0] since_wr;                 // cycles since the last FB WRITE (sat.)
reg        fb_last_w;                // FB slot alternation (read/write)
reg  [3:0] fbr_v, fbr_last;          // FB read tags, aligned with cl_pipe
reg  [8:0] fbr_col [0:3];
wire       fw_pend = fw_req && !fw_wait_low && (fw_fill || fw_valid);
wire       fr_pend = fr_req && !fr_wait_low;
wire       fb_pend = fw_pend | fr_pend;

// Tile rendering has a hard scanline deadline: if p1 waits behind a complete
// six-port rotation, the renderer can miss the next line and the parity line
// buffer displays stale texture data. Give a newly pending p1 burst one
// deadline-priority credit. After that burst, one background reader must run
// before p1 can use the credit again. Preserve the background round-robin
// pointer across a priority p1 grant so repeated tile traffic cannot keep
// restarting background selection at the same port.
// SP: the FB pair is background member 6 (after p5, before p0); an urgent
// scanout read is served after p1's credit and before the rotation.
// SP: the decision is REGISTERED (one cycle ahead of ST_IDLE) so the priority
// rotation does not sit between the request flops and the command/address
// registers. It is computed every cycle from the pending flags, with the port
// currently in service masked out (its pend clears only on its own ack, which
// can land on the very edge ST_IDLE is entered), so the registered decision
// seen in ST_IDLE never names the transfer that just finished. A request
// that arrives in the ST_IDLE cycle itself waits one cycle (counted in the
// p1 bound, README).
reg       read_valid;                // registered decision (used in ST_IDLE)
reg [2:0] read_grant;
reg       p1_priority_grant;
reg       fb_urgent_grant;
reg       fb_sel_rd;
reg       p1_was_last;
reg       rv_c, p1p_c, fbu_c, selrd_c;
reg [2:0] rg_c;
// a transfer is in service in every state except ST_IDLE and the refresh states
// (refresh starts only from ST_IDLE, after the previous transfer's pend cleared)
wire      in_svc   = !(state == ST_IDLE || state == ST_PRE_REF || state == ST_REF || state == ST_REFW);
wire      p0_pm = p0_pend && !(in_svc && grant == 3'd0);
wire      p1_pm = p1_pend && !(in_svc && grant == 3'd1);
wire      p2_pm = p2_pend && !(in_svc && grant == 3'd2);
wire      p3_pm = p3_pend && !(in_svc && grant == 3'd3);
wire      p4_pm = p4_pend && !(in_svc && grant == 3'd4);
wire      p5_pm = p5_pend && !(in_svc && grant == 3'd5);
wire      fbsvc = in_svc && grant == 3'd6;
wire      fw_pm = fw_pend && !fbsvc;
wire      fr_pm = fr_pend && !fbsvc;
wire      fb_pm = fw_pm | fr_pm;
wire      background_pend = p0_pm | p2_pm | p3_pm | p4_pm | p5_pm | fb_pm;
wire      p1_fairness_block = p1_was_last & background_pend;
wire [6:0] rr_pend = {fb_pm, p5_pm, p4_pm, p3_pm, p2_pm,
                      p1_pm && !p1_fairness_block, p0_pm};
always @* begin
    rv_c = 1'b1;
    rg_c = rr_next;
    p1p_c = 1'b0;
    fbu_c = 1'b0;
    if (p1_pm && !p1_was_last) begin
        rg_c = 3'd1;
        p1p_c = 1'b1;
    end
    else if (fr_pm && fr_urgent) begin
        rg_c = 3'd6;
        fbu_c = 1'b1;
    end
    else begin
        // Upstream's rotation (p0..p5 in order starting at rr_next) with the
        // FB member inserted after p5. The lowest offset from rr_next wins.
        // One constant-index priority chain per rr_next value (no modulo adder).
        rv_c = 1'b0;
        for (int r = 0; r < 7; r++)
            if (rr_next == 3'(r))
                for (int k = 6; k >= 0; k--)
                    if (rr_pend[(r + k) % 7]) begin
                        rg_c = 3'((r + k) % 7);
                        rv_c = 1'b1;
                    end
    end
    // FB member: an urgent grant is always the read; otherwise alternate when both pend.
    selrd_c = fbu_c || (fr_pm && (!fw_pm || fb_last_w));
end
always @(posedge clk) begin
    read_valid <= rv_c && !init;
    read_grant <= rg_c;
    p1_priority_grant <= p1p_c;
    fb_urgent_grant <= fbu_c;
    fb_sel_rd <= selrd_c;
end

// Latch each port on the RISING EDGE of its request.  The previous
// level-sampled guard (req && !pend && !ack) had a one-clk_ram drop window: a
// requester that pulses its next request in direct response to an ack (the V60
// icache fill chain does) can present a 2-cycle pulse whose first cycle is
// blocked by the still-clearing pend and whose second is blocked by the
// stretched ack — the transaction vanished and the port hung forever.  Which
// transactions hit the window depended on each ack's clk_ram parity, i.e. on
// arbitration history — so adding V25 p5 traffic could sink the V60 while
// lighter boards never faulted.  Edge-detection latches exactly once per
// request pulse (or per level-request start: the loader holds wr_req until
// wr_ack) regardless of ack overlap.
reg p0_req_d, p1_req_d, p2_req_d, p3_req_d, p4_req_d, p5_req_d, wr_req_d;
reg p0_ack_d2, p1_ack_d2, p2_ack_d2, p3_ack_d2, p4_ack_d2, p5_ack_d2, wr_ack_d2;
always @(posedge clk) begin
    // Completion clears pend on the ack RISING EDGE only, and FIRST, so a
    // same-edge new request edge below overrides it (the later nonblocking
    // assignment wins).  Two hazards are closed together: (1) a chained
    // request whose rising edge lands on the very edge the previous ack
    // clears pend — the V60 icache fill pattern — latches instead of
    // vanishing; (2) the 2-cycle ack stretch must not wipe a request that
    // latched during the stretch window on the stretch's second cycle.
    p0_ack_d2 <= p0_ack; p1_ack_d2 <= p1_ack; p2_ack_d2 <= p2_ack;
    p3_ack_d2 <= p3_ack; p4_ack_d2 <= p4_ack; p5_ack_d2 <= p5_ack;
    wr_ack_d2 <= wr_ack;
    if (p0_ack && !p0_ack_d2) p0_pend <= 1'b0;
    if (p1_ack && !p1_ack_d2) p1_pend <= 1'b0;
    if (p2_ack && !p2_ack_d2) p2_pend <= 1'b0;
    if (p3_ack && !p3_ack_d2) p3_pend <= 1'b0;
    if (p4_ack && !p4_ack_d2) p4_pend <= 1'b0;
    if (p5_ack && !p5_ack_d2) p5_pend <= 1'b0;
    if (wr_ack && !wr_ack_d2) wr_pend <= 1'b0;
    p0_req_d <= p0_req; p1_req_d <= p1_req; p2_req_d <= p2_req;
    p3_req_d <= p3_req; p4_req_d <= p4_req; p5_req_d <= p5_req;
    wr_req_d <= wr_req;
    // Every s32 requester keeps at most one transaction outstanding (pulse, or
    // level held until ack), so an unqualified rising-edge latch is exact.
    if (p0_req && !p0_req_d) begin
        p0_pend <= 1'b1; p0_addr_p <= p0_addr;
        p0_burst_p <= p0_burst;
    end
    if (p1_req && !p1_req_d) begin
        p1_pend <= 1'b1; p1_addr_p <= p1_addr;
    end
    if (p2_req && !p2_req_d) begin
        p2_pend <= 1'b1; p2_addr_p <= p2_addr;
    end
    if (p3_req && !p3_req_d) begin
        p3_pend <= 1'b1; p3_addr_p <= p3_addr;
    end
    if (p4_req && !p4_req_d) begin
        p4_pend <= 1'b1; p4_addr_p <= p4_addr;
    end
    if (p5_req && !p5_req_d) begin
        p5_pend <= 1'b1; p5_addr_p <= p5_addr;
    end
    if (wr_req && !wr_req_d) begin
        wr_pend <= 1'b1; wr_addr_p <= wr_addr;
        wr_din_p <= wr_din; wr_be_p <= wr_be;
    end
    if (init) begin
        {p0_pend,p1_pend,p2_pend,p3_pend,p4_pend,p5_pend,wr_pend} <= '0;
        {p0_req_d,p1_req_d,p2_req_d,p3_req_d,p4_req_d,p5_req_d,wr_req_d} <= '0;
        {p0_ack_d2,p1_ack_d2,p2_ack_d2,p3_ack_d2,p4_ack_d2,p5_ack_d2,wr_ack_d2} <= '0;
        p0_addr_p <= '0; p1_addr_p <= '0; p2_addr_p <= '0;
        p0_burst_p <= 1'b0;
        p3_addr_p <= '0; p4_addr_p <= '0; p5_addr_p <= '0; wr_addr_p <= '0;
        wr_din_p <= '0; wr_be_p <= '0;
    end
end

`ifdef SIMULATION
// Contract watchdog (sim only): a request held high long after its pend
// cleared means the requester expects per-ack re-service from a level — the
// pre-edge-latch semantics.  It would receive exactly one transaction and
// hang silently in hardware; make that loud here.  Legitimate level holds
// drop within a few clk_ram of the stretched ack (clk_sys requesters take 2).
generate
    genvar gi;
    for (gi = 0; gi < 7; gi = gi + 1) begin : g_reqwatch
        reg [7:0] held;
        wire req_i  = gi==0 ? p0_req  : gi==1 ? p1_req  : gi==2 ? p2_req  :
                      gi==3 ? p3_req  : gi==4 ? p4_req  : gi==5 ? p5_req  : wr_req;
        wire pend_i = gi==0 ? p0_pend : gi==1 ? p1_pend : gi==2 ? p2_pend :
                      gi==3 ? p3_pend : gi==4 ? p4_pend : gi==5 ? p5_pend : wr_pend;
        always @(posedge clk) begin
            if (init || !req_i || pend_i) held <= 8'd0;
            else if (held != 8'hff) begin
                held <= held + 8'd1;
                if (held == 8'd200)
                    $display("SDRAM CONTRACT WARNING: port %0d req held %0d cycles after service — held levels are serviced once, not per ack", gi, held);
            end
        end
    end
endgenerate
`endif

// Centre the SDRAM board interface with SDRAM_CLK forwarded at 180 degrees.
// Commands and write data launched here have half a cycle of setup at the
// chip. CL2 read data returns to this direct pin-to-register sample under the
// SDC input-delay and multicycle constraints, so Quartus can place dq_in in
// the input IOE. The fourth pipe tap transfers the already-registered word
// into the response buffer one cycle later without a pin-to-core critical path.
reg [15:0] dq_in;
reg [3:0]  cl_pipe;
reg        pipes_idle;               // SP: registered (cl_pipe == 0 && fbr_v == 0)
reg [15:0] cap_buf [0:7];

always @(posedge clk) dq_in <= SDRAM_DQ;

task automatic deliver(input [15:0] final_word);
    case (grant)
        // The final buffer write and delivery share an edge.  Use the staged
        // word directly for the last lane instead of returning stale cap_buf.
        3'd0: begin
            p0_dout <= (rd_total == 4'd4)
                       ? {final_word, cap_buf[2], cap_buf[1], cap_buf[0]}
                       : {48'd0, final_word};
            p0_ack <= 1'b1;
        end
        3'd1: begin p1_dout <= {final_word, cap_buf[2], cap_buf[1], cap_buf[0]}; p1_ack <= 1'b1; end
        3'd2: begin p2_dout <= {final_word, cap_buf[6], cap_buf[5], cap_buf[4],
                                cap_buf[3], cap_buf[2], cap_buf[1], cap_buf[0]}; p2_ack <= 1'b1; end
        3'd3: begin p3_dout <= final_word; p3_ack <= 1'b1; end
        3'd4: begin p4_dout <= final_word; p4_ack <= 1'b1; end
        3'd5: begin p5_dout <= {final_word, cap_buf[2], cap_buf[1], cap_buf[0]}; p5_ack <= 1'b1; end
        default: ;
    endcase
endtask

// SP: FB command fields for this cycle (used only in ST_FBW / ST_FBR).
wire        fbw_fill  = fw_fill;
wire        fbw_have  = fbw_fill ? 1'b1 : fw_valid;
wire  [8:0] fbw_col   = fbw_fill ? fill_ptr : fw_col;
wire [15:0] fbw_data  = fbw_fill ? 16'hFFFF : fw_data;
wire  [1:0] fbw_be    = fbw_fill ? 2'b11 : fw_be;
wire        fbw_last  = fbw_fill ? fill_last : fw_last;
wire        fb_preq   = FB_PREEMPT && (p1_pend | p0_pend | ref_pend);
wire        fb_min_ok = (fb_cnt >= 7'(FB_MINW - 1));
wire        fbw_capst = (fb_cnt == 7'(FB_CAP - 1));
wire        fbr_min_ok = (fb_cnt >= 7'(FB_MINW_RD - 1));
// a fill (erase) has no stream bubbles and is as cheap per word as a read: same minimum run
wire        fbw_stop  = fbw_last | fbw_capst | (fb_preq & (fbw_fill ? fbr_min_ok : fb_min_ok));
wire        fbr_capst = fb_urg ? (fb_cnt == 7'(FB_URG_CAP - 1)) : (fb_cnt == 7'(FB_CAP - 1));
wire        fbr_stop  = (rd_rem == 10'd1) | fbr_capst | (!fb_urg & fb_preq & fbr_min_ok);
assign fw_pop = (state == ST_FBW) && !fw_fill && fw_valid;

always @(posedge clk) begin
    cmd    <= CMD_NOP;
    dq_oe  <= 1'b0;
    fw_done <= 1'b0;
    fr_done <= 1'b0;
    fr_dv   <= 1'b0;
    if (!fw_req) fw_wait_low <= 1'b0;
    if (!fr_req) fr_wait_low <= 1'b0;

    // acks: assert for 2 cycles so clk_sys (=clk/2, synchronous) always
    // samples exactly one rising edge with ack high
    if (ack_stretch != 0) ack_stretch <= ack_stretch - 1'd1;
    else begin
        p0_ack <= 1'b0; p1_ack <= 1'b0; p2_ack <= 1'b0;
        p3_ack <= 1'b0; p4_ack <= 1'b0; p5_ack <= 1'b0; wr_ack <= 1'b0;
    end

    if (init) begin
        init_cnt <= 16'hffff;
        ready    <= 1'b0;
        state    <= ST_IDLE;
        ref_pend <= 1'b0;
        ref_cnt  <= 10'd0;
        row_open <= 1'b0;
        open_bank <= 2'b00;
        open_row <= 13'd0;
        reuse_open_write_row <= 1'b0;
        pre_cnt <= 2'd0;
        dqm      <= 2'b11;
        cl_pipe  <= 4'b0000;
        ack_stretch <= 0;
        rr_next <= 3'd0;
        p1_was_last <= 1'b0;
        fw_wait_low <= 1'b0; fr_wait_low <= 1'b0;
        fill_act <= 1'b0; rdj_act <= 1'b0;
        fbr_v <= 4'b0000; fbr_last <= 4'b0000; pipes_idle <= 1'b1;
        fb_last_w <= 1'b0; since_wr <= 2'd3;
    end
    else if (!ready) begin
        init_cnt <= init_cnt - 1'd1;
        dqm      <= 2'b11;
        // init: wait >100us, PRE-all, 8x REF, MRS (JEDEC)
        case (init_cnt)
            16'h0400: begin cmd <= CMD_PRE; SDRAM_A[10] <= 1'b1; end
            16'h03c0, 16'h0380, 16'h0340, 16'h0300,
            16'h02c0, 16'h0280, 16'h0240, 16'h0200: cmd <= CMD_REF;
            16'h00a0: begin
                cmd      <= CMD_MRS;
                SDRAM_BA <= 2'b00;
                SDRAM_A  <= 13'b000_0_00_010_0_000; // CL2, sequential, burst 1
            end
            16'h0001: ready <= 1'b1;
            default: ;
        endcase
    end
    else begin
        dqm <= 2'b00;
        // refresh scheduling: 8192 rows / 64ms @ 96.65MHz -> every 755 cyc
        ref_cnt <= ref_cnt + 1'd1;
        if (ref_cnt == 10'd700) begin ref_cnt <= 0; ref_pend <= 1'b1; end

        // Read capture after CL2 and the centred IOE register above.
        cl_pipe <= {cl_pipe[2:0], 1'b0};
        if (cl_pipe[3]) begin
            cap_buf[rd_captured[2:0]] <= dq_in;
            rd_captured <= rd_captured + 1'd1;
            if (rd_captured + 1'd1 == rd_total) begin
                deliver(dq_in);
                ack_stretch <= 2'd1;   // hold ack this cycle + next
            end
        end
        // SP: FB read data, same tap as cl_pipe[3].
        fbr_v    <= {fbr_v[2:0], 1'b0};
        fbr_last <= {fbr_last[2:0], 1'b0};
        fbr_col[1] <= fbr_col[0]; fbr_col[2] <= fbr_col[1]; fbr_col[3] <= fbr_col[2];
        if (fbr_v[3]) begin
            fr_dv   <= 1'b1;
            fr_dout <= dq_in;
            fr_dcol <= fbr_col[3];
            if (fbr_last[3]) begin
                fr_done <= 1'b1;
                fr_wait_low <= 1'b1;
            end
        end
        if (since_wr != 2'd3) since_wr <= since_wr + 1'd1;
        // next cycle's (cl_pipe == 0 && fbr_v == 0): only ST_RD / ST_FBR load a pipe
        pipes_idle <= (cl_pipe[2:0] == 3'b000) && (fbr_v[2:0] == 3'b000) && !(state == ST_RD || state == ST_FBR);

        case (state)
        ST_IDLE: begin
            if (ref_pend && pipes_idle) begin
                cmd <= CMD_PRE; SDRAM_A[10] <= 1'b1;
                row_open <= 1'b0;
                refw_cnt <= 3'd1;             // tRP >= 2 cycles before REF
                state <= ST_PRE_REF;
            end
            else if (wr_pend | read_valid) begin
                logic [24:1] a;
                if      (wr_pend) begin grant <= 3'd7; a = wr_addr_p;           rd_total <= 4'd1; is_write <= 1'b1; end
                else begin
                    grant <= read_grant;
                    is_write <= 1'b0;
                    p1_was_last <= (read_grant == 3'd1);
                    if (!p1_priority_grant && !fb_urgent_grant)
                        rr_next <= (read_grant == 3'd6) ? 3'd0 : read_grant + 1'd1;
                    case (read_grant)
                        3'd0: begin
                            // Cache-line fills are exactly four aligned words;
                            // protection and other exact reads stay single-word.
                            a = p0_burst_p ? {p0_addr_p[24:3], 2'b00} : p0_addr_p;
                            rd_total <= p0_burst_p ? 4'd4 : 4'd1;
                        end
                        3'd1: begin a = {p1_addr_p, 2'b00};  rd_total <= 4'd4; end
                        3'd2: begin a = {p2_addr_p, 3'b000}; rd_total <= 4'd8; end
                        3'd3: begin a = p3_addr_p;           rd_total <= 4'd1; end
                        3'd4: begin a = p4_addr_p;           rd_total <= 4'd1; end
                        3'd6: begin
                            // SP: FB member. An urgent grant is always the
                            // read; otherwise alternate when both pend.
                            logic sel_rd;
                            sel_rd = fb_sel_rd;
                            fb_is_rd  <= sel_rd;
                            fb_urg    <= fb_urgent_grant;
                            fb_last_w <= !sel_rd;
                            fb_page   <= sel_rd ? fr_page : fw_page;
                            a = {(sel_rd ? fr_page : fw_page), 9'd0};
                            rd_total <= 4'd0;
                            if (sel_rd && !rdj_act) begin
                                rdj_act <= 1'b1; rd_ptr <= fr_c0; rd_rem <= fr_n;
                            end
                            if (!sel_rd && fw_fill && !fill_act) begin
                                fill_act <= 1'b1; fill_ptr <= fw_c0; fill_last <= (fw_c0 == fw_c1);
                            end
                        end
                        default: begin a = {p5_addr_p, 2'b00}; rd_total <= 4'd4; end
                    endcase
                end
                xfer_addr <= a;
                // The selected address and transfer type are already stable at
                // this arbitration boundary.  Register the row-reuse decision
                // here so ST_DISPATCH does not place the bank/row comparator in
                // the SDRAM command-output timing cone.
                // SP: compared on wr_addr_p directly (a == wr_addr_p whenever
                // wr_pend), keeping the 7-way address mux out of this cone.
                reuse_open_write_row <= wr_pend && row_open &&
                                        wr_addr_p[24:23] == open_bank &&
                                        wr_addr_p[22:10] == open_row;
                din_r     <= wr_din_p;
                be_r      <= wr_be_p;
                rd_issued   <= 0;
                rd_captured <= 0;
                fb_cnt      <= 0;
                // Register arbitration before command generation.  The
                // dedicated dispatch cycle breaks the dense real-V25 path
                // from p5_pend through the six-port priority mux and row
                // decision into cmd[].  Requesters already wait for ack, so
                // the extra clk_ram cycle changes latency but not semantics.
                state <= ST_DISPATCH;
            end
        end

        ST_DISPATCH: begin
            // A same-row download write can reuse the active row. Every other
            // transfer first closes the row explicitly so reads retain their
            // original ACT/auto-precharge behavior.
            if (reuse_open_write_row) begin
                state <= ST_WR;
            end
            else if (row_open) begin
                cmd <= CMD_PRE; SDRAM_A[10] <= 1'b1;
                row_open <= 1'b0;
                pre_cnt <= 2'd1;          // tRP >= 2 cycles before ACT
                state <= ST_PRE_XFER;
            end
            else begin
                state <= ST_ACT;
            end
        end

        ST_PRE_XFER: begin
            if (pre_cnt == 0) state <= ST_ACT;
            else pre_cnt <= pre_cnt - 1'd1;
        end

        ST_ACT: begin
            cmd      <= CMD_ACT;
            SDRAM_BA <= xfer_addr[24:23];
            SDRAM_A  <= xfer_addr[22:10];
            open_bank <= xfer_addr[24:23];
            open_row  <= xfer_addr[22:10];
            row_open  <= 1'b1;
            state    <= ST_RCD1;
        end

        // tRCD >= 21ns = 3 cycles ACT->READ/WRITE
        ST_RCD1: state <= ST_RCD2;
        ST_RCD2: begin
            if (grant == 3'd6 && !is_write) state <= fb_is_rd ? ST_FBR : ST_FBW;
            else if (is_write) state <= ST_WR;
            else          state <= ST_RD;
        end

        ST_WR: begin
            cmd      <= CMD_WRITE;
            SDRAM_BA <= xfer_addr[24:23];
            SDRAM_A  <= {2'b00, 1'b0, xfer_addr[10:1]};  // keep row open
            dq_out   <= din_r;
            dq_oe    <= 1'b1;
            dqm      <= ~be_r;
            // No auto-precharge is used for download writes. The conservative
            // two-cycle write-recovery gap is enough before another WRITE on
            // the same row and still leaves explicit precharge for row changes.
            wrrc_cnt <= 3'd2;
            state    <= ST_WRRC;
        end
        ST_WRRC: begin
            if (wrrc_cnt == 3'd2) begin wr_ack <= 1'b1; ack_stretch <= 2'd1; end
            if (wrrc_cnt == 0) state <= ST_IDLE;
            else wrrc_cnt <= wrrc_cnt - 1'd1;
        end

        ST_RD: begin
            // issue one READ per cycle until rd_total issued
            cmd      <= CMD_READ;
            SDRAM_BA <= xfer_addr[24:23];
            SDRAM_A  <= {2'b00, (rd_issued + 1'd1 == rd_total) ? 1'b1 : 1'b0,
                         xfer_addr[10:1]};
            cl_pipe[0] <= 1'b1;
            xfer_addr[10:1] <= xfer_addr[10:1] + 1'd1;
            rd_issued <= rd_issued + 1'd1;
            if (rd_issued + 1'd1 == rd_total) begin
                row_open <= 1'b0; // final CAS requests auto-precharge
                state <= ST_RDW;
            end
        end
        ST_RDW: begin
            // wait for capture pipeline to finish (delivery in capture logic);
            // also cover tRC for the single-read case with the drain cycles
            if (pipes_idle) state <= ST_IDLE;
        end

        // SP: framebuffer row-burst write / fill. One WRITE per cycle, each
        // with its own column and DQM; the stopping WRITE auto-precharges.
        ST_FBW: begin
            if (fbw_have) begin
                cmd      <= CMD_WRITE;
                SDRAM_BA <= fb_page[24:23];
                SDRAM_A  <= {2'b00, fbw_stop, fb_page[10], fbw_col};
                dq_out   <= fbw_data;
                dq_oe    <= 1'b1;
                dqm      <= ~fbw_be;
                since_wr <= 2'd0;
                fb_cnt   <= fb_cnt + 1'd1;
                if (fbw_fill) begin
                    fill_ptr  <= fill_ptr + 1'd1;
                    fill_last <= (fill_ptr + 1'd1 == fw_c1);
                end
                if (fbw_stop) begin
                    row_open <= 1'b0;
                    wrrc_cnt <= 3'd2;          // tWR + tRP before the next ACT
                    state    <= ST_FBW_END;
                    if (fbw_last) begin
                        fw_done <= 1'b1;
                        fw_wait_low <= 1'b1;
                        fill_act <= 1'b0;
                    end
                end
            end
            else if (fb_preq || fbw_capst) begin
                // stream bubble while someone waits: close the row explicitly
                state <= ST_FBW_CLOSE;
            end
        end
        ST_FBW_END: begin
            if (wrrc_cnt == 0) state <= ST_IDLE;
            else wrrc_cnt <= wrrc_cnt - 1'd1;
        end
        ST_FBW_CLOSE: begin
            // tWR (2 cycles after the last WRITE; tRAS is covered because a
            // grant issues its first WRITE before any bubble) then PRE, tRP.
            if (since_wr >= 2'd2) begin
                cmd <= CMD_PRE; SDRAM_BA <= fb_page[24:23]; SDRAM_A[10] <= 1'b0;
                row_open <= 1'b0;
                wrrc_cnt <= 3'd2;
                state <= ST_FBW_END;
            end
        end

        // SP: framebuffer row-burst read. The stopping READ auto-precharges.
        ST_FBR: begin
            cmd      <= CMD_READ;
            SDRAM_BA <= fb_page[24:23];
            SDRAM_A  <= {2'b00, fbr_stop, fb_page[10], rd_ptr};
            fbr_v[0]    <= 1'b1;
            fbr_col[0]  <= rd_ptr;
            fbr_last[0] <= (rd_rem == 10'd1);
            rd_ptr <= rd_ptr + 1'd1;
            rd_rem <= rd_rem - 1'd1;
            fb_cnt <= fb_cnt + 1'd1;
            if (rd_rem == 10'd1) rdj_act <= 1'b0;
            if (fbr_stop) begin
                row_open <= 1'b0;
                state <= ST_RDW;
            end
        end

        ST_PRE_REF: begin
            if (refw_cnt == 0) begin
                cmd      <= CMD_REF;
                row_open <= 1'b0;
                ref_pend <= 1'b0;
                refw_cnt <= 3'd6;   // tRC(ref) >= 63ns = 7 cycles
                state    <= ST_REFW;
            end
            else refw_cnt <= refw_cnt - 1'd1;
        end
        ST_REF: state <= ST_REFW;   // (unused; kept for enum stability)
        ST_REFW: begin
            if (refw_cnt == 0) state <= ST_IDLE;
            else refw_cnt <= refw_cnt - 1'd1;
        end
        default: state <= ST_IDLE;
        endcase
    end
end

endmodule
