# System 32 core audit — pixel clock and video timing (2026-10-16)

Scope: the RTL under `rtl/` and `Arcade-SegaSystem32.sv`, with pixel clocks as the
main focus. Base commit: `9560ff2`. Simulation only: no RBF, no Quartus fit, no
hardware test.

## Findings, ranked by severity

| # | Severity | Finding | Status |
|---|---|---|---|
| F1 | High | No scandoubler in the core: `forced_scandoubler` is ignored, and "Scandoubler Fx" dims every other native 15 kHz line | Open, follow-up (needs a RAM/fit check) |
| F2 | Medium | CRT Adjust forced the retimer into bypass, which brought back the 8/7-clock 320-mode cadence on Direct Video and the analog DAC | **Fixed**, with a directed test |
| F3 | Medium | The 320-wide dot clock is MAIN/5 = 6.443 MHz, but the board's is OSC2/8 = 6.25 MHz | Open, needs a PCB measurement |
| F4 | Low–Medium | CRT Adjust H-Size of +2 or more (320) and large values (416) stretch the picture into HSync | Open (pre-existing in `crt_adjust` centring) |
| F5 | Low | Retimer grid phase is unanchored on line 0 of the first frame after a 320/416 mode switch | Open, cosmetic |
| F6 | Low | Stale SDC comment: it says `S32_V60_NO_FP` is undefined, but the QSF defines it | Doc only; the SDC handles it |

Checked and found consistent (no action): the 416 raster, VBlank lines, sync
placement, audio clock enables, the 315-5385 timers, and a per-module Verilator
`-Wall` lint (see the end of this report).

---

### F1 — no scandoubler; Scandoubler Fx misbehaves (High, open)

Evidence:
- `Arcade-SegaSystem32.sv` drives `VGA_R/G/B/HS/VS/DE` straight from
  `s32_video_retime`. There is no `arcade_video`/`video_mixer`, and
  `forced_scandoubler` is never read from `hps_io`. `git log -S arcade_video`
  shows the core never had one.
- `VGA_SL = status[4:3]` ("Scandoubler Fx": None/CRT 25/50/75). `sys/sys_top.v:1414`
  feeds it to `scanlines`, which toggles its dimming phase on every HSync
  (`sys/scanlines.v:27`). This dims every other line. Its output drives both the
  VGA DAC and the Direct Video HDMI path (`hr_out = vga_data_sl`,
  `sys_top.v:1773`). With no doubler, any Fx setting other than None dims half of
  the game's own 224 lines on CRT, VGA and Direct Video.
- With `vga_scaler=0` and `forced_scandoubler=1` (a 31 kHz VGA monitor), the
  output stays at 15.7 kHz, so the monitor shows no picture.
- Survey (below): all 24 MiSTer-devel cores read `forced_scandoubler` and send it
  to a scandoubler.

Fix direction: put `video_mixer` (HQ2x off, `LINE_LENGTH` ≥ 416) in the clk_ram
domain after the retimer. Enable it with `forced_scandoubler || fx != 0`, and gate
CRT Adjust off whenever it is enabled (it is already gated on `fx`). This was not
done here because the last fit used **546/553 RAM blocks** and 90 % of ALMs
(`output_files/Arcade-SegaSystem32.fit.summary`, 2026-09-25). The doubler line
buffer needs a native check fit before it can be adopted. Also, 15 clk_ram per
320-wide pixel is odd, so doubled pixels alternate 7/8 clocks (video rule 2). That
is acceptable for a VGA monitor but should be documented.

### F2 — CRT Adjust re-introduced the fractional cadence (Medium, fixed)

`crt_adjust_active = crt_on && !hdmi_output_active && fx == 0`. In Direct Video
mode, `sys_top.v:1837` passes `HDMI_WIDTH = 0`, so CRT Adjust is live on Direct
Video as well as on the analog DAC. The top passed `crt_adjust_active` to the
retimer's `bypass`. Every CRT Adjust stream was therefore re-emitted 1:1 at its
source cadence. At H-Size 0 (for example, using H-Position only), that cadence is
the 7.5-clk_sys quarter-cycle accumulator, which gives 8,7,8,7 clk_sys. That is
the same column-tearing pattern s32_video_retime was written to remove (memory
note s32-direct-video-retime; lessons 284, 2830).

Fix (`rtl/video/s32_video_retime.sv`, `Arcade-SegaSystem32.sv`): the retimer now
receives `crt_active` and the read period `crt_rd_period` (quarter-clk_sys units).
Every `crt_adjust` output register updates on its read enable, HSync included. So
when the period is even, its width is the integer `crt_rd_period/2` clk_ram, and
the existing HSync-anchored uniform grid re-emits it exactly. H-Size 0 gives 15/12,
and every even H-Size step is also uniform. An odd period (a half-clk_ram width)
stays 1:1, as before. A new `hs_in_picture` guard also falls back to 1:1 when the
picture overlaps HSync (F4). There, `crt_adjust` restarts its read phase inside the
picture, and re-anchoring would duplicate a pixel.

Test: `verif/common/tb_crt_retime.sv` (Icarus; added to `verif/run_regression.sh`
step 23). Chain: real `s32_video` → `crt_adjust` → the top's read-enable glue →
clk_sys register → `s32_video_retime`. Each pixel carries its source x. Checks:
no duplicated or dropped pixel within DE, exact spacing for even periods with the
picture clear of HSync, and whole content runs.

| case | fixed RTL | pre-fix RTL (negative control, `-DOLD_PORTS`) |
|---|---|---|
| 320, H-Size 0 | 15..15 clk, 320 px | **FAIL 14..16** |
| 320, H-Pos +6 | 15..15 | **FAIL 14..16** |
| 320, H-Size −4 | 13..13 | **FAIL 12..14** |
| 320, H-Size +2 (overlaps HSync) | 1:1, 16..22, no dup | 16..22 |
| 416, H-Size 0 | 12..12, 416 px | 12..12 |
| 416, H-Size +6, H-Pos −4 (overlaps) | 1:1, 14..24, no dup | 14..24 |
| 320, H-Size +1 (odd) | 1:1, 14..16 | 14..16 |

The existing `tb_video_retime` now uses the new ports and still passes, including
its bypass instance (odd period 31).

Risk: the change sits in the 96.6 MHz output path. It adds a 5-bit period
compare and a mid-point adder fed from quasi-static `status`/mode registers.
Fitted timing has not been checked (no build authorized), and the change is not
hardware-verified on Direct Video or a CRT.

### F3 — 320-wide dot clock differs from the board (Medium, open)

- MacDonald, *Sega System 32 Hardware Notes* (2005), "System timing" table (probed
  board): OSC1 32.2159 MHz, OSC2 50.000 MHz. "320-pixel mode, PCLK = 6.25 MHz
  (OSC2 / 8); 416-pixel mode, PCLK = 8.053975 MHz (OSC1 / 4)". Pixels per line are
  given only as "preliminary ideas", 397.58 / 512.33, back-derived from an
  assumed 60 Hz.
- MAME 0.289 `segas32.cpp:2288-2290` has `set_refresh_hz(60)` and
  `set_size(52*8, 262)`, which is a bitmap size, not a raster (video rule 4).
- Core (`s32_video.sv`, `docs/DESIGN.md:887`): 320 mode is MAIN/5 = 48.317307/7.5
  = 6.4423 MHz, 410 total, 15.713 kHz, 59.98 Hz. That is **3.1 % faster** than
  6.25 MHz, so the 320-wide picture is about 3 % narrower on a CRT than on the
  board.
- The board's 320-mode H-total is unmeasured. At 6.25 MHz, a 400-dot line would
  give 15.625 kHz and 59.64 Hz, so 320-mode games could run up to about 0.6 %
  fast in the core. The 416-mode raster (8.0529 MHz × 512 × 262 = 60.03 Hz,
  −0.0135 % clock scale shared by the whole core) agrees with the 15.75 kHz
  monitor specification (memory note s32-macdonald-hardware-notes).
- Why not changed: 6.25 MHz is 15.46 clk_ram. No uniform grid on the 96.63 MHz
  PLL can produce it, and a single CLK_VIDEO cannot be an integer multiple of both
  6.25 and 8.053975 MHz. A faithful fix needs a second video clock or a clock mux,
  plus the real H-total. **Next step:** measure the HSync period of a 320-mode
  game (for example holo or arabfgt) on a PCB, then decide.

### F4 — CRT Adjust stretch reaches HSync (Low–Medium, open)

`tb_crt_retime` reports "HSync-in-picture" on every line at 320/H-Size +2 and at
416/H-Size +6. The picture's right edge runs into the shifted HSync. At 416/+6,
416 × 15 clk_ram is longer than the line, so only 335 px survive. This is
pre-existing behaviour of the `crt_adjust` centring. Lesson 11456 recommends
measuring a per-H-Size centring table and dropping sizes that overlap HSync. F2's
guard makes these cases 1:1 rather than duplicating pixels.

### F5 — mode-switch phase (Low, open)

`mode_416_active` switches at the frame wrap, but the retimer grid re-anchors only
at the next HSync, at pixel 344/440. On the first frame after a 320↔416 switch,
line 0 can therefore sample near pixel boundaries (one line, once per switch).
The fix needs the core's RGB pipeline depth to anchor at frame start. Not worth
the risk now.

### F6 — stale SDC comment (Low)

`Arcade-SegaSystem32.sdc` says S32_V60_NO_FP "is no longer defined anywhere".
`Arcade-SegaSystem32.qsf:189` defines it, so `fp_a` is absent and the SDC takes its
`info` branch. There is no functional effect.

## Survey: how MiSTer-devel cores drive the pixel clock

Method: `build/pixclk_survey/survey.py` uses the GitHub API to pin each repo's
default-branch SHA, fetches its `emu` top level and generated PLL parameters, and
writes the raw files plus SHA-256 hashes to `build/pixclk_survey/` (local).
24 repos resolved; 4 names did not exist.

| core (SHA) | CLK_VIDEO | declared PLL MHz | output path |
|---|---|---|---|
| Template 5dda3736 | clk_sys | 20 | video_mixer, fsd |
| NeoGeo ffaab14c | CLK_48M | 96.671316 / 48.335658 | video_mixer, fsd |
| Genesis adc0c42c | clk_ram | 53.693175 / 107.386350 | video_mixer `ce_pix(~old_ce_pix & ce_pix)`, fsd |
| MegaCD a3a3da81 | clk_ram | 107.386350 / 53.693175 | same as Genesis |
| S32X 3d4209c6 | clk_ram | 53.693175 / 107.386350 | same as Genesis |
| SNES 23026830 | (in sys glue) | 85.909080 / 42.954540 / 21.477270 | video_mixer, fsd |
| NES 49a0a662 | (in sys glue) | 85.909088 / 42.954544 / 21.477272 | video_mixer, fsd |
| TurboGrafx16 827d45f4 | clk_ram | 85.909090 / 42.954545 | `ce_pix <= ~old_ce & ce_vid`, video_mixer, fsd |
| SMS 8443a0d1 | clk_sys | 53.693175 | video_mixer + screen_rotate, fsd |
| Gameboy f6796f3a | clk_ram | 67.108864 / 33.554432 | video_mixer, fsd |
| GBA 93790a02 | — | 100.663296 / 50.331648 | video_mixer, fsd |
| PSX cd17b5a0 | clk_vid | 33.8688 / 67.7376 / 101.6064 | own path, fsd |
| Saturn a23bbb22 | clk_sys | 57.272799 / 114.545598 | jtframe_resync + video_mixer, fsd |
| Irem M72 15c370e6 | CLK_32M | 96 / 32 | jtframe_resync + video_mixer + screen_rotate, fsd |
| Irem M92 68a4683e | clk_sys | 120 / 40 | sub-module with fsd/fx ports |
| Cave ee191eab | clk_video | 96 / 32 | video_mixer, fsd |
| Taito F2 9438df98 | clk_sys | 106.744 / 53.372 | sub-module with fsd/fx ports |
| Gyruss 526597e3 | — | 49.152 | arcade_video + screen_rotate, fsd |
| GnG 99ce73ff | — | 48 / 24 / 12 | arcade_video, fsd |
| DonkeyKong 737a576b | — | 49.147727 / 24.573863 | arcade_video + screen_rotate, fsd |
| Galaga 7f89f576 | — | 48 / 18 | arcade_video + screen_rotate, fsd |
| Druaga 0fa91623 | — | 49.147727 | arcade_video + screen_rotate, fsd |
| Pacman 6b5ccb08 | — | 48.648 / 24.324 | arcade_video + screen_rotate, fsd |
| Robotron a83916f9 | — | 48 / 12 | arcade_video + screen_rotate, fsd |

("fsd" = reads `forced_scandoubler`. "—" = CLK_VIDEO is assigned inside
`arcade_video`, which uses the core clock.)

What they have in common:
1. **The PLL is retuned to the board crystal so that pixel dividers are integers.**
   Examples: 53.693175 = Genesis MCLK, 21.477272×4 (NES/SNES), 67.108864 =
   16×4.194304 (GB), 33.8688 (PSX), 49.152, 48.648, 106.744. None of the cores
   above relies on a fractional pixel enable as its main cadence.
2. **CLK_VIDEO is one PLL output that is an integer multiple of every pixel rate
   in use.** Cores with several dot clocks (Genesis H32/H40, TG16, SNES hi-res)
   choose the fast clk_ram, or a sys clock that both divide into. When the enable
   comes from a slower domain, they edge-detect it into the CLK_VIDEO domain
   (`~old_ce & ce`). This core's CLK_VIDEO = clk_ram with 15/12-clock pixels
   follows the same pattern.
3. **Every core feeds a scandoubler** (`video_mixer`/`arcade_video` or an
   equivalent sub-module) and honours `forced_scandoubler`. This core is the
   outlier (F1).
4. A core-side resync stage appears where the raster comes from a CPU-programmable
   CRTC (Saturn, M72: `jtframe_resync`), not for fixed rasters.

Comparison: this core's dot clock and CLK_VIDEO follow the common pattern. It
differs in two ways. Its 320 mode uses MAIN/5 instead of the board's own 50 MHz
crystal (F3), because the board itself has two unrelated pixel oscillators, which
none of the surveyed single-oscillator cores has to handle. And it has no
scandoubler (F1).

## Other subsystems checked

- **Raster (416):** `s32_video.sv` gives 512×262, VBlank lines 224–261 (IN at 224,
  OUT at 0, matching MacDonald), HSync pixels 440–471, VSync lines 234–236. HSync,
  VSync and their distance from the picture are constant. The line is 3072
  clk_sys = 6144 clk_ram, an exact multiple of 12.
- **Raster (320):** 410 total = 3075 clk_sys = 6150 clk_ram, an exact multiple of
  15. The accumulator returns to the same phase each line (3075×32 = 410×240).
- **Audio enables** (`s32_audio_ce.sv`): Z80/FM 715924818/2^32 × 48.317307 =
  8.054 MHz (MAIN/4), PCM 12.500 MHz (50 MHz/4). Both match MAME
  `segas32.cpp:2259/2296/2305`.
- **315-5385 timers** (`s32_io.sv:940-990`): timer 0 is 0x800·N ticks of MAIN/2
  (×3 clk_sys), timer 1 is 0x100·N ticks of 50 MHz/16 through an NCO. Both match
  MAME `segas32.cpp:759-783`.
- **Lint:** Verilator 5.032 `-Wall` per module, over 25 tops (`build/lint_run.sh`).
  It reported only width or blocking-style warnings, and every one was inspected
  as benign: tilemap layer indices are guarded, V60 `disp_of(x-3'd4)` arguments
  are 2-bit by construction, and the mixer `>>>3` is in range. There are no
  latch, multidriven or undriven-in-use findings.
- **Clock-timing guidance** (`reference/mister-clock-timing.md`): §1 (pixel-rate
  arithmetic above) and §2 (the retimer remains a CE in clk_ram) apply. No
  PLL/SDC/exception change was made, so §3–§5 are not exercised. §7: no fitted
  timing is claimed.

## Regression

`verif/run_regression.sh` (all 38 steps) was run twice in parallel with
fail-and-continue: once in a clean `HEAD` worktree (baseline) and once in the
fixed tree. The wrapper is `build/regress_cont.sh`; the logs are
`build/reg_base.log` and `build/reg_new.log`.

- **New:** `CRT RETIME: PASS`. PASS count 76 → 77.
- **Pre-existing, identical in both runs (not caused by this change):**
  CORE UNIVERSAL BOOT, CORE S32-ONLY BOOT, V60 DIFF (50/50), MIXER, MIXER PIXEL
  LATENCY, MIXER DIFFERENTIAL, V60 FETCH MODE and V60 FETCH RAM ("Preprocessor
  failed"), TILEMAP VRAM, CORE MAP DECODE.
- **Environmental, not comparable:** the V25 firmware runner needs
  `roms/sim/ga2/mcu.bin` (absent in the worktree), and in the main tree it hit a
  CRLF `vvp` interpreter line.
- `tb_video_retime` (updated ports) passes in both runs.

No Verilator simulation was run for this change; only a Verilator lint was. So no
waveform or frame screenshots exist, and the directed test is Icarus.
