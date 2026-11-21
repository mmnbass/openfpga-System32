// SPDX-License-Identifier: GPL-3.0-or-later
//
// d2_v60_hw: a module NAMED s32_v60 (the port list and parameters of upstream s32_v60, of
// experiments/F2-fullchip/rtl/f2_v60_shim.sv and experiments/WC-sim/rtl/wc_v2_shim.sv) that wraps UNMODIFIED V2
// (v2_core) for the synthesizable D2 Pocket top. Exploratory.
//
// Differences from the WC-sim/S2 wrapper (wc_v2_shim.sv, simulation only):
//   * CS_ROM = 1, as the HW-001 core that ran on the Pocket (experiments/HW-cpu-test/rtl/hwt_soc.sv): the control store
//     and the decode tables are ROMs initialized from v2_cstore.hex / v2_dispatch.hex / v2_mode.hex (v2_useq.sv,
//     v2_decode in v2_useq.sv, $readmemh at elaboration), which must be the production --reset-native images
//     (m1_cstore.hex / m1_dispatch.hex / m1_mode.hex of m1_m11_microcode.py, copied under the v2_* names). The load
//     ports are tied off, so there is no 3000-cycle loader and no plusarg.
//   * no monitors: the observer/checks binds, the stop conditions and the logs live in the testbench-only files
//     (experiments/D2-pocket-top/tb/d2_v2_binds.sv, tb/d2_v2_monitor.sv).
// As in wc_v2_shim: V2 steps on every clk_sys edge (ce = 1, the `ce` input is unused; V2's bus adapter inside s32_core
// runs on the board enable), fast_ifetch passed through, START_PC etc. accepted for port compatibility (V2's fetch queue
// has its own reset address 0xFFFFFFF0 = upstream START_PC).
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

v2_core #(.CS_DEPTH(2048), .CS_ROM(1'b1), .SHIFT_ITER(1'b0), .UNIT_STYLE(0)) core (
    .clk(clk), .ce(1'b1), .rst(rst), .fast_ifetch(fast_ifetch),
    .if_req(if_req), .if_addr(if_addr), .if_data(if_data), .if_ack(if_ack),
    .bus_req(bus_req), .bus_we(bus_we), .bus_addr(bus_addr), .bus_size(bus_size),
    .bus_wdata(bus_wdata), .bus_rdata(bus_rdata), .bus_ack(bus_ack),
    .irq_n(irq_n), .irq_vector(irq_vector), .irq_ack(irq_ack), .nmi_n(nmi_n),
    .cs_we(1'b0), .cs_waddr(12'd0), .cs_wdata({`CW_W{1'b0}}),
    .tbl_we(1'b0), .tbl_sel(1'b0), .tbl_addr(9'd0), .tbl_wdata(24'd0)
);

endmodule
