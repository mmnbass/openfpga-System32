// SPDX-License-Identifier: GPL-3.0-or-later
//
// tb_m1_up: runs a program image on the pinned upstream V60 RTL (s32_v60 +
// s32_v60_bus, read from references/s32, unmodified) and prints the canonical
// M1 dump (same format as m1_ref.py): R00..R31, PSW, every data write in
// order, then END halted=<0/1> instrs=<n> cycles=<clk> ce=<ce cycles>.
//
// Clocking matches the GA2 core: one clk_sys (48.317 MHz, period 20.697 ns)
// and the V60 / bus adapter on ce = every 3rd cycle (16.106 MHz), as
// tb_core_romboot does with CPUDIV=3. Instruction fetch goes through the data
// bus (FAST_IFETCH=0), as in upstream's own unit benches.
//
// Retired instructions: one per entry into S_DECODE (st_t value 3, the same
// constant tb_core_romboot uses), as upstream's +PCHIST counts them, PLUS one
// per S_DECODE -> S_DECODE loop-window restore. A taken DBcc/TB whose target
// is the retained window (s32_v60.sv, DBcc LOOP_RESTORE path) stays in
// S_DECODE for the next instruction, so the entry test alone misses it (S0
// finding at GA2 10052D; also in +PCHIST). That path, and only it among the
// S_DECODE -> S_DECODE transitions, increments pf_epoch; a pc test would miss
// a DBcc that branches to itself. Regression: run_dbcc_boundary.sh.
// HALT is decoded in S_DECODE, so it is counted. The harness checks this
// count against the Python reference's instruction count.
//
// Plusargs: +img=<16-bit $readmemh image>  +maxcyc=<n> (default 2,000,000)
//           +trace  print "T n pc=<pc> op=<opcode byte>" at each counted instruction

`timescale 1ps/1ps
module tb_m1_up;

localparam [6:0] S_DECODE_V = 7'd3;

reg clk = 0, rst = 1;
always #10349 clk = ~clk;                 // 20.697 ns period

reg [1:0] ce_div = 0;
wire      ce = (ce_div == 2'd2);
always @(posedge clk) ce_div <= (ce_div == 2'd2) ? 2'd0 : ce_div + 2'd1;

wire        c_req, c_we, c_ack;
wire [31:0] c_addr, c_wdata, c_rdata;
wire  [1:0] c_size;
wire        m_req, m_we, m_ack;
wire [23:1] m_addr;
wire [15:0] m_wdata, m_rdata;
wire  [1:0] m_be;

s32_v60 #(.START_PC(32'h0000_0000)) cpu (
    .clk(clk), .ce(ce), .rst(rst), .fast_ifetch(1'b0),
    .if_req(), .if_addr(), .if_data(64'd0), .if_ack(1'b0),
    .bus_req(c_req), .bus_we(c_we), .bus_addr(c_addr), .bus_size(c_size),
    .bus_wdata(c_wdata), .bus_rdata(c_rdata), .bus_ack(c_ack),
    .irq_n(1'b1), .irq_vector(8'h00), .irq_ack(), .nmi_n(1'b1)
);
s32_v60_bus adapter (
    .clk(clk), .ce(ce), .rst(rst),
    .c_req(c_req), .c_we(c_we), .c_addr(c_addr), .c_size(c_size),
    .c_wdata(c_wdata), .c_rdata(c_rdata), .c_ack(c_ack),
    .m_req(m_req), .m_we(m_we), .m_addr(m_addr), .m_wdata(m_wdata),
    .m_be(m_be), .m_rdata(m_rdata), .m_ack(m_ack)
);
m1_mem mem (.clk(clk), .m_req(m_req), .m_we(m_we), .m_addr(m_addr), .m_wdata(m_wdata),
            .m_be(m_be), .m_rdata(m_rdata), .m_ack(m_ack));

// data writes, in order, at the logical (CPU) side of the adapter
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
        wr_seen <= c_req && c_we && c_ack;     // one line per acknowledged write
    end
end

integer fd_chk;
task automatic must_exist(input string path);
    fd_chk = $fopen(path, "r");
    if (fd_chk == 0) begin $display("FATAL: cannot open %s", path); $finish; end
    $fclose(fd_chk);
endtask
integer instrs = 0, cycles = 0, ce_cycles = 0, maxcyc, i;
reg trace;
initial trace = $test$plusargs("trace");
reg [6:0] st_prev = 7'd0;
reg [3:0] epoch_prev = 4'd0;
// lock-step records (+lockstep): when instruction n starts (S_DECODE), the
// record of instruction n-1 is printed with every GPR it changed, then the PSW
// if it changed; the last one (HALT) is printed at the end.
// "L n pc=<pc> op=<opcode>[ Rnn=<v>...][ PSW=<v>]"
reg        lockstep;
initial    lockstep = $test$plusargs("lockstep");
// R1a event counters (+counters; test infrastructure, default off). With +counters
// (and lock-step records) every retired instruction also prints
//     C <n> dr=<a> dw=<b> priv=<c>
// counted over the instruction's window (from its S_DECODE entry to the next):
//   dr/dw  DATA-bus transactions the CPU saw acknowledged (the CPU's own `dack`
//          = bus_ack with the data port as bus owner, sampled at ce), by direction
//          (bus_we). Instruction prefetch (owner OWN_PF) is not a data access.
//   priv   cycles (at ce) in which any privileged register changed value
//          (sbr sycw tkcw pir psw2 isp l0sp-l3sp trr atbr/atlr0-3 trmode adtr0/1
//          adtmr0/1). APPROXIMATION: upstream has no write strobe, so a write of
//          an unchanged value is not seen; for a JMP (which must not write any)
//          a count of 0 is still meaningful, and LDPR of a new value is seen.
reg counters;
initial counters = $test$plusargs("counters");
integer n_dr = 0, n_dw = 0, n_pw = 0;
wire [767:0] privv = {cpu.sbr, cpu.sycw, cpu.tkcw, cpu.pir, cpu.psw2, cpu.isp, cpu.l0sp, cpu.l1sp,
    cpu.l2sp, cpu.l3sp, cpu.trr, cpu.atbr0, cpu.atlr0, cpu.atbr1, cpu.atlr1, cpu.atbr2, cpu.atlr2,
    cpu.atbr3, cpu.atlr3, cpu.trmode, cpu.adtr0, cpu.adtr1, cpu.adtmr0, cpu.adtmr1};
reg [767:0] priv_prev;
always @(posedge clk) priv_prev <= privv;
// R2a privileged-register EVENTS (+privev; test infrastructure, default off). Upstream has no write
// strobe, so an event is a VALUE CHANGE of one privileged register (ID, new value), charged to the
// instruction whose window it lands in (the same rule as `priv` above). A write of an unchanged value
// - a zero to a zero register, a repeated nonzero value - is INVISIBLE here (docs/r2-ldpr-plan.md 9 #3).
// With +privev: "P n id=<regID> val=<v>" before each retired instruction's L record, and at the end
// "PR id=<regID> val=<v>" for every privileged register (IDs as the manual: 0 ISP 1 L0SP 2 L1SP 3 L2SP
// 4 L3SP 5 SBR 6 TR 7 SYCW 8 TKCW 9 PIR 15 PSW2 16-23 ATBR0..ATLR3 24 TRMOD 25-28 ADTR0/1 ADTMR0/1).
reg privev;
initial privev = $test$plusargs("privev");
// privv packs the registers MSB-first in this order (see its concatenation above)
function automatic integer pv_id(input integer j);
    case (j)
        0: pv_id = 5;  1: pv_id = 7;  2: pv_id = 8;  3: pv_id = 9;  4: pv_id = 15; 5: pv_id = 0;
        6: pv_id = 1;  7: pv_id = 2;  8: pv_id = 3;  9: pv_id = 4;  10: pv_id = 6; 11: pv_id = 16;
        12: pv_id = 17; 13: pv_id = 18; 14: pv_id = 19; 15: pv_id = 20; 16: pv_id = 21; 17: pv_id = 22;
        18: pv_id = 23; 19: pv_id = 24; 20: pv_id = 25; 21: pv_id = 26; 22: pv_id = 27; default: pv_id = 28;
    endcase
endfunction
integer pev_id [0:31];
reg [31:0] pev_val [0:31];
integer pev_n = 0, pj;

reg [31:0] snap [0:31];
reg [31:0] snap_psw;
reg [31:0] ls_pc = 0;
reg  [7:0] ls_op = 0;
integer    k;
task automatic ls_record(input integer n);
    string s;
    begin
        s = $sformatf("L %0d pc=%08x op=%02x", n, ls_pc, ls_op);
        for (k = 0; k < 32; k = k + 1)
            if (cpu.r[k] !== snap[k]) s = {s, $sformatf(" R%02d=%08x", k, cpu.r[k])};
        if (cpu.psw !== snap_psw) s = {s, $sformatf(" PSW=%08x", cpu.psw)};
        for (k = 0; k < pev_n; k = k + 1) $display("P %0d id=%0d val=%08x", n, pev_id[k], pev_val[k]);
        pev_n = 0;
        $display("%s", s);
        if (counters) $display("C %0d dr=%0d dw=%0d priv=%0d", n, n_dr, n_dw, n_pw);
        n_dr = 0; n_dw = 0; n_pw = 0;
        for (k = 0; k < 32; k = k + 1) snap[k] = cpu.r[k];
        snap_psw = cpu.psw;
    end
endtask
always @(posedge clk) if (!rst) begin
    cycles <= cycles + 1;
    if (privev && privv !== priv_prev)
        for (pj = 0; pj < 24; pj = pj + 1)
            if (privv[767 - 32*pj -: 32] !== priv_prev[767 - 32*pj -: 32] && pev_n < 32) begin
                pev_id[pev_n] = pv_id(pj); pev_val[pev_n] = privv[767 - 32*pj -: 32]; pev_n = pev_n + 1;
            end
    if (privv !== priv_prev) n_pw = n_pw + 1;     // any clk: a write lands between ce cycles' samples
    if (ce) begin
        ce_cycles <= ce_cycles + 1;
        if (cpu.dack) begin if (c_we) n_dw = n_dw + 1; else n_dr = n_dr + 1; end
        if (cpu.st == S_DECODE_V && (st_prev != S_DECODE_V || cpu.pf_epoch != epoch_prev)) begin
            instrs <= instrs + 1;
            if (trace) $display("T %0d pc=%08x op=%02x", instrs, cpu.pc, cpu.opcode);
            if (lockstep) begin
                if (instrs > 0) ls_record(instrs - 1);
                else begin
                    n_dr = 0; n_dw = 0; n_pw = 0; pev_n = 0;
                    for (k = 0; k < 32; k = k + 1) snap[k] = cpu.r[k];
                    snap_psw = cpu.psw;
                end
                ls_pc = cpu.pc; ls_op = cpu.opcode;
            end
        end
        st_prev <= cpu.st;
        epoch_prev <= cpu.pf_epoch;
    end
end

string img;   // any path length (a fixed-width reg truncates long paths)
initial begin
    if (!$value$plusargs("img=%s", img)) begin $display("no +img="); $finish; end
    if (!$value$plusargs("maxcyc=%d", maxcyc)) maxcyc = 2000000;
    must_exist(img); $readmemh(img, mem.ram);
    repeat (12) @(posedge clk);
    rst = 0;
    for (i = 0; i < maxcyc && !cpu.halted; i = i + 1) @(posedge clk);
    repeat (12) @(posedge clk);                   // let a trailing write complete
    if (lockstep && instrs > 0) ls_record(instrs - 1);
    for (i = 0; i < 32; i = i + 1) $display("R%02d=%08x", i, cpu.r[i]);
    $display("PSW=%08x", cpu.psw);
    if (privev) for (pj = 0; pj < 24; pj = pj + 1) $display("PR id=%0d val=%08x", pv_id(pj), privv[767 - 32*pj -: 32]);
    $display("END halted=%0d instrs=%0d cycles=%0d ce=%0d", cpu.halted, instrs, cycles, ce_cycles);
    $finish;
end

endmodule
