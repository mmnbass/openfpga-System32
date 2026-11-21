// SPDX-License-Identifier: GPL-3.0-or-later
//
// wc_v25_stub: STUB for the V25 sound/protection MCU's 0xA00100-0xA0015E reply, in its own module.
// SIMULATION. It is NOT the V25: it has no CPU, no program, no state beyond a read counter.
//
// Provenance (K, measured): MAME 0.289 ga2u boot from reset, fresh NVRAM, the roadmap study's tagged
// access trace (run3/trace.gz, 'PROT R' lines). The boot reads the 0xA00xxx window exactly 48 times,
// all byte reads at the even addresses 0xA00100, 0xA00102 ... 0xA0015E (mask FF, bus data 0xFFxx),
// once each, at instructions 754,395-754,630, just before interrupts are enabled. The values spell
// "wake up! GOLDEN AXE The Revenge of Death-Adder! " (the V25's handshake text). Table regenerated
// by extract_v25_stub.py (v25_stub_seq.txt).
//
// Behaviour: a read of an address in the table returns {8'hff, table[(addr-0x100)/2]} (the same bus
// shape as upstream's real-V25 path, which presents {8'hff, v25_q}); any other read returns 0xFFFF
// and is flagged UNEXPECTED. Every access is logged ("V25 R addr data n=k", "V25 X addr ..." for
// unexpected ones, "V25 W ..." for writes, which the boot never makes).
//
// NOT integrated into s32_core: upstream's V25 sits inside s32_core's CPU read mux (unmodified here),
// and the stub is only needed at instruction ~754,395, far beyond what V2 reaches. Integration would
// need a derived copy of s32_core (the F2 patch_upstream.py pattern), not done.
module wc_v25_stub (
    input             clk,
    input             sel,          // CPU access in 0xA00000-0xA00FFF
    input             we,
    input      [11:1] addr,         // word address within the window
    output     [15:0] rdata
);
reg [7:0] tbl [0:47];
initial begin
    {tbl[0],  tbl[1],  tbl[2],  tbl[3],  tbl[4],  tbl[5],  tbl[6],  tbl[7],
      tbl[8],  tbl[9],  tbl[10], tbl[11], tbl[12], tbl[13], tbl[14], tbl[15],
      tbl[16], tbl[17], tbl[18], tbl[19], tbl[20], tbl[21], tbl[22], tbl[23],
      tbl[24], tbl[25], tbl[26], tbl[27], tbl[28], tbl[29], tbl[30], tbl[31],
      tbl[32], tbl[33], tbl[34], tbl[35], tbl[36], tbl[37], tbl[38], tbl[39],
      tbl[40], tbl[41], tbl[42], tbl[43], tbl[44], tbl[45], tbl[46], tbl[47]} = {
        8'h77, 8'h61, 8'h6B, 8'h65, 8'h20, 8'h75, 8'h70, 8'h21,
        8'h20, 8'h47, 8'h4F, 8'h4C, 8'h44, 8'h45, 8'h4E, 8'h20,
        8'h41, 8'h58, 8'h45, 8'h20, 8'h54, 8'h68, 8'h65, 8'h20,
        8'h52, 8'h65, 8'h76, 8'h65, 8'h6E, 8'h67, 8'h65, 8'h20,
        8'h6F, 8'h66, 8'h20, 8'h44, 8'h65, 8'h61, 8'h74, 8'h68,
        8'h2D, 8'h41, 8'h64, 8'h64, 8'h65, 8'h72, 8'h21, 8'h20};
end
wire [11:0] a   = {addr, 1'b0};
wire        hit = (a >= 12'h100) && (a <= 12'h15E);
wire  [5:0] idx = 6'((a - 12'h100) >> 1);
assign rdata = hit ? {8'hff, tbl[idx]} : 16'hffff;
integer n = 0;
reg     sel_q = 1'b0;
always @(posedge clk) begin
    sel_q <= sel;
    if (sel && !sel_q) begin     // one log line per access (sel held across the bus cycle)
        if (we)       $display("V25 W %06x", 24'ha00000 | a);
        else if (hit) begin $display("V25 R %06x %04x n=%0d", 24'ha00000 | a, rdata, n); n <= n + 1; end
        else          $display("V25 X %06x", 24'ha00000 | a);
    end
end
endmodule
