// SPDX-License-Identifier: GPL-3.0-or-later
//
// tb_v2_units: focused edge-case tests for the shifter's SHA left-shift result,
// carry and overflow, and the bit-field unit's first-set-bit search.
//
// The SHA reference is the upstream V60's behaviour, transcribed from
// references/s32 rtl/cpu/v60/s32_v60.sv (sha_res, shl_lastout, sha_left_ov;
// upstream matched these to MAME op12.hxx after its own audits):
//   result = v <<< n within the width;
//   carry  = (n <= w) ? v[w - n] : 0;
//   OV     = 0 for n <= 0; (v != 0) for n > w; otherwise the top n bits of
//            the width-sized value are not all equal to its sign bit.
// first_set(src) = index of the lowest 1 bit, 32 when src = 0.
//
// Divergences are counted per category (result / carry / overflow /
// first_set) so that known gaps of the skeleton are visible, not hidden.
//
// Cases: zero, all-ones, sign-bit-only, maximum positive, every leading-run
// length (every sign-transition position) of both signs, single bits, all
// three operand widths, counts 0..33 and right shifts, plus random values.

`timescale 1ns/1ps
module tb_v2_units;

reg  [31:0] a;
reg   [7:0] count;
reg   [2:0] op;
reg   [1:0] size;
wire [31:0] res;
wire        cy, ov;
v2_shift sh (.a(a), .count(count), .op(op), .size(size), .cin(1'b0), .res(res), .cy(cy), .ov(ov));

reg  [31:0] src;
wire [31:0] bf_mask, bf_ins, bf_ext, bf_boff;
wire  [5:0] first_set;
v2_bitfield bf (.src(src), .dst(32'd0), .offset(5'd0), .length(6'd1), .bitoff(32'd0),
                .mask(bf_mask), .inserted(bf_ins), .extracted(bf_ext),
                .first_set(first_set), .byte_addr_off(bf_boff));

integer errors = 0, checks = 0;

function integer width(input [1:0] sz);
    width = (sz == 2'd0) ? 8 : (sz == 2'd1) ? 16 : 32;
endfunction

// reference SHA left (upstream s32_v60 semantics), 0 < n
task ref_sha_left(input [31:0] val, input integer w, input integer n,
                  output [31:0] r, output c, output o);
    reg [31:0] field, ones, wm; reg s0;
    begin
        wm = (w == 32) ? 32'hFFFF_FFFF : ((32'd1 << w) - 32'd1);
        s0 = val[w-1];
        r  = (n >= 32) ? 32'd0 : ((val << n) & wm);
        c  = (n == 0) ? 1'b0 : (n <= w) ? val[w - n] : 1'b0;
        if (n <= 0)      o = 1'b0;
        else if (n > w)  o = (val != 32'd0);
        else begin
            field = (val >> (w - n)) & ((n == 32) ? 32'hFFFF_FFFF : ((32'd1 << n) - 32'd1));
            ones  = (n == 32) ? 32'hFFFF_FFFF : ((32'd1 << n) - 32'd1);
            o = s0 ? (field != ones) : (field != 32'd0);
        end
    end
endtask

integer e_res = 0, e_cy = 0, e_ov = 0, e_fs = 0;
integer e_cy_big = 0, e_ov_eqw = 0, e_ov_other = 0;

task check_sha(input [31:0] val, input [1:0] sz, input integer n);
    reg [31:0] er; reg ec, eo; integer w; reg [31:0] v; reg bad;
    begin
        w = width(sz);
        v = (w == 32) ? val : (val & ((32'd1 << w) - 32'd1));
        ref_sha_left(v, w, n, er, ec, eo);
        a = val; size = sz; op = 3'd1; count = n[7:0];
        #1;
        checks = checks + 1; bad = 1'b0;
        if (res !== er) begin e_res = e_res + 1; bad = 1'b1; end
        if (n != 0 && cy !== ec) begin
            e_cy = e_cy + 1; bad = 1'b1;
            if (n > w) e_cy_big = e_cy_big + 1;
        end
        if (ov !== eo) begin
            e_ov = e_ov + 1; bad = 1'b1;
            if (n == w) e_ov_eqw = e_ov_eqw + 1; else e_ov_other = e_ov_other + 1;
        end
        if (bad) begin
            errors = errors + 1;
            if (errors <= 12)
                $display("DIFF SHA< w=%0d n=%0d v=%08x: res %08x/%08x cy %b/%b ov %b/%b (dut/ref)",
                         w, n, v, res, er, cy, ec, ov, eo);
        end
    end
endtask

task check_fs(input [31:0] val);
    integer i; reg [5:0] e;
    begin
        e = 6'd32;
        for (i = 31; i >= 0; i = i - 1) if (val[i]) e = i[5:0];
        src = val; #1;
        checks = checks + 1;
        if (first_set !== e) begin
            errors = errors + 1; e_fs = e_fs + 1;
            if (errors <= 200) $display("FAIL first_set(%08x) = %0d, expected %0d", val, first_set, e);
        end
    end
endtask

integer s, n, k, i, w;
reg [31:0] v;
initial begin
    for (s = 0; s < 3; s = s + 1) begin
        w = width(s[1:0]);
        for (n = 0; n <= 33; n = n + 1) begin
            // zero, all-ones, sign bit only, maximum positive, one
            check_sha(32'd0, s[1:0], n);
            check_sha(32'hFFFF_FFFF, s[1:0], n);
            check_sha(32'd1 << (w - 1), s[1:0], n);
            check_sha((32'd1 << (w - 1)) - 32'd1, s[1:0], n);
            check_sha(32'd1, s[1:0], n);
            // every leading-run length: k copies of the sign then the opposite bit
            for (k = 1; k < w; k = k + 1) begin
                v = ~((32'hFFFF_FFFF) >> k);                      // k leading ones (32-bit)
                v = (w == 32) ? v : (v >> (32 - w));              // move to width w
                check_sha(v, s[1:0], n);                          // negative, run of k ones
                check_sha(~v & ((w == 32) ? 32'hFFFF_FFFF : ((32'd1 << w) - 32'd1)), s[1:0], n);
            end
            // bits above the operand width must be ignored
            check_sha(32'hA5A5_0000 | 32'h0000_0001, s[1:0], n);
        end
        // right shifts never report overflow
        for (n = 1; n <= 33; n = n + 1) begin
            a = 32'h8000_0001; size = s[1:0]; op = 3'd1; count = -n[7:0]; #1;
            checks = checks + 1;
            if (ov !== 1'b0) begin errors = errors + 1; $display("FAIL SHA> ov set, w=%0d n=%0d", w, n); end
        end
        for (i = 0; i < 2000; i = i + 1) check_sha($random, s[1:0], $unsigned($random) % 34);
    end
    // first set bit
    check_fs(32'd0);
    check_fs(32'hFFFF_FFFF);
    for (i = 0; i < 32; i = i + 1) begin
        check_fs(32'd1 << i);                 // single bit
        check_fs(32'hFFFF_FFFF << i);         // run of ones from bit i up
        check_fs(32'h8000_0000 | (32'd1 << i));
    end
    for (i = 0; i < 5000; i = i + 1) check_fs($random);
    $display("SHA< divergences vs upstream: result %0d, carry %0d (of which count > width: %0d), overflow %0d (count == width: %0d, other: %0d)",
             e_res, e_cy, e_cy_big, e_ov, e_ov_eqw, e_ov_other);
    $display("first_set divergences: %0d", e_fs);
    if (errors == 0) $display("V2 UNITS PASS: %0d checks", checks);
    else             $display("V2 UNITS DIFF: %0d of %0d checks differ from the reference", errors, checks);
    $finish;
end

endmodule
