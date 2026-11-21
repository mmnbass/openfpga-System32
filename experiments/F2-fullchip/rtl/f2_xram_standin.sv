// SPDX-License-Identifier: GPL-3.0-or-later
//
// f2_xram_standin: FIT HARNESS ONLY. Replaces an upstream s32_big_dpram that
// the Pocket port would move to external memory (work RAM, sprite RAM), with
// the same parameters and ports, so the F2 fit measures the chip WITHOUT those
// M10Ks but WITH every signal that drives or reads them still live.
//
// It is upstream's own s32_big_dpram (unmodified, the module whose M10K
// inference upstream's builds rely on) instantiated at 512 words, addressed by
// an XOR fold of the full address, so no address bit can be swept. Run
// 40414970647 showed that the previous hand-written two-array model was not
// inferred as RAM when both ports write (4,048 ALUTs, 8,208 registers).
//
// It is NOT a functional model of external memory and has none of the logic
// external memory needs: no memory-controller client, no arbitration, no wait
// states on the CPU bus, no prefetch for the sprite engine's clk_ram reads.
// That logic is reported as missing integration (see the F2 README).

module f2_xram_standin #(
    parameter integer ADDR_WIDTH = 16,
    parameter integer NUM_WORDS  = (1 << ADDR_WIDTH),
    parameter         MIXED_RDW_MODE = "OLD_DATA",
    parameter bit     PORT_B_READ_ONLY = 1'b0,
    parameter bit     MIXED_COLLISION_FWD = PORT_B_READ_ONLY,
    parameter bit     SIM_COLLISION_POISON = 1'b0,
    parameter integer DEPTH_LOG2 = 9
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
    output               [15:0] q_b
);

function automatic [DEPTH_LOG2-1:0] fold(input [ADDR_WIDTH-1:0] a);
    integer i;
    fold = '0;
    for (i = 0; i < ADDR_WIDTH; i = i + DEPTH_LOG2)
        fold = fold ^ DEPTH_LOG2'(a >> i);
endfunction

s32_big_dpram #(
    .ADDR_WIDTH(DEPTH_LOG2), .NUM_WORDS(1 << DEPTH_LOG2),
    .MIXED_RDW_MODE(MIXED_RDW_MODE), .PORT_B_READ_ONLY(PORT_B_READ_ONLY),
    .MIXED_COLLISION_FWD(MIXED_COLLISION_FWD), .SIM_COLLISION_POISON(SIM_COLLISION_POISON)
) ram (
    .clock_a(clock_a), .address_a(fold(address_a)), .data_a(data_a), .byteena_a(byteena_a),
    .wren_a(wren_a), .q_a(q_a),
    .clock_b(clock_b), .address_b(fold(address_b)), .data_b(data_b), .byteena_b(byteena_b),
    .wren_b(wren_b), .q_b(q_b)
);

endmodule
