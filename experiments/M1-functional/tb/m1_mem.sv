// SPDX-License-Identifier: GPL-3.0-or-later
//
// m1_mem: 128 KB, 16-bit memory on the s32_v60_bus system side, shared by
// the upstream and V2 benches so both CPUs see an identical memory system.
//
// s32_v60_bus raises m_req on a board enable and samples m_ack only on board
// enables, holding m_req until it sees the acknowledge. So the acknowledge
// here is a level that follows m_req by one clk_sys cycle, and a write is
// performed once, on the first cycle of each request. The adapter drops
// m_req for at least one board enable between 16-bit cycles.
//
// Wait states (default none, as for all M1.0 results): +memwait=<n> and
// +memjit=<j> (0..j more, from a fixed LFSR, varying per request) give a wait
// w = n + jitter. The acknowledge follows m_req by 1 clk_sys cycle with no
// wait settings, and by w + 2 cycles otherwise (so +memjit alone with jitter 0
// still takes 2). Measured: memwait 1, 2, 5 -> 3, 4, 7 cycles; memjit 4 -> 2..6.
// The adapter samples the acknowledge only on board enables, which further
// quantizes what it sees.

module m1_mem (
    input             clk,
    input             m_req,
    input             m_we,
    input      [23:1] m_addr,
    input      [15:0] m_wdata,
    input       [1:0] m_be,
    output     [15:0] m_rdata,
    output reg        m_ack
);

reg [15:0] ram [0:65535];
reg        busy = 1'b0;
integer k, memwait, memjit, cnt;
reg [15:0] lfsr = 16'hACE1;
initial begin
    for (k = 0; k < 65536; k = k + 1) ram[k] = 16'h0000;
    m_ack = 1'b0;
    if (!$value$plusargs("memwait=%d", memwait)) memwait = 0;
    if (!$value$plusargs("memjit=%d", memjit)) memjit = 0;
    cnt = 0;
end

assign m_rdata = ram[m_addr[16:1]];

always @(posedge clk) begin
    if (!m_req) begin
        m_ack <= 1'b0;
    end else if (!busy) begin
        cnt   <= memwait + ((memjit > 0) ? (lfsr % (memjit + 1)) : 0);
        lfsr  <= {lfsr[14:0], lfsr[15] ^ lfsr[13] ^ lfsr[12] ^ lfsr[10]};
        m_ack <= (memwait == 0 && memjit == 0);
    end else if (cnt > 0) begin
        cnt <= cnt - 1;
    end else begin
        m_ack <= 1'b1;
    end
    if (m_req && !busy) begin
        busy <= 1'b1;
        if (m_we) begin
            if (m_be[0]) ram[m_addr[16:1]][7:0]  <= m_wdata[7:0];
            if (m_be[1]) ram[m_addr[16:1]][15:8] <= m_wdata[15:8];
        end
    end
    if (!m_req) busy <= 1'b0;
end

endmodule
