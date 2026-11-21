// SPDX-License-Identifier: GPL-3.0-or-later
// TESTBENCH ONLY (D2 Pocket top): the V2 simulation observer (retirement records "L n pc= op=" with +lockstep, the
// retired counter) and checks (SIMCHECK), bound into the unmodified v2_core exactly as experiments/WC-sim/rtl/wc_v2_shim.sv
// binds them (the two bind statements below are copied from it verbatim). Nothing here is in the synthesizable set.
`include "v2_pkg.svh"
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
