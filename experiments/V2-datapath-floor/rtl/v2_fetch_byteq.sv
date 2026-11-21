// SPDX-License-Identifier: GPL-3.0-or-later
//
// v2_fetch_byteq: (UNIT_STYLE=1 variant) byte-granular instruction queue.
//
//   * 32-byte circular queue (V60 instructions reach 20+ bytes; this keeps a
//     whole worst-case instruction plus lookahead resident)
//   * fill from the wide 8-byte ROM fetch port (same contract as upstream:
//     s32_core returns the containing line pre-aligned so byte 0 is the
//     frontier byte) or from 32-bit prefetches over the data bus for code
//     outside ROM; a request outstanding at a redirect is marked stale and
//     its return is dropped
//   * 8-byte aligner presenting the bytes at the queue head to decode and
//     operand extraction; microcode consumes 0-8 bytes per step
//   * redirect (branch/jump/exception) and self-modifying-code snoop: a data
//     write that falls inside the queued range flushes and refetches
//
// A single aligner is shared by every operand extraction: operands are taken
// from the head one at a time and consumed, instead of being read in place at
// many offsets as today's 24-byte window is.

module v2_fetch_byteq #(
    parameter QB = 32               // queue bytes (power of two)
) (
    input             clk,
    input             rst,
    input             fast_ifetch,
    // control
    input       [3:0] consume,          // 0..8 bytes this step
    input             redirect,
    input      [31:0] redirect_pc,
    // self-modifying-code snoop
    input             dwr_valid,
    input      [31:0] dwr_addr,
    input       [1:0] dwr_size,
    // wide ROM fetch port (to s32_core's icache)
    output reg        if_req,
    output     [23:0] if_addr,
    input      [63:0] if_data,
    input             if_ack,
    // prefetch over the data bus (arbitrated in v2_bus)
    output reg        pf_req,
    output reg [31:0] pf_addr,
    input      [31:0] pf_rdata,
    input             pf_ack,
    // to decode / operand staging
    output     [63:0] window,           // 8 bytes at head, byte 0 = window[7:0]
    output reg  [5:0] avail,            // valid bytes in queue
    output reg [31:0] head_pc
);

localparam int AW = $clog2(QB);

reg  [7:0]    q [0:QB-1];
reg  [AW-1:0] head;
reg  [31:0]   fetch_pc;                 // address of the next byte to fetch
reg           pending, pending_wide;
reg           stale;                    // outstanding request predates a flush (see v2_fetch)

wire [5:0] space   = QB[5:0] - avail;
wire       is_rom  = (fetch_pc[23:21] == 3'b000) || (fetch_pc[23:20] == 4'hf);
wire [3:0] wide_n  = 4'd8 - {1'b0, fetch_pc[2:0]};      // bytes in the returned line
wire [2:0] narrow_n = 3'd4 - {1'b0, fetch_pc[1:0]};     // bytes in a 32-bit prefetch

// ---- aligner: 8 bytes at the head ----
genvar g;
generate
    for (g = 0; g < 8; g = g + 1) begin : al
        wire [AW-1:0] idx = head + g[AW-1:0];
        assign window[g*8 +: 8] = q[idx];
    end
endgenerate

reg [23:0] if_addr_q;                   // address of the outstanding wide request
assign if_addr = if_addr_q;             // held with if_req; never follows a redirect

// ---- snoop: a data write of 1, 2 or 4 bytes overlapping [head_pc, head_pc + avail),
// in modular arithmetic; it may start below the head (C1-3, fixed 2026-10-24) ----
wire [2:0]  snoop_len  = (dwr_size == 2'd0) ? 3'd1 : (dwr_size == 2'd1) ? 3'd2 : 3'd4;
wire [31:0] snoop_off  = dwr_addr - head_pc;
wire [31:0] snoop_back = head_pc - dwr_addr;
wire        smc_hit    = dwr_valid && (avail != 6'd0) &&
                         ((snoop_off < {26'd0, avail}) || (snoop_back < {29'd0, snoop_len}));

// ---- fill path ----
wire        fill_wide   = if_ack && pending && pending_wide && !stale;
wire        fill_narrow = pf_ack && pending && !pending_wide && !stale;
wire [3:0]  fill_n      = fill_wide ? wide_n : (fill_narrow ? {1'b0, narrow_n} : 4'd0);
wire [AW-1:0] tail      = head + avail[AW-1:0];

integer i;
reg [AW-1:0] rel;
always @(posedge clk) begin
    if (rst) begin
        head <= '0; avail <= 6'd0; stale <= 1'b0; pending <= 1'b0;
        if_req <= 1'b0; pf_req <= 1'b0; head_pc <= 32'hFFFF_FFF0;
        fetch_pc <= 32'hFFFF_FFF0;
    end else if (redirect || smc_hit) begin
        head     <= '0;
        avail    <= 6'd0;
        head_pc  <= redirect ? redirect_pc : head_pc;
        fetch_pc <= redirect ? redirect_pc : head_pc;
        // D1: an outstanding request is never withdrawn (see v2_fetch); it
        // completes marked stale and is dropped, unless it completes now
        if (pending && (if_ack || pf_ack)) begin
            if_req <= 1'b0; pf_req <= 1'b0; pending <= 1'b0; stale <= 1'b0;
        end else begin
            stale <= pending;
        end
    end else begin
        // append returned bytes at the tail
        for (i = 0; i < QB; i = i + 1) begin
            rel = i[AW-1:0] - tail;          // distance from the tail, mod QB
            if (fill_wide && ({1'b0, rel} < {{(AW-3){1'b0}}, wide_n}))
                q[i] <= if_data[rel[2:0]*8 +: 8];
            else if (fill_narrow && ({1'b0, rel} < {{(AW-2){1'b0}}, narrow_n}))
                q[i] <= pf_rdata[rel[1:0]*8 +: 8];
        end
        if (fill_wide || fill_narrow) begin
            pending  <= 1'b0;
            if_req   <= 1'b0;
            pf_req   <= 1'b0;
            fetch_pc <= fetch_pc + {28'd0, fill_n};
        end else if ((if_ack || pf_ack) && pending) begin
            pending <= 1'b0;            // stale: drop
            stale   <= 1'b0;
            if_req  <= 1'b0;
            pf_req  <= 1'b0;
        end
        // consume at the head
        head    <= head + {{(AW-4){1'b0}}, consume};
        head_pc <= head_pc + {28'd0, consume};
        avail   <= avail + {2'd0, fill_n} - {2'd0, consume};
        // issue the next fetch when there is room for a full line
        if (!pending && space > 6'd8) begin
            pending      <= 1'b1;
            pending_wide <= fast_ifetch && is_rom;
            if (fast_ifetch && is_rom) begin if_req <= 1'b1; if_addr_q <= fetch_pc[23:0]; end
            else begin pf_req <= 1'b1; pf_addr <= fetch_pc; end
        end
    end
end

endmodule
