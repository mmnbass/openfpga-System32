// SPDX-License-Identifier: GPL-3.0-or-later
//
// tb_v2_smoke: connectivity smoke test for the V2 skeleton.
//
// Loads a small microprogram (sim/microasm.py smoke) through the control-store
// write port, and the decode tables through the table port, while reset is held, then runs it against simple memory models:
//   * wide fetch port and bus reads from addresses below 0x100 or at/above
//     0xFFFFFF00 return a ROM pattern byte(a) = a[7:0] ^ 0xA5;
//   * other bus addresses are a 64 KB RAM; every access acks after 2 cycles.
// Every bus write is checked, in order, against smoke_expected.hex. This proves
// the units are wired and produce correct basic results at the ports. It does
// not test any V60 instruction.

`timescale 1ns/1ps
`include "v2_pkg.svh"

module tb_v2_smoke;

parameter UNIT_STYLE = 0;
parameter SHIFT_ITER = 0;
parameter CS_DEPTH   = 2048;

reg clk = 0, rst = 1;
always #5 clk = ~clk;

wire        if_req, bus_req, bus_we, irq_ack;
wire [23:0] if_addr;
wire [31:0] bus_addr, bus_wdata;
wire  [1:0] bus_size;
reg  [63:0] if_data;
reg         if_ack;
reg  [31:0] bus_rdata;
reg         bus_ack;
reg         cs_we;
reg  [11:0] cs_waddr;
reg  [`CW_W-1:0] cs_wdata;
reg         tbl_we, tbl_sel;
reg   [8:0] tbl_addr;
reg  [23:0] tbl_wdata;

v2_core #(.CS_DEPTH(CS_DEPTH), .CS_ROM(1'b0), .SHIFT_ITER(SHIFT_ITER), .UNIT_STYLE(UNIT_STYLE)) dut (
    .clk(clk), .ce(1'b1), .rst(rst), .fast_ifetch(1'b1),
    .if_req(if_req), .if_addr(if_addr), .if_data(if_data), .if_ack(if_ack),
    .bus_req(bus_req), .bus_we(bus_we), .bus_addr(bus_addr), .bus_size(bus_size),
    .bus_wdata(bus_wdata), .bus_rdata(bus_rdata), .bus_ack(bus_ack),
    .irq_n(1'b1), .irq_vector(8'd0), .irq_ack(irq_ack), .nmi_n(1'b1),
    .cs_we(cs_we), .cs_waddr(cs_waddr), .cs_wdata(cs_wdata),
    .tbl_we(tbl_we), .tbl_sel(tbl_sel), .tbl_addr(tbl_addr), .tbl_wdata(tbl_wdata)
);

function [7:0] rom_byte(input [31:0] a);
    rom_byte = a[7:0] ^ 8'hA5;
endfunction
function is_rom(input [31:0] a);
    is_rom = (a < 32'h100) || (a >= 32'hFFFF_FF00);
endfunction

reg [7:0]  ram [0:65535];
reg [`CW_W-1:0] prog [0:CS_DEPTH-1];
reg [23:0] dtab_init [0:255];
reg [23:0] mtab_init [0:511];
reg [31:0] expected [0:63];
integer    nexp, nwr, errors, i;

// wide fetch port: line returned with byte 0 = the requested byte
reg [1:0] if_cnt;
reg [23:0] if_a;
always @(posedge clk) begin
    if_ack <= 1'b0;
    if (rst) if_cnt <= 0;
    else if (if_req && if_cnt == 0 && !if_ack) begin if_cnt <= 2; if_a <= if_addr; end
    else if (if_cnt == 1) begin
        for (i = 0; i < 8; i = i + 1)
            if_data[i*8 +: 8] <= rom_byte({8'd0, if_a} + i);   // pattern uses a[7:0] only
        if_ack <= 1'b1; if_cnt <= 0;
    end else if (if_cnt != 0) if_cnt <= if_cnt - 1;
end

// data bus
reg [1:0] b_cnt;
always @(posedge clk) begin
    bus_ack <= 1'b0;
    if (rst) b_cnt <= 0;
    else if (bus_req && b_cnt == 0 && !bus_ack) b_cnt <= 2;
    else if (b_cnt == 1) begin
        b_cnt <= 0; bus_ack <= 1'b1;
        if (bus_we) begin
            for (i = 0; i < 4; i = i + 1) ram[(bus_addr[15:0] + i) & 16'hffff] <= bus_wdata[i*8 +: 8];
            if (nwr < nexp && bus_wdata !== expected[nwr]) begin
                $display("FAIL store %0d at %08x: got %08x expected %08x", nwr, bus_addr, bus_wdata, expected[nwr]);
                errors = errors + 1;
            end
            nwr = nwr + 1;
        end else begin
            for (i = 0; i < 4; i = i + 1)
                bus_rdata[i*8 +: 8] <= is_rom(bus_addr + i) ? rom_byte(bus_addr + i)
                                                             : ram[(bus_addr[15:0] + i) & 16'hffff];
        end
    end else if (b_cnt != 0) b_cnt <= b_cnt - 1;
end

initial begin
    errors = 0; nwr = 0; nexp = 0;
    cs_we = 0; cs_waddr = 0; cs_wdata = 0;
    tbl_we = 0; tbl_sel = 0; tbl_addr = 0; tbl_wdata = 0;
    for (i = 0; i < CS_DEPTH; i = i + 1) prog[i] = '0;
    $readmemh("smoke_cstore.hex", prog);
    $readmemh("smoke_dispatch.hex", dtab_init);
    $readmemh("smoke_mode.hex", mtab_init);
    $readmemh("smoke_count.hex", expected);     // first word: number of stores
    nexp = expected[0];
    $readmemh("smoke_expected.hex", expected);
    // load the control store while reset is held
    repeat (2) @(posedge clk);
    for (i = 0; i < CS_DEPTH; i = i + 1) begin
        @(negedge clk); cs_we = 1; cs_waddr = i[11:0]; cs_wdata = prog[i];
    end
    @(negedge clk); cs_we = 0;
    // load the opcode-dispatch and addressing-mode tables
    for (i = 0; i < 768; i = i + 1) begin      // dispatch table 256, mode table 512
        @(negedge clk); tbl_we = 1; tbl_sel = (i >= 256);
        tbl_addr = (i >= 256) ? 9'(i - 256) : 9'(i);
        tbl_wdata = (i >= 256) ? mtab_init[i - 256] : dtab_init[i];
    end
    @(negedge clk); tbl_we = 0;
    repeat (2) @(posedge clk);
    rst = 0;
    for (i = 0; i < 20000 && nwr < nexp; i = i + 1) @(posedge clk);
    repeat (10) @(posedge clk);
    if (nwr < nexp)       $display("V2 SMOKE FAIL: timeout after %0d/%0d stores", nwr, nexp);
    else if (errors != 0) $display("V2 SMOKE FAIL: %0d errors", errors);
    else $display("V2 SMOKE PASS (UNIT_STYLE=%0d SHIFT_ITER=%0d): %0d/%0d stores correct",
                  UNIT_STYLE, SHIFT_ITER, nwr, nexp);
    $finish;
end

// +trace: print every write-back
reg trace;
initial trace = $test$plusargs("trace");
always @(posedge clk) if (trace && !rst && dut.adv && dut.cw3[56:54] != 3'd0)
    $display("%0t wb dst=%0d gpr=%0d temp=%0d val=%08x  upc=%0d", $time, dut.cw3[56:54],
             dut.wr_addr3, dut.cw3[62:59], dut.res_q, dut.upc);

endmodule
