//============================================================================
// Per-line clip windows 2/3 (MAME PR #16038, segas32_v.cpp
// compute_clipping_extents).
//
// $1FF04 bit 4 / bit 5 replace the X bounds of clip rectangle 2 / 3 with words
// from the rowscroll/rowselect table page, indexed by the line (flipped with
// $1FF00 bit 9): clip 2 = +0x000 min / +0x200 max, clip 3 = +0x100 / +0x300,
// max inclusive, 0xFFFF in either word drops the rectangle on that line.
// NBG0 is rendered fully opaque with only the selected rectangle in its mask,
// so the set of opaque pixels on the line is exactly the clip window.
//============================================================================
`timescale 1ns/1ps

module tb_tilemap_lineclip;

reg clk_sys = 0, clk_ram = 0, rst = 1;
always #10 clk_sys = ~clk_sys;
always #5  clk_ram = ~clk_ram;

reg         cpu_we = 0;
reg  [15:0] cpu_addr = 0;
reg  [15:0] cpu_wdata = 0;
reg   [1:0] cpu_be = 2'b11;
wire [15:0] cpu_rdata;
wire [15:0] vid_addr;
wire [15:0] vid_rdata;

wire [15:0] r1ff00, r1ff02, r1ff04, r1ff06, r1ff5c, r1ff5e;
wire [15:0] r1ff88, r1ff8a, r1ff8c, r1ff8e;
wire [15:0] scrollfracx [0:1], scrollfracy [0:1];
wire [15:0] scrollx [0:3], scrolly [0:3], offsx [0:3], offsy [0:3];
wire [15:0] pages [0:7], zoomx [0:1], zoomy [0:1], clips [0:19];

s32_vram vram (
    .clk(clk_sys), .vid_clk(clk_ram),
    .cpu_we(cpu_we), .cpu_addr(cpu_addr), .cpu_wdata(cpu_wdata),
    .cpu_be(cpu_be), .cpu_rdata(cpu_rdata),
    .vid_addr(vid_addr), .vid_rdata(vid_rdata),
    .reg_1ff00(r1ff00), .reg_1ff02(r1ff02), .reg_1ff04(r1ff04),
    .reg_1ff06(r1ff06),
    .reg_scrollfracx(scrollfracx), .reg_scrollfracy(scrollfracy),
    .reg_scrollx(scrollx), .reg_scrolly(scrolly),
    .reg_offsx(offsx), .reg_offsy(offsy), .reg_pages(pages),
    .reg_zoomx(zoomx), .reg_zoomy(zoomy), .reg_1ff5c(r1ff5c),
    .reg_1ff5e(r1ff5e), .reg_clips(clips), .reg_1ff88(r1ff88),
    .reg_1ff8a(r1ff8a), .reg_1ff8c(r1ff8c), .reg_1ff8e(r1ff8e)
);

reg         line_start = 0;
reg  [8:0]  render_line = 0;
wire        line_done;
wire        tile_req;
wire [21:3] tile_addr;
reg         tile_ack = 0;
wire        lb_we;
wire  [2:0] lb_layer;
wire  [8:0] lb_x;
wire [13:0] lb_pix;

s32_tilemap dut (
    .clk(clk_ram), .rst(rst), .line(render_line), .line_start(line_start),
    .line_done(line_done), .mode_416(1'b0), .is_multi32(1'b0), .ext_tilebank(8'b0),
    .r1ff00(r1ff00), .r1ff02(r1ff02), .r1ff04(r1ff04),
    .r1ff06(r1ff06), .r1ff5c(r1ff5c), .r1ff5e(r1ff5e),
    .r1ff88(r1ff88), .r1ff8a(r1ff8a), .r1ff8c(r1ff8c),
    .r1ff8e(r1ff8e),
    .scrollfracx(scrollfracx), .scrollfracy(scrollfracy),
    .scrollx(scrollx), .scrolly(scrolly),
    .offsx(offsx), .offsy(offsy), .pages(pages), .zoomx(zoomx),
    .zoomy(zoomy), .clips(clips), .vram_addr(vid_addr),
    .vram_rdata(vid_rdata), .tile_req(tile_req), .tile_addr(tile_addr),
    .tile_data(64'h1111_1111_1111_1111), .tile_ack(tile_ack), .lb_we(lb_we),
    .lb_layer(lb_layer), .lb_x(lb_x), .lb_pix(lb_pix), .layer_off_o()
);

always @(posedge clk_ram) begin
    tile_ack <= 1'b0;
    if (tile_req && !tile_ack)
        tile_ack <= 1'b1;
end

integer errors = 0;
integer vis_count, vis_min, vis_max, emitted;

task automatic cpu_write(input [15:0] a, input [15:0] d);
begin
    @(negedge clk_sys);
    cpu_addr = a; cpu_wdata = d; cpu_we = 1'b1;
    @(negedge clk_sys);
    cpu_we = 1'b0;
end
endtask

task automatic reg_write(input [6:0] r, input [15:0] d);
    cpu_write(16'hff80 | {9'b0, r}, d);
endtask

// rect i: {min_x, min_y, max_x, max_y} at register words 0x30 + 4i
task automatic set_rect(input integer i, input [15:0] x0, input [15:0] y0,
                        input [15:0] x1, input [15:0] y1);
begin
    reg_write(7'h30 + 4*i + 0, x0);
    reg_write(7'h30 + 4*i + 1, y0);
    reg_write(7'h30 + 4*i + 2, x1);
    reg_write(7'h30 + 4*i + 3, y1);
end
endtask

// Render one line and collect the NBG0 (lb_layer 1) opaque-pixel span.
task automatic render(input [8:0] ln);
integer t, px;
begin
    render_line = ln;
    vis_count = 0; vis_min = 999; vis_max = -1; emitted = 0;
    @(negedge clk_ram); line_start = 1'b1;
    @(negedge clk_ram); line_start = 1'b0;
    for (t = 0; t < 40000; t = t + 1) begin
        @(posedge clk_ram);
        if (lb_we && lb_layer == 3'd1) begin
            emitted = emitted + 1;
            if (lb_pix[13]) begin
                vis_count = vis_count + 1;
                px = lb_x;   // integer: avoid an unsigned compare against -1
                if (px < vis_min) vis_min = px;
                if (px > vis_max) vis_max = px;
            end
        end
        if (line_done) t = 40000;
    end
end
endtask

task automatic expect_span(input [8*24-1:0] name, input integer want_min,
                           input integer want_max);
integer want_count;
begin
    want_count = (want_max < want_min) ? 0 : (want_max - want_min + 1);
    if (emitted != 320) begin
        $display("FAIL %0s: emitted %0d NBG0 pixels, want 320", name, emitted);
        errors = errors + 1;
    end
    else if (vis_count != want_count ||
             (want_count != 0 && (vis_min != want_min || vis_max != want_max))) begin
        $display("FAIL %0s: visible %0d [%0d..%0d], want %0d [%0d..%0d]",
                 name, vis_count, vis_min, vis_max, want_count, want_min, want_max);
        errors = errors + 1;
    end
    else
        $display("ok   %0s: visible %0d [%0d..%0d]", name, vis_count, vis_min, vis_max);
end
endtask

initial begin
    repeat (4) @(posedge clk_sys);
    rst = 0;
    repeat (4) @(posedge clk_sys);

    // NBG0 only, its clip enabled (bit 11), clip-in.  Neutral zoom.
    reg_write(7'h01, 16'h083e);
    reg_write(7'h28, 16'h0200);   // zoomx[0]  ($1FF50)
    reg_write(7'h29, 16'h0200);   // zoomy[0]
    // rect 2: whole screen from the clip registers; rect 3: x 10..20.
    set_rect(2, 16'd0, 16'd0, 16'd319, 16'd223);
    set_rect(3, 16'd10, 16'd0, 16'd20, 16'd223);
    // Table page 1 (word 0x400): clip2 line 10 = 50..100, line 11 = 0xFFFF,
    // flip index 213 = 60..70; clip3 line 10 = 200..250.
    cpu_write(16'h0400 + 10,  16'd50);  cpu_write(16'h0600 + 10,  16'd100);
    cpu_write(16'h0400 + 11,  16'hffff); cpu_write(16'h0600 + 11, 16'd100);
    cpu_write(16'h0400 + 213, 16'd60);  cpu_write(16'h0600 + 213, 16'd70);
    cpu_write(16'h0500 + 10,  16'd200); cpu_write(16'h0700 + 10,  16'd250);
    // y-range gate: rect 2 limited to lines 0..9 must still drop line 10
    // below once restored to 0..223.

    // ---- clip 2 selected ----
    reg_write(7'h03, 16'h0004);   // NBG0 mask = rect 2

    reg_write(7'h02, 16'h0400);   // table page 1, per-line off
    render(9'd10); expect_span("c2 register rect", 0, 319);

    reg_write(7'h02, 16'h0410);   // per-line clip 2 on
    render(9'd10); expect_span("c2 per-line 50..100", 50, 100);
    render(9'd11); expect_span("c2 per-line 0xFFFF", 1, 0);

    reg_write(7'h3b, 16'd9);      // rect 2 max_y = 9: y gate still applies
    render(9'd10); expect_span("c2 per-line y gated", 1, 0);
    reg_write(7'h3b, 16'd223);

    reg_write(7'h02, 16'h0420);   // only clip 3 per-line: rect 2 unchanged
    render(9'd10); expect_span("c2 untouched by bit5", 0, 319);

    reg_write(7'h00, 16'h0200);   // game flip: index 223-10 = 213, x mirrored
    reg_write(7'h02, 16'h0410);
    render(9'd10); expect_span("c2 flip 60..70", 319 - 70, 319 - 60);
    reg_write(7'h00, 16'h0000);

    // ---- clip 3 selected ----
    reg_write(7'h03, 16'h0008);   // NBG0 mask = rect 3
    reg_write(7'h02, 16'h0400);
    render(9'd10); expect_span("c3 register rect", 10, 20);
    reg_write(7'h02, 16'h0420);
    render(9'd10); expect_span("c3 per-line 200..250", 200, 250);
    reg_write(7'h02, 16'h0410);   // only clip 2 per-line: rect 3 unchanged
    render(9'd10); expect_span("c3 untouched by bit4", 10, 20);

    // ---- clip-out with per-line clip 2 ----
    reg_write(7'h03, 16'h0004);
    reg_write(7'h01, 16'h087e);   // bit 6: NBG0 clip-out
    reg_write(7'h02, 16'h0410);
    render(9'd10);
    if (emitted != 320 || vis_count != 320 - 51) begin
        $display("FAIL clip-out: visible %0d, want %0d", vis_count, 320 - 51);
        errors = errors + 1;
    end
    else $display("ok   clip-out: visible %0d", vis_count);

    if (errors == 0) $display("TILEMAP LINECLIP PASS");
    else             $display("TILEMAP LINECLIP FAIL (%0d errors)", errors);
    $finish;
end

endmodule
