// SPDX-License-Identifier: GPL-3.0-or-later
//
// v2_sim_checks: SIMULATION-ONLY microcode timing checks for v2_core (M1.1
// N6). Attached with `bind` (as v2_sim_observer), drives nothing, never in
// the synthesizable file list. Each violation prints "SIMCHECK ..." (first 10)
// and counts in `errors`; the M1 benches report the count and the runners
// require 0.
//
//   dispatch   an opcode dispatch (NXT 5) uses the dispatch-table entry read
//              in the previous cycle, so in that cycle the opcode byte must
//              have been queued and the head must not have moved since; and
//              it must still be queued now (no flush in between)
//   mode       a mode dispatch (NXT 6): the same, for the byte at the previous
//              cycle's IMM_OFS
//   lookup     a dispatch word repeats the IMM_OFS (and, for NXT 6, MODM_SEL)
//              of the lookup it uses: a stalled word re-reads the tables with
//              its own fields every cycle
//   format     a micro-branch on the format byte (conditions 24 F1, 25 D)
//              needs the format byte queued when the opcode dispatch latched it
//   immediate  an immediate/displacement latch (IMM_LD) reads only queued bytes
//   ustack     no call with 4 return addresses held, no return with none
//   hazard     a word that uses its operands (writes a result, updates flags,
//              redirects, loads MAR or starts mul/div) and reads a GPR or temp
//              on an operand bus (A/B = RA, RB, RT) or as the address-unit temp
//              base reads it in E0, and
//              the register file returns the old value while a write is
//              pending: no older word still in E1-E3 may be writing that
//              register (the microcode must space such words, or bypass);
//              likewise the PSW (A/B = PSW) while an older word's flag update
//              or PSW write is pending (M1.2)

`include "v2_pkg.svh"

module v2_sim_checks (
    input             clk,
    input             rst,
    input             adv,
    input  [`CW_W-1:0] cw,
    input       [5:0] avail,
    input      [31:0] head_pc,
    input             imm_load,
    input      [11:0] upc,
    // register reads (E0) and pending writes (E1-E3)
    input       [4:0] ra_addr, rb_addr,
    input  [`CW_W-1:0] cw1, cw2, cw3,
    input       [4:0] wr_addr1, wr_addr2, wr_addr3
);

integer errors = 0;
reg  [5:0] avail_q = 0;
reg [31:0] head_q = 0;
reg  [2:0] ofs_q = 0;
reg  [1:0] msel_q = 0;
reg        ifl_ok = 1'b0;
integer    depth = 0;
wire [2:0] nxt  = cw[`CW_NXT];
wire [4:0] csel = {cw[`CW_COND_HI], cw[`CW_COND]};
integer    nbytes;
wire [3:0] asel = cw[`CW_ASEL], bsel = cw[`CW_BSEL];
wire       uses  = (cw[`CW_WR_DST] != 3'd0) || (cw[`CW_FLAG_UPD] != 4'd0) || cw[`CW_REDIRECT]
                 || cw[`CW_MAR_LD] || cw[`CW_MD_START];
wire       rd_ra = (asel == 4'd0) || (bsel == 4'd0);
wire       rd_rb = (asel == 4'd1) || (bsel == 4'd1);
wire       rd_rt = (asel == 4'd3) || (bsel == 4'd3) || (cw[`CW_AGU_BASE] == 3'd6);
function automatic gpr_w(input [`CW_W-1:0] c, input [4:0] wa, input [4:0] a);
    gpr_w = (c[`CW_WR_DST] == 3'd1) && (wa == a);
endfunction
wire       rd_psw = (asel == 4'd7) || (bsel == 4'd7);
function automatic psw_w(input [`CW_W-1:0] c);
    psw_w = (c[`CW_FLAG_UPD] != 4'd0) || (c[`CW_WR_DST] == 3'd4);
endfunction
function automatic tmp_w(input [`CW_W-1:0] c, input [3:0] t);
    tmp_w = (c[`CW_WR_DST] == 3'd2) && (c[`CW_TADDR] == t);
endfunction

task automatic fail(input string what);
    begin
        errors = errors + 1;
        if (errors <= 10) $display("SIMCHECK %0t %s (upc=%0d head=%08x avail=%0d)", $time, what, upc, head_pc, avail);
    end
endtask

always @(posedge clk) begin
    if (rst) begin
        depth = 0; ifl_ok = 1'b0;
    end else begin
        if (adv && nxt == 3'd5) begin
            if (!(avail_q > 0) || head_pc !== head_q) fail("dispatch: opcode byte not settled in the previous cycle");
            if (!(avail > 0)) fail("dispatch: opcode byte no longer queued (flushed after its lookup)");
            ifl_ok = (avail >= 6'd2);
        end
        if (adv && nxt == 3'd6 && (!(avail_q > {3'd0, ofs_q}) || head_pc !== head_q))
            fail("mode: mode byte not settled in the previous cycle");
        if (adv && nxt == 3'd6 && !(avail > {3'd0, ofs_q}))
            fail("mode: mode byte no longer queued (flushed after its lookup)");
        if (adv && (nxt == 3'd5 || nxt == 3'd6) && cw[`CW_IMM_OFS] !== ofs_q)
            fail("lookup: dispatch word's IMM_OFS differs from the lookup's");
        if (adv && nxt == 3'd6 && cw[`CW_MODM_SEL] !== msel_q)
            fail("lookup: mode dispatch word's MODM_SEL differs from the lookup's");
        if (adv && nxt == 3'd2 && (csel == 5'd24 || csel == 5'd25) && !ifl_ok)
            fail("format: branch on a format byte that was not queued at dispatch");
        if (adv && imm_load) begin
            nbytes = immx.ofs + ((immx.isz == 2'd0) ? 1 : (immx.isz == 2'd1) ? 2 : 4);
            if (avail < nbytes) fail("immediate: latched from bytes not yet queued");
        end
        if (adv && uses && ((rd_ra && (gpr_w(cw1, wr_addr1, ra_addr) || gpr_w(cw2, wr_addr2, ra_addr) || gpr_w(cw3, wr_addr3, ra_addr))) ||
                    (rd_rb && (gpr_w(cw1, wr_addr1, rb_addr) || gpr_w(cw2, wr_addr2, rb_addr) || gpr_w(cw3, wr_addr3, rb_addr))) ||
                    (rd_rt && (tmp_w(cw1, cw[`CW_TADDR]) || tmp_w(cw2, cw[`CW_TADDR]) || tmp_w(cw3, cw[`CW_TADDR]))) ||
                    (rd_psw && (psw_w(cw1) || psw_w(cw2) || psw_w(cw3)))))
            fail("hazard: register read while an older word's write to it is pending");
        if (adv && nxt == 3'd3) begin
            if (depth == 4) fail("ustack: call with 4 return addresses held");
            else depth = depth + 1;
        end
        if (adv && nxt == 3'd4) begin
            if (depth == 0) fail("ustack: return with none held");
            else depth = depth - 1;
        end
    end
    avail_q <= avail;
    head_q  <= head_pc;
    ofs_q   <= cw[`CW_IMM_OFS];
    msel_q  <= cw[`CW_MODM_SEL];
end

endmodule
