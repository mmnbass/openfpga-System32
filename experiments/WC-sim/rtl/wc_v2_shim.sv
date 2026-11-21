// SPDX-License-Identifier: GPL-3.0-or-later
//
// wc_v2_shim: a module NAMED s32_v60 that wraps UNMODIFIED V2 (v2_core) for the
// WC-sim whole-chip simulation. SIMULATION ONLY. Port list and instance
// parameters are those of experiments/F2-fullchip/rtl/f2_v60_shim.sv and of
// upstream s32_v60; V2 itself and the F2 shim are not touched.
//
// Why this is not f2_v60_shim.sv: that shim is a FIT harness. Its microcode
// loader is a stand-in that feeds unknown bits (if_data, bus_rdata) into V2's
// writable control store so synthesis cannot specialise it; the CPU cannot run
// code from it. Here the REAL production microcode is loaded instead, from the
// hex files m1_m11_microcode.py --reset-native wrote (+mc=<dir>), by a loader
// that runs for 3000 clk_sys cycles after power-up with V2 held in reset.
// Everything else is as the F2 shim: ce = 1 (V2 steps every clk_sys edge), the
// fetch queue's own reset address 0xFFFFFFF0, v2_core CS_DEPTH 2048, CS_ROM 0.
//
// Sim-only monitors (bound into v2_core, as in the S0 bench):
//   v2_sim_observer  retirement records "L n pc= op=" with +lockstep
//   v2_sim_checks    SIMCHECK
// Stops (display, then $finish after a 12-cycle drain): V2 microcode trap
// (+trappc= +trapmodepc= +trapimmpc=, from m1_symbols.txt), HALT, stall (no
// retirement for +stallcyc= cycles, default 100000), +maxinstr=, +maxcyc=.
// Bus log (+buslog): every logical bus access outside program ROM and work
// RAM, "A <R|W> <addr> <size> <data> i=<retired>".
// +nowide: force fast_ifetch = 0 (instruction fetch on the data-bus path).

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

// ---- microcode loader (real production microcode) ----
reg [`CW_W-1:0] prog [0:2047];
reg      [23:0] dtab [0:255];
reg      [23:0] mtab [0:511];
string          mc;
integer         ii;
reg             nowide, buslog, busall;
initial begin
    for (ii = 0; ii < 2048; ii = ii + 1) prog[ii] = '0;
    if (!$value$plusargs("mc=%s", mc)) begin $display("FATAL: no +mc=<microcode dir>"); $finish; end
    $readmemh({mc, "/m1_cstore.hex"}, prog);
    $readmemh({mc, "/m1_dispatch.hex"}, dtab);
    $readmemh({mc, "/m1_mode.hex"}, mtab);
    nowide = $test$plusargs("nowide");
    buslog = $test$plusargs("buslog");
    busall = $test$plusargs("busall");
end
localparam integer LOAD_END = 3000;
integer ld = 0;
always @(posedge clk) if (ld < LOAD_END) ld <= ld + 1;
wire        v2_rst  = rst | (ld < LOAD_END);
wire [9:0]  ti      = ld[9:0] - 10'd0;
wire [11:0] tj      = ld[11:0] - 12'd2048;      // table index during the table phase
wire        cs_ld   = (ld < 2048);
wire        tb_ld   = (ld >= 2048) && (ld < 2816);
wire        cs_we   = cs_ld;
wire [11:0] cs_waddr = ld[11:0];
wire [`CW_W-1:0] cs_wdata = prog[ld[10:0]];
wire        tbl_we  = tb_ld;
wire        tbl_sel = tj >= 12'd256;
wire  [8:0] tbl_addr = tbl_sel ? 9'(tj - 12'd256) : 9'(tj);
wire [23:0] tbl_wdata = tbl_sel ? mtab[tj[8:0] - 9'd256 + 9'd0] : dtab[tj[7:0]];

v2_core #(.CS_DEPTH(2048), .CS_ROM(1'b0), .SHIFT_ITER(1'b0), .UNIT_STYLE(0)) core (
    .clk(clk), .ce(1'b1), .rst(v2_rst), .fast_ifetch(fast_ifetch & ~nowide),
    .if_req(if_req), .if_addr(if_addr), .if_data(if_data), .if_ack(if_ack),
    .bus_req(bus_req), .bus_we(bus_we), .bus_addr(bus_addr), .bus_size(bus_size),
    .bus_wdata(bus_wdata), .bus_rdata(bus_rdata), .bus_ack(bus_ack),
    .irq_n(irq_n), .irq_vector(irq_vector), .irq_ack(irq_ack), .nmi_n(nmi_n),
    .cs_we(cs_we), .cs_waddr(cs_waddr), .cs_wdata(cs_wdata),
    .tbl_we(tbl_we), .tbl_sel(tbl_sel), .tbl_addr(tbl_addr), .tbl_wdata(tbl_wdata)
);

bind v2_core v2_sim_observer obs (
    .clk(clk), .rst(rst), .ce(ce), .adv(adv), .data_busy(data_busy), .sh_busy(sh_busy),
    .fetch_short(fetch_short), .cw(cw), .upc(upc), .head_pc(head_pc), .window(window),
    .ucond(ucond),
    .gpr_we(regs.gpr_we), .gpr_waddr(regs.w_addr), .gpr_wbe(regs.w_be), .gpr_wdata(regs.w_data),
    .ipc(ipc),
    .psw(psw), .psw_upd(adv && (cw3[`CW_FLAG_UPD] != 4'd0 || cw3[`CW_WR_DST] == 3'd4)),
    .dop_ack(busu.bus_ack && busu.owner == 2'd1), .dop_we(busu.d_we),
    .priv_we(adv && cw3[`CW_WR_DST] == 3'd3),
    .priv_id(wr_addr3), .priv_be(wr_be3), .priv_data(res_q),
    .redir_e3(adv && cw3[`CW_REDIRECT])
);
bind v2_core v2_sim_checks chk (
    .clk(clk), .rst(rst), .adv(adv), .cw(cw), .avail(avail), .head_pc(head_pc),
    .imm_load(imm_load), .upc(upc),
    .ra_addr(regs.ra_addr), .rb_addr(regs.rb_addr), .cw1(cw1), .cw2(cw2), .cw3(cw3),
    .wr_addr1(wr_addr1), .wr_addr2(wr_addr2), .wr_addr3(wr_addr3)
);

// ---- logical bus access log (outside program ROM and work RAM) ----
function automatic [31:0] szmask(input [1:0] s);
    szmask = (s == 2'd0) ? 32'h0000_00FF : (s == 2'd1) ? 32'h0000_FFFF : 32'hFFFF_FFFF;
endfunction
wire a_rom_wram = (bus_addr[23:0] < 24'h200000) || (bus_addr[23:20] == 4'hF) || (bus_addr[23:20] == 4'h2);
reg  acc_seen;
always @(posedge clk) begin
    if (v2_rst) acc_seen <= 1'b0;
    else begin
        if (buslog && bus_req && bus_ack && !acc_seen && !a_rom_wram)
            $display("A %s %08x %0d %08x i=%0d", bus_we ? "W" : "R", bus_addr,
                     (bus_size == 2'd0) ? 1 : (bus_size == 2'd1) ? 2 : 4,
                     (bus_we ? bus_wdata : bus_rdata) & szmask(bus_size), core.obs.retired);
        if (busall && bus_req && bus_ack && !acc_seen)    // every data-port access, any region ('B' lines)
            $display("B %s %08x %0d %08x", bus_we ? "W" : "R", bus_addr,
                     (bus_size == 2'd0) ? 1 : (bus_size == 2'd1) ? 2 : 4,
                     (bus_we ? bus_wdata : bus_rdata) & szmask(bus_size));
        acc_seen <= bus_req && bus_ack;
    end
end

// ---- state at reset release, and a check that V2's stores hold the loaded image ----
reg init_done = 1'b0;
integer ci, cm_cs, cm_d, cm_m;
always @(posedge clk) if (!v2_rst && !init_done) begin
    init_done <= 1'b1;
    cm_cs = 0; cm_d = 0; cm_m = 0;
    for (ci = 0; ci < 2048; ci = ci + 1) if (core.useq.cstore[ci] !== prog[ci]) cm_cs = cm_cs + 1;
    for (ci = 0; ci < 256;  ci = ci + 1) if (core.dec.dtab[ci] !== dtab[ci]) cm_d = cm_d + 1;
    for (ci = 0; ci < 512;  ci = ci + 1) if (core.dec.mtab[ci] !== mtab[ci]) cm_m = cm_m + 1;
    $display("UCLOAD control_store_words=2048 mismatches=%0d dispatch_words=256 mismatches=%0d mode_words=512 mismatches=%0d", cm_cs, cm_d, cm_m);
    for (ci = 0; ci < 32; ci = ci + 1) $display("INITR%02d=%08x", ci, core.regs.gpr_a[ci]);
    $display("INITPSW=%08x", core.psw);
    for (ci = 0; ci < 32; ci = ci + 1) $display("INITPR id=%0d val=%08x", ci, core.regs.priv[ci]);
end

// ---- request counters (reported at END) ----
integer n_bus_req = 0, n_bus_ack = 0, n_if_req = 0, n_if_ack = 0;
reg bq = 0, bk = 0, iq = 0, ik = 0;
always @(posedge clk) if (!v2_rst) begin
    bq <= bus_req; bk <= bus_ack; iq <= if_req; ik <= if_ack;
    if (bus_req && !bq) n_bus_req <= n_bus_req + 1;
    if (bus_ack && !bk) n_bus_ack <= n_bus_ack + 1;
    if (if_req && !iq) n_if_req <= n_if_req + 1;
    if (if_ack && !ik) n_if_ack <= n_if_ack + 1;
end

// ---- interrupt line observation (IRQ readiness; reported at END) ----
integer n_irq_assert = 0, n_irq_v40 = 0, n_irq_v44 = 0, n_irq_other = 0, n_irq_ack = 0, n_irq_low_cycles = 0;
reg irq_q = 1'b1, irqack_q = 1'b0;
always @(posedge clk) if (!v2_rst) begin
    irq_q <= irq_n; irqack_q <= irq_ack;
    if (!irq_n) n_irq_low_cycles <= n_irq_low_cycles + 1;
    if (!irq_n && irq_q) begin
        n_irq_assert <= n_irq_assert + 1;
        if (irq_vector == 8'h40) n_irq_v40 <= n_irq_v40 + 1;
        else if (irq_vector == 8'h44) n_irq_v44 <= n_irq_v44 + 1;
        else n_irq_other <= n_irq_other + 1;
    end
    if (irq_ack && !irqack_q) n_irq_ack <= n_irq_ack + 1;
end

// ---- +irqlog: interrupt-controller timeline (sim-only observation through $root; drives nothing) ----
reg irqlog;
initial irqlog = $test$plusargs("irqlog");
reg vbl_q = 1'b0, t1e_q = 1'b0, t0e_q = 1'b0;
always @(posedge clk) if (irqlog && !v2_rst) begin
    vbl_q <= $root.wc_top.core.vbl_start; t1e_q <= $root.wc_top.core.intc.t1_expire;
    if (irq_n != irq_q)
        $display("IRQN %s i=%0d vec=%02x pending=%05b mask6=%02x ctl0..4=%02x,%02x,%02x,%02x,%02x ie=%0d", irq_n ? "rise" : "fall",
                 core.obs.retired, irq_vector, $root.wc_top.core.intc.pending, $root.wc_top.core.intc.ctl[6],
                 $root.wc_top.core.intc.ctl[0], $root.wc_top.core.intc.ctl[1], $root.wc_top.core.intc.ctl[2],
                 $root.wc_top.core.intc.ctl[3], $root.wc_top.core.intc.ctl[4], core.psw[18]);
    if ($root.wc_top.core.vbl_start && !vbl_q) $display("VBLSTART i=%0d", core.obs.retired);
    if ($root.wc_top.core.intc.t1_expire && !t1e_q) $display("T1EXPIRE i=%0d", core.obs.retired);
    if (irq_ack && !irqack_q) $display("IRQACK i=%0d vec=%02x", core.obs.retired, irq_vector);
end

// ---- bounded hang detection ----
wire wd_idle, wd_abs;
wc_watchdog wd (.clk(clk), .rst(v2_rst), .retired(core.obs.retired), .progress(bus_ack | if_ack),
                .n_idle(stallcyc), .abs_cap(maxinstcyc), .stall_idle(wd_idle), .stall_abs(wd_abs));

// ---- +linestat: tile-line scheduler statistics (sim-only observation through $root; drives nothing) ----
reg linestat;
initial linestat = $test$plusargs("linestat");
integer ls_kick = 0, ls_drop = 0, ls_start = 0, ls_done = 0;
reg kq = 1'b0, sq = 1'b0, dq = 1'b0, vq2 = 1'b0;
always @(posedge clk) if (linestat && !v2_rst) begin
    kq <= $root.wc_top.core.tm_render_kick; sq <= $root.wc_top.core.line_start_r; dq <= $root.wc_top.core.tm_line_done;
    vq2 <= $root.wc_top.core.vbl_start;
    if ($root.wc_top.core.tm_render_kick && !kq) begin
        ls_kick <= ls_kick + 1;
        if ($root.wc_top.core.tm_line_busy && !$root.wc_top.core.tm_line_done) ls_drop <= ls_drop + 1;   // kick while the previous line is still rendering
    end
    if ($root.wc_top.core.line_start_r && !sq) ls_start <= ls_start + 1;
    if ($root.wc_top.core.tm_line_done && !dq) ls_done <= ls_done + 1;
    if ($root.wc_top.core.vbl_start && !vq2) $display("LINESTAT i=%0d kicks=%0d dropped=%0d starts=%0d done=%0d", core.obs.retired, ls_kick, ls_drop, ls_start, ls_done);
end

// ---- stop conditions and end-of-run report ----
integer maxinstr, maxcyc, stallcyc, maxinstcyc, trappc, trapmodepc, trapimmpc;
integer cycles = 0, last_ret = 0, last_ret_cyc = 0, drain = -1, r;
reg trapped = 0, stalled = 0;
string trapkind = "";
initial begin
    if (!$value$plusargs("maxinstr=%d", maxinstr)) maxinstr = 1000000;
    if (!$value$plusargs("maxcyc=%d", maxcyc)) maxcyc = 2000000000;
    // idle watchdog: nothing retired AND no CPU bus progress (data/fetch acknowledge) for stallcyc cycles; absolute backstop: no
    // retirement at all for maxinstcyc cycles. A 12,000-byte movcu.b retires nothing for ~480k cycles but acknowledges a bus access
    // every few tens of cycles, so it trips neither. See rtl/wc_watchdog.sv.
    if (!$value$plusargs("stallcyc=%d", stallcyc)) stallcyc = 1000000;
    if (!$value$plusargs("maxinstcyc=%d", maxinstcyc)) maxinstcyc = 50000000;
    if (!$value$plusargs("trappc=%d", trappc)) trappc = -1;
    if (!$value$plusargs("trapmodepc=%d", trapmodepc)) trapmodepc = -1;
    if (!$value$plusargs("trapimmpc=%d", trapimmpc)) trapimmpc = -1;
end
always @(posedge clk) if (!v2_rst) begin
    cycles <= cycles + 1;
    if (drain < 0) begin
        if (core.obs.retired != last_ret) begin last_ret <= core.obs.retired; last_ret_cyc <= cycles; end
        else if (wd_idle || wd_abs) begin stalled <= 1; drain <= 12; end
        if (trappc >= 0 && core.upc == trappc[11:0])         begin trapped <= 1; trapkind <= "opcode";  drain <= 12; end
        if (trapmodepc >= 0 && core.upc == trapmodepc[11:0]) begin trapped <= 1; trapkind <= "mode";    drain <= 12; end
        if (trapimmpc >= 0 && core.upc == trapimmpc[11:0])   begin trapped <= 1; trapkind <= "immdest"; drain <= 12; end
        if (core.obs.halted || core.obs.retired > maxinstr || cycles > maxcyc) drain <= 12;
    end else if (drain > 0) drain <= drain - 1;
    else begin
        for (r = 0; r < 32; r = r + 1) $display("R%02d=%08x", r, core.regs.gpr_a[r]);
        $display("PSW=%08x", core.psw);
        if (trapped) $display("TRAP %s ipc=%08x", trapkind, core.ipc);
        if (stalled) $display("STALL %s ipc=%08x upc=%0d retired=%0d", wd_abs ? "abs-cap" : "idle(no retire, no bus progress)", core.ipc, core.upc, core.obs.retired);
        $display("END halted=%0d instrs=%0d cycles=%0d", core.obs.halted, core.obs.retired, cycles);
        $display("COUNTERS pre=%0d exec=%0d mem=%0d fetch=%0d iter=%0d", core.obs.t_pre, core.obs.t_exec,
                 core.obs.t_mem, core.obs.t_fetch, core.obs.t_iter);
        $display("BUSCOUNT bus_req=%0d bus_ack=%0d if_req=%0d if_ack=%0d", n_bus_req, n_bus_ack, n_if_req, n_if_ack);
        $display("IRQ asserted=%0d vector64=%0d vector68=%0d other=%0d irq_ack_by_cpu=%0d irq_n_low_cycles=%0d psw_ie=%0d", n_irq_assert, n_irq_v40, n_irq_v44, n_irq_other, n_irq_ack, n_irq_low_cycles, core.psw[18]);
        $display("SIMCHECK errors=%0d ipc_errors=%0d", core.chk.errors, core.obs.ipc_errors);
        $finish;
    end
end

endmodule
