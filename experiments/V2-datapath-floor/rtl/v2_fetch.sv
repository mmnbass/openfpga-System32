// SPDX-License-Identifier: GPL-3.0-or-later
//
// v2_fetch: V60 instruction queue, aligner and prefetcher (UNIT_STYLE=0).
//
//   * 32 bytes held as four aligned 8-byte lines (slot = line address[4:3]),
//     so a fill writes one whole slot instead of placing bytes one by one
//   * fills from the wide ROM fetch port (requests are line-aligned, so the
//     line s32_core returns is already in slot order) or, for code outside
//     ROM, from two 32-bit prefetches over the data bus per line
//   * one aligner: the two lines covering the head are selected and an 8-byte
//     window is taken at the head's byte offset; microcode consumes 0-8 bytes
//     per step and operands are taken from the head one at a time
//   * redirect (branch/jump/exception) and a self-modifying-code snoop: a data
//     write overlapping any queued byte flushes and refetches; a stale flag
//     drops the fill of a request issued before a flush
//   * bus contract (upstream s32_v60_bus): a request is taken by the adapter on
//     a board enable with no acknowledgment of acceptance, completes one to
//     three 16-bit cycles later, and is acknowledged only while it is still
//     held. So a raised request is never withdrawn or re-addressed: a flush
//     leaves it outstanding, marked stale, its response is consumed and
//     discarded, and fetching resumes at the new address only after that
//     (defect D1, found by experiments/M1-functional, fixed 2026-10-23)
//
// Same capability as v2_fetch_byteq (UNIT_STYLE=1), built the standard way.

module v2_fetch (
    input             clk,
    input             rst,
    input             fast_ifetch,
    input       [3:0] consume,
    input             redirect,
    input      [31:0] redirect_pc,
    input             dwr_valid,
    input      [31:0] dwr_addr,
    input       [1:0] dwr_size,
    output reg        if_req,
    output     [23:0] if_addr,
    input      [63:0] if_data,
    input             if_ack,
    output reg        pf_req,
    output reg [31:0] pf_addr,
    input      [31:0] pf_rdata,
    input             pf_ack,
    output     [63:0] window,
    output      [5:0] avail,
    output reg [31:0] head_pc
);

reg  [63:0] line [0:3];
reg  [2:0]  nl;                 // valid lines starting at the head's line
reg  [31:0] fetch_line;         // 8-aligned address of the next line to fetch
reg         pending, pending_wide, half;
reg         stale;              // the outstanding request predates a flush (a
                                // flag, not a counter, so any number of flushes
                                // during one request cannot make it look current)

reg  [23:0] if_addr_q;          // address of the outstanding wide request
wire [31:0] head_line = {head_pc[31:3], 3'b000};
wire        is_rom    = (fetch_line[23:21] == 3'b000) || (fetch_line[23:20] == 4'hf);

// bytes from the head to the end of the valid lines; zero while no line is
// held (after a redirect to an unaligned address nl = 0 but the head offset is
// not, and the plain difference underflowed: defect D1b, fixed 2026-10-23)
assign avail   = (nl == 3'd0) ? 6'd0 : {nl, 3'b000} - {3'b000, head_pc[2:0]};
assign if_addr = if_addr_q;     // held with if_req; never follows a redirect

// aligner: two consecutive lines, then a byte shift by the head offset
wire [1:0]   s0 = head_pc[4:3];
wire [127:0] pair = {line[s0 + 2'd1], line[s0]};
wire [127:0] shifted = pair >> {head_pc[2:0], 3'b000};
assign window = shifted[63:0];

// self-modifying-code snoop. Queued bytes: the valid lines [head_line,
// fetch_line), i.e. nl*8 bytes, plus the first half of the line being filled
// once it is captured. A write of 1, 2 or 4 bytes flushes if its byte range
// overlaps them. Offsets are modular, so the queue may span 0xFFFFFFFF -> 0,
// and a write may start below the queued range. (Review findings C1-2,
// C1-2w, C1-3, fixed 2026-10-24: the old test, head_line <= addr < fetch_line,
// missed the captured half, was empty across the wrap, and ignored the size.)
wire [5:0]  q_len   = {nl, 3'b000} + ((pending && half && !stale) ? 6'd4 : 6'd0);
wire [2:0]  w_len   = (dwr_size == 2'd0) ? 3'd1 : (dwr_size == 2'd1) ? 3'd2 : 3'd4;
wire [31:0] w_off   = dwr_addr - head_line;     // write start, from the queue start
wire [31:0] q_off   = head_line - dwr_addr;     // queue start, from the write start
wire        smc_hit = dwr_valid && (q_len != 6'd0) &&
                      ((w_off < {26'd0, q_len}) || (q_off < {29'd0, w_len}));

wire        cur      = !stale;
wire        fill_w   = if_ack && pending && pending_wide && cur;
wire        fill_n   = pf_ack && pending && !pending_wide && cur;
wire        line_done = fill_w || (fill_n && half);
wire        crosses  = ({1'b0, head_pc[2:0]} + consume) >= 4'd8;

always @(posedge clk) begin
    if (rst) begin
        nl <= 3'd0; stale <= 1'b0; pending <= 1'b0; half <= 1'b0;
        if_req <= 1'b0; pf_req <= 1'b0;
        head_pc <= 32'hFFFF_FFF0; fetch_line <= 32'hFFFF_FFF0;
    end else if (redirect || smc_hit) begin
        nl <= 3'd0;
        // An outstanding request stays raised, with its address, marked stale:
        // its response is consumed and dropped by the stale path below. If it
        // completes in this very cycle it is retired here instead.
        if (pending && (if_ack || pf_ack)) begin
            pending <= 1'b0; half <= 1'b0; if_req <= 1'b0; pf_req <= 1'b0;
            stale <= 1'b0;
        end else begin
            stale <= pending;
        end
        head_pc    <= redirect ? redirect_pc : head_pc;
        fetch_line <= redirect ? {redirect_pc[31:3], 3'b000} : head_line;
    end else begin
        // fills
        if (fill_w) line[fetch_line[4:3]] <= if_data;
        if (fill_n) begin
            if (half) line[fetch_line[4:3]][63:32] <= pf_rdata;
            else      line[fetch_line[4:3]][31:0]  <= pf_rdata;
        end
        if ((if_ack || pf_ack) && pending) begin
            if_req <= 1'b0; pf_req <= 1'b0;
            if (!cur) begin pending <= 1'b0; half <= 1'b0; stale <= 1'b0; end
            else if (fill_n && !half) begin
                half <= 1'b1;                      // second word of the line
                pf_req <= 1'b1; pf_addr <= fetch_line + 32'd4;
            end else begin
                pending <= 1'b0; half <= 1'b0;
                fetch_line <= fetch_line + 32'd8;
            end
        end
        // consume at the head; a line is released when the head leaves it
        head_pc <= head_pc + {28'd0, consume};
        nl <= nl + {2'd0, line_done} - {2'd0, crosses};
        // request the next line while a slot is free
        if (!pending && (nl + {2'd0, line_done} < 3'd4)) begin
            pending      <= 1'b1;
            pending_wide <= fast_ifetch && is_rom;
            if (fast_ifetch && is_rom) begin if_req <= 1'b1; if_addr_q <= fetch_line[23:0]; end
            else begin pf_req <= 1'b1; pf_addr <= fetch_line; end
        end
    end
end

endmodule
