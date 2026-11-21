//============================================================================
//  sp_spriteram_win: on-chip window onto System 32's 128 KB sprite RAM
//  (exp/sprite-path, page map revised on exp/sprite-int from the MAME census).
//  Same parameters and ports as upstream s32_big_dpram (and F2's
//  f2_xram_standin), so it replaces the sprite_ram instance in
//  s32_core_d2_fit.sv one for one.
//
//  The 64K-word space is cut into 2^(16-PAGE_BITS) pages of 2^PAGE_BITS words.
//  PAGE_LIST names the pages that have a slot: slot k holds page PAGE_LIST[6k +: 6]
//  (page number = word address >> PAGE_BITS). The slots are the 2^PAGE_BITS-word
//  slices of ONE upstream s32_big_dpram of NSLOT * 2^PAGE_BITS words.
//  Default (PAGE_BITS 10 = 1K-word pages, NSLOT 11 = 22 M10K): pages at words
//  0x0000, 0x4000 (sprite list / indirect tables), 0x8000..0x9C00 (eight pages,
//  from-RAM pixel data) and 0xA000 (CPU block copied at the scene transition
//  around frame 293/328, never read by the engine). Evidence:
//  experiments/SP-sprite-path/tools/census_report.py / census_fbt.py and the
//  README section "Sprite-RAM page map".
//
//  Outside the window: CPU writes are dropped, reads (both ports) return 0000,
//  and each event is counted (saturating) so a scene that leaves the census
//  is visible on the diagnostic band and in the sim summaries rather than
//  silently mis-drawn.  Invariant: while the CPU-write counter is 0, every
//  out-of-window word is still its power-up 0000, so engine reads of it
//  (counter n_oow_rd_b, informational only) are exact.
//============================================================================

module sp_spriteram_win #(
    parameter integer ADDR_WIDTH = 16,
    parameter integer NUM_WORDS  = (1 << ADDR_WIDTH),
    parameter         MIXED_RDW_MODE = "OLD_DATA",
    parameter bit     PORT_B_READ_ONLY = 1'b0,
    parameter bit     MIXED_COLLISION_FWD = PORT_B_READ_ONLY,
    parameter bit     SIM_COLLISION_POISON = 1'b0,
    parameter integer PAGE_BITS = 10,          // words per page = 2^PAGE_BITS (10: 1K words = 2 M10K)
    parameter integer NSLOT     = 11,          // pages with a slot (<= 16)
    // slot k -> page number PAGE_LIST[6k+5:6k]; default: words 0x0000, 0x4000, 0x8000..0x9C00 step 0x400, 0xA000
    parameter [95:0]  PAGE_LIST = {6'd40, 6'd39, 6'd38, 6'd37, 6'd36, 6'd35, 6'd34, 6'd33, 6'd32, 6'd16, 6'd0}
) (
    input                       clock_a,
    input      [ADDR_WIDTH-1:0] address_a,
    input                [15:0] data_a,
    input                 [1:0] byteena_a,
    input                       wren_a,
    output               [15:0] q_a,

    input                       clock_b,
    input      [ADDR_WIDTH-1:0] address_b,
    input                [15:0] data_b,
    input                 [1:0] byteena_b,
    input                       wren_b,
    output               [15:0] q_b,

    // CPU read strobe (one clock per CPU read bus cycle; tie 0 if unused)
    input                       rd_strobe_a,
    // diagnostics: clock_a domain (CPU) and clock_b domain (engine)
    output reg           [15:0] n_oow_wr_a,
    output reg           [15:0] n_oow_rd_a,
    output reg           [15:0] n_oow_rd_b
);

localparam integer PW = ADDR_WIDTH - PAGE_BITS;    // page-number bits (6 for 64K words / 1K pages)

// map_of -> {valid, slot[3:0]} (slot index of the page, constant per parameter set)
function automatic [4:0] map_of(input [PW-1:0] page);
    integer k;
    begin
        map_of = 5'd0;
        for (k = 0; k < NSLOT; k = k + 1)
            if (PAGE_LIST[6*k +: 6] == page) map_of = {1'b1, k[3:0]};
    end
endfunction

wire [4:0] ma = map_of(address_a[ADDR_WIDTH-1:PAGE_BITS]);
wire [4:0] mb = map_of(address_b[ADDR_WIDTH-1:PAGE_BITS]);
wire       va = ma[4], vb = mb[4];

wire [15:0] ram_q_a, ram_q_b;
s32_big_dpram #(
    .ADDR_WIDTH(4 + PAGE_BITS), .NUM_WORDS(NSLOT << PAGE_BITS),
    .MIXED_RDW_MODE(MIXED_RDW_MODE), .PORT_B_READ_ONLY(PORT_B_READ_ONLY),
    .MIXED_COLLISION_FWD(MIXED_COLLISION_FWD), .SIM_COLLISION_POISON(SIM_COLLISION_POISON)
) ram (
    .clock_a(clock_a), .address_a({ma[3:0], address_a[PAGE_BITS-1:0]}), .data_a(data_a), .byteena_a(byteena_a),
    .wren_a(wren_a && va), .q_a(ram_q_a),
    .clock_b(clock_b), .address_b({mb[3:0], address_b[PAGE_BITS-1:0]}), .data_b(data_b), .byteena_b(byteena_b),
    .wren_b(wren_b && vb), .q_b(ram_q_b)
);

// The RAM output is registered from the address; qualify it with the window
// flag of the same address (also registered).
reg va_q = 1'b0, vb_q = 1'b0;
always @(posedge clock_a) va_q <= va;
always @(posedge clock_b) vb_q <= vb;
assign q_a = va_q ? ram_q_a : 16'h0000;
assign q_b = vb_q ? ram_q_b : 16'h0000;

// Counters (saturating): CPU writes (one per wren_a clock) and CPU reads (one
// per rd_strobe_a clock) outside the window; engine reads per distinct
// out-of-window address it presents.
initial begin n_oow_wr_a = 16'd0; n_oow_rd_a = 16'd0; n_oow_rd_b = 16'd0; end
always @(posedge clock_a)
    if (wren_a && !va && n_oow_wr_a != 16'hFFFF) n_oow_wr_a <= n_oow_wr_a + 1'd1;
reg [ADDR_WIDTH-1:0] addr_b_q;
always @(posedge clock_b) begin
    addr_b_q <= address_b;
    // count each distinct out-of-window engine address step (not every
    // clock it is held), so the number is comparable between benches
    if (!vb && address_b != addr_b_q && n_oow_rd_b != 16'hFFFF) n_oow_rd_b <= n_oow_rd_b + 1'd1;
end
always @(posedge clock_a)
    if (rd_strobe_a && !va && n_oow_rd_a != 16'hFFFF) n_oow_rd_a <= n_oow_rd_a + 1'd1;

endmodule
