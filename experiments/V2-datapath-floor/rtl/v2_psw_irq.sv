// SPDX-License-Identifier: GPL-3.0-or-later
//
// v2_psw: V60 PSW (Z=b0 S=b1 OV=b2 CY=b3, IE=b18, EL=b25:24, IS=b28 as in
// upstream/MAME), per-flag update from the selected result unit, full-word
// write (UPDPSW/LDPSW/RETI), and the 16 V60 branch conditions in upstream's
// cond_true order: V NV L NL Z NZ NH H N P R - LT GE LE GT.
//
// v2_irq: maskable interrupt and NMI capture, same port contract as the
// current V60 (irq_n level, irq_vector, irq_ack pulse, nmi_n).

module v2_psw (
    input             clk,
    input             rst,
    input       [3:0] flag_upd,     // {cy, ov, s, z} update enables
    input             nz, ns, nov, ncy,
    input             psw_we,
    input      [31:0] psw_wdata,
    output     [31:0] psw,
    output reg [15:0] cond
);

reg        f_z, f_s, f_ov, f_cy;
reg [27:0] rest;                    // PSW bits 31:4
assign psw = {rest, f_cy, f_ov, f_s, f_z};

always @(posedge clk) begin
    if (rst) begin
        {f_z, f_s, f_ov, f_cy} <= 4'b0;
        rest <= 28'h1000000;        // IS=1 at reset (bit 28)
    end else if (psw_we) begin
        {f_cy, f_ov, f_s, f_z} <= psw_wdata[3:0];
        rest <= psw_wdata[31:4];
    end else begin
        if (flag_upd[0]) f_z  <= nz;
        if (flag_upd[1]) f_s  <= ns;
        if (flag_upd[2]) f_ov <= nov;
        if (flag_upd[3]) f_cy <= ncy;
    end
end

always @* begin
    cond[0]  = f_ov;
    cond[1]  = ~f_ov;
    cond[2]  = f_cy;
    cond[3]  = ~f_cy;
    cond[4]  = f_z;
    cond[5]  = ~f_z;
    cond[6]  = f_cy | f_z;
    cond[7]  = ~(f_cy | f_z);
    cond[8]  = f_s;
    cond[9]  = ~f_s;
    cond[10] = 1'b1;
    cond[11] = 1'b0;
    cond[12] = f_s ^ f_ov;
    cond[13] = ~(f_s ^ f_ov);
    cond[14] = (f_s ^ f_ov) | f_z;
    cond[15] = ~((f_s ^ f_ov) | f_z);
end

endmodule

module v2_irq (
    input             clk,
    input             rst,
    input             irq_n,
    input       [7:0] irq_vector,
    input             nmi_n,
    input             psw_ie,
    input             ack,           // microcode acknowledges
    output            pending,
    output reg        nmi_pending,
    output reg  [7:0] vector,        // +0x40 applied, as upstream does
    output reg        irq_ack
);

reg [1:0] irq_s, nmi_s;
always @(posedge clk) begin
    if (rst) begin
        irq_s <= 2'b11; nmi_s <= 2'b11; nmi_pending <= 1'b0; irq_ack <= 1'b0;
        vector <= 8'd0;
    end else begin
        irq_s   <= {irq_s[0], irq_n};
        nmi_s   <= {nmi_s[0], nmi_n};
        irq_ack <= 1'b0;
        if (nmi_s == 2'b10) nmi_pending <= 1'b1;
        if (ack) begin
            if (nmi_pending) nmi_pending <= 1'b0;
            else begin
                vector  <= irq_vector + 8'h40;
                irq_ack <= 1'b1;
            end
        end
    end
end
assign pending = (~irq_s[1] & psw_ie) | nmi_pending;

endmodule
