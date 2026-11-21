// SPDX-License-Identifier: GPL-3.0-or-later
//
// v2_bcd: packed-decimal byte add/subtract with decimal adjust, plus the
// pack/unpack nibble moves (ADDDC/SUBDC/CVTDPZ/CVTDZP-class operations).
//
// v2_bitfield: bit-field and bit-string helpers: a 32-bit mask generator
// from (offset, length) built from thermometer decoders, insert merge,
// extract mask, find-first-set for SCHBS, and bit-offset to byte-address
// conversion for BAM operands. All shifting is done by v2_shift; microcode
// sequences shift-then-merge.

module v2_bcd (
    input       [7:0] a,
    input       [7:0] b,
    input             cin,
    input             sub,
    output      [7:0] res,
    output            cout,
    output     [15:0] unpack,        // {4'h3, hi, 4'h3, lo}-style zoned form
    output      [7:0] pack           // two zoned digits back to packed
);

wire [4:0] lo_raw = sub ? ({1'b0, b[3:0]} - {1'b0, a[3:0]} - {4'd0, cin})
                        : ({1'b0, b[3:0]} + {1'b0, a[3:0]} + {4'd0, cin});
wire       lo_adj = sub ? lo_raw[4] : (lo_raw > 5'd9);
wire [4:0] lo     = sub ? (lo_adj ? lo_raw - 5'd6 : lo_raw)
                        : (lo_adj ? lo_raw + 5'd6 : lo_raw);
wire [4:0] hi_raw = sub ? ({1'b0, b[7:4]} - {1'b0, a[7:4]} - {4'd0, lo_adj})
                        : ({1'b0, b[7:4]} + {1'b0, a[7:4]} + {4'd0, lo_adj});
wire       hi_adj = sub ? hi_raw[4] : (hi_raw > 5'd9);
wire [4:0] hi     = sub ? (hi_adj ? hi_raw - 5'd6 : hi_raw)
                        : (hi_adj ? hi_raw + 5'd6 : hi_raw);
assign res    = {hi[3:0], lo[3:0]};
assign cout   = hi_adj;
assign unpack = {4'h3, a[7:4], 4'h3, a[3:0]};
assign pack   = {a[3:0], b[3:0]};

endmodule

module v2_bitfield (
    input      [31:0] src,          // insert: value already shifted into place by v2_shift
                                    // extract: value already shifted down by v2_shift
    input      [31:0] dst,          // destination word (insert)
    input       [4:0] offset,
    input       [5:0] length,       // 1..32
    input      [31:0] bitoff,       // full BAM bit offset
    output     [31:0] mask,
    output     [31:0] inserted,
    output     [31:0] extracted,
    output      [5:0] first_set,    // SCHBS: index of first 1 (32 = none)
    output     [31:0] byte_addr_off // bitoff >> 3 (signed)
);

// thermometer decoders: lo(x) has the low x bits set (x = 0..32)
function automatic [31:0] lo(input [6:0] x);
    integer b;
    begin
        for (b = 0; b < 32; b = b + 1) lo[b] = (b < x);
    end
endfunction

wire [6:0] top = {2'b0, offset} + {1'b0, length};
assign mask      = lo(top) & ~lo({2'b0, offset});
assign inserted  = (dst & ~mask) | (src & mask);
assign extracted = src & lo({1'b0, length});

// first set bit = trailing zeros = leading zeros of the bit-reversed word
// (32 when none), from a balanced tree instead of a 32-deep priority chain
wire [31:0] src_rev;
genvar gi;
generate for (gi = 0; gi < 32; gi = gi + 1) begin : rv
    assign src_rev[gi] = src[31 - gi];
end endgenerate
v2_clz32 fsu (.x(src_rev), .n(first_set));

assign byte_addr_off = {{3{bitoff[31]}}, bitoff[31:3]};

endmodule

// (UNIT_STYLE=1 variant) bit-field helper with its own shifters.
module v2_bitfield_naive (
    input      [31:0] src,          // value to insert / shifted extract input
    input      [31:0] dst,          // destination word (insert)
    input       [4:0] offset,
    input       [5:0] length,       // 1..32
    input      [31:0] bitoff,       // full BAM bit offset
    output     [31:0] mask,
    output     [31:0] inserted,
    output     [31:0] extracted,
    output reg  [5:0] first_set,    // SCHBS: index of first 1 (32 = none)
    output     [31:0] byte_addr_off // bitoff >> 3 (signed)
);

wire [63:0] ones  = (64'd1 << length) - 64'd1;
wire [63:0] smask = ones << offset;
assign mask      = smask[31:0];
wire [31:0] shifted_src = src << offset;
assign inserted  = (dst & ~mask) | (shifted_src & mask);
assign extracted = src & ones[31:0];      // src arrives already shifted right

integer i;
always @* begin
    first_set = 6'd32;
    for (i = 31; i >= 0; i = i - 1)
        if (src[i]) first_set = i[5:0];
end

assign byte_addr_off = {{3{bitoff[31]}}, bitoff[31:3]};

endmodule
