# BASELINE_S2: Pocket-top simulation, integration baseline

**Status: exploratory integration baseline. Simulation only. Not qualification, not a hardware result.** It freezes what the S2
Pocket-top simulation (`experiments/P1-pocket-top`, source commit `e11a57f` on `sim/s2-pocket-top`) produced, with
the numbers re-derived from the stored run outputs (`p1_report.py` output, `report.json`, `sim.log.gz`, `<run>.out`) and
nothing re-simulated. Companion files: `baseline_s2_manifest.json` (all numbers, per-frame data, histogram bins, hashes) and its
generator `make_baseline_manifest.py`. Description of the design and assumptions: `README.md` in this directory (K = measured in this
sim, E = estimate). Prior freeze this builds on: `experiments/WC-sim/MILESTONE_first_screen.md`.

## 1. Runs and evidence
| Run (`build/P1/...`) | Config | Role |
| --- | --- | --- |
| `s2c_t19` | SRAM 19 ns (`--sram-taa-ps 19000 --sram-rd-edges 2`), ce_cpu exact 1/3, 120 frames | baseline |
| `s2d_t38` | SRAM 38 ns (`38000`, `--sram-rd-edges 3`), exact 1/3 | slow-SRAM variant |
| `s2e_acc` | 19 ns, upstream ce accumulator +21848/65536 (`--ce-mode 1`) | GA2 PCB bus-rate cadence |
| `s2f_neg` | 38 ns SRAM but sampled at E1+1 = 20.7 ns (`--sram-rd-edges 1`), 45 frames | negative control (must fail) |
| `s2b_t19` | 19 ns, exact 1/3, build `b3` | SUPERSEDED, bridge vblank-pad race present: bug evidence only |
| `s2a_t19` | build `b2` | SUPERSEDED (capture-side indexing error: 0 of 82 equal MAME); not archived (see 9) |

`b1`..`b4`, `gen_t0`, `smoke1` are build/smoke directories, not evidence. s2c..s2f use build `b4`; all runs use the same program/tile
ROM, EEPROM image and microcode.

Commands (from `cmd.txt` of each run; the run directory is always fresh):
```
experiments/P1-pocket-top/run_p1.sh build build/P1/b4
- `s2c_t19`: `experiments/P1-pocket-top/run_p1.sh sim build/P1/b4 build/P1/s2c_t19 120 --sram-taa-ps 19000 --sram-rd-edges 2 --ce-mode 0`
- `s2d_t38`: `experiments/P1-pocket-top/run_p1.sh sim build/P1/b4 build/P1/s2d_t38 120 --sram-taa-ps 38000 --sram-rd-edges 3 --ce-mode 0`
- `s2e_acc`: `experiments/P1-pocket-top/run_p1.sh sim build/P1/b4 build/P1/s2e_acc 120 --sram-taa-ps 19000 --sram-rd-edges 2 --ce-mode 1`
- `s2f_neg`: `experiments/P1-pocket-top/run_p1.sh sim build/P1/b4 build/P1/s2f_neg 45 --sram-taa-ps 38000 --sram-rd-edges 1 --ce-mode 0`
- `s2b_t19`: `experiments/P1-pocket-top/run_p1.sh sim build/P1/b3 build/P1/s2b_t19 120 --sram-taa-ps 19000 --sram-rd-edges 2 --ce-mode 0`
python3 -B experiments/P1-pocket-top/p1_report.py <run> --mame <main>/build/WC/mame_run1 --ref <main>/build/M1/c1/wc3 --ref2 <main>/build/WC/v2_run17_ms_vd --json <run>/report.json
```
(`run_and_report.sh <build> <run> <frames> <opts>` wraps sim + report.) `s2f_neg` has no `report.json` content: it stops after 53 instructions with 0
vblanks and `p1_report.py` raises IndexError in its CPI step (`report.txt` holds the traceback); its numbers are from `sim.log.gz`/`s2f_neg.out`.

## 2. ROM load and read-back verification (identical in s2b, s2c, s2d, s2e, s2f)
| Item | Value |
| --- | --- |
| Program ROM slot sum / position-weighted sum (RTL read-back through sdram.sv p5) | `a54f8e76` / `9e152dfa` |
| Tile ROM slot sum / position-weighted sum (RTL) | `5e9bf850` / `39aeff9a` |
| Sums computed from the files (`tb_p1.cpp`) | rom `a54f8e76/9e152dfa`, tiles `5e9bf850/39aeff9a`: **MATCH** |
| Backdoor compare of SDRAM model contents vs files | 3,145,728 words (1,048,576 program + 2,097,152 tile), **0 differing** (rom=0, tiles=0) |
| APF words written / write-queue in / out | 1,572,864 32-bit bridge words / 3,145,728 / 3,145,728; overflow 0, collisions 0 |
| Write-queue maximum fill | **1** |
| Load time | load done at rc=153,529,035 clk_ram = 1.589 s simulated; verify finished rc=14,319,184 clk_ram later; reset released at rc=167,914,727 = 1.7376 s |
| Microcode load check (UCLOAD) | control store 2048 words, dispatch 256, mode 512: 0 mismatches |
| Program ROM word-for-word vs MAME program-space dump (make_rom.py, `rom.log`) | 1,048,576 words, 0 differ |

(Sum definition: `p1_slot_verify.sv`: sum of 16-bit LE words mod 2^32, and the sum of running prefix sums mod 2^32. Not CRCs. ROM file hashes
are in the manifest: main_rom.bin sha256 `72b2bbc22b3df2d15f9fca9626e0e6cf2c34f79c5a4d15f0718938bedcec2dfa`, tiles.bin `55f80c6e7675826dbb74e470e319672ab46fed98839ad6fd6a2377da8a0242d5`; hashes only, no ROM content.)

## 3. Bridge-output frames: all 82 compared, three runs
Frames are captured at the video bridge output (clk_vid), 320 x 224, compared to MAME snapshots at 5-bit depth (low 3 bits of the sim's 8-bit
channels dropped, as in the WC-sim comparison), and hashed against the HEAD WC-sim run `build/M1/c1/wc3` (`v2driven_frame_*`) and the frozen milestone
run `build/WC/v2_run17_ms_vd`.

| Quantity | s2c | s2d | s2e |
| --- | --- | --- | --- |
| Bridge frames / bad lines / bad frames / fallback | 226 / 0 / 0 / 0 | 226 / 0 / 0 / 0 | 226 / 0 / 0 / 0 |
| Non-black bridge frames | 82 | 82 | 82 |
| Equal to a MAME snapshot at 5-bit (0 differing pixels) | **82 of 82** | **82 of 82** | **82 of 82** |
| Identical to a wc3 frame | 82 of 82 | 82 of 82 | 82 of 82 |
| Identical to a milestone frame | 82 of 82 | 82 of 82 | 82 of 82 |
| Distinct images, in order | 30 (= wc3 30, = milestone 30, sequence equal) | same | same |
| Bridge sequence = core-side capture sequence | true | true | true |
| First non-black bridge frame | 144 | 144 | 144 |
| First non-black frame in wc3 / milestone | 40 / 39 | 40 / 39 | 40 / 39 |
| Non-black frames in wc3 / milestone reference runs | 82 / 73 | 82 / 73 | 82 / 73 |

Note on "40": the bridge numbers frames from its own reset (first non-black = 144, a different origin), wc3 numbers V2 vblanks (first non-black = 40;
milestone 39). Frame 144 and wc3 frame 40 are the same image (image no. 1). The three runs agree frame by frame in the MAME snapshot chosen, the diff-pixel
count and the image hash (checked by the generator). Table columns: bridge frame index (the file number `bridge_frame_NNNN.ppm`), equal to MAME 5-bit,
differing pixels at 5-bit against the best MAME snapshot, MAME snapshot number (as numbered by `p1_report.py`), image number in order of first appearance
(1-30; wc3 and milestone distinct-image order are the same sequence), wc3 and milestone frame numbers that carry the same image.

| bridge frame | equal MAME 5-bit | diff px | MAME snap | image no. | wc3 frame(s) | milestone frame(s) |
| --- | --- | --- | --- | --- | --- | --- |
| 144 | yes | 0 | 33 | 1 | 40-41 | 39-40 |
| 145 | yes | 0 | 33 | 1 | 40-41 | 39-40 |
| 146 | yes | 0 | 35 | 2 | 42-43 | 41-42 |
| 147 | yes | 0 | 35 | 2 | 42-43 | 41-42 |
| 148 | yes | 0 | 37 | 3 | 44-45 | 43-44 |
| 149 | yes | 0 | 37 | 3 | 44-45 | 43-44 |
| 150 | yes | 0 | 39 | 4 | 46-47 | 45-46 |
| 151 | yes | 0 | 39 | 4 | 46-47 | 45-46 |
| 152 | yes | 0 | 41 | 5 | 48-49 | 47-48 |
| 153 | yes | 0 | 41 | 5 | 48-49 | 47-48 |
| 154 | yes | 0 | 43 | 6 | 50-51 | 49-50 |
| 155 | yes | 0 | 43 | 6 | 50-51 | 49-50 |
| 156 | yes | 0 | 45 | 7 | 52-53 | 51-52 |
| 157 | yes | 0 | 45 | 7 | 52-53 | 51-52 |
| 158 | yes | 0 | 47 | 8 | 54-55 | 53-54 |
| 159 | yes | 0 | 47 | 8 | 54-55 | 53-54 |
| 160 | yes | 0 | 49 | 9 | 56-57 | 55-56 |
| 161 | yes | 0 | 49 | 9 | 56-57 | 55-56 |
| 162 | yes | 0 | 51 | 10 | 58-59 | 57-58 |
| 163 | yes | 0 | 51 | 10 | 58-59 | 57-58 |
| 164 | yes | 0 | 53 | 11 | 60-61 | 59-60 |
| 165 | yes | 0 | 53 | 11 | 60-61 | 59-60 |
| 166 | yes | 0 | 55 | 12 | 62-63 | 61-62 |
| 167 | yes | 0 | 55 | 12 | 62-63 | 61-62 |
| 168 | yes | 0 | 57 | 13 | 64-65 | 63-64 |
| 169 | yes | 0 | 57 | 13 | 64-65 | 63-64 |
| 170 | yes | 0 | 59 | 14 | 66-67 | 65-66 |
| 171 | yes | 0 | 59 | 14 | 66-67 | 65-66 |
| 172 | yes | 0 | 61 | 15 | 68-69 | 67-68 |
| 173 | yes | 0 | 61 | 15 | 68-69 | 67-68 |
| 174 | yes | 0 | 63 | 16 | 70-71 | 69-70 |
| 175 | yes | 0 | 63 | 16 | 70-71 | 69-70 |
| 176 | yes | 0 | 65 | 17 | 72-73 | 71-72 |
| 177 | yes | 0 | 65 | 17 | 72-73 | 71-72 |
| 178 | yes | 0 | 67 | 18 | 74-75 | 73-74 |
| 179 | yes | 0 | 67 | 18 | 74-75 | 73-74 |
| 180 | yes | 0 | 69 | 19 | 76-77 | 75-76 |
| 181 | yes | 0 | 69 | 19 | 76-77 | 75-76 |
| 182 | yes | 0 | 71 | 20 | 78-79 | 77-78 |
| 183 | yes | 0 | 71 | 20 | 78-79 | 77-78 |
| 184 | yes | 0 | 73 | 21 | 80-81 | 79-80 |
| 185 | yes | 0 | 73 | 21 | 80-81 | 79-80 |
| 186 | yes | 0 | 75 | 22 | 82-83 | 81-82 |
| 187 | yes | 0 | 75 | 22 | 82-83 | 81-82 |
| 188 | yes | 0 | 77 | 23 | 84-85 | 83-84 |
| 189 | yes | 0 | 77 | 23 | 84-85 | 83-84 |
| 190 | yes | 0 | 79 | 24 | 86-87 | 85-86 |
| 191 | yes | 0 | 79 | 24 | 86-87 | 85-86 |
| 192 | yes | 0 | 81 | 25 | 88-89 | 87-88 |
| 193 | yes | 0 | 81 | 25 | 88-89 | 87-88 |
| 194 | yes | 0 | 83 | 26 | 90-91 | 89-90 |
| 195 | yes | 0 | 83 | 26 | 90-91 | 89-90 |
| 196 | yes | 0 | 85 | 27 | 92-93 | 91-92 |
| 197 | yes | 0 | 85 | 27 | 92-93 | 91-92 |
| 198 | yes | 0 | 87 | 28 | 94-95 | 93-94 |
| 199 | yes | 0 | 87 | 28 | 94-95 | 93-94 |
| 200 | yes | 0 | 89 | 29 | 96-97 | 95-96 |
| 201 | yes | 0 | 89 | 29 | 96-97 | 95-96 |
| 202 | yes | 0 | 91 | 30 | 98-121 | 97-111 |
| 203 | yes | 0 | 91 | 30 | 98-121 | 97-111 |
| 204 | yes | 0 | 91 | 30 | 98-121 | 97-111 |
| 205 | yes | 0 | 91 | 30 | 98-121 | 97-111 |
| 206 | yes | 0 | 91 | 30 | 98-121 | 97-111 |
| 207 | yes | 0 | 91 | 30 | 98-121 | 97-111 |
| 208 | yes | 0 | 91 | 30 | 98-121 | 97-111 |
| 209 | yes | 0 | 91 | 30 | 98-121 | 97-111 |
| 210 | yes | 0 | 91 | 30 | 98-121 | 97-111 |
| 211 | yes | 0 | 91 | 30 | 98-121 | 97-111 |
| 212 | yes | 0 | 91 | 30 | 98-121 | 97-111 |
| 213 | yes | 0 | 91 | 30 | 98-121 | 97-111 |
| 214 | yes | 0 | 91 | 30 | 98-121 | 97-111 |
| 215 | yes | 0 | 91 | 30 | 98-121 | 97-111 |
| 216 | yes | 0 | 91 | 30 | 98-121 | 97-111 |
| 217 | yes | 0 | 91 | 30 | 98-121 | 97-111 |
| 218 | yes | 0 | 91 | 30 | 98-121 | 97-111 |
| 219 | yes | 0 | 91 | 30 | 98-121 | 97-111 |
| 220 | yes | 0 | 91 | 30 | 98-121 | 97-111 |
| 221 | yes | 0 | 91 | 30 | 98-121 | 97-111 |
| 222 | yes | 0 | 91 | 30 | 98-121 | 97-111 |
| 223 | yes | 0 | 91 | 30 | 98-121 | 97-111 |
| 224 | yes | 0 | 91 | 30 | 98-121 | 97-111 |
| 225 | yes | 0 | 91 | 30 | 98-121 | 97-111 |


Distinct images in order of first appearance: distinct images in order of first appearance in s2c: 30; first bridge frame of each: 144, 146, 148, 150, 152, 154, 156, 158, 160, 162, 164, 166, 168, 170, 172, 174, 176, 178, 180, 182, 184, 186, 188, 190, 192, 194, 196, 198, 200, 202
(images 1-29 each occupy two consecutive bridge frames; image 30 occupies bridge frames 202-225, 24 frames, and is the steady final screen).

IRQ and PC stream against wc3 (`p1_report.py`):

| | s2c | s2d | s2e |
| --- | --- | --- | --- |
| IRQ acknowledges (p1 / wc3) | 103 / 103 | 103 / 103 | 103 / 103 |
| First acknowledge, instruction index (p1; vec) | 613,013 (00) | 613,013 (00) | 613,215 (00) |
| First acknowledge in wc3 (vec) | 625,204 (00) | 625,204 (00) | 625,204 (00) |
| PC stream compared (up to the earlier first ack) | 613,013 | 613,013 | 613,215 |
| First PC divergence from wc3 (instruction index) | **179,660** | **179,660** | 179,671 |

The V2 PC stream is identical to wc3 for 179,660 instructions (s2c, s2d; 179,671 in s2e) and then differs. The cause stated in `README.md` is the
93C46 write-busy poll count (a wall-time poll, as in WC-sim S0); this report derived the divergence index, not the cause (the cause is carried over
from the README and the WC-sim S0 analysis). The images still come out identical.

## 4. SRAM timing tests
| | s2c (19 ns) | s2d (38 ns) | s2e (19 ns, acc) | s2f_neg (38 ns model, sampled 20.7 ns) |
| --- | --- | --- | --- | --- |
| Reads (samples) | 1,905,272 | 1,905,272 | 1,904,794 | 4 |
| Bad samples | **0** | **0** | **0** | **4** (a=07ffc..07fff, margin -17.303 ns) |
| Minimum margin | 22.393 ns | 24.090 ns | 22.393 ns | -17.303 ns |
| Writes | 207,701 | 207,701 | 208,213 | 4 |
| Reads of never-written words | 0 | 0 | 0 | 0 |
| Overlap / tWP / addr-during-WE / write-without-DQ / contention violations | 0 | 0 | 0 | 0 |

Negative control (`s2f_neg`): the model needs 38 ns after the address/OE edge, the port samples after 20.7 ns, so the first four reads (the work-RAM
words at 0x07FFC..0x07FFF, the first reads after reset) return poisoned data (XOR A5A5). The CPU enters trap mode (`TRAP mode ipc=00100559`), **retired = 53
instructions**, 3,052 clk_sys after reset, 0 vblanks; the bridge never shows a good frame (frame 105 line 19 has 129 pixels, 1 bad line/frame, fallback raster
on, 0 non-black frames saved). The test can fail.
The s2c and s2d runs are cycle-identical in timing (CPI vbl 20-120 = 41.089 in both): the bus samples ack only on ce, so any sample point up to E1+3 gives the same cycles.
SDRAM-side counters also match between s2c and s2d (act 2,123,142, rd 7,595,076).

## 5. SDRAM protocol (chip model checks in `tb_p1.cpp`)
| | s2c | s2d | s2e | s2f_neg |
| --- | --- | --- | --- | --- |
| Violations (all 11 classes: act_open, rw_idle, rcd, rp, rc, ras, wr, rfc, ref_open, mrd, mrs) | **0** | **0** | **0** | **0** |
| CL / BL code / MRS count | 2 / 0 / 1 | same | same | same |
| ACT / RD / WR / PRE | 2,123,142 / 7,595,076 / 3,145,728 / 523,406 | same | 2,128,517 / 7,616,576 / 3,145,728 / 523,404 | 1,010,827 / 3,145,816 / 3,145,728 / 244,811 |
| REF total / after ready | 518,054 / 518,046 | 518,054 / 518,046 | 518,052 / 518,044 | 239,459 / 239,451 |
| Longest REF gap | 714 clk_ram (7.39 us) | 714 | 714 | 712 (7.37 us) |
| Refresh rate after ready | 518,046 in 3757.968 ms = 8822.6 per 64 ms (need 8192) | same | 518,044 in 3757.952 ms = 8822.6 | 239,451 in 1737.003 ms = 8822.6 |
| Reads of unwritten words | 0 | 0 | 0 | 0 |
| Core requests on unused ports (`other_req`) | 0 | 0 | 0 | 0 |

## 6. Tile fetch latency, line deadlines, drops (K)
Latency = clk_ram from the edge p1_req is first high to the edge p1_ack is first high (same definition as the WC-sim `--lat` model).
**27,103 kicks, 27,103 starts, 27,103 dones, 0 dropped lines** in s2c, s2d and s2e; kick interval 6,144..6,150 clk_ram; 0 p1 reads outside the tile region.

Per-frame group figures (from the per-vblank `FL` records; the same in s2c and s2d, s2e differs where marked):

| Frames (V2 vblank) | Mode / layers | Kicks | Bursts per line | Max p1 latency (clk_ram) | Min line slack (clk_ram) | Dropped |
| --- | --- | --- | --- | --- | --- | --- |
| 1-17 | 416 mode, all layers | 3,807 | 104 | 38 (s2e 37) | 845 (s2e 854) | 0 |
| 18-33 | 320 mode, all layers | 3,584 | 80-104 | 38 | **777** (s2e 671, see below) | 0 |
| 34-121 | warning screen, NBG0 + text | 19,712 | 20 | 25 (s2e 26) | 4,741 (s2e 4,740) | 0 |

s2e (ce accumulator) has the lowest slack of any run: 671 clk_ram, at frame 18, which in s2e is still 416 mode (mode flag 1; in s2c/s2d the 416 to 320 switch happens at frame 18,
in s2e one frame later), 104 bursts per line; the group rows above are by frame number, so s2e's 18-33 minimum of 671 comes from that 416-mode frame and its 1-17 minimum is 854.
Whole-run minimum slack: s2c/s2d 777, s2e 671.
Worst line render time (clk_ram): all layers 5,370 (s2e 5,472), warning screen 1,408 (s2e 1,409).

**Histogram granularity.** The testbench aggregates histograms by line class, not by frame group: class "all layers" (lines with >=80 p1 bursts) = frames 1-33
(7,391 lines, i.e. 33 x 224 minus 1), class "light" (<=30 bursts) = frames 34-121 (19,712 lines = 88 x 224); the split between frames 1-17 and 18-33 exists only as the
per-frame max latency / min slack above. Line-render and slack histograms saturate at bin 1024 (their p50/p99 print 1024); max and the per-frame minima are exact.

#### s2c_t19
- p0 (CPU program fetch) latency, clk_ram, bin:count: n=35425 mean=14.19 p50=14 p99=22 p99.9=27 max=37
  bins 14:34430, 15:90, 16:73, 17:78, 18:98, 19:67, 20:83, 21:77, 22:75, 23:80, 24:72, 25:87, 26:77, 27:25, 28:3, 29:4, 31:2, 35:1, 36:2, 37:1
- p1 tile latency, frames 1-33 (all layers; lines with >=80 bursts): n=682672 mean=14.04 p50=14 p99=14 p99.9=25 max=38
  bins 14:677977, 15:787, 16:280, 17:348, 18:306, 19:320, 20:330, 21:311, 22:327, 23:478, 24:521, 25:136, 26:82, 27:405, 28:35, 29:2, 31:2, 33:1, 34:3, 35:4, 36:1, 37:13, 38:3
- p1 tile latency, frames 34-121 (warning screen, NBG0+text; 20 bursts/line): n=394240 mean=14.08 p50=14 p99=17 p99.9=24 max=25
  bins 14:388609, 15:565, 16:561, 17:567, 18:563, 19:564, 20:564, 21:560, 22:565, 23:560, 24:561, 25:1
- line slack to next kick, frames 1-33, clk_ram (bin = value, 1024 = saturated bin ">=1024"): n=7391 mean=2521.03 p50=981 p99=1024 p99.9=1024 max=235715
  bins 777:1, 845:1, 854:1, 919:1, 924:1, 926:2, 927:3, 928:6, 929:4, 930:7, 931:10, 932:9, 933:8, 934:7, 935:5, 936:8, 937:4, 938:7, 939:8, 940:9, 941:10, 942:9, 943:4, 944:2, 945:4, 946:5, 947:9, 948:9, 949:11, 950:15, 951:13, 952:13, 953:2, 954:3, 955:2, 956:2, 957:2, 958:3, 959:3, 960:3, 961:4, 962:6, 963:2, 964:2, 965:2, 966:4, 967:4, 968:3, 969:4, 970:128, 971:176, 972:105, 973:98, 974:100, 975:102, 976:105, 977:100, 978:107, 979:102, 980:104, 981:2307, 1024:3600

#### s2e_acc
- p0 (CPU program fetch) latency, clk_ram, bin:count: n=35424 mean=14.19 p50=14 p99=23 p99.9=27 max=36
  bins 14:34441, 15:84, 16:60, 17:67, 18:97, 19:77, 20:78, 21:70, 22:94, 23:77, 24:59, 25:101, 26:82, 27:30, 28:2, 31:1, 33:1, 34:1, 36:2
- p1 tile latency, frames 1-33 (all layers; lines with >=80 bursts): n=688048 mean=14.04 p50=14 p99=14 p99.9=25 max=38
  bins 14:683288, 15:839, 16:281, 17:375, 18:316, 19:313, 20:313, 21:313, 22:331, 23:476, 24:512, 25:163, 26:97, 27:362, 28:42, 29:3, 30:1, 31:1, 35:4, 36:4, 37:13, 38:1
- p1 tile latency, frames 34-121 (warning screen, NBG0+text; 20 bursts/line): n=394240 mean=14.08 p50=14 p99=17 p99.9=24 max=26
  bins 14:388617, 15:563, 16:561, 17:565, 18:561, 19:564, 20:560, 21:563, 22:561, 23:560, 24:564, 26:1
- line slack to next kick, frames 1-33, clk_ram (bin = value, 1024 = saturated bin ">=1024"): n=7391 mean=2483.42 p50=981 p99=1024 p99.9=1024 max=235715
  bins 671:1, 728:1, 736:1, 742:1, 743:1, 748:1, 750:1, 756:1, 762:7, 763:10, 764:7, 765:6, 766:6, 767:6, 768:6, 769:6, 770:6, 771:6, 772:6, 773:141, 777:1, 854:1, 900:1, 922:3, 923:2, 924:3, 926:1, 927:1, 928:1, 929:1, 930:7, 931:7, 932:8, 933:8, 934:6, 935:10, 936:5, 937:5, 938:13, 939:10, 940:2, 941:10, 942:8, 943:10, 944:12, 945:5, 946:1, 947:10, 948:4, 949:4, 950:7, 951:16, 952:11, 953:3, 954:3, 955:4, 956:2, 957:3, 958:1, 959:5, 960:1, 961:2, 962:1, 963:2, 964:4, 965:3, 966:5, 967:2, 968:6, 969:6, 970:123, 971:186, 972:104, 973:106, 974:106, 975:107, 976:103, 977:102, 978:105, 979:104, 980:105, 981:2295, 1024:3377


Full bins for s2d (identical to s2c) and for all runs are in the manifest (`runs.<name>.hist_bins`). Uncontended p1 burst: 14 clk_ram (99.3% of all-layer bursts, 98.6% of warning-screen bursts).

## 7. CPI against WC-sim (vblanks 20-120)
| | CPI clk_sys / instruction | ratio to wc3 |
| --- | --- | --- |
| wc3 (WC-sim, constant 10 clk_ram SDRAM latency, on-chip work RAM) | 38.259 | 1.000 |
| s2c / s2d (cycle-identical) | 41.089 | 1.074 |
| s2e (accumulator) | 41.081 | 1.0738 |

Whole run (121 vblanks, from the TB lines): s2c 97,650,639 clk_sys after reset / 2,481,875 retired = 39.346; s2e 97,649,853 / 2,482,294 = 39.339;
wc3 97,653,575 / 2,636,233 = 37.04 (wc3 TB line). The README's explanation (one more bus phase per work-RAM read, plus p0 latency mean 14.19 vs 10) is an estimate from counts, not measured by separate experiment.

## 8. The two integration bugs
**(a) Load write queue.** `sdram.sv` latches one download transaction per `wr_req` rising edge in a single mailbox; a second `wr_req` before `wr_ack` overwrote
the first. Fix: `rtl/p1_load_wq.sv` holds `wr_req` high until `wr_ack` and issues one write at a time (16-deep queue; max fill 1 in the final runs, `wq_overflow`/`wq_collide` counters 0).
Detection: the mechanism is documented in `p1_load_wq.sv`; the load is guarded by the read-back sums and the backdoor compare (section 2), which would show a lost
write. **The stored runs do not contain a failing load** (b1/smoke1 hold no log of the pre-fix behaviour), so how it was first seen cannot be re-derived from the preserved
outputs; the code comment is the only record.

**(b) Video-bridge vblank-pad race.** A line is 768.75 clk_vid, so the source line start moves by +-1 clk_vid between frames. In `ga2_video.sv`, padding a vblank
window line exactly at the learned line start (`h_start`) pre-empted the real line 0 on alternate frames, leaving row 0 black. Fix: pad at `h_start + 4`
(`s_vbl && hcnt == h_start + 11'd4`). Evidence in `s2b_t19` (build `b3`, archived): bridge frames 169, 171, ..., 225 (29 frames, every second one) differ from MAME by
exactly 320 pixels (row 0), only 53 of 82 are equal to MAME and to a wc3 frame, 70 distinct images instead of 30 (sequence differs at index 13), and the bridge
counter `blank_window_lines=52` (vs 0 in the fixed runs). Detected by the frame comparison against MAME and wc3 (`p1_report.py`) and the `blank_window_lines` counter. Everything else in
`s2b_t19` equals `s2c_t19` (same sums, SRAM, load). `b3` -> `b4` differ only in `rtl/ga2_video.sv` and `tb_p1.cpp` (build `manifest.txt` diff).
(`s2a_t19`, build `b2`, was a capture-side indexing error in the testbench: 0 of 82 equal MAME; superseded.)

## 9. Source identity
- Repository: `ga2-pocket-research` worktree `~/ga2-wt-s2`, branch `sim/s2-pocket-top`, source commit **`e11a57f6b3b6e221df1a31c4ebb7d1a0533a8643`**. Build `b4` recorded `repo HEAD 3d4e1bd` with 1 dirty
  path (the then-uncommitted `experiments/P1-pocket-top`); all 161 files hashed in `b4/manifest.txt` re-hash identically to the committed tree
  (`mismatching_now` = []). Upstream `references/s32` HEAD `accd644d139eb74110668b004abe2b48d8e4b07c`.
- Microcode: `--reset-native` built from MC_REF `HEAD = 3d4e1bd`, builder `m1_m11_microcode.py` sha256 `bfdaff294fd3b9a21599ea2412a4e3bd12c08921d3eacb623d8493ce9102bb07` (prefix `bfdaff294fd3b9a2`, the b53b39a compaction, same as wc3).
  Loaded images (`b4/mc`, UCLOAD check 0 mismatches): m1_cstore.hex `c4727a7203164fb555ab626229ee5ca2650943fc7232ccc811dbf18fde01050a`, m1_dispatch.hex `ef7b92cdb4e12fcec818cd393b3487333eb1af758c82431c1bc40d79aa6cdad0`,
  m1_mode.hex `a4064ff16bd8793515d0683a16c13dadb224732de93af840d01921441fff5f87`. Upstream V25 `microcode.bin` `243e3c78e0390153e5b0a6dd36c5a941a91038992b6fd09b1182c5df9c6dc7d7`.
- ROM inputs (hashes only): main_rom.bin crc32 81f268a7, tiles.bin crc32 c2acca4b, EEPROM image (128 B, all 0xFF (checked); sha256 `e9175db65a9789096ca9cb5524d3abc2107df03e3c9ba3af1aca628f9c5d3bd2`), MAME program dump `ga2u_prog.hex` sha256 `be116e71a2353dcefb92fc2317dfe3e35e45c08a172d7bb645bd0fa4c7a9244a`.
- Simulator binary `b4/obj/p1_sim` sha256 `d46c53572c6ed1a1f98d0864958c94f4f3fc14b89db5a0538d322d78ca65d267` (not committed).
- sha256 of every compiled file (160, from `b4/gen/files.f`) is in the manifest under `source.files_compiled_by_the_build`. The files from this
  repository (not the upstream `references/s32` and jt12 files) are:

| Path | sha256 |
| --- | --- |
| `experiments/P1-pocket-top/rtl/sim_variants/s32_core_p1.sv` | `30e038dee3d1b04d3a83a1d798f1e60bae4de63cf7f6b69a449ee23b30f9b933` |
| `experiments/WC-sim/rtl/sim_variants/s32_io_eep_mame.sv` | `84809d94abf1845add549378313ea75faedc56ea11e59bc4226ccac230c71a41` |
| `experiments/WC-sim/rtl/wc_v25_stub.sv` | `38a82c0462b5783e7c93be250ab60a2f20be4c1e12b4e6cee61472193da12447` |
| `experiments/V2-datapath-floor/rtl/v2_core.sv` | `225bc426d2521a12a1f33aa24d78d435aae6640aef64d5bafe755a7f9e1f4185` |
| `experiments/V2-datapath-floor/rtl/v2_useq.sv` | `833b625906af3781f5e951a7b30e2725060b257fbba5e36691da8698d2a3748f` |
| `experiments/V2-datapath-floor/rtl/v2_regs.sv` | `7007af8b455734819afc14f3bafca3c62a253fa2909a64b9718e69c390f87e9b` |
| `experiments/V2-datapath-floor/rtl/v2_alu.sv` | `ec56a0c63b0a86a84ea1988c11c59b9e587f5ba043f5ab3ef8f01f6281b93c62` |
| `experiments/V2-datapath-floor/rtl/v2_shift.sv` | `951a5ba6c2e9dc61fc26afe648139ac01e44b8703e02f72a90fcbe42928a1db3` |
| `experiments/V2-datapath-floor/rtl/v2_shift_naive.sv` | `d3f8f58b44a7a2f3c390ec4a5fdb569dba9763dffe3eb9b8af5c387c4d1b3934` |
| `experiments/V2-datapath-floor/rtl/v2_shift_iter.sv` | `c8bb418f49b6101af0831edc614f7453355192a7172a8059b9d34b98ea31dcd3` |
| `experiments/V2-datapath-floor/rtl/v2_muldiv.sv` | `101988cc41916586f5ab2f3a2f305f18ec3f192bf21cf90b7c4f137c146aad26` |
| `experiments/V2-datapath-floor/rtl/v2_agu.sv` | `25ade3f68b8e425dc81ff5f51c2cee9a01ca1c1ce5865076b0ec98a13105e694` |
| `experiments/V2-datapath-floor/rtl/v2_bcd_bitfield.sv` | `89746e479b508dd3bba9d31f36a618880079185d03480e9c57d96634cae1cefe` |
| `experiments/V2-datapath-floor/rtl/v2_fetch.sv` | `f84ad979ff414e6b13dc614864df293ff08aa3dd4d8933a1aca7dcc70fbfd6ef` |
| `experiments/V2-datapath-floor/rtl/v2_fetch_byteq.sv` | `d8d3262b09bf7e9a51da34d4bcbac43d6776533a01d1960e19fe84cae11ff726` |
| `experiments/V2-datapath-floor/rtl/v2_psw_irq.sv` | `6a31f9f3d9f09bf407796e37dc9d72a211067fb0a59422c015b08dddea2e7b1f` |
| `experiments/V2-datapath-floor/rtl/v2_bus.sv` | `72f07f0e39114b6c10ccad79bf08c79c85d27562990eed7a313da576d43d8bd8` |
| `experiments/V2-datapath-floor/rtl/v2_glue.sv` | `041f0de75b9f698456781b29e5bf9965f4b74cfced8e56fa42515588ab7ba233` |
| `experiments/V2-datapath-floor/rtl/v2_clz.sv` | `e5ad49d4ad48f5bdd0c50b6ade87dfd4332136151e950a1c8d912c60e5e23f2a` |
| `experiments/M1-functional/tb/v2_sim_observer.sv` | `414ce6403d19be40826711e179d6bd760a8a008c9d27575e91c194c5697a3506` |
| `experiments/M1-functional/tb/v2_sim_checks.sv` | `c37a4c993574dac1c74d738dad45b3b34f593f08cccb35dbaa6bb355cad1826b` |
| `experiments/WC-sim/rtl/wc_watchdog.sv` | `0c79a207075a0c603accde2cfceac72d4807d51ea06dcab4e55947ac650949ac` |
| `experiments/WC-sim/rtl/wc_v2_shim.sv` | `04cac94e02b0e31c1dbdb1da8e49ae88cb05108f28bc24efa2e2e753ccceacc1` |
| `experiments/P1-pocket-top/rtl/sim/dcfifo_sim.sv` | `1b8b5b5fd7d8346751358f33df7b871cfbd4bd389a66444521650483cf0e0986` |
| `experiments/P1-pocket-top/rtl/third_party/data_loader.sv` | `813a9e6e2869dbd9915c07eb7827c08c9ff13e4155ada808be3ef65750008cae` |
| `experiments/P1-pocket-top/rtl/p1_sram_port.sv` | `3bc7c1e096502c3fff489a6af3da0d99bbbfed6670a849a71029a6032a5f09c9` |
| `experiments/P1-pocket-top/rtl/p1_load_wq.sv` | `44c2c1f9f2925eb567ddfadd64c048ca7e438b431296cac28954bd8c2154ca39` |
| `experiments/P1-pocket-top/rtl/p1_slot_verify.sv` | `72b148dc7a2ef10aae893dfb83d31fab2745e25047a5bf35e107923771aa9cd2` |
| `experiments/P1-pocket-top/rtl/ga2_video.sv` | `a2e18be626d2a4a6a5a391f70228fee3457878189684f31445ed3c90258523a5` |
| `experiments/P1-pocket-top/rtl/p1_top.sv` | `593ebad102cee22be9f4984e9d02b88b832093a699974716c3460c6446124c8e` |

Testbench and scripts used:

| Path | sha256 |
| --- | --- |
| `experiments/P1-pocket-top/tb_p1.cpp` | `cd6758179fa0546f243f8fd8805bd3f4e8470452df78517972a3305fc555dad4` |
| `experiments/P1-pocket-top/run_p1.sh` | `02b596b88aba2a1b4cb35f52ba14b4c402e96b62c07bcfcd0008f3bf4244781c` |
| `experiments/P1-pocket-top/run_and_report.sh` | `0c5e2fac848f734ebc555b2e3323b3fc7804db478704128adfd0d38bc1cb833f` |
| `experiments/P1-pocket-top/p1_report.py` | `895cd17033a85b517a644a659cf9fcba2a6c369eed07b10ab227f1365516fef9` |
| `experiments/P1-pocket-top/gen_p1.py` | `fa14273404ccdd981b11c9be0d501d1452ad7334b2bc7d4b8c7da89be8a464c4` |
| `experiments/P1-pocket-top/make_core_variant_p1.py` | `f10c48d4a2666043f1a28e6c75ff9222d80281fbb31ec1b28ae02cf44f5da952` |

For the upstream files under `references/s32` see the manifest (all 130 entries).

## 10. Evidence archive
- Archive: `~/ga2-evidence/P1-S2-2026-11-05/P1-S2-evidence.tar.gz` (outside any git tree), 20,921,858 bytes, **sha256 `bb8791863fe44ee5bb9de7347a873d86203d4d1a4e701c3096377eccc0146bd9`**.
- Contents: all files of `s2b_t19`, `s2c_t19`, `s2d_t38`, `s2e_acc`, `s2f_neg` (87 + 88 + 88 + 88 + 5 files, frames/logs/reports/cmd) plus `s2c_t19.out`, `s2d_t38.out`, `s2e_acc.out`, `s2f_neg.out` = 360 files;
  `FILELIST.txt` and `SHA256SUMS_originals.txt` (per-file sha256 of the originals) sit beside the archive. `s2a_t19` (superseded) and the build/smoke directories are not archived; the originals are untouched.
- Verification: extracted into a fresh scratch directory; `diff -r` on the five run directories and `cmp` on the four `.out` files against the originals found no difference; the extracted file list equals
  `FILELIST.txt`; `shasum -c SHA256SUMS_originals.txt` on the extracted tree: 0 failures. Result: **byte-identical**.

## 11. What this baseline does and does not say
It says: in simulation, with the stated stubs and models, the unmodified upstream `sdram.sv`, external-SRAM work RAM at 19/38 ns, APF-style slot loading and the video bridge draw
the warning screen identically to MAME (5-bit) and to the earlier WC-sim freeze. It does not say: anything about real SDRAM/SRAM I/O timing or board delays, PLL/reset sequencing, the real APF bridge,
sprites, audio, a real V25 program, real 93C46 behaviour, or timing closure. Latency/slack figures hold for this workload only (CPU fetch + refresh + tiles). See the README "Remaining blockers".
