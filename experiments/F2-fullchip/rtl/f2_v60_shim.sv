// SPDX-License-Identifier: GPL-3.0-or-later
//
// f2_v60_shim: a module NAMED s32_v60 that wraps V2, so upstream s32_core
// (which instantiates s32_v60 by name) builds with V2 in place of upstream's
// CPU. FIT HARNESS ONLY. Upstream's rtl/cpu/v60/s32_v60.sv (that one module
// alone) is left out of the F2 file list; nothing upstream is edited for this.
//
// Port list and parameters are upstream s32_v60's. The parameters are
// accepted and ignored: V2 has its own reset redirect and fetch-port choice
// (fast_ifetch is a port, passed through).
//
// Differences from how upstream runs its own CPU, all deliberate:
//   * ce is NOT used: V2 steps on every clk_sys edge (ce = 1), the provisional
//     48.317 MHz baseline every V2 fit used. Upstream's V60 runs on a
//     clk_sys/2 cadence under a two-cycle multicycle constraint; F2's SDC does
//     not apply that constraint to V2, so V2 is timed single-cycle, as in F1.
//   * the control store and decode tables stay WRITABLE (CS_ROM = 0), as in
//     the F1 floor, so synthesis cannot prune datapath against known
//     microcode. Their load ports are fed, while rst is high, from inputs this
//     module already has (if_data, bus_rdata, irq_vector): a stand-in for a
//     microcode loader, whose contents synthesis cannot know. It costs a
//     12-bit counter. A shipped core would use CS_ROM = 1 with real microcode
//     (smaller, never larger, for the same RTL).
//   * irq_ack is driven by V2 (upstream leaves it unconnected in s32_core).

`include "v2_pkg.svh"

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

// loader stand-in: one word per cycle while rst is high
reg [11:0] ld_addr = 12'd0;
always @(posedge clk) ld_addr <= rst ? ld_addr + 12'd1 : 12'd0;
wire [`CW_W-1:0] ld_word = {irq_vector[3:0], bus_rdata, if_data};

v2_core #(.CS_DEPTH(2048), .CS_ROM(1'b0), .SHIFT_ITER(1'b0), .UNIT_STYLE(0)) core (
    .clk(clk), .ce(1'b1), .rst(rst), .fast_ifetch(fast_ifetch),
    .if_req(if_req), .if_addr(if_addr), .if_data(if_data), .if_ack(if_ack),
    .bus_req(bus_req), .bus_we(bus_we), .bus_addr(bus_addr), .bus_size(bus_size),
    .bus_wdata(bus_wdata), .bus_rdata(bus_rdata), .bus_ack(bus_ack),
    .irq_n(irq_n), .irq_vector(irq_vector), .irq_ack(irq_ack), .nmi_n(nmi_n),
    .cs_we(rst && !ld_addr[11]), .cs_waddr(ld_addr), .cs_wdata(ld_word),
    .tbl_we(rst && ld_addr[11]), .tbl_sel(ld_addr[9]), .tbl_addr(ld_addr[8:0]),
    .tbl_wdata(if_data[23:0])
);

endmodule
