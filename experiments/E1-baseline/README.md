# E1 — GA2-specialized resource baseline

**Question.** How many ALMs, M10K blocks and DSP blocks does the upstream
System 32 core need when specialized to Golden Axe: The Revenge of Death
Adder? How are they split between V60, V25, audio, sprite, tilemap, mixer and
the rest, and what timing slack does each clock domain have? The core keeps
the real V25 and has V60 floating point removed. It is measured on the
DE10-Nano die (5CSEBA6U23I7), so the numbers are comparable with upstream's
own fit history.

**Not in scope.** C8 timing (E2), the Pocket device, ablations, memory
controllers (`sdram.sv`, `s32_fb_if.sv`, loader), optimization patches, APF.

## Method

- `references/s32` @ `accd644`, compiled unmodified.
- `rtl/ga2_core_harness.sv` instantiates `s32_core` directly. There is no
  MiSTer framework (`sys_top`, scaler, OSD, `hps_io`) and no MiSTer `emu` top.
  - **Board descriptor** is the GA2 constant decoded from the MRA bytes
    `22 00 00 83 00`: real V25 with the ga2 opcode table, i8255, sprite bank
    mask 3, no ADC, no protection HLE. Synthesis prunes what GA2 cannot reach,
    as a dedicated build would.
  - **Constants tied exactly as upstream's MiSTer top ties them:**
    `fast_v60 = 1`; `in_portc_b`, `in_svc12_b`, `in_svc34_b` = `8'hff`;
    `out_lamps` unconnected.
  - **Every other port is exposed** (664 input bits, 361 output bits) so
    nothing is pruned for lack of a driver or a load. That includes inputs GA2
    does not use (ADC, trackball, gun): if descriptor tying does not remove
    their logic, the measurement shows it rather than hiding it.
- **Settings** are copied from upstream `Arcade-SegaSystem32.qsf` by
  `gen_project.py`, which records what it copied and dropped in `run.json`.
  - Dropped: per-build bookkeeping (seed, output dir, RBF, smart recompile,
    parallelism) and `MISTER_*` framework macros.
  - Note: upstream now uses `OPTIMIZATION_TECHNIQUE SPEED`; the 6 Aug GA2
    profile used AREA. E1 follows current upstream.
- **Sources** are upstream `files.qip` plus the sources the upstream QSF adds,
  minus `sys/`, the MiSTer PLL, and the `emu` top.
- **Constraints** (`ga2_e1.sdc`): the upstream PLL frequencies as top-level
  clocks, plus upstream's V60 two-cycle, sprite two-cycle and V25
  asynchronous-group exceptions, copied. Missing register sets are an error
  after synthesis, not a silent skip.
- **Runs**: Quartus 17.1 Lite (`raetro/quartus:17.1`). Seeds 1, 2, 3 for each
  of two harness configurations (below). Upstream pins 17.0.2; 17.1 is the
  nearest image available and is what the Jaguar M0 baseline used.

## Validating the measurement method

Exposing ~1,000 signals is itself a measurement artefact, so E1 runs two
independent harnesses and compares them:

| Config | How core I/O is kept alive | Artefact |
| --- | --- | --- |
| `vp` (primary) | Each signal is a top-level port marked `VIRTUAL_PIN` | Quartus builds a logic cell per virtual pin (in the top entity). The fitter may pack those cells into ALMs shared with core logic, which perturbs the fractional per-entity ALM attribution. |
| `sr` (cross-check) | Inputs from a serial shift chain, outputs into a parallel-load chain; no virtual pins | ~1,025 chain registers in the top entity, false-pathed in the SDC. Different packing neighbours for the core's boundary logic. |

What is compared (`tools/e1_compare.py`):

1. **Map-level** `s32_core` combinational ALUTs and registers. These are
   placement-independent. If they are identical between `vp` and `sr`, both
   harnesses kept exactly the same logic.
2. **Fitted** `s32_core` "ALMs needed", against the seed-to-seed spread
   inside each configuration. A `vp`-`sr` difference within that spread
   means the harness choice is not distorting the core figure.
3. **Block-sum conservation**: the named blocks plus "other" must add up to
   the `s32_core` total.

**What the method cannot settle.**

- Fitted per-entity ALM figures are attributions of shared ALMs, not exact
  counts. They are reported to the nearest ALM, but the uncertainty is the
  observed seed spread plus any `vp`/`sr` gap.
- Any game-logic figure derived by subtracting top-entity cells from the
  whole-chip total is **not** used. The `s32_core` hierarchy row is the
  figure of record.

## Exit criteria

- [ ] All six runs complete synthesis and pass the map gate: real V25 and V60 present, no V25 HLE, no MultiPCM, block RAM inferred, registers < 50K.
- [ ] Fits succeed: virtual pins = 1,025 (`vp`) / 0 (`sr`).
- [ ] `RESULTS.md` gives per-block fitted ALM/M10K/DSP with seed ranges, and worst setup/hold slack per clock.
- [ ] The `vp`/`sr` cross-check is reported, with the residual uncertainty stated.

**Outcomes.**

- A fit that fails for lack of resources on the DE10-Nano die is a result too: it would contradict upstream's own history and must be explained.
- A game-logic figure above ~27K ALMs is flagged for reassessment against the per-module breakdown. It is not an automatic cancellation.

## Reproduce

```
git submodule update --init
make e1-lint                      # Verilator pre-flight, no Quartus
make e1-local CFG=vp SEED=1       # one run, native Quartus or Docker
# or: GitHub Actions -> "E1 — GA2 resource baseline" -> Run workflow
make e1-compare                   # after reports/E1/<run>/ exist
```

## Files

| File | Role |
| --- | --- |
| `rtl/ga2_core_harness.sv` | The only place `s32_core` is instantiated; GA2 ties |
| `rtl/ga2_e1_vp_top.sv`, `rtl/ga2_e1_sr_top.sv` | The two measurement tops |
| `ga2_e1.sdc` | Clocks and copied core exceptions |
| `gen_project.py` | QSF/QPF/run.json generator; refuses a dirty or mis-pinned upstream |
| `lint.py` | Verilator elaboration pre-flight |
| `run_local.sh` | Same steps and gates as CI, locally |
