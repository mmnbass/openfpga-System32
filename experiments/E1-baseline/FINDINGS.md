# E1 findings — 22 Oct 2026

Run: GitHub Actions run 39782778035 (all 6 jobs succeeded). Quartus 17.1.0
Build 590 Lite, `raetro/quartus:17.1`, device 5CSEBA6U23I7. Upstream s32 @
`accd644`. Seeds 1-3 x configs `vp` and `sr`. Each fit took ~27-36 min on
a 2-core runner. Raw numbers: [`reports/E1/RESULTS.md`](../../reports/E1/RESULTS.md).

Grades: **M** measured in this run, **D** derived from measured parts,
**I** inferred, **S** speculative.

## 1. Headline

The GA2-specialized core needs **~28,000 ALMs**, **484 M10K** and **37 DSP**
on the DE10-Nano die (M). Timing is met on every clock with positive setup
and hold slack (M).

The ALMs are concentrated in one block: **the V60 CPU is 17,824 ALMs (64%)
(M)**. Everything else in the core together is **~10,200 ALMs (D)**.

The figure exceeds the ~27K mark, so per the brief it is flagged for
reassessment with the breakdown below, not treated as a cancellation.

## 2. Per-block resources (vp, mean of 3 seeds; range in RESULTS.md)

| Block | ALMs | Share | M10K | DSP | Notes |
| --- | --- | --- | --- | --- | --- |
| **V60 CPU** | **17,824** | **63.7%** | 0 | 15 | 26,898 comb. ALUTs, 3,972 registers, no MLAB; FP absent (no `fp_a` in netlist) |
| Audio subsystem | 3,520 | 12.6% | 94 | 5 | T80 1,077 · JT12 714 + 706 · RF5C68 564 (+64 M10K wave RAM) · mixer 251 · glue ~210 |
| V25 (real) | 1,976 | 7.1% | 20 | 5 | s80x86 `Core` 1,754 · program line buffer 60 · RAMs |
| Mixer | 1,192 | 4.3% | 0 | 3 | |
| Sprite engine | 1,101 | 3.9% | 1 | 1 | `sr`: 1,056; the gap is method noise on a small block |
| Tilemap engine | 784 | 2.8% | 0 | 8 | includes 66-ALM serial scale divider |
| s32_core own logic | 585 | 2.1% | 0 | 0 | decode, glue |
| I/O, EEPROM, intc, 8255 | 341 | 1.2% | 1 | 0 | |
| V60 ROM cache | 282 | 1.0% | 2 | 0 | |
| VRAM + video regs | 201 | 0.7% | 128 | 0 | |
| Sprite RAM | 68 | 0.2% | 128 | 0 | |
| Palette | 50 | 0.2% | 32 | 0 | |
| Line buffers, CRT, protection glue, work RAM | 63 | 0.2% | 76 | 0 | work RAM 64 M10K |
| **s32_core total** | **27,989** | 100% | **484** | **37** | seeds 27,947-28,013 |

Why the V60 is large (M, Quartus synthesis statistics, indicative only):
the multiplexer-restructuring table lists 294 V60 muxes with a baseline cost
of ~48K LEs. Examples: a 132:1 x 22-bit selector on `dbus_addr`, 172:1 on
`dbus_wdata`, 163:1 and 60:1 x 32-bit state selectors. This is the footprint
of a single wide FSM in which many states drive the bus and datapath
directly. It is consistent with upstream's own description of the core as a
serial microsequencer.

## 3. Against the earlier estimates

| Quantity | 13/21 Oct estimate (grade then) | Measured now |
| --- | --- | --- |
| GA2 game logic excl. memory controllers | 22-24K (I) | **28.0K (M)**, higher |
| V60 | 6-8K (S) | **17.8K (M)**, 2.2-3x higher |
| Everything except V60 | ~16K (S, implied) | **10.2K (D)**, much lower |
| V25 | 2-3K (S) | 2.0K (M) |
| 2 x JT12 | 2.5-4K (S) | 1.4K (M) |
| On-chip M10K after moving work, sprite and wave RAM to SRAM | ~235 (D) | **228 (D from M)**: 484 - 64 - 128 - 64 |
| DSP | "not a constraint" | 37 of 66 Pocket DSP (M on DE10 die) |

The total was underestimated by ~15%, but its composition was wrong by much
more. The excess is not spread across the system. It sits almost entirely in
one replaceable block.

## 4. What this means for the Pocket (assessment)

Budget for game logic on the Pocket (I, unchanged method from 21 Oct):

```
0.9 x 18,480 - ~600 (APF) - 2,500..3,500 (memory controllers, not yet measured) = ~12,500..13,500 ALMs
```

- **Non-V60 logic, 10.2K (D), fits inside that budget** with ~2.3-3.3K to
  spare. The Oct studies feared the video, audio and V25 side was the
  problem. It is not.
- **That leaves ~2.3-3.3K ALMs for the CPU (I)**, against a measured 17.8K.
  Closing it needs a V60 about 5-7x smaller. That is a **new V60
  implementation**, not an optimization of the existing one. The low-risk
  patch list from 21 Oct (L1-L5) cannot close a ~14.5K gap.
- **Reference sizes measured in this run (M):** a microcoded 80186 (s80x86
  `Core`) costs 1,754 ALMs and a Z80 (T80) 1,077. The V60 is a much larger
  ISA: 32-bit, 32 registers, orthogonal addressing, string, bit-field and
  decimal groups. Whether it can be built in ~3-4.5K ALMs while keeping or
  improving today's throughput is **unknown (S)**. That question is the
  project now.
- **Possible headroom elsewhere (S):** one time-multiplexed YM engine
  (≤ ~0.7K), synthesis AREA vs SPEED (untested here; upstream's 6 Aug AREA
  profile is not comparable RTL). Even optimistically this raises the CPU
  budget to ~4-4.5K, not more.
- **Block RAM is solved on paper (D):** 228 M10K on chip after the SRAM
  move, 74% of 308, before the APF and controllers' buffers.
- **Timing:** comfortable on I7 (+0.67 ns worst at 96.6 MHz, Fmax
  104-109 MHz; `clk_sys` Fmax 52-56 MHz vs 48.3). C8 behaviour is unmeasured
  (E2). These margins (~7-13% at 96.6 MHz) are of the same order as the
  usual I7-to-C8 penalty, so C8 closure is not assured (S).
- **Accuracy constraint still applies.** Upstream's V60 already shows
  stronger-than-PCB slowdown (issue #15). A replacement must match or beat
  its throughput, not merely fit.

## 5. Measurement method — validated, with stated uncertainty

- Both harnesses kept the same 393 entities under `s32_core`. Synthesis is
  deterministic across seeds within a harness (identical map counts).
- Between harnesses, map-level `s32_core` differs by +155 ALUTs (0.36%) and
  +92 registers. These are small optimization differences spread over V60,
  tilemap, sprite and mixer, not pruning.
- Fitted `s32_core` ALMs: `vp` 27,989 (27,947-28,013), `sr` 28,090
  (28,039-28,179). Means differ by 102 ALMs (0.36%), comparable to the
  seed spread.
- **Stated uncertainty:** ±0.5% on the core total. Up to ~±4% on blocks
  under ~1.2K ALMs (sprite engine `vp` 1,101 vs `sr` 1,056).
- The `vp` top entity holds 513 "ALMs containing virtual pins". They sit
  outside `s32_core` and are excluded. The figure of record is the
  `s32_core` hierarchy row, never a whole-chip-minus-pins subtraction.
- Per-entity ALM figures are the fitter's attribution of shared ALMs. They
  are not exact counts.

## 6. Limitations

- Quartus 17.1, not upstream's pinned 17.0.2. `OPTIMIZATION_TECHNIQUE
  SPEED`, as current upstream uses.
- DE10-Nano die and I7 speed grade only. No C8 data, no Pocket-device data.
- Memory controllers, loader and framebuffer interface are excluded. The
  Pocket budget's controller term is still estimated.
- Inputs GA2 never uses (ADC, trackball, gun) were left dynamic on
  purpose. With the GA2 descriptor their logic should be largely gated off,
  but any remainder is included (conservative).
- Run-to-run determinism across Quartus versions or machines was not
  tested beyond the 3 seeds.
