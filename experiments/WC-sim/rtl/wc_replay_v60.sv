// SPDX-License-Identifier: GPL-3.0-or-later
//
// wc_replay_v60: a module NAMED s32_v60 (same name, parameters and ports as
// upstream rtl/cpu/v60/s32_v60.sv and as the F2 shim), so the UNMODIFIED
// upstream s32_core builds with a CPU-bus replay driver where the CPU would be.
// SIMULATION ONLY. It is not a CPU: it executes nothing.
//
// The Verilator testbench drives one logical bus access at a time through the
// public cmd_* registers (a stand-in for MAME's V60 bus writes). This module
// presents it on the CPU-side interface of upstream's s32_v60_bus with the
// same four-phase handshake the real CPU uses: raise bus_req with address, size
// and data, wait for bus_ack, drop bus_req, wait for bus_ack to fall. Everything
// downstream of that (s32_v60_bus's 16-bit cycle splitting and byte lanes, the
// address decode, VRAM, palette, mixer registers, sprite control, 315-5296 I/O)
// is the real upstream RTL.
//
// A later step replaces this module with V2 (the F2 shim pattern): the port
// list is the contract.

module s32_v60 #(
    parameter [31:0] START_PC     = 32'hFFFFFFF0,
    parameter        IS_V70       = 1'b0,
    parameter        FAST_IFETCH  = 1'b0,
    parameter        SEQ_DISPATCH = 1'b0,
    parameter        EXEC_RETIRE  = 1'b0
) (
    input             clk,
    input             ce,
    input             rst,
    input             fast_ifetch,
    output            if_req,
    output     [23:0] if_addr,
    input      [63:0] if_data,
    input             if_ack,
    output            bus_req,
    output            bus_we,
    output     [31:0] bus_addr,
    output      [1:0] bus_size,
    output     [31:0] bus_wdata,
    input      [31:0] bus_rdata,
    input             bus_ack,
    input             irq_n,
    input       [7:0] irq_vector,
    output            irq_ack,
    input             nmi_n
);

// command mailbox, owned by the C++ testbench
reg        cmd_req   /*verilator public_flat_rw*/ = 1'b0;
reg        cmd_we    /*verilator public_flat_rw*/ = 1'b0;
reg [31:0] cmd_addr  /*verilator public_flat_rw*/ = 32'd0;
reg  [1:0] cmd_size  /*verilator public_flat_rw*/ = 2'd0;
reg [31:0] cmd_wdata /*verilator public_flat_rw*/ = 32'd0;
reg        cmd_done  /*verilator public_flat_rw*/ = 1'b0;
reg [31:0] cmd_rdata /*verilator public_flat_rw*/ = 32'd0;

assign if_req   = 1'b0;
assign if_addr  = 24'd0;
assign irq_ack  = 1'b0;

reg        q_req = 1'b0;
reg        q_we  = 1'b0;
reg [31:0] q_addr = 32'd0, q_wdata = 32'd0;
reg  [1:0] q_size = 2'd0;
assign bus_req   = q_req;
assign bus_we    = q_we;
assign bus_addr  = q_addr;
assign bus_size  = q_size;
assign bus_wdata = q_wdata;

localparam S_IDLE = 2'd0, S_WAITACK = 2'd1, S_WAITLOW = 2'd2, S_DONE = 2'd3;
reg [1:0] st = S_IDLE;

always @(posedge clk) begin
    if (rst) begin
        st <= S_IDLE; q_req <= 1'b0; cmd_done <= 1'b0;
    end
    else case (st)
        S_IDLE: if (cmd_req && !cmd_done) begin
            q_req <= 1'b1; q_we <= cmd_we; q_addr <= cmd_addr;
            q_size <= cmd_size; q_wdata <= cmd_wdata;
            st <= S_WAITACK;
        end
        S_WAITACK: if (bus_ack) begin
            q_req <= 1'b0;
            cmd_rdata <= bus_rdata;
            st <= S_WAITLOW;
        end
        S_WAITLOW: if (!bus_ack) begin
            cmd_done <= 1'b1;
            st <= S_DONE;
        end
        S_DONE: if (!cmd_req) begin
            cmd_done <= 1'b0;
            st <= S_IDLE;
        end
    endcase
end

endmodule
