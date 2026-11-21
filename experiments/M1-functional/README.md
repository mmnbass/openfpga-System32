# M1 — functional V60 milestones: M1.0 differential harness

Plan: [`docs/v2-functional-plan.md`](../../docs/v2-functional-plan.md).

**M1.0 is harness and observability only.** M1.0 itself (through `fc5516c`)
made no change to V2's synthesizable RTL; its only V2 content is microcode and
decode-table data, loaded through the existing write ports. The fetch defects
it exposed, D1 and D1b, were then fixed in the fetch RTL (`dbe89f4`) and
corrected after review checkpoint C1 (see below).

## What is here

| Path | Role |
| --- | --- |
| `ref/v60_ref.py`, `ref/gen_diff_program.py`, `ref/compare.py` | Copies of upstream `verif/cosim/*` @ `accd644`, **byte-identical below a provenance header**. The pinned submodule is the authority; these copies are never edited. |
| `m1_ref.py` | Runs `v60_ref.py` on an image and prints the canonical dump: R00–R31, PSW, ordered data writes, `END halted= instrs=`. `--trace` prints each instruction's PC and opcode. |
| `m1_image.py` | Writes images (`.bin`, and the 16-bit `$readmemh` `.hex` used by the benches). `random <seed>` calls upstream's unmodified `gen()`. |
| `m1_directed.py` | Deterministic programs (`TESTS`) and documented-discrepancy probes (`PROBES`). |
| `m1_compare.py` | Category-by-category comparison of two canonical dumps. |
| `tb/tb_m1_up.sv` | Upstream `s32_v60` + `s32_v60_bus`, read in place from `references/s32`, with `S32_V60_NO_FP` as in the GA2 build and `FAST_IFETCH = 0`. |
| `tb/tb_m1_v2.sv` | V2 behind the **same** `s32_v60_bus` and memory model. |
| `tb/m1_mem.sv` | Shared 128 KB 16-bit memory with a level acknowledge, as the board-rate adapter needs. |
| `tb/v2_sim_observer.sv` | **Simulation-only** retirement port and counters, attached with `bind`. It is not in `V2-datapath-floor/rtl/files.f`. |
| `m1_microcode.py` | M1.0 microcode: reset redirect to 0, dispatch wait, HALT, NOP, and a trap for every other opcode. It uses V2's microassembler unmodified. |
| `tb/tb_d1_fetch.sv`, `run_d1.sh`, `d1_mutants.py` | The D1 fetch/redirect regression, its runner, and the plausible-incorrect fetch mutants it must catch. |
| `run_refs.sh`, `run_v2.sh`, `run_m1_0.sh` | Runners. `run_m1_0.sh [nseeds]` runs everything, including `run_d1.sh`. |
| `m1_m11.py`, `m1_m11_microcode.py`, `run_m11.sh` | M1.1: MOV.W / BR8 program set (directed, unsupported, probes, random), the M1.1 microcode, and the runner (references, then V2 per fetch configuration). |
| `m1_lockstep.py` | Lock-step comparison: one record per retired instruction (start PC, opcode, registers changed). |
| `m11_mutants.py`, `run_m11_mutants.sh` | M1.1 front-end mutants (RTL and microcode) that the M1.1 suite must catch. |
| `tb/v2_sim_checks.sv` | **Simulation-only** microcode timing checks (bound like the observer): settled lookups, lookup fields repeated by the dispatch word, format-byte branches, immediate latches, µstack depth, RAW hazards. |

**Clocking (both benches).** One `clk_sys` at 48.317 MHz. Upstream's V60 and
both bus adapters run on the board enable, every third cycle (16.1 MHz), as
in the GA2 core. V2 steps on every cycle, the provisional 48.3 MHz baseline.
Instruction bytes travel over the data bus in both, as in upstream's unit
benches.

## Reproducibility

**Historical record (`2b29ed0`, 2026-10-23, before D1).** `run_m1_0.sh 100`
was run on a fresh clone:

- 178-character checkout path;
- empty `build/`;
- submodule fetched from GitHub;
- about 25 s total.

It reproduced every M1.0 result of that commit: the reference and probe
results below, and the then-failing `nops` program. A clean clone of `dbe89f4`
reproduced the D1 results of that commit.

**Current (`2c17515` RTL, after D1, D1b, C1 round 1 and the SMC correction).**
A clean clone of `2c17515` (152-character path, empty `build/`, submodule
fetched) reproduced everything: `build/M1/cleanclone_2c17515.log`. The final
run with the round-2 test corrections is `build/M1/run_m1_0_final.log`.

An earlier clean-clone run failed every comparison. The benches had held
file paths in a 128-character register, so a long path was truncated and
`$readmemh` silently loaded nothing. Paths are now strings, and an unreadable
input file is fatal.

## Results (2026-10-23)

**References** (`run_refs.sh 100`): upstream RTL against `v60_ref.py`, on
R0–R31, PSW, ordered data writes, halt and retired-instruction count.

| Set | Result |
| --- | --- |
| 100 random seeds (upstream `gen()`: MOV-immediate, 10 word ALU/MUL/DIV ops register–register, GETPSW, absolute stores) | **100/100 agree** |
| `alu_flags`: ADD/SUB/CMP/AND/OR/XOR/NOT/NEG × B/H/W × 12 corner pairs, PSW captured after each | agree (1,748 instructions) |
| `muldiv`: MUL/MULU/DIV/DIVU × B/H/W, including 0x80000000 / −1 | agree (524) |
| `modes`: disp8/16/32[Rn], [Rn], deferred disp8[[Rn]], [Rn+], [−Rn], absolute, immediate quick/full, F1 memory-to-memory incl. RMW, B/H/W loads and stores | agree (83) |
| `branches`: Bcc8 and Bcc16, 15 conditions × taken/not-taken over 6 flag sets, forward and backward, counted loops | agree (1,018) |
| `stack_calls`: PUSH/POP (register and quick), BSR, JSR abs, return by POP + JMP [Rn], JMP [Rn] | agree (37) |

The **retired-instruction count** agrees everywhere. So upstream's
"one instruction per entry into S_DECODE" (`+PCHIST`) is a valid boundary on
these programs.

## Reference discrepancies (documented; neither reference modified)

Each has a probe in `m1_directed.py` `PROBES`, which `run_refs.sh` re-checks
still differs as described.

| Probe | Upstream RTL | `v60_ref.py` | Note |
| --- | --- | --- | --- |
| `probe_bcc_hole`: opcodes 0x6B / 0x7B | reserved-instruction exception, vector 8 (PSW pushed, vectored) | branch-never, falls through | The RTL comment says these are holes in MAME's primary dispatch table. MAME itself not checked here. GA2 relevance unknown; real code should not use them. |
| `probe_jmp_regdirect`: JMP with a register-direct operand (`D7 69`) | jumps to the register *index* (ReadAMAddress convention) | jumps to the register's *contents* | Not a meaningful V60 form. |
| `probe_ret_frame`: RET #n | pops PC, then AP (R29), then SP += n: the CALL frame, per the RTL's comment and MAME's `opRET` | pops PC only | BSR/JSR pair with RSR, which `v60_ref.py` does not implement. |

**Coverage gaps in `v60_ref.py`.**

- Its docstring lists SHA and ROT, but its opcode table has only SHL.
- It has no RSR, CALL, string, bit-field, decimal or exception handling.

The SHA carry/overflow validation (M1.5) therefore needs a SHA model added to
**our** copy, written from MAME, before the two can be compared.

**My test-design errors these exposed.** The first deterministic versions
used condition 11, JMP register-direct and BSR…RET. The agreement suite now
avoids those forms, and the probes keep the differences visible.

## V2 (`run_v2.sh`)

| Program | V2 vs `v60_ref.py` | V2 vs upstream RTL | What it shows |
| --- | --- | --- | --- |
| `halt`: `00` | MATCH | MATCH | Reset, redirect, dispatch and HALT run. Retirement reports 1 instruction, PC 0, opcode 00. **Weak evidence on its own**: memory is all zeros, which is also HALT's opcode (see below). |
| `nops`: `CD`×5, `00` | MATCH | MATCH | 6 instructions retire in order: NOP at PCs 0–4, HALT at 5. Before the D1 fix this failed: V2 dispatched `00` at PC 0 from stale data. |

**`nops` counters** (after the D1 fix; plain reset microcode).

| Instruction | Cycles | Breakdown |
| --- | --- | --- |
| NOP 0 | 30 | 4 exec + 26 fetch (waiting for the second fetch line) |
| NOP 1–4 | 4 each | consume, settle, ready check, dispatch |
| HALT | 1 | — |
| **Totals** | — | exec 21, fetch 26, mem 0, iter 0, plus 49 pre-start cycles |

The per-instruction cycles add up to the totals.

**These are cost figures for M1.0's placeholder microcode, not V2's
instruction costs.** Real front-end microcode is M1.1.

## V2 defects found by M1.0, and their fix (2026-10-23)

Fixed in `dbe89f4`; corrected and re-verified after Codex review checkpoint
C1, recorded in [`docs/reviews/C1-codex-review.md`](../../docs/reviews/C1-codex-review.md).

**D1. Prefetch request withdrawn mid-transaction on a fetch redirect.**

- **Defect.** On a redirect or SMC flush, `v2_fetch` and `v2_fetch_byteq`
  dropped `pf_req` / `if_req`, and `if_addr` followed the live fetch pointer.
- **Why that breaks upstream `s32_v60_bus`.** The adapter takes a request on
  a board enable with no acceptance signal, completes it one to three 16-bit
  cycles later, and acknowledges only while the request is still held.
- **Result.** The old read's acknowledge landed on the next request, and the
  queue accepted the stale data.
- **Fix.** A raised request is never withdrawn or re-addressed. A flush leaves
  it outstanding and marks it **stale**. Its acknowledge is consumed by the
  stale path, which drops the data and issues no second half of the line. If
  it completes in the flush cycle itself, it is retired there. Fetching
  resumes at the redirected address after that. `if_addr` is latched at issue
  (`if_addr_q`).
- **C1 correction (finding C1-1).** `dbe89f4` marked stale requests with a
  4-bit epoch. Sixteen flushes during one outstanding request wrapped it, and
  the stale data was then accepted (reproduced: the flush-storm scenario fails
  on `dbe89f4`). The epoch is replaced by a 1-bit stale flag, which no number
  of flushes can clear.
- **Matches upstream's wide-port server.** `s32_core` holds `if_ack` until
  `if_req` drops, drains invalidated fills, and returns the line shifted by
  `if_addr[2:0]`.

**D1b. `avail` underflow after a redirect to an unaligned address** (standard
`v2_fetch` only).

- **Defect.** `avail = nl*8 - head_pc[2:0]` underflowed while no line was
  held, e.g. 0 − 5 = 59, so an empty queue claimed 59 bytes.
- **How it was found.** By the D1 regression. The NOP program redirects to
  the aligned address 0, so it could not see it.
- **Fix.** `avail` is 0 while `nl = 0`.

**Regression: `run_d1.sh`** (rebuilt after C1). It drives
`tb/tb_d1_fetch.sv`: the fetch unit and `v2_bus` behind the real
`s32_v60_bus` and `m1_mem`, plus a model of `s32_core`'s wide-port server.
Each scenario resets, then fires its event at every cycle offset 1–90.
Targets alternate between ROM and non-ROM addresses, so with the wide port
enabled both ports, and the switch between them, are exercised.

| Scenario | Event |
| --- | --- |
| 0 | one redirect |
| 1 | two redirects 1–3 cycles apart, the second into the other region |
| 2 | a redirect, then another after the stale acknowledge |
| 3 | a redirect with the queue full and the bus idle |
| 4 | a reset with a prefetch outstanding |
| 5 | an SMC flush: a data write into the queued head line; odd offsets add a redirect in the same cycle |
| 6 | a flush storm: memory and wide server stalled, 14–18 redirects on consecutive cycles while one request is outstanding |

Configurations: memory wait 0, 2, and 5 + 0–4 jitter; the wide port with 0
and 3 wait states; random data reads and writes contending with prefetch
(`+dtraffic`) at wait 0 and 3 + 0–2 jitter, and with the wide port.

Checks, every cycle:

| Check | What it catches |
| --- | --- |
| Protocol | a raised request is held, unchanged, until acknowledged |
| PC | `head_pc` against an independent model (reset address or redirect target, plus the bytes consumed); catches a consistently shifted resume |
| Data | every queued byte equals memory at the modelled address: stale, misplaced, lost or reordered bytes |
| Sequence | every fetch request issued since the last flush has exactly the address and port a correct prefetcher issues next; catches re-issued, duplicated, extra-stale and skipped transactions |
| Read | bench data reads return memory |
| Progress | 24 more bytes are delivered after each event |

**Results** (current: `build/M1/run_m1_0_final.log`; per-run outputs in
`build/M1/d1/runs/`. The round-1 intermediate, before the SMC correction, is
`build/M1/run_m1_0_c1.log`):

| RTL | Result |
| --- | --- |
| current `v2_fetch` and `v2_fetch_byteq` | **PASS**, all 10 configurations each (the 8 below plus `+smc`, `+smc +wide`) |
| pre-fix (`fc5516c`), both variants | **FAIL**, every configuration: protocol violations plus data errors |
| 4-bit-epoch fix (`dbe89f4`), both variants | **FAIL**: in the 8 D1 configurations, data and sequence errors from the flush storm only |
| 15 mutants (`d1_mutants.py`), 4 of them for the SMC snoop | **each FAIL** (the SMC mutants in the `+smc` configurations) |

The mutants: D1b alone reverted; D1 reintroduced (both variants); stale
marking removed (both variants); an acknowledge in the flush cycle not
retired; the stale drop also skipping the next line; the stale request
re-issued once as a real second transaction (request low, then raised again
at the old address); the request kept raised over a held acknowledge
(`ack_extend`, caught by Protocol); a second half fetched after a stale first
half; head and fetch pointer resuming one byte past the target. Wide
transactions are counted where the server accepts them (review C1-R2-2). The pre-C1 bench (`dbe89f4`)
passed four of them (`reissue`, `stale_half`, `no_retire`, `shifted`) in each
of the three configurations tried (wait 0, wait 5 + jitter, wide port); the
new PC and Sequence checks catch them. With `+scen=<n>`, the `dbe89f4` RTL
passes scenarios 0–5 and fails scenario 6 in every configuration, for both
variants.

Coverage at the event (`fixed`, wait 0): data-bus phase idle 90, request 32,
mid-transfer 370, acknowledge cycle 25, after it 113, with 12 / 186 / 13 / 15
of the non-idle events during a line's second half; wide-port phase (`+wide`)
idle 135, request 41, server busy 336, acknowledge 42, after it 76. Data
accesses: about 10,000 per `+dtraffic` run; 90 SMC writes into the queue and
90 storms with a request outstanding per run.

**System level.** The NOP program (`CD`×5, `00`), which failed before the
fix, matches both references with the plain M1.0 microcode.

- Reset-to-first-dispatch is 49 cycles, up from 34. The fetch unit starts
  prefetching at the reset address `0xFFFFFFF0`; the M1.0 test microcode then
  redirects to 0, and that useless first transfer must complete before the
  redirected fetch starts. The old, shorter figure was produced by the defect.
  On a real V60, execution starts at `0xFFFFFFF0`, so that first transfer is
  useful; the 15 cycles are an artefact of the test convention.
- A redirect taken during an outstanding prefetch waits for that transfer.
  Under the adapter's contract a request it may have accepted must complete.
  A request still masked behind data ownership could in principle be
  cancelled, but that needs arbiter cooperation and is deferred (C1-10) to
  the branch-cost work of M1.4.

**SMC snoop corrections (C1-2, C1-2w, C1-3; approved separately, fixed
2026-10-24).** The self-modifying-code snoop predates D1. Codex round 1 found
two gaps and Claude a third:

- **C1-2:** the line being filled was excluded after its first word was
  captured;
- **C1-2w:** in `v2_fetch` the range test was empty once the queue spanned
  `0xFFFFFFFF` → 0;
- **C1-3:** only the write's start address was tested, so a write starting
  below the queued bytes but overlapping them was missed.

Both variants now flush when the write's byte range (1, 2 or 4 bytes)
overlaps any queued byte, using modular offsets. In `v2_fetch`, the queued
bytes are the valid lines plus the captured first half of the line being
filled.

The write size reaches the fetch unit as a new `v2_bus` output, `dwr_size`.
It is the same held `size` the adapter samples as `c_size`; `v2_core`
freezes its whole pipeline while `data_busy`, so it is stable for the
access. The logical bus port to `s32_v60_bus` is unchanged.

**SMC suite (`+smc`; 528 deterministic cases per run):**

| Group | Cases | What |
| --- | --- | --- |
| range | 106 | writes from head −4 to +33 bytes into a full queue, aligned and unaligned head |
| below | 8 | writes overlapping the queue start from below (C1-3) |
| half | 90 | writes from −2 to +7 bytes of the line being filled, in the cycle its first word is captured, for lines 1–3 (C1-2) |
| wrap | 204 | range and half cases with the queue spanning `0xFFFFFFFF` → 0 (C1-2w) |
| prefetch | 45 | writes while prefetch runs, the issue cycle swept over 60 offsets |
| redirect | 75 | writes with a redirect in the flush cycle, or 0–12 cycles after a redirect that left a request stale |

All sizes (byte, halfword, word) and alignments occur in every group. The
PC, Data, Sequence, Protocol and Progress checks verify the instruction bytes
and the request order, not the flush signal.

Results (`run_d1.sh`):

- **Current RTL:** passes, both variants, with and without the wide port.
  With the wide port, the 90 wrap-half cases are skipped: ROM lines are
  fetched whole, so no half-captured state arises.
- **Round-1 RTL (`05bb4ea`):**
  - `v2_fetch` fails below 4/8, half 39/90, wrap 128/204;
  - `v2_fetch_byteq` fails below 8/8, plus other cases where the write
    starts below its head.
- **Mutants:** `smc_no_half`, `smc_nonmodular`, `smc_no_width` and
  `bq_smc_no_width` each fail.

**Structural impact.** From `yosys_indicator.sh`, which gives indications, not
fitted ALMs:

| RTL | LUTs | Flip-flops |
| --- | --- | --- |
| `fc5516c` (pre-fix) | 4,490 | 1,157 |
| `dbe89f4` (D1/D1b) | 4,485 | 1,181 |
| `05bb4ea` (C1 round 1) | 4,447 | 1,174 |
| `2c17515` (SMC correction; current) | 4,542 | 1,174 |

M10K, MLAB and DSP are unchanged.

**No Quartus fit since the change.** The 2,429-ALM / 48.3 MHz result
(run 40024114327) is for the pre-fix RTL and is **not** re-established.

**D2 (open, M1.1).** The only dispatch-ready condition is "≥ 8 bytes queued",
which over-counts fetch waits.

## M1.1: the V60 instruction front end (2026-10-24)

Plan: [`docs/v2-m1.1-plan.md`](../../docs/v2-m1.1-plan.md).

**Scope.** `MOV.W` (0x2D) in every F1/F2 direction, with register direct,
`#quick` and `#imm32` operands; `BR8` (0x6A); NOP; HALT. Everything else
traps, with the kind reported. Run with `run_m11.sh 1000 "" "+memwait=2"
"+memwait=5 +memjit=4" "+wide"`, and the mutants with
`run_m11_mutants.sh 50`.

### M1.1a: reference behaviour

The two references agree on 1,031 programs, in final state and in lock-step:
1,000 random, plus 31 directed or V2-unsupported. That is 34,993 instruction
records.

**Two documented discrepancies (probes; neither reference changed):**

| Encoding | upstream RTL | `v60_ref.py` | V2 (until a decision) |
| --- | --- | --- | --- |
| MOV.W to an immediate destination (F2 D=0 `#quick`/`#imm32`; F1 operand 2 `#quick`/`#imm32`) | writes the value to the immediate taken as an address (`#3` → a write to address 3) | discards the store | traps `immdest` |
| `modm` 1, mode groups 6/7 (0xC0–0xFF) | group 6: executes some operation (wrote 0x00C5612D to R1 for `2D 61 C5`); group 7: takes an exception | NotImplementedError | traps `mode` |

Registers and instruction length agree in both cases; the immediate-destination
difference is the write stream only. Upstream's behaviour appears to follow
MAME's address-mode handling. That is my recollection, not checked against
MAME source; MAME is a third reference not in this harness.

A first register-only probe wrongly suggested the references agree on
immediate destinations; the full comparison, which includes writes, showed
they do not.

### M1.1b: lock-step tracing

Each retired instruction gives one record, `L n pc=<start pc> op=<opcode>[
Rnn=<v>...]`, listing the registers the instruction changed.

- `m1_ref.py --lockstep` and `tb_m1_up +lockstep` produce it for the
  references.
- `v2_sim_observer +lockstep` produces it for V2: GPR writes are snooped at
  the write port in E3 and charged to the instruction in E3.

`m1_lockstep.py` reports the first divergent instruction. It rejects
corrupted record sets: a changed PC, a changed register value, a dropped
record. PSW and memory writes are compared in the final dump. A per-instruction
PSW comparison arrives with M1.2.

### M1.1c: front-end RTL

| Item | Change |
| --- | --- |
| N1 | Format byte (byte 1) and opcode bit 0 latched at opcode dispatch; micro-conditions 24 F1, 25 D, 26 immediate quick |
| N2 | Mode table 512 × 24, addressed `{modm, mode byte}`; control-word field `MODM_SEL` (bits 99:98; the control word grows from 98 to 100 bits) |
| N3 | Decode-ready micro-condition 27 (see below). Closes D2: the M1.0 `nops` program's fetch waits fall from 26 to 0, and its cycles from 108 to 82. |
| N4 | Mode-byte register latched at mode dispatch |
| N5 | Instruction-start PC `ipc` latched at opcode dispatch; it is the A/B-bus and AGU "PC" source. The observer cross-checks it on every instruction (`ipc_errors`). |
| N6 | `tb/v2_sim_checks.sv` (simulation only) |

**Decode-ready as built.** The plan's single `lk_ok_q` bit was computed from
the previous word's `IMM_OFS`, so a poll could pass one cycle early. The
simulation checks caught this in M1.1d: a dispatch went ahead with only the
opcode queued.

As built, `rdy_q[k]` means that in the previous cycle bytes 0..k were queued
and the head could not move. Condition 27 selects `rdy_q[IMM_OFS]` of the
current word. That costs 8 flip-flops and an 8:1 mux on one condition input;
it is not a stall term.

**Microcode rule found in M1.1c.** A dispatch word must repeat the lookup's
`IMM_OFS` and `MODM_SEL`: a stalled word re-reads the tables with its own
fields every cycle. `v2_sim_checks` enforces this.

### M1.1d/e: MOV.W and BR8 microcode, and the results

**Microcode** (`m1_m11_microcode.py`):

- Operands are looked up through a one-word mode-dispatch stub (CALL). Every
  supported mode's routine is a single RETURN, which leaves the latched
  descriptor and register for the caller.
- F1 operand 2 starts at window offset 0, because operand 1's specifier is
  consumed first.
- `BR8` adds `ipc` and the sign-extended displacement, then redirects. It
  consumes its two bytes like any instruction, so the PC source must be
  `ipc`.

**Results, after the C2 review corrections** (`build/M1/run_m11_c2final.log`,
before the limitation regression was added; with it, the counts are 1,051 and
1,043 in `build/M1/run_m11_c2close.log`;
the SMC set runs with both store schedules, separate and merged):
1,042 programs per configuration (1,000 random), plus the 14
self-modifying-code programs.

| Fetch configuration | V2 | SMC set | Lock-step records | BR8 redirects (with a fetch request outstanding) |
| --- | --- | --- | --- | --- |
| data-bus prefetch, wait 0 | 1,042 / 1,042 | 14 / 14 | 35,039 | 8,204 (8,148) |
| data-bus prefetch, wait 2 | 1,042 / 1,042 | 14 / 14 | 35,039 | 8,204 (8,204) |
| data-bus prefetch, wait 5 + 0–4 jitter | 1,042 / 1,042 | 14 / 14 | 35,039 | 8,204 (8,204) |
| wide port (server 2–10 cycles) | 1,042 / 1,042 | 14 / 14 | 35,039 | 8,204 (6,291) |

There is also one reset redirect per program. Before C2 the redirect count
included the resets (9,241, of which 1,037 were resets: review C2-4).

**Added by the C2 review:**

- **Self-modifying code** (C2-1). A TEST-ONLY microcode variant,
  `m1_m11_microcode.py --smc-test`, adds the store `MOV.W Rs,[Rn]`. Fourteen
  programs store over queued instructions, which both references then
  execute.
- **Truncated instructions** (C2-3). `tb_m1_v2 +ackmax`; the five
  `trunc_j*` programs.
- **Exact trap PCs from the program** (C2-2), with a wrong-PC self-check.

See `docs/reviews/C2-codex-review.md`.

**Known limitation, accepted provisionally** (found by the C2-3 tests).
Opcode dispatch waits for the opcode and the next byte, even for a one-byte
instruction. A NOP or HALT whose next byte never arrives therefore never
executes. **This is not fully faithful V60 behaviour.**

- It is pinned by the regression `limitation_onebyte_halt`: both references
  halt, and V2 does not.
- To be revisited before the CPU is declared functionally complete, or
  earlier if it affects GA2.

**Immediate destinations are a reference discrepancy, deferred to M1.3.**
V2 traps (`immdest`) as a placeholder; that is not a claim about correct
V60 behaviour. See `docs/reviews/C2-codex-review.md`.

**Counts.** "References 1,051" counts every program the references must
agree on, including the SMC set but excluding the 6 probes. "V2 1,043" is
the main list including the probes; the SMC set is reported separately.

**What "pass" means:**

- **The 1,025 agree programs** halt with the final state (R0–R31, PSW,
  writes, instruction count) and the lock-step records identical to both
  references.
- **The 6 V2-unsupported programs** (ADD.W, OR.W, memory modes) **and the 6
  probes** trap with the listed kind, at the start PC of the right
  instruction. Their earlier records, registers and PSW equal the
  reference's after that many instructions.
- **Every run** reports 0 `SIMCHECK` and 0 `ipc` errors.

**Program coverage:**

- every F1/F2 direction;
- every register, including R31;
- lengths 1, 2, 3, 4, 7 and 8 at all 8 start alignments;
- `BR8` forward and backward (loop-free), to the next instruction, to every
  target alignment, at maximum reach, and over bytes that would trap or be
  reserved if decoded.

**Not exercised at system level.** With the wide port, all programs are in
ROM ranges. The fall-back to data-bus prefetch for non-ROM code is covered by
the D1 bench (`run_d1.sh`, `+wide`), not by `run_m11.sh`.

**Mutants** (`build/M1/run_m11_mutants_c2final.log`, after C2): **23 of 24
caught**, the survivor being the documented equivalent. C2 added two:

- `ready_ignores_pending_store` (round 1): it fails all 14 SMC programs,
  and `SIMCHECK` reports "no longer queued";
- `ready_ignores_issuing_store` (round 2): it fails the merged
  store-and-jump schedule (`--smc-merged`).

- **RTL mutants:**
  - N1: format byte from byte 2, F1 or D read from bit 6, quick for every
    immediate;
  - N2: `MODM_SEL` 1/2 swapped;
  - N3: decode-ready that ignores the offset, is always true, comes from the
    previous word (the defect found), or ignores head movement;
  - N4: register never latched;
  - N5: `ipc` following the head every cycle, PC source = head.
- **Microcode mutants:** dispatch without waiting for the format byte or for
  decode-ready, stub without `MODM_SEL`, F1 destination looked up with bit 6,
  quick unmasked, `#imm32` consuming 4 bytes, `BR8` unsigned, F2 D=0 without
  its lookup (also a RAW hazard), immediate destination written.

  7 of the caught mutants also trip a `SIMCHECK`.
- **The one survivor is equivalent** under this microcode: N4 "free-running"
  (pre-M1.1). Every operand routine reads the register in the same word that
  consumes its specifier. The smoke test catches it, and it becomes
  observable in M1.3.

**Smoke and wiring.** The smoke test checks 31 stores. It covers the F1/D
conditions, a bank-1 lookup with a bank-0 decoy, register lifetime with a
nonzero register, and `ipc` after the head has moved. The wiring mutations
are 16/16 (`experiments/V2-datapath-floor/sim/run_wiring_mutations.py`).

**Structural cost.** These are Yosys indications (`yosys_indicator.sh`), not
fitted ALMs:

| RTL | LUTs | Arith LUTs | FF | M10K |
| --- | --- | --- | --- | --- |
| `fc5516c` (last fitted RTL: 2,429 ALMs, 48.3 MHz, timing met) | 4,490 | 1,062 | 1,157 | 22 |
| `2c17515` (before M1.1) | 4,542 | 1,124 | 1,174 | 22 |
| M1.1 (`4716602`) | 4,554 | 1,124 | 1,223 | 23 |
| M1.1 + C2 corrections (`6768fff`) | 4,553 | 1,122 | 1,225 | 23 |

The M10K change comes from the mode table alone. Yosys maps, per memory:

- control store: 20 blocks at 98 bits and at 100 bits (2K deep × 5 bits per
  block);
- mode table: 1 → 2;
- dispatch table: 1.

Quartus counts M10K differently: the fitted design reported 34 where Yosys
reported 22. **No fit** has been run since `fc5516c`, so timing at 48.3 MHz
is **not** established for D1, SMC or M1.1.

## M1.2: ALU instructions and flags (2026-10-25; C3 review pending)

Plan: [`docs/v2-m1.2-plan.md`](../../docs/v2-m1.2-plan.md). Status and
results: [`docs/M1.2-checkpoint.md`](../../docs/M1.2-checkpoint.md).

- **Instructions:** `ADD.W`, `SUB.W`, `CMP.W` and `AND.W` in every M1.1
  form, plus `GETPSW` to a register. Microcode and tables only; no RTL change.
- **Run:**
  - `M11_GEN=$PWD/experiments/M1-functional/m1_m12.py M11_OUT=$PWD/build/M1/m12
    M11_LABEL=M1.2 bash experiments/M1-functional/run_m11.sh 1000 ""
    "+memwait=2" "+memwait=5 +memjit=4" "+wide"`;
  - mutants: `M11_MUTSET=m12 bash experiments/M1-functional/run_m11_mutants.sh 50`;
  - costs: `m1_costs.py`.
- **Lock-step records now carry the PSW per instruction**, in all three
  executions.
- **Reference semantics** (both agree):
  - operand 1 = source `a`, operand 2 = destination `b`;
  - ADD `b + a`, CY = carry;
  - SUB/CMP `b − a`, CY = borrow; CMP writes nothing;
  - AND `b & a`, OV = 0, CY unchanged;
  - OV = signed overflow;
  - GETPSW writes the whole PSW.
- **CMP with an immediate operand 2:** the references agree (a value) and V2
  implements it. **ADD/SUB/AND to an immediate:** the references disagree
  (6 probes), and V2 traps `immdest` (M1.3).
- **Results at `4209565`:**
  - references 1,044/1,044;
  - V2 1,050/1,050 in each of 4 fetch configurations;
  - 13,135 PSW changes compared;
  - M1.2 mutants 20/20;
  - the M1.1 suite and mutants unchanged on the extended microcode;
  - clean clone reproduced.
- **Measured cost:** the same execute cycles as the matching MOV.W (12–19),
  except F2 D=1 `#quick` (+1: a mask word). Total cycles add fetch stalls,
  which depend on the fetch path: 0–1 on the wide port. `m1_costs.py`
  rejects any run that trapped, timed out, did not halt, or reported
  `SIMCHECK` errors (review C3-2). Yosys is unchanged (4,553 LUTs / 1,225
  FF / 23 M10K).

## Instrumentation definitions (`tb/v2_sim_observer.sv`)

**Dispatch.** An issued microinstruction with NXT = 5. It is the *last*
microinstruction of the instruction before it. The next instruction starts in
the following cycle; its PC and opcode are the queue head at that dispatch.

**HALT.** Its routine ends in a self-loop at `+haltpc`. The loop's first
issue completes HALT; later loop cycles belong to no instruction.

**Retirement, in order.** Instruction *k* retires when all three hold:

- the next instruction has started, or HALT completed;
- none of *k*'s microinstructions is in E1, E2 or E3;
- no data-bus transaction is outstanding.

Its register, PSW and memory effects are then committed.

**Cycle classes.** Every `clk` with `ce` is counted, against the instruction
current in E0:

| Class | Condition |
| --- | --- |
| `mem` | `data_busy` (pipeline frozen on a data transfer) |
| `iter` | `sh_busy`, or a microcode wait on condition 9 (multiply/divide) |
| `fetch` | `fetch_short`, or a microcode wait on condition 7 (queue bytes) |
| `exec` | everything else |

- Priority: mem > iter > fetch > exec.
- A microcode wait is a conditional micro-branch to its own address whose
  condition holds.
- Cycles before the first dispatch are counted as `pre`.

**Upstream counting.** Upstream instructions are counted at S_DECODE entry on
`ce` cycles. Its cost unit is the V60 clock (`ce`), 62.08 ns; V2's is
`clk_sys`, 20.70 ns.

## Regression of the synthesizable V2

**Historical record (M1.0, `fc5516c`).** `git diff 179add9 --
experiments/V2-datapath-floor/rtl` was empty; the figures were smoke 26/26 ×3,
wiring mutations 9/9, liveness 2K 97/98, 23/24, 23/24, the documented SHA
differences, and Yosys 4,490 LUTs / 1,157 FF. That RTL fitted at 2,429 ALMs in
run 40024114327.

**Current (`2c17515` RTL, after D1, D1b, C1 round 1 and the SMC correction;
`build/M1/smc_regression.log`; the round-1 intermediate is
`build/M1/c1_regression.log`, 4,447 LUTs):**

| Check | Result |
| --- | --- |
| Smoke test | 26/26 in all three styles |
| Wiring mutations | 9/9 caught |
| `liveness.py` | 2K: 97/98, 23/24, 23/24, unchanged; 4K all live |
| `run_units.sh` | the same documented SHA differences (310 OV, 71 carry) |
| Verilator lint (`-Wall`, 5 parameter sets) | clean, apart from the pre-existing naive-style BLKSEQ |
| `yosys_indicator.sh` | 4,542 LUTs, 1,174 FF, 22 M10K, 259 MLAB, 4 DSP |

A Yosys count is not a fitted-ALM figure, and this RTL has not been fitted.

## R2a: `ldpr #imm32, #q` test infrastructure (2026-10-28; no microcode)

Plan: [`docs/r2-ldpr-plan.md`](../../docs/r2-ldpr-plan.md) (sections 4, 7, 9). Test infrastructure and vectors only: no
LDPR routine, no dispatch entry, no change to `m1_m11_microcode.py` or any CPU RTL. V2 traps LDPR (`opcode`) until R2b.

| Path | Role |
| --- | --- |
| `m1_r2.py` | 206 programs with independently computed expectations (lock-step records, per-instruction privileged write events, upstream's value-change-only event set, V2 phase r2a/r2b expectations) |
| `m1_r2_check.py` | checker: R1 checks plus exact per-instruction privileged events (ID, value, byte enables, multiplicity), final privileged dump, no PSW update, no events by unretired instructions |
| `m1_r2_run.py`, `run_r2.sh` | driver: V60X + upstream, V2 at 4 fetch configs, L6, SMC matrix, dispatch isolation (8 combinations), probes, negative controls |
| `m1_r2a_catchers.py` | mutant-catcher contract: microflow model of the planned routine over the real tables, plus real-RTL mutants of the probe image |
| bench changes | `+privev` (P/PR events) in V2 observer and both upstream benches; `+trapprivpc/+trapregidpc` (kinds `priv`, `regid`), `+forceel` (TEST-ONLY EL force), `+ackdelay` (cycle-count release); `m1_ref.py --privev`. All inert by default. |

Probe image (in memory, TEST-ONLY): spare opcode 0xFE carries the planned operand/immediate/write steps (not LDPR) to measure
`WR_ASEL = 3`; 0xFD/0xFC give the new trap kinds; 0xFB has the privileged dispatch flag to prove the EL force reaches `ucond[16]`.
Results: `results/r2a_verification_2026-10-28.{txt,json}`. `R2_PHASE=r2b ./run_r2.sh` flips the V2 expectations for R2b.

### Codex round 1 dispositions (R2a)

| # | Finding | Disposition |
| --- | --- | --- |
| 1 | Blocker: R2b phase cannot run against a correct R2b builder | The probe image is built from a frozen pre-R2 fixture (`results/r2a_pre_r2_fixture_2026-10-28.json`), never the live builder. Structural stages are phase-aware (r2b: `dtab[0x12]` = ldpr with the privileged flag, only entry with it, `trap_priv`/`trap_regid` exist); the wrong-trap-kind control picks a kind different from the phase's expectation; r2b baselines come from the model or the phase-independent probe. Demonstrated with `R2_PHASE=r2b R2_SIM_R2B=1 run_r2.sh` (`m1_r2b_simbuilder.py`: in-memory inert stubs, not LDPR microcode): the driver reaches validation, 43/79 groups (all structural) pass, V2 LDPR groups fail as expected, 107/107 controls are caught. |
| 2 | Incomplete evidence passes the checker | Missing `psw=` fields, a missing PR line of a written ID, and a trap `END instrs` different from the retirement count are failures. Controls: FLAG_UPD mutant with `psw=` stripped, `l1_v01.ext` without the SBR PR line, trap `instrs=999`: all rejected. |
| 3 | Forced-EL shared state | The shared `_replay` monkeypatch is gone: the forced EL is verified and removed from the log locally. 600 interleaved EL 0/1/2/3 checks on 8 threads all pass. |
| 4 | SMC matrix skipped L6 | smc and smcm run the 14 JMP-free L6 programs (12 in R2b permanent-withhold runs, which exclude the two k=8 cases) withheld (never released) and released by cycle count at 4 fetch configs. The 8 JMP-free wrap programs are added to the S0 SMC runs. |

Qualification: the 23 model mutants show catcher sensitivity under the modelled semantics only; real LDPR mutation evidence remains an R2b obligation. "107 controls" counts passing baselines as well as mutant kills.

## R2b: `ldpr #imm32, #q` microcode (2026-10-28; simulation only)

Plan: [`docs/r2-ldpr-plan.md`](../../docs/r2-ldpr-plan.md) (section 3, 9). **Microcode and dispatch table only: no CPU RTL change.**
`m1_m11_microcode.py` gains the `ldpr` routine (appended after every existing word, so no existing address moves), the trap labels
`trap_priv` / `trap_regid`, and `dtab[0x12] = ldpr` with the privileged flag (fmt bit 7). Present in production and `--smc-test` builds.
Control store +21 words in every option combination (production 223 -> 244, reset-native 219 -> 240, smc 221 -> 242, smc+merged 220 -> 241,
smc+reset 217 -> 238 ...); the mode table and every other dispatch entry are bit-identical (R2a pins 8/8).

Routine (25 microcycles): `br C_PRIV -> trap_priv`; `br !C_F1 -> trap_mode` (format I); CONSUME 2; operand 1 (`C_IMM` / `C_QUICK` guards, `trap_mode`);
immediate word (IMM_OFS 1, IMM_SZ 2, CONSUME 5) -> T0; operand 2 (`C_QUICK` guard); quick word (AND 15) and the result-bypass chain
`(q + 30) AND 28` (result only, no FLAG_UPD); two spacer words; `br !C_ZERO -> trap_regid` (res_q is registered); write word
(`WR_DST` PRIV, `WR_ASEL` 3 = operand 2's mode-byte register = q, A = T0, CONSUME 1). Cost: 30 cycles default (25 exec + 5 fetch), 27 at `+wide`; every other
form's cost table is identical to the pre-R2b microcode (102 programs, 2 configs).

Builder self-checks (every `build()` call, hence all 8 option combinations): regID predicate exhaustive over q = 0..15 read back from the emitted words;
no FLAG_UPD / bus op / GPR write in the routine; exactly one privileged write with `WR_ASEL` 3; no jump into the SMC `[Rn]` store path; only `dtab[0x12]` has the privileged flag.

| Item | Result |
| --- | --- |
| R2a suite, `R2_PHASE=r2b run_r2.sh` against the real builder (not `R2_SIM_R2B`) | 79/79 groups; 107/107 controls/catchers; 206 programs (148 positive, 41 negative, 17 probe) |
| Real microcode mutants (`m1_r2b_mutants.py`, 430 V2 runs each) | 28/28 killed, each by a program of its designated catcher tag; baseline 0 failures; per-mutant evidence in `results/r2b_mutants_2026-10-28.json` |
| S0, real GA2 ROM (executable `--r2b-check`, privileged-register records compared) | reach 2; `TRAP opcode` at `0x0010050E` (`movea.b 210000, SP`); V2 SBR = upstream SBR = `0x00100000`; PSW `0x10000000` |
| Regression | R1a/R1b 35/35 + 39/39, mutants 33 (28 + 5 equivalent); refs, M1.1, M1.2, SMC, DBcc `.sum` files byte-identical to R1b; D1 rc 0; S0 self-test, 8/8 compare tests; S1 122 PASS, 113/8/0/1 |
| HW-001 | ALL OK, 7/7 image hashes identical, HW-001 tree and its frozen builder untouched |
| Default-output identity vs pre-R2b | 6888 log pairs: 0 differences on any program without an LDPR; differences only on the 2092 R2a LDPR runs (pre: `TRAP opcode`) and `nv_ldpr` (R1 non-vacuity program, contains an LDPR) x 4 configs |

Mutant notes: FLAG_UPD is mutated on each of the five LDPR words; "off by one" ID checks are the constant mutants ADD 31 (window 1..4: allows 1, rejects 5) and ADD 29 (window 3..6: allows 6,
rejects 2), caught by N1 and by the SBR / L1SP positives; extras: `WR_ASEL` const 5, `WR_ASEL` field 2, mask 24, format-I guard removed. SMC builds share the routine text and were not separately mutated;
the unmutated SMC builds pass the whole matrix. Results: `results/r2b_verification_2026-10-28.{txt,json}`, `r2b_mutants_2026-10-28.json`, `r2b_s0_result_2026-10-28.json`, `r2b_costs_default_2026-10-28.txt`.

**S0 reach criterion is executable (Codex finding, 2026-10-28).** `s0_compare.py` previously ignored privileged-register records (`P n id= val=` writes, `PR id= val=` end dump), so corrupting both V2 SBR records still reported reach 2. Now: when both logs carry them, the `P` records are compared per instruction (ID, value; V2's `be=` is ignored) as part of lock-step equality, so any difference, missing or extra privileged write ends the reach at that instruction (`incorrect`), including an extra write by the trapping instruction. `s0_compare.py ... --r2b-check` additionally requires `+privev` records in both logs and asserts reach == 2, `TRAP opcode` at `0x0010050E`, a `P id=5 val=00100000` write and final `PR id=5` == `0x00100000` in both; exit 1 on any failure. Logs without `P` records (the long 20003-record upstream run `up.log`) compare exactly as before: re-running `up.log` + `v2.log` in default mode reproduces `build/M1/r2b/s0/s0_result.json` byte for byte apart from the new `priv_compared` field. The privileged-register check uses the short `+privev` pair `build/M1/r2b/s0/{up2,v2}.log` (3 upstream records; the long run was not made with `+privev`). Evidence: `results/r2b_s0_check_2026-10-28.txt` (archived pair PASS; three negatives on edited copies of `v2.log`: both SBR records `deadbeef`, V2 `P` records removed, an extra `P 2 id=7` write: all FAIL rc 1). `test_s0_compare.py` now has 20 cases (8 original + 12 new: matching pass, SBR corrupted, P value differs, P missing, PR id=5 missing, extra write, extra write by the trapping instruction, no-`+privev` logs, reach != 2, default-mode cases, archived-log pass); S0 self-test unchanged (PASS). **Round-2 correction (Codex R2b r2):** the first version attached `P n` to instruction n−1 (so an extra `P 0` or `P 4` write passed); `P n` belongs to instruction n (it precedes `L n` in both benches). Fixed, plus any V2 privileged event beyond the trapping instruction now fails; Codex's repro (extra `P 0`) and an extra `P 4` both FAIL, archived pair PASS, long-pair default result unchanged; `test_s0_compare.py` 22/22 (test indices corrected; a corrupted LDPR `P` record now fails at reach 1, the LDPR).

Note on the identity helper (`build/M1/r2b/ident_r2b.py`, local, not committed): its name-based classifier labelled the four `nv_ldpr` differences "NON-LDPR"; they are LDPR runs (`nv_ldpr` contains an LDPR at pc 7). The helper label was wrong; the enumeration of differences (only R2a LDPR runs plus `nv_ldpr` x 4) is unaffected.

Qualification: simulation only; the references (V60X, upstream) and V2 agree on the in-scope form, which says nothing about real-silicon behaviour; IDs 0, 1, 6..28 and every other LDPR form still trap.
No fit, no Quartus, nothing hardware-proven. Codex checkpoint (step C) not yet run.

## R3a: `movea.b /abs, Rn` test infrastructure (2026-10-29; no microcode)

Plan: [`docs/r3-movea-plan.md`](../../docs/r3-movea-plan.md) (sections 4, 6, 8, 9, 11). Test infrastructure and vectors only: no MOVEA routine, no
dispatch entry, no change to `m1_m11_microcode.py`, `m1_microcode.py` or anything under `experiments/V2-datapath-floor/` (`git diff --stat HEAD` on those paths is empty).
V2 traps every MOVEA (`opcode`, opcodes 0x40 / 0x42 / 0x44) until R3b.

| Path | Role |
| --- | --- |
| `m1_r3.py` | 912 programs with independently computed expectations (lock-step records, per-instruction **GPR write events**, privileged events, PSW-update counts, V2 phase r3a/r3b expectations): A1-A10 (180 positive: 164 M1 bench + 16 S0 harness), N1-N6 incl. the exhaustive 511-entry mode-table sweep (573 negative), 159 probe-image programs. `--freeze` / `--verify-frozen` write / check the frozen-expectation record |
| `m1_r3_check.py` | checker: R2 checks plus exact GPR events (register, value, byte enables, multiplicity, order), the `GPREV on` marker, `gpr=` counters, events of unretired instructions after the drain, MOVEA-window dr = dw = priv = psw = 0 |
| `m1_r3_run.py`, `run_r3.sh` | driver: corrected V60X + upstream, V2 at 4 fetch configs, A7 withheld bytes, SMC matrix, structural invariants (8 builder combinations), probe image, `probe_movea_value`, S0, negative controls |
| `m1_r3a_catchers.py` | mutant-catcher contract: microflow model of the planned routine over the real tables (one knob per planned mutant), real-RTL mutants of the probe image, structural mutants |
| `m1_r3a_negctl.py`, `m1_r3a_s0.py` | infrastructure-level controls (V60X mutants, corrupted logs, forced-zero instrumentation, **the three GPR-event checker controls on the real V2**, `probe_movea_value` enforcement, dispatch format bits) and the `s0_compare.py --r3b-check` runs against the real GA2 prefix |
| `m1_probe_movea.py` | `probe_movea_value` (D1) enforcement: specific values, both runs successful and COMPLETE, each validated independently against the full expected execution (retirement PCs / opcodes / per-instruction effects, counters, writes, R00-R31, PSW, END), so the only permitted difference is v60_ref R1 = 0x5A, dr = 1 against upstream R1 = 0x20, dr = 0 (R3a fix, `docs/r3-movea-plan.md` §12); called by `run_refs.sh` |
| `m1_r3_fixture.py`, `m1_r3b_simbuilder.py`, `m1_r3a_identity.py` | frozen pre-R3 fixture and table pins (`results/r3a_pre_r3_fixture_*.json`, `r3a_table_pins_*.json`); in-memory table-only R3b stub (inert self-loop, NOT microcode); default-output identity check |
| bench / reference changes | `+gprev` in the V2 observer (`G n id= be= val=` events, `GPREV on`, `gpr=` in C/CU); `m1_ref.py --gprev`; **corrected `V60X` MOVEA** (operand 1 as an address; the base `v60_ref.py` is untouched: D1 retained and enforced); `probe_movea_value` in `m1_directed.py`; `run_refs.sh` exits non-zero when a documented probe stops reproducing; `s0_compare.py --r3b-check`. All inert by default (identity below) |

Probe image (in memory, TEST-ONLY, from the frozen fixture): spare opcode 0xFE carries the plan's execute step (CONSUME 2, operand lookup, IMM_LD IMM_OFS 1 IMM_SZ 2 CONSUME 5, `WR_DST` GPR `WR_ASEL` 1, no guards) so the
GPR event log, the value/size/consume/write-target classes and the A7 availability checker are exercised on the real V2; 0xFC / 0xFB trap `mode` in the middle of a routine. Results and the ASSUMED-ledger answers:
`results/r3a_verification_2026-10-29.{txt,json}`; corrections after Codex round 1 (`--r3b-check` completeness, D1 guard, identity wording): `docs/r3-movea-plan.md` §12 and `results/r3a_fixes_verification_2026-10-30.{txt,json}`.

Identity wording: the default-output check (`m1_r3a_identity.py`) is a **normalised** log identity (every `- ` simulator-banner line removed, line endings normalised, both runs required to exit 0 and be complete); the M1.1 / M1.2 / DBcc `.sum` comparison with R2b is **footer-removed** (the `.sum` files gained a final `rc=0` line). `R3_PHASE=r3b ./run_r3.sh` flips the V2 expectations; `R3_PHASE=r3b R3_SIM_R3B=1` runs that phase against the stub (V2 groups fail by design, structural/probe/controls pass).
