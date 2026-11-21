// SPDX-License-Identifier: GPL-3.0-or-later
//
// tb_m1_v2: runs a program image on v2_core behind the same s32_v60_bus
// adapter and m1_mem model as tb_m1_up, and prints the same canonical M1 dump
// (R00..R31, PSW, ordered data writes, END ...), so m1_compare.py can compare
// V2 with the references directly.
//
// Clocking: one clk_sys at 48.317 MHz. V2 steps on every cycle (its ce = 1,
// the provisional 48.3 MHz baseline). The adapter runs on the board enable
// (every 3rd cycle), as for the upstream CPU, so data and instruction-fetch
// bus cycles follow the same modelled PCB cadence. Instruction bytes come
// through the data bus (fast_ifetch = 0), as in the upstream bench.
//
// The microcode and decode tables (m1_microcode.py) are loaded through
// v2_core's existing write ports while reset is held. The simulation-only
// observer (v2_sim_observer, attached with bind below) defines instruction
// retirement and the cycle counters; see its header.
//
// Plusargs: +img=<image .hex>  +mc=<dir with m1_cstore/m1_dispatch/m1_mode.hex>
//           +haltpc=<n> +trappc=<n> [+trapmodepc=<n> +trapimmpc=<n>] (microcode
//           addresses from m1_symbols.txt: unsupported opcode / addressing mode /
//           immediate destination)  +maxcyc=<n>  +retire
//           +lockstep  (one record per retired instruction; see v2_sim_observer)
//           +wide      instruction fetch through the wide port (fast_ifetch = 1)
//                      from a model of s32_core's server (if_ack held until
//                      if_req drops; the line returned shifted by if_addr[2:0];
//                      2-10 cycles, varying); otherwise over the data bus
//           +ackmax=<b> instruction fetches in [b, b + 256) are never
//                      acknowledged (truncated-instruction tests): data-bus
//                      prefetch reads there, and wide lines reaching it. A window,
//                      not "everything above": the reset-address prefetch
//                      (0xFFFFFFF0) must still complete
//           +ackdelay=<n> (R2a, with +ackmax) the withheld fetches complete with the real bytes after n
//                      cycles of being withheld, without a redirect (see the declaration)
//           +privev    (R2a) print every privileged-register write "P n id= be= val=" and dump the file ("PR")
//           +trapprivpc=<n> +trapregidpc=<n>  (R2a) microcode addresses of the privileged-instruction /
//                      invalid-register-ID traps (kinds `priv`, `regid`)
//           +forceel=<0..3> (R2a, TEST-ONLY) force PSW[25:24] after reset, to exercise the privilege check
// Reported: "REDIRECTS reset=r instr=i instr_outstanding=m": the reset redirect,
// redirects by instructions, and how many of those found a fetch request
// (prefetch or wide) still outstanding.

`timescale 1ps/1ps
`include "v2_pkg.svh"

module tb_m1_v2;

reg clk = 0, rst = 1;
always #10349 clk = ~clk;                 // 20.697 ns: 48.317 MHz

reg [1:0] ce_div = 0;
wire      bce = (ce_div == 2'd2);         // board enable for the bus adapter
always @(posedge clk) ce_div <= (ce_div == 2'd2) ? 2'd0 : ce_div + 2'd1;

wire        c_req, c_we, c_ack;
wire [31:0] c_addr, c_wdata, c_rdata;
wire  [1:0] c_size;
wire        m_req, m_we, m_ack, m_ack_mem;
integer     ackmax = -1;
wire [23:1] m_addr;
wire [15:0] m_wdata, m_rdata, m_rdata_mem;
wire  [1:0] m_be;

reg         cs_we = 0;
reg  [11:0] cs_waddr = 0;
reg [`CW_W-1:0] cs_wdata = 0;
reg         tbl_we = 0, tbl_sel = 0;
reg   [8:0] tbl_addr = 0;
reg  [23:0] tbl_wdata = 0;

reg         wide = 1'b0;
wire        if_req, if_ack;
wire [23:0] if_addr;
wire [63:0] if_data;
v2_core #(.CS_DEPTH(2048), .CS_ROM(1'b0), .SHIFT_ITER(1'b0), .UNIT_STYLE(0)) dut (
    .clk(clk), .ce(1'b1), .rst(rst), .fast_ifetch(wide),
    .if_req(if_req), .if_addr(if_addr), .if_data(if_data), .if_ack(if_ack),
    .bus_req(c_req), .bus_we(c_we), .bus_addr(c_addr), .bus_size(c_size),
    .bus_wdata(c_wdata), .bus_rdata(c_rdata), .bus_ack(c_ack),
    .irq_n(1'b1), .irq_vector(8'd0), .irq_ack(), .nmi_n(1'b1),
    .cs_we(cs_we), .cs_waddr(cs_waddr), .cs_wdata(cs_wdata),
    .tbl_we(tbl_we), .tbl_sel(tbl_sel), .tbl_addr(tbl_addr), .tbl_wdata(tbl_wdata)
);
s32_v60_bus adapter (
    .clk(clk), .ce(bce), .rst(rst),
    .c_req(c_req), .c_we(c_we), .c_addr(c_addr), .c_size(c_size),
    .c_wdata(c_wdata), .c_rdata(c_rdata), .c_ack(c_ack),
    .m_req(m_req), .m_we(m_we), .m_addr(m_addr), .m_wdata(m_wdata),
    .m_be(m_be), .m_rdata(m_rdata), .m_ack(m_ack)
);
m1_mem mem (.clk(clk), .m_req(m_req), .m_we(m_we), .m_addr(m_addr), .m_wdata(m_wdata),
            .m_be(m_be), .m_rdata(m_rdata_mem), .m_ack(m_ack_mem));
wire [31:0] m_byte = {8'd0, m_addr, 1'b0};
// R1a: +ackrel=<pc> (read by the observer): the withheld fetches (+ackmax) are held until the instruction starting at
// <pc> issues a REDIRECT, then complete returning POISON (0xFF bytes: no valid instruction
// stream; V2's stale-response path must drop them). Without +ackrel (and for any other
// redirect) they stay withheld forever. Default (no +ackmax): both are inert.
wire        ack_released = dut.obs.rel_hit;   // set by the observer (+ackrel), see v2_sim_observer
wire        in_win = ackmax >= 0 && m_req && !m_we && m_byte >= ackmax && m_byte < ackmax + 256;
// R2a: +ackdelay=<n>: the withheld fetches are NOT held forever: after n cycles in which a withheld request was
// pending (counted from the first one) they complete with the REAL bytes, no redirect needed (LDPR does not
// redirect; plan 9 #5). Default (-1): inert.
integer     ackdelay = -1, win_wait = 0;
wire        hold_now = ackmax >= 0 && !ack_released &&
                       (in_win || (if_req && {8'd0, if_addr[23:3], 3'b111} >= ackmax && {8'd0, if_addr} < ackmax + 256));
always @(posedge clk) if (!rst && hold_now) win_wait <= win_wait + 1;
wire        delay_rel = ackdelay >= 0 && win_wait >= ackdelay;
assign m_ack = m_ack_mem && !(in_win && !ack_released && !delay_rel);
assign m_rdata = (in_win && ack_released) ? 16'hFFFF : m_rdata_mem;

bind v2_core v2_sim_observer obs (
    .clk(clk), .rst(rst), .ce(ce), .adv(adv), .data_busy(data_busy), .sh_busy(sh_busy),
    .fetch_short(fetch_short), .cw(cw), .upc(upc), .head_pc(head_pc), .window(window),
    .ucond(ucond),
    .gpr_we(regs.gpr_we), .gpr_waddr(regs.w_addr), .gpr_wbe(regs.w_be), .gpr_wdata(regs.w_data),
    .ipc(ipc),
    .psw(psw), .psw_upd(adv && (cw3[`CW_FLAG_UPD] != 4'd0 || cw3[`CW_WR_DST] == 3'd4)),
    .dop_ack(busu.bus_ack && busu.owner == 2'd1), .dop_we(busu.d_we),      // R1a counters (+counters)
    .priv_we(adv && cw3[`CW_WR_DST] == 3'd3),
    .priv_id(wr_addr3), .priv_be(wr_be3), .priv_data(res_q),                  // R2a privileged events (+privev)
    .redir_e3(adv && cw3[`CW_REDIRECT])
);
bind v2_core v2_sim_checks chk (
    .clk(clk), .rst(rst), .adv(adv), .cw(cw), .avail(avail), .head_pc(head_pc),
    .imm_load(imm_load), .upc(upc),
    .ra_addr(regs.ra_addr), .rb_addr(regs.rb_addr), .cw1(cw1), .cw2(cw2), .cw3(cw3),
    .wr_addr1(wr_addr1), .wr_addr2(wr_addr2), .wr_addr3(wr_addr3)
);

// wide-port server model (as tb_d1_fetch's)
reg  [63:0] w_data = 0;
reg         w_ack = 0, w_busy = 0;
reg  [23:0] w_addr = 0;
integer     w_cnt = 0, wb;
reg  [15:0] w_lfsr = 16'h1D2B;
reg  [16:0] w_ba;
assign if_data = w_data;
assign if_ack  = w_ack;
always @(posedge clk) begin
    if (rst) begin w_ack <= 0; w_busy <= 0; end
    else begin
        if (!if_req) w_ack <= 1'b0;
        if (if_req && !w_ack && !w_busy &&
            !(ackmax >= 0 && {8'd0, if_addr[23:3], 3'b111} >= ackmax && {8'd0, if_addr} < ackmax + 256 && !ack_released && !delay_rel)) begin
            w_busy <= 1'b1; w_addr <= if_addr; w_cnt <= 2 + (w_lfsr % 9);
            w_lfsr <= {w_lfsr[14:0], w_lfsr[15] ^ w_lfsr[13] ^ w_lfsr[12] ^ w_lfsr[10]};
        end else if (w_busy) begin
            if (w_cnt > 0) w_cnt <= w_cnt - 1;
            else begin
                for (wb = 0; wb < 8; wb = wb + 1) begin
                    w_ba = w_addr[16:0] + wb[16:0];
                    w_data[wb*8 +: 8] <= (wb < 8 - w_addr[2:0]) ?
                        ((ackmax >= 0 && ack_released && {7'd0, w_ba} >= ackmax && {7'd0, w_ba} < ackmax + 256) ? 8'hFF :   // poison (R1a)
                         (w_ba[0] ? mem.ram[w_ba[16:1]][15:8] : mem.ram[w_ba[16:1]][7:0])) : 8'h00;
                end
                w_ack <= 1'b1; w_busy <= 1'b0;
            end
        end
    end
end

// fetch redirects, and those taken with a fetch request outstanding
integer n_redir_reset = 0, n_redir = 0, n_redir_out = 0;
always @(posedge clk) if (!rst && dut.adv && dut.cw3[`CW_REDIRECT]) begin
    if (dut.obs.started == 0) n_redir_reset = n_redir_reset + 1;   // before the first instruction
    else begin
        n_redir = n_redir + 1;
        if (dut.pf_req || if_req) n_redir_out = n_redir_out + 1;
    end
end

// data writes at the logical side of the adapter (prefetch reads never write)
function [31:0] szmask(input [1:0] s);
    szmask = (s == 2'd0) ? 32'h0000_00FF : (s == 2'd1) ? 32'h0000_FFFF : 32'hFFFF_FFFF;
endfunction
reg wr_seen;
always @(posedge clk) begin
    if (rst) wr_seen <= 1'b0;
    else begin
        if (c_req && c_we && c_ack && !wr_seen)
            $display("W addr=%08x size=%0d data=%08x", c_addr,
                     (c_size == 2'd0) ? 1 : (c_size == 2'd1) ? 2 : 4, c_wdata & szmask(c_size));
        wr_seen <= c_req && c_we && c_ack;
    end
end

reg [`CW_W-1:0] prog [0:2047];
reg      [23:0] dtab [0:255];
reg      [23:0] mtab [0:511];
string          img, mc;   // any path length (a fixed-width reg truncates long paths)
integer fd_chk;
task automatic must_exist(input string path);
    fd_chk = $fopen(path, "r");
    if (fd_chk == 0) begin $display("FATAL: cannot open %s", path); $finish; end
    $fclose(fd_chk);
endtask
integer i, maxcyc, trappc, trapmodepc, trapimmpc, trapprivpc, trapregidpc, forceel, cycles = 0;
reg trapped = 0;
string trapkind = "";

always @(posedge clk) if (!rst) cycles <= cycles + 1;

// SM1 (TB only): +uptrace prints "U <cycle> <upc> adv=<adv> stall=<data_busy>" every cycle, to count the microwords a form takes.
reg uptrace;
initial uptrace = $test$plusargs("uptrace");
always @(posedge clk) if (uptrace && !rst) $display("U %0d %0d adv=%0d busy=%0d", cycles, dut.upc, dut.adv, dut.data_busy);

initial begin
    if (!$value$plusargs("img=%s", img)) begin $display("no +img="); $finish; end
    if (!$value$plusargs("mc=%s", mc))  begin $display("no +mc=");  $finish; end
    if (!$value$plusargs("maxcyc=%d", maxcyc)) maxcyc = 2000000;
    if (!$value$plusargs("trappc=%d", trappc)) trappc = -1;
    wide = $test$plusargs("wide");
    if (!$value$plusargs("ackmax=%d", ackmax)) ackmax = -1;
    if (!$value$plusargs("ackdelay=%d", ackdelay)) ackdelay = -1;
    if (!$value$plusargs("trapmodepc=%d", trapmodepc)) trapmodepc = -1;
    if (!$value$plusargs("trapimmpc=%d", trapimmpc)) trapimmpc = -1;
    if (!$value$plusargs("trapprivpc=%d", trapprivpc)) trapprivpc = -1;     // R2a: privileged instruction at EL != 0
    if (!$value$plusargs("trapregidpc=%d", trapregidpc)) trapregidpc = -1;  // R2a: invalid privileged-register ID
    if (!$value$plusargs("forceel=%d", forceel)) forceel = -1;              // R2a TEST-ONLY: PSW.EL forced after reset
    for (i = 0; i < 2048; i = i + 1) prog[i] = '0;
    must_exist(img); $readmemh(img, mem.ram);
    must_exist({mc, "/m1_cstore.hex"}); $readmemh({mc, "/m1_cstore.hex"}, prog);
    must_exist({mc, "/m1_dispatch.hex"}); $readmemh({mc, "/m1_dispatch.hex"}, dtab);
    must_exist({mc, "/m1_mode.hex"}); $readmemh({mc, "/m1_mode.hex"}, mtab);
    repeat (2) @(posedge clk);
    for (i = 0; i < 2048; i = i + 1) begin
        @(negedge clk); cs_we = 1; cs_waddr = i[11:0]; cs_wdata = prog[i];
    end
    for (i = 0; i < 768; i = i + 1) begin      // dispatch table 256, mode table 512
        @(negedge clk); cs_we = 0; tbl_we = 1; tbl_sel = (i >= 256);
        tbl_addr = (i >= 256) ? 9'(i - 256) : 9'(i);
        tbl_wdata = (i >= 256) ? mtab[i - 256] : dtab[i];
    end
    @(negedge clk); tbl_we = 0;
    repeat (3) @(posedge clk);
    rst = 0;
    if (forceel >= 0) begin @(posedge clk); dut.pswu.rest[21:20] <= forceel[1:0]; end   // R2a TEST-ONLY: PSW[25:24] = EL (bench only)
    for (i = 0; i < maxcyc && !dut.obs.halted && !trapped; i = i + 1) begin
        @(posedge clk);
        if (trappc >= 0 && dut.upc == trappc[11:0])         begin trapped = 1; trapkind = "opcode";  end
        if (trapmodepc >= 0 && dut.upc == trapmodepc[11:0]) begin trapped = 1; trapkind = "mode";    end
        if (trapimmpc >= 0 && dut.upc == trapimmpc[11:0])   begin trapped = 1; trapkind = "immdest"; end
        if (trapprivpc >= 0 && dut.upc == trapprivpc[11:0]) begin trapped = 1; trapkind = "priv";    end
        if (trapregidpc >= 0 && dut.upc == trapregidpc[11:0]) begin trapped = 1; trapkind = "regid"; end
    end
    repeat (12) @(posedge clk);
    if ($value$plusargs("drain=%d", i) && i > 0) repeat (i) @(posedge clk);   // R1a: late side effects of a trapped / waiting instruction
    for (i = 0; i < 32; i = i + 1) $display("R%02d=%08x", i, dut.regs.gpr_a[i]);
    $display("PSW=%08x", dut.psw);
    if (dut.obs.privev) for (i = 0; i < 32; i = i + 1) $display("PR id=%0d val=%08x", i, dut.regs.priv[i]);
    dut.obs.dump_inflight();
    if (trapped) $display("TRAP %s ipc=%08x", trapkind, dut.ipc);
    $display("END halted=%0d instrs=%0d cycles=%0d", dut.obs.halted, dut.obs.retired, cycles);
    $display("COUNTERS pre=%0d exec=%0d mem=%0d fetch=%0d iter=%0d",
             dut.obs.t_pre, dut.obs.t_exec, dut.obs.t_mem, dut.obs.t_fetch, dut.obs.t_iter);
    $display("SIMCHECK errors=%0d ipc_errors=%0d", dut.chk.errors, dut.obs.ipc_errors);
    $display("REDIRECTS reset=%0d instr=%0d instr_outstanding=%0d (%s)", n_redir_reset, n_redir, n_redir_out,
             wide ? "wide port" : "data-bus prefetch");
    $finish;
end

endmodule
