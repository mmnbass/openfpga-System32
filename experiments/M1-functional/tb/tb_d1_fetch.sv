// SPDX-License-Identifier: GPL-3.0-or-later
//
// tb_d1_fetch: regression for defect D1 (prefetch withdrawn on a fetch
// redirect) and its review findings (checkpoint C1). The V2 fetch unit
// (v2_fetch, or v2_fetch_byteq with +define+BYTEQ) and v2_bus run behind the
// real upstream s32_v60_bus adapter (read in place) and m1_mem (+memwait /
// +memjit wait states). A model of upstream's wide-port server (held if_ack,
// line returned shifted by if_addr[2:0], as s32_core's) serves fast_ifetch
// requests.
//
// Every scenario: reset, let the queue run, then at a chosen cycle offset
// apply an event and keep consuming. Offsets sweep every cycle, so the event
// lands in every bus phase. Scenarios:
//   0  one redirect                       4  reset with a prefetch outstanding
//   1  two redirects 1..3 cycles apart    5  SMC flush: a data write into the
//      (the second into the other           queued head line (odd offsets: a
//      ROM/non-ROM region)                   redirect in the same cycle)
//   2  redirect, then another after the   6  flush storm: memory and wide
//      stale acknowledge                     server stalled, 14..18 redirects
//   3  redirect with the queue full          on consecutive cycles while one
//                                            request is outstanding
// Targets alternate between ROM and non-ROM addresses (aliases of the same
// m1_mem bytes), so with +wide both ports and the switch between them run.
//
// Checks, every cycle:
//   PROTOCOL  a raised request (c_req, if_req) is held, with the same address,
//             size and direction, until its acknowledge, and dropped right
//             after it (never kept raised over the held acknowledge)
//   PC        head_pc equals an independent model: the reset address or the
//             redirect target, plus the bytes the bench consumed
//   DATA      every queued byte the window shows (min(avail, 8)) equals
//             memory at the modelled head + i (not checked while a bench data
//             write is in flight), so stale, misplaced, lost or reordered
//             bytes are caught
//   SEQUENCE  every fetch request issued after the last flush has exactly the
//             address and port a correct prefetcher issues next (from the
//             flush point, in order, each once); only the one request
//             outstanding at a flush may complete stale, so re-issued,
//             duplicated or skipped transactions are caught
//   READ      bench data reads (+dtraffic) return memory
//   PROGRESS  after the event the queue delivers 24 more bytes within a bound
// The bench prints coverage and "D1 PASS" only with zero errors.
//
// +smc runs the self-modifying-code suite instead (review findings C1-2,
// C1-2w, C1-3; docs/reviews/C1-codex-review.md): deterministic data writes of
// 1, 2 and 4 bytes, aligned and unaligned, against the queued bytes, with the
// same checks (so instruction bytes and request order are verified, not the
// flush signal). Groups:
//   range    writes at -4..+33 bytes from the head of a full queue (an
//            aligned and an unaligned head), not overlapping its start
//   below    the same, overlapping the queue's first bytes from below (C1-3)
//   half     a write at -2..+7 bytes from the line being filled, in the cycle
//            its first word is captured (C1-2; v2_fetch_byteq: the cycle a
//            run is captured)
//   wrap     the range and half cases with the queue spanning
//            0xFFFFFFFF -> 0 (C1-2w)
//   prefetch a write at -1..+9 bytes from the head while prefetch runs, the
//            event sweeping every cycle offset; every 4th with a redirect in
//            the flush cycle
//   redirect a redirect while a request is outstanding (now stale), then a
//            write 0..12 cycles later
//
// Plusargs: +wide (fast_ifetch)  +memwait / +memjit (m1_mem)  +dtraffic
//           (random data reads/writes contending with prefetch)
//           +maxk=<n> offsets swept (default 90)  +scen=<n> one scenario only
//           +smc

`timescale 1ps/1ps

module tb_d1_fetch;

reg clk = 0, rst = 1;
always #10349 clk = ~clk;
reg [1:0] ce_div = 0;
wire      bce = (ce_div == 2'd2);
always @(posedge clk) ce_div <= rst ? 2'd0 : ((ce_div == 2'd2) ? 2'd0 : ce_div + 2'd1);

// initial memory pattern: distinct, address-dependent bytes, on the 17-bit
// byte address m1_mem decodes (the adapter passes m_addr[16:1] through)
function [7:0] pat(input [31:0] a);
    reg [16:0] m;
    begin
        m = a[16:0];
        pat = m[7:0] ^ {m[11:8], m[15:12]} ^ 8'h5A ^ {m[16], 7'd0};
    end
endfunction

function automatic is_rom(input [31:0] a);
    is_rom = (a[23:21] == 3'b000) || (a[23:20] == 4'hf);
endfunction

// ---------------------------------------------------------------- DUT side
reg   [3:0] consume = 0;
reg         redirect = 0;
reg  [31:0] redirect_pc = 0;
reg         wide = 0;
wire        if_req, if_ack;
wire [23:0] if_addr;
wire [63:0] if_data;
wire        pf_req, pf_ack;
wire [31:0] pf_addr, pf_rdata;
wire [63:0] window;
wire  [5:0] avail;
wire [31:0] head_pc;
wire        dwr_valid;
wire [31:0] dwr_addr, mdr_in;
wire  [1:0] dwr_size;
wire        mdr_in_valid, data_busy;

`ifdef BYTEQ
v2_fetch_byteq fetch (
`else
v2_fetch fetch (
`endif
    .clk(clk), .rst(rst), .fast_ifetch(wide), .consume(consume),
    .redirect(redirect), .redirect_pc(redirect_pc),
    .dwr_valid(dwr_valid), .dwr_addr(dwr_addr),
`ifndef NO_DWR_SIZE                         // fetch RTL before the C1 SMC fix
    .dwr_size(dwr_size),
`endif
    .if_req(if_req), .if_addr(if_addr), .if_data(if_data), .if_ack(if_ack),
    .pf_req(pf_req), .pf_addr(pf_addr), .pf_rdata(pf_rdata), .pf_ack(pf_ack),
    .window(window), .avail(avail), .head_pc(head_pc)
);

// bench data accesses (the microcode's side of v2_bus)
reg   [1:0] d_op = 0, d_size = 2;
reg  [31:0] d_mar = 0, d_wdata = 0;

wire        c_req, c_we, c_ack;
wire [31:0] c_addr, c_wdata, c_rdata;
wire  [1:0] c_size;
v2_bus busu (
    .clk(clk), .rst(rst), .op(d_op), .size(d_size), .sx(1'b0),
    .mar(d_mar), .mdr_out(d_wdata), .mdr_in(mdr_in), .mdr_in_valid(mdr_in_valid),
    .data_busy(data_busy),
    .pf_req(pf_req), .pf_addr(pf_addr), .pf_rdata(pf_rdata), .pf_ack(pf_ack),
    .dwr_valid(dwr_valid), .dwr_addr(dwr_addr),
`ifndef NO_DWR_SIZE
    .dwr_size(dwr_size),
`endif
    .bus_req(c_req), .bus_we(c_we), .bus_addr(c_addr), .bus_size(c_size),
    .bus_wdata(c_wdata), .bus_rdata(c_rdata), .bus_ack(c_ack)
);

wire        m_req, m_we, m_ack_mem;
wire [23:1] m_addr;
wire [15:0] m_wdata, m_rdata;
wire  [1:0] m_be;
reg         m_stall = 0;                      // scen 6: hold the memory acknowledge back
wire        m_ack = m_ack_mem && !m_stall;    // m1_mem's acknowledge is a held level
s32_v60_bus adapter (
    .clk(clk), .ce(bce), .rst(rst),
    .c_req(c_req), .c_we(c_we), .c_addr(c_addr), .c_size(c_size),
    .c_wdata(c_wdata), .c_rdata(c_rdata), .c_ack(c_ack),
    .m_req(m_req), .m_we(m_we), .m_addr(m_addr), .m_wdata(m_wdata),
    .m_be(m_be), .m_rdata(m_rdata), .m_ack(m_ack)
);
m1_mem mem (.clk(clk), .m_req(m_req), .m_we(m_we), .m_addr(m_addr), .m_wdata(m_wdata),
            .m_be(m_be), .m_rdata(m_rdata), .m_ack(m_ack_mem));

function automatic [7:0] mbyte(input [31:0] a);
    mbyte = a[0] ? mem.ram[a[16:1]][15:8] : mem.ram[a[16:1]][7:0];
endfunction

// wide-port server model: like s32_core's, if_ack is held until if_req drops,
// and the returned line is shifted so byte 0 is the byte at if_addr; the line
// is served after a variable delay from the address seen at the start
reg  [63:0] w_data = 0;
reg         w_ack = 0, w_busy = 0, w_stall = 0;
reg  [23:0] w_addr = 0;
integer     w_cnt = 0;
reg  [15:0] w_lfsr = 16'h1D2B;
assign if_data = w_data;
assign if_ack  = w_ack;
integer b;
always @(posedge clk) begin
    if (rst) begin w_ack <= 0; w_busy <= 0; end
    else begin
        if (!if_req) w_ack <= 1'b0;
        if (if_req && !w_ack && !w_busy) begin
            w_busy <= 1'b1; w_addr <= if_addr; w_cnt <= 2 + (w_lfsr % 9);
            w_lfsr <= {w_lfsr[14:0], w_lfsr[15] ^ w_lfsr[13] ^ w_lfsr[12] ^ w_lfsr[10]};
        end else if (w_busy && !w_stall) begin
            if (w_cnt > 0) w_cnt <= w_cnt - 1;
            else begin
                for (b = 0; b < 8; b = b + 1)
                    w_data[b*8 +: 8] <= (b < 8 - w_addr[2:0]) ? mbyte({8'd0, w_addr} + b) : 8'h00;
                w_ack <= 1'b1; w_busy <= 1'b0;
            end
        end
    end
end

// ---------------------------------------------------------------- model
// head: the reset address or the redirect target, plus what the bench consumed
reg  [31:0] exp_pc = 32'hFFFF_FFF0;
integer     total_consumed = 0;
always @(posedge clk) begin
    if (rst)           exp_pc <= 32'hFFFF_FFF0;
    else if (redirect) exp_pc <= redirect_pc;
    else               exp_pc <= exp_pc + {28'd0, consume};
    if (!rst && !redirect) total_consumed <= total_consumed + consume;
end

// next fetch request a correct prefetcher issues: lines (v2_fetch) or byte
// runs (v2_fetch_byteq) from the flush point, in order, each exactly once
function automatic [31:0] seq_start(input [31:0] a);
`ifdef BYTEQ
    seq_start = a;
`else
    seq_start = {a[31:3], 3'b000};
`endif
endfunction
function automatic [31:0] seq_next(input [31:0] a, input w);
`ifdef BYTEQ
    seq_next = w ? a + (32'd8 - {29'd0, a[2:0]}) : a + (32'd4 - {30'd0, a[1:0]});
`else
    seq_next = w ? a + 32'd8 : a + 32'd4;        // narrow: two words per line
`endif
endfunction

// ---------------------------------------------------------------- checkers
integer errors = 0, n_proto = 0, n_data = 0, n_prog = 0, n_pc = 0, n_seq = 0, n_rd = 0;
reg        c_out = 0, i_out = 0;
reg [31:0] c_addr_q; reg [1:0] c_size_q; reg c_we_q;
reg [23:0] i_addr_q;
reg        c_ack_d = 0, i_ack_d = 0;
reg        wr_inflight = 0;               // bench data write under way: memory is ahead of the queue
reg        pf_req_q = 0, pf_ack_q = 0, if_req_q = 0, if_ack_q = 0, flush_q = 0, rst_q = 1;
reg [31:0] flush_to_q = 0;
reg [31:0] sb_next = 32'hFFFF_FFF0;
integer    n_req = 0, n_flush = 0, n_wide = 0;
integer    i;
wire       new_pf = pf_req && (!pf_req_q || pf_ack_q);
// a wide transaction is counted where the server accepts it (a request kept
// raised over a held acknowledge starts none; PROTOCOL flags that)
wire       new_if = if_req && !w_ack && !w_busy;
reg        c_both_q = 0, i_both_q = 0;
always @(posedge clk) begin
    if (rst) begin
        c_out = 0; i_out = 0;              // reset legitimately drops requests
    end else begin
        // data bus: held, same address, until acknowledged
        if (c_out) begin
            if (!c_req) begin
                n_proto = n_proto + 1; errors = errors + 1;
                if (n_proto <= 5) $display("PROTOCOL c_req withdrawn before ack (addr %08x)", c_addr_q);
                c_out = 0;
            end else if (c_addr !== c_addr_q || c_size !== c_size_q || c_we !== c_we_q) begin
                n_proto = n_proto + 1; errors = errors + 1;
                if (n_proto <= 5) $display("PROTOCOL c-request changed while outstanding: %08x -> %08x", c_addr_q, c_addr);
                c_addr_q = c_addr;
            end
            if (c_ack) c_out = 0;
        end else if (c_req && !c_ack) begin
            c_out = 1; c_addr_q = c_addr; c_size_q = c_size; c_we_q = c_we;
        end
        // wide port: same rule
        if (i_out) begin
            if (!if_req) begin
                n_proto = n_proto + 1; errors = errors + 1;
                if (n_proto <= 5) $display("PROTOCOL if_req withdrawn before ack (addr %06x)", i_addr_q);
                i_out = 0;
            end else if (if_addr !== i_addr_q) begin
                n_proto = n_proto + 1; errors = errors + 1;
                if (n_proto <= 5) $display("PROTOCOL if_addr changed while outstanding: %06x -> %06x", i_addr_q, if_addr);
                i_addr_q = if_addr;
            end
            if (if_ack) i_out = 0;
        end else if (if_req && !if_ack) begin
            i_out = 1; i_addr_q = if_addr;
        end
        // a request is dropped the cycle after its acknowledge: never held
        // over the adapter's or server's held acknowledge (it would be taken
        // twice, or keep the acknowledge alive)
        if (c_req && c_ack && c_both_q) begin
            n_proto = n_proto + 1; errors = errors + 1;
            if (n_proto <= 5) $display("PROTOCOL c_req held over its acknowledge (addr %08x)", c_addr);
        end
        if (if_req && if_ack && i_both_q) begin
            n_proto = n_proto + 1; errors = errors + 1;
            if (n_proto <= 5) $display("PROTOCOL if_req held over its acknowledge (addr %06x)", if_addr);
        end
        // head against the model
        if (head_pc !== exp_pc) begin
            n_pc = n_pc + 1; errors = errors + 1;
            if (n_pc <= 5) $display("PC head_pc=%08x expected %08x", head_pc, exp_pc);
        end
        // queued bytes equal memory at their modelled addresses
        if (!wr_inflight)
            for (i = 0; i < 8; i = i + 1)
                if (i < avail && window[i*8 +: 8] !== mbyte(exp_pc + i)) begin
                    n_data = n_data + 1; errors = errors + 1;
                    if (n_data <= 5) $display("DATA head=%08x +%0d: got %02x expected %02x (avail %0d)",
                                              exp_pc, i, window[i*8 +: 8], mbyte(exp_pc + i), avail);
                end
        // request sequence: a reset or flush (redirect or SMC hit, at the
        // previous edge) restarts it; each request issued since must be the next
        if (rst_q)        sb_next = seq_start(32'hFFFF_FFF0);
        else if (flush_q) sb_next = seq_start(flush_to_q);
        if (new_pf || new_if) begin
            n_req = n_req + 1;
            if (new_if) n_wide = n_wide + 1;
            if (new_if !== (wide && is_rom(sb_next)) ||
                (new_if ? (if_addr !== sb_next[23:0]) : (pf_addr !== sb_next))) begin
                n_seq = n_seq + 1; errors = errors + 1;
                if (n_seq <= 5) $display("SEQUENCE %s request at %08x, expected %s at %08x",
                                         new_if ? "wide" : "narrow", new_if ? {8'd0, if_addr} : pf_addr,
                                         (wide && is_rom(sb_next)) ? "wide" : "narrow", sb_next);
            end
            sb_next = seq_next(sb_next, wide && is_rom(sb_next));
        end
    end
    pf_req_q   <= pf_req; pf_ack_q <= pf_ack; if_req_q <= if_req; if_ack_q <= if_ack;
    c_both_q   <= !rst && c_req && c_ack; i_both_q <= !rst && if_req && if_ack;
    rst_q      <= rst;
    flush_q    <= !rst && (redirect || fetch.smc_hit);
    flush_to_q <= redirect ? redirect_pc : exp_pc;
    if (!rst && (redirect || fetch.smc_hit)) n_flush <= n_flush + 1;
end

// consumption: take 1..min(avail,8) bytes on most cycles (LFSR-driven); none
// while a data access runs (the microcode is stalled on data_busy)
reg [15:0] c_lfsr = 16'hB400;
reg        hold = 0;                   // pause consumption (lets the queue fill and the bus go idle)
always @(negedge clk) begin
    if (rst || avail == 0 || hold || data_busy) consume = 0;
    else begin
        c_lfsr = {c_lfsr[14:0], c_lfsr[15] ^ c_lfsr[13] ^ c_lfsr[12] ^ c_lfsr[10]};
        if (c_lfsr[1:0] == 2'd0)                      consume = 4'd0;
        else if ({3'd0, c_lfsr[4:2]} + 6'd1 <= avail) consume = {1'b0, c_lfsr[4:2]} + 4'd1;
        else                                           consume = avail[3:0];
    end
end

// ---------------------------------------------------------------- data accesses
// one access at a time, started from a negedge; returns after its acknowledge
integer n_dacc = 0;
reg     d_lock = 0;
function automatic [31:0] mread(input [31:0] a, input [1:0] sz);
    mread = {mbyte(a + 3), mbyte(a + 2), mbyte(a + 1), mbyte(a)} &
            ((sz == 2'd0) ? 32'h0000_00FF : (sz == 2'd1) ? 32'h0000_FFFF : 32'hFFFF_FFFF);
endfunction
task automatic data_access(input w, input [31:0] a, input [1:0] sz, input [31:0] wd);
    reg [31:0] expect_rd;
    reg        aborted;
    integer t;
    begin
        while (d_lock) @(negedge clk);
        d_lock = 1; aborted = 0;
        expect_rd = mread(a, sz);
        d_mar = a; d_size = sz; d_wdata = wd; wr_inflight = w;
        d_op = w ? 2'd2 : 2'd1;
        @(negedge clk); d_op = 2'd0;
        for (t = 0; t < 5000 && data_busy; t = t + 1) begin
            @(negedge clk);
            if (rst) aborted = 1;          // a reset cancels the access
        end
        if (!w && !aborted && (!mdr_in_valid || mdr_in !== expect_rd)) begin
            n_rd = n_rd + 1; errors = errors + 1;
            if (n_rd <= 5) $display("READ %08x size %0d: got %08x (valid %0d) expected %08x",
                                    a, sz, mdr_in, mdr_in_valid, expect_rd);
        end
        wr_inflight = 0;
        n_dacc = n_dacc + 1;
        d_lock = 0;
    end
endtask

// +dtraffic: random reads and writes in a scratch area no code address aliases
reg        dtraffic = 0, traffic_on = 0;
reg [15:0] t_lfsr = 16'h7E11;
always begin
    @(negedge clk);
    t_lfsr = {t_lfsr[14:0], t_lfsr[15] ^ t_lfsr[13] ^ t_lfsr[12] ^ t_lfsr[10]};
    if (dtraffic && traffic_on && !rst && !data_busy && !d_lock && t_lfsr[2:0] == 3'd0)
        data_access(t_lfsr[3], 32'h0001_E000 + {20'd0, t_lfsr[15:4]},
                    (t_lfsr[5:4] == 2'd3) ? 2'd2 : t_lfsr[5:4], {t_lfsr, ~t_lfsr});
end

// ---------------------------------------------------------------- scenarios
// phase seen at the event, data bus: 0 idle, 1 request raised not taken,
// 2 adapter busy (mid-transfer), 3 acknowledge cycle, 4 cycle after it;
// wide port: 0 idle, 1 raised not taken, 2 server busy, 3 acknowledge, 4 after
integer cov [0:4], cov_h1 [0:4], wcov [0:4];
integer cov_valid = 0, events = 0, storm_hits = 0, storm_events = 0, smc_hits = 0;
function integer phase_now();
    if (c_req && c_ack)                                 phase_now = 3;
    else if (c_ack_d)                                   phase_now = 4;
    else if (adapter.bst != 0)                          phase_now = 2;
    else if (c_req)                                     phase_now = 1;
    else                                                phase_now = 0;
endfunction
function integer wphase_now();
    if (if_req && if_ack)                               wphase_now = 3;
    else if (i_ack_d)                                   wphase_now = 4;
    else if (w_busy)                                    wphase_now = 2;
    else if (if_req)                                    wphase_now = 1;
    else                                                wphase_now = 0;
endfunction
always @(posedge clk) begin c_ack_d <= c_ack && c_req; i_ack_d <= if_ack && if_req; end

integer k, maxk, scen, only, settle, nstorm, j, t0;
task automatic do_reset(input integer cycles);
    begin
        @(negedge clk); rst = 1;
        repeat (cycles) @(negedge clk);
        rst = 0;
    end
endtask
task automatic wait_progress(input integer limit);
    wait_bytes(limit, 24);
endtask
task automatic wait_bytes(input integer limit, input integer nbytes);
    integer t, start;
    begin
        start = total_consumed;
        for (t = 0; t < limit && total_consumed - start < nbytes; t = t + 1) @(negedge clk);
        if (t >= limit) begin
            n_prog = n_prog + 1; errors = errors + 1;
            if (n_prog <= 5) $display("PROGRESS scen %0d k %0d: %0d bytes in %0d cycles (head %08x)",
                                      scen, k, total_consumed - start, limit, head_pc);
        end
        repeat (40) @(negedge clk);       // keep consuming and checking
    end
endtask
task automatic note_event();
    integer ph;
    begin
        ph = phase_now(); cov[ph] = cov[ph] + 1;
`ifndef BYTEQ
        if (fetch.half) cov_h1[ph] = cov_h1[ph] + 1;
`endif
        ph = wphase_now(); wcov[ph] = wcov[ph] + 1;
        events = events + 1;
        if (avail > 0) cov_valid = cov_valid + 1;
    end
endtask
// target for offset k: alternately ROM and non-ROM (the same m1_mem bytes)
function automatic [31:0] target(input integer kk, input integer sc);
    target = 32'h0000_1000 + kk * 37 + sc * 4096 + ((kk % 2) ? 32'h0020_0000 : 32'd0);
endfunction

reg [31:0] tgt, wa, base;
integer    g_n [0:5], g_f [0:5], g_skip [0:5];
integer    bi, ri, sz, rel, wlen, grp, e0, m, cnt;
integer    rels [0:18];
integer    pf_rels [0:4];
function automatic string gname(input integer g);
    case (g)
        0: gname = "range";    1: gname = "below";    2: gname = "half";
        3: gname = "wrap";     4: gname = "prefetch"; default: gname = "redirect";
    endcase
endfunction
string     cfg;
initial begin
    rels[0] = -4; rels[1] = -3; rels[2] = -2; rels[3] = -1; rels[4] = 0; rels[5] = 1;
    rels[6] = 2; rels[7] = 3; rels[8] = 6; rels[9] = 7; rels[10] = 8; rels[11] = 15;
    rels[12] = 16; rels[13] = 22; rels[14] = 23; rels[15] = 24; rels[16] = 31;
    rels[17] = 32; rels[18] = 33;
    pf_rels[0] = -1; pf_rels[1] = 0; pf_rels[2] = 2; pf_rels[3] = 5; pf_rels[4] = 9;
end
initial begin
    for (i = 0; i < 5; i = i + 1) begin cov[i] = 0; cov_h1[i] = 0; wcov[i] = 0; end
    if (!$value$plusargs("maxk=%d", maxk)) maxk = 90;
    if (!$value$plusargs("scen=%d", only)) only = -1;
    wide     = $test$plusargs("wide");
    dtraffic = $test$plusargs("dtraffic");
    for (i = 0; i < 65536; i = i + 1)
        mem.ram[i] = {pat({i[30:0], 1'b1}), pat({i[30:0], 1'b0})};

    if ($test$plusargs("smc")) begin
        for (i = 0; i < 6; i = i + 1) begin g_n[i] = 0; g_f[i] = 0; g_skip[i] = 0; end
        // range / below / wrap: a full queue (consumption held), then a write
        // at head + rel; the head is 8-aligned or not, away from or across the wrap
        for (bi = 0; bi < 4; bi = bi + 1) begin
            base = (bi == 0) ? 32'h0020_4000 : (bi == 1) ? 32'h0020_4805 :
                   (bi == 2) ? 32'hFFFF_FFF0 : 32'hFFFF_FFFB;
            for (ri = 0; ri < 19; ri = ri + 1) for (sz = 0; sz < 3; sz = sz + 1) begin
                rel = rels[ri];
                wlen = (sz == 0) ? 1 : (sz == 1) ? 2 : 4;
                grp = (bi >= 2) ? 3 : (rel < 0 && rel + wlen > 0) ? 1 : 0;
                e0 = errors;
                do_reset(4);
                redirect = 1; redirect_pc = base; @(negedge clk); redirect = 0;
                hold = 1;
                for (t0 = 0; t0 < 600 && avail < 24; t0 = t0 + 1) @(negedge clk);
                wa = exp_pc + rel;
                data_access(1'b1, wa, sz[1:0], ~mread(wa, 2'd2));
                repeat (4) @(negedge clk);
                hold = 0;
                wait_bytes(1500, 48);
                g_n[grp] = g_n[grp] + 1; if (errors > e0) g_f[grp] = g_f[grp] + 1;
            end
        end
        // half / wrap: from an idle bus, redirect to a line, hold consumption,
        // and write in the cycle the m-th first word (byteq: run) is captured
        for (bi = 0; bi < 2; bi = bi + 1) for (m = 1; m <= 3; m = m + 1)
        for (rel = -2; rel <= 7; rel = rel + 1) for (sz = 0; sz < 3; sz = sz + 1) begin
            base = (bi == 0) ? 32'h0020_6000 : 32'hFFFF_FFF8;
            grp = (bi == 0) ? 2 : 3;
            e0 = errors;
            do_reset(4);
            hold = 1;
            for (t0 = 0; t0 < 600 && !(avail >= 24 && !pf_req && !if_req); t0 = t0 + 1) @(negedge clk);
            redirect = 1; redirect_pc = base; @(negedge clk); redirect = 0;
            cnt = 0;
            for (t0 = 0; t0 < 600 && cnt < m; t0 = t0 + 1) begin
`ifdef BYTEQ
                if (pf_ack) cnt = cnt + 1;
`else
                if (pf_ack && !fetch.half) cnt = cnt + 1;
`endif
                if (cnt < m) @(negedge clk);
            end
            if (cnt < m) begin                       // wide fetch: no captured halves
                g_skip[grp] = g_skip[grp] + 1; hold = 0;
            end else begin
`ifdef BYTEQ
                wa = pf_addr + rel;
`else
                wa = fetch.fetch_line + rel;
`endif
                data_access(1'b1, wa, sz[1:0], ~mread(wa, 2'd2));
                repeat (4) @(negedge clk);
                hold = 0;
                wait_bytes(1500, 48);
                g_n[grp] = g_n[grp] + 1; if (errors > e0) g_f[grp] = g_f[grp] + 1;
            end
        end
        // prefetch: the write's issue cycle sweeps the prefetch phases
        for (k = 1; k <= 60; k = k + 1) begin
            grp = (k % 4 == 1) ? 5 : 4;
            e0 = errors;
            do_reset(4);
            tgt = ((k % 2) ? 32'h0020_8000 : 32'h0000_8000) + k * 24;
            redirect = 1; redirect_pc = tgt; @(negedge clk); redirect = 0;
            repeat (k) @(negedge clk);
            wa = exp_pc + pf_rels[k % 5];
            fork
                data_access(1'b1, wa, k % 3, ~mread(wa, 2'd2));
                if (k % 4 == 1) begin                // a redirect in the flush cycle
                    for (t0 = 0; t0 < 5000 && !dwr_valid; t0 = t0 + 1) @(negedge clk);
                    redirect = 1; redirect_pc = tgt + 32'h200; @(negedge clk); redirect = 0;
                end
            join
            wait_bytes(1500, 48);
            g_n[grp] = g_n[grp] + 1; if (errors > e0) g_f[grp] = g_f[grp] + 1;
        end
        // redirect: the old request goes stale, then a write 0..12 cycles later
        for (k = 1; k <= 60; k = k + 1) begin
            e0 = errors;
            do_reset(4);
            tgt = ((k % 2) ? 32'h0020_A000 : 32'h0000_A000) + k * 24;
            redirect = 1; redirect_pc = tgt; @(negedge clk); redirect = 0;
            repeat (30) @(negedge clk);
            for (t0 = 0; t0 < 200 && !(pf_req || if_req); t0 = t0 + 1) @(negedge clk);
            redirect = 1; redirect_pc = tgt + 32'h300 + (k % 8); @(negedge clk); redirect = 0;
            repeat (k % 13) @(negedge clk);
            wa = exp_pc + pf_rels[(k / 3) % 5];
            data_access(1'b1, wa, k % 3, ~mread(wa, 2'd2));
            wait_bytes(1500, 48);
            g_n[5] = g_n[5] + 1; if (errors > e0) g_f[5] = g_f[5] + 1;
        end
        for (i = 0; i < 6; i = i + 1)
            $display("SMC %-8s %3d cases, %3d failed, %3d skipped", gname(i), g_n[i], g_f[i], g_skip[i]);
        $display("errors: protocol=%0d pc=%0d data=%0d sequence=%0d read=%0d progress=%0d",
                 n_proto, n_pc, n_data, n_seq, n_rd, n_prog);
        cfg = wide ? "SMC suite, wide port" : "SMC suite, data-bus prefetch";
        if (errors == 0) $display("D1 PASS (%s)", cfg);
        else             $display("D1 FAIL: %0d errors", errors);
        $finish;
    end

    for (scen = 0; scen < 7; scen = scen + 1) begin
        for (k = 1; k <= maxk && (only < 0 || only == scen); k = k + 1) begin
            // reset into the reset address, then (scen 3) let the queue fill
            redirect = 0; traffic_on = 0;
            do_reset(4);
            traffic_on = (scen != 5);
            // scen 3: stop consuming so the queue fills (32 bytes) and the
            // prefetcher stops; the offset then lands in the idle bus or in the
            // last fills, always with valid bytes queued
            hold = (scen == 3);
            settle = (scen == 3) ? 150 : 0;
            repeat (settle) @(negedge clk);
            repeat (k) @(negedge clk);
            tgt = target(k, scen);
            case (scen)
                0, 3: begin                              // one redirect (3: queue holds bytes)
                    note_event(); hold = 0;
                    redirect = 1; redirect_pc = tgt; @(negedge clk); redirect = 0;
                    wait_progress(1500);
                end
                1: begin                                 // back-to-back redirects, 1..3 apart
                    note_event();
                    redirect = 1; redirect_pc = tgt; @(negedge clk); redirect = 0;
                    repeat (k % 3) @(negedge clk);
                    redirect = 1; redirect_pc = (tgt ^ 32'h0020_0000) + 32'h0000_0800 + 3;
                    @(negedge clk); redirect = 0;
                    wait_progress(1500);
                end
                2: begin                                 // redirect, then another after the stale ack
                    note_event();
                    redirect = 1; redirect_pc = tgt; @(negedge clk); redirect = 0;
                    wait_progress(1500);
                    redirect = 1; redirect_pc = tgt + 5; @(negedge clk); redirect = 0;
                    wait_progress(1500);
                end
                4: begin                                 // reset with a prefetch outstanding
                    note_event();
                    do_reset(2);
                    wait_progress(1500);
                end
                5: begin                                 // SMC flush of the queued head line
                    // (away from the top of the address space: see C1-2w)
                    redirect = 1; redirect_pc = tgt; @(negedge clk); redirect = 0;
                    repeat (k % 7) @(negedge clk);
                    for (t0 = 0; t0 < 400 && avail == 0; t0 = t0 + 1) @(negedge clk);
                    note_event();
                    wa = exp_pc;                       // from the head byte (C1-3: not below it)
                    if (avail > 0) smc_hits = smc_hits + 1;
                    fork
                        data_access(1'b1, wa, 2'd2, ~mread(wa, 2'd2));
                        if (k % 2) begin                 // a redirect in the flush cycle
                            for (t0 = 0; t0 < 5000 && !dwr_valid; t0 = t0 + 1) @(negedge clk);
                            redirect = 1; redirect_pc = tgt + 32'h100; @(negedge clk); redirect = 0;
                        end
                    join
                    wait_progress(1500);
                end
                6: begin                                 // flush storm on one outstanding request
                    m_stall = 1; w_stall = 1;
                    for (t0 = 0; t0 < 400 && !(pf_req || if_req); t0 = t0 + 1) @(negedge clk);
                    repeat (k % 4) @(negedge clk);
                    note_event();
                    storm_events = storm_events + 1;
                    if (pf_req || if_req) storm_hits = storm_hits + 1;
                    nstorm = 14 + (k % 5);
                    for (j = 0; j < nstorm; j = j + 1) begin
                        redirect = 1; redirect_pc = tgt + j * 16 + (k % 8); @(negedge clk);
                    end
                    redirect = 0;
                    repeat (k % 3) @(negedge clk);
                    m_stall = 0; w_stall = 0;
                    wait_progress(1500);
                end
            endcase
        end
    end
    traffic_on = 0;
    $display("phase coverage (data bus): idle=%0d request=%0d in-transfer=%0d ack-cycle=%0d after-ack=%0d; valid bytes queued at %0d of %0d events",
             cov[0], cov[1], cov[2], cov[3], cov[4], cov_valid, events);
`ifndef BYTEQ
    $display("  of which during a line's second half: idle=%0d request=%0d in-transfer=%0d ack-cycle=%0d after-ack=%0d",
             cov_h1[0], cov_h1[1], cov_h1[2], cov_h1[3], cov_h1[4]);
`endif
    $display("phase coverage (wide port): idle=%0d request=%0d server-busy=%0d ack-cycle=%0d after-ack=%0d",
             wcov[0], wcov[1], wcov[2], wcov[3], wcov[4]);
    $display("requests %0d (wide transfers %0d), flushes %0d, data accesses %0d, SMC writes into the queue %0d, storms with a request outstanding %0d of %0d",
             n_req, n_wide, n_flush, n_dacc, smc_hits, storm_hits, storm_events);
    $display("errors: protocol=%0d pc=%0d data=%0d sequence=%0d read=%0d progress=%0d",
             n_proto, n_pc, n_data, n_seq, n_rd, n_prog);
    cfg = wide ? "wide port" : "data-bus prefetch";
    if (dtraffic) cfg = {cfg, ", data traffic"};
    if (errors == 0) $display("D1 PASS (%s)", cfg);
    else             $display("D1 FAIL: %0d errors", errors);
    $finish;
end

endmodule
