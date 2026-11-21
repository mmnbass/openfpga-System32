// SPDX-License-Identifier: GPL-3.0-or-later
//
// ga2_video: System 32 clock-enable video -> APF video stream, following pocket_jaguar/target/pocket/jaguar_video.sv (v2,
// proven on the Pocket): one-clock HS/VS, VS then HS three clocks later, exactly H_PIXELS written pixels per line
// (video_skip with DE high between pixels, black padding if the source line is short, crop if long), video_rgb = 0 while
// DE is low, a fixed window of V_LINES lines counted from VS (lines in that window where the source is in vblank are
// emitted black), and a free-running blank raster when the source gives no VS for ~2^21 clk_vid.
// Differences from jaguar_video: 320 x 224 (System 32 320 mode), clk_vid = clk_sys/4 = 12.079 MHz (the source writes a
// pixel every 7.5 clk_sys = 1.875 clk_vid, so there is no /1 decimation mode), fallback raster 769 x 262 (one 410-dot
// line = 768.75 clk_vid). Same synchronous /4 transfer: values latched in clk_sys, held PIPE cycles, a new pixel is
// marked with a toggle. P1 Pocket-top simulation (stage S2).
`default_nettype none
module ga2_video #(
    parameter int PIPE     = 2,
    parameter int H_PIXELS = 320,
    parameter int V_LINES  = 224,
    parameter int V_START  = 28      // HS count after the VS line; 28 = source line 0 (VS at line 233, 262 lines)
) (
    input  wire        clk_sys,
    input  wire        ce_pix,
    input  wire [23:0] rgb,
    input  wire        hs,
    input  wire        vs,
    input  wire        hblank,
    input  wire        vblank,
    input  wire        clk_vid,
    output reg  [23:0] video_rgb  = 24'h0,
    output reg         video_de   = 1'b0,
    output reg         video_skip = 1'b0,
    output reg         video_hs   = 1'b0,
    output reg         video_vs   = 1'b0,
    output reg         fallback   = 1'b1,
    output reg  [15:0] n_blank_lines = 0    // window lines emitted black because the source was in vblank (expect 0)
);
    // hblank/vblank are latched WITH the pixel on ce_pix, so a pixel and its blanking state travel together (the pairing
    // the core-side reference capture uses); hs/vs are passed as levels.
    reg [23:0] px_hold = 0;
    reg        px_tog  = 0, hb_hold = 1'b1, vb_hold = 1'b1;
    always @(posedge clk_sys)
        if (ce_pix) begin px_hold <= rgb; px_tog <= ~px_tog; hb_hold <= hblank; vb_hold <= vblank; end

    reg [23:0] px_pipe [0:PIPE-1];
    reg [PIPE-1:0] tog_pipe = 0, hs_pipe = 0, vs_pipe = 0, hbl_pipe = 0, vbl_pipe = 0;
    integer i;
    always @(posedge clk_sys) begin
        px_pipe[0] <= px_hold;
        for (i = 1; i < PIPE; i = i + 1) px_pipe[i] <= px_pipe[i-1];
        tog_pipe <= {tog_pipe[PIPE-2:0], px_tog};
        hs_pipe  <= {hs_pipe [PIPE-2:0], hs};
        vs_pipe  <= {vs_pipe [PIPE-2:0], vs};
        hbl_pipe <= {hbl_pipe[PIPE-2:0], hb_hold};
        vbl_pipe <= {vbl_pipe[PIPE-2:0], vb_hold};
    end

    reg [23:0] s_px  = 0;
    reg        s_tog = 0, s_hs = 0, s_vs = 0, s_hbl = 1, s_vbl = 1;
    reg        p_tog = 0, p_hs = 0, p_vs = 0, p_hbl = 1;
    always @(posedge clk_vid) begin
        s_px  <= px_pipe [PIPE-1];
        s_tog <= tog_pipe[PIPE-1];
        s_hs  <= hs_pipe [PIPE-1];
        s_vs  <= vs_pipe [PIPE-1];
        s_hbl <= hbl_pipe[PIPE-1];
        s_vbl <= vbl_pipe[PIPE-1];
        p_tog <= s_tog;  p_hs <= s_hs;  p_vs <= s_vs;  p_hbl <= s_hbl;
    end
    wire new_px   = s_tog ^ p_tog;
    wire hs_rise  = s_hs & ~p_hs;
    wire vs_rise  = s_vs & ~p_vs;
    wire hbl_fall = ~s_hbl & p_hbl;

    localparam [1:0] IDLE = 2'd0, ACTIVE = 2'd1, PAD = 2'd2;
    reg [1:0]  st = IDLE;
    reg [10:0] hcnt = 0;
    reg [10:0] h_start = 11'd100;
    reg [9:0]  vline = 10'h3FF;
    reg [9:0]  wcount = 0;
    reg        line_done = 0;
    reg        vs_pend = 0;
    reg [1:0]  hs_dly = 0;
    reg        hs_pend = 0;
    reg [20:0] wd = 0;
    reg [9:0]  fh = 0;
    reg [8:0]  fv = 0;
    reg        fb_exit = 0;
    wire in_window = (vline >= V_START) && (vline < V_START + V_LINES);

    always @(posedge clk_vid) begin
        video_vs <= 1'b0; video_hs <= 1'b0; video_de <= 1'b0; video_skip <= 1'b0; video_rgb <= 24'h0;
        hcnt <= (hcnt == 11'h7FF) ? hcnt : hcnt + 1'b1;
        if (vs_rise) vs_pend <= 1'b1;
        if (hs_rise) begin
            hcnt <= 0; line_done <= 1'b0;
            if (vs_pend) begin vs_pend <= 1'b0; vline <= 0; hs_dly <= 2'd3; end
            else begin vline <= (vline == 10'h3FF) ? vline : vline + 1'b1; hs_pend <= 1'b1; end
        end
        if (hs_dly != 0) begin
            hs_dly <= hs_dly - 1'b1;
            if (hs_dly == 2'd3) video_vs <= 1'b1;
            if (hs_dly == 2'd1) hs_pend  <= 1'b1;
        end
        if (hs_pend && st == IDLE && hs_dly == 0) begin video_hs <= 1'b1; hs_pend <= 1'b0; end
        if (hbl_fall && ~s_vbl) h_start <= hcnt;
        case (st)
        IDLE: if (in_window && ~line_done && ~hs_pend && hs_dly == 0) begin
            if (~s_vbl && hbl_fall) begin
                // the first active pixel arrives together with the (latched) hblank fall: write it now
                st <= ACTIVE; line_done <= 1'b1;
                video_de <= 1'b1; video_rgb <= s_px; wcount <= 10'd1;
            end
            // a window line in source vblank is padded black a few clocks AFTER the learned start: the source line start
            // moves by +-1 clk_vid between frames (768.75 clk_vid per line), so padding exactly at h_start pre-empted a real
            // line 0 on every second frame (found in this sim: row 0 black on alternate frames)
            else if (s_vbl && hcnt == h_start + 11'd4) begin st <= PAD; wcount <= 0; line_done <= 1'b1; n_blank_lines <= n_blank_lines + 1'b1; end
        end
        ACTIVE: begin
            video_de <= 1'b1;
            if (s_hbl || hs_rise) begin video_skip <= 1'b1; st <= hs_rise ? IDLE : PAD; end
            else if (new_px) begin
                video_rgb <= s_px; wcount <= wcount + 1'b1;
                if (wcount == 10'(H_PIXELS - 1)) st <= IDLE;
            end else video_skip <= 1'b1;
        end
        PAD: begin
            if (wcount < 10'(H_PIXELS) && ~hs_rise) begin
                video_de <= 1'b1; wcount <= wcount + 1'b1;
                if (wcount == 10'(H_PIXELS - 1)) st <= IDLE;
            end else st <= IDLE;
        end
        default: st <= IDLE;
        endcase
        // fallback raster
        if (vs_rise) begin wd <= 0; if (fallback) fb_exit <= 1'b1; end
        else if (~&wd) wd <= wd + 1'b1;
        else begin fallback <= 1'b1; fb_exit <= 1'b0; end
        fh <= (fh == 10'd768) ? 10'd0 : fh + 1'b1;
        if (fh == 10'd768) fv <= (fv == 9'd261) ? 9'd0 : fv + 1'b1;
        if (fallback && fb_exit && fv == 9'd0 && fh == 10'd0) begin
            fallback <= 1'b0; fb_exit <= 1'b0; vs_pend <= 1'b0;
            video_vs <= 1'b0; video_hs <= 1'b0; video_de <= 1'b0; video_skip <= 1'b0; video_rgb <= 24'h0;
        end else if (fallback) begin
            st <= IDLE; vs_pend <= 1'b0; hs_pend <= 1'b0; hs_dly <= 2'd0;
            video_vs <= (fv == 9'd0 && fh == 10'd0);
            video_hs <= (fh == 10'd3);
            video_de <= (fh >= 10'd120) && (fh < 10'd120 + 10'(H_PIXELS)) && (fv >= 9'(V_START)) && (fv < 9'(V_START + V_LINES));
            video_skip <= 1'b0; video_rgb <= 24'h0;
        end
    end
endmodule
`default_nettype wire
