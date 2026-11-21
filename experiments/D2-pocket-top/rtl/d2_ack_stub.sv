// SPDX-License-Identifier: GPL-3.0-or-later
//
// d2_ack_stub: synthesizable stand-in for the s32_core client ports the first-pixels core does not serve from memory
// (sdram p2/p3/p4/p5, wave-RAM write, framebuffer erase/read). D2 Pocket top, exploratory.
//
// It is the RTL form of the WC-sim / S2 C++ stub (tb_p1.cpp ack_model), cycle for cycle in clk_ram: a request seen high
// for the first time (and not already served) is acknowledged LAT clk_ram later for 2 clk_ram, data = DATA (zeros; the
// framebuffer read returns FFFF = transparent). A request held high is served once; it must fall before the next one,
// as upstream's request contract (sdram.sv header). The C++ stub sets ack right after the edge that showed the request;
// a register can only do that one edge later, so the counter is loaded with LAT-1 and `ack` is ack_r gated by the
// current request (the C++ stub drops ack on the edge after the request falls). All s32_core requests on these ports are
// registered (s32_core.sv fb_rd_req_r, s32_sprite fb_er_req, sdr_p5_req_r, ...), so the gate makes no combinational loop.
// n_req counts accepted requests (observation; S2 saw 0 on p2-p5/wave and ~27k on the framebuffer ports).
module d2_ack_stub #(
    parameter int LAT = 10
) (
    input              clk,          // clk_ram
    input              rst,
    input              req,
    output             ack,
    output reg  [15:0] n_req = 16'd0
);
reg       ack_r = 1'b0, served = 1'b0, busy = 1'b0;
reg [4:0] cnt = 5'd0;
reg [1:0] hold = 2'd0;
assign ack = ack_r & req;
always @(posedge clk) begin
    if (rst || !req) begin
        ack_r <= 1'b0; served <= 1'b0; busy <= 1'b0; hold <= 2'd0;
    end else if (ack_r) begin
        if (hold == 2'd1) begin ack_r <= 1'b0; served <= 1'b1; end
        hold <= hold - 2'd1;
    end else if (busy) begin
        if (cnt == 5'd0) begin busy <= 1'b0; ack_r <= 1'b1; hold <= 2'd2; end
        else cnt <= cnt - 5'd1;
    end else if (!served) begin
        busy <= 1'b1; cnt <= 5'(LAT - 2); n_req <= n_req + 16'd1;
    end
end
endmodule
