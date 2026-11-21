// SPDX-License-Identifier: GPL-3.0-or-later
//
// v2_regs: complete V60 architectural register storage, RAM-based.
//
//   GPR  R0-R31 (R31 = active SP)   two RAM copies -> read ports A and B
//   PRIV 32 x 32 privileged/stack-bank file (ISP, L0SP-L3SP, SBR, TR, SYCW,
//        TKCW, PIR, PSW2, ATBR/ATLR x4, TRMOD, ADTR/ADTMR x2, spare)
//   TEMP 16 x 32 microcode temporaries (string pointers/counts, bit-field
//        scratch, XCH/MOVD halves, exception frame words)
//   SP   flop shadow of R31 for single-step push/pop address generation
//
// Reads are synchronous (address in cycle E0, data in E1). One write per
// cycle per file, with byte enables (the V60 writes B/H/W). A real CPU needs
// the same storage; only the port shape (1 write/cycle) is a design choice.

module v2_regs (
    input             clk,
    input       [4:0] ra_addr,
    input       [4:0] rb_addr,
    input       [4:0] rp_addr,      // privileged read
    input       [3:0] rt_addr,      // temp read
    output reg [31:0] ra_data,
    output reg [31:0] rb_data,
    output reg [31:0] rp_data,
    output reg [31:0] rt_data,
    output reg [31:0] sp,
    input             gpr_we,
    input             priv_we,
    input             temp_we,
    input       [4:0] w_addr,
    input       [3:0] wt_addr,
    input       [3:0] w_be,
    input      [31:0] w_data
);

(* ramstyle = "no_rw_check" *) reg [31:0] gpr_a [0:31];
(* ramstyle = "no_rw_check" *) reg [31:0] gpr_b [0:31];
(* ramstyle = "no_rw_check" *) reg [31:0] priv  [0:31];
(* ramstyle = "no_rw_check" *) reg [31:0] temp  [0:15];

integer k;
always @(posedge clk) begin
    for (k = 0; k < 4; k = k + 1) begin
        if (gpr_we && w_be[k]) begin
            gpr_a[w_addr][k*8 +: 8] <= w_data[k*8 +: 8];
            gpr_b[w_addr][k*8 +: 8] <= w_data[k*8 +: 8];
        end
        if (priv_we && w_be[k]) priv[w_addr][k*8 +: 8] <= w_data[k*8 +: 8];
        if (temp_we && w_be[k]) temp[wt_addr][k*8 +: 8] <= w_data[k*8 +: 8];
        if (gpr_we && w_addr == 5'd31 && w_be[k]) sp[k*8 +: 8] <= w_data[k*8 +: 8];
    end
    ra_data <= gpr_a[ra_addr];
    rb_data <= gpr_b[rb_addr];
    rp_data <= priv[rp_addr];
    rt_data <= temp[rt_addr];
end

endmodule
