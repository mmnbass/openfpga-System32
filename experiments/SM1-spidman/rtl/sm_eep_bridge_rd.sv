// SPDX-License-Identifier: GPL-3.0-or-later
//
// SM1.2: APF bridge read-back of the 93C46 EEPROM (the Spider-Man Save slot, bridge 0x5000_0000, 128 bytes).
// Replaces data_unloader for this slot. data_unloader assembles a 32-bit bridge word from two 16-bit memory reads and
// requests the second response from its clock-crossing FIFO two clk_74a cycles after the first, without checking that the
// FIFO is non-empty; with the memory side on clk_sys (48.3 MHz, about 8 clk_sys per response) the second read underflows
// (Codex SM1 review round 1, finding 1; shown in simulation, docs/reviews/SM1-codex-review.md).
//
// Here one bridge read is one request/acknowledge handshake (toggles through 3-flop synchronisers). The clk_sys side reads
// both words of the pair from the EEPROM's independent read port, waiting RD_LAT clocks for each, and holds them in
// `pair_q` until the next request. The clk_74a side takes `pair_q` only after the acknowledge toggle has crossed, so the
// 32-bit value is stable when sampled (a multi-bit crossing justified by the handshake, as d2_core_top's slot sizes).
// Byte order: file bytes [w(2k)[7:0], w(2k)[15:8], w(2k+1)[7:0], w(2k+1)[15:8]] (MAME's little-endian nvram words), placed
// big-endian on the bridge (bits 31:24 = first file byte) because the core reports bridge_endian_little = 0.
// Latency from the bridge_rd edge to valid bridge_rd_data: about 3 clk_sys + 2 * (RD_LAT + 1) + 1 clk_sys + 3 clk_74a, i.e.
// about 20 clk_74a at the D2 clocks; the maximum over clock phases is measured by tb/tb_sm_eep_bridge_rd.sv.
// Host contract: APF's bridge reads are serialised (one outstanding). A read arriving while one is in flight is queued one
// deep (a newer queued read replaces an older one) and served after it; every response is one whole pair, never a mixture
// (tb/tb_sm_eep_bridge_rd.sv).
`default_nettype none
module sm_eep_bridge_rd #(
    parameter [3:0]   ADDRESS_MASK_UPPER_4 = 4'h5,
    parameter integer RD_LAT               = 3       // clk_sys from eep_rd_addr to a settled eep_rd_data (s32_eeprom_ram port B: 1-2)
) (
    input  wire        clk_74a,
    input  wire        clk_sys,
    input  wire        bridge_rd,
    input  wire [31:0] bridge_addr,
    output reg  [31:0] bridge_rd_data = 32'd0,
    output reg   [5:0] eep_rd_addr = 6'd0,
    input  wire [15:0] eep_rd_data
);
// ---- clk_74a: request on the rising edge of a bridge read in this slot's window. One request is outstanding at a time;
// pair_74 never changes while one is (so the clk_sys side's sample of it is stable), and a read arriving meanwhile is
// queued (one deep) and issued when the acknowledge has crossed.
    reg        rd_q = 1'b0, req_t = 1'b0;
    reg  [4:0] pair_74 = 5'd0;           // word-pair index = byte address [6:2] of the outstanding request
    reg        pend = 1'b0;
    reg  [4:0] pend_pair = 5'd0;
    reg  [2:0] ack_s = 3'b000;
    wire       ack_t;
    reg [31:0] pair_q = 32'd0;           // clk_sys, stable while ack_s settles
    wire       busy74 = (req_t != ack_s[2]);
    wire       new_rd = bridge_rd && !rd_q && bridge_addr[31:28] == ADDRESS_MASK_UPPER_4;
always @(posedge clk_74a) begin
    rd_q  <= bridge_rd;
    ack_s <= {ack_s[1:0], ack_t};
    if (ack_s[2] != ack_s[1])
        bridge_rd_data <= {pair_q[7:0], pair_q[15:8], pair_q[23:16], pair_q[31:24]};
    if (new_rd && (busy74 || pend)) begin pend <= 1'b1; pend_pair <= bridge_addr[6:2]; end   // newest queued read wins
    else if (new_rd) begin pair_74 <= bridge_addr[6:2]; req_t <= ~req_t; end
    else if (pend && !busy74) begin pair_74 <= pend_pair; req_t <= ~req_t; pend <= 1'b0; end
end

// ---- clk_sys: read word 2k, then 2k+1, then acknowledge
    reg  [2:0] req_s = 3'b000;
    reg        ack_r = 1'b0;
    reg        req_done = 1'b0;          // the request toggle value last serviced (a request arriving mid-read is not lost)
    reg  [1:0] st = 2'd0;                // 0 idle, 1 low word, 2 high word
    reg  [3:0] wait_c = 4'd0;
    reg  [4:0] pair_act = 5'd0;          // the pair being read, latched once (Codex SM1 r2 #1: never re-read the live pair_74)
assign ack_t = ack_r;
always @(posedge clk_sys) begin
    req_s <= {req_s[1:0], req_t};
    case (st)
    2'd0: if (req_s[2] != req_done) begin req_done <= req_s[2]; pair_act <= pair_74; eep_rd_addr <= {pair_74, 1'b0}; wait_c <= 4'(RD_LAT); st <= 2'd1; end
    2'd1: if (wait_c != 0) wait_c <= wait_c - 1'd1;
          else begin pair_q[15:0] <= eep_rd_data; eep_rd_addr <= {pair_act, 1'b1}; wait_c <= 4'(RD_LAT); st <= 2'd2; end
    2'd2: if (wait_c != 0) wait_c <= wait_c - 1'd1;
          else begin pair_q[31:16] <= eep_rd_data; ack_r <= ~ack_r; st <= 2'd0; end
    default: st <= 2'd0;
    endcase
end
endmodule
`default_nettype wire
