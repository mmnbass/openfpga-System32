// SPDX-License-Identifier: GPL-3.0-or-later
//
// v2_sim_observer: SIMULATION-ONLY retirement/debug port and cost counters for
// v2_core. It is attached with `bind` from the M1 bench, reads v2_core's
// internal signals, drives nothing, and is never part of the synthesizable
// file list (experiments/V2-datapath-floor/rtl/files.f).
//
// ---- Instruction boundary and retirement (precise definitions) ----
// * An opcode DISPATCH is an issued microinstruction (E0, adv) with NXT = 5.
//   It is the LAST microinstruction of the instruction before it: it finishes
//   that instruction and selects the next routine. The next instruction
//   STARTS in the following cycle, with the first microinstruction of the
//   routine dispatched to. An instruction is every microinstruction from its
//   start up to and including the dispatch that ends it. Its PC and opcode
//   are the fetch-queue head (head_pc, window[7:0]) at the dispatch that
//   starts it; microcode must consume the previous instruction's bytes, and
//   let the registered decode tables see the new head, before dispatching.
// * HALT has no following dispatch. HALT's routine ends in a self-loop at the
//   microcode address +haltpc. When that loop word first issues, HALT is
//   complete and the CPU is "halted". Loop words issued after that belong to
//   no instruction.
// * An instruction RETIRES when (a) the next instruction has started, or
//   HALT's loop has issued, AND (b) none of its microinstructions remains in
//   E1, E2 or E3, AND (c) no data-bus transaction is outstanding
//   (data_busy = 0). Its architectural effects (register, PSW, memory
//   writes) are then all committed. Retirement is reported in order, one
//   record per instruction, with HALT included.
// * Lock-step record (+lockstep), printed at retirement, as tb_m1_up and
//   m1_ref.py --lockstep print them: "L n pc=<pc> op=<opcode>[ Rnn=<v>...]",
//   listing every general register the instruction changed, then
//   " PSW=<v>" if it changed the PSW (M1.2). GPR writes are snooped at the
//   register file's write port (E3) and charged to the instruction in E3, so
//   a later instruction's writes are never mixed in, even when retirement
//   waits on the data bus. PSW updates (a flag update or a PSW write in E3)
//   are charged the same way; the new PSW is read on the next edge, and the
//   instruction retires only after that.
//
// ---- Cycle accounting (every clk with ce = 1, attributed to the
//      instruction that is current in E0 at that cycle) ----
//   mem   data_busy = 1            pipeline frozen on a data-bus transaction
//   iter  sh_busy = 1, or a microcode WAIT on condition 9 (multiply/divide)
//   fetch fetch_short = 1 (frozen: the queue holds fewer bytes than the step
//         consumes), or a microcode WAIT on condition 7 (queue >= 8 bytes) or
//         27 (decode-ready, M1.1)
//   exec  any other advancing cycle
// A microcode WAIT is an issued conditional micro-branch (NXT = 2) whose target
// is its own address and whose condition holds, so it loops. Waits on other
// conditions count as exec.
// Priority when several apply: mem > iter > fetch > exec. Instruction cycles
// = exec + mem + fetch + iter, from the cycle after the dispatch that starts
// it, through the cycle of the dispatch that ends it (for HALT: through its
// loop's first issue). Cycles before the first dispatch are counted as "pre".

//
// ---- R1a event counters (+counters; test infrastructure, default off) ----
// With +counters, every retirement also prints
//     C <n> dr=<a> dw=<b> priv=<c> psw=<d>
// (after its L record, when +lockstep is also given), where, for the
// instruction in E3 at the event (the same charging rule as the GPR snoop, so
// a later instruction is never mixed in), over its whole retire window:
//   dr    DATA-bus read transactions completed: v2_bus acknowledged with the
//         microcode data port as owner (owner == OWN_D) and not a write phase.
//         Instruction prefetch (owner OWN_PF) and the wide fetch port are not
//         data accesses and are not counted.
//   dw    the same, write phase (a read-modify-write counts one read + one write)
//   priv  writes to the privileged register file: adv && cw3.WR_DST == 3
//         (v2_core's priv_we, v2_regs.sv `priv`)
//   psw   PSW update events: a flag update or a PSW write in E3 (the existing
//         psw_upd input), counted even when the value does not change
// Without +counters nothing is printed and no other output changes.
//
// ---- R2a privileged-write events (+privev; default off) ----
// With +privev every privileged-register-file write in E3 (adv && cw3.WR_DST == 3) prints
//     P <n> id=<regID> be=<byte enables> val=<res_q>
// at once, n = the instruction in E3 (same charging rule as the counters). All writes are events,
// including a write of an unchanged value. The bench dumps the whole file at the end ("PR id= val=").
// Without +privev nothing is printed.
//
// ---- R3a GPR write events (+gprev; default off, test infrastructure) ----
// With +gprev the observer prints "GPREV on" once at time 0 (so a checker can tell "no events" from "option
// absent") and, for every general-register write in E3 (the register file's write port, the same snoop and the
// same charging rule as the lock-step records),
//     G <n> id=<register> be=<byte enables> val=<write data>
// at once, n = the instruction in E3 (an instruction that never retires, e.g. a trapped one, may own events:
// the checker rejects any event of an unretired instruction after the pipeline has drained). Unlike the
// lock-step record, an event is printed whether or not the register's value changes, and a write that is
// restored by a later write is still two events. With +gprev (and +counters) the C and CU records gain a
// trailing " gpr=<n>" (the number of events of that instruction). Without +gprev nothing is printed and no
// other output changes (default output byte-identical).

`include "v2_pkg.svh"

module v2_sim_observer (
    input             clk,
    input             rst,
    input             ce,
    input             adv,
    input             data_busy,
    input             sh_busy,
    input             fetch_short,
    input  [`CW_W-1:0] cw,
    input      [11:0] upc,
    input      [31:0] head_pc,
    input      [63:0] window,
    input      [31:0] ucond,
    // GPR write port (E3), for the lock-step records
    input             gpr_we,
    input       [4:0] gpr_waddr,
    input       [3:0] gpr_wbe,
    input      [31:0] gpr_wdata,
    // instruction-start PC register (M1.1 N5), cross-checked against the head at dispatch
    input      [31:0] ipc,
    // PSW and its update in E3 (M1.2)
    input      [31:0] psw,
    input             psw_upd,
    // R1a counters: data-bus transaction completed this cycle (owner = data port), its
    // direction, and a privileged-register-file write in E3
    input             dop_ack,
    input             dop_we,
    input             priv_we,
    // R2a: the privileged write's ID (register-file address), byte enables and data, all in E3
    input       [4:0] priv_id,
    input       [3:0] priv_be,
    input      [31:0] priv_data,
    // R1a: a REDIRECT word is in E3 and advances this edge (adv && cw3.REDIRECT)
    input             redir_e3
);

integer haltpc;
reg     verbose, lockstep, counters, privev, gprev;
// +ackrel=<pc> (R1a): rel_hit goes (and stays) 1 once the instruction that started at <pc> issues a
// REDIRECT. Evaluated here, at the edge, BEFORE the tags advance, so the attribution (tag3, the
// instruction in E3) pairs with the cw3 that issued it; the benches only read the settled value.
integer ackrel;
reg     rel_hit = 1'b0;
integer c_dr [0:7], c_dw [0:7], c_pw [0:7], c_ps [0:7], c_gw [0:7];
initial begin
    counters = $test$plusargs("counters");
    privev   = $test$plusargs("privev");
    gprev    = $test$plusargs("gprev");
    if (gprev) $display("GPREV on");
    if (!$value$plusargs("ackrel=%d", ackrel)) ackrel = -1;
    if (!$value$plusargs("haltpc=%d", haltpc)) haltpc = -1;
    verbose  = $test$plusargs("retire");
    lockstep = $test$plusargs("lockstep");
end

// per in-flight instruction (slot = instruction number mod 8): registers it
// wrote, their values before its first write and after its last
reg [31:0] w_mask   [0:7];
reg [31:0] w_before [0:7][0:31];
reg [31:0] w_after  [0:7][0:31];
reg [31:0] w_old, w_new;
reg        p_mask   [0:7];
reg [31:0] p_before [0:7];
reg [31:0] p_after  [0:7];
integer    p_pend = -1;            // instruction whose PSW update is read on this edge
integer    slot, kk;
string     ls;

// instruction tags through E1..E3, advanced exactly like cw1..cw3
integer cur = -1;                  // instruction current in E0 (-1 = before the first dispatch)
integer tag1 = -1, tag2 = -1, tag3 = -1;
integer started = 0;               // instructions started (dispatches + HALT completion)
integer retired = 0;
reg     halted = 1'b0;
wire    dispatch_now = adv && cw[`CW_NXT] == 3'd5;
wire    halt_now     = adv && !halted && haltpc >= 0 && upc == haltpc[11:0];

// per-instruction records
integer pc_of  [0:65535];
integer op_of  [0:65535];
integer c_exec [0:65535], c_mem [0:65535], c_fetch [0:65535], c_iter [0:65535];
integer t_exec = 0, t_mem = 0, t_fetch = 0, t_iter = 0, t_pre = 0;

wire [4:0] csel     = {cw[`CW_COND_HI], cw[`CW_COND]};
wire       self_wait = adv && cw[`CW_NXT] == 3'd2 && cw[`CW_JADDR] == upc
                       && (ucond[csel] ^ cw[`CW_CPOL]);
wire       md_wait    = self_wait && csel == 5'd9;
wire       fetch_wait = self_wait && (csel == 5'd7 || csel == 5'd27);   // queue / decode-ready waits
integer    ipc_errors = 0;
reg        ipc_check = 1'b0;

always @(posedge clk) if (!rst && ce) begin
    // cycle accounting for the instruction current in E0
    if (cur < 0 || halted) begin
        if (!halted) t_pre = t_pre + 1;
    end else if (data_busy) begin
        c_mem[cur] = c_mem[cur] + 1; t_mem = t_mem + 1;
    end else if (sh_busy || md_wait) begin
        c_iter[cur] = c_iter[cur] + 1; t_iter = t_iter + 1;
    end else if (fetch_short || fetch_wait) begin
        c_fetch[cur] = c_fetch[cur] + 1; t_fetch = t_fetch + 1;
    end else begin
        c_exec[cur] = c_exec[cur] + 1; t_exec = t_exec + 1;
    end
    // ipc must hold the start PC recorded at the dispatch that started cur
    if (ipc_check && cur >= 0 && ipc !== pc_of[cur][31:0]) begin
        ipc_errors = ipc_errors + 1;
        if (ipc_errors <= 5) $display("IPC instruction %0d: ipc=%08x, head at its dispatch %08x", cur, ipc, pc_of[cur]);
    end
    ipc_check = 1'b0;
    // PSW: the value after an update at the previous edge
    if (p_pend >= 0) begin p_after[p_pend % 8] = psw; p_pend = -1; end
    if (psw_upd && tag3 >= 0) begin
        if (!p_mask[tag3 % 8]) p_before[tag3 % 8] = psw;
        p_mask[tag3 % 8] = 1'b1;
        p_pend = tag3;
    end
    // a GPR write at this edge belongs to the instruction in E3 (before the
    // tags move); the register file still holds the pre-write value
    if (gpr_we && tag3 >= 0) begin
        slot  = tag3 % 8;
        w_old = regs.gpr_a[gpr_waddr];
        for (kk = 0; kk < 4; kk = kk + 1)
            w_new[kk*8 +: 8] = gpr_wbe[kk] ? gpr_wdata[kk*8 +: 8] : w_old[kk*8 +: 8];
        if (!w_mask[slot][gpr_waddr]) w_before[slot][gpr_waddr] = w_old;
        w_mask[slot][gpr_waddr] = 1'b1;
        w_after[slot][gpr_waddr] = w_new;
        c_gw[slot] = c_gw[slot] + 1;
        if (gprev) $display("G %0d id=%0d be=%x val=%08x", tag3, gpr_waddr, gpr_wbe, gpr_wdata);
    end
    // R1a counters: charged to the instruction in E3 (tags have not moved yet)
    if (tag3 >= 0) begin
        if (dop_ack && !dop_we) c_dr[tag3 % 8] = c_dr[tag3 % 8] + 1;
        if (dop_ack &&  dop_we) c_dw[tag3 % 8] = c_dw[tag3 % 8] + 1;
        if (priv_we)            c_pw[tag3 % 8] = c_pw[tag3 % 8] + 1;
        // R2a (+privev): every privileged write is an EVENT "P n id= be= val=" charged to the instruction in
        // E3, in order, whether or not the value changes, printed when it happens (n may be an instruction
        // that never retires: the checker rejects any event of an unretired instruction)
        if (priv_we && privev)  $display("P %0d id=%0d be=%x val=%08x", tag3, priv_id, priv_be, priv_data);
        if (psw_upd)            c_ps[tag3 % 8] = c_ps[tag3 % 8] + 1;
    end
    if (redir_e3 && tag3 >= 0 && ackrel >= 0 && pc_of[tag3] == ackrel) rel_hit <= 1'b1;   // nonblocking: deterministic handoff to the memory server (Codex R1a final #1)
    // tags move with the pipeline
    if (adv) begin
        tag3 = tag2; tag2 = tag1; tag1 = cur;
    end
    // a new instruction starts at an issued dispatch
    if (dispatch_now) begin
        cur = started; started = started + 1;
        pc_of[cur] = head_pc; op_of[cur] = window[7:0];
        c_exec[cur] = 0; c_mem[cur] = 0; c_fetch[cur] = 0; c_iter[cur] = 0;
        w_mask[cur % 8] = 32'd0;
        p_mask[cur % 8] = 1'b0;
        c_dr[cur % 8] = 0; c_dw[cur % 8] = 0; c_pw[cur % 8] = 0; c_ps[cur % 8] = 0; c_gw[cur % 8] = 0;
        ipc_check = 1'b1;
        if (started - retired > 8) $fatal(1, "v2_sim_observer: more than 8 instructions in flight");
    end
    if (halt_now) begin
        halted = 1'b1;
        cur = -2;                  // later loop words belong to no instruction
    end
    // in-order retirement
    while (retired < started &&
           (retired < cur || halted) &&
           tag1 != retired && tag2 != retired && tag3 != retired && !data_busy && p_pend != retired) begin
        if (verbose)
            $display("RETIRE %0d pc=%08x op=%02x cycles=%0d exec=%0d mem=%0d fetch=%0d iter=%0d",
                     retired, pc_of[retired], op_of[retired],
                     c_exec[retired] + c_mem[retired] + c_fetch[retired] + c_iter[retired],
                     c_exec[retired], c_mem[retired], c_fetch[retired], c_iter[retired]);
        if (lockstep) begin
            slot = retired % 8;
            ls = $sformatf("L %0d pc=%08x op=%02x", retired, pc_of[retired], op_of[retired]);
            for (kk = 0; kk < 32; kk = kk + 1)
                if (w_mask[slot][kk] && w_after[slot][kk] !== w_before[slot][kk])
                    ls = {ls, $sformatf(" R%02d=%08x", kk, w_after[slot][kk])};
            if (p_mask[slot] && p_after[slot] !== p_before[slot])
                ls = {ls, $sformatf(" PSW=%08x", p_after[slot])};
            $display("%s", ls);
        end
        if (counters && !gprev)
            $display("C %0d dr=%0d dw=%0d priv=%0d psw=%0d", retired, c_dr[retired % 8], c_dw[retired % 8],
                     c_pw[retired % 8], c_ps[retired % 8]);
        if (counters && gprev)
            $display("C %0d dr=%0d dw=%0d priv=%0d psw=%0d gpr=%0d", retired, c_dr[retired % 8], c_dw[retired % 8],
                     c_pw[retired % 8], c_ps[retired % 8], c_gw[retired % 8]);
        retired = retired + 1;
    end
end

// R1a: the counters of instructions that started but never retired (a trap, or a wait that
// the run ended on). Printed as "CU n dr= dw= priv= psw=" by the bench just before END, so a
// trapped or waiting instruction's side effects are visible too. Only with +counters.
task automatic dump_inflight;
    integer q;
    begin
        if (counters)
            for (q = retired; q < started; q = q + 1)
                if (gprev)
                    $display("CU %0d dr=%0d dw=%0d priv=%0d psw=%0d gpr=%0d", q, c_dr[q % 8], c_dw[q % 8], c_pw[q % 8], c_ps[q % 8], c_gw[q % 8]);
                else
                    $display("CU %0d dr=%0d dw=%0d priv=%0d psw=%0d", q, c_dr[q % 8], c_dw[q % 8], c_pw[q % 8], c_ps[q % 8]);
    end
endtask

endmodule
