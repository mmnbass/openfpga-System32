// SPDX-License-Identifier: GPL-3.0-or-later
// wc_watchdog: bounded hang detection for the V2-driven sim (SIMULATION ONLY).
//   stall_idle  : nothing retired AND no CPU bus progress (a data-port or instruction-fetch acknowledge) for n_idle cycles.
//                 A long legitimate string move (movcu.b of 12,000 bytes) retires nothing for ~480,000 cycles but makes bus
//                 progress every few tens of cycles, so it never trips this; a stuck bus handshake or a microcode self-loop
//                 (no more acknowledges once the fetch queue is full) does.
//   stall_abs   : backstop, no retirement at all for abs_cap cycles, whatever the bus does.
// `retired` is the retirement counter, `progress` a pulse/level that is high while a bus acknowledge is presented.
module wc_watchdog (
    input             clk,
    input             rst,
    input      [31:0] retired,
    input             progress,
    input      [31:0] n_idle,
    input      [31:0] abs_cap,
    output reg        stall_idle,
    output reg        stall_abs
);
reg [31:0] last_ret = 0, since_ret = 0, since_any = 0;
always @(posedge clk) begin
    if (rst) begin
        last_ret <= retired; since_ret <= 0; since_any <= 0; stall_idle <= 1'b0; stall_abs <= 1'b0;
    end else begin
        if (retired != last_ret) begin last_ret <= retired; since_ret <= 0; since_any <= 0; end
        else begin
            since_ret <= since_ret + 1;
            since_any <= progress ? 32'd0 : since_any + 1;
        end
        if (since_any > n_idle) stall_idle <= 1'b1;
        if (since_ret > abs_cap) stall_abs <= 1'b1;
    end
end
endmodule
