// Directed test: CRT Adjust output through s32_video_retime.
//
// Chain under test is the production one: s32_video (raster + fractional
// dot CE) -> crt_adjust (line buffer, read enable from the top-level
// quarter-cycle accumulator, replicated here) -> clk_sys output register ->
// s32_video_retime in clk_ram.  CRT Adjust is active on Direct Video as well
// as on the analog DAC, so its read stream must leave the core with one
// integer pixel width wherever that width exists.
//
// For each case the checker looks at every retimed CE inside a content run
// (DE high) and asserts:
//   * consecutive pixels carry consecutive source x (no dup/drop);
//   * even read period, picture clear of HSync: CE spacing == period/2
//     clk_ram exactly;
//   * otherwise (odd period, or a stretch that runs into HSync): the source
//     cadence is kept 1:1, so only the no-dup/drop rule applies;
//   * >= 250 content pixels per checked line (the path really carries video).
// Build with +define+OLD_PORTS against the pre-fix module to see the
// negative control fail (it bypassed every CRT Adjust stream).
`timescale 1ns/1ps
module tb_crt_retime;

reg clk_ram = 1'b0, clk_sys = 1'b0;
always #5  clk_ram = ~clk_ram;
always #10 clk_sys = ~clk_sys;

reg        rst;
reg        mode_416;
reg signed [4:0] hsize;
reg signed [8:0] hoffset;

// ---- source raster (real RTL) ----
wire       ce_pix, mode_active, hb, vb, hs, vs, vbs, vbe;
wire [8:0] hcnt, vcnt;
s32_video crt (
    .clk(clk_sys), .rst(rst), .mode_416(mode_416),
    .ce_pix(ce_pix), .mode_active(mode_active), .hcnt(hcnt), .vcnt(vcnt),
    .hblank(hb), .vblank(vb), .hsync(hs), .vsync(vs),
    .vblank_start(vbs), .vblank_end(vbe)
);
// pixel value = its own source x, plus the line for diagnostics
wire [23:0] src_rgb = {6'd0, vcnt, hcnt};

// ---- top-level read-enable glue (Arcade-SegaSystem32.sv) ----
wire crt_hs_ref;
reg  [7:0] crt_rd_acc;
reg        crt_hs_ref_d;
wire [7:0] crt_base_period = mode_active ? 8'd24 : 8'd30;
wire [7:0] crt_rd_period = crt_base_period + {{3{hsize[4]}}, hsize};
wire crt_rd_tick = (crt_rd_acc + 8'd4) >= {1'b0, crt_rd_period};
wire crt_hs_ref_rise = crt_hs_ref & ~crt_hs_ref_d;
always @(posedge clk_sys) begin
    if (rst) begin
        crt_rd_acc   <= 8'd0;
        crt_hs_ref_d <= 1'b0;
    end
    else begin
        crt_hs_ref_d <= crt_hs_ref;
        if (crt_hs_ref_rise)  crt_rd_acc <= 8'd0;
        else if (crt_rd_tick) crt_rd_acc <= crt_rd_acc + 8'd4 - {1'b0, crt_rd_period};
        else                  crt_rd_acc <= crt_rd_acc + 8'd4;
    end
end

wire [7:0] cr, cg, cb;
wire c_hs, c_vs, c_hb, c_vb;
crt_adjust #(.VTOTAL(262), .HTOTAL(512), .HPOS_MODE(0), .AW(10)) u_crt (
    .clk(clk_sys), .pxl_cen(ce_pix), .pxl2_cen(crt_rd_tick), .active(1'b1),
    .hsize(hsize), .hoffset(hoffset), .voffset(6'sd0),
    .r_in(src_rgb[23:16]), .g_in(src_rgb[15:8]), .b_in(src_rgb[7:0]),
    .hs_in(hs), .vs_in(vs), .hb_in(hb | vb), .vb_in(vb),
    .r_out(cr), .g_out(cg), .b_out(cb),
    .hs_out(c_hs), .vs_out(c_vs), .hb_out(c_hb), .vb_out(c_vb),
    .hs_ref_out(crt_hs_ref)
);

// clk_sys output register in front of the retimer (as in the top)
reg [23:0] s_rgb; reg s_ce, s_hs, s_vs, s_de;
always @(posedge clk_sys) begin
    s_rgb <= {cr, cg, cb};
    s_ce  <= crt_rd_tick;
    s_hs  <= c_hs;
    s_vs  <= c_vs;
    s_de  <= ~c_hb;   // content window (the top's OSD DE is a separate marker)
end

wire        o_ce, o_hs, o_vs, o_de;
wire [23:0] o_rgb;
s32_video_retime dut (
    .clk(clk_ram), .rst(rst), .mode_416(mode_active),
`ifdef OLD_PORTS
    .bypass(1'b1),
`else
    .crt_active(1'b1), .crt_period(crt_rd_period),
`endif
    .raw_ce(s_ce), .raw_rgb(s_rgb), .raw_hs(s_hs), .raw_vs(s_vs), .raw_de(s_de),
    .ce(o_ce), .rgb(o_rgb), .hs(o_hs), .vs(o_vs), .de(o_de)
);

// ---- checker ----
integer errors = 0;
integer spacing, since_ce;
integer first_x, last_x, run_first, run_last;
integer run_len, prev_x, lines_checked, sp_min, sp_max, hs_overlaps, run_min;
reg     checking, prev_de, o_hs_d, run_whole;
wire [8:0] o_x = o_rgb[8:0];

always @(posedge clk_ram) begin
    since_ce <= since_ce + 1;
    o_hs_d <= o_hs;
    if (checking && o_hs && !o_hs_d && o_de) hs_overlaps = hs_overlaps + 1;
    if (o_ce) begin
        since_ce <= 1;
        if (checking) begin
            if (o_de && prev_de) begin
                if (o_x != prev_x + 1) begin
                    errors = errors + 1;
                    if (errors < 12)
                        $display("FAIL: h=%0d off=%0d m416=%0d x %0d -> %0d (dup/drop) line %0d",
                                 hsize, hoffset, mode_active, prev_x, o_x, o_rgb[17:9]);
                end
                if (since_ce < sp_min) sp_min = since_ce;
                if (since_ce > sp_max) sp_max = since_ce;
            end
            if (o_de && !prev_de) begin first_x = o_x; run_whole = 1; end
            if (o_de) begin run_len = run_len + 1; last_x = o_x; end
            if (!o_de && prev_de) begin
                if (run_whole && run_len < run_min) begin run_min = run_len; run_first = first_x; run_last = last_x; end
                if (run_whole && run_len < 250) begin
                    errors = errors + 1;
                    $display("FAIL: h=%0d off=%0d content run only %0d pixels", hsize, hoffset, run_len);
                end
                lines_checked = lines_checked + 1;
                run_len = 0;
            end
        end
        prev_de <= o_de;
        prev_x  <= o_x;
    end
end

task run_case(input m, input signed [4:0] hs_sz, input signed [8:0] hoff);
    integer per2;
    begin
        rst = 1; mode_416 = m; hsize = hs_sz; hoffset = hoff;
        checking = 0;
        repeat (40) @(posedge clk_ram);
        rst = 0;
        // let the raster settle and the line buffer fill (a few lines)
        repeat (4 * 6200) @(posedge clk_ram);
        sp_min = 1000; sp_max = 0; run_len = 0; lines_checked = 0;
        hs_overlaps = 0; run_min = 1000; run_whole = 0;
        checking = 1;
        repeat (12 * 6200) @(posedge clk_ram);
        checking = 0;
        per2 = crt_rd_period;   // quarter-clk_sys == half-clk_ram units
        if (lines_checked < 10) begin
            errors = errors + 1;
            $display("FAIL: h=%0d only %0d lines checked", hs_sz, lines_checked);
        end
        if (per2 % 2 == 0 && hs_overlaps == 0) begin
            if (sp_min != per2 / 2 || sp_max != per2 / 2) begin
                errors = errors + 1;
                $display("FAIL: m416=%0d h=%0d off=%0d period %0d clk expected, spacing %0d..%0d",
                         m, hs_sz, hoff, per2 / 2, sp_min, sp_max);
            end
        end
        $display("case m416=%0d hsize=%0d hoff=%0d: period %0d/2 clk_ram, spacing %0d..%0d, %0d lines, min run %0d px (x %0d..%0d), HSync-in-picture %0d%s",
                 m, hs_sz, hoff, per2, sp_min, sp_max, lines_checked, run_min, run_first, run_last, hs_overlaps,
                 (per2 % 2 == 0 && hs_overlaps == 0) ? " [uniform]" : " [1:1]");
    end
endtask

initial begin
    since_ce = 0; prev_de = 0; prev_x = 0; checking = 0;
    rst = 1; mode_416 = 0; hsize = 0; hoffset = 0;
    run_case(1'b0,  5'sd0,  9'sd0);    // 320, H-Size 0: native 15 clk
    run_case(1'b0,  5'sd0,  9'sd6);    // 320, H-Position only
    run_case(1'b0,  5'sd2,  9'sd0);    // 320, wider: 16 clk
    run_case(1'b0, -5'sd4,  9'sd0);    // 320, narrower: 13 clk
    run_case(1'b1,  5'sd0,  9'sd0);    // 416, H-Size 0: 12 clk
    run_case(1'b1,  5'sd6, -9'sd4);    // 416, wider + left shift: 15 clk
    run_case(1'b0,  5'sd1,  9'sd0);    // 320, odd step: kept 1:1 (15/16)
    if (errors == 0) $display("CRT RETIME PASS");
    else             $display("CRT RETIME FAIL (%0d errors)", errors);
    $finish;
end

endmodule
