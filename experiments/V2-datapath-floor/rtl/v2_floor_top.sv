// SPDX-License-Identifier: GPL-3.0-or-later
//
// V2 measurement top. Every v2_core port except the clock is a top-level port
// that the generated QSF marks VIRTUAL_PIN, so inputs are unknown to synthesis
// and outputs are observed (the E1 method). The floor figure of record is the
// `v2_core:core` entity row, not the chip total.

module v2_floor_top #(
    parameter CS_DEPTH   = 2048,
    parameter CS_ROM     = 1'b0,
    parameter SHIFT_ITER = 1'b0,
    parameter UNIT_STYLE = 0
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
    input             nmi_n,
    input             cs_we,
    input      [11:0] cs_waddr,
    input      [99:0] cs_wdata,
    input             tbl_we,
    input             tbl_sel,
    input       [8:0] tbl_addr,
    input      [23:0] tbl_wdata
);

v2_core #(.CS_DEPTH(CS_DEPTH), .CS_ROM(CS_ROM), .SHIFT_ITER(SHIFT_ITER), .UNIT_STYLE(UNIT_STYLE)) core (
    .clk(clk), .ce(ce), .rst(rst), .fast_ifetch(fast_ifetch),
    .if_req(if_req), .if_addr(if_addr), .if_data(if_data), .if_ack(if_ack),
    .bus_req(bus_req), .bus_we(bus_we), .bus_addr(bus_addr), .bus_size(bus_size),
    .bus_wdata(bus_wdata), .bus_rdata(bus_rdata), .bus_ack(bus_ack),
    .irq_n(irq_n), .irq_vector(irq_vector), .irq_ack(irq_ack), .nmi_n(nmi_n),
    .cs_we(cs_we), .cs_waddr(cs_waddr), .cs_wdata(cs_wdata),
    .tbl_we(tbl_we), .tbl_sel(tbl_sel), .tbl_addr(tbl_addr), .tbl_wdata(tbl_wdata)
);

endmodule
