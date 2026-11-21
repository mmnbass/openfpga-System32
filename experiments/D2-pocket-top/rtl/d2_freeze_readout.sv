// SPDX-License-Identifier: GPL-3.0-or-later
//
// d2_freeze_readout (GA2Play5, D2 `sp` configuration only; exploratory): an on-screen HEX readout of the V2 CPU's state, so ONE
// photo of a hardware freeze tells a V2 trap from a bus / fetch stall and gives the PC, the micro-PC, the outstanding (or last)
// data access with its direction, the outstanding (or last) instruction fetch, the handshake flags and the opcode bytes.
// Display only: nothing here drives the CPU, the bus fabric or memory. Two modules:
//   d2_freeze_cap  (clk_sys) registers the raw observation bundles of V2 (dbg_v2, from the sp-derived v2_core) and of the bus
//                  fabric (dbg_bus, from the sp-derived s32_core) one clock after they leave the core (plain flip-flop loads, no
//                  logic on V2's nets), derives the state and keeps a SNAPSHOT that is updated once per video frame (on a toggle
//                  from the band, two-flop synchronised): the snapshot is constant while the band draws it, so a running CPU
//                  gives a consistent value per frame and a frozen one a static value.
//   d2_freeze_band (clk_vid) two-flop synchronises every snapshot bit (display only; static while frozen) and draws one text
//                  line of 53 cells over the APF stream: DE lines ROW0..ROW0+8 (default 211-219, directly above d2_diag_band's
//                  220-223), x = X0 .. X0+317 (default 1..318). Every pixel of that rectangle is drawn (black background, glyphs
//                  in colour); every other pixel is rgb_in unchanged. Own 5x7 pixel font (glyph table below; no third-party font).
//                  Position counting as d2_diag_band (written pixels of the outgoing stream), so it also works on the fallback raster.
//
// Text line (cell k at x = X0 + 6k; glyph columns 1..5 of a cell, rows ROW0+1..ROW0+7; labels amber, values white):
//   k  1     'P'   2-7   PC       instruction-start PC (V2 ipc[23:0]); while TRAP is sticky: the PC latched at the trap
//   k  9-11  state RUN (green)  an instruction dispatched within the last 2^RUN_LOG2 clk_sys (2^20 = 21.7 ms)
//                  TRP (red)    V2 executes an unconditional micro-jump to itself (the microcode's trap / halt self-loops: trap,
//                               trap_mode, trap_immdest, trap_priv, trap_regid, halt); sticky until CPU reset
//                  BUS (orange) no dispatch for 2^RUN_LOG2 clk_sys and V2 is stalled on its data bus unit (data_busy)
//                  FCH (orange) ... waiting for instruction bytes: stalled on fetch_short, or a wide fetch request is
//                               outstanding (with V2's production microcode a starved fetch queue shows as the dispatch
//                               word polling decode-ready, U = 000, not as a fetch_short stall: focused bench, mode fstall)
//                  LOP (orange) ... none of these: the micro-PC is cycling with no request outstanding (a poll loop that
//                               never ends)
//                  RST (blue)   the CPU is held in reset
//   k 13     'U'   14-16 UPC      V2 micro-PC (on TRP: the trap word's address; see the microcode's m1_symbols.txt)
//   k 18     'M'   19 R/W  20-25 data access: V2 bus_addr[23:0] of the outstanding (or last) data request and its direction
//   k 27     'Q'   28-33  fetch:  V2 if_addr[23:0] of the outstanding (or last) wide instruction-fetch request
//   k 35     'H'   36    flag digit 1: bit3 TRAP sticky, bit2 fetch_short, bit1 data_busy, bit0 work-RAM (SRAM) read pending
//                  37    flag digit 2: bit3 fetch request pending (if_req & !if_ack), bit2 V2 data request pending (bus_req &
//                        !bus_ack), bit1 16-bit board bus pending (m_req & !m_ack), bit0 SDRAM p0 (program-ROM cache fill) pending
//   k 39     'I'   40-47 the 4 bytes at the instruction head when the last instruction dispatched, in memory order (opcode first)
//   k 49     'T'   50-51 time since the last dispatch in units of 2^20 clk_sys (21.7 ms), saturating at FF (5.5 s)
//   every other cell is blank. A data address's first digit is the System 32 region: 0/1 program ROM, 2 work RAM, 3 video RAM,
//   4 sprite RAM, 5 sprite control, 7 sound/shared, 8 comm, A V25 DPRAM, C I/O, D interrupt controller, F ROM (high mirror).
//
// dbg_v2 layout (sp-derived v2_core): [95:64] window[31:0] (fetch window, byte 0 = head), [63:32] ipc, [31:20] upc,
//   [19:8] cw[19:8] (JADDR of the current word), [7:5] cw[2:0] (NXT), [4] adv, [3] sh_busy, [2] fetch_short, [1] data_busy, [0] 0.
// dbg_bus layout (sp-derived s32_core): [63:40] if_addr, [39:16] bus_addr[23:0], [15] if_req, [14] if_ack, [13] bus_req,
//   [12] bus_we, [11] bus_ack, [10] m_req, [9] m_ack, [8] irq_n, [7:0] 0.

module d2_freeze_cap #(
    parameter int RUN_LOG2 = 20
) (
    input             clk,          // clk_sys
    input             rst,          // CPU reset (core_top rst_r)
    input      [95:0] v2,
    input      [63:0] bus,
    input             p0_req, p0_ack,          // SDRAM port 0 (program ROM / instruction cache fill)
    input             sram_rd, sram_rdy,       // external work-RAM port read / sample ready
    input             frame_tgl,               // from d2_freeze_band (clk_vid): toggles once per frame
    output reg [23:0] s_pc = 24'd0,
    output reg  [2:0] s_st = 3'd5,
    output reg [11:0] s_upc = 12'd0,
    output reg        s_we = 1'b0,
    output reg [23:0] s_da = 24'd0,
    output reg [23:0] s_fa = 24'd0,
    output reg  [7:0] s_fl = 8'd0,
    output reg [31:0] s_op = 32'd0,
    output reg  [7:0] s_age = 8'd0
);
localparam [2:0] ST_RUN = 3'd0, ST_TRP = 3'd1, ST_BUS = 3'd2, ST_FCH = 3'd3, ST_LOP = 3'd4, ST_RST = 3'd5;
// stage 1: plain registers of the observed nets
reg [95:0] v2_q = 96'd0;
reg [63:0] bus_q = 64'd0;
reg        p0r_q = 1'b0, p0a_q = 1'b0, srd_q = 1'b0, srdy_q = 1'b0, rst_q = 1'b1;
always @(posedge clk) begin
    v2_q <= v2; bus_q <= bus; p0r_q <= p0_req; p0a_q <= p0_ack; srd_q <= sram_rd; srdy_q <= sram_rdy; rst_q <= rst;
end
wire [31:0] win   = v2_q[95:64];
wire [31:0] ipc   = v2_q[63:32];
wire [11:0] upc   = v2_q[31:20];
wire [11:0] jaddr = v2_q[19:8];
wire  [2:0] nxt   = v2_q[7:5];
wire        adv   = v2_q[4];
wire        fshort = v2_q[2], dbusy = v2_q[1];
wire [23:0] if_addr = bus_q[63:40], c_addr = bus_q[39:16];
wire        if_req = bus_q[15], if_ack = bus_q[14], c_req = bus_q[13], c_we = bus_q[12], c_ack = bus_q[11];
wire        m_req = bus_q[10], m_ack = bus_q[9];
wire        disp     = adv && nxt == 3'd5;             // opcode dispatch (V2 latches ipc on the same condition)
wire        selfjump = nxt == 3'd1 && jaddr == upc;    // the current micro-word jumps to itself unconditionally
// stage 2: live state
reg  [27:0] age_c = 28'd0;
reg         sticky = 1'b0;
reg  [23:0] tpc = 24'd0, da = 24'd0, fa = 24'd0;
reg  [11:0] tupc = 12'd0;
reg  [31:0] op = 32'd0;
reg         dwe = 1'b0;
always @(posedge clk) begin
    if (rst_q) begin age_c <= 28'd0; sticky <= 1'b0; end
    else begin
        if (disp) age_c <= 28'd0; else if (~&age_c) age_c <= age_c + 28'd1;
        if (selfjump && !sticky) begin sticky <= 1'b1; tpc <= ipc[23:0]; tupc <= upc; end
    end
    if (disp) op <= win;
    if (c_req) begin da <= c_addr; dwe <= c_we; end
    if (if_req) fa <= if_addr;
end
wire        running = age_c[27:RUN_LOG2] == '0;
wire  [2:0] st = rst_q ? ST_RST : (sticky || selfjump) ? ST_TRP : running ? ST_RUN : dbusy ? ST_BUS
               : (fshort || (if_req && !if_ack)) ? ST_FCH : ST_LOP;
wire  [7:0] fl = {sticky, fshort, dbusy, srd_q && !srdy_q, if_req && !if_ack, c_req && !c_ack, m_req && !m_ack, p0r_q && !p0a_q};
// snapshot, once per video frame
reg   [2:0] ft = 3'd0;
always @(posedge clk) begin
    ft <= {ft[1:0], frame_tgl};
    if (ft[2] ^ ft[1]) begin
        s_pc <= sticky ? tpc : ipc[23:0]; s_st <= st; s_upc <= sticky ? tupc : upc;
        s_we <= dwe; s_da <= da; s_fa <= fa; s_fl <= fl; s_op <= op; s_age <= age_c[27:20];
    end
end
endmodule

module d2_freeze_band #(
    parameter int ROW0 = 211,
    parameter int X0   = 1
) (
    input             clk_vid,
    input             en,
    input      [23:0] rgb_in,
    input             de, skip, vs,
    output     [23:0] rgb_out,
    output            in_ro,            // observation: this pixel is a drawn readout pixel
    output reg        frame_tgl = 1'b0,
    // snapshot (clk_sys, quasi-static)
    input      [23:0] pc,
    input       [2:0] st,
    input      [11:0] upc,
    input             we,
    input      [23:0] da, fa,
    input       [7:0] fl,
    input      [31:0] op,
    input       [7:0] age
);
localparam int NCELL = 53;
reg [135:0] m1 = 136'd0, m2 = 136'd0;
always @(posedge clk_vid) begin
    m1 <= {pc, st, upc, we, da, fa, fl, op, age};
    m2 <= m1;
end
wire [23:0] v_pc  = m2[135:112];
wire  [2:0] v_st  = m2[111:109];
wire [11:0] v_upc = m2[108:97];
wire        v_we  = m2[96];
wire [23:0] v_da  = m2[95:72];
wire [23:0] v_fa  = m2[71:48];
wire  [7:0] v_fl  = m2[47:40];
wire [31:0] v_op  = m2[39:8];
wire  [7:0] v_age = m2[7:0];
// position (as d2_diag_band) and the cell / column within the line
reg  [9:0] dx = 10'd0;
reg  [8:0] dy = 9'd0;
reg  [5:0] cpos = 6'd0;
reg  [2:0] col = 3'd0;
reg        dde = 1'b0, vs_d = 1'b0;
always @(posedge clk_vid) begin
    dde <= de; vs_d <= vs;
    if (vs && !vs_d) frame_tgl <= ~frame_tgl;
    if (vs) dy <= 9'd0;
    else if (dde && !de) dy <= dy + 1'b1;
    if (!de) begin dx <= 10'd0; cpos <= 6'd0; col <= 3'd0; end
    else if (!skip) begin
        dx <= dx + 1'b1;
        if (dx >= 10'(X0)) begin
            if (col == 3'd5) begin col <= 3'd0; cpos <= cpos + 6'd1; end
            else col <= col + 3'd1;
        end
    end
end
// character codes: 0-15 hex digits, 16 H, 17 I, 18 L, 19 M, 20 N, 21 O, 22 P, 23 Q, 24 R, 25 S, 26 T, 27 U, 28 W, 31 blank
localparam [4:0] C_B = 5'd11, C_C = 5'd12, C_F = 5'd15, C_H = 5'd16, C_I = 5'd17, C_L = 5'd18, C_M = 5'd19, C_N = 5'd20,
                 C_O = 5'd21, C_P = 5'd22, C_Q = 5'd23, C_R = 5'd24, C_S = 5'd25, C_T = 5'd26, C_U = 5'd27, C_W = 5'd28,
                 C_SP = 5'd31;
function automatic [4:0] hx(input [3:0] n); hx = {1'b0, n}; endfunction
function automatic [14:0] stword(input [2:0] s);
    case (s)
    3'd0: stword = {C_R, C_U, C_N};
    3'd1: stword = {C_T, C_R, C_P};
    3'd2: stword = {C_B, C_U, C_S};
    3'd3: stword = {C_F, C_C, C_H};
    3'd4: stword = {C_L, C_O, C_P};
    default: stword = {C_R, C_S, C_T};
    endcase
endfunction
wire [14:0] sw = stword(v_st);
reg  [4:0] ch;
reg  [1:0] cls;                 // 0 label, 1 value, 2 state
always @(*) begin
    ch = C_SP; cls = 2'd1;
    case (cpos)
    6'd1:  begin ch = C_P; cls = 2'd0; end
    6'd2:  ch = hx(v_pc[23:20]);  6'd3:  ch = hx(v_pc[19:16]);  6'd4:  ch = hx(v_pc[15:12]);
    6'd5:  ch = hx(v_pc[11:8]);   6'd6:  ch = hx(v_pc[7:4]);    6'd7:  ch = hx(v_pc[3:0]);
    6'd9:  begin ch = sw[14:10]; cls = 2'd2; end
    6'd10: begin ch = sw[9:5];   cls = 2'd2; end
    6'd11: begin ch = sw[4:0];   cls = 2'd2; end
    6'd13: begin ch = C_U; cls = 2'd0; end
    6'd14: ch = hx(v_upc[11:8]);  6'd15: ch = hx(v_upc[7:4]);   6'd16: ch = hx(v_upc[3:0]);
    6'd18: begin ch = C_M; cls = 2'd0; end
    6'd19: ch = v_we ? C_W : C_R;
    6'd20: ch = hx(v_da[23:20]);  6'd21: ch = hx(v_da[19:16]);  6'd22: ch = hx(v_da[15:12]);
    6'd23: ch = hx(v_da[11:8]);   6'd24: ch = hx(v_da[7:4]);    6'd25: ch = hx(v_da[3:0]);
    6'd27: begin ch = C_Q; cls = 2'd0; end
    6'd28: ch = hx(v_fa[23:20]);  6'd29: ch = hx(v_fa[19:16]);  6'd30: ch = hx(v_fa[15:12]);
    6'd31: ch = hx(v_fa[11:8]);   6'd32: ch = hx(v_fa[7:4]);    6'd33: ch = hx(v_fa[3:0]);
    6'd35: begin ch = C_H; cls = 2'd0; end
    6'd36: ch = hx(v_fl[7:4]);    6'd37: ch = hx(v_fl[3:0]);
    6'd39: begin ch = C_I; cls = 2'd0; end
    6'd40: ch = hx(v_op[7:4]);    6'd41: ch = hx(v_op[3:0]);    6'd42: ch = hx(v_op[15:12]);  6'd43: ch = hx(v_op[11:8]);
    6'd44: ch = hx(v_op[23:20]);  6'd45: ch = hx(v_op[19:16]);  6'd46: ch = hx(v_op[31:28]);  6'd47: ch = hx(v_op[27:24]);
    6'd49: begin ch = C_T; cls = 2'd0; end
    6'd50: ch = hx(v_age[7:4]);   6'd51: ch = hx(v_age[3:0]);
    default: ch = C_SP;
    endcase
end
// 5x7 glyphs, row 0 (top) in bits 34:30, MSB = leftmost column. The decoder (tb/decode_readout.py) parses this table.
function automatic [34:0] glyph(input [4:0] c);
    case (c)
    5'd0:  glyph = 35'b01110_10001_10011_10101_11001_10001_01110;   // 0 (slashed)
    5'd1:  glyph = 35'b00100_01100_00100_00100_00100_00100_01110;   // 1
    5'd2:  glyph = 35'b01110_10001_00001_00010_00100_01000_11111;   // 2
    5'd3:  glyph = 35'b11111_00010_00100_00010_00001_10001_01110;   // 3
    5'd4:  glyph = 35'b00010_00110_01010_10010_11111_00010_00010;   // 4
    5'd5:  glyph = 35'b11111_10000_11110_00001_00001_10001_01110;   // 5
    5'd6:  glyph = 35'b00110_01000_10000_11110_10001_10001_01110;   // 6
    5'd7:  glyph = 35'b11111_00001_00010_00100_01000_01000_01000;   // 7
    5'd8:  glyph = 35'b01110_10001_10001_01110_10001_10001_01110;   // 8
    5'd9:  glyph = 35'b01110_10001_10001_01111_00001_00010_01100;   // 9
    5'd10: glyph = 35'b01110_10001_10001_11111_10001_10001_10001;   // A
    5'd11: glyph = 35'b11110_10001_10001_11110_10001_10001_11110;   // B
    5'd12: glyph = 35'b01110_10001_10000_10000_10000_10001_01110;   // C
    5'd13: glyph = 35'b11100_10010_10001_10001_10001_10010_11100;   // D
    5'd14: glyph = 35'b11111_10000_10000_11110_10000_10000_11111;   // E
    5'd15: glyph = 35'b11111_10000_10000_11110_10000_10000_10000;   // F
    5'd16: glyph = 35'b10001_10001_10001_11111_10001_10001_10001;   // H
    5'd17: glyph = 35'b01110_00100_00100_00100_00100_00100_01110;   // I
    5'd18: glyph = 35'b10000_10000_10000_10000_10000_10000_11111;   // L
    5'd19: glyph = 35'b10001_11011_10101_10101_10001_10001_10001;   // M
    5'd20: glyph = 35'b10001_10001_11001_10101_10011_10001_10001;   // N
    5'd21: glyph = 35'b01110_10001_10001_10001_10001_10001_01110;   // O
    5'd22: glyph = 35'b11110_10001_10001_11110_10000_10000_10000;   // P
    5'd23: glyph = 35'b01110_10001_10001_10001_10101_10010_01101;   // Q
    5'd24: glyph = 35'b11110_10001_10001_11110_10100_10010_10001;   // R
    5'd25: glyph = 35'b01111_10000_10000_01110_00001_00001_11110;   // S
    5'd26: glyph = 35'b11111_00100_00100_00100_00100_00100_00100;   // T
    5'd27: glyph = 35'b10001_10001_10001_10001_10001_10001_01110;   // U
    5'd28: glyph = 35'b10001_10001_10001_10101_10101_10101_01010;   // W
    default: glyph = 35'd0;                                         // blank
    endcase
endfunction
localparam [23:0] BLK = 24'h000000, LAB = 24'hFFC000, VAL = 24'hFFFFFF,
                  S_GRN = 24'h00FF00, S_RED = 24'hFF2020, S_ORG = 24'hFF8000, S_BLU = 24'h4080FF;
wire        in_rows = (dy >= 9'(ROW0)) && (dy < 9'(ROW0 + 9));
wire        in_cols = (dx >= 10'(X0)) && (dx < 10'(X0 + 6 * NCELL));
wire        box     = en && de && !skip && in_rows && in_cols;
wire        g_row   = (dy > 9'(ROW0)) && (dy < 9'(ROW0 + 8));
wire  [8:0] ry      = dy - 9'(ROW0 + 1);
wire [34:0] g       = glyph(ch);
wire  [2:0] rr      = 3'd6 - ry[2:0];                // glyph row 0 (top) = bits 34:30
wire  [5:0] gsh     = {3'd0, rr} * 6'd5;
wire  [4:0] bits    = g[gsh +: 5];
wire        on      = g_row && col != 3'd0 && bits[3'd5 - col];
reg  [23:0] fg;
always @(*) begin
    case (cls)
    2'd0: fg = LAB;
    2'd2: fg = (v_st == 3'd0) ? S_GRN : (v_st == 3'd1) ? S_RED : (v_st == 3'd5) ? S_BLU : S_ORG;
    default: fg = VAL;
    endcase
end
assign in_ro   = box;
assign rgb_out = box ? (on ? fg : BLK) : rgb_in;
endmodule
