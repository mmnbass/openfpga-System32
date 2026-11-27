// SPDX-License-Identifier: GPL-3.0-or-later
//
// u1_wram_pr: the work RAM's protection port ("port B" of upstream's on-chip work RAM) on the Pocket, where the work
// RAM is the external asynchronous SRAM with a single port (d2_sram_port). This module sits between the core's
// wram_ext_* signals and that port and time-shares it between the CPU and one protection client.
//
// Protection client (upstream responder interface, rtl/prot/s32_prot.sv): pr_req level with pr_we / pr_addr / pr_wdata /
// pr_be, held until pr_ack. Here pr_ack is a ONE-CYCLE pulse per completed access (upstream's dual-port RAM gives a
// level one clock after the request); pr_q is valid from that pulse. After the pulse one clock is skipped so the client
// can present its next request. A client that holds its request until it sees pr_ack (s32_prot_sonic does) works
// unchanged; a client that relies on the level acknowledge of upstream is NOT supported by this module.
//
// Arbitration: the CPU owns the port. A protection access starts only after QUIET consecutive clocks without a CPU
// work-RAM request (we / rd low), which is longer than the port needs to finish a CPU access (write: 5 clocks from the
// request edge; read: 1 clock after rd falls), so the port is idle when it starts. While it runs (busy), CPU requests
// are hidden from the port and cpu_rdy is held low; the core holds its acknowledge while busy (reads through cpu_rdy,
// writes through busy), so the CPU access simply starts when the protection access is over. With pr_req never raised
// the module is transparent: x_* = cpu_*, cpu_rdy = x_rdy, busy = 0.
`default_nettype none
module u1_wram_pr #(
    parameter [2:0] QUIET  = 3'd6,    // CPU-idle clocks before a protection access may start
    parameter [2:0] WR_LEN = 3'd6     // clocks from the write pulse until the port is idle again (d2_sram_port: 5)
) (
    input  wire        clk,
    input  wire        rst,
    // CPU side (the core's wram_ext_* outputs)
    input  wire [15:0] cpu_addr, cpu_wdata,
    input  wire  [1:0] cpu_be,
    input  wire        cpu_we, cpu_rd,
    output wire        cpu_rdy,
    // protection client
    input  wire        pr_req, pr_we,
    input  wire [15:0] pr_addr, pr_wdata,
    input  wire  [1:0] pr_be,
    output reg  [15:0] pr_q = 16'hffff,
    output reg         pr_ack = 1'b0,
    output wire        busy,
    // external SRAM port (d2_sram_port)
    output wire [15:0] x_addr, x_wdata,
    output wire  [1:0] x_be,
    output wire        x_we, x_rd,
    input  wire [15:0] x_q,
    input  wire        x_rdy
);
    localparam [2:0] S_IDLE = 3'd0, S_WR = 3'd1, S_WWAIT = 3'd2, S_RD = 3'd3, S_RWAIT = 3'd4, S_ACK = 3'd5, S_GAP = 3'd6;
    reg  [2:0] st = S_IDLE;
    reg  [2:0] quiet = 3'd0, cnt = 3'd0;
    reg [15:0] a_r = 16'd0, d_r = 16'd0;
    reg  [1:0] b_r = 2'd0;
    wire       cpu_act = cpu_we || cpu_rd;
assign busy = (st == S_WR) || (st == S_WWAIT) || (st == S_RD) || (st == S_RWAIT);
always @(posedge clk) begin
    pr_ack <= 1'b0;
    if (cpu_act) quiet <= 3'd0; else if (quiet != 3'd7) quiet <= quiet + 3'd1;
    if (rst) st <= S_IDLE;
    else case (st)
        S_IDLE: if (pr_req && !cpu_act && quiet >= QUIET) begin
            a_r <= pr_addr; d_r <= pr_wdata; b_r <= pr_be;
            st <= pr_we ? S_WR : S_RD;
        end
        S_WR:    begin cnt <= WR_LEN; st <= S_WWAIT; end
        S_WWAIT: if (cnt == 3'd0) st <= S_ACK; else cnt <= cnt - 3'd1;
        S_RD:    if (x_rdy) begin pr_q <= x_q; cnt <= 3'd1; st <= S_RWAIT; end
        S_RWAIT: if (cnt == 3'd0) st <= S_ACK; else cnt <= cnt - 3'd1;
        S_ACK:   begin pr_ack <= 1'b1; st <= S_GAP; end
        S_GAP:   st <= S_IDLE;                 // pr_ack is high in this clock; the client changes its request at its end
        default: st <= S_IDLE;
    endcase
end
`ifdef SIMULATION
// simulation report: every protection access (word address, data) and the clocks CPU requests were held
integer n_pr = 0, n_held = 0;
always @(posedge clk) begin
    if (busy && cpu_act) n_held <= n_held + 1;
    if (st == S_ACK) begin
        n_pr <= n_pr + 1;
        if (n_pr < 400) $display("WPR %s word=%04x data=%04x n=%0d", (x_we_seen ? "W" : "R"), a_r, (x_we_seen ? d_r : pr_q), n_pr + 1);
    end
end
reg x_we_seen = 1'b0;
always @(posedge clk) if (st == S_WR) x_we_seen <= 1'b1; else if (st == S_RD) x_we_seen <= 1'b0;
final $display("WPRSUM accesses=%0d cpu_held_clocks=%0d", n_pr, n_held);
`endif
    wire own = (st != S_IDLE) && (st != S_ACK) && (st != S_GAP);   // = busy
assign x_addr  = own ? a_r : cpu_addr;
assign x_wdata = own ? d_r : cpu_wdata;
assign x_be    = own ? b_r : cpu_be;
assign x_we    = own ? (st == S_WR) : cpu_we;
assign x_rd    = own ? (st == S_RD) : cpu_rd;
assign cpu_rdy = x_rdy && !own;
endmodule
`default_nettype wire
