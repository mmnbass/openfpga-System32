#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""D2 Pocket top (synthesizable set): regenerates the three DERIVED RTL files in rtl/derived/ from frozen S2 / WC-sim sources.
Every textual replacement must match exactly the stated number of times; the inputs are never modified.

  s32_core_d2.sv       = experiments/P1-pocket-top/rtl/sim_variants/s32_core_p1.sv (S2 variant, itself checked against
                         upstream by make_core_variant_p1.py --check) + two debug outputs for the diagnostic band:
                         dbg_irq_ack (V2's interrupt acknowledge, upstream leaves .irq_ack() open) and dbg_irq_vector.
  d2_sram_port.sv      = experiments/P1-pocket-top/rtl/p1_sram_port.sv with the module renamed and its one
                         simulation-only $display line removed (the rd_edges check); logic unchanged.
  s32_core_d2_fit.sv   = s32_core_d2.sv + F2's sprite-RAM edit (experiments/F2-fullchip/patch_upstream.py PATCHES, imported):
                         sprite_ram s32_big_dpram 64K x 16 (128 M10K in E1) -> f2_xram_standin (512 words, XOR-folded address).
                         The plan's first-pixels core (docs/pocket-first-pixels-plan.md section 1) and its M10K budget
                         (section 2: F2's 265) assume this stand-in; the S2 core kept the full RAM.
  s32_soundsys_d2.sv   = references/s32/rtl/audio/s32_soundsys.sv + F2's wave-RAM edit (same PATCHES): EXTERNAL_WAVE_RAM 1
                         (upstream's external path through sdr_p4 / sdr_wave_wr, answered by the D2 stubs; ce_pcm = ce_z80 = 0,
                         so it is inert here). Also part of F2's 265 M10K basis.
  wc_v25_stub_hw.sv    = experiments/WC-sim/rtl/wc_v25_stub.sv (module name kept: the core variant instantiates
                         wc_v25_stub) with the $display access log removed and the 48-byte table written as a case ROM
                         (the initial-block concatenation of the original is a simulation idiom); the bytes are parsed
                         from the original and the read function is otherwise identical.
  s32_sprite_d2.sv     = references/s32/rtl/video/s32_sprite.sv + the r2 timing patch (gameplay build 1 retry r2): the combinational
                         clip_x_min / clip_x_max (functions of clip_en, clip_l, clip_r, hpix) are registered every clk_ram cycle and the
                         registered values are used at the two use sites (R_PIXEL end-of-row test, R_EMIT fast_continue). The one-cycle
                         latency is safe because the clip registers / hpix are written only in R_RENDER, R_DECODE (clip command), R_SWAP
                         and reset, each followed by at least 11 clocks before R_PIXEL / R_EMIT is reachable (argument in the file header
                         and in the README); a simulation-only (`ifndef SYNTHESIS) check flags any use in R_PIXEL / R_EMIT in a cycle in
                         which an input differs from the previous cycle or the registered value differs from the combinational one.
                         + the r3 timing patch (gameplay build 1 retry r3, SPRITE_R3 below): the row-advance and x-position arithmetic
                         (yacc + ystep, dy + 1, xacc + xstep, scrx, scry, the source byte address, the R_PIXEL end-of-row test and the
                         R_EMIT next-pixel tests) is precomputed in registers that are provably equal to upstream's combinational
                         expressions in every cycle in which they are consumed (free-running sums + one-cycle write-history flags, and
                         lockstep registers updated at every write site of their inputs). Cycle-exact by construction; the
                         `ifndef SYNTHESIS block checks every precomputed value against upstream's expression ($fatal on any difference).
  --- SP configuration (exp/sprite-int: the sprite path of experiments/SP-sprite-path integrated; gen_d2.py --config sp) ---
  (the sp configuration uses s32_sprite_d2.sv above, the r3 engine, as the fit configuration does; gen_d2.py selects it)
  s32_core_d2_sp.sv    = s32_core_d2.sv + sprite_ram s32_big_dpram 64K x 16 -> SP's sp_spriteram_win (22 M10K window, 11 x 1K-word pages,
                         same parameters), its CPU read strobe (rising edge of m_req && !m_we && sel_sprram) and its three
                         out-of-window counters exported as dbg_spr_oow_wr / dbg_spr_oow_rd / dbg_spr_oow_eng.
  d2_load_wq3.sv       = experiments/P1-pocket-top/rtl/p1_load_wq.sv with a third loader input (slot 3, sprite ROM).
  d2_slot_verify3.sv   = rtl/d2_slot_verify.sv with a third region (slot 3, sprite ROM; sum2 / fl2, same two checksums and the
                         same schedule per region); done after all three.
  d2_core_top_sp.sv    = rtl/d2_core_top.sv (module name core_top kept) with: upstream sdram -> SP's derived controller
                         sp_sdram (p2 = the core's sprite-ROM port, fw/fr = the framebuffer client); the
                         framebuffer stubs st_fe / st_fr and the p2 stub st_p2 -> SP's sp_fb_sdram (fb_wr_busy / fb_rd_pix
                         from it); a third data_loader (slot 3 sprites.bin, bridge 0x3000_0000 -> SDRAM 0x100_0000) into
                         d2_load_wq3; the core's out-of-window counters wired out (observation only); the slot-3 size latch, d2_slot_verify3 and the
                         expected sprite sums EXP_SUM2 / EXP_FL2 (band cell 5 = tile sums AND sprite sums);
                         GA2Play5: the freeze readout (rtl/d2_freeze_readout.sv: d2_freeze_cap on clk_sys, d2_freeze_band on
                         clk_vid, DE lines 211-219 above the band) fed by the core's dbg_v2 / dbg_bus. Nothing else.
  --- GA2Play5 freeze readout (sp configuration only; the fit configuration's compiled set is unchanged) ---
  v2_core.sv           = experiments/V2-datapath-floor/rtl/v2_core.sv (module name v2_core KEPT: the SDC selects
                         s32_v60:v60|v2_core:core) + ONE observation output dbg_v2 [95:0] = {window[31:0], ipc, upc, cw[19:8],
                         cw[2:0], adv, sh_busy, fetch_short, data_busy, 1'b0}: a continuous assign of existing nets, no logic,
                         no register, nothing else changed. gen_d2.py --config sp compiles it INSTEAD of the V2 tree's file.
  d2_v60_hw.sv         = rtl/d2_v60_hw.sv (module name s32_v60 kept) + the output dbg_v2, connected to v2_core's. Compiled
                         instead of rtl/d2_v60_hw.sv in the sp configuration (it is the sp build's one V2 wrapper).
  (s32_core_d2_sp.sv above additionally gets dbg_v2 from the v60 instance and dbg_bus [63:0] = {if_addr, bus_addr[23:0],
   if_req, if_ack, bus_req, bus_we, bus_ack, m_req, m_ack, irq_n, 8'd0}: existing nets only.)
  --- AU3 configuration only (exp/audio3, GA2Audio3; gen_d2.py --config au3) ---
  s32_sprite_d2_au3.sv = s32_sprite_d2.sv (r2 + r3, unchanged and still compiled by fit / sp / au) + the r4 timing patch
                         (SPRITE_R4): row_byte_base <= adv_q ? rbb_inc_q : rbb_cur_q, two free-running registers holding upstream's
                         R_ROW expression of yacc_inc / yacc one cycle early (proof in the file header; SIMCHECK s32_sprite r4).
                         (d2_core_top_au3.sv / d2_diag_band_au3.sv, the other au3 files, are described with their functions.)
Usage: make_d2_derived.py            write rtl/derived/*
       make_d2_derived.py --check    exit 1 unless the committed files equal the regenerated ones"""
import re, subprocess, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
P1 = ROOT / "experiments/P1-pocket-top"
OUT = HERE / "rtl/derived"

def apply(text, reps, what):
    for a, b, n in reps:
        c = text.count(a)
        assert c == n, f"{what}: '{a[:70]}' found {c}x, expected {n}"
        text = text.replace(a, b)
    return text

def core():
    r = subprocess.run([sys.executable, "-B", str(P1 / "make_core_variant_p1.py"), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, "S2 core variant does not regenerate: " + r.stdout + r.stderr
    src = (P1 / "rtl/sim_variants/s32_core_p1.sv").read_text()
    body = apply(src, [
        ("    input             wram_ext_rdy,    // the external port has sampled the data for the current read\n",
         "    input             wram_ext_rdy,    // the external port has sampled the data for the current read\n"
         "    // D2 VARIANT: V2 interrupt acknowledge and the pending vector, for the on-screen diagnostic band only\n"
         "    output            dbg_irq_ack,\n"
         "    output      [7:0] dbg_irq_vector,\n", 1),
        ("wire [7:0]  irq_vector;\n", "wire [7:0]  irq_vector;\nassign dbg_irq_vector = irq_vector;   // D2 VARIANT (diagnostic band)\n", 1),
        (".irq_n(irq_n), .irq_vector(irq_vector), .irq_ack(),",
         ".irq_n(irq_n), .irq_vector(irq_vector), .irq_ack(dbg_irq_ack),   // D2 VARIANT: was .irq_ack() (unused upstream)", 1),
        # D2-play: the stub now answers the WHOLE 0xA00000-0xA00FFF window (MAME DPRAM table, see v25()), not only A00100-A0015E
        ("wire        v25_stub_sel = m_req && !m_we && sel_v25 && (A[11:1] >= 11'h080) && (A[11:1] <= 11'h0AF);",
         "wire        v25_stub_sel = m_req && !m_we && sel_v25;   // D2-play: whole DPRAM window (S2 variant: A[11:1] 080..0AF only)", 1),
    ], "s32_core_p1")
    hdr = ("// SPDX-License-Identifier: GPL-3.0-or-later\n//\n"
           "// *** D2 POCKET-TOP DERIVED COPY (exploratory; not a hardware decision). Generated by\n"
           "// experiments/D2-pocket-top/make_d2_derived.py from experiments/P1-pocket-top/rtl/sim_variants/s32_core_p1.sv\n"
           "// (the frozen S2 variant, below verbatim apart from) + outputs dbg_irq_ack / dbg_irq_vector for the diagnostic band.\n"
           "// The only simulation-only text is the S2 variant's `ifdef SIMULATION port-B write report.\n")
    return hdr + body

def sram():
    src = (P1 / "rtl/p1_sram_port.sv").read_text()
    body = apply(src, [
        ("module p1_sram_port (", "module d2_sram_port (", 1),
        ("always @(posedge clk) if (rd_edges == 3'd0) $display(\"P1 SRAM PORT ERROR: rd_edges must be 1..7\");\n\n", "", 1),
    ], "p1_sram_port")
    hdr = ("// SPDX-License-Identifier: GPL-3.0-or-later\n//\n"
           "// D2 DERIVED COPY of experiments/P1-pocket-top/rtl/p1_sram_port.sv (generated by make_d2_derived.py): module renamed to\n"
           "// d2_sram_port, the simulation-only rd_edges $display removed. The port logic is unchanged; the D2 top ties rd_edges\n"
           "// to a constant parameter. sample_now / n_reads / n_writes / overlap are observation outputs (unconnected in\n"
           "// synthesis, pruned).\n")
    return hdr + body.replace("// SPDX-License-Identifier: GPL-3.0-or-later\n//\n", "//\n", 1)

# D2-play: what the main CPU can read of the V25's 2 KB DPRAM (word j at 0xA00000 + 2j reads {8'hFF, dp[j]}), measured in MAME
# 0.289 ga2u (fresh NVRAM; dump of the main CPU's program space 0xA00000-0xA00FFE at MAME frames 1, 30, 661 and 700: all four
# identical; also identical on the V25 side, frames 1..3900). Nonzero low bytes only: j 0..15 (the 4-entry stage-start table the
# main CPU reads at A00000-A00006 at stage start / respawn), j 0x080..0x0AF (the handshake string, S2's window) and j 0x7FE, 0x7FF.
# Every other word reads {8'hFF, 8'h00}. The upper byte of every read is 0xFF (byte-lane bus shape, as upstream's {8'hff, v25_q}).
DP_TABLE = bytes.fromhex("0A00C500 11001100 18001800 1F00C600".replace(" ", ""))   # dp[0..15]
DP_TOP = {0x7FE: 0x22, 0x7FF: 0x04}

def v25():
    src = (ROOT / "experiments/WC-sim/rtl/wc_v25_stub.sv").read_text()
    m = re.search(r"\} = \{(.*?)\};", src, re.S)
    vals = [int(x, 16) for x in re.findall(r"8'h([0-9A-Fa-f]{2})", m.group(1))]
    assert len(vals) == 48, len(vals)
    assert bytes(vals) == b"wake up! GOLDEN AXE The Revenge of Death-Adder! ", bytes(vals)
    assert len(DP_TABLE) == 16
    cases = "\n".join(f"    6'd{i}: tb = 8'h{v:02X};" for i, v in enumerate(vals))
    tcases = "\n".join(f"    4'd{i}: tt = 8'h{v:02X};" for i, v in enumerate(DP_TABLE))
    return f"""// SPDX-License-Identifier: GPL-3.0-or-later
//
// D2 DERIVED, EXTENDED COPY of experiments/WC-sim/rtl/wc_v25_stub.sv (generated by experiments/D2-pocket-top/make_d2_derived.py).
// STUB for the V25 sound/protection MCU's DPRAM as the MAIN CPU reads it (0xA00000-0xA00FFF, 2,048 words). NOT the V25.
// Read value = {{8'hff, dp[j]}} for word j = addr[11:1] (the bus shape of upstream's real-V25 path). dp[j] is the MAME ga2u
// dump (constant from frame 1 to 3900): the 16-byte stage-start table at j 0..15 (0A 00 C5 00 11 00 11 00 18 00 18 00 1F 00
// C6 00; the main CPU reads j 0..3 at stage start / respawn), the 48-byte handshake string "wake up! GOLDEN AXE The Revenge of
// Death-Adder! " at j 0x080..0x0AF (main address A00100-A0015E, S2's window) and dp[0x7FE] = 22, dp[0x7FF] = 04; every other
// word is 00 (the original stub returned 16'hffff outside the string window). Writes are ignored. The $display access log is
// removed and the tables are case ROMs. clk/we/sel are kept for port compatibility (unused, as in the original's read path).
module wc_v25_stub (
    input             clk,
    input             sel,
    input             we,
    input      [11:1] addr,
    output     [15:0] rdata
);
wire        win = (addr >= 11'h080) && (addr <= 11'h0AF);       // main A00100-A0015E
wire  [5:0] idx = 6'(addr - 11'h080);
wire        tbl16 = (addr[11:5] == 7'd0);                       // j 0..15
wire        top2  = (addr[11:1] == 11'h7FE) || (addr[11:1] == 11'h7FF);
reg   [7:0] tb;
always @(*) begin
    case (idx)
{cases}
    default: tb = 8'hFF;
    endcase
end
reg   [7:0] tt;
always @(*) begin
    case (addr[4:1])
{tcases}
    default: tt = 8'h00;
    endcase
end
wire  [7:0] top_v = (addr[1] == 1'b0) ? 8'h22 : 8'h04;
assign rdata = {{8'hff, win ? tb : tbl16 ? tt : top2 ? top_v : 8'h00}};
endmodule
"""

sys.path.insert(0, str(ROOT / "experiments/F2-fullchip"))
from patch_upstream import PATCHES as F2P  # noqa: E402

def core_fit():
    spr = [(a, b, 1) for a, b in F2P["s32_core.sv"] if "sprite_ram" in a]
    assert len(spr) == 1, "F2 sprite-RAM patch not found"
    body = apply(core(), spr, "s32_core_d2 (F2 sprite-RAM edit)")
    return body.replace("// The only simulation-only text is",
                        "// FIT CONFIGURATION: + F2's sprite-RAM edit (sprite_ram -> f2_xram_standin, 512 words, XOR-folded).\n"
                        "// The only simulation-only text is", 1)

def soundsys():
    src = (ROOT / "references/s32/rtl/audio/s32_soundsys.sv").read_text()
    body = apply(src, [(a, b, 1) for a, b in F2P["audio/s32_soundsys.sv"]], "s32_soundsys (F2 wave-RAM edit)")
    return ("// SPDX-License-Identifier: GPL-3.0-or-later\n// D2 FIT-CONFIGURATION DERIVED COPY of references/s32/rtl/audio/s32_soundsys.sv"
            " (make_d2_derived.py): F2's wave-RAM edit (experiments/F2-fullchip/patch_upstream.py), nothing else.\n" + body)

SPRITE_HDR = """// D2 DERIVED COPY of references/s32/rtl/video/s32_sprite.sv (generated by experiments/D2-pocket-top/make_d2_derived.py; upstream
// is never edited). TWO timing patches, nothing else. r2 (gameplay build 1 retry r2; build 1 failed clk_ram setup -0.605 / -0.690 ns on
// clip_l / clip_r -> yacc / pixel_scrx through the combinational clip_x_min / clip_x_max):
//   clip_x_min / clip_x_max are registered every clk_ram cycle (clip_x_min_q / clip_x_max_q, no enable, no reset) and the registered
//   values are used where upstream computed them inline (R_PIXEL end-of-row test; R_EMIT fast_continue). The expressions are
//   upstream's, character for character.
// WHY ONE CYCLE OF LATENCY IS EXACT. The inputs of the expression are clip_en, clip_l, clip_r (written only by reset, R_RENDER and
// the R_DECODE set-clip command; all three go to R_FETCH) and hpix = f(ctl_latched[6][0]) (written only by reset and R_SWAP, which
// goes to R_ERASE or R_RENDER). The only consumers are R_PIXEL and R_EMIT. From R_FETCH the walker needs R_FETCH, R_FETCHP,
// R_FETCHW x8, R_DECODE (draw), R_SCALE, [R_INDTAB..], R_ROW before R_PIXEL: at least 12 clocks (R_EMIT is entered only from
// R_PIXEL). After an input write the registered value is therefore up to date >= 11 clocks before its first use, and while the
// inputs are stable the register equals the combinational value. The `ifndef SYNTHESIS block below checks this in simulation.
//
// + r3 TIMING PATCH (gameplay build 1 retry r3; r2 failed clk_ram setup -0.585 ns slow 0C / -0.342 ns slow 85C: dx / pixel_piw ->
// yacc / dy (the end-of-row and end-code tests feeding the yacc + ystep adder) and the source-address adder -> pixel_scrx, i.e. the
// R_PIXEL / R_EMIT decision cones). Behaviour is cycle-identical to upstream: every precomputed value below equals upstream's
// combinational expression in every cycle in which it is consumed. Producer -> consumer distances (clk_ram cycles; "write" = a
// non-blocking assignment that takes effect at the end of the cycle):
//   yacc, dy   written by R_DECODE (draw: <= 0) and by the row advance (R_ROW skip, R_PIXEL end of row, R_SCAN / R_EMIT end code:
//              += ystep / += 1, all -> R_ROW). Consumed (as yacc + ystep, dy + 1) at those same four advance sites. Distance >= 1
//              (R_ROW skip -> R_ROW, advance -> R_ROW), >= 2 to R_PIXEL / R_SCAN / R_EMIT (via a non-writing R_ROW cycle).
//   ystep      written only by R_SCALE (scale_done; yacc is 0 then, set by R_DECODE, untouched until R_SCALE) -> R_ROW: distance >= 1.
//   xacc, dx   written by R_ROW (visible row: <= 0) and R_EMIT (no end code: += xstep / += 1). xacc + xstep consumed only in R_EMIT
//              (distance >= 2 after R_ROW, 1 after R_EMIT); dx / scrx / byte address consumed in R_PIXEL (distance 1 after R_ROW or
//              R_EMIT) and next_dx in R_EMIT (distance 1).
//   xstep      R_SCALE only; >= 3 clocks before R_EMIT.
//   x0, y0     R_DECODE (draw) only; sw[] (d_dstw, d_dsth, d_flipx, d_flipy, d_bpp8, ...) R_FETCHW only: >= 51 clocks (the divider)
//              before R_SCALE completes, >= 52 before any R_ROW.
//   row_byte_base  every R_ROW cycle; consumed in R_PIXEL (distance 1 after the visible R_ROW) and R_EMIT (>= 2).
//   pixel_scrx R_PIXEL (cache hit) and R_EMIT (fast_continue); consumed in R_EMIT (distance 1).
//   clip_x_*_q (r2) follow clip_en / clip_l / clip_r / hpix by one cycle; those are written only by reset, R_RENDER, R_DECODE (clip)
//              and R_SWAP, >= 12 clocks before R_ROW; nothing writes them inside a row.
//   Inside a row (R_PIXEL, R_EMIT, R_SCAN, the ROM / RAM fetch and verify states and their waits on srom_ack) only R_PIXEL and R_EMIT
//   write any of these inputs; a stall (srom_ack wait, verify retry, fb_busy in R_ROW / R_DONE) writes none of them, so every
//   precomputed value stays equal to its expression for the whole stall.
// Structure:
//   (1) free-running sums (registered every cycle, no enable, no reset): yacc_p1/p2 = yacc + ystep / + 2 ystep, dy_p1/p2 = dy + 1 / + 2,
//       xacc_p1/p2 = xacc + xstep / + 2 xstep, bnx1/2 = row_byte_base + offset(xacc + xstep / + 2 xstep), xst_q / ys0_q = scrx / scry
//       at dx = 0 / dy = 0, cf0s_q = the end-of-row clip test at dx = 0. Each equals its expression one cycle late, i.e. exactly when
//       the previous cycle wrote none of its inputs.
//   (2) one-cycle write-history flags: adv_q (the previous cycle advanced yacc / dy by ystep / 1), zy_q (it set yacc <= 0 or loaded
//       ystep; yacc == 0 then), zd_q (it set dy <= 0), emit_q (it advanced xacc by xstep). Hence, in EVERY cycle:
//       yacc_inc = adv_q ? yacc_p2 : zy_q ? ystep : yacc_p1 == yacc + ystep;   dy_inc = adv_q ? dy_p2 : zd_q ? 1 : dy_p1 == dy + 1;
//       and in every R_EMIT cycle (its predecessor is R_PIXEL, which writes none, or R_EMIT, which advanced xacc by xstep):
//       xacc_inc = emit_q ? xacc_p2 : xacc_p1 == xacc + xstep;   bnx_inc == row_byte_base + offset(xacc + xstep).
//       (modular: (a + b) + b == a + 2b mod 2^25 / 2^10 / 2^24.)
//   (3) lockstep registers, assigned at EVERY write site of their inputs with the value the expression takes after that write:
//       scrx_q (= scrx(dx)), eq0_q (= dx == d_dstw), cf0_q (= the end-of-row clip test of scrx), ltn_q (= next_dx < d_dstw),
//       byteaddr (bfirst_q ? row_byte_base : byteaddr_q; = row_byte_base + offset(xacc)): at the visible R_ROW (dx <= 0, xacc <= 0) and
//       at R_EMIT (dx += 1, xacc += xstep; scrx(dx + 1) = scrx(dx) -+ 1, no wrap since dx < d_dstw <= 1023 there); valid in every
//       in-row cycle. okn_q (= R_EMIT's clip test of next_scrx = pixel_scrx -+ 1): at both writes of pixel_scrx (R_PIXEL hit, R_EMIT
//       fast_continue). scry_q (= scry(dy)): loaded from ys0_q at R_SCALE (dy == 0) and stepped -+ 1 at every row advance; valid
//       in every R_ROW cycle.
//   No consumer reads a precomputed value in a cycle in which it can differ from the expression; the `ifndef SYNTHESIS block
//   (SIMCHECK s32_sprite r3) compares each one with upstream's expression, verbatim, in every such cycle and calls $fatal otherwise.
"""
SPRITE_R3_REG = """
// D2 VARIANT r3 (timing): precomputed row-advance / x-position arithmetic (see the header). r3_pos is upstream's scrx / scry form.
function automatic signed [13:0] r3_pos(input signed [13:0] o, input [9:0] w, input fl, input [9:0] d);
    r3_pos = fl ? (o + $signed({4'b0000, w}) - 14'sd1 - $signed({4'b0000, d})) : (o + $signed({4'b0000, d}));
endfunction
// upstream's R_PIXEL end-of-row clip test (true = past the inclusive clip edge); R_EMIT's next-pixel test is its complement
function automatic r3_clipfail(input signed [13:0] s, input fl, input signed [15:0] mn, input signed [15:0] mx);
    r3_clipfail = (!fl && s > mx) || (fl && s < mn);
endfunction
// upstream's R_PIXEL source byte offset of a given x accumulator
function automatic [23:0] r3_boff(input [24:0] xa, input b8);
    r3_boff = b8 ? {14'b0, 1'b0, xa[24:16]} : {15'b0, xa[24:17]};
endfunction
reg  [24:0] yacc_p1, yacc_p2, xacc_p1, xacc_p2;     // (1) free-running
reg   [9:0] dy_p1, dy_p2;
reg  [23:0] bnx1_q, bnx2_q;
reg  signed [13:0] xst_q, ys0_q;
reg         cf0s_q;
reg         adv_q, zy_q, zd_q, emit_q;              // (2) write-history flags
reg  signed [13:0] scrx_q, scry_q;                  // (3) lockstep
reg         eq0_q, cf0_q, ltn_q, okn_q, bfirst_q;
reg  [23:0] byteaddr_q;
always @(posedge clk) begin
    yacc_p1 <= yacc + ystep;
    yacc_p2 <= yacc + {ystep[23:0], 1'b0};
    dy_p1   <= dy + 10'd1;
    dy_p2   <= dy + 10'd2;
    xacc_p1 <= xacc + xstep;
    xacc_p2 <= xacc + {xstep[23:0], 1'b0};
    bnx1_q  <= row_byte_base + r3_boff(xacc + xstep, d_bpp8);
    bnx2_q  <= row_byte_base + r3_boff(xacc + {xstep[23:0], 1'b0}, d_bpp8);
    xst_q   <= r3_pos(x0, d_dstw, d_flipx, 10'd0);
    ys0_q   <= r3_pos(y0, d_dsth, d_flipy, 10'd0);
    cf0s_q  <= r3_clipfail(xst_q, d_flipx, clip_x_min_q, clip_x_max_q);
end
wire [24:0] yacc_inc   = adv_q ? yacc_p2 : (zy_q ? ystep : yacc_p1);
wire  [9:0] dy_inc     = adv_q ? dy_p2 : (zd_q ? 10'd1 : dy_p1);
wire [24:0] xacc_inc   = emit_q ? xacc_p2 : xacc_p1;
wire [23:0] bnx_inc    = emit_q ? bnx2_q : bnx1_q;
wire [23:0] byteaddr_r = bfirst_q ? row_byte_base : byteaddr_q;
`ifndef SYNTHESIS
// SIMULATION ONLY (r3): every precomputed value must equal upstream's combinational expression (verbatim below, with r2's
// combinational clip_x_*_c) in every cycle in which it is consumed, and over the wider windows named; else $fatal.
//   every cycle after the first draw decode: yacc_inc, dy_inc          every R_ROW cycle: scry_q, xst_q, cf0s_q
//   every in-row cycle: scrx_q, eq0_q, cf0_q, ltn_q, byteaddr_r        every R_EMIT cycle: okn_q, xacc_inc, bnx_inc
integer r3c_inc, r3c_row, r3c_inrow, r3c_emit, r3c_adv2, r3c_zero, r3c_emit2, r3c_first, r3c_bad;
reg     r3_armed;
initial begin
    r3c_inc = 0; r3c_row = 0; r3c_inrow = 0; r3c_emit = 0; r3c_adv2 = 0; r3c_zero = 0; r3c_emit2 = 0; r3c_first = 0; r3c_bad = 0;
    r3_armed = 1'b0;
end
wire r3_inrow = rs == R_PIXEL || rs == R_EMIT || rs == R_SCAN || rs == R_ROWDATA || rs == R_ROWDATAW || rs == R_ROM_GAP ||
                rs == R_ROM_VERIFY || rs == R_ROM_VERIFYW || rs == R_ROM_RETRY || rs == R_RAMDATA || rs == R_RAMDATAP || rs == R_RAMDATAW;
wire signed [13:0] up_scrx  = d_flipx ? (x0 + $signed({4'b0000, d_dstw})
                              - 14'sd1 - $signed({4'b0000, dx}))
                           : (x0 + $signed({4'b0000, dx}));
wire signed [13:0] up_scrx0 = d_flipx ? (x0 + $signed({4'b0000, d_dstw})
                              - 14'sd1 - $signed({4'b0000, 10'd0}))
                           : (x0 + $signed({4'b0000, 10'd0}));
wire signed [13:0] up_scry  = d_flipy ? (y0 + $signed({4'b0000, d_dsth})
                              - 14'sd1 - $signed({4'b0000, dy}))
                           : (y0 + $signed({4'b0000, dy}));
wire        [9:0]  up_sx_px = {1'b0, xacc[24:16]};
wire       [23:0]  up_byteaddr = row_byte_base
                     + (d_bpp8 ? {14'b0, up_sx_px} : {15'b0, up_sx_px[9:1]});
wire               up_rowend = dx == d_dstw ||
                (!d_flipx && up_scrx > clip_x_max_c) ||
                ( d_flipx && up_scrx < clip_x_min_c);
wire               up_cf0 = (!d_flipx && up_scrx > clip_x_max_c) || ( d_flipx && up_scrx < clip_x_min_c);
wire               up_cf00 = (!d_flipx && up_scrx0 > clip_x_max_c) || ( d_flipx && up_scrx0 < clip_x_min_c);
wire signed [13:0] up_next_scrx = d_flipx ? (pixel_scrx - 14'sd1)
                                        : (pixel_scrx + 14'sd1);
wire        [9:0]  up_next_dx = dx + 1'd1;
wire               up_ltn = up_next_dx < d_dstw;
wire               up_okn = (!d_flipx && up_next_scrx <= clip_x_max_c) ||
                                 ( d_flipx && up_next_scrx >= clip_x_min_c);
wire       [24:0]  up_xacc_n = xacc + xstep;
wire        [9:0]  up_sxn = {1'b0, up_xacc_n[24:16]};
wire       [23:0]  up_byteaddr_n = row_byte_base
                     + (d_bpp8 ? {14'b0, up_sxn} : {15'b0, up_sxn[9:1]});
wire       [24:0]  up_yacc_n = yacc + ystep;
wire        [9:0]  up_dy_n = dy + 1'd1;
always @(posedge clk) begin
    if (!rst && rs == R_DECODE) r3_armed <= 1'b1;
    if (!rst && r3_armed) begin
        r3c_inc <= r3c_inc + 1;
        if (adv_q) r3c_adv2 <= r3c_adv2 + 1;
        if (zy_q || zd_q) r3c_zero <= r3c_zero + 1;
        if (yacc_inc !== up_yacc_n || dy_inc !== up_dy_n) begin
            r3c_bad <= r3c_bad + 1;
            $display("SIMCHECK FAIL s32_sprite r3 yacc_inc/dy_inc rs=%0d adv=%b zy=%b zd=%b yacc_inc=%h exp=%h dy_inc=%0d exp=%0d",
                     rs, adv_q, zy_q, zd_q, yacc_inc, up_yacc_n, dy_inc, up_dy_n);
            $fatal(1, "s32_sprite r3: yacc_inc / dy_inc differ from yacc + ystep / dy + 1");
        end
    end
    if (!rst && rs == R_ROW) begin
        r3c_row <= r3c_row + 1;
        if (scry_q !== up_scry || xst_q !== up_scrx0 || cf0s_q !== up_cf00) begin
            r3c_bad <= r3c_bad + 1;
            $display("SIMCHECK FAIL s32_sprite r3 R_ROW scry_q=%0d exp=%0d xst_q=%0d exp=%0d cf0s_q=%b exp=%b",
                     scry_q, up_scry, xst_q, up_scrx0, cf0s_q, up_cf00);
            $fatal(1, "s32_sprite r3: R_ROW precomputed value differs from upstream's expression");
        end
    end
    if (!rst && r3_inrow) begin
        r3c_inrow <= r3c_inrow + 1;
        if (rs == R_PIXEL && bfirst_q) r3c_first <= r3c_first + 1;
        if (scrx_q !== up_scrx || eq0_q !== (dx == d_dstw) || cf0_q !== up_cf0 || (eq0_q || cf0_q) !== up_rowend ||
            ltn_q !== up_ltn || byteaddr_r !== up_byteaddr) begin
            r3c_bad <= r3c_bad + 1;
            $display("SIMCHECK FAIL s32_sprite r3 in-row rs=%0d scrx_q=%0d exp=%0d eq0=%b cf0=%b rowend exp=%b ltn=%b exp=%b byteaddr=%h exp=%h",
                     rs, scrx_q, up_scrx, eq0_q, cf0_q, up_rowend, ltn_q, up_ltn, byteaddr_r, up_byteaddr);
            $fatal(1, "s32_sprite r3: in-row precomputed value differs from upstream's expression");
        end
    end
    if (!rst && rs == R_EMIT) begin
        r3c_emit <= r3c_emit + 1;
        if (emit_q) r3c_emit2 <= r3c_emit2 + 1;
        if (okn_q !== up_okn || xacc_inc !== up_xacc_n || bnx_inc !== up_byteaddr_n) begin
            r3c_bad <= r3c_bad + 1;
            $display("SIMCHECK FAIL s32_sprite r3 R_EMIT okn=%b exp=%b xacc_inc=%h exp=%h bnx=%h exp=%h emit_q=%b",
                     okn_q, up_okn, xacc_inc, up_xacc_n, bnx_inc, up_byteaddr_n, emit_q);
            $fatal(1, "s32_sprite r3: R_EMIT precomputed value differs from upstream's expression");
        end
    end
end
final $display("SIMCHECK s32_sprite r3: inc_cycles=%0d adv2_uses=%0d zero_uses=%0d row_cycles=%0d inrow_cycles=%0d first_pixel_cycles=%0d emit_cycles=%0d emit2_uses=%0d violations=%0d",
               r3c_inc, r3c_adv2, r3c_zero, r3c_row, r3c_inrow, r3c_first, r3c_emit, r3c_emit2, r3c_bad);
`endif
"""
ADV_UP = ("                fb_wr_end <= 1'b1;\n"
          "                dy <= dy + 1'd1;\n"
          "                yacc <= yacc + ystep;\n"
          "                rs <= R_ROW;\n")
ADV_R3 = ("                fb_wr_end <= 1'b1;\n"
          "                dy <= dy_inc;                  // D2 VARIANT r3: upstream dy + 1'd1 (precomputed)\n"
          "                yacc <= yacc_inc;              // D2 VARIANT r3: upstream yacc + ystep (precomputed)\n"
          "                adv_q <= 1'b1;\n"
          "                scry_q <= d_flipy ? scry_q - 14'sd1 : scry_q + 14'sd1;\n"
          "                rs <= R_ROW;\n")
SPRITE_R3 = [
    # (2) flags: cleared by reset and by default every cycle, set at the write sites below
    ("        fb_wr_shadow <= 0;\n",
     "        fb_wr_shadow <= 0;\n"
     "        adv_q <= 1'b0; zy_q <= 1'b0; zd_q <= 1'b0; emit_q <= 1'b0;   // D2 VARIANT r3\n", 1),
    ("        fb_wr_end   <= 0;\n",
     "        fb_wr_end   <= 0;\n"
     "        adv_q <= 1'b0; zy_q <= 1'b0; zd_q <= 1'b0; emit_q <= 1'b0;   // D2 VARIANT r3: write-history flags\n", 1),
    ("                    yacc <= 0;\n"
     "                    dy <= 0;\n",
     "                    yacc <= 0;\n"
     "                    dy <= 0;\n"
     "                    zy_q <= 1'b1; zd_q <= 1'b1;   // D2 VARIANT r3\n", 1),
    ("            xstep <= scale_xquot;\n",
     "            xstep <= scale_xquot;\n"
     "            zy_q <= 1'b1;            // D2 VARIANT r3 (yacc == 0 here)\n"
     "            scry_q <= ys0_q;         // D2 VARIANT r3: scry at dy == 0\n", 1),
    # R_ROW: scry from scry_q; skip uses the precomputed sums; the visible row initialises the in-row lockstep registers
    ("            scry = d_flipy ? (y0 + $signed({4'b0000, d_dsth})\n"
     "                              - 14'sd1 - $signed({4'b0000, dy}))\n"
     "                           : (y0 + $signed({4'b0000, dy}));\n",
     "            scry = scry_q;   // D2 VARIANT r3: precomputed (upstream: inline expression of y0, d_dsth, d_flipy, dy)\n", 1),
    ("                dy <= dy + 1'd1;               // row not visible: skip\n"
     "                yacc <= yacc + ystep;\n",
     "                dy <= dy_inc;                  // row not visible: skip (D2 VARIANT r3: precomputed dy + 1'd1)\n"
     "                yacc <= yacc_inc;              // D2 VARIANT r3: precomputed yacc + ystep\n"
     "                adv_q <= 1'b1;\n"
     "                scry_q <= d_flipy ? scry_q - 14'sd1 : scry_q + 14'sd1;\n", 1),
    ("                fb_wr_shadow <= d_shad;\n"
     "                rs <= R_PIXEL;\n",
     "                fb_wr_shadow <= d_shad;\n"
     "                scrx_q <= xst_q;                 // D2 VARIANT r3: in-row lockstep registers at dx = 0, xacc = 0\n"
     "                eq0_q <= (10'd0 == d_dstw);\n"
     "                cf0_q <= cf0s_q;\n"
     "                ltn_q <= (10'd1 < d_dstw);\n"
     "                bfirst_q <= 1'b1;\n"
     "                rs <= R_PIXEL;\n", 1),
    # R_PIXEL
    ("            scrx = d_flipx ? (x0 + $signed({4'b0000, d_dstw})\n"
     "                              - 14'sd1 - $signed({4'b0000, dx}))\n"
     "                           : (x0 + $signed({4'b0000, dx}));\n",
     "            scrx = scrx_q;   // D2 VARIANT r3: precomputed (upstream: inline expression of x0, d_dstw, d_flipx, dx)\n", 1),
    ("            byteaddr = row_byte_base\n"
     "                     + (d_bpp8 ? {14'b0, sx_px} : {15'b0, sx_px[9:1]});\n",
     "            byteaddr = byteaddr_r;   // D2 VARIANT r3: precomputed (upstream: row_byte_base + offset of sx_px)\n", 1),
    ("            if (dx == d_dstw ||\n"
     "                (!d_flipx && scrx > clip_x_max) ||\n"
     "                ( d_flipx && scrx < clip_x_min)) begin\n",
     "            if (eq0_q || cf0_q) begin   // D2 VARIANT r3: precomputed (upstream: dx == d_dstw || clip test of scrx)\n", 1),
    (ADV_UP, ADV_R3, 3),          # R_PIXEL end of row, R_SCAN end code, R_EMIT end code
    ("                pixel_scrx     <= scrx;\n",
     "                pixel_scrx     <= scrx;\n"
     "                okn_q <= !r3_clipfail(d_flipx ? scrx - 14'sd1 : scrx + 14'sd1,   // D2 VARIANT r3\n"
     "                                      d_flipx, clip_x_min_q, clip_x_max_q);\n", 1),
    # R_EMIT
    ("            logic        fast_continue;\n",
     "            logic        fast_continue;\n"
     "            logic signed [13:0] r3_sx1;   // D2 VARIANT r3\n", 1),
    ("                dx <= dx + 1'd1;\n"
     "                xacc <= xacc + xstep;\n",
     "                dx <= dx + 1'd1;\n"
     "                xacc <= xacc_inc;   // D2 VARIANT r3: precomputed xacc + xstep\n"
     "                emit_q <= 1'b1;     // D2 VARIANT r3: in-row lockstep registers at dx + 1, xacc + xstep\n"
     "                r3_sx1 = d_flipx ? scrx_q - 14'sd1 : scrx_q + 14'sd1;\n"
     "                scrx_q <= r3_sx1;\n"
     "                eq0_q <= (10'(dx + 10'd1) == d_dstw);\n"
     "                cf0_q <= r3_clipfail(r3_sx1, d_flipx, clip_x_min_q, clip_x_max_q);\n"
     "                ltn_q <= (10'(dx + 10'd2) < d_dstw);\n"
     "                byteaddr_q <= bnx_inc;\n"
     "                bfirst_q <= 1'b0;\n", 1),
    ("                fast_continue = FAST_1X && (xstep == 25'h1_0000) &&\n"
     "                                (next_dx < d_dstw) && rowtag_v &&\n"
     "                                same_cache &&\n"
     "                                ((!d_flipx && next_scrx <= clip_x_max) ||\n"
     "                                 ( d_flipx && next_scrx >= clip_x_min));\n",
     "                fast_continue = FAST_1X && (xstep == 25'h1_0000) &&\n"
     "                                ltn_q && rowtag_v &&   // D2 VARIANT r3: ltn_q = (next_dx < d_dstw), precomputed\n"
     "                                same_cache &&\n"
     "                                okn_q;                 // D2 VARIANT r3: upstream's clip test of next_scrx, precomputed\n", 1),
    ("                    pixel_scrx     <= next_scrx;\n",
     "                    pixel_scrx     <= next_scrx;\n"
     "                    okn_q <= !r3_clipfail(d_flipx ? next_scrx - 14'sd1 : next_scrx + 14'sd1,   // D2 VARIANT r3\n"
     "                                          d_flipx, clip_x_min_q, clip_x_max_q);\n", 1),
]
SPRITE_REG = """
// D2 VARIANT (timing): registered clip limits. clip_x_*_c is upstream's expression; clip_x_*_q = it, one clk_ram cycle later.
wire signed [15:0] clip_x_min_c = (clip_en && $signed(clip_l) > 0) ? $signed(clip_l) : 16'sd0;
wire signed [15:0] clip_x_max_c = (clip_en && $signed(clip_r) < $signed({7'b0, hpix}) - 1)
                                ? $signed(clip_r) : $signed({7'b0, hpix}) - 1;
reg  signed [15:0] clip_x_min_q, clip_x_max_q;
always @(posedge clk) begin
    clip_x_min_q <= clip_x_min_c;
    clip_x_max_q <= clip_x_max_c;
end
`ifndef SYNTHESIS
// SIMULATION ONLY: the registered limits must never be consumed in the cycle after an input change.
reg        clip_en_p;
reg [15:0] clip_l_p, clip_r_p;
reg  [8:0] hpix_p;
integer    clipchk_uses, clipchk_uses_en, clipchk_uses_lim, clipchk_bad;
initial begin clipchk_uses = 0; clipchk_uses_en = 0; clipchk_uses_lim = 0; clipchk_bad = 0; end
wire clip_in_chg = (clip_en !== clip_en_p) || (clip_l !== clip_l_p) || (clip_r !== clip_r_p) || (hpix !== hpix_p);
always @(posedge clk) begin
    clip_en_p <= clip_en; clip_l_p <= clip_l; clip_r_p <= clip_r; hpix_p <= hpix;
    if (!rst && (rs == R_PIXEL || rs == R_EMIT)) begin
        clipchk_uses <= clipchk_uses + 1;
        if (clip_en) clipchk_uses_en <= clipchk_uses_en + 1;
        if (clip_en && ($signed(clip_r) < $signed({7'b0, hpix}) - 1 || $signed(clip_l) > 0)) clipchk_uses_lim <= clipchk_uses_lim + 1;
        if (clip_in_chg || clip_x_min_q !== clip_x_min_c || clip_x_max_q !== clip_x_max_c) begin
            clipchk_bad <= clipchk_bad + 1;
            $display("SIMCHECK FAIL s32_sprite clip_x_* used right after an input change (rs=%0d chg=%b q/c min %0d/%0d max %0d/%0d)",
                     rs, clip_in_chg, clip_x_min_q, clip_x_min_c, clip_x_max_q, clip_x_max_c);
            $fatal(1, "s32_sprite: registered clip limits consumed in the cycle after an input change");
        end
    end
end
final $display("SIMCHECK s32_sprite clipreg: uses=%0d clip_en_uses=%0d limiting_uses=%0d violations=%0d",
               clipchk_uses, clipchk_uses_en, clipchk_uses_lim, clipchk_bad);
`endif
"""
def sprite():
    src = (ROOT / "references/s32/rtl/video/s32_sprite.sv").read_text()
    body = apply(src, [
        ("wire [8:0] hpix = ctl_latched[6][0] ? 9'd416 : 9'd320;\n",
         "wire [8:0] hpix = ctl_latched[6][0] ? 9'd416 : 9'd320;\n" + SPRITE_REG + SPRITE_R3_REG, 1),
        ("            clip_x_min = (clip_en && $signed(clip_l) > 0) ? $signed(clip_l) : 16'sd0;\n"
         "            clip_x_max = (clip_en && $signed(clip_r) < $signed({7'b0, hpix}) - 1)\n"
         "                       ? $signed(clip_r) : $signed({7'b0, hpix}) - 1;\n",
         "            clip_x_min = clip_x_min_q;   // D2 VARIANT: registered (upstream: inline expression)\n"
         "            clip_x_max = clip_x_max_q;\n", 1),
        ("                clip_x_min = (clip_en && $signed(clip_l) > 0)\n"
         "                           ? $signed(clip_l) : 16'sd0;\n"
         "                clip_x_max = (clip_en && $signed(clip_r) <\n"
         "                               $signed({7'b0, hpix}) - 1)\n"
         "                           ? $signed(clip_r)\n"
         "                           : $signed({7'b0, hpix}) - 1;\n",
         "                clip_x_min = clip_x_min_q;   // D2 VARIANT: registered (upstream: inline expression)\n"
         "                clip_x_max = clip_x_max_q;\n", 1),
    ], "s32_sprite (registered clip limits)")
    body = apply(body, SPRITE_R3, "s32_sprite (r3 precomputed row-advance / x-position arithmetic)")
    return SPRITE_HDR + body

# ------------------------------------------------------------------ AU3 configuration only (exp/audio3): the r4 sprite timing patch
# s32_sprite_d2_au3.sv = sprite() (the r2 + r3 engine, s32_sprite_d2.sv, which fit / sp / au keep compiling unchanged) + r4.
SPRITE_R4_HDR = """//
// + r4 TIMING PATCH (GA2Audio3 = the au3 configuration ONLY; this file = s32_sprite_d2.sv + r4. s32_sprite_d2.sv is unchanged and is what
// the fit / sp / au configurations compile). The au3 fits of seeds 1-3 (runs 43799033988 / 43799090763 / 43799119377) failed clk_ram setup
// -0.638 / -0.728 / -1.033 ns (slow 0C). Every failing path starts at a retimed register of Add33 = row_byte_base's D-input adder
// ({4'b0, d_addr} << 2) + sy_now * {d_srcw, 2'b00} (the fitter retimed row_byte_base backwards through it; the retimed registers were
// packed into the output register of the Mult0 DSP, the sy_now * {d_srcw, 2'b00} multiplier) or at row_byte_base[2]_OTERM, and ends
// in retimed registers of Add51, a logic cone fed by row_byte_base (seed 3 also ends in yacc[*], pixel_pen8[5] and the indtab address):
// the DSP output, the Add33 carry chain and the downstream logic of row_byte_base in one clk_ram cycle.
//   r4 computes the same value one cycle earlier, in two free-running registers (no enable, no reset):
//     rbb_inc_q <= R4(yacc_inc)   rbb_cur_q <= R4(yacc)   with R4(y) = ({4'b0, d_addr} << 2) + ({4'b0000, y[24:16]} * {d_srcw, 2'b00})
//   (R4 is upstream's R_ROW expression, sy_now = {4'b0000, yacc[24:16]}), and R_ROW loads row_byte_base <= adv_q ? rbb_inc_q : rbb_cur_q.
//   row_byte_base keeps its single write site (every R_ROW cycle) and its value in every cycle; no state, cycle count, srom request or
//   framebuffer write changes. Its D input is now a 2:1 select of registers; the multiply and the add have a cycle of their own.
// WHY rbb_r = adv_q ? rbb_inc_q : rbb_cur_q EQUALS R4(yacc) IN EVERY R_ROW CYCLE t. R4's inputs are yacc and sw[] (d_addr =
// {sw[2][15:12], sw[6]}; d_srcw = sw[1][5:0] or sw[1][6:1] by d_bpp8 = sw[0][9]). sw[] is written only in R_FETCHW (-> R_FETCHW /
// R_DECODE). yacc is written only by R_DECODE (draw: yacc <= 0; -> R_SCALE) and by the four row advances (R_ROW skip, R_PIXEL end of
// row, R_SCAN end code, R_EMIT end code), each of which writes yacc <= yacc_inc and adv_q <= 1 and goes to R_ROW; adv_q is cleared in
// every other cycle and by rst. R_ROW is entered only from R_SCALE, R_INDTABW, R_ROW and the four advances. So, in an R_ROW cycle t:
//   adv_q(t) = 1: cycle t-1 advanced: yacc(t) = yacc_inc(t-1), sw unchanged       => rbb_inc_q(t) = R4(yacc_inc(t-1)) = R4(yacc(t));
//   adv_q(t) = 0: cycle t-1 is R_SCALE, R_INDTABW or a non-advancing R_ROW (the fb_busy wait): it writes neither yacc nor sw
//                                                                                  => rbb_cur_q(t) = R4(yacc(t-1)) = R4(yacc(t)).
// (Producer -> consumer distance 1 in both cases; modular arithmetic as upstream's, 24-bit context.) The power-up value of rbb_*_q is
// never used: after rst, R_ROW is first reached through R_IDLE / R_DELAY / R_SWAP / R_RENDER / R_FETCH* / R_DECODE / R_SCALE, more
// than one clock later. The `ifndef SYNTHESIS block (SIMCHECK s32_sprite r4) compares rbb_r with upstream's expression, verbatim, in
// every R_ROW cycle and calls $fatal otherwise.
"""
SPRITE_R4_REG = """
// D2 VARIANT r4 (au3 only; timing): precomputed row byte base (see the header). r4_rbb is upstream's R_ROW expression of the row base.
function automatic [23:0] r4_rbb(input [24:0] ya, input [19:0] da, input [5:0] sw6);
    r4_rbb = ({4'b0, da} << 2) + ({4'b0000, ya[24:16]} * {sw6, 2'b00});
endfunction
reg  [23:0] rbb_inc_q, rbb_cur_q;                   // free-running
always @(posedge clk) begin
    rbb_inc_q <= r4_rbb(yacc_inc, d_addr, d_srcw);
    rbb_cur_q <= r4_rbb(yacc, d_addr, d_srcw);
end
wire [23:0] rbb_r = adv_q ? rbb_inc_q : rbb_cur_q;
`ifndef SYNTHESIS
// SIMULATION ONLY (r4): in every R_ROW cycle rbb_r must equal upstream's inline expression (verbatim below); else $fatal.
integer r4c_row, r4c_inc, r4c_cur, r4c_bad;
initial begin r4c_row = 0; r4c_inc = 0; r4c_cur = 0; r4c_bad = 0; end
wire [12:0] up_sy_now = {4'b0000, yacc[24:16]};
wire [23:0] up_row_byte_base = ({4'b0, d_addr} << 2)
                           + (up_sy_now * {d_srcw, 2'b00});
always @(posedge clk) begin
    if (!rst && rs == R_ROW) begin
        r4c_row <= r4c_row + 1;
        if (adv_q) r4c_inc <= r4c_inc + 1; else r4c_cur <= r4c_cur + 1;
        if (rbb_r !== up_row_byte_base) begin
            r4c_bad <= r4c_bad + 1;
            $display("SIMCHECK FAIL s32_sprite r4 R_ROW adv_q=%b rbb_r=%h exp=%h yacc=%h d_addr=%h d_srcw=%h",
                     adv_q, rbb_r, up_row_byte_base, yacc, d_addr, d_srcw);
            $fatal(1, "s32_sprite r4: precomputed row byte base differs from upstream's expression");
        end
    end
end
final $display("SIMCHECK s32_sprite r4: row_cycles=%0d inc_uses=%0d cur_uses=%0d violations=%0d", r4c_row, r4c_inc, r4c_cur, r4c_bad);
`endif
"""
SPRITE_R4 = [
    ("// is never edited). TWO timing patches, nothing else. r2",
     "// is never edited). TWO timing patches (r2, r3) + the au3-only r4 patch (end of this header), nothing else. r2", 1),
    ("//============================================================================\n//  System 32 object/sprite engine",
     SPRITE_R4_HDR + "//============================================================================\n//  System 32 object/sprite engine", 1),
    ("wire [23:0] byteaddr_r = bfirst_q ? row_byte_base : byteaddr_q;\n",
     "wire [23:0] byteaddr_r = bfirst_q ? row_byte_base : byteaddr_q;\n" + SPRITE_R4_REG, 1),
    ("            row_byte_base <= ({4'b0, d_addr} << 2)\n"
     "                           + (sy_now * {d_srcw, 2'b00});\n",
     "            row_byte_base <= rbb_r;   // D2 VARIANT r4: precomputed (upstream: ({4'b0, d_addr} << 2) + (sy_now * {d_srcw, 2'b00}))\n", 1),
]
def sprite_au3():
    return apply(sprite(), SPRITE_R4, "s32_sprite (r4 precomputed row byte base, au3 only)")

# ------------------------------------------------------------------ SP configuration (exp/sprite-int)
SPR_OLD = ("s32_big_dpram #(\n    .ADDR_WIDTH(16), .NUM_WORDS(65536), .MIXED_RDW_MODE(\"DONT_CARE\"),\n"
           "    .PORT_B_READ_ONLY(1'b1)\n) sprite_ram (\n"
           "    .clock_a(clk_sys), .address_a(A[16:1]),\n    .data_a(m_wdata), .byteena_a(m_be),\n"
           "    .wren_a(m_req && m_we && sel_sprram), .q_a(sprram_q),\n"
           "    .clock_b(clk_ram), .address_b(slist_addr),\n    .data_b(16'h0000), .byteena_b(2'b00),\n"
           "    .wren_b(1'b0), .q_b(sprlist_q)\n);\n")
SPR_NEW = ("// D2 SP VARIANT: sprite RAM = SP's window (experiments/SP-sprite-path/rtl/sp_spriteram_win.sv; 11 x 1K-word pages of the\n"
           "// 64K-word space on chip: words 0x0000, 0x4000, 0x8000-0x9FFF, 0xA000; everything else dropped / read as 0000 and counted).\n"
           "// Read strobe = rising edge of a\n"
           "// CPU read cycle to sprite RAM (one count per bus cycle).\n"
           "reg spr_rd_d = 1'b0;\nalways @(posedge clk_sys) spr_rd_d <= m_req && !m_we && sel_sprram;\n"
           "sp_spriteram_win #(\n    .ADDR_WIDTH(16), .NUM_WORDS(65536), .MIXED_RDW_MODE(\"DONT_CARE\"),\n"
           "    .PORT_B_READ_ONLY(1'b1)\n) sprite_ram (\n"
           "    .clock_a(clk_sys), .address_a(A[16:1]),\n    .data_a(m_wdata), .byteena_a(m_be),\n"
           "    .wren_a(m_req && m_we && sel_sprram), .q_a(sprram_q),\n"
           "    .clock_b(clk_ram), .address_b(slist_addr),\n    .data_b(16'h0000), .byteena_b(2'b00),\n"
           "    .wren_b(1'b0), .q_b(sprlist_q),\n"
           "    .rd_strobe_a(m_req && !m_we && sel_sprram && !spr_rd_d),\n"
           "    .n_oow_wr_a(dbg_spr_oow_wr), .n_oow_rd_a(dbg_spr_oow_rd), .n_oow_rd_b(dbg_spr_oow_eng)\n);\n")

def core_sp():
    body = apply(core(), [
        ("    output      [7:0] dbg_irq_vector,\n",
         "    output      [7:0] dbg_irq_vector,\n"
         "    // D2 SP VARIANT: sprite-RAM window out-of-window counters (observation; saturating)\n"
         "    output     [15:0] dbg_spr_oow_wr,   // CPU writes dropped (clk_sys)\n"
         "    output     [15:0] dbg_spr_oow_rd,   // CPU read cycles answered 0000 (clk_sys)\n"
         "    output     [15:0] dbg_spr_oow_eng,  // engine address steps outside the window (clk_ram)\n", 1),
        (SPR_OLD, SPR_NEW, 1),
        # GA2Play5 freeze readout: observation bundles (existing nets only, display only)
        ("    output     [15:0] dbg_spr_oow_eng,  // engine address steps outside the window (clk_ram)\n",
         "    output     [15:0] dbg_spr_oow_eng,  // engine address steps outside the window (clk_ram)\n"
         "    // D2 SP VARIANT (GA2Play5 freeze readout, observation only): V2's bundle and the V2-side bus fabric (layouts in\n"
         "    // experiments/D2-pocket-top/rtl/d2_freeze_readout.sv)\n"
         "    output     [95:0] dbg_v2,\n"
         "    output     [63:0] dbg_bus,\n", 1),
        (".irq_n(irq_n), .irq_vector(irq_vector), .irq_ack(dbg_irq_ack),   // D2 VARIANT: was .irq_ack() (unused upstream)\n"
         "    .nmi_n(1'b1)\n);\n",
         ".irq_n(irq_n), .irq_vector(irq_vector), .irq_ack(dbg_irq_ack),   // D2 VARIANT: was .irq_ack() (unused upstream)\n"
         "    .nmi_n(1'b1),\n    .dbg_v2(dbg_v2)   // D2 SP VARIANT (GA2Play5 freeze readout)\n);\n", 1),
        ("s32_v60_bus vbus (\n",
         "// D2 SP VARIANT (GA2Play5 freeze readout): the V2-side bus nets, observation only\n"
         "assign dbg_bus = {if_addr, c_addr[23:0], if_req, if_ack, c_req, c_we, c_ack, m_req, m_ack, irq_n, 8'd0};\n"
         "s32_v60_bus vbus (\n", 1),
    ], "s32_core_d2 (SP sprite-RAM window)")
    return body.replace("// The only simulation-only text is",
                        "// SP CONFIGURATION: + sprite_ram -> sp_spriteram_win (SP 32 KB window) and its counters as outputs.\n"
                        "// GA2Play5: + dbg_v2 (from the sp-derived s32_v60 / v2_core) and dbg_bus, observation outputs for the freeze readout.\n"
                        "// The only simulation-only text is", 1)

V2R = ROOT / "experiments/V2-datapath-floor/rtl"

def v2core_sp():
    src = (V2R / "v2_core.sv").read_text()
    body = apply(src, [
        ("    input             nmi_n,\n",
         "    input             nmi_n,\n"
         "    // D2 SP DERIVED (GA2Play5 freeze readout): observation only, a continuous assign of existing nets (layout in\n"
         "    // experiments/D2-pocket-top/rtl/d2_freeze_readout.sv)\n"
         "    output     [95:0] dbg_v2,\n", 1),
        ("\nendmodule\n",
         "\n// D2 SP DERIVED (GA2Play5 freeze readout): no logic, no register; the consumer registers it first\n"
         "assign dbg_v2 = {window[31:0], ipc, upc, cw[19:8], cw[2:0], adv, sh_busy, fetch_short, data_busy, 1'b0};\n"
         "\nendmodule\n", 1),
    ], "v2_core")
    hdr = ("// SPDX-License-Identifier: GPL-3.0-or-later\n//\n"
           "// *** D2 SP DERIVED COPY (GA2Play5; exploratory). Generated by experiments/D2-pocket-top/make_d2_derived.py from\n"
           "// experiments/V2-datapath-floor/rtl/v2_core.sv: verbatim apart from ONE observation output, dbg_v2 = existing nets\n"
           "// (fetch window bytes 0-3, ipc, upc, the current word's JADDR and NXT, adv and the three stall terms). Module name\n"
           "// v2_core kept (the SDC selects s32_v60:v60|v2_core:core); compiled instead of the V2 tree's file in the sp configuration.\n")
    return hdr + body.replace("// SPDX-License-Identifier: GPL-3.0-or-later\n//\n", "//\n", 1)

def v60hw_sp():
    src = (HERE / "rtl/d2_v60_hw.sv").read_text()
    body = apply(src, [
        ("    input             nmi_n\n);\n",
         "    input             nmi_n,\n"
         "    output     [95:0] dbg_v2       // D2 SP DERIVED (GA2Play5 freeze readout): v2_core's observation bundle\n);\n", 1),
        ("    .tbl_we(1'b0), .tbl_sel(1'b0), .tbl_addr(9'd0), .tbl_wdata(24'd0)\n);\n",
         "    .tbl_we(1'b0), .tbl_sel(1'b0), .tbl_addr(9'd0), .tbl_wdata(24'd0),\n"
         "    .dbg_v2(dbg_v2)   // D2 SP DERIVED (GA2Play5 freeze readout)\n);\n", 1),
    ], "d2_v60_hw")
    assert re.search(r"\bv2_core\b[^;]*?\)\s*core\s*\(", body, re.S), "the v2_core instance must stay `core`"
    hdr = ("// SPDX-License-Identifier: GPL-3.0-or-later\n//\n"
           "// *** D2 SP DERIVED COPY (GA2Play5; exploratory). Generated by experiments/D2-pocket-top/make_d2_derived.py from\n"
           "// rtl/d2_v60_hw.sv: + the output dbg_v2 (v2_core's observation bundle, for the freeze readout). Module name s32_v60 and\n"
           "// instance name core kept; compiled instead of rtl/d2_v60_hw.sv in the sp configuration (the sp build's one V2 wrapper).\n")
    return hdr + body.replace("// SPDX-License-Identifier: GPL-3.0-or-later\n//\n", "//\n", 1)

def load_wq3():
    src = (P1 / "rtl/p1_load_wq.sv").read_text()
    body = apply(src, [
        ("module p1_load_wq #(", "module d2_load_wq3 #(", 1),
        ("    input       [15:0] b_data,\n",
         "    input       [15:0] b_data,\n    input              c_en,          // slot 3 (sprite ROM)\n"
         "    input       [24:0] c_addr,\n    input       [15:0] c_data,\n", 1),
        ("    input       [24:0] b_base,\n", "    input       [24:0] b_base,\n    input       [24:0] c_base,\n", 1),
        ("reg a_d = 1'b0, b_d = 1'b0, ack_d = 1'b0;\nwire a_rise = a_en && !a_d, b_rise = b_en && !b_d;",
         "reg a_d = 1'b0, b_d = 1'b0, c_d = 1'b0, ack_d = 1'b0;\nwire a_rise = a_en && !a_d, b_rise = b_en && !b_d, c_rise = c_en && !c_d;", 1),
        ("    a_d <= a_en; b_d <= b_en; ack_d <= wr_ack;", "    a_d <= a_en; b_d <= b_en; c_d <= c_en; ack_d <= wr_ack;", 1),
        ("        if (a_rise && b_rise) n_collide <= n_collide + 1;",
         "        if ((a_rise && b_rise) || (a_rise && c_rise) || (b_rise && c_rise)) n_collide <= n_collide + 1;", 1),
        ("        if (a_rise || b_rise) begin", "        if (a_rise || b_rise || c_rise) begin", 1),
        ("a_rise ? {24'((a_base + a_addr) >> 1), a_data} : {24'((b_base + b_addr) >> 1), b_data};",
         "a_rise ? {24'((a_base + a_addr) >> 1), a_data} : b_rise ? {24'((b_base + b_addr) >> 1), b_data}\n"
         "                                                    : {24'((c_base + c_addr) >> 1), c_data};", 1),
    ], "p1_load_wq")
    hdr = ("// SPDX-License-Identifier: GPL-3.0-or-later\n//\n"
           "// D2 SP DERIVED COPY of experiments/P1-pocket-top/rtl/p1_load_wq.sv (generated by make_d2_derived.py): module renamed to\n"
           "// d2_load_wq3, a third loader input (c_*, slot 3 = sprite ROM) with its own SDRAM base. The host writes one slot at a\n"
           "// time, so the three strobes never coincide in practice; a coincidence is counted in n_collide (as a/b before).\n")
    return hdr + body.replace("// SPDX-License-Identifier: GPL-3.0-or-later\n//\n", "//\n", 1)

def slot_verify3():
    src = (HERE / "rtl/d2_slot_verify.sv").read_text()
    body = apply(src, [
        ("module d2_slot_verify (", "module d2_slot_verify3 (", 1),
        ("    input       [24:0] base1, input [24:0] bytes1,\n",
         "    input       [24:0] base1, input [24:0] bytes1,\n    input       [24:0] base2, input [24:0] bytes2,   // SP: slot 3 (sprite ROM)\n", 1),
        ("    output reg  [31:0] sum0 = 0, fl0 = 0, sum1 = 0, fl1 = 0,\n",
         "    output reg  [31:0] sum0 = 0, fl0 = 0, sum1 = 0, fl1 = 0, sum2 = 0, fl2 = 0,\n", 1),
        ("reg        region = 0;\n", "reg [1:0]  region = 0;               // SP: 0 program, 1 tiles, 2 sprites\n", 1),
        ("wire        last   = (off + 25'd8 >= (region ? bytes1 : bytes0));\n",
         "wire [24:0] bytes_r = region[1] ? bytes2 : region[0] ? bytes1 : bytes0;\n"
         "wire        last   = (off + 25'd8 >= bytes_r);\n", 1),
        ("sum0 <= 0; fl0 <= 0; sum1 <= 0; fl1 <= 0; cycles <= 0;", "sum0 <= 0; fl0 <= 0; sum1 <= 0; fl1 <= 0; sum2 <= 0; fl2 <= 0; cycles <= 0;", 1),
        ("            p5_addr <= 22'(((region ? base1 : base0) + off) >> 3);\n",
         "            p5_addr <= 22'(((region[1] ? base2 : region[0] ? base1 : base0) + off) >> 3);\n", 1),
        ("                    if (region) begin sum1 <= s_next; fl1 <= f_next; done <= 1'b1; st <= S_IDLE; end\n"
         "                    else begin sum0 <= s_next; fl0 <= f_next; region <= 1'b1; off <= 0;\n"
         "                               acc_s <= 0; acc_f <= 0; st <= S_FALL; end\n",
         "                    if (region[1]) begin sum2 <= s_next; fl2 <= f_next; done <= 1'b1; st <= S_IDLE; end\n"
         "                    else if (region[0]) begin sum1 <= s_next; fl1 <= f_next; region <= 2'd2; off <= 0;\n"
         "                               acc_s <= 0; acc_f <= 0; st <= S_FALL; end\n"
         "                    else begin sum0 <= s_next; fl0 <= f_next; region <= 2'd1; off <= 0;\n"
         "                               acc_s <= 0; acc_f <= 0; st <= S_FALL; end\n", 1),
    ], "d2_slot_verify")
    hdr = ("// SPDX-License-Identifier: GPL-3.0-or-later\n//\n"
           "// D2 SP DERIVED COPY of rtl/d2_slot_verify.sv (generated by make_d2_derived.py): module renamed d2_slot_verify3, region 2 =\n"
           "// slot 3 (sprite ROM, base2 / bytes2) added after the tile region with its own sum2 / fl2; everything else (word-serial fold,\n"
           "// p5 protocol, per-region copy of the running sums, done after the LAST region) as the original. A slot-3 size of 0 (package\n"
           "// without sprites.bin) still terminates: one burst is folded, the sums then differ from the expected ones.\n")
    return hdr + body.replace("// SPDX-License-Identifier: GPL-3.0-or-later\n//\n", "//\n", 1)

def top_sp():
    src = (HERE / "rtl/d2_core_top.sv").read_text()
    body = apply(src, [
        ("    wire        l1_en, l2_en;\n    wire [24:0] l1_addr, l2_addr;\n    wire [15:0] l1_data, l2_data;\n",
         "    wire        l1_en, l2_en, l3_en;\n    wire [24:0] l1_addr, l2_addr, l3_addr;\n    wire [15:0] l1_data, l2_data, l3_data;\n", 1),
        ("    .write_en(l2_en), .write_addr(l2_addr), .write_data(l2_data));\n",
         "    .write_en(l2_en), .write_addr(l2_addr), .write_data(l2_data));\n"
         "// SP: slot 3 = sprites.bin (16 MiB, bridge 0x3000_0000) -> SDRAM SDR_SPRITES_BASE 0x100_0000 (the s32_core p2 region)\n"
         "data_loader #(.ADDRESS_MASK_UPPER_4(4'h3), .ADDRESS_SIZE(25), .OUTPUT_WORD_SIZE(2)) slot3_loader (\n"
         "    .clk_74a(clk_74a), .clk_memory(clk_sys), .bridge_wr(bridge_wr), .bridge_endian_little(bridge_endian_little),\n"
         "    .bridge_addr(bridge_addr), .bridge_wr_data(bridge_wr_data),\n"
         "    .write_en(l3_en), .write_addr(l3_addr), .write_data(l3_data));\n", 1),
        ("sdram sdram (\n",
         "// SP: sprite-ROM port (core p2) and the framebuffer client's fw / fr ports\n"
         "    wire         sp_p2_req, sp_p2_ack; wire [24:4] sp_p2_addr; wire [127:0] sp_p2_dout;\n"
         "    wire         sp_fb_wr_busy; wire [15:0] sp_fb_rd_pix;\n"
         "    wire         fw_req, fw_fill, fw_valid, fw_last, fw_pop, fw_done; wire [24:10] fw_page;\n"
         "    wire [8:0]   fw_c0, fw_c1, fw_col; wire [15:0] fw_data; wire [1:0] fw_be;\n"
         "    wire         fr_req, fr_urgent, fr_dv, fr_done; wire [24:10] fr_page; wire [8:0] fr_c0, fr_dcol;\n"
         "    wire [9:0]   fr_n; wire [15:0] fr_dout;\n"
         "sp_sdram sdram (   // SP: experiments/SP-sprite-path/rtl/derived/sp_sdram.sv = upstream sdram.sv + sp_sdram.diff\n", 1),
        ("    .p2_req(1'b0), .p2_addr(21'd0), .p2_dout(), .p2_ack(),\n",
         "    .p2_req(sp_p2_req), .p2_addr(sp_p2_addr), .p2_dout(sp_p2_dout), .p2_ack(sp_p2_ack),\n", 1),
        ("    .p5_req(p5_req), .p5_addr(p5_addr), .p5_dout(p5_dout), .p5_ack(p5_ack)\n);\n",
         "    .p5_req(p5_req), .p5_addr(p5_addr), .p5_dout(p5_dout), .p5_ack(p5_ack),\n"
         "    .fw_req(fw_req), .fw_page(fw_page), .fw_fill(fw_fill), .fw_c0(fw_c0), .fw_c1(fw_c1),\n"
         "    .fw_valid(fw_valid), .fw_col(fw_col), .fw_data(fw_data), .fw_be(fw_be), .fw_last(fw_last),\n"
         "    .fw_pop(fw_pop), .fw_done(fw_done),\n"
         "    .fr_req(fr_req), .fr_page(fr_page), .fr_c0(fr_c0), .fr_n(fr_n), .fr_urgent(fr_urgent),\n"
         "    .fr_dv(fr_dv), .fr_dcol(fr_dcol), .fr_dout(fr_dout), .fr_done(fr_done)\n);\n", 1),
        ("p1_load_wq wq (\n", "d2_load_wq3 wq (   // SP: three slot inputs\n", 1),
        ("    .b_en(l2_en), .b_addr(l2_addr), .b_data(l2_data),\n    .a_base(SDR_MAINCPU_BASE), .b_base(SDR_TILES_BASE),\n",
         "    .b_en(l2_en), .b_addr(l2_addr), .b_data(l2_data),\n    .c_en(l3_en), .c_addr(l3_addr), .c_data(l3_data),\n"
         "    .a_base(SDR_MAINCPU_BASE), .b_base(SDR_TILES_BASE), .c_base(SDR_SPRITES_BASE),\n", 1),
        ("if (!allc_s[2] || !wq_empty || l1_en || l2_en || wr_req) quiet",
         "if (!allc_s[2] || !wq_empty || l1_en || l2_en || l3_en || wr_req) quiet", 1),
        ("    .sdr_p2_req(c_p2_req), .sdr_p2_addr(c_p2_addr_u), .sdr_p2_dout(128'd0), .sdr_p2_ack(c_p2_ack),\n",
         "    .sdr_p2_req(sp_p2_req), .sdr_p2_addr(sp_p2_addr), .sdr_p2_dout(sp_p2_dout), .sdr_p2_ack(sp_p2_ack),   // SP\n", 1),
        ("    .fb_wr_shadow(fb_wr_shadow_u), .fb_wr_busy(1'b0),\n",
         "    .fb_wr_shadow(fb_wr_shadow_u), .fb_wr_busy(sp_fb_wr_busy),   // SP\n", 1),
        ("    .fb_rd_x(fb_rd_x_u), .fb_rd_pix(16'hFFFF),\n", "    .fb_rd_x(fb_rd_x_u), .fb_rd_pix(sp_fb_rd_pix),   // SP\n", 1),
        ("    .dbg_irq_ack(irq_ack_v2), .dbg_irq_vector(irq_vec),\n",
         "    .dbg_irq_ack(irq_ack_v2), .dbg_irq_vector(irq_vec),\n"
         "    .dbg_spr_oow_wr(spr_oow_wr), .dbg_spr_oow_rd(spr_oow_rd), .dbg_spr_oow_eng(spr_oow_eng),   // SP (observation)\n", 1),
        ("    wire         irq_ack_v2;\n", "    wire         irq_ack_v2;\n    wire [15:0]  spr_oow_wr, spr_oow_rd, spr_oow_eng;   // SP\n", 1),
        ("    wire [15:0] n_p2, n_p3, n_p4, n_p5, n_wave, n_fb_er, n_fb_rd;\n", "    wire [15:0] n_p3, n_p4, n_p5, n_wave;\n", 1),
        ("d2_ack_stub #(.LAT(10)) st_p2 (.clk(clk_ram), .rst(rst_r), .req(c_p2_req),    .ack(c_p2_ack),    .n_req(n_p2));\n", "", 1),
        ("d2_ack_stub #(.LAT(4))  st_fe (.clk(clk_ram), .rst(rst_r), .req(c_fb_er_req), .ack(c_fb_er_ack), .n_req(n_fb_er));\n"
         "d2_ack_stub #(.LAT(4))  st_fr (.clk(clk_ram), .rst(rst_r), .req(c_fb_rd_req), .ack(c_fb_rd_ack), .n_req(n_fb_rd));\n",
         "\n// SP: sprite framebuffer in SDRAM (experiments/SP-sprite-path/rtl/sp_fb_sdram.sv, s32_fb_if drop-in), 4 slots x 256 KB at\n"
         "// 0x0C0_0000; line fetch width follows the CRT mode (320 / 416). p2 (sprite ROM) is served by sp_sdram above.\n"
         "    wire [15:0] fb_n_shadow_drop, fb_n_erase_skip, fb_n_erase_fill, fb_n_late_publish; wire fb_init_busy;\n"
         "sp_fb_sdram #(.FB_BASE(25'h0C0_0000)) spfb (\n"
         "    .clk(clk_ram), .rst(rst_r), .scan_wide(mode_416_active),\n"
         "    .wr_start(fb_wr_start_u), .wr_buf(fb_wr_buf_u), .wr_x(fb_wr_x_u), .wr_y(fb_wr_y_u),\n"
         "    .wr_valid(fb_wr_valid_u), .wr_pix(fb_wr_pix_u), .wr_end(fb_wr_end_u), .wr_shadow(fb_wr_shadow_u), .wr_busy(sp_fb_wr_busy),\n"
         "    .er_req(c_fb_er_req), .er_buf(fb_er_buf_u), .er_y(fb_er_y_u), .er_ack(c_fb_er_ack),\n"
         "    .rd_req(c_fb_rd_req), .rd_buf(fb_rd_buf_u), .rd_y(fb_rd_y_u), .rd_ack(c_fb_rd_ack),\n"
         "    .rd_x(fb_rd_x_u), .rd_pix(sp_fb_rd_pix),\n"
         "    .fw_req(fw_req), .fw_page(fw_page), .fw_fill(fw_fill), .fw_c0(fw_c0), .fw_c1(fw_c1),\n"
         "    .fw_valid(fw_valid), .fw_col(fw_col), .fw_data(fw_data), .fw_be(fw_be), .fw_last(fw_last),\n"
         "    .fw_pop(fw_pop), .fw_done(fw_done),\n"
         "    .fr_req(fr_req), .fr_page(fr_page), .fr_c0(fr_c0), .fr_n(fr_n), .fr_urgent(fr_urgent),\n"
         "    .fr_dv(fr_dv), .fr_dcol(fr_dcol), .fr_dout(fr_dout), .fr_done(fr_done),\n"
         "    .n_shadow_drop(fb_n_shadow_drop), .n_erase_skip(fb_n_erase_skip), .n_erase_fill(fb_n_erase_fill),\n"
         "    .n_late_publish(fb_n_late_publish), .init_busy(fb_init_busy));\n"
         "// request-edge counters of the ports the stubs used to count (observation only; unconnected in synthesis, pruned)\n"
         "    reg [15:0] n_p2 = 16'd0, n_fb_er = 16'd0, n_fb_rd = 16'd0; reg p2q_d = 1'b0, erq_d = 1'b0, rdq_d = 1'b0;\n"
         "always @(posedge clk_ram) begin\n"
         "    p2q_d <= sp_p2_req; erq_d <= c_fb_er_req; rdq_d <= c_fb_rd_req;\n"
         "    if (sp_p2_req && !p2q_d) n_p2 <= n_p2 + 16'd1;\n"
         "    if (c_fb_er_req && !erq_d) n_fb_er <= n_fb_er + 16'd1;\n"
         "    if (c_fb_rd_req && !rdq_d) n_fb_rd <= n_fb_rd + 16'd1;\n"
         "end\n", 1),
        ("id 2 tile ROM (0x2000_0000, 4 MB);", "id 2 tile ROM (0x2000_0000, 4 MB), SP: id 3 sprite ROM (0x3000_0000, 16 MB);", 1),
        ("    parameter [31:0]       EXP_SUM1 = 32'h5e9bf850, EXP_FL1 = 32'h39aeff9a    // ga2u tile slot\n",
         "    parameter [31:0]       EXP_SUM1 = 32'h5e9bf850, EXP_FL1 = 32'h39aeff9a,   // ga2u tile slot\n"
         "    parameter [31:0]       EXP_SUM2 = 32'hbcd81c86, EXP_FL2 = 32'hf5dfbf9c    // SP: ga2 sprite ROM image (slot 3, 16 MiB; checksums only)\n", 1),
        ("    reg [24:0] size1_74 = 25'd0, size2_74 = 25'd0;\n", "    reg [24:0] size1_74 = 25'd0, size2_74 = 25'd0, size3_74 = 25'd0;\n", 1),
        ("        if (dataslot_requestwrite_id == 16'd2) size2_74 <= dataslot_requestwrite_size[24:0];\n",
         "        if (dataslot_requestwrite_id == 16'd2) size2_74 <= dataslot_requestwrite_size[24:0];\n"
         "        if (dataslot_requestwrite_id == 16'd3) size3_74 <= dataslot_requestwrite_size[24:0];   // SP\n", 1),
        ("    reg [24:0] size1_m = 25'd0, size1_r = 25'd0, size2_m = 25'd0, size2_r = 25'd0;\n",
         "    reg [24:0] size1_m = 25'd0, size1_r = 25'd0, size2_m = 25'd0, size2_r = 25'd0, size3_m = 25'd0, size3_r = 25'd0;   // SP: + slot 3\n", 1),
        ("    size2_m <= size2_74; size2_r <= size2_m;\n", "    size2_m <= size2_74; size2_r <= size2_m;\n    size3_m <= size3_74; size3_r <= size3_m;\n", 1),
        ("    wire [31:0] v_sum0, v_fl0, v_sum1, v_fl1, v_cycles;\nd2_slot_verify ver (",
         "    wire [31:0] v_sum0, v_fl0, v_sum1, v_fl1, v_sum2, v_fl2, v_cycles;\nd2_slot_verify3 ver (   // SP: + slot 3 (sprite ROM)\n"
         "    .base2(SDR_SPRITES_BASE), .bytes2(size3_r), .sum2(v_sum2), .fl2(v_fl2),\n    ", 1),
        ("    reg sums0_ok = 1'b0, sums1_ok = 1'b0;\n", "    reg sums0_ok = 1'b0, sums1_ok = 1'b0, sums2_ok = 1'b0;\n", 1),
        ("    sums1_ok <= verify_done && v_sum1 == EXP_SUM1 && v_fl1 == EXP_FL1;\n",
         "    sums1_ok <= verify_done && v_sum1 == EXP_SUM1 && v_fl1 == EXP_FL1;\n"
         "    sums2_ok <= verify_done && v_sum2 == EXP_SUM2 && v_fl2 == EXP_FL2;   // SP: sprite ROM image\n", 1),
        (".sums0_ok(sums0_ok), .sums1_ok(sums1_ok),", ".sums0_ok(sums0_ok), .sums1_ok(sums1_ok && sums2_ok),   // SP: band cell 5 = tile sums AND sprite sums\n    ", 1),
        # GA2Play5 freeze readout (rtl/d2_freeze_readout.sv): observation bundles from the core, the capture on clk_sys and the
        # readout band on clk_vid after d2_diag_band (DE lines BAND_ROW0-9 .. BAND_ROW0-1; display only)
        ("    .dbg_spr_oow_wr(spr_oow_wr), .dbg_spr_oow_rd(spr_oow_rd), .dbg_spr_oow_eng(spr_oow_eng),   // SP (observation)\n",
         "    .dbg_spr_oow_wr(spr_oow_wr), .dbg_spr_oow_rd(spr_oow_rd), .dbg_spr_oow_eng(spr_oow_eng),   // SP (observation)\n"
         "    .dbg_v2(dbg_v2), .dbg_bus(dbg_bus),   // GA2Play5 freeze readout (observation)\n", 1),
        ("    wire [15:0]  spr_oow_wr, spr_oow_rd, spr_oow_eng;   // SP\n",
         "    wire [15:0]  spr_oow_wr, spr_oow_rd, spr_oow_eng;   // SP\n"
         "    wire [95:0]  dbg_v2; wire [63:0] dbg_bus;           // GA2Play5 freeze readout (observation)\n", 1),
        ("    wire band_px;\n",
         "    wire band_px, diag_px, ro_px; wire [23:0] diag_rgb;   // GA2Play5: ga2_video -> d2_diag_band -> d2_freeze_band -> pins\n", 1),
        ("    .rgb_in(vid_rgb_pre), .de(vid_de), .skip(vid_skip), .vs(vid_vs), .rgb_out(video_rgb), .in_band(band_px),\n",
         "    .rgb_in(vid_rgb_pre), .de(vid_de), .skip(vid_skip), .vs(vid_vs), .rgb_out(diag_rgb), .in_band(diag_px),\n", 1),
        (".irq64(irq64_cnt));\n",
         ".irq64(irq64_cnt));\n"
         "// GA2Play5 freeze readout (rtl/d2_freeze_readout.sv): one text line over DE lines BAND_ROW0-9 .. BAND_ROW0-1 (211-219), x 1-318:\n"
         "// PC, state (RUN / TRP / BUS / FCH / LOP / RST), micro-PC, data access + direction, fetch address, handshake flags, opcode\n"
         "// bytes, time since the last dispatch. Display only; the snapshot is taken once per frame on clk_sys and two-flop\n"
         "// synchronised per bit into clk_vid.\n"
         "    wire        ro_tgl, ro_we; wire [2:0] ro_st; wire [11:0] ro_upc; wire [23:0] ro_pc, ro_da, ro_fa;\n"
         "    wire  [7:0] ro_fl, ro_age; wire [31:0] ro_op;\n"
         "d2_freeze_cap rocap (\n"
         "    .clk(clk_sys), .rst(rst_r), .v2(dbg_v2), .bus(dbg_bus), .p0_req(p0_req), .p0_ack(p0_ack), .sram_rd(wx_rd), .sram_rdy(wx_rdy),\n"
         "    .frame_tgl(ro_tgl), .s_pc(ro_pc), .s_st(ro_st), .s_upc(ro_upc), .s_we(ro_we), .s_da(ro_da), .s_fa(ro_fa), .s_fl(ro_fl),\n"
         "    .s_op(ro_op), .s_age(ro_age));\n"
         "d2_freeze_band #(.ROW0(BAND_ROW0 - 9)) roband (\n"
         "    .clk_vid(clk_vid), .en(BAND_EN != 0),\n"
         "    .rgb_in(diag_rgb), .de(vid_de), .skip(vid_skip), .vs(vid_vs), .rgb_out(video_rgb), .in_ro(ro_px), .frame_tgl(ro_tgl),\n"
         "    .pc(ro_pc), .st(ro_st), .upc(ro_upc), .we(ro_we), .da(ro_da), .fa(ro_fa), .fl(ro_fl), .op(ro_op), .age(ro_age));\n"
         "assign band_px = diag_px | ro_px;   // observation: band or readout pixel\n", 1),
    ], "d2_core_top (SP)")
    hdr = ("// SPDX-License-Identifier: GPL-3.0-or-later\n//\n"
           "// *** D2 SP DERIVED COPY (exp/sprite-int; exploratory, SIMULATED ONLY). Generated by experiments/D2-pocket-top/make_d2_derived.py\n"
           "// from rtl/d2_core_top.sv: upstream sdram -> sp_sdram (p2 sprite ROM + framebuffer fw/fr), framebuffer / p2 stubs ->\n"
           "// sp_fb_sdram, slot 3 sprites.bin (0x3000_0000 -> SDRAM 0x100_0000) via a third data_loader into d2_load_wq3, the core's\n"
           "// sprite-RAM window counters wired out; slot 3 is read back and summed like slots 1 and 2 (d2_slot_verify3, EXP_SUM2 / EXP_FL2;\n"
           "// the CPU reset waits for all three; band cell 5 = tile sums AND sprite sums).\n"
           "// GA2Play5: + the freeze readout (d2_freeze_cap / d2_freeze_band, DE lines 211-219 above the band; observation only).\n"
           "// The comments below are the original file's and describe the fit configuration where they differ.\n")
    return hdr + body.replace("// SPDX-License-Identifier: GPL-3.0-or-later\n//\n", "//\n", 1)

# ------------------------------------------------------------------ AU configuration (exp/audio1: sound, experiments/AUDIO/PLAN.md)
# Every AU file is derived from the corresponding SP file's GENERATED text (load_wq3(), slot_verify3(), top_sp()) or from
# rtl/d2_diag_band.sv, so the sp outputs are untouched by construction; --check covers both.
def load_wq4():
    body = apply(load_wq3(), [
        ("module d2_load_wq3 #(", "module d2_load_wq4 #(", 1),
        ("    input       [15:0] c_data,\n",
         "    input       [15:0] c_data,\n    input              d_en,          // AU: slot 4 (sound ROM)\n"
         "    input       [24:0] d_addr,\n    input       [15:0] d_data,\n", 1),
        ("    input       [24:0] c_base,\n", "    input       [24:0] c_base,\n    input       [24:0] d_base,\n", 1),
        ("reg a_d = 1'b0, b_d = 1'b0, c_d = 1'b0, ack_d = 1'b0;\n"
         "wire a_rise = a_en && !a_d, b_rise = b_en && !b_d, c_rise = c_en && !c_d;",
         "reg a_d = 1'b0, b_d = 1'b0, c_d = 1'b0, d_d = 1'b0, ack_d = 1'b0;\n"
         "wire a_rise = a_en && !a_d, b_rise = b_en && !b_d, c_rise = c_en && !c_d, d_rise = d_en && !d_d;", 1),
        ("    a_d <= a_en; b_d <= b_en; c_d <= c_en; ack_d <= wr_ack;", "    a_d <= a_en; b_d <= b_en; c_d <= c_en; d_d <= d_en; ack_d <= wr_ack;", 1),
        ("        if ((a_rise && b_rise) || (a_rise && c_rise) || (b_rise && c_rise)) n_collide <= n_collide + 1;",
         "        if ((a_rise && b_rise) || (a_rise && c_rise) || (b_rise && c_rise) ||\n"
         "            (d_rise && (a_rise || b_rise || c_rise))) n_collide <= n_collide + 1;", 1),
        ("        if (a_rise || b_rise || c_rise) begin", "        if (a_rise || b_rise || c_rise || d_rise) begin", 1),
        ("                                                    : {24'((c_base + c_addr) >> 1), c_data};",
         "                                                    : c_rise ? {24'((c_base + c_addr) >> 1), c_data}\n"
         "                                                    : {24'((d_base + d_addr) >> 1), d_data};", 1),
    ], "d2_load_wq3 (AU)")
    return body.replace("// D2 SP DERIVED COPY of", "// D2 AU DERIVED COPY (make_d2_derived.py: d2_load_wq3 + a fourth loader input d_*, slot 4 = sound ROM,\n"
                        "// base d_base; a coincidence with any other strobe is counted in n_collide). Below: the d2_load_wq3 header.\n"
                        "// D2 SP DERIVED COPY of", 1)

def slot_verify4():
    body = apply(slot_verify3(), [
        ("module d2_slot_verify3 (", "module d2_slot_verify4 (", 1),
        ("    input       [24:0] base2, input [24:0] bytes2,   // SP: slot 3 (sprite ROM)\n",
         "    input       [24:0] base2, input [24:0] bytes2,   // SP: slot 3 (sprite ROM)\n"
         "    input       [24:0] base3, input [24:0] bytes3,   // AU: slot 4 (sound ROM)\n", 1),
        ("    output reg  [31:0] sum0 = 0, fl0 = 0, sum1 = 0, fl1 = 0, sum2 = 0, fl2 = 0,\n",
         "    output reg  [31:0] sum0 = 0, fl0 = 0, sum1 = 0, fl1 = 0, sum2 = 0, fl2 = 0, sum3 = 0, fl3 = 0,\n", 1),
        ("reg [1:0]  region = 0;               // SP: 0 program, 1 tiles, 2 sprites\n",
         "reg [1:0]  region = 0;               // SP: 0 program, 1 tiles, 2 sprites; AU: 3 sound\n", 1),
        ("wire [24:0] bytes_r = region[1] ? bytes2 : region[0] ? bytes1 : bytes0;\n",
         "wire [24:0] bytes_r = (region == 2'd3) ? bytes3 : region[1] ? bytes2 : region[0] ? bytes1 : bytes0;\n", 1),
        ("sum0 <= 0; fl0 <= 0; sum1 <= 0; fl1 <= 0; sum2 <= 0; fl2 <= 0; cycles <= 0;",
         "sum0 <= 0; fl0 <= 0; sum1 <= 0; fl1 <= 0; sum2 <= 0; fl2 <= 0; sum3 <= 0; fl3 <= 0; cycles <= 0;", 1),
        ("            p5_addr <= 22'(((region[1] ? base2 : region[0] ? base1 : base0) + off) >> 3);\n",
         "            p5_addr <= 22'((((region == 2'd3) ? base3 : region[1] ? base2 : region[0] ? base1 : base0) + off) >> 3);\n", 1),
        ("                    if (region[1]) begin sum2 <= s_next; fl2 <= f_next; done <= 1'b1; st <= S_IDLE; end\n",
         "                    if (region == 2'd3) begin sum3 <= s_next; fl3 <= f_next; done <= 1'b1; st <= S_IDLE; end\n"
         "                    else if (region[1]) begin sum2 <= s_next; fl2 <= f_next; region <= 2'd3; off <= 0;\n"
         "                               acc_s <= 0; acc_f <= 0; st <= S_FALL; end\n", 1),
    ], "d2_slot_verify3 (AU)")
    return body.replace("// D2 SP DERIVED COPY of", "// D2 AU DERIVED COPY (make_d2_derived.py: d2_slot_verify3 + region 3 = slot 4, sound ROM, base3 / bytes3 -> sum3 / fl3,\n"
                        "// after the sprite region; done after it; a slot-4 size of 0 still terminates). Below: the d2_slot_verify3 header.\n"
                        "// D2 SP DERIVED COPY of", 1)

def diag_band_au():
    src = (HERE / "rtl/d2_diag_band.sv").read_text()
    body = apply(src, [
        ("module d2_diag_band #(", "module d2_diag_band_au #(", 1),
        ("    input       [7:0] irq64\n);\n",
         "    input       [7:0] irq64,\n"
         "    // AU: cells 16-29 (cells 0-15 keep their meaning)\n"
         "    input             snd_sums_ok, psram_clear_done, wave_wd_rd, wave_wd_wr,\n"
         "    input             wv_rst_low, wv_tpu_ok, wv_busy, wv_acc_tgl, wclr_timeout,   // AU2: cells 20, 21, 28, 27, 29\n"
         "    input      [15:11] wv_clr_idx                                                  // AU2: cells 22-26 (bit 15 first)\n);\n", 1),
        ("reg [15:0] s1 = 16'd0, s2 = 16'd0;\n", "reg [29:0] s1 = 30'd0, s2 = 30'd0;\n", 1),
        ("    s1 <= {irq64, heartbeat, cpu_running, sums1_ok, sums0_ok, verify_done, all_complete, sdram_ready, pll_locked};\n",
         "    s1 <= {wclr_timeout, wv_busy, wv_acc_tgl, wv_clr_idx[11], wv_clr_idx[12], wv_clr_idx[13], wv_clr_idx[14], wv_clr_idx[15],\n"
         "           wv_tpu_ok, wv_rst_low,\n"
         "           wave_wd_wr, wave_wd_rd, psram_clear_done, snd_sums_ok,\n"
         "           irq64, heartbeat, cpu_running, sums1_ok, sums0_ok, verify_done, all_complete, sdram_ready, pll_locked};\n", 1),
        ("wire [3:0] cix  = rx[6:3];\n", "wire [4:0] cix  = rx[7:3];   // AU: 30 cells\n", 1),
        ("(dx >= 10'(X0)) && (dx < 10'(X0 + 128))", "(dx >= 10'(X0)) && (dx < 10'(X0 + 240))", 1),
        ("    4'd0: col = flag(s2[0]);\n    4'd1: col = flag(s2[1]);\n    4'd2: col = flag(s2[2]);\n    4'd3: col = flag(s2[3]);\n"
         "    4'd4: col = !s2[3] ? BLU : flag(s2[4]);\n    4'd5: col = !s2[3] ? BLU : flag(s2[5]);\n    4'd6: col = flag(s2[6]);\n"
         "    4'd7: col = s2[7] ? WHT : GRY;\n    default: col = cnt[3'(4'd15 - cix)] ? WHT : GRY;\n",
         "    5'd0: col = flag(s2[0]);\n    5'd1: col = flag(s2[1]);\n    5'd2: col = flag(s2[2]);\n    5'd3: col = flag(s2[3]);\n"
         "    5'd4: col = !s2[3] ? BLU : flag(s2[4]);\n    5'd5: col = !s2[3] ? BLU : flag(s2[5]);\n    5'd6: col = flag(s2[6]);\n"
         "    5'd7: col = s2[7] ? WHT : GRY;\n"
         "    5'd16: col = !s2[3] ? BLU : flag(s2[16]);   // AU: sound-ROM (slot 4) sums\n"
         "    5'd17: col = flag(s2[17]);                  // AU: PSRAM wave-RAM clear done\n"
         "    5'd18: col = s2[18] ? RED : GRN;            // AU: wave-read watchdog (red = sticky trip)\n"
         "    5'd19: col = s2[19] ? RED : GRN;            // AU: wave-write watchdog (red = sticky trip)\n"
         "    5'd20: col = flag(s2[20]);                  // AU2: d2_wave_psram rst low (PLL lock seen in clk_sys)\n"
         "    5'd21: col = flag(s2[21]);                  // AU2: d2_wave_psram tPU wait over\n"
         "    5'd22, 5'd23, 5'd24, 5'd25, 5'd26, 5'd27, 5'd28: col = s2[cix] ? WHT : GRY;   // AU2: clr_idx[15:11], access toggle, busy\n"
         "    5'd29: col = s2[29] ? RED : GRN;            // AU2: clear-wait timeout taken (red)\n"
         "    default: col = cnt[3'(5'd15 - cix)] ? WHT : GRY;\n", 1),
    ], "d2_diag_band (AU)")
    hdr = ("// SPDX-License-Identifier: GPL-3.0-or-later\n//\n"
           "// D2 AU DERIVED COPY of rtl/d2_diag_band.sv (generated by make_d2_derived.py): module d2_diag_band_au = the band with 14\n"
           "// more cells to the right (16-29); cells 0-15 are unchanged in position, colour and meaning:\n"
           "//   16 sound-ROM (slot 4) read-back sums (blue until read back, then green = expected sums, red = different)\n"
           "//   17 PSRAM wave-RAM clear done (green) / not done (red)\n"
           "//   18 wave-READ watchdog: green = never tripped, red = sdr_p4_req was high > WD_LIMIT clk_sys (sticky)\n"
           "//   19 wave-WRITE watchdog: the same for sdr_wave_wr_req\n"
           "//   GA2Audio2 (d2_wave_psram observation; white = 1, grey = 0 unless stated):\n"
           "//   20 d2_wave_psram rst low (green) / high (red)      21 tPU wait over (green) / running (red)\n"
           "//   22 clr_idx[15] (all clear writes issued)           23-26 clr_idx[14:11]\n"
           "//   27 toggles at the end of every PSRAM access        28 sequencer busy\n"
           "//   29 clear-wait timeout taken (red) / not taken (green): the CPU reset stopped waiting for cell 17\n")
    return hdr + body.replace("// SPDX-License-Identifier: GPL-3.0-or-later\n//\n", "//\n", 1)

def soundsys_au():
    """AU timing patch (CI run 43657433253: clk_sys -2.949 ns, jt12 fm1 clk_en -> the retimed s32_audio_mix adders): the eight chip outputs
    are registered (plain free-running clk_sys registers, no enable, no reset) just before s32_audio_mix; s32_audio_mix itself is upstream's
    file, unchanged. Every mixer input is delayed by exactly one clk_sys, so the mixer output is the previous build's, one clk_sys later."""
    body = apply(soundsys(), [
        ("s32_audio_mix output_mixer (\n"
         "    .is_multi32(is_multi32),\n"
         "    .fm1_l(fm1_l), .fm1_r(fm1_r),\n"
         "    .fm2_l(fm2_l), .fm2_r(fm2_r),\n"
         "    .rf_l(rf_l),   .rf_r(rf_r),\n"
         "    .mp_l(mp_l),   .mp_r(mp_r),\n",
         "// D2 AU VARIANT (timing): the chip outputs registered once (free-running clk_sys, no enable / reset) before the unchanged mixer\n"
         "reg signed [15:0] fm1_lq = 16'sd0, fm1_rq = 16'sd0, fm2_lq = 16'sd0, fm2_rq = 16'sd0;\n"
         "reg signed [15:0] rf_lq = 16'sd0, rf_rq = 16'sd0, mp_lq = 16'sd0, mp_rq = 16'sd0;\n"
         "always @(posedge clk) begin\n"
         "    fm1_lq <= fm1_l; fm1_rq <= fm1_r; fm2_lq <= fm2_l; fm2_rq <= fm2_r;\n"
         "    rf_lq <= rf_l; rf_rq <= rf_r; mp_lq <= mp_l; mp_rq <= mp_r;\n"
         "end\n"
         "s32_audio_mix output_mixer (\n"
         "    .is_multi32(is_multi32),\n"
         "    .fm1_l(fm1_lq), .fm1_r(fm1_rq),   // D2 AU VARIANT: registered (upstream: fm1_l / fm1_r)\n"
         "    .fm2_l(fm2_lq), .fm2_r(fm2_rq),\n"
         "    .rf_l(rf_lq),   .rf_r(rf_rq),\n"
         "    .mp_l(mp_lq),   .mp_r(mp_rq),\n", 1),
    ], "s32_soundsys_d2 (AU mixer-input registers)")
    return body.replace("// D2 FIT-CONFIGURATION DERIVED COPY of references/s32/rtl/audio/s32_soundsys.sv (make_d2_derived.py): F2's wave-RAM edit (experiments/F2-fullchip/patch_upstream.py), nothing else.\n",
                        "// D2 AU DERIVED COPY of references/s32/rtl/audio/s32_soundsys.sv (make_d2_derived.py): F2's wave-RAM edit (experiments/F2-fullchip/patch_upstream.py)\n"
                        "// + the AU timing patch (au configuration only): fm1 / fm2 / rf / mp outputs registered once (free-running clk_sys) before the\n"
                        "// unchanged s32_audio_mix (CI run 43657433253: clk_sys -2.949 ns into the mixer). Module name s32_soundsys kept. Nothing else.\n", 1)

AU_TOP_HDR = ("// SPDX-License-Identifier: GPL-3.0-or-later\n//\n"
              "// *** D2 AU DERIVED COPY (exp/audio1; experiments/AUDIO/PLAN.md, owner decisions D1-D5). Generated by\n"
              "// experiments/D2-pocket-top/make_d2_derived.py from the GENERATED sp top (d2_core_top_sp.sv) with the sound subsystem\n"
              "// enabled; nothing else changes:\n"
              "//   * upstream s32_audio_ce (unmodified) drives ce_z80 / ce_fm / ce_pcm (clk_sys = upstream's 48.317 MHz);\n"
              "//   * s32_core p3 (Z80 sound ROM, word cache in s32_soundsys) -> sp_sdram p3 (upstream's port; st_p3 removed); the\n"
              "//     request is a clk_sys level held until ack, the ack is stretched 2 clk_ram (upstream sdram.sv contract);\n"
              "//   * s32_core p4 (RF5C68 wave reads) and the wave-write port -> d2_wave_psram on PSRAM chip 0 (cram0, clk_sys;\n"
              "//     st_p4 / st_wv removed); cram1 stays tied off; the wave-RAM clear runs from PLL lock and the CPU / sound reset\n"
              "//     (rst_r) is also held until it is done;\n"
              "//   * slot 4 sound.bin (bridge 0x4000_0000, 4 MiB; data_loader ADDRESS_SIZE 22, so every write stays inside\n"
              "//     SDRAM 0x20_0000-0x5F_FFFF = SDR_SOUNDCPU_BASE + 4 MiB) via d2_load_wq4, read back by d2_slot_verify4 (sum3 / fl3,\n"
              "//     EXP_SUM3 / EXP_FL3), the CPU reset waits for it; band (d2_diag_band_au) cells 16-19: slot-4 sums, PSRAM clear done,\n"
              "//     wave-read / wave-write watchdogs; cells 0-15 unchanged; GA2Audio2: cells 20-29 = d2_wave_psram observation and the\n"
              "//     clear-wait timeout (rst_r stops waiting for the clear WCLR_TIMEOUT clk_sys = 100 ms after PLL lock; diagnostic);\n"
              "//   * audio: the mixer output (s32_core audio_l / audio_r, combinational in s32_audio_mix) is registered on ce_fm and\n"
              "//     fed to sound_i2s (agg23, MIT; rtl/third_party, sync_fifo use_eab OFF) -> audio_mclk / audio_lrck / audio_dac.\n"
              "// The comments below are the sp file's.\n")

def top_au():
    body = apply(top_sp(), [
        ("id 2 tile ROM (0x2000_0000, 4 MB), SP: id 3 sprite ROM (0x3000_0000, 16 MB);",
         "id 2 tile ROM (0x2000_0000, 4 MB), SP: id 3 sprite ROM (0x3000_0000, 16 MB), AU: id 4 sound ROM (0x4000_0000, 4 MB);", 1),
        ("    parameter [31:0]       EXP_SUM2 = 32'hbcd81c86, EXP_FL2 = 32'hf5dfbf9c    // SP: ga2 sprite ROM image (slot 3, 16 MiB; checksums only)\n",
         "    parameter [31:0]       EXP_SUM2 = 32'hbcd81c86, EXP_FL2 = 32'hf5dfbf9c,   // SP: ga2 sprite ROM image (slot 3, 16 MiB; checksums only)\n"
         "    parameter [31:0]       EXP_SUM3 = 32'h91db8f87, EXP_FL3 = 32'hb7405f70    // AU: ga2 sound ROM image (slot 4, 4 MiB; checksums only)\n", 1),
        ("assign cram0_a='h0; assign cram0_dq={16{1'bZ}}; assign cram0_clk=0;\n"
         "assign cram0_adv_n=1; assign cram0_cre=0; assign cram0_ce0_n=1; assign cram0_ce1_n=1;\n"
         "assign cram0_oe_n=1; assign cram0_we_n=1; assign cram0_ub_n=1; assign cram0_lb_n=1;\n",
         "// AU: cram0 is driven by d2_wave_psram (the RF5C68 wave RAM), below; cram1 stays tied off.\n", 1),
        ("assign audio_mclk = 0; assign audio_lrck = 0; assign audio_dac = 0;   // no audio in the first-pixels core\n",
         "// AU: audio_mclk / audio_lrck / audio_dac are driven by sound_i2s (below).\n", 1),
        ("    reg [24:0] size1_74 = 25'd0, size2_74 = 25'd0, size3_74 = 25'd0;\n",
         "    reg [24:0] size1_74 = 25'd0, size2_74 = 25'd0, size3_74 = 25'd0, size4_74 = 25'd0;\n", 1),
        ("        if (dataslot_requestwrite_id == 16'd3) size3_74 <= dataslot_requestwrite_size[24:0];   // SP\n",
         "        if (dataslot_requestwrite_id == 16'd3) size3_74 <= dataslot_requestwrite_size[24:0];   // SP\n"
         "        if (dataslot_requestwrite_id == 16'd4) size4_74 <= dataslot_requestwrite_size[24:0];   // AU\n", 1),
        ("    reg [24:0] size1_m = 25'd0, size1_r = 25'd0, size2_m = 25'd0, size2_r = 25'd0, size3_m = 25'd0, size3_r = 25'd0;   // SP: + slot 3\n",
         "    reg [24:0] size1_m = 25'd0, size1_r = 25'd0, size2_m = 25'd0, size2_r = 25'd0, size3_m = 25'd0, size3_r = 25'd0;   // SP: + slot 3\n"
         "    reg [24:0] size4_m = 25'd0, size4_r = 25'd0;   // AU: slot 4\n", 1),
        ("    size3_m <= size3_74; size3_r <= size3_m;\n", "    size3_m <= size3_74; size3_r <= size3_m;\n    size4_m <= size4_74; size4_r <= size4_m;\n", 1),
        ("    .write_en(l3_en), .write_addr(l3_addr), .write_data(l3_data));\n",
         "    .write_en(l3_en), .write_addr(l3_addr), .write_data(l3_data));\n"
         "// AU: slot 4 = sound.bin (4 MiB, bridge 0x4000_0000) -> SDRAM SDR_SOUNDCPU_BASE 0x20_0000 (the s32_core p3 region). ADDRESS_SIZE 22:\n"
         "// the slot offset is 22 bits, so no write of this slot can leave 0x20_0000-0x5F_FFFF (it cannot reach the tiles at 0x60_0000).\n"
         "    wire        l4_en; wire [21:0] l4_addr; wire [15:0] l4_data;\n"
         "data_loader #(.ADDRESS_MASK_UPPER_4(4'h4), .ADDRESS_SIZE(22), .OUTPUT_WORD_SIZE(2)) slot4_loader (\n"
         "    .clk_74a(clk_74a), .clk_memory(clk_sys), .bridge_wr(bridge_wr), .bridge_endian_little(bridge_endian_little),\n"
         "    .bridge_addr(bridge_addr), .bridge_wr_data(bridge_wr_data),\n"
         "    .write_en(l4_en), .write_addr(l4_addr), .write_data(l4_data));\n", 1),
        ("// SP: sprite-ROM port (core p2) and the framebuffer client's fw / fr ports\n",
         "// AU: Z80 sound-ROM port (core p3, clk_sys requester; upstream's p3 contract)\n"
         "    wire         au_p3_req, au_p3_ack; wire [24:1] au_p3_addr; wire [15:0] au_p3_dout;\n"
         "// SP: sprite-ROM port (core p2) and the framebuffer client's fw / fr ports\n", 1),
        ("    .p3_req(1'b0), .p3_addr(24'd0), .p3_dout(), .p3_ack(),\n",
         "    .p3_req(au_p3_req), .p3_addr(au_p3_addr), .p3_dout(au_p3_dout), .p3_ack(au_p3_ack),   // AU: Z80 sound ROM\n", 1),
        ("d2_load_wq3 wq (   // SP: three slot inputs\n", "d2_load_wq4 wq (   // AU: four slot inputs\n", 1),
        ("    .c_en(l3_en), .c_addr(l3_addr), .c_data(l3_data),\n"
         "    .a_base(SDR_MAINCPU_BASE), .b_base(SDR_TILES_BASE), .c_base(SDR_SPRITES_BASE),\n",
         "    .c_en(l3_en), .c_addr(l3_addr), .c_data(l3_data),\n"
         "    .d_en(l4_en), .d_addr({3'b000, l4_addr}), .d_data(l4_data),   // AU\n"
         "    .a_base(SDR_MAINCPU_BASE), .b_base(SDR_TILES_BASE), .c_base(SDR_SPRITES_BASE), .d_base(SDR_SOUNDCPU_BASE),\n", 1),
        ("if (!allc_s[2] || !wq_empty || l1_en || l2_en || l3_en || wr_req) quiet",
         "if (!allc_s[2] || !wq_empty || l1_en || l2_en || l3_en || l4_en || wr_req) quiet", 1),
        ("    wire [31:0] v_sum0, v_fl0, v_sum1, v_fl1, v_sum2, v_fl2, v_cycles;\nd2_slot_verify3 ver (   // SP: + slot 3 (sprite ROM)\n"
         "    .base2(SDR_SPRITES_BASE), .bytes2(size3_r), .sum2(v_sum2), .fl2(v_fl2),\n",
         "    wire [31:0] v_sum0, v_fl0, v_sum1, v_fl1, v_sum2, v_fl2, v_sum3, v_fl3, v_cycles;\n"
         "d2_slot_verify4 ver (   // AU: + slot 4 (sound ROM)\n"
         "    .base2(SDR_SPRITES_BASE), .bytes2(size3_r), .sum2(v_sum2), .fl2(v_fl2),\n"
         "    .base3(SDR_SOUNDCPU_BASE), .bytes3(size4_r), .sum3(v_sum3), .fl3(v_fl3),\n", 1),
        ("    reg sums0_ok = 1'b0, sums1_ok = 1'b0, sums2_ok = 1'b0;\n", "    reg sums0_ok = 1'b0, sums1_ok = 1'b0, sums2_ok = 1'b0, sums3_ok = 1'b0;\n", 1),
        ("    sums2_ok <= verify_done && v_sum2 == EXP_SUM2 && v_fl2 == EXP_FL2;   // SP: sprite ROM image\n",
         "    sums2_ok <= verify_done && v_sum2 == EXP_SUM2 && v_fl2 == EXP_FL2;   // SP: sprite ROM image\n"
         "    sums3_ok <= verify_done && v_sum3 == EXP_SUM3 && v_fl3 == EXP_FL3;   // AU: sound ROM image\n", 1),
        ("    reg         rst_r = 1'b1, vrst_r = 1'b1;\n",
         "    reg         rst_r = 1'b1, vrst_r = 1'b1;\n"
         "    wire        wv_clear_done, wv_wd_rd, wv_wd_wr;   // AU: d2_wave_psram status (below)\n"
         "    wire        wv_tpu_ok, wv_busy, wv_acc_tgl; wire [15:11] wv_clr_idx;   // AU2: d2_wave_psram observation (band only)\n"
         "// AU2 diagnostic fallback: if the wave-RAM clear is not done WCLR_TIMEOUT clk_sys (100 ms) after PLL lock, rst_r stops waiting\n"
         "// for it (band cell 17 stays red, cell 29 shows the timeout). The counter stops when the clear is done first, so the normal\n"
         "// path is unchanged (the clear takes about 4.3 ms).\n"
         "    localparam integer WCLR_TIMEOUT = 4831731;   // 100 ms at 48.317 MHz\n"
         "    reg  [22:0] wclr_cnt = 23'd0; reg wclr_to = 1'b0;\n"
         "always @(posedge clk_sys)\n"
         "    if (!locked_s) begin wclr_cnt <= 23'd0; wclr_to <= 1'b0; end\n"
         "    else if (!wv_clear_done && !wclr_to) begin\n"
         "        if (wclr_cnt == 23'(WCLR_TIMEOUT - 1)) wclr_to <= 1'b1; else wclr_cnt <= wclr_cnt + 23'd1;\n"
         "    end\n", 1),
        ("    if (!vd_s[1] || !reset_n_s || !locked_s) begin rst_r <= 1'b1; vrst_r <= 1'b1; vcnt_r <= 32'd0; end\n",
         "    if (!vd_s[1] || !reset_n_s || !locked_s || !(wv_clear_done || wclr_to)) begin rst_r <= 1'b1; vrst_r <= 1'b1; vcnt_r <= 32'd0; end   // AU: + wave-RAM clear (AU2: or timeout)\n", 1),
        ("    wire ce_cpu = (CE_MODE != 0) ? ce_acc : ce_div3;\n",
         "    wire ce_cpu = (CE_MODE != 0) ? ce_acc : ce_div3;\n\n"
         "// ============================================================================================ audio clock enables (AU)\n"
         "// upstream s32_audio_ce, unmodified, connected as Arcade-SegaSystem32.sv:339-342 (reset = the board reset, FM free-running from\n"
         "// PLL lock); D2 has no pause, so the enables go to the core directly. clk_sys = 48.317 MHz = upstream's, so every rate is upstream's.\n"
         "    wire ce_z80, ce_fm, ce_pcm;\n"
         "s32_audio_ce audio_ce (\n"
         "    .clk(clk_sys), .reset(rst_r), .pll_locked(locked_s),\n"
         "    .is_multi32(1'b0), .ce_z80(ce_z80), .ce_fm(ce_fm), .ce_pcm(ce_pcm));\n", 1),
        ("    wire         c_p2_req, c_p3_req, c_p4_req, c_p5_req, c_wave_req, c_fb_er_req, c_fb_rd_req;\n"
         "    wire         c_p2_ack, c_p3_ack, c_p4_ack, c_p5_ack, c_wave_ack, c_fb_er_ack, c_fb_rd_ack;\n"
         "    wire [24:4]  c_p2_addr_u; wire [24:1] c_p3_addr_u, c_p4_addr_u, c_wave_addr_u; wire [24:3] c_p5_addr_u;\n",
         "    wire         c_p2_req, c_p4_req, c_p5_req, c_wave_req, c_fb_er_req, c_fb_rd_req;   // AU: p3 = au_p3_* (above)\n"
         "    wire         c_p2_ack, c_p4_ack, c_p5_ack, c_wave_ack, c_fb_er_ack, c_fb_rd_ack;\n"
         "    wire [24:4]  c_p2_addr_u; wire [24:1] c_p4_addr_u, c_wave_addr_u; wire [24:3] c_p5_addr_u;\n"
         "    wire [15:0]  c_p4_dout;   // AU: wave read data from d2_wave_psram\n", 1),
        ("    wire signed [15:0] audio_l_unused, audio_r_unused;\n", "    wire signed [15:0] core_audio_l, core_audio_r;   // AU: s32_audio_mix output\n", 1),
        ("    .ce_cpu(ce_cpu), .ce_z80(1'b0), .ce_fm(1'b0), .ce_pcm(1'b0),\n",
         "    .ce_cpu(ce_cpu), .ce_z80(ce_z80), .ce_fm(ce_fm), .ce_pcm(ce_pcm),   // AU: upstream s32_audio_ce\n", 1),
        ("    .sdr_p3_req(c_p3_req), .sdr_p3_addr(c_p3_addr_u), .sdr_p3_dout(16'd0), .sdr_p3_ack(c_p3_ack),\n"
         "    .sdr_p4_req(c_p4_req), .sdr_p4_addr(c_p4_addr_u), .sdr_p4_dout(16'd0), .sdr_p4_ack(c_p4_ack),\n",
         "    .sdr_p3_req(au_p3_req), .sdr_p3_addr(au_p3_addr), .sdr_p3_dout(au_p3_dout), .sdr_p3_ack(au_p3_ack),   // AU: sp_sdram p3\n"
         "    .sdr_p4_req(c_p4_req), .sdr_p4_addr(c_p4_addr_u), .sdr_p4_dout(c_p4_dout), .sdr_p4_ack(c_p4_ack),     // AU: d2_wave_psram\n", 1),
        ("    .audio_l(audio_l_unused), .audio_r(audio_r_unused),\n", "    .audio_l(core_audio_l), .audio_r(core_audio_r),   // AU\n", 1),
        ("    wire [15:0] n_p3, n_p4, n_p5, n_wave;\n"
         "d2_ack_stub #(.LAT(10)) st_p3 (.clk(clk_ram), .rst(rst_r), .req(c_p3_req),    .ack(c_p3_ack),    .n_req(n_p3));\n"
         "d2_ack_stub #(.LAT(10)) st_p4 (.clk(clk_ram), .rst(rst_r), .req(c_p4_req),    .ack(c_p4_ack),    .n_req(n_p4));\n"
         "d2_ack_stub #(.LAT(10)) st_p5 (.clk(clk_ram), .rst(rst_r), .req(c_p5_req),    .ack(c_p5_ack),    .n_req(n_p5));\n"
         "d2_ack_stub #(.LAT(4))  st_wv (.clk(clk_ram), .rst(rst_r), .req(c_wave_req),  .ack(c_wave_ack),  .n_req(n_wave));\n",
         "    wire [15:0] n_p5;\n"
         "d2_ack_stub #(.LAT(10)) st_p5 (.clk(clk_ram), .rst(rst_r), .req(c_p5_req),    .ack(c_p5_ack),    .n_req(n_p5));\n"
         "// AU: the RF5C68 wave RAM in PSRAM chip 0 (rtl/d2_wave_psram.sv; clk_sys, the RF5C68's own domain; 4-phase req / ack).\n"
         "// Its clear (32K words of 0 = logical 0xFF, inverted storage) starts at PLL lock; rst_r waits for clear_done (above).\n"
         "d2_wave_psram wvps (\n"
         "    .clk(clk_sys), .rst(!locked_s),\n"
         "    .sdr_p4_req(c_p4_req), .sdr_p4_addr(c_p4_addr_u), .sdr_p4_dout(c_p4_dout), .sdr_p4_ack(c_p4_ack),\n"
         "    .sdr_wave_wr_req(c_wave_req), .sdr_wave_wr_addr(c_wave_addr_u), .sdr_wave_wr_data(c_wave_data_u),\n"
         "    .sdr_wave_wr_be(c_wave_be_u), .sdr_wave_wr_ack(c_wave_ack),\n"
         "    .clear_done(wv_clear_done), .wd_rd(wv_wd_rd), .wd_wr(wv_wd_wr),\n"
         "    .cram_a(cram0_a), .cram_dq(cram0_dq), .cram_wait(cram0_wait), .cram_clk(cram0_clk), .cram_adv_n(cram0_adv_n),\n"
         "    .cram_cre(cram0_cre), .cram_ce0_n(cram0_ce0_n), .cram_ce1_n(cram0_ce1_n), .cram_oe_n(cram0_oe_n), .cram_we_n(cram0_we_n),\n"
         "    .cram_ub_n(cram0_ub_n), .cram_lb_n(cram0_lb_n),\n"
         "    .dbg_tpu_ok(wv_tpu_ok), .dbg_clr_idx(wv_clr_idx), .dbg_acc_tgl(wv_acc_tgl), .dbg_busy(wv_busy));   // AU2: band only\n"
         "// AU: request-edge counters of the ports the stubs used to count (observation only; unconnected in synthesis, pruned)\n"
         "    reg [15:0] n_p3 = 16'd0, n_p4 = 16'd0, n_wave = 16'd0; reg p3q_d = 1'b0, p4q_d = 1'b0, wvq_d = 1'b0;\n"
         "always @(posedge clk_sys) begin\n"
         "    p3q_d <= au_p3_req; p4q_d <= c_p4_req; wvq_d <= c_wave_req;\n"
         "    if (au_p3_req && !p3q_d) n_p3 <= n_p3 + 16'd1;\n"
         "    if (c_p4_req && !p4q_d) n_p4 <= n_p4 + 16'd1;\n"
         "    if (c_wave_req && !wvq_d) n_wave <= n_wave + 16'd1;\n"
         "end\n"
         "// AU: audio out (plan 3b): the combinational s32_audio_mix output is registered once per ce_fm (8.054 MHz, a clk_sys strobe),\n"
         "// so the /10 shift-add tree ends in this register and sound_i2s's change detector / dcfifo see one settled value per update;\n"
         "// sound_i2s (MIT, agg23; as pocket_jaguar core_top.sv:1020-1031) resamples nearest-neighbour to 48 kHz on clk_74a (decision D5).\n"
         "// Timing (CI run 43657433253): two free-running clk_sys pipeline registers in front of the ce_fm register give the register\n"
         "// retimer registers to place inside the /10 tree (with the mixer-input registers of s32_soundsys_d2_au.sv: +3 clk_sys of latency,\n"
         "// sample values unchanged). No timing exception.\n"
         "    reg signed [15:0] aud_l_p1 = 16'sd0, aud_r_p1 = 16'sd0, aud_l_p2 = 16'sd0, aud_r_p2 = 16'sd0;\n"
         "    reg signed [15:0] aud_l_r = 16'sd0, aud_r_r = 16'sd0;\n"
         "always @(posedge clk_sys) begin aud_l_p1 <= core_audio_l; aud_r_p1 <= core_audio_r; aud_l_p2 <= aud_l_p1; aud_r_p2 <= aud_r_p1; end\n"
         "always @(posedge clk_sys) if (ce_fm) begin aud_l_r <= aud_l_p2; aud_r_r <= aud_r_p2; end\n"
         "sound_i2s #(\n"
         "    .CHANNEL_WIDTH ( 16 ),\n"
         "    .SIGNED_INPUT  ( 1 )\n"
         ") sound_i2s (\n"
         "    .clk_74a    ( clk_74a ),\n"
         "    .clk_audio  ( clk_sys ),\n"
         "    .audio_l    ( aud_l_r ),\n"
         "    .audio_r    ( aud_r_r ),\n"
         "    .audio_mclk ( audio_mclk ),\n"
         "    .audio_lrck ( audio_lrck ),\n"
         "    .audio_dac  ( audio_dac )\n"
         ");\n", 1),
        ("d2_diag_band #(.ROW0(BAND_ROW0)) band (\n", "d2_diag_band_au #(.ROW0(BAND_ROW0)) band (   // AU: + cells 16-29\n", 1),
        ("     .cpu_running(!rst_r), .heartbeat(wram_acc[HB_BIT]), .irq64(irq64_cnt));\n",
         "     .cpu_running(!rst_r), .heartbeat(wram_acc[HB_BIT]), .irq64(irq64_cnt),\n"
         "    .snd_sums_ok(sums3_ok), .psram_clear_done(wv_clear_done), .wave_wd_rd(wv_wd_rd), .wave_wd_wr(wv_wd_wr),   // AU\n"
         "    .wv_rst_low(locked_s), .wv_tpu_ok(wv_tpu_ok), .wv_busy(wv_busy), .wv_acc_tgl(wv_acc_tgl), .wclr_timeout(wclr_to),\n"
         "    .wv_clr_idx(wv_clr_idx));   // AU2\n", 1),
    ], "d2_core_top_sp (AU)")
    return AU_TOP_HDR + body.replace("// SPDX-License-Identifier: GPL-3.0-or-later\n//\n", "//\n", 1)

# ------------------------------------------------------------------ AU3 configuration (exp/audio3: GA2Audio3, option B)
# The Z80 sound ROM moves from SDRAM (sp_sdram p3) to PSRAM chip 1 (rtl/d2_rom_psram.sv). Both AU3 files are derived from the
# GENERATED au text (top_au(), diag_band_au()), so the au / sp / fit outputs are untouched by construction; --check covers all.
def diag_band_au3():
    body = apply(diag_band_au(), [
        ("module d2_diag_band_au #(", "module d2_diag_band_au3 #(", 1),
        ("    input      [15:11] wv_clr_idx                                                  // AU2: cells 22-26 (bit 15 first)\n);\n",
         "    input      [15:11] wv_clr_idx,                                                 // AU2: cells 22-26 (bit 15 first)\n"
         "    // AU3: the sound-ROM PSRAM client (d2_rom_psram): read-back done (cell 16 blue until then), p3 watchdog, loader overflow\n"
         "    input             snd_verify_done, rom_wd_p3, rom_ld_ovf\n);\n", 1),
        ("reg [29:0] s1 = 30'd0, s2 = 30'd0;\n", "reg [32:0] s1 = 33'd0, s2 = 33'd0;   // AU3: bit 32 = snd_verify_done (not a cell)\n", 1),
        ("    s1 <= {wclr_timeout, wv_busy,", "    s1 <= {snd_verify_done, rom_ld_ovf, rom_wd_p3,\n           wclr_timeout, wv_busy,", 1),
        ("wire [4:0] cix  = rx[7:3];   // AU: 30 cells\n", "wire [4:0] cix  = rx[7:3];   // AU: 30 cells; AU3: 32\n", 1),
        ("(dx >= 10'(X0)) && (dx < 10'(X0 + 240))", "(dx >= 10'(X0)) && (dx < 10'(X0 + 256))", 1),
        ("    5'd16: col = !s2[3] ? BLU : flag(s2[16]);   // AU: sound-ROM (slot 4) sums\n",
         "    5'd16: col = !s2[32] ? BLU : flag(s2[16]);  // AU3: sound-ROM (slot 4) sums, blue until the PSRAM read-back is done\n", 1),
        ("    5'd29: col = s2[29] ? RED : GRN;            // AU2: clear-wait timeout taken (red)\n",
         "    5'd29: col = s2[29] ? RED : GRN;            // AU2: clear-wait timeout taken (red)\n"
         "    5'd30: col = s2[30] ? RED : GRN;            // AU3: sound-ROM p3 watchdog (red = sticky trip)\n"
         "    5'd31: col = s2[31] ? RED : GRN;            // AU3: slot-4 loader word dropped (red = sticky)\n", 1),
    ], "d2_diag_band_au (AU3)")
    return body.replace("// D2 AU DERIVED COPY of rtl/d2_diag_band.sv (generated by make_d2_derived.py): module d2_diag_band_au = the band with 14\n",
                        "// D2 AU3 DERIVED COPY (make_d2_derived.py: d2_diag_band_au + two cells, 30 / 31, and cell 16's blue phase follows the PSRAM\n"
                        "// read-back of slot 4 (snd_verify_done) instead of the SDRAM read-back (cell 3); every other cell unchanged):\n"
                        "//   16 sound-ROM (slot 4) read-back sums: blue until d2_rom_psram's read-back is done, then green / red as before\n"
                        "//   30 sound-ROM p3 watchdog: green = never tripped, red = sdr_p3_req was high > WD_LIMIT clk_sys (sticky)\n"
                        "//   31 slot-4 loader overflow: green = no word dropped, red = a loader word found d2_rom_psram's queue full (sticky)\n"
                        "// Below: the d2_diag_band_au header.\n"
                        "// D2 AU DERIVED COPY of rtl/d2_diag_band.sv (generated by make_d2_derived.py): module d2_diag_band_au = the band with 14\n", 1)

AU3_TOP_HDR = ("// SPDX-License-Identifier: GPL-3.0-or-later\n//\n"
               "// *** D2 AU3 DERIVED COPY (exp/audio3, GA2Audio3 = option B, experiments/AUDIO/B_PSRAM_ROM_NOTE.md; owner approval 2026-11-15).\n"
               "// Generated by experiments/D2-pocket-top/make_d2_derived.py from the GENERATED au top (d2_core_top_au.sv); the differences:\n"
               "//   * the Z80 sound ROM (slot 4 sound.bin, 4 MiB) is in PSRAM chip 1 (cram1) behind rtl/d2_rom_psram.sv (rmps, clk_sys): the\n"
               "//     slot-4 data_loader's words go to it (not to the SDRAM write queue), it reads the slot back (sum3 / fl3 -> sums3_ok,\n"
               "//     EXP_SUM3 / EXP_FL3 unchanged) and then serves s32_core's p3 port (the s32_soundsys ROM-cache misses);\n"
               "//   * sp_sdram p3 is tied off again exactly as in the sp top (GA2Play8), and the write queue / SDRAM read-back are the\n"
               "//     sp ones (d2_load_wq3, d2_slot_verify3): SDRAM 0x20_0000-0x5F_FFFF is no longer loaded or read;\n"
               "//   * rst_r (CPU / sound reset) also waits for the PSRAM read-back (rp_vf_done); band d2_diag_band_au3: cell 16 blue until\n"
               "//     that read-back is done, + cell 30 (p3 watchdog) and cell 31 (slot-4 loader word dropped).\n"
               "// d2_wave_psram on cram0 (the RF5C68 wave RAM) and everything else are the au file's. The comments below are the au file's.\n")

# exp/audio3-2p (GA2Audio3 2P, owner GO 2026-11-17): Pocket controller 2 (cont2_key, Dock) -> GA2 player 2, au3 top ONLY (fit / sp / au
# tops keep the 1P d2_controls). MAME ga2u ports: P2_A (C00002, io in_pb) = the P1_A layout; SERVICE12 b5 = Start2; EXTRA3 (8255 port C)
# b2 = COIN2 on the US set (b3 = COIN1). SERVICE12 b2/b3 are COIN4/COIN3 on ga2u and stay released (README double-coin finding). With
# cont2_key = 0 (no second controller) every port equals the 1P constants (FF / FF / 0F), so a 1P session drives the same port values.
AU3_2P = [
    ("// Idle (no key): p1a = svc12 = FF, ppi_pc = 0F, exactly the previous constants.\n"
     "module d2_controls (\n"
     "    input  wire        clk,\n"
     "    input  wire [15:0] key,\n"
     "    output wire  [7:0] p1a, svc12, ppi_pc\n"
     ");\n"
     "    reg  [15:0] key_m = 16'd0, key_s = 16'd0;\n"
     "always @(posedge clk) begin key_m <= key; key_s <= key_m; end\n"
     "    wire        k_up = key_s[0], k_dn = key_s[1], k_lf = key_s[2], k_rt = key_s[3];\n"
     "    wire        k_atk = key_s[4], k_jmp = key_s[5], k_mag = key_s[6] | key_s[7], k_sel = key_s[14], k_sta = key_s[15];\n"
     "assign p1a    = ~{k_lf, k_rt, k_up, k_dn, 1'b0, k_mag, k_jmp, k_atk};\n"
     "assign svc12  = ~{3'b000, k_sta, 4'b0000};\n"
     "assign ppi_pc = {4'h0, ~k_sel, 3'b111};\n"
     "endmodule\n",
     "// Idle (no key): p1a = svc12 = FF, ppi_pc = 0F, exactly the previous constants.\n"
     "// 2P (exp/audio3-2p): key2 = Pocket cont2_key[15:0] (Dock controller 2), the same button layout and synchroniser, drives MAME ga2u\n"
     "//   p2a   (C00002, P2_A): the p1a layout; svc12 b5 Start2 (start); ppi_pc b2 Coin2 (select; EXTRA3 COIN2 of the US set).\n"
     "//   svc12 b2/b3 (COIN4/COIN3 on ga2u) stay released, as for player 1. Idle (no key2): p2a = FF and svc12 / ppi_pc as above.\n"
     "module d2_controls (\n"
     "    input  wire        clk,\n"
     "    input  wire [15:0] key,\n"
     "    input  wire [15:0] key2,\n"
     "    output wire  [7:0] p1a, p2a, svc12, ppi_pc\n"
     ");\n"
     "    reg  [15:0] key_m = 16'd0, key_s = 16'd0;\n"
     "    reg  [15:0] key2_m = 16'd0, key2_s = 16'd0;\n"
     "always @(posedge clk) begin key_m <= key; key_s <= key_m; key2_m <= key2; key2_s <= key2_m; end\n"
     "    wire        k_up = key_s[0], k_dn = key_s[1], k_lf = key_s[2], k_rt = key_s[3];\n"
     "    wire        k_atk = key_s[4], k_jmp = key_s[5], k_mag = key_s[6] | key_s[7], k_sel = key_s[14], k_sta = key_s[15];\n"
     "    wire        q_up = key2_s[0], q_dn = key2_s[1], q_lf = key2_s[2], q_rt = key2_s[3];\n"
     "    wire        q_atk = key2_s[4], q_jmp = key2_s[5], q_mag = key2_s[6] | key2_s[7], q_sel = key2_s[14], q_sta = key2_s[15];\n"
     "assign p1a    = ~{k_lf, k_rt, k_up, k_dn, 1'b0, k_mag, k_jmp, k_atk};\n"
     "assign p2a    = ~{q_lf, q_rt, q_up, q_dn, 1'b0, q_mag, q_jmp, q_atk};\n"
     "assign svc12  = ~{2'b00, q_sta, k_sta, 4'b0000};\n"
     "assign ppi_pc = {4'h0, ~k_sel, ~q_sel, 2'b11};\n"
     "endmodule\n", 1),
    ("    wire  [7:0] g_p1a, g_svc12, g_ppi_pc;\n"
     "d2_controls ctl (.clk(clk_sys), .key(cont1_key[15:0]), .p1a(g_p1a), .svc12(g_svc12), .ppi_pc(g_ppi_pc));\n",
     "    wire  [7:0] g_p1a, g_p2a, g_svc12, g_ppi_pc;   // 2P: + g_p2a\n"
     "d2_controls ctl (.clk(clk_sys), .key(cont1_key[15:0]), .key2(cont2_key[15:0]), .p1a(g_p1a), .p2a(g_p2a), .svc12(g_svc12), .ppi_pc(g_ppi_pc));\n", 1),
    ("    .in_p1a(g_p1a), .in_p2a(8'hff), .in_portc(8'hff),\n",
     "    .in_p1a(g_p1a), .in_p2a(g_p2a), .in_portc(8'hff),   // 2P: player 2 = Pocket controller 2\n", 1),
]

# exp/audio3-4p (GA2Audio3 4P, owner approval 2026-11-17): Pocket controllers 3 / 4 (cont3_key / cont4_key, Dock) -> GA2 players 3 / 4,
# applied on top of AU3_2P, au3 top ONLY. MAME ga2u (0.289 field masks, GA2-4P-2026-11-17/mame): EXTRA1 = 8255 port A = P3, EXTRA2 = port B
# = P4, both the P1_A layout (upstream Arcade-SegaSystem32.sv core_ppi_pa / core_ppi_pb = p_dig(joystick_2 / 3)); EXTRA3 (port C) b0 Start3,
# b1 Start4 (upstream ga2_ppi_pc); SERVICE12 b3 Coin 3, b2 Coin 4 (ga2u; upstream has no P3 / P4 coin). With cont3/4_key = 0 every port
# equals the 2P values (ppi_pa = ppi_pb = FF, svc12 b2/b3 and ppi_pc b0/b1 released).
AU3_4P = [
    ("// 2P (exp/audio3-2p): key2 = Pocket cont2_key[15:0] (Dock controller 2), the same button layout and synchroniser, drives MAME ga2u\n"
     "//   p2a   (C00002, P2_A): the p1a layout; svc12 b5 Start2 (start); ppi_pc b2 Coin2 (select; EXTRA3 COIN2 of the US set).\n"
     "//   svc12 b2/b3 (COIN4/COIN3 on ga2u) stay released, as for player 1. Idle (no key2): p2a = FF and svc12 / ppi_pc as above.\n"
     "module d2_controls (\n"
     "    input  wire        clk,\n"
     "    input  wire [15:0] key,\n"
     "    input  wire [15:0] key2,\n"
     "    output wire  [7:0] p1a, p2a, svc12, ppi_pc\n"
     ");\n"
     "    reg  [15:0] key_m = 16'd0, key_s = 16'd0;\n"
     "    reg  [15:0] key2_m = 16'd0, key2_s = 16'd0;\n"
     "always @(posedge clk) begin key_m <= key; key_s <= key_m; key2_m <= key2; key2_s <= key2_m; end\n"
     "    wire        k_up = key_s[0], k_dn = key_s[1], k_lf = key_s[2], k_rt = key_s[3];\n"
     "    wire        k_atk = key_s[4], k_jmp = key_s[5], k_mag = key_s[6] | key_s[7], k_sel = key_s[14], k_sta = key_s[15];\n"
     "    wire        q_up = key2_s[0], q_dn = key2_s[1], q_lf = key2_s[2], q_rt = key2_s[3];\n"
     "    wire        q_atk = key2_s[4], q_jmp = key2_s[5], q_mag = key2_s[6] | key2_s[7], q_sel = key2_s[14], q_sta = key2_s[15];\n"
     "assign p1a    = ~{k_lf, k_rt, k_up, k_dn, 1'b0, k_mag, k_jmp, k_atk};\n"
     "assign p2a    = ~{q_lf, q_rt, q_up, q_dn, 1'b0, q_mag, q_jmp, q_atk};\n"
     "assign svc12  = ~{2'b00, q_sta, k_sta, 4'b0000};\n"
     "assign ppi_pc = {4'h0, ~k_sel, ~q_sel, 2'b11};\n"
     "endmodule\n",
     "// 2P (exp/audio3-2p): key2 = Pocket cont2_key[15:0] (Dock controller 2), the same button layout and synchroniser, drives MAME ga2u\n"
     "//   p2a   (C00002, P2_A): the p1a layout; svc12 b5 Start2 (start); ppi_pc b2 Coin2 (select; EXTRA3 COIN2 of the US set).\n"
     "// 4P (exp/audio3-4p): key3 / key4 = Pocket cont3_key / cont4_key [15:0] (Dock controllers 3 / 4), the same layout and synchroniser:\n"
     "//   ppa / ppb (8255 port A / B = MAME EXTRA1 / EXTRA2 = P3 / P4): the p1a layout; ppi_pc b0 Start3 / b1 Start4 (start);\n"
     "//   svc12 b3 Coin 3 / b2 Coin 4 (select; ga2u SERVICE12 COIN3 / COIN4). Idle (no key2-4): p2a = ppa = ppb = FF, svc12 / ppi_pc as 1P.\n"
     "module d2_controls (\n"
     "    input  wire        clk,\n"
     "    input  wire [15:0] key,\n"
     "    input  wire [15:0] key2,\n"
     "    input  wire [15:0] key3,\n"
     "    input  wire [15:0] key4,\n"
     "    output wire  [7:0] p1a, p2a, ppa, ppb, svc12, ppi_pc\n"
     ");\n"
     "    reg  [15:0] key_m = 16'd0, key_s = 16'd0;\n"
     "    reg  [15:0] key2_m = 16'd0, key2_s = 16'd0;\n"
     "    reg  [15:0] key3_m = 16'd0, key3_s = 16'd0;\n"
     "    reg  [15:0] key4_m = 16'd0, key4_s = 16'd0;\n"
     "always @(posedge clk) begin key_m <= key; key_s <= key_m; key2_m <= key2; key2_s <= key2_m;\n"
     "                            key3_m <= key3; key3_s <= key3_m; key4_m <= key4; key4_s <= key4_m; end\n"
     "    function automatic [7:0] pdig(input [15:0] k);   // GA2 player port (P1_A layout), active low; X or Y = magic\n"
     "        pdig = ~{k[2], k[3], k[0], k[1], 1'b0, k[6] | k[7], k[5], k[4]};\n"
     "    endfunction\n"
     "assign p1a    = pdig(key_s);\n"
     "assign p2a    = pdig(key2_s);\n"
     "assign ppa    = pdig(key3_s);\n"
     "assign ppb    = pdig(key4_s);\n"
     "assign svc12  = ~{2'b00, key2_s[15], key_s[15], key3_s[14], key4_s[14], 2'b00};\n"
     "assign ppi_pc = {4'h0, ~key_s[14], ~key2_s[14], ~key4_s[15], ~key3_s[15]};\n"
     "endmodule\n", 1),
    ("    wire  [7:0] g_p1a, g_p2a, g_svc12, g_ppi_pc;   // 2P: + g_p2a\n"
     "d2_controls ctl (.clk(clk_sys), .key(cont1_key[15:0]), .key2(cont2_key[15:0]), .p1a(g_p1a), .p2a(g_p2a), .svc12(g_svc12), .ppi_pc(g_ppi_pc));\n",
     "    wire  [7:0] g_p1a, g_p2a, g_ppa, g_ppb, g_svc12, g_ppi_pc;   // 2P: + g_p2a; 4P: + g_ppa / g_ppb\n"
     "d2_controls ctl (.clk(clk_sys), .key(cont1_key[15:0]), .key2(cont2_key[15:0]), .key3(cont3_key[15:0]), .key4(cont4_key[15:0]),\n"
     "    .p1a(g_p1a), .p2a(g_p2a), .ppa(g_ppa), .ppb(g_ppb), .svc12(g_svc12), .ppi_pc(g_ppi_pc));\n", 1),
    ("    .ppi_pa(8'hff), .ppi_pb(8'hff), .ppi_pc(g_ppi_pc),\n",
     "    .ppi_pa(g_ppa), .ppi_pb(g_ppb), .ppi_pc(g_ppi_pc),   // 4P: players 3 / 4 = Pocket controllers 3 / 4\n", 1),
]

# GA2 release candidate 1 (exp/ga2-rc1), applied on top of AU3_4P, au3 top ONLY (owner GO 2026-11-17):
# (1) the on-screen diagnostic overlay off (BAND_EN 0: the band and freeze-readout modules stay instantiated with en = 0, so their
#     pixels pass the game picture through unchanged); (2) pause while the Pocket OS menu is open. APF host command 0x00B0 (OS Notify:
#     Menu State) -> core_bridge_cmd osnotify_inmenu (clk_74a level) -> two-flop synchroniser -> pause_r (clk_sys). pause_r uses
#     upstream's OSD-pause mechanism (Arcade-SegaSystem32.sv:259-267, 932, 987-988): ce_cpu / ce_z80 / ce_fm / ce_pcm gated, the NCOs
#     running behind the gates, s32_core .pause driven, the audio output silenced. With pause_r = 0 every enable and the audio register
#     are cycle-identical to GA2Audio3P4's. The V2 CPU ignores the exec CE; it stops at its next board-bus access (s32_v60_bus runs on
#     ce_cpu; V2's pipeline holds on data_busy); see ~/ga2-evidence/GA2-RC1-2026-11-17/PAUSE_ANALYSIS.md.
AU3_RC = [
    ("    parameter int          BAND_EN         = 1,          // on-screen diagnostic band\n",
     "    parameter int          BAND_EN         = 0,          // on-screen diagnostic band (RC1: off = the full game picture)\n", 1),
    ("    if (rst_r) begin ce_acc <= 1'b0; acc_cpu <= 16'd0; end\n"
     "    else {ce_acc, acc_cpu} <= {1'b0, acc_cpu} + 17'd21848;\n"
     "end\n",
     "    if (rst_r) begin ce_acc <= 1'b0; acc_cpu <= 16'd0; end\n"
     "    else begin {ce_acc, acc_cpu} <= {1'b0, acc_cpu} + 17'd21848; if (pause_r) ce_acc <= 1'b0; end   // RC1: gated, NCO runs on\n"
     "    if (pause_r) ce_div3 <= 1'b0;   // RC1 (CE_MODE 0, simulation only)\n"
     "end\n", 1),
    ("// ============================================================================================ ce_cpu\n",
     "// ============================================================================================ RC1: pause on the Pocket menu\n"
     "// osnotify_inmenu (core_bridge_cmd, clk_74a, set / cleared by host command 0x00B0, 0 after bridge reset) -> clk_sys. clk_74a is an\n"
     "// asynchronous clock group (d2_core_constraints.sdc), as for the controller keys.\n"
     "    reg        menu_m = 1'b0, menu_s = 1'b0, pause_r = 1'b0;\n"
     "always @(posedge clk_sys) begin menu_m <= osnotify_inmenu; menu_s <= menu_m; pause_r <= menu_s; end\n"
     "\n"
     "// ============================================================================================ ce_cpu\n", 1),
    ("    wire ce_z80, ce_fm, ce_pcm;\n"
     "s32_audio_ce audio_ce (\n"
     "    .clk(clk_sys), .reset(rst_r), .pll_locked(locked_s),\n"
     "    .is_multi32(1'b0), .ce_z80(ce_z80), .ce_fm(ce_fm), .ce_pcm(ce_pcm));\n",
     "// RC1: the enables are gated by pause_r as upstream's ce_*_run (the NCOs keep running behind the gates).\n"
     "    wire ce_z80_n, ce_fm_n, ce_pcm_n;\n"
     "s32_audio_ce audio_ce (\n"
     "    .clk(clk_sys), .reset(rst_r), .pll_locked(locked_s),\n"
     "    .is_multi32(1'b0), .ce_z80(ce_z80_n), .ce_fm(ce_fm_n), .ce_pcm(ce_pcm_n));\n"
     "    wire ce_z80 = ce_z80_n & ~pause_r, ce_fm = ce_fm_n & ~pause_r, ce_pcm = ce_pcm_n & ~pause_r;\n", 1),
    ("    .pause(1'b0),\n", "    .pause(pause_r),   // RC1: upstream's pause input (V60 exec cadence, V25 domain)\n", 1),
    ("always @(posedge clk_sys) if (ce_fm) begin aud_l_r <= aud_l_p2; aud_r_r <= aud_r_p2; end\n",
     "// RC1: the output register keeps the ungated ce_fm cadence and loads 0 while paused (upstream: AUDIO = pause ? 0 : aud).\n"
     "always @(posedge clk_sys) if (ce_fm_n) begin aud_l_r <= pause_r ? 16'sd0 : aud_l_p2; aud_r_r <= pause_r ? 16'sd0 : aud_r_p2; end\n", 1),
]

def top_au3():
    body = apply(top_au(), [
        ("// AU: cram0 is driven by d2_wave_psram (the RF5C68 wave RAM), below; cram1 stays tied off.\n"
         "assign cram1_a='h0; assign cram1_dq={16{1'bZ}}; assign cram1_clk=0;\n"
         "assign cram1_adv_n=1; assign cram1_cre=0; assign cram1_ce0_n=1; assign cram1_ce1_n=1;\n"
         "assign cram1_oe_n=1; assign cram1_we_n=1; assign cram1_ub_n=1; assign cram1_lb_n=1;\n",
         "// AU: cram0 is driven by d2_wave_psram (the RF5C68 wave RAM); AU3: cram1 by d2_rom_psram (the Z80 sound ROM); both below.\n", 1),
        ("// AU: slot 4 = sound.bin (4 MiB, bridge 0x4000_0000) -> SDRAM SDR_SOUNDCPU_BASE 0x20_0000 (the s32_core p3 region). ADDRESS_SIZE 22:\n"
         "// the slot offset is 22 bits, so no write of this slot can leave 0x20_0000-0x5F_FFFF (it cannot reach the tiles at 0x60_0000).\n",
         "// AU3: slot 4 = sound.bin (4 MiB, bridge 0x4000_0000) -> d2_rom_psram (PSRAM chip 1, word address = byte offset / 2). ADDRESS_SIZE 22:\n"
         "// the slot offset is 22 bits, so every write of this slot stays inside chip 1's words 0x000000-0x1FFFFF (die 0); none reaches SDRAM.\n", 1),
        ("// AU: Z80 sound-ROM port (core p3, clk_sys requester; upstream's p3 contract)\n",
         "// AU3: Z80 sound-ROM port (core p3, clk_sys requester; upstream's p3 contract) -> d2_rom_psram rmps (below), not sp_sdram\n", 1),
        ("    .p3_req(au_p3_req), .p3_addr(au_p3_addr), .p3_dout(au_p3_dout), .p3_ack(au_p3_ack),   // AU: Z80 sound ROM\n",
         "    .p3_req(1'b0), .p3_addr(24'd0), .p3_dout(), .p3_ack(),\n", 1),
        ("d2_load_wq4 wq (   // AU: four slot inputs\n", "d2_load_wq3 wq (   // SP: three slot inputs\n", 1),
        ("    .d_en(l4_en), .d_addr({3'b000, l4_addr}), .d_data(l4_data),   // AU\n", "", 1),
        ("    .a_base(SDR_MAINCPU_BASE), .b_base(SDR_TILES_BASE), .c_base(SDR_SPRITES_BASE), .d_base(SDR_SOUNDCPU_BASE),\n",
         "    .a_base(SDR_MAINCPU_BASE), .b_base(SDR_TILES_BASE), .c_base(SDR_SPRITES_BASE),\n", 1),
        ("    wire [31:0] v_sum0, v_fl0, v_sum1, v_fl1, v_sum2, v_fl2, v_sum3, v_fl3, v_cycles;\n"
         "d2_slot_verify4 ver (   // AU: + slot 4 (sound ROM)\n"
         "    .base2(SDR_SPRITES_BASE), .bytes2(size3_r), .sum2(v_sum2), .fl2(v_fl2),\n"
         "    .base3(SDR_SOUNDCPU_BASE), .bytes3(size4_r), .sum3(v_sum3), .fl3(v_fl3),\n",
         "    wire [31:0] v_sum0, v_fl0, v_sum1, v_fl1, v_sum2, v_fl2, v_sum3, v_fl3, v_cycles;   // AU3: v_sum3 / v_fl3 = d2_rom_psram's read-back\n"
         "d2_slot_verify3 ver (   // SP: + slot 3 (sprite ROM)\n"
         "    .base2(SDR_SPRITES_BASE), .bytes2(size3_r), .sum2(v_sum2), .fl2(v_fl2),\n", 1),
        ("    sums3_ok <= verify_done && v_sum3 == EXP_SUM3 && v_fl3 == EXP_FL3;   // AU: sound ROM image\n", "", 1),
        ("    wire        wv_tpu_ok, wv_busy, wv_acc_tgl; wire [15:11] wv_clr_idx;   // AU2: d2_wave_psram observation (band only)\n",
         "    wire        wv_tpu_ok, wv_busy, wv_acc_tgl; wire [15:11] wv_clr_idx;   // AU2: d2_wave_psram observation (band only)\n"
         "    wire        rp_vf_done, rp_wd_p3, rp_ld_ovf, rp_wq_empty, rp_tpu_ok, rp_acc_tgl, rp_busy;   // AU3: d2_rom_psram status (below)\n", 1),
        ("    if (!vd_s[1] || !reset_n_s || !locked_s || !(wv_clear_done || wclr_to)) begin rst_r <= 1'b1; vrst_r <= 1'b1; vcnt_r <= 32'd0; end   // AU: + wave-RAM clear (AU2: or timeout)\n",
         "    if (!vd_s[1] || !reset_n_s || !locked_s || !(wv_clear_done || wclr_to) || !rp_vf_done) begin rst_r <= 1'b1; vrst_r <= 1'b1; vcnt_r <= 32'd0; end   // AU: + wave-RAM clear (AU2: or timeout); AU3: + the PSRAM sound-ROM read-back\n", 1),
        ("    .dbg_tpu_ok(wv_tpu_ok), .dbg_clr_idx(wv_clr_idx), .dbg_acc_tgl(wv_acc_tgl), .dbg_busy(wv_busy));   // AU2: band only\n",
         "    .dbg_tpu_ok(wv_tpu_ok), .dbg_clr_idx(wv_clr_idx), .dbg_acc_tgl(wv_acc_tgl), .dbg_busy(wv_busy));   // AU2: band only\n"
         "// AU3: the Z80 sound ROM in PSRAM chip 1 (rtl/d2_rom_psram.sv; clk_sys, the requester's own domain; 4-phase p3 req / ack). It takes\n"
         "// the slot-4 loader's words, reads the slot back once the loaders are idle (load_done, the same start as the SDRAM read-back of\n"
         "// slots 1-3; the CPU reset waits for rp_vf_done), then serves s32_core's p3 port.\n"
         "    reg  [1:0]  ld_s = 2'b00;   // load_done (clk_ram) into clk_sys\n"
         "always @(posedge clk_sys) ld_s <= {ld_s[0], load_done};\n"
         "d2_rom_psram rmps (\n"
         "    .clk(clk_sys), .rst(!locked_s),\n"
         "    .ld_en(l4_en), .ld_addr(l4_addr), .ld_data(l4_data),\n"
         "    .vf_start(ld_s[1]), .vf_bytes(size4_r), .vf_done(rp_vf_done), .vf_sum(v_sum3), .vf_fl(v_fl3),\n"
         "    .sdr_p3_req(au_p3_req), .sdr_p3_addr(au_p3_addr), .sdr_p3_dout(au_p3_dout), .sdr_p3_ack(au_p3_ack),\n"
         "    .wd_p3(rp_wd_p3), .ld_ovf(rp_ld_ovf), .wq_empty(rp_wq_empty),\n"
         "    .cram_a(cram1_a), .cram_dq(cram1_dq), .cram_wait(cram1_wait), .cram_clk(cram1_clk), .cram_adv_n(cram1_adv_n),\n"
         "    .cram_cre(cram1_cre), .cram_ce0_n(cram1_ce0_n), .cram_ce1_n(cram1_ce1_n), .cram_oe_n(cram1_oe_n), .cram_we_n(cram1_we_n),\n"
         "    .cram_ub_n(cram1_ub_n), .cram_lb_n(cram1_lb_n),\n"
         "    .dbg_tpu_ok(rp_tpu_ok), .dbg_acc_tgl(rp_acc_tgl), .dbg_busy(rp_busy));   // AU3: observation only\n"
         "// AU3: sound-ROM sums against the expected values (clk_sys; the d2_slot_verify4 comparison of the au top, moved to rmps's domain)\n"
         "always @(posedge clk_sys) sums3_ok <= rp_vf_done && v_sum3 == EXP_SUM3 && v_fl3 == EXP_FL3;\n", 1),
        ("d2_diag_band_au #(.ROW0(BAND_ROW0)) band (   // AU: + cells 16-29\n",
         "d2_diag_band_au3 #(.ROW0(BAND_ROW0)) band (   // AU: + cells 16-29; AU3: + 30-31, cell 16 follows the PSRAM read-back\n", 1),
        ("    .wv_clr_idx(wv_clr_idx));   // AU2\n",
         "    .wv_clr_idx(wv_clr_idx),   // AU2\n"
         "    .snd_verify_done(rp_vf_done), .rom_wd_p3(rp_wd_p3), .rom_ld_ovf(rp_ld_ovf));   // AU3\n", 1),
    ], "d2_core_top_au (AU3)")
    body = apply(body, AU3_2P, "d2_core_top_au3 (2P: Pocket controller 2 = GA2 player 2)")
    body = apply(body, AU3_4P, "d2_core_top_au3 (4P: Pocket controllers 3 / 4 = GA2 players 3 / 4)")
    body = apply(body, AU3_RC, "d2_core_top_au3 (RC1: overlay off, pause on the Pocket menu)")
    return AU3_TOP_HDR + body.replace("// SPDX-License-Identifier: GPL-3.0-or-later\n//\n", "//\n", 1)

FILES = {"s32_core_d2.sv": core, "s32_core_d2_fit.sv": core_fit, "s32_soundsys_d2.sv": soundsys,
         "s32_sprite_d2.sv": sprite, "d2_sram_port.sv": sram, "wc_v25_stub_hw.sv": v25,
         "s32_core_d2_sp.sv": core_sp, "d2_load_wq3.sv": load_wq3, "d2_slot_verify3.sv": slot_verify3, "d2_core_top_sp.sv": top_sp,
         "v2_core.sv": v2core_sp, "d2_v60_hw.sv": v60hw_sp,
         "d2_load_wq4.sv": load_wq4, "d2_slot_verify4.sv": slot_verify4, "d2_diag_band_au.sv": diag_band_au, "d2_core_top_au.sv": top_au, "s32_soundsys_d2_au.sv": soundsys_au,
         "d2_diag_band_au3.sv": diag_band_au3, "d2_core_top_au3.sv": top_au3, "s32_sprite_d2_au3.sv": sprite_au3}

if __name__ == "__main__":
    gen = {k: f() for k, f in FILES.items()}
    if "--check" in sys.argv:
        bad = [k for k, v in gen.items() if not (OUT / k).exists() or (OUT / k).read_text() != v]
        print("derived files equal regenerated:", not bad, bad or "")
        sys.exit(1 if bad else 0)
    OUT.mkdir(parents=True, exist_ok=True)
    for k, v in gen.items():
        (OUT / k).write_text(v)
        print("wrote", OUT / k)
