// SPDX-License-Identifier: GPL-3.0-or-later
//============================================================================
//  EXPERIMENT V60-SONIC-DMAP (research, simulation only so far; not fitted,
//  not in any release file list). Demand-mapped variant of sp_spriteram_win:
//  same module name, parameters and ports, so it replaces the release window
//  one for one in a private file list.
//
//  Storage is unchanged: ONE s32_big_dpram of NSLOT * 2^PAGE_BITS words
//  (default 11 x 1K words = 22 M10K), same ports, same read latency. Only the
//  address translation differs: PAGE_LIST is ignored; each slot has a page tag
//  register and a valid bit.
//
//  Allocation (clock_a, CPU port only): a write to a page that has no slot,
//  whose ENABLED byte lanes carry non-zero data, takes the next free slot and
//  is performed in the same clock. A write of zero to an unmapped page is
//  dropped (the page reads 0000 already, so that is exact). With no slot left
//  the write is dropped and counted in n_oow_wr_a ("overflow").
//
//  The NSLOT slots are a LIFETIME budget, not a cache: there is no eviction
//  and NO RESET INPUT. Tags, valid bits and the slot counter take their
//  initial values at FPGA configuration only, exactly like the RAM contents,
//  so a slot never changes page while the RAM holds data.
//
//  Invariant: while n_oow_wr_a is 0, both ports return what a full
//  2^ADDR_WIDTH-word s32_big_dpram (zero at power-up, same parameters) returns
//  on every clock. The first-write bypass below is what makes that hold for
//  the engine port in the clocks around an allocation.
//
//  Port B is the engine's read port (clock_b = 2 x clock_a, phase aligned).
//  A port-B write lands only in a page that already has a slot; it never
//  allocates (the release instance ties wren_b to 0).
//============================================================================

module sp_spriteram_win #(
    parameter integer ADDR_WIDTH = 16,
    parameter integer NUM_WORDS  = (1 << ADDR_WIDTH),
    parameter         MIXED_RDW_MODE = "OLD_DATA",
    parameter bit     PORT_B_READ_ONLY = 1'b0,
    parameter bit     MIXED_COLLISION_FWD = PORT_B_READ_ONLY,
    parameter bit     SIM_COLLISION_POISON = 1'b0,
    parameter integer PAGE_BITS = 10,          // words per page = 2^PAGE_BITS
    parameter integer NSLOT     = 11,          // slots (<= 16)
    parameter [95:0]  PAGE_LIST = 96'd0        // ignored (kept so the release instance elaborates unchanged)
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

    input                       rd_strobe_a,
    output reg           [15:0] n_oow_wr_a,    // CPU writes dropped for lack of a slot (overflow)
    output reg           [15:0] n_oow_rd_a,    // CPU read cycles of a page with no slot (answered 0000; exact while n_oow_wr_a = 0)
    output reg           [15:0] n_oow_rd_b     // engine address steps into a page with no slot (same remark)
);

localparam integer PW = ADDR_WIDTH - PAGE_BITS;

reg [PW-1:0] tag [0:NSLOT-1];
reg [NSLOT-1:0] tag_v;
reg [4:0]    n_used;                           // slots taken, 0..NSLOT
integer i;
initial begin
    for (i = 0; i < NSLOT; i = i + 1) tag[i] = {PW{1'b0}};
    tag_v = {NSLOT{1'b0}}; n_used = 5'd0;
    n_oow_wr_a = 16'd0; n_oow_rd_a = 16'd0; n_oow_rd_b = 16'd0;
end

// {hit, slot[3:0]} of a page number
function automatic [4:0] map_of(input [PW-1:0] page);
    integer k;
    begin
        map_of = 5'd0;
        for (k = 0; k < NSLOT; k = k + 1)
            if (tag_v[k] && tag[k] == page) map_of = map_of | {1'b1, k[3:0]};
    end
endfunction

wire [PW-1:0] pg_a = address_a[ADDR_WIDTH-1:PAGE_BITS];
wire [PW-1:0] pg_b = address_b[ADDR_WIDTH-1:PAGE_BITS];
wire [4:0] ma = map_of(pg_a);
wire [4:0] mb = map_of(pg_b);
wire       va = ma[4], vb = mb[4];

// CPU write classification
wire nz_a    = (byteena_a[0] && data_a[7:0] != 8'h00) || (byteena_a[1] && data_a[15:8] != 8'h00);
wire full    = (n_used == NSLOT[4:0]);
wire alloc   = wren_a && !va && nz_a && !full;         // first non-zero write to a page: take a slot, write now
wire ovf     = wren_a && !va && nz_a &&  full;         // dropped and counted
// a miss addresses slot 0 (ma[3:0] = 0), as the release window does; only the allocating write addresses the free slot
wire [3:0] slot_a = ma[3:0] | (alloc ? n_used[3:0] : 4'd0);

always @(posedge clock_a) if (alloc) begin
    tag[n_used[3:0]]   <= pg_a;
    tag_v[n_used[3:0]] <= 1'b1;
    n_used             <= n_used + 5'd1;
end

wire [15:0] ram_q_a, ram_q_b;
s32_big_dpram #(
    .ADDR_WIDTH(4 + PAGE_BITS), .NUM_WORDS(NSLOT << PAGE_BITS),
    .MIXED_RDW_MODE(MIXED_RDW_MODE), .PORT_B_READ_ONLY(PORT_B_READ_ONLY),
    .MIXED_COLLISION_FWD(MIXED_COLLISION_FWD), .SIM_COLLISION_POISON(SIM_COLLISION_POISON)
) ram (
    .clock_a(clock_a), .address_a({slot_a, address_a[PAGE_BITS-1:0]}), .data_a(data_a), .byteena_a(byteena_a),
    .wren_a(wren_a && (va || alloc)), .q_a(ram_q_a),
    .clock_b(clock_b), .address_b({mb[3:0], address_b[PAGE_BITS-1:0]}), .data_b(data_b), .byteena_b(byteena_b),
    .wren_b(wren_b && vb), .q_b(ram_q_b)
);

// The RAM output is registered from the address; qualify it with the hit flag of the same address.
reg va_q = 1'b0, vb_q = 1'b0;
always @(posedge clock_a) va_q <= va;
always @(posedge clock_b) vb_q <= vb;
assign q_a = va_q ? ram_q_a : 16'h0000;

// First-write bypass (engine port). While the allocating write is on port A the page has no tag yet, so a
// port-B read of the SAME word in those clocks would miss and return 0000, where the full RAM's mixed-port
// forwarding (s32_big_dpram g_mixed_fwd) returns the written lanes. Mirror it: enabled lanes = the CPU data,
// the other lanes = 00 (the word was never written, so its old content is 0000 by construction).
generate
if (MIXED_COLLISION_FWD) begin : g_fw
    reg        fw_r = 1'b0;
    reg [15:0] fw_d = 16'h0000;
    always @(posedge clock_b) begin
        fw_r <= alloc && (address_a == address_b);
        fw_d <= {byteena_a[1] ? data_a[15:8] : 8'h00, byteena_a[0] ? data_a[7:0] : 8'h00};
    end
    assign q_b = fw_r ? fw_d : (vb_q ? ram_q_b : 16'h0000);
end
else begin : g_nofw
    assign q_b = vb_q ? ram_q_b : 16'h0000;
end
endgenerate

// Counters (saturating)
always @(posedge clock_a) begin
    if (ovf && n_oow_wr_a != 16'hFFFF) n_oow_wr_a <= n_oow_wr_a + 1'd1;
    if (rd_strobe_a && !va && n_oow_rd_a != 16'hFFFF) n_oow_rd_a <= n_oow_rd_a + 1'd1;
end
reg [ADDR_WIDTH-1:0] addr_b_q;
always @(posedge clock_b) begin
    addr_b_q <= address_b;
    if (!vb && address_b != addr_b_q && n_oow_rd_b != 16'hFFFF) n_oow_rd_b <= n_oow_rd_b + 1'd1;
end

`ifdef SIMULATION
// simulation report: the allocation order and what was dropped
always @(posedge clock_a) if (alloc) $display("DMAP alloc slot=%0d page=%0d (word %04x)", n_used, pg_a, {pg_a, {PAGE_BITS{1'b0}}});
final begin
    $write("DMAPSUM slots_used=%0d of %0d overflow_wr=%0d unmapped_cpu_rd=%0d unmapped_eng=%0d pages=", n_used, NSLOT, n_oow_wr_a, n_oow_rd_a, n_oow_rd_b);
    for (i = 0; i < NSLOT; i = i + 1) if (tag_v[i]) $write("%0d ", tag[i]);
    $write("\n");
end
`endif

endmodule
