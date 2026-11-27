// SPDX-License-Identifier: GPL-3.0-or-later
//
// TESTBENCH ONLY (D2 Pocket top): the observation, stop and report logic that experiments/WC-sim/rtl/wc_v2_shim.sv
// carries inside the simulation wrapper, moved out of the synthesizable s32_v60 (rtl/d2_v60_hw.sv) into this module,
// instantiated by tb/d2_tb.sv. It reads V2 through hierarchical references only and drives nothing. Same output lines
// and plusargs as wc_v2_shim where kept (p1_report.py parses them): IRQACK / VBLSTART / T1EXPIRE (+irqlog), the stop
// conditions (+trappc= +trapmodepc= +trapimmpc= from m1_symbols.txt, HALT, +stallcyc= / +maxinstcyc= watchdog,
// +maxinstr=, +maxcyc=), and the END / COUNTERS / BUSCOUNT / IRQ / SIMCHECK lines.
// The microcode check (UCLOAD) compares V2's ROM arrays (CS_ROM = 1, initialised from v2_*.hex) with the +mc= images.
`include "v2_pkg.svh"
`define V2 $root.d2_tb.ic.core.v60.core
`define CORE $root.d2_tb.ic.core
module d2_v2_monitor (
    input clk      // clk_sys
);
wire rst = `V2.rst;

// ---- ROM contents check at reset release (the images the run directory's v2_*.hex were copied from) ----
reg [`CW_W-1:0] prog [0:2047];
reg      [23:0] dtab [0:255];
reg      [23:0] mtab [0:511];
string mc;
integer ii;
initial begin
    for (ii = 0; ii < 2048; ii = ii + 1) prog[ii] = '0;
    if (!$value$plusargs("mc=%s", mc)) begin $display("FATAL: no +mc=<microcode dir>"); $finish; end
    $readmemh({mc, "/m1_cstore.hex"}, prog);
    $readmemh({mc, "/m1_dispatch.hex"}, dtab);
    $readmemh({mc, "/m1_mode.hex"}, mtab);
end
reg init_done = 1'b0;
integer ci, cm_cs, cm_d, cm_m;
always @(posedge clk) if (!rst && !init_done) begin
    init_done <= 1'b1;
    cm_cs = 0; cm_d = 0; cm_m = 0;
    for (ci = 0; ci < 2048; ci = ci + 1) if (`V2.useq.cstore[ci] !== prog[ci]) cm_cs = cm_cs + 1;
    for (ci = 0; ci < 256;  ci = ci + 1) if (`V2.dec.dtab[ci] !== dtab[ci]) cm_d = cm_d + 1;
    for (ci = 0; ci < 512;  ci = ci + 1) if (`V2.dec.mtab[ci] !== mtab[ci]) cm_m = cm_m + 1;
    $display("UCLOAD (CS_ROM=1 images vs +mc) control_store_words=2048 mismatches=%0d dispatch_words=256 mismatches=%0d mode_words=512 mismatches=%0d", cm_cs, cm_d, cm_m);
end

// ---- request counters ----
integer n_bus_req = 0, n_bus_ack = 0, n_if_req = 0, n_if_ack = 0;
reg bq = 0, bk = 0, iq = 0, ik = 0;
always @(posedge clk) if (!rst) begin
    bq <= `V2.bus_req; bk <= `V2.bus_ack; iq <= `V2.if_req; ik <= `V2.if_ack;
    if (`V2.bus_req && !bq) n_bus_req <= n_bus_req + 1;
    if (`V2.bus_ack && !bk) n_bus_ack <= n_bus_ack + 1;
    if (`V2.if_req && !iq) n_if_req <= n_if_req + 1;
    if (`V2.if_ack && !ik) n_if_ack <= n_if_ack + 1;
end

// ---- interrupt line observation ----
integer n_irq_assert = 0, n_irq_v40 = 0, n_irq_v44 = 0, n_irq_other = 0, n_irq_ack = 0, n_irq_low_cycles = 0, n_ack_v00 = 0, n_ack_v04 = 0;
reg irq_q = 1'b1, irqack_q = 1'b0;
always @(posedge clk) if (!rst) begin
    irq_q <= `V2.irq_n; irqack_q <= `V2.irq_ack;
    if (!`V2.irq_n) n_irq_low_cycles <= n_irq_low_cycles + 1;
    if (!`V2.irq_n && irq_q) begin
        n_irq_assert <= n_irq_assert + 1;
        if (`V2.irq_vector == 8'h40) n_irq_v40 <= n_irq_v40 + 1;
        else if (`V2.irq_vector == 8'h44) n_irq_v44 <= n_irq_v44 + 1;
        else n_irq_other <= n_irq_other + 1;
    end
    if (`V2.irq_ack && !irqack_q) begin
        n_irq_ack <= n_irq_ack + 1;
        if (`V2.irq_vector == 8'h00) n_ack_v00 <= n_ack_v00 + 1;
        if (`V2.irq_vector == 8'h04) n_ack_v04 <= n_ack_v04 + 1;
    end
end

// ---- +irqlog: interrupt-controller timeline ----
reg irqlog;
initial irqlog = $test$plusargs("irqlog");
reg vbl_q = 1'b0, t1e_q = 1'b0, irqn_q = 1'b1, ack_q = 1'b0;
always @(posedge clk) if (irqlog && !rst) begin
    vbl_q <= `CORE.vbl_start; t1e_q <= `CORE.intc.t1_expire; irqn_q <= `V2.irq_n; ack_q <= `V2.irq_ack;
    if (`V2.irq_n != irqn_q)
        $display("IRQN %s i=%0d vec=%02x pending=%05b", `V2.irq_n ? "rise" : "fall", `V2.obs.retired, `V2.irq_vector, `CORE.intc.pending);
    if (`CORE.vbl_start && !vbl_q) $display("VBLSTART i=%0d", `V2.obs.retired);
    if (`CORE.intc.t1_expire && !t1e_q) $display("T1EXPIRE i=%0d", `V2.obs.retired);
    if (`V2.irq_ack && !ack_q) $display("IRQACK i=%0d vec=%02x", `V2.obs.retired, `V2.irq_vector);
end

// ---- +intclog (SM1, TB only): every main-CPU write to the interrupt controller 0xD00000-0xD7FFFF (one line per write strobe),
// timer 0 arm / expiry, the timer-0 pending bit, and CPU interrupt acceptance. c= is clk_sys cycles since reset release.
// Format: INTCW <addr> <wdata> be= i=<retired> c= ; T0ARM cnt= i= c= ; T0EXPIRE i= c= mask= pending= ; T0PEND set|clear i= c= ; ISRENTRY vec= i= c=
reg intclog;
initial intclog = $test$plusargs("intclog");
reg t0r_q = 1'b0, t0e_q = 1'b0, tp3_q = 1'b0, ack_iq = 1'b0;
reg [24:0] t0c_q = 25'd0;
integer icyc = 0;   // clk_sys cycles since CPU reset release (same count as the end-of-run `cycles`): the c= stamp below
always @(posedge clk) if (!rst) icyc <= icyc + 1;
always @(posedge clk) if (intclog && !rst) begin
    t0r_q <= `CORE.intc.t0_run; t0e_q <= `CORE.intc.t0_expire; t0c_q <= `CORE.intc.t0_cnt;
    tp3_q <= `CORE.intc.pending[3]; ack_iq <= `V2.irq_ack;
    if (`CORE.wr_stb && `CORE.sel_intc && `CORE.m_we)
        $display("INTCW %06x %04x be=%0d%0d i=%0d c=%0d", `CORE.A, `CORE.m_wdata, `CORE.m_be[1], `CORE.m_be[0], `V2.obs.retired, icyc);
    // arm: t0_run rises, or a running count is reloaded upwards (a later byte write re-arms an in-flight one-shot)
    if (`CORE.intc.t0_run && (!t0r_q || `CORE.intc.t0_cnt > t0c_q))
        $display("T0ARM cnt=%0d i=%0d c=%0d", `CORE.intc.t0_cnt, `V2.obs.retired, icyc);
    if (`CORE.intc.t0_expire && !t0e_q)
        $display("T0EXPIRE i=%0d c=%0d mask=%02x pending=%05b", `V2.obs.retired, icyc, `CORE.intc.ctl[6], `CORE.intc.pending);
    if (`CORE.intc.pending[3] != tp3_q)
        $display("T0PEND %s i=%0d c=%0d", `CORE.intc.pending[3] ? "set" : "clear", `V2.obs.retired, icyc);
    if (`V2.irq_ack && !ack_iq)
        $display("ISRENTRY vec=%02x i=%0d c=%0d", `V2.irq_vector, `V2.obs.retired, icyc);
end

// ---- +eeprom=<hex> (SM1, SIMULATION-ONLY backdoor): 64 16-bit words ($readmemh) copied into the 93C46 storage while the core is
// in reset, standing in for a saved EEPROM (the D2 core has no EEPROM load path yet). EEPROM lines: EEPLOAD at the copy, EEPDUMP at the end.
// Drives the storage array only through this TB-only hierarchical write; no RTL signal changes.
reg [15:0] eimg [0:63];
string epath;
reg eload = 1'b0, edone = 1'b0;
integer ei;
initial eload = $value$plusargs("eeprom=%s", epath);
initial if (eload) $readmemh(epath, eimg);
always @(posedge clk) if (eload && !edone && rst) begin
    edone <= 1'b1;
    for (ei = 0; ei < 64; ei = ei + 1) `CORE.eeprom.storage.mem[ei] = eimg[ei];
    $display("EEPLOAD %s w0=%04x w1=%04x w63=%04x", epath, eimg[0], eimg[1], eimg[63]);
end
// ---- +eeplog (SM1 EEPROM-model study, TB only): one line per 93C46 chip-select session with the serial command it carried
// (opcode, address) and how many SK rising edges arrived in the READ state: a single-word READ needs 16 (17 with a trailing
// clock), a sequential multi-word READ more; that is where the WC-sim variant (a 0 at every word boundary) and upstream
// differ. Format: EEPCS op=<READ|WRITE|ERASE|EWEN|EWDS|ERAL|WRAL|none> addr=<a> read_clks=<n> i=<retired> c=<clk_sys>
reg eeplog; initial eeplog = $test$plusargs("eeplog");
reg el_cs_q = 1'b0, el_sk_q = 1'b0, el_cmd_seen = 1'b0;
reg [7:0] el_cmd = 8'd0; integer el_rclk = 0, el_c = 0;
always @(posedge clk) if (eeplog) begin
    el_c <= el_c + 1;
    el_cs_q <= `CORE.eeprom.cs; el_sk_q <= `CORE.eeprom.sk;
    if (`CORE.eeprom.cs && `CORE.eeprom.sk && !el_sk_q && `CORE.eeprom.es == 3'd2) el_rclk <= el_rclk + 1;
    if (`CORE.eeprom.cs && `CORE.eeprom.sk && !el_sk_q && `CORE.eeprom.es == 3'd1 && `CORE.eeprom.bitcnt == 4'd7) begin
        el_cmd <= {`CORE.eeprom.sr[6:0], `CORE.eeprom.di}; el_cmd_seen <= 1'b1;
    end
    if (el_cs_q && !`CORE.eeprom.cs) begin
        $display("EEPCS op=%s addr=%0d read_clks=%0d i=%0d c=%0d",
                 !el_cmd_seen ? "none" : el_cmd[7:6] == 2'b10 ? "READ" : el_cmd[7:6] == 2'b01 ? "WRITE" : el_cmd[7:6] == 2'b11 ? "ERASE" :
                 el_cmd[5:4] == 2'b11 ? "EWEN" : el_cmd[5:4] == 2'b00 ? "EWDS" : el_cmd[5:4] == 2'b10 ? "ERAL" : "WRAL",
                 el_cmd[5:0], el_rclk, `V2.obs.retired, el_c);
        el_rclk <= 0; el_cmd_seen <= 1'b0;
    end
end

// ---- +eepboot (SM1.2, TB only): EEPROM load-port writes counted while the CPU is in reset, and the 64 logical words (storage is
// inverted) printed on the first clock after CPU reset release: EEPBOOT ld_writes=<n> late_ld_writes=<writes after release> w=<64 words>
reg eepboot; initial eepboot = $test$plusargs("eepboot");
reg eb_done = 1'b0; integer eb_n = 0, eb_late = 0, ebi;
always @(posedge clk) if (eepboot) begin
    if (`CORE.eeprom.ld_wr) begin if (rst) eb_n <= eb_n + 1; else eb_late <= eb_late + 1; end
    if (!rst && !eb_done) begin
        eb_done <= 1'b1;
        $write("EEPBOOT ld_writes=%0d w=", eb_n);
        for (ebi = 0; ebi < 64; ebi = ebi + 1) $write("%04x%s", ~`CORE.eeprom.storage.mem[ebi], ebi == 63 ? "\n" : " ");
    end
end
final if (eepboot) $display("EEPLATE ld_writes_after_cpu_reset_release=%0d", eb_late);
final if (eload) begin
    $write("EEPDUMP");
    for (ei = 0; ei < 64; ei = ei + 1) $write(" %04x", `CORE.eeprom.storage.mem[ei]);
    $write("\n");
end

// ---- +iolog (D2-play): main-CPU writes to the I/O area 0xC00000-0xC0007F (once per bus request), and every read answered by
// the V25 DPRAM stub (0xA00000-0xA00FFF). Format: IOW <addr> <wdata> i=<retired> ; V25R <addr> <q> i=<retired>.
reg iolog;
initial iolog = $test$plusargs("iolog");
reg iow_q = 1'b0, v25r_q = 1'b0;
always @(posedge clk) if (iolog && !rst) begin
    iow_q <= `CORE.m_req && `CORE.m_we && (`CORE.sel_io0 || `CORE.sel_ioex);
    v25r_q <= `CORE.v25_stub_sel;
    if (`CORE.m_req && `CORE.m_we && (`CORE.sel_io0 || `CORE.sel_ioex) && !iow_q)
        $display("IOW %06x %04x i=%0d", `CORE.A, `CORE.m_wdata, `V2.obs.retired);
    if (`CORE.v25_stub_sel && !v25r_q)
        $display("V25R %06x %04x i=%0d", `CORE.A, `CORE.v25_stub_q, `V2.obs.retired);
end

// ---- +ioxlog (regional sets, TB only): every main-CPU access to the extended I/O area (0xC00040-0xC0007F: 8255 at 0x60-0x67),
// one line per bus request: IOX R|W <addr> be=<be> wd=<wdata> ctrl=<8255 control> pc_in=<port C pins> i=<retired>. Capped.
reg ioxlog; integer n_iox = 0;
initial ioxlog = $test$plusargs("ioxlog");
reg iox_q = 1'b0;
always @(posedge clk) if (ioxlog && !rst) begin
    iox_q <= `CORE.m_req && `CORE.sel_ioex;
    if (`CORE.m_req && `CORE.sel_ioex && !iox_q && n_iox < 60000) begin
        n_iox <= n_iox + 1;
        $display("IOX %s %06x be=%b wd=%04x ctrl=%02x pc_in=%02x i=%0d", `CORE.m_we ? "W" : "R", `CORE.A, `CORE.m_be, `CORE.m_wdata, `CORE.ppi.ctrl, `CORE.ppi_pc, `V2.obs.retired);
    end
end

// ---- +wramdump=<file> (build-1 gameplay gate, TB only): a byte shadow of main work RAM 0x200000-0x20FFFF built from the CPU's own
// writes (m_req & m_we & sel_wram; byte lane 0 = even address, as in the sprite shadow above), written as 65536 raw bytes at every
// vblank start (frame k = k-th vbl_start), the same layout as the MAME dump (ram.bin) used to compare. Also +wramlog: one line
// "WW f=<frame> <addr> <data> be=<be>" per write inside 0x2000A0-0x2001FF / 0x20AC00-0x20AC8F / 0x20F800-0x20F80F.
// Observation only: drives nothing, no RTL signal changes.
reg [7:0] wshadow [0:65535];
integer wi, wfile = 0, wframe = 0;
reg wram_en, wlog_en, ww_q = 1'b0, wvb_q = 1'b0;
string wpath;
initial begin
    for (wi = 0; wi < 65536; wi = wi + 1) wshadow[wi] = 8'h0;
    wram_en = $value$plusargs("wramdump=%s", wpath);
    wlog_en = $test$plusargs("wramlog");
    if (wram_en) wfile = $fopen(wpath, "wb");
end
reg [15:0] wa;
always @(posedge clk) if (!rst) begin
    ww_q <= `CORE.m_req && `CORE.m_we && `CORE.sel_wram;
    if (`CORE.m_req && `CORE.m_we && `CORE.sel_wram && !ww_q) begin
        wa = `CORE.A[15:0] & 16'hFFFE;
        if (`CORE.m_be[0]) wshadow[wa]     = `CORE.m_wdata[7:0];
        if (`CORE.m_be[1]) wshadow[wa + 1] = `CORE.m_wdata[15:8];
        if (wlog_en && ((wa >= 16'h00A0 && wa < 16'h0200) || (wa >= 16'hAC00 && wa < 16'hAC90) || (wa >= 16'hF800 && wa < 16'hF810)))
            $display("WW f=%0d %06x %04x be=%0d%0d i=%0d", wframe, `CORE.A, `CORE.m_wdata, `CORE.m_be[1], `CORE.m_be[0], `V2.obs.retired);
    end
    wvb_q <= `CORE.vbl_start;
    if (`CORE.vbl_start && !wvb_q) begin
        wframe = wframe + 1;
        if (wram_en) begin for (wi = 0; wi < 65536; wi = wi + 1) $fwrite(wfile, "%c", wshadow[wi]); $fflush(wfile); end
    end
end

// ---- sprite RAM CPU traffic (evidence for the fit configuration's 512-word XOR-folded stand-in) ----
// Counts CPU cycles to sprite RAM (0x400000-0x4FFFFF) and keeps two byte-lane shadows: the full 64K-word RAM and a
// 512-word RAM addressed by f2_xram_standin's XOR fold. A read where the two would return different data is counted:
// with the stand-in such a read would see aliased contents. Printed as SPRRAM at each vblank start when changed.
reg  [15:0] spr_full [0:65535];
reg  [15:0] spr_fold [0:511];
integer si, spr_rd = 0, spr_wr = 0, spr_rd_alias = 0, spr_rd_nonzero = 0, spr_wmax = -1, spr_rmax = -1, spr_last_rd = -1;
initial begin for (si = 0; si < 65536; si = si + 1) spr_full[si] = 16'h0; for (si = 0; si < 512; si = si + 1) spr_fold[si] = 16'h0; end
function automatic [8:0] fold9(input [15:0] a); fold9 = a[8:0] ^ a[15:9] ^ 9'd0; endfunction
reg spr_q = 1'b0, vbs_q = 1'b0;
reg [15:0] sa; reg [8:0] sf; reg [15:0] sw; reg [1:0] sb;
always @(posedge clk) if (!rst) begin
    spr_q <= `CORE.m_req && `CORE.sel_sprram;
    if (`CORE.m_req && `CORE.sel_sprram && !spr_q) begin
        sa = `CORE.A[16:1]; sf = fold9(sa); sw = `CORE.m_wdata; sb = `CORE.m_be;
        if (`CORE.m_we) begin
            spr_wr = spr_wr + 1; if ($signed({1'b0, sa}) > spr_wmax) spr_wmax = sa;
            if (sb[0]) begin spr_full[sa][7:0] = sw[7:0];   spr_fold[sf][7:0] = sw[7:0];   end
            if (sb[1]) begin spr_full[sa][15:8] = sw[15:8]; spr_fold[sf][15:8] = sw[15:8]; end
        end else begin
            spr_rd = spr_rd + 1; if ($signed({1'b0, sa}) > spr_rmax) spr_rmax = sa;
            if (spr_full[sa] != spr_fold[sf]) spr_rd_alias = spr_rd_alias + 1;
            if (spr_full[sa] != 16'h0) spr_rd_nonzero = spr_rd_nonzero + 1;
        end
    end
    vbs_q <= `CORE.vbl_start;
    if (`CORE.vbl_start && !vbs_q && spr_rd + spr_wr != spr_last_rd) begin
        spr_last_rd = spr_rd + spr_wr;
        $display("SPRRAM i=%0d cpu_writes=%0d cpu_reads=%0d reads_nonzero=%0d reads_where_512w_fold_differs=%0d max_word_written=%0d max_word_read=%0d",
                 `V2.obs.retired, spr_wr, spr_rd, spr_rd_nonzero, spr_rd_alias, spr_wmax, spr_rmax);
    end
end

// ---- bounded hang detection (experiments/WC-sim/rtl/wc_watchdog.sv) ----
integer maxinstr, maxcyc, stallcyc, maxinstcyc, trappc, trapmodepc, trapimmpc;
initial begin
    if (!$value$plusargs("maxinstr=%d", maxinstr)) maxinstr = 1000000;
    if (!$value$plusargs("maxcyc=%d", maxcyc)) maxcyc = 2000000000;
    if (!$value$plusargs("stallcyc=%d", stallcyc)) stallcyc = 1000000;
    if (!$value$plusargs("maxinstcyc=%d", maxinstcyc)) maxinstcyc = 50000000;
    if (!$value$plusargs("trappc=%d", trappc)) trappc = -1;
    if (!$value$plusargs("trapmodepc=%d", trapmodepc)) trapmodepc = -1;
    if (!$value$plusargs("trapimmpc=%d", trapimmpc)) trapimmpc = -1;
end
wire wd_idle, wd_abs;
// RC1 (exp/ga2-rc1): while the core is paused on the Pocket menu (pause_r) the CPU is stopped on purpose; the freeze watchdog is held
// in reset for the pause (a real freeze outside a pause is still caught, and the watchdog restarts from zero at the resume).
`ifdef D2_AU3
wire wd_hold = $root.d2_tb.ic.pause_r;
`else
wire wd_hold = 1'b0;
`endif
wc_watchdog wd (.clk(clk), .rst(rst | wd_hold), .retired(`V2.obs.retired), .progress(`V2.bus_ack | `V2.if_ack),
                .n_idle(stallcyc), .abs_cap(maxinstcyc), .stall_idle(wd_idle), .stall_abs(wd_abs));

// ---- stop conditions and end-of-run report ----
integer cycles = 0, last_ret = 0, drain = -1, r;
reg trapped = 0, stalled = 0;
string trapkind = "";
task automatic report();
    for (r = 0; r < 32; r = r + 1) $display("R%02d=%08x", r, `V2.regs.gpr_a[r]);
    $display("PSW=%08x", `V2.psw);
    if (trapped) $display("TRAP %s ipc=%08x", trapkind, `V2.ipc);
    if (stalled) $display("STALL %s ipc=%08x upc=%0d retired=%0d", wd_abs ? "abs-cap" : "idle(no retire, no bus progress)", `V2.ipc, `V2.upc, `V2.obs.retired);
    $display("END halted=%0d instrs=%0d cycles=%0d", `V2.obs.halted, `V2.obs.retired, cycles);
    $display("COUNTERS pre=%0d exec=%0d mem=%0d fetch=%0d iter=%0d", `V2.obs.t_pre, `V2.obs.t_exec, `V2.obs.t_mem, `V2.obs.t_fetch, `V2.obs.t_iter);
    $display("BUSCOUNT bus_req=%0d bus_ack=%0d if_req=%0d if_ack=%0d", n_bus_req, n_bus_ack, n_if_req, n_if_ack);
    $display("IRQ asserted=%0d vector64=%0d vector68=%0d other=%0d irq_ack_by_cpu=%0d ack_vec00=%0d ack_vec04=%0d irq_n_low_cycles=%0d psw_ie=%0d",
             n_irq_assert, n_irq_v40, n_irq_v44, n_irq_other, n_irq_ack, n_ack_v00, n_ack_v04, n_irq_low_cycles, `V2.psw[18]);
    $display("SIMCHECK errors=%0d ipc_errors=%0d", `V2.chk.errors, `V2.obs.ipc_errors);
endtask
always @(posedge clk) if (!rst) begin
    cycles <= cycles + 1;
    if (drain < 0) begin
        if (`V2.obs.retired != last_ret) last_ret <= `V2.obs.retired;
        else if (wd_idle || wd_abs) begin stalled <= 1; drain <= 12; end
        if (trappc >= 0 && `V2.upc == trappc[11:0])         begin trapped <= 1; trapkind <= "opcode";  drain <= 12; end
        if (trapmodepc >= 0 && `V2.upc == trapmodepc[11:0]) begin trapped <= 1; trapkind <= "mode";    drain <= 12; end
        if (trapimmpc >= 0 && `V2.upc == trapimmpc[11:0])   begin trapped <= 1; trapkind <= "immdest"; drain <= 12; end
        if (`V2.obs.halted || `V2.obs.retired > maxinstr || cycles > maxcyc) drain <= 12;
    end else if (drain > 0) drain <= drain - 1;
    else begin
        report();
        $finish;
    end
end
endmodule
`undef V2
`undef CORE
