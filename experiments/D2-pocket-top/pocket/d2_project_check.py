#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Static validation of the generated D2 Quartus project BEFORE Quartus runs (README "Draft D2 fit plan" step 4; pattern of
tools/hwt_project_check.py, added after HW-001 builds failed on project configuration). No Quartus is needed.

  d2_project_check.py <project-dir> --template <core-template>/src/fpga [--seed N] [--microcode-pin FILE] [--config fit|sp|spid|univ|au|au3|rel] [--selftest [--neg-tag TAG]]
  --config (default fit): the configuration the project must be. sp = GA2Play2 (fit + the SP sprite path): C9 / C11 / C13 check the
  sp files instead (below); every other check is the same. spid = SpidPlay1 (SM1: sp with the Spider-Man top
  experiments/SM1-spidman/rtl/derived/d2_core_top_spid.sv, its EEPROM read-back sm_eep_bridge_rd.sv and upstream s32_io.sv): the sp
  checks with exactly those differences (C9 / C13 / C16 below). univ = U1b (experiments/U1-universal: GA2Play8's sp top and core derived
  by univ_derive.py into d2_core_top_univ.sv / s32_core_d2_univ.sv, the configuration register file u1_board_cfg.sv, SpidPlay1's EEPROM
  read-back sm_eep_bridge_rd.sv, upstream s32_io.sv): the sp checks with exactly those differences (C9 / C13 / C16 below).
  au = exp/audio1 (sp + sound): C4 / C9 / C11 / C12 / C13 / C16 as below.
  au3 = exp/audio3 (GA2Audio3: au with the Z80 sound ROM in PSRAM chip 1): as au with the au3 differences stated below.
  rel = release/s32-v1 (experiments/U1-universal rel_derive.py: RC1's au3 top with the universal replacements, d2_core_top_rel.sv, on
  the univ core s32_core_d2_univ.sv): the au3 checks with the univ differences (C4 / C9 / C12 / C13 / C16 / C17 below).

Checks (ids are used by the negative controls):
  C1  VERILOG_MACRO / SEARCH_PATH only in the QSF (never in a QIP); the QSF macros are SYNTHESIS=1 plus upstream's macros
      recorded in run.json (all of them also in upstream's Arcade-SegaSystem32.qsf, no MISTER_*), each once.
  C2  every source / QIP / SDC / script path of the QSF and the QIPs resolves; every SEARCH_PATH exists; every `include
      resolves; the project QIP uses only VERILOG_FILE / SYSTEMVERILOG_FILE / VHDL_FILE; no absolute paths.
  C3  no template core files (core/core_top.v, mf_pllbase, stp1.stp, SLD_FILE) in the QSF or the project; apf/ and
      core_bridge_cmd.v are byte-identical to the template's.
  C4  constraints read once: the only SDC_FILE is apf/apf_constraints.sdc (which read_sdc's core/core_constraints.sdc once);
      core/core_constraints.sdc == pocket/d2_core_constraints.sdc (--config univ: those bytes followed by exactly the bytes of
      prep.UNIV_SDC_FRAGMENT, experiments/U1-universal/pocket/u1_fit_hold.sdc); no other .sdc in the project; the core SDC calls
      neither create_clock, derive_pll_clocks nor read_sdc.
      --config au: core/core_constraints.sdc == pocket/d2_core_constraints.sdc + pocket/d2_au_constraints.sdc, and it BEGINS with the
      base SDC byte for byte (no existing constraint changed); the appendix creates no clock, clock group or fitter-only setting.
      --config au3: core/core_constraints.sdc == the base SDC + pocket/d2_au_constraints.sdc + pocket/d2_au3_constraints.sdc (each byte for
      byte, in that order); neither appendix creates a clock, clock group or fitter-only setting.
      --config rel: the au3 SDC followed by exactly prep.UNIV_SDC_FRAGMENT (each byte for byte); the two appendices as au3.
  C5  FAMILY Cyclone V, DEVICE 5CEBA4F23C8, TOP_LEVEL_ENTITY apf_top, junction temperature 0..85 C set once, SignalTap off,
      RBF generation on, QIP and revision names.
  C6  d2_pocket_io applied: 37 sram_* IO_STANDARD "3.3-V LVTTL" lines, no LVCMOS on sram_*, its 12 appended lines present
      once, every pin LOCATION equal to the template's, and the QSF begins with exactly d2_pocket_io.apply(template minus core
      files).
  C7  exactly one altera_pll (mf_pll_ga2.v), 5 outputs (number_of_clocks 5, output 0-4 non-zero, 5-17 zero), one VCO within
      10 ppm (tools/hwt_project_check.pll_feasible).
  C8  v2_cstore/dispatch/mode.hex and microcode.bin sha256 == BASELINE_S2.md section 9 (with --microcode-pin: the three images ==
      the pin's, the pin valid, its V25 value == BASELINE_S2.md's; microcode.bin always == BASELINE_S2.md).
  C9  --config fit: the fit derived core + soundsys + f2_xram_standin are the compiled ones (not the s2 core, not upstream's
      s32_core / s32_soundsys / s32_v60); in the derived core sprite_ram is the stand-in (once) and there is no work_ram
      instance; the wave RAM is external.
      --config sp: s32_core_d2_sp.sv, s32_soundsys_d2.sv, the r3 derived s32_sprite_d2.sv, SP's derived sp_sdram.sv, sp_fb_sdram.sv,
      sp_spriteram_win.sv, d2_core_top_sp.sv, d2_load_wq3.sv, d2_slot_verify3.sv compiled once each; not compiled: the fit core,
      f2_xram_standin, d2_core_top.sv, p1_load_wq.sv, d2_slot_verify.sv, upstream sdram.sv / s32_sprite.sv; in the sp core sprite_ram is
      sp_spriteram_win (once), no work_ram, no s32_big_dpram instance; the wave RAM is external.
      --config spid: as sp, except d2_core_top_spid.sv, sm_eep_bridge_rd.sv and upstream rtl/io/s32_io.sv compiled once each instead of
      d2_core_top_sp.sv, and not compiled: d2_core_top_sp.sv and the WC-sim EEPROM variant s32_io_eep_mame.sv.
      --config univ: as sp, except s32_core_d2_univ.sv, d2_core_top_univ.sv, u1_board_cfg.sv, sm_eep_bridge_rd.sv and upstream rtl/io/s32_io.sv
      compiled once each instead of s32_core_d2_sp.sv / d2_core_top_sp.sv, and not compiled: s32_core_d2_sp.sv, d2_core_top_sp.sv, the
      Spider-Man top d2_core_top_spid.sv and the WC-sim EEPROM variant s32_io_eep_mame.sv; the sprite-RAM / work-RAM check reads the univ core.
      --config au: the sp set with d2_core_top_au.sv / d2_load_wq4.sv / d2_slot_verify4.sv / d2_diag_band_au.sv / s32_soundsys_d2_au.sv instead
      of the sp top / d2_load_wq3 / d2_slot_verify3 / d2_diag_band / s32_soundsys_d2.sv, + d2_wave_psram.sv, third_party sync_fifo.sv / sound_i2s.sv; upstream T80 VHDL
      compiled (never the GHDL simulation netlist); the au top instantiates s32_audio_ce, d2_wave_psram wvps, sound_i2s, the slot-4
      data_loader with ADDRESS_SIZE 22 and d2_load_wq4 with d_base SDR_SOUNDCPU_BASE, and drives no ce_* input of the core with a constant.
      --config au3: the au set with d2_core_top_au3.sv / d2_diag_band_au3.sv instead of the au top / band and the sp d2_load_wq3.sv /
      d2_slot_verify3.sv instead of d2_load_wq4 / d2_slot_verify4, + d2_rom_psram.sv; the au3 top instantiates d2_rom_psram rmps (fed by
      the slot-4 loader, serving the core's p3), d2_wave_psram wvps, sound_i2s, s32_audio_ce; sp_sdram's p3 is tied off exactly as in the
      sp top; no d_* slot input; cram1 is driven by rmps, not tied off; rst_r waits for rp_vf_done.
      au3 r4: the sprite engine is rtl/derived/s32_sprite_d2_au3.sv (r3 + r4), s32_sprite_d2.sv is not compiled (negative r3-sprite).
      --config rel: the au3 set with d2_core_top_rel.sv / s32_core_d2_univ.sv instead of the au3 top / s32_core_d2_sp.sv, + u1_board_cfg.sv,
      sm_eep_bridge_rd.sv and upstream rtl/io/s32_io.sv; not compiled: the au3 / univ / sp / Spider-Man tops, s32_core_d2_sp.sv, the WC-sim
      EEPROM variant; the rel top has the au3 structure with the sound-ROM data_loader on bridge nibble 7 (slot id 7).
  C10 no simulation-only file in the compiled set (check_synth_set.py on the list re-derived from the QIP).
  C11 run.json: config (fit, or the --config value), template rev == pinned, microcode verified, every recorded project file hash matches. With
      --microcode-pin: run.json records that pin (verified_against = its repository path, pin_sha256 = its sha256) and its hashes
      (builder, images); without the flag a run.json that records a pin FAILS (the pin must be named).
      If the pin has builder `args`, run.json must record exactly them (microcode.builder_args); otherwise it must not have the key.
  C12 SDC audit: every "ASSUMPTION: A<n>" id in the SDC is a row of the README table and vice versa (--config univ / rel: the README table
      plus the univ-only table of experiments/U1-universal/README.md, whose ids must be new); every SDC section holding a timing command
      carries one. --config au: also every "ASSUMPTION: AU<n>" id of the appendix is an `| AU<n> |` README row and vice versa.
      --config au3 / rel: the same for AU<n> and, for the au3 appendix, every "ASSUMPTION: AR<n>" id is an `| AR<n> |` README row and vice versa.
  C13 names the SDC uses exist in the RTL: ic (core_top instance in apf_top.v), mp / pll_inst / altera_pll_i, v60 / core,
      sprite, sdramclk_ddr, sram_port with register q, dram_clk and sram_dq ports.
  C14 the PLL the project compiles (mf_pll_ga2.v) and the repository's rtl/mf_pll_ga2.v are byte-identical to the PLL accepted by the
      PLL diagnostic fit (run 42116132593): sha256 ACCEPTED_PLL_SHA256, also equal to experiments/PLL-diag/pocket/mf_pll_ga2.v.
  C15 references/s32 is at its UPSTREAM.lock pin and clean (tools/check_upstream.py, run as a subprocess).
  C16 the seed (--seed N, and run.json's) is in the set tools/ci_request.py allows for the job: SEEDS["d2"] (fit), SEEDS["d2sp"] (--config sp),
      SEEDS["spid"] (--config spid), SEEDS["univ"] (--config univ), SEEDS["d2au"] (--config au), SEEDS["d2au3"] (--config au3),
      SEEDS["rel"] (--config rel).
  C17 the univ-only SDC fragment (A12, U1b; Michael 2026-11-14: "a univ-only fitter hold-optimization margin, not an RTL change").
      --config univ / rel: the code of the repository's fragment AND of the part of the project's core SDC after the shared bytes (rel: after the au3 SDC) (Tcl comment
      and blank lines aside) is exactly UNIV_FRAGMENT_CODE: one fitter-only (quartus_fit) set_clock_uncertainty -hold -add
      -enable_same_physical_edge -from clk_sys -to clk_ram 0.050, its clock lookup / d2_require / message, nothing else (no other timing
      command, no exception, no setup uncertainty, no sign-off effect); no comment line ends in a backslash (a Tcl comment continued onto
      the next line would silently disable code); run.json sdc_univ_fragment == {path, sha256 of the repository file}.
      Every other configuration: run.json records no sdc_univ_fragment (and C4 keeps its core SDC the shared file).
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
D2 = HERE.parent
ROOT = D2.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools"))
import d2_pocket_io  # noqa: E402
import prepare_quartus_d2 as prep  # noqa: E402
from hwt_project_check import pll_feasible  # noqa: E402

ASSIGN = re.compile(r'^\s*set_(?:global|instance)_assignment\s+(?:.*?\s)?-name\s+(\S+)\s+(.*?)\s*$')
JOIN = re.compile(r'\[file join \$::quartus\(qip_path\)\s+([^\]]+)\]')
ALLOWED_QIP_NAMES = {"VERILOG_FILE", "SYSTEMVERILOG_FILE", "VHDL_FILE"}
# sha256 of mf_pll_ga2.v accepted by the PLL diagnostic fit (CI run 42116132593, record: experiments/PLL-diag/README.md; the run's
# source_sha256.txt lists the same hash for experiments/PLL-diag/pocket/mf_pll_ga2.v)
ACCEPTED_PLL_SHA256 = "307ae526ac707305564236d5106667f789dc4ea4ddd7dd7b33fbb3d824c84d5f"
# C17: the approved code of the univ-only fragment A12 (experiments/U1-universal/pocket/u1_fit_hold.sdc), line for line, comments and
# blank lines aside. A change of the constraint needs a change here (and a new identity pin, tools/d2rbf_identity.py SDC_FRAGMENTS).
UNIV_FRAGMENT_CODE = (
    'if {$::TimeQuestInfo(nameofexecutable) eq "quartus_fit"} {',
    'set u1_ram_clk [get_clocks -nowarn {ic|mp|pll_inst|altera_pll_i|*[0].*|divclk}]',
    'd2_require [expr {[get_collection_size $sys_clk] == 1 && [get_collection_size $u1_ram_clk] == 1}] "clk_sys and clk_ram PLL output clocks (U1 A12)"',
    'set_clock_uncertainty -hold -add -enable_same_physical_edge -from $sys_clk -to $u1_ram_clk 0.050',
    'post_message -type info "U1: fitter hold margin request +0.050 ns on clk_sys -> clk_ram (A12)"',
    '}',
)
U1_README = ROOT / "experiments/U1-universal/README.md"


def sdc_code(text):
    """(code lines, comment lines ending in a backslash): Tcl comment and blank lines dropped, whitespace stripped."""
    lines = [l.strip() for l in text.splitlines()]
    return [l for l in lines if l and not l.startswith("#")], [l for l in lines if l.startswith("#") and l.endswith("\\")]


def strip_c(text):
    return re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", text, flags=re.S))


def expand(qfile, base, out):
    """(name, resolved path or raw value, from-file, raw) for every assignment, QIP_FILEs followed."""
    qfile = Path(qfile)
    if not qfile.is_file():
        out.append(("MISSING_QIP", qfile, qfile, str(qfile)))
        return
    for line in qfile.read_text(errors="replace").splitlines():
        m = ASSIGN.match(line)
        if not m:
            continue
        name, val = m.group(1), m.group(2).strip()
        j = JOIN.fullmatch(val)
        p = (qfile.parent / j.group(1).strip().strip('"')) if j else (base / val.strip('"'))
        out.append((name, Path(p), qfile, val))
        if name == "QIP_FILE":
            expand(Path(p), base, out)


def run_checks(proj, tpl, seed=None, pin=None, config="fit"):
    """-> list of (id, ok, message). seed: the request seed (None = only run.json's is checked). pin: a microcode pin file
    (None = BASELINE_S2.md section 9)."""
    res = []

    def check(cid, ok, msg):
        res.append((cid, bool(ok), msg))

    proj = Path(proj).resolve()
    tpl = Path(tpl).resolve()
    qsf_p = proj / "ga2_d2.qsf"
    if not qsf_p.is_file():
        check("C2", False, f"{qsf_p} exists")
        return res
    qsf = qsf_p.read_bytes().decode()
    qsf_lines = qsf.replace("\r", "").splitlines()
    items = []
    expand(qsf_p, proj, items)
    run = {}
    try:
        run = json.loads((proj / "run.json").read_text())
    except Exception as e:                                           # noqa: BLE001
        check("C11", False, f"run.json readable ({e})")
    qips = [p for n, p, _, _ in items if n == "QIP_FILE" and p.is_file()]
    d2qip = proj / "d2_core.qip"

    # ---- C1 macros / search paths
    in_qip = [(n, q.name) for n, _p, q, _r in items if n in ("VERILOG_MACRO", "SEARCH_PATH") and q.suffix == ".qip"]
    check("C1", not in_qip, f"C1 no VERILOG_MACRO / SEARCH_PATH in any QIP{': ' + str(in_qip) if in_qip else ''}")
    macros = re.findall(r'^set_global_assignment -name VERILOG_MACRO "([^"]+)"\s*$', "\n".join(qsf_lines), re.M)
    up = (ROOT / "references/s32/Arcade-SegaSystem32.qsf").read_text()
    up_macros = set(re.findall(r'^set_global_assignment -name VERILOG_MACRO "([^"]+)"\s*$', up, re.M))
    want = ["SYNTHESIS=1"] + list(run.get("macros", ["SYNTHESIS=1"])[1:]) if run else ["SYNTHESIS=1"]
    check("C1", "SYNTHESIS=1" in macros and "SYNTHESIS=1" in up_macros,
          "C1 QSF defines SYNTHESIS=1 (upstream's own line, which is also in upstream's Arcade-SegaSystem32.qsf)")
    check("C1", macros == want and len(set(macros)) == len(macros) and set(macros) <= up_macros
          and not any(m.startswith("MISTER_") for m in macros),
          f"C1 QSF macros {macros} == SYNTHESIS=1 + upstream's recorded in run.json, once each, all from upstream, no MISTER_*")

    # ---- C2 paths
    files, notes = [], []
    for n, p, _src, raw in items:
        if n == "MISSING_QIP":
            files.append((n, p))
            continue
        if not n.endswith("_FILE") or raw.strip('"') in ("ON", "OFF"):
            continue
        if n == "PRE_FLOW_SCRIPT_FILE":
            p = (proj / raw.strip('"').split(":", 1)[1]).resolve()
        if n == "MISC_FILE" and not p.exists():
            notes.append(p.name)
            continue
        files.append((n, p))
    missing = [str(p) for n, p in files if not p.exists()]
    check("C2", not missing, f"C2 {len(files)} source/QIP/SDC/script paths resolve" + (f": missing {missing[:3]}" if missing else ""))
    search = [p for n, p, _, _ in items if n == "SEARCH_PATH"]
    check("C2", search and all(p.is_dir() for p in search), f"C2 {len(search)} SEARCH_PATHs exist")
    inc_missing = []
    for n, p in files:
        if p.suffix in (".sv", ".v", ".svh") and p.exists():
            for inc in re.findall(r'^\s*`include\s+"([^"]+)"', p.read_text(errors="replace"), re.M):
                if not any((d / inc).exists() for d in [p.parent] + search):
                    inc_missing.append(f"{p.name}: {inc}")
    check("C2", not inc_missing, "C2 every `include resolves" + (f": {inc_missing[:3]}" if inc_missing else ""))
    ours = {m.group(1) for m in (ASSIGN.match(l) for l in d2qip.read_text().splitlines()) if m} if d2qip.is_file() else None
    check("C2", ours is not None and ours <= ALLOWED_QIP_NAMES,
          f"C2 d2_core.qip uses only {sorted(ALLOWED_QIP_NAMES)} (has {sorted(ours) if ours is not None else 'no file'})")
    qtext = d2qip.read_text() if d2qip.is_file() else ""
    absq = [l for l in qsf_lines + qtext.splitlines() if ASSIGN.match(l) and re.search(r'"/|\s/(Users|home|build|opt|private|var)/', l)]
    check("C2", not absq, "C2 no absolute paths in the QSF or d2_core.qip" + (f": {absq[:2]}" if absq else ""))

    # ---- C3 template files
    forbidden = ("core/core_top.v", "core/mf_pllbase", "stp1.stp", "SLD_FILE", "core/core_bridge_cmd.v")
    bad = [l for l in qsf_lines if not l.lstrip().startswith("#") and any(f in l for f in forbidden)]
    check("C3", not bad, "C3 no template core files / SignalTap files in the QSF" + (f": {bad[:2]}" if bad else ""))
    leftovers = [f for f in ("core/core_top.v", "core/mf_pllbase.v", "core/mf_pllbase.qip", "core/stp1.stp", "core/core_bridge_cmd.v")
                 if (proj / f).exists()]
    check("C3", not leftovers, f"C3 no template core files in the project dir{': ' + str(leftovers) if leftovers else ''}")
    same = (proj / "core_bridge_cmd.v").is_file() and (proj / "core_bridge_cmd.v").read_bytes() == (tpl / "core/core_bridge_cmd.v").read_bytes()
    tfiles = sorted(p.relative_to(tpl / "apf").as_posix() for p in (tpl / "apf").rglob("*") if p.is_file() and p.name != ".DS_Store")
    pfiles = sorted(p.relative_to(proj / "apf").as_posix() for p in (proj / "apf").rglob("*") if p.is_file()) if (proj / "apf").is_dir() else []
    apf_same = tfiles == pfiles and all((tpl / "apf" / f).read_bytes() == (proj / "apf" / f).read_bytes() for f in tfiles)
    check("C3", same and apf_same, f"C3 core_bridge_cmd.v and apf/ ({len(tfiles)} files) byte-identical to the template's")

    # ---- C4 SDC
    sdc_items = [p for n, p in files if n == "SDC_FILE"]
    check("C4", [p.name for p in sdc_items] == ["apf_constraints.sdc"] and sdc_items and sdc_items[0].parent == proj / "apf",
          f"C4 the only SDC_FILE is apf/apf_constraints.sdc ({[str(p.relative_to(proj)) if proj in p.parents else str(p) for p in sdc_items]})")
    core_sdc = proj / "core" / "core_constraints.sdc"
    src_sdc = HERE / "d2_core_constraints.sdc"
    frag_p = ROOT / prep.UNIV_SDC_FRAGMENT
    if config in ("au3", "rel"):
        base_b, app_b, app3_b = src_sdc.read_bytes(), (HERE / "d2_au_constraints.sdc").read_bytes(), (HERE / "d2_au3_constraints.sdc").read_bytes()
        fr_b = frag_p.read_bytes() if config == "rel" and frag_p.is_file() else b""
        got_b = core_sdc.read_bytes() if core_sdc.is_file() else b""
        check("C4", got_b == base_b + app_b + app3_b + fr_b and got_b.startswith(base_b + app_b) and (config != "rel" or frag_p.is_file()),
              f"C4 {config}: core/core_constraints.sdc == pocket/d2_core_constraints.sdc + pocket/d2_au_constraints.sdc + pocket/d2_au3_constraints.sdc"
              + (f" + {prep.UNIV_SDC_FRAGMENT}" if config == "rel" else "") + " (byte for byte)")
        app_t = re.sub(r"#[^\n]*", "", (app_b + app3_b).decode(errors="replace"))
        check("C4", not re.search(r"\b(create_clock|create_generated_clock|derive_pll_clocks|read_sdc|set_clock_groups|set_false_path|set_clock_uncertainty|set_max_delay|set_min_delay|remove_\w+|reset_\w+)\b", app_t),
              f"C4 {config}: the two appendices create / remove no clock, clock group, false path, delay override or fitter-only setting")
    elif config == "au":
        base_b, app_b = src_sdc.read_bytes(), (HERE / "d2_au_constraints.sdc").read_bytes()
        got_b = core_sdc.read_bytes() if core_sdc.is_file() else b""
        check("C4", got_b == base_b + app_b and got_b.startswith(base_b),
              "C4 au: core/core_constraints.sdc == pocket/d2_core_constraints.sdc (byte-for-byte prefix) + pocket/d2_au_constraints.sdc")
        app_t = re.sub(r"#[^\n]*", "", app_b.decode(errors="replace"))
        check("C4", not re.search(r"\b(create_clock|create_generated_clock|derive_pll_clocks|read_sdc|set_clock_groups|set_false_path|set_clock_uncertainty|set_max_delay|set_min_delay|remove_\w+|reset_\w+)\b", app_t),
              "C4 au: the appendix creates / removes no clock, clock group, false path, delay override or fitter-only setting")
    else:
        want_sdc = src_sdc.read_bytes() + (frag_p.read_bytes() if config == "univ" and frag_p.is_file() else b"")
        check("C4", core_sdc.is_file() and core_sdc.read_bytes() == want_sdc and (config != "univ" or frag_p.is_file()),
              "C4 core/core_constraints.sdc == pocket/d2_core_constraints.sdc" + (f" + {prep.UNIV_SDC_FRAGMENT} (univ only)" if config == "univ" else ""))
    sdcs = sorted(p.relative_to(proj).as_posix() for p in proj.rglob("*.sdc"))
    check("C4", sdcs == ["apf/apf_constraints.sdc", "core/core_constraints.sdc"], f"C4 the project holds exactly these SDC files: {sdcs}")
    reads = []
    for s in sdcs:
        reads += [(s, r) for r in re.findall(r'^\s*read_sdc\s+"?([^"\s]+)"?', (proj / s).read_text(errors="replace"), re.M)]
    check("C4", reads == [("apf/apf_constraints.sdc", "core/core_constraints.sdc")] and (proj / "core/core_constraints.sdc").is_file(),
          f"C4 read_sdc appears once (apf -> core/core_constraints.sdc): {reads}")
    ctext = re.sub(r"#[^\n]*", "", core_sdc.read_text(errors="replace")) if core_sdc.is_file() else ""
    check("C4", not re.search(r"\b(create_clock|derive_pll_clocks|read_sdc)\b", ctext),
          "C4 core SDC does not create base clocks, call derive_pll_clocks or read_sdc (apf's SDC does)")

    # ---- C5 device / conventions
    def g(name):
        m = re.findall(rf'^set_global_assignment -name {name}\s+"?([^"\r\n]+?)"?\s*$', "\n".join(qsf_lines), re.M)
        return m
    check("C5", g("FAMILY") == ["Cyclone V"] and g("DEVICE") == ["5CEBA4F23C8"] and g("TOP_LEVEL_ENTITY") == ["apf_top"],
          f"C5 FAMILY {g('FAMILY')} DEVICE {g('DEVICE')} TOP {g('TOP_LEVEL_ENTITY')}")
    check("C5", g("MIN_CORE_JUNCTION_TEMP") == ["0"] and g("MAX_CORE_JUNCTION_TEMP") == ["85"],
          f"C5 junction {g('MIN_CORE_JUNCTION_TEMP')}..{g('MAX_CORE_JUNCTION_TEMP')} C, each set once")
    check("C5", g("ENABLE_SIGNALTAP") == ["OFF"] and g("GENERATE_RBF_FILE") == ["ON"], "C5 SignalTap off (once), RBF generation on")
    check("C5", g("QIP_FILE").count("d2_core.qip") == 1 and g("QIP_FILE").count("apf/apf.qip") == 1 and len(g("SEED")) == 1,
          "C5 QIP_FILE d2_core.qip and apf/apf.qip once each, one SEED")
    check("C5", (proj / "ga2_d2.qpf").is_file() and 'PROJECT_REVISION = "ga2_d2"' in (proj / "ga2_d2.qpf").read_text(), "C5 ga2_d2.qpf names revision ga2_d2")

    # ---- C6 I/O
    sram_std = [l for l in qsf_lines if re.match(r'set_instance_assignment -name IO_STANDARD "3\.3-V LVTTL" -to sram_', l)]
    sram_cmos = [l for l in qsf_lines if re.match(r'set_instance_assignment -name IO_STANDARD "3\.3-V LVCMOS" -to sram_', l)]
    check("C6", len(sram_std) == d2_pocket_io.EXPECTED_SRAM_STD_LINES and not sram_cmos,
          f"C6 {len(sram_std)} sram_* 3.3-V LVTTL lines (expected {d2_pocket_io.EXPECTED_SRAM_STD_LINES}), {len(sram_cmos)} LVCMOS on sram_*")
    app = [l for l in d2_pocket_io.APPEND.splitlines() if l.startswith("set_instance_assignment")]
    present = [qsf_lines.count(l) for l in app]
    check("C6", len(app) == 12 and present == [1] * 12, f"C6 d2_pocket_io's {len(app)} appended lines present once each ({present})")
    loc = lambda lines: sorted(l.strip() for l in lines if l.startswith("set_location_assignment"))
    tq = (tpl / "ap_core.qsf").read_bytes().decode().replace("\r", "").splitlines()
    check("C6", loc(qsf_lines) == loc(tq) and len(loc(tq)) > 100, f"C6 {len(loc(qsf_lines))} pin locations == template's ({len(loc(tq))})")
    try:
        kept = [l for l in (tpl / "ap_core.qsf").read_bytes().decode().splitlines(keepends=True) if not any(d in l for d in prep.DROP)]
        expect_head = d2_pocket_io.apply("".join(kept)).replace("\r\n", "\n").replace("\n", "\r\n")
        check("C6", qsf.startswith(expect_head), "C6 the QSF begins with d2_pocket_io.apply(template minus core files), byte for byte (CRLF-normalised)")
        check("C6", qsf.count("\n") == qsf.count("\r\n") and qsf.endswith("\r\n"), "C6 the QSF uses CRLF line endings throughout (no bare LF)")
    except SystemExit as e:
        check("C6", False, f"C6 d2_pocket_io.apply on the template: {e}")

    # ---- C7 PLL
    srcs = [p for n, p in files if p.is_file() and p.suffix in (".v", ".sv")]
    plls = []
    for p in srcs:
        t = strip_c(p.read_text(errors="replace"))
        plls += [(p, m.start()) for m in re.finditer(r"\baltera_pll\s*#\s*\(", t)]
    check("C7", len(plls) == 1 and plls[0][0].name == "mf_pll_ga2.v", f"C7 exactly one altera_pll instance, in mf_pll_ga2.v ({[p.name for p, _ in plls]})")
    if len(plls) == 1:
        t = strip_c(plls[0][0].read_text())
        nclk = re.search(r"\.number_of_clocks\s*\(\s*(\d+)\s*\)", t)
        fr = {int(i): float(f) for i, f in re.findall(r'\.output_clock_frequency(\d+)\s*\(\s*"([\d.]+) MHz"\s*\)', t)}
        used = [fr.get(i, -1) for i in range(5)]
        unused = [fr.get(i, -1) for i in range(5, 18)]
        check("C7", nclk and int(nclk.group(1)) == 5 and all(f > 0 for f in used) and all(f == 0 for f in unused),
              f"C7 PLL number_of_clocks {nclk.group(1) if nclk else None}, outputs 0-4 {used} MHz, 5-17 all 0 MHz")
        ok, best = pll_feasible(used) if all(f > 0 for f in used) else (False, -1)
        check("C7", ok, "C7 PLL outputs share one VCO in 600-1300 MHz within 10 ppm" + (f" (a common VCO exists, e.g. {best:.3f} MHz)" if ok else f" (closest {best} ppm)"))
    # ---- C8 microcode
    try:
        exp = prep.expected_hashes(pin)
    except SystemExit as e:
        exp = None
        check("C8", False, f"C8 expected microcode hashes unavailable: {e}")
    sh = lambda f: hashlib.sha256((proj / f).read_bytes()).hexdigest() if (proj / f).is_file() else None
    got = {"m1_cstore.hex": sh("v2_cstore.hex"), "m1_dispatch.hex": sh("v2_dispatch.hex"), "m1_mode.hex": sh("v2_mode.hex"),
           "microcode.bin": sh("microcode.bin")}
    if exp is not None:
        check("C8", all(got[k] == exp[k] for k in got),
              f"C8 v2_cstore/dispatch/mode.hex and microcode.bin sha256 == {'BASELINE_S2.md section 9' if pin is None else 'the pin (microcode.bin: BASELINE_S2.md)'}" +
              "".join(f"; {k} MISMATCH" for k in got if got[k] != exp[k]))

    # ---- C9 configuration
    listed = [str(p) for n, p in files if n in ALLOWED_QIP_NAMES and p.exists()]
    has = lambda tail: sum(1 for x in listed if x.endswith(tail))
    if config == "au":
        want1 = ("D2-pocket-top/rtl/derived/s32_core_d2_sp.sv", "D2-pocket-top/rtl/derived/s32_soundsys_d2_au.sv", "D2-pocket-top/rtl/derived/s32_sprite_d2.sv",
                 "SP-sprite-path/rtl/derived/sp_sdram.sv", "SP-sprite-path/rtl/sp_fb_sdram.sv", "SP-sprite-path/rtl/sp_spriteram_win.sv",
                 "D2-pocket-top/rtl/derived/d2_core_top_au.sv", "D2-pocket-top/rtl/derived/d2_load_wq4.sv", "D2-pocket-top/rtl/derived/d2_slot_verify4.sv",
                 "D2-pocket-top/rtl/derived/d2_diag_band_au.sv", "D2-pocket-top/rtl/d2_wave_psram.sv", "D2-pocket-top/rtl/third_party/sync_fifo.sv",
                 "D2-pocket-top/rtl/third_party/sound_i2s.sv", "references/s32/rtl/audio/s32_audio_ce.sv", "references/s32/rtl/audio/s32_rf5c68.sv",
                 "references/s32/rtl/audio/s32_audio_mix.sv", "references/s32/rtl/audio/T80/T80s.vhd", "references/s32/rtl/audio/T80/T80.vhd")
        want0 = ("derived/s32_core_d2_fit.sv", "F2-fullchip/rtl/f2_xram_standin.sv", "D2-pocket-top/rtl/d2_core_top.sv", "rtl/p1_load_wq.sv",
                 "rtl/d2_slot_verify.sv", "references/s32/rtl/mem/sdram.sv", "references/s32/rtl/video/s32_sprite.sv",
                 "D2-pocket-top/rtl/derived/d2_core_top_sp.sv", "D2-pocket-top/rtl/derived/d2_load_wq3.sv", "D2-pocket-top/rtl/derived/d2_slot_verify3.sv",
                 "D2-pocket-top/rtl/d2_diag_band.sv", "t80s_ghdl.v", "D2-pocket-top/rtl/derived/s32_soundsys_d2.sv")
        check("C9", all(has(w) == 1 for w in want1) and all(has(w) == 0 for w in want0),
              "C9 au compiled once each: " + ", ".join(Path(w).name for w in want1) + "; not compiled: " + ", ".join(Path(w).name for w in want0)
              + "".join(f"; {Path(w).name} x{has(w)}" for w in want1 if has(w) != 1) + "".join(f"; {Path(w).name} x{has(w)} (must be 0)" for w in want0 if has(w)))
        check("C9", has("derived/s32_core_d2.sv") == 0 and has("references/s32/rtl/s32_core.sv") == 0 and has("rtl/audio/s32_soundsys.sv") == 0
              and has("cpu/v60/s32_v60.sv") == 0 and has("d2_v60_hw.sv") == 1,
              "C9 not compiled: the s2 core, upstream s32_core / s32_soundsys / s32_v60 (V2 wrapper d2_v60_hw.sv is)")
        dt = strip_c((D2 / "rtl/derived/s32_core_d2_sp.sv").read_text())
        n_sp = len(re.findall(r"\bsp_spriteram_win\b[^;]*?\)\s*sprite_ram\s*\(", dt, re.S))
        check("C9", n_sp == 1 and not re.search(r"\bwork_ram\b", dt) and "s32_big_dpram" not in dt,
              f"C9 au core (= the sp core): sprite_ram = sp_spriteram_win ({n_sp}x), no work_ram, no s32_big_dpram instance")
        at = strip_c((D2 / "rtl/derived/d2_core_top_au.sv").read_text())
        au_struct = {
            "s32_audio_ce audio_ce": re.search(r"\bs32_audio_ce\s+audio_ce\s*\(", at) is not None,
            "core ce_z80/ce_fm/ce_pcm from it": ".ce_z80(ce_z80), .ce_fm(ce_fm), .ce_pcm(ce_pcm)" in at and not re.search(r"\.ce_(z80|fm|pcm)\(1'b[01]\)", at),
            "d2_wave_psram wvps": re.search(r"\bd2_wave_psram\s+wvps\s*\(", at) is not None,
            "sound_i2s CHANNEL_WIDTH 16 SIGNED_INPUT 1": re.search(r"sound_i2s\s*#\(\s*\.CHANNEL_WIDTH\s*\(\s*16\s*\),\s*\.SIGNED_INPUT\s*\(\s*1\s*\)\s*\)\s*sound_i2s\s*\(", at) is not None,
            "slot4 data_loader ADDRESS_SIZE 22": re.search(r"data_loader\s*#\(\.ADDRESS_MASK_UPPER_4\(4'h4\),\s*\.ADDRESS_SIZE\(22\)[^;]*\)\s*slot4_loader\s*\(", at) is not None,
            "d2_load_wq4 d_base SDR_SOUNDCPU_BASE": ".d_base(SDR_SOUNDCPU_BASE)" in at and re.search(r"\bd2_load_wq4\s+wq\s*\(", at) is not None,
            "sp_sdram p3 = the core's p3": ".p3_req(au_p3_req)" in at and ".sdr_p3_req(au_p3_req)" in at,
            "no ack stub on p3 / p4 / wave": not re.search(r"d2_ack_stub[^;]*\bst_(p3|p4|wv)\b", at),
            "cram1 tied off": "assign cram1_ce0_n=1;" in at and "assign cram1_we_n=1;" in at,
        }
        check("C9", all(au_struct.values()), "C9 au top structure: " + ", ".join(au_struct) + "".join(f"; MISSING {k}" for k, v in au_struct.items() if not v))
    elif config in ("au3", "rel"):   # au3 r4: the sprite engine is s32_sprite_d2_au3.sv (r3 + r4), never s32_sprite_d2.sv
        rel = config == "rel"   # rel: the univ core / rel top replace the sp core / au3 top; + the universal files
        CORE_F = "U1-universal/rtl/derived/s32_core_d2_univ.sv" if rel else "D2-pocket-top/rtl/derived/s32_core_d2_sp.sv"
        TOP_F = "U1-universal/rtl/derived/d2_core_top_rel.sv" if rel else "D2-pocket-top/rtl/derived/d2_core_top_au3.sv"
        want1 = (CORE_F, "D2-pocket-top/rtl/derived/s32_soundsys_d2_au.sv", "D2-pocket-top/rtl/derived/s32_sprite_d2_au3.sv",
                 "SP-sprite-path/rtl/derived/sp_sdram.sv", "SP-sprite-path/rtl/sp_fb_sdram.sv", "SP-sprite-path/rtl/sp_spriteram_win.sv",
                 TOP_F, "D2-pocket-top/rtl/derived/d2_load_wq3.sv", "D2-pocket-top/rtl/derived/d2_slot_verify3.sv",
                 "D2-pocket-top/rtl/derived/d2_diag_band_au3.sv", "D2-pocket-top/rtl/d2_wave_psram.sv", "D2-pocket-top/rtl/d2_rom_psram.sv",
                 "D2-pocket-top/rtl/third_party/sync_fifo.sv", "D2-pocket-top/rtl/third_party/sound_i2s.sv", "references/s32/rtl/audio/s32_audio_ce.sv",
                 "references/s32/rtl/audio/s32_rf5c68.sv", "references/s32/rtl/audio/s32_audio_mix.sv", "references/s32/rtl/audio/T80/T80s.vhd",
                 "references/s32/rtl/audio/T80/T80.vhd")
        want0 = ("derived/s32_core_d2_fit.sv", "F2-fullchip/rtl/f2_xram_standin.sv", "D2-pocket-top/rtl/d2_core_top.sv", "rtl/p1_load_wq.sv",
                 "rtl/d2_slot_verify.sv", "references/s32/rtl/mem/sdram.sv", "references/s32/rtl/video/s32_sprite.sv",
                 "D2-pocket-top/rtl/derived/d2_core_top_sp.sv", "D2-pocket-top/rtl/derived/d2_core_top_au.sv", "D2-pocket-top/rtl/derived/d2_load_wq4.sv",
                 "D2-pocket-top/rtl/derived/d2_slot_verify4.sv", "D2-pocket-top/rtl/derived/d2_diag_band_au.sv", "D2-pocket-top/rtl/d2_diag_band.sv",
                 "t80s_ghdl.v", "D2-pocket-top/rtl/derived/s32_soundsys_d2.sv", "D2-pocket-top/rtl/derived/s32_sprite_d2.sv")
        if rel:
            want1 = want1 + ("U1-universal/rtl/u1_board_cfg.sv", "U1-universal/rtl/u1_wram_pr.sv", "U1-universal/rtl/u1_hiscore.sv", "U1-universal/rtl/u1_video.sv", "U1-universal/rtl/u1_save_rd.sv", "SM1-spidman/rtl/sm_eep_bridge_rd.sv", "references/s32/rtl/io/s32_io.sv")   # SN: + u1_wram_pr
            want0 = want0 + ("D2-pocket-top/rtl/derived/d2_core_top_au3.sv", "D2-pocket-top/rtl/derived/s32_core_d2_sp.sv",
                             "U1-universal/rtl/derived/d2_core_top_univ.sv", "SM1-spidman/rtl/derived/d2_core_top_spid.sv",
                             "WC-sim/rtl/sim_variants/s32_io_eep_mame.sv")
        check("C9", all(has(w) == 1 for w in want1) and all(has(w) == 0 for w in want0),
              f"C9 {config} compiled once each: " + ", ".join(Path(w).name for w in want1) + "; not compiled: " + ", ".join(Path(w).name for w in want0)
              + "".join(f"; {Path(w).name} x{has(w)}" for w in want1 if has(w) != 1) + "".join(f"; {Path(w).name} x{has(w)} (must be 0)" for w in want0 if has(w)))
        check("C9", has("derived/s32_core_d2.sv") == 0 and has("references/s32/rtl/s32_core.sv") == 0 and has("rtl/audio/s32_soundsys.sv") == 0
              and has("cpu/v60/s32_v60.sv") == 0 and has("d2_v60_hw.sv") == 1,
              "C9 not compiled: the s2 core, upstream s32_core / s32_soundsys / s32_v60 (V2 wrapper d2_v60_hw.sv is)")
        dt = strip_c((ROOT / "experiments" / CORE_F).read_text())
        n_sp = len(re.findall(r"\bsp_spriteram_win\b[^;]*?\)\s*sprite_ram\s*\(", dt, re.S))
        check("C9", n_sp == 1 and not re.search(r"\bwork_ram\b", dt) and "s32_big_dpram" not in dt,
              f"C9 {config} core ({Path(CORE_F).name}): sprite_ram = sp_spriteram_win ({n_sp}x), no work_ram, no s32_big_dpram instance")
        at = strip_c((ROOT / "experiments" / TOP_F).read_text())
        nib = "7" if rel else "4"   # rel: the sound ROM on slot id 7 / bridge nibble 7 (rel_derive.py C)
        spt = strip_c((D2 / "rtl/derived/d2_core_top_sp.sv").read_text())
        sdr = lambda t: (re.search(r"\bsp_sdram\s+sdram\s*\(.*?\n\);", t, re.S) or [None])[0]
        au3_struct = {
            "s32_audio_ce audio_ce": re.search(r"\bs32_audio_ce\s+audio_ce\s*\(", at) is not None,
            "core ce_z80/ce_fm/ce_pcm from it": ".ce_z80(ce_z80), .ce_fm(ce_fm), .ce_pcm(ce_pcm)" in at and not re.search(r"\.ce_(z80|fm|pcm)\(1'b[01]\)", at),
            "d2_wave_psram wvps": re.search(r"\bd2_wave_psram\s+wvps\s*\(", at) is not None,
            "d2_rom_psram rmps": re.search(r"\bd2_rom_psram\s+rmps\s*\(", at) is not None,
            "sound_i2s CHANNEL_WIDTH 16 SIGNED_INPUT 1": re.search(r"sound_i2s\s*#\(\s*\.CHANNEL_WIDTH\s*\(\s*16\s*\),\s*\.SIGNED_INPUT\s*\(\s*1\s*\)\s*\)\s*sound_i2s\s*\(", at) is not None,
            f"sound data_loader nibble {nib} ADDRESS_SIZE 22": re.search(r"data_loader\s*#\(\.ADDRESS_MASK_UPPER_4\(4'h" + nib + r"\),\s*\.ADDRESS_SIZE\(22\)[^;]*\)\s*slot4_loader\s*\(", at) is not None,
            "slot-4 words to rmps, not to the SDRAM queue": ".ld_en(l4_en), .ld_addr(l4_addr), .ld_data(l4_data)" in at and ".d_en(" not in at and ".d_base(" not in at
                                                            and re.search(r"\bd2_load_wq3\s+wq\s*\(", at) is not None,
            "core p3 = rmps": ".sdr_p3_req(au_p3_req)" in at and re.search(r"\bd2_rom_psram\s+rmps\s*\([^;]*\.sdr_p3_req\(au_p3_req\)", at, re.S) is not None,
            "sp_sdram instance == the sp top's (p3 tied off)": sdr(at) is not None and sdr(at) == sdr(spt) and ".p3_req(1'b0), .p3_addr(24'd0), .p3_dout(), .p3_ack()," in sdr(at),
            "no ack stub on p3 / p4 / wave": not re.search(r"d2_ack_stub[^;]*\bst_(p3|p4|wv)\b", at),
            "cram1 driven by rmps (no tie-off)": "assign cram1_ce0_n=1;" not in at and ".cram_ce0_n(cram1_ce0_n)" in at,
            "rst_r waits for rp_vf_done": ("|| !rp_vf_done || !u1_ok || !wram_clr_done) begin rst_r <= 1'b1;" if rel else "|| !rp_vf_done) begin rst_r <= 1'b1;") in at,   # rel: + the valid configuration; WC: + the work-RAM clear
            **({"WC: work-RAM clear in front of the SRAM port, port reset released for it": re.search(r"\bu1_wram_clear\s+wram_clr\s*\(", at) is not None
                    and ".rst(rst_r && wram_clr_done), .addr(wp_addr), .wdata(wp_wdata), .be(wp_be), .we(wp_we)," in at and ".rd(wp_rd), .q(wx_q), .rdy(wx_rdy)," in at} if rel else {}),
        }
        if rel:
            au3_struct["rel: u1_board_cfg cfg, u1_controls ctl with the guard"] = (re.search(r"\bu1_board_cfg\s+cfg\s*\(", at) is not None
                                                                              and re.search(r"\bu1_controls\s+ctl\s*\([^;]*\.hold\(pause_r\)", at, re.S) is not None)
            au3_struct["rel: read-back size from the clk_sys copy"] = ".vf_bytes(size4_sys)" in at
        check("C9", all(au3_struct.values()), f"C9 {config} top structure: " + ", ".join(au3_struct) + "".join(f"; MISSING {k}" for k, v in au3_struct.items() if not v))
    elif config in ("sp", "spid", "univ"):
        want1 = ("D2-pocket-top/rtl/derived/s32_core_d2_sp.sv", "D2-pocket-top/rtl/derived/s32_soundsys_d2.sv", "D2-pocket-top/rtl/derived/s32_sprite_d2.sv",
                 "SP-sprite-path/rtl/derived/sp_sdram.sv", "SP-sprite-path/rtl/sp_fb_sdram.sv", "SP-sprite-path/rtl/sp_spriteram_win.sv",
                 "D2-pocket-top/rtl/derived/d2_core_top_sp.sv", "D2-pocket-top/rtl/derived/d2_load_wq3.sv", "D2-pocket-top/rtl/derived/d2_slot_verify3.sv")
        want0 = ("derived/s32_core_d2_fit.sv", "F2-fullchip/rtl/f2_xram_standin.sv", "D2-pocket-top/rtl/d2_core_top.sv", "rtl/p1_load_wq.sv",
                 "rtl/d2_slot_verify.sv", "references/s32/rtl/mem/sdram.sv", "references/s32/rtl/video/s32_sprite.sv")
        if config == "spid":       # SpidPlay1: the Spider-Man top replaces the sp top; upstream s32_io.sv replaces the WC-sim EEPROM variant
            want1 = tuple(w for w in want1 if w != "D2-pocket-top/rtl/derived/d2_core_top_sp.sv") + (
                "SM1-spidman/rtl/derived/d2_core_top_spid.sv", "SM1-spidman/rtl/sm_eep_bridge_rd.sv", "references/s32/rtl/io/s32_io.sv")
            want0 = want0 + ("D2-pocket-top/rtl/derived/d2_core_top_sp.sv", "WC-sim/rtl/sim_variants/s32_io_eep_mame.sv")
        if config == "univ":       # U1b: the universal core / top replace the sp ones; + the configuration register file, EEPROM read-back, upstream s32_io.sv
            want1 = tuple(w for w in want1 if w not in ("D2-pocket-top/rtl/derived/d2_core_top_sp.sv", "D2-pocket-top/rtl/derived/s32_core_d2_sp.sv")) + (
                "U1-universal/rtl/derived/s32_core_d2_univ.sv", "U1-universal/rtl/derived/d2_core_top_univ.sv", "U1-universal/rtl/u1_board_cfg.sv",
                "SM1-spidman/rtl/sm_eep_bridge_rd.sv", "references/s32/rtl/io/s32_io.sv")
            want0 = want0 + ("D2-pocket-top/rtl/derived/d2_core_top_sp.sv", "D2-pocket-top/rtl/derived/s32_core_d2_sp.sv",
                             "SM1-spidman/rtl/derived/d2_core_top_spid.sv", "WC-sim/rtl/sim_variants/s32_io_eep_mame.sv")
        check("C9", all(has(w) == 1 for w in want1) and all(has(w) == 0 for w in want0),
              f"C9 {config} compiled once each: " + ", ".join(Path(w).name for w in want1) + "; not compiled: " + ", ".join(Path(w).name for w in want0)
              + "".join(f"; {Path(w).name} x{has(w)}" for w in want1 if has(w) != 1) + "".join(f"; {Path(w).name} x{has(w)} (must be 0)" for w in want0 if has(w)))
        check("C9", has("derived/s32_core_d2.sv") == 0 and has("references/s32/rtl/s32_core.sv") == 0 and has("rtl/audio/s32_soundsys.sv") == 0
              and has("cpu/v60/s32_v60.sv") == 0 and has("d2_v60_hw.sv") == 1,
              "C9 not compiled: the s2 core, upstream s32_core / s32_soundsys / s32_v60 (V2 wrapper d2_v60_hw.sv is)")
        dt = strip_c(((ROOT / "experiments/U1-universal/rtl/derived/s32_core_d2_univ.sv") if config == "univ" else (D2 / "rtl/derived/s32_core_d2_sp.sv")).read_text())
        n_sp = len(re.findall(r"\bsp_spriteram_win\b[^;]*?\)\s*sprite_ram\s*\(", dt, re.S))
        check("C9", n_sp == 1 and not re.search(r"\bwork_ram\b", dt) and "s32_big_dpram" not in dt,
              f"C9 {'univ' if config == 'univ' else 'sp'} core: sprite_ram = sp_spriteram_win ({n_sp}x), no work_ram, no s32_big_dpram instance in it (work RAM is the external SRAM port)")
    else:
        check("C9", has("D2-pocket-top/rtl/derived/s32_core_d2_fit.sv") == 1 and has("D2-pocket-top/rtl/derived/s32_soundsys_d2.sv") == 1
              and has("F2-fullchip/rtl/f2_xram_standin.sv") == 1,
              "C9 compiled: s32_core_d2_fit.sv, s32_soundsys_d2.sv, f2_xram_standin.sv (once each)")
        check("C9", has("derived/s32_core_d2.sv") == 0 and has("references/s32/rtl/s32_core.sv") == 0 and has("rtl/audio/s32_soundsys.sv") == 0
              and has("cpu/v60/s32_v60.sv") == 0 and has("d2_v60_hw.sv") == 1,
              "C9 not compiled: the s2 core, upstream s32_core / s32_soundsys / s32_v60 (V2 wrapper d2_v60_hw.sv is)")
        dc = D2 / "rtl/derived/s32_core_d2_fit.sv"
        dt = strip_c(dc.read_text())
        n_sp = len(re.findall(r"\bf2_xram_standin\b[^;]*?\)\s*sprite_ram\s*\(", dt, re.S))
        check("C9", n_sp == 1 and not re.search(r"\bwork_ram\b", dt) and "s32_big_dpram" not in dt,
              f"C9 derived core: sprite_ram = f2_xram_standin ({n_sp}x), no work_ram, no s32_big_dpram instance in it (work RAM is the external SRAM port)")
    ds = strip_c((D2 / ("rtl/derived/s32_soundsys_d2_au.sv" if config in ("au", "au3") else "rtl/derived/s32_soundsys_d2.sv")).read_text())
    check("C9", re.search(r"\.EXTERNAL_WAVE_RAM\(1'b1\)", ds) is not None, "C9 soundsys: external wave RAM (EXTERNAL_WAVE_RAM = 1)")

    # ---- C10 simulation-only files (check_synth_set.py on the list re-derived from the QIP)
    lst = [p for n, p in files if n in ALLOWED_QIP_NAMES and p.exists()] + [proj / "core_bridge_cmd.v"] + sorted((proj / "apf").glob("*.v"))
    lf = proj / "files_synth_checked.f"
    lf.write_text("\n".join(str(p) for p in lst) + "\n")
    r = subprocess.run([sys.executable, "-B", str(D2 / "check_synth_set.py"), str(lf)], capture_output=True, text=True)
    last = (r.stdout.strip().splitlines() or [""])[-1]
    check("C10", r.returncode == 0, f"C10 check_synth_set.py on the project's compiled set: {last}")

    # ---- C11 provenance
    if run:
        check("C11", run.get("config") == config and run.get("template", {}).get("rev") == prep.TEMPLATE_REV
              and run.get("microcode", {}).get("images_sha256") == run.get("microcode", {}).get("baseline_s2_expected"),
              f"C11 run.json: config {config} (is {run.get('config')!r}), template rev == pinned, microcode images == BASELINE_S2.md")
        mc = run.get("microcode", {})
        if pin is None:
            check("C11", "pin_sha256" not in mc and "builder_args" not in mc and mc.get("verified_against") == "experiments/P1-pocket-top/BASELINE_S2.md section 9",
                  "C11 run.json: microcode verified against BASELINE_S2.md section 9 (no pin recorded)")
        else:
            try:
                pj = prep.microcode_pin.load_pin(pin)
                want_im = {k.replace("m1_", "v2_"): v for k, v in pj["images_sha256"].items()}
                ok = (mc.get("pin_sha256") == prep.sha(pin) and mc.get("verified_against") == prep.microcode_pin.pin_repo_path(pin, prep.ROOT)
                      and mc.get("images_sha256") == want_im and mc.get("baseline_s2_expected") == want_im
                      and mc.get("builder_sha256") == pj["builder_sha256"] and mc.get("v25_microcode_bin_sha256") == pj["v25_microcode_bin_sha256"]
                      and (mc.get("builder_args") == pj["args"] if "args" in pj else "builder_args" not in mc))
            except SystemExit as e:
                ok = False
                print(f"info  pin: {e}")
            check("C11", ok, "C11 run.json: microcode recorded against the pin (pin_sha256, verified_against, builder, builder_args if the pin has them, images, V25)")
        bad = [f for f, h in run.get("project_files", {}).items()
               if not (proj / f).is_file() or hashlib.sha256((proj / f).read_bytes()).hexdigest() != h]
        extra = [p.relative_to(proj).as_posix() for p in proj.rglob("*") if p.is_file()
                 and p.relative_to(proj).as_posix() not in run.get("project_files", {})
                 and p.name not in ("run.json", "files_synth_checked.f")]
        check("C11", not bad and not extra, f"C11 {len(run.get('project_files', {}))} project files match run.json hashes"
              + (f"; changed/missing {bad[:3]}" if bad else "") + (f"; unrecorded {extra[:3]}" if extra else ""))
        print(f"info  run.json: repo {run['repo']['commit'][:9]} dirty={run['repo']['dirty']}, template {run['template']['rev'][:9]}, "
              f"upstream s32 {run['upstream_s32']['commit'][:9]}, seed {run['seed']}")

    # ---- C12 SDC audit (reads the project's copy, which C4 requires to equal the source)
    sdc_t = core_sdc.read_text() if core_sdc.is_file() else ""
    sdc_ids = set(re.findall(r"ASSUMPTION: (A\d+)\b", sdc_t))
    rd = (D2 / "README.md").read_text()
    readme_ids = set(re.findall(r"^\| (A\d+) \|", rd, re.M))
    u1_ids = set(re.findall(r"^\| (A\d+) \|", U1_README.read_text(), re.M)) if config in ("univ", "rel") else set()
    check("C12", sdc_ids and sdc_ids == readme_ids | u1_ids and not (readme_ids & u1_ids),
          f"C12 SDC ASSUMPTION ids {sorted(sdc_ids)} == README table ids {sorted(readme_ids)}" + (f" + univ-only {sorted(u1_ids)}" if u1_ids else ""))
    au_sdc_ids = set(re.findall(r"ASSUMPTION: (AU\d+)\b", sdc_t))
    au_readme_ids = set(re.findall(r"^\| (AU\d+) \|", rd, re.M))
    if config in ("au", "au3", "rel"):
        check("C12", au_sdc_ids and au_sdc_ids == au_readme_ids, f"C12 au appendix ASSUMPTION ids {sorted(au_sdc_ids)} == README AU table ids {sorted(au_readme_ids)}")
    else:
        check("C12", not au_sdc_ids, f"C12 no AU appendix ids in a {config} SDC ({sorted(au_sdc_ids)})")
    ar_sdc_ids = set(re.findall(r"ASSUMPTION: (AR\d+)\b", sdc_t))
    ar_readme_ids = set(re.findall(r"^\| (AR\d+) \|", rd, re.M))
    if config in ("au3", "rel"):
        check("C12", ar_sdc_ids and ar_sdc_ids == ar_readme_ids, f"C12 au3 appendix ASSUMPTION ids {sorted(ar_sdc_ids)} == README AR table ids {sorted(ar_readme_ids)}")
    elif ar_sdc_ids:
        check("C12", False, f"C12 no AU3 appendix ids in a {config} SDC ({sorted(ar_sdc_ids)})")
    secs = re.split(r"^# ---- ", sdc_t, flags=re.M)[1:]
    cmd = re.compile(r"^\s*(set_clock_groups|set_multicycle_path|set_input_delay|set_output_delay|set_clock_uncertainty|create_generated_clock)\b", re.M)
    unl = [s.splitlines()[0][:50] for s in secs if cmd.search(s) and "ASSUMPTION: A" not in s]
    check("C12", secs and not unl, f"C12 every SDC section with a timing command carries an ASSUMPTION tag ({len(secs)} sections" + (f"; untagged {unl}" if unl else ")"))

    # ---- C13 names
    def txt(rel):
        p = (ROOT / rel) if not Path(rel).is_absolute() else Path(rel)
        return strip_c(p.read_text(errors="replace"))
    top = txt({"sp": "experiments/D2-pocket-top/rtl/derived/d2_core_top_sp.sv", "spid": "experiments/SM1-spidman/rtl/derived/d2_core_top_spid.sv",
               "univ": "experiments/U1-universal/rtl/derived/d2_core_top_univ.sv", "au": "experiments/D2-pocket-top/rtl/derived/d2_core_top_au.sv",
               "au3": "experiments/D2-pocket-top/rtl/derived/d2_core_top_au3.sv", "rel": "experiments/U1-universal/rtl/derived/d2_core_top_rel.sv"}
              .get(config, "experiments/D2-pocket-top/rtl/d2_core_top.sv"))
    apf_top = (tpl / "apf/apf_top.v").read_text()
    core_v = txt("experiments/U1-universal/rtl/derived/s32_core_d2_univ.sv" if config in ("univ", "rel") else
                 "experiments/D2-pocket-top/rtl/derived/" + ("s32_core_d2_sp.sv" if config in ("sp", "spid", "au", "au3") else "s32_core_d2_fit.sv"))
    v60 = txt("experiments/D2-pocket-top/rtl/d2_v60_hw.sv")
    sram = txt("experiments/D2-pocket-top/rtl/derived/d2_sram_port.sv")
    pll = txt("experiments/D2-pocket-top/rtl/mf_pll_ga2.v")
    sprite_ok = re.search(r"\bs32_sprite\b[^;]*?\)\s*sprite\s*\(", core_v, re.S) is not None
    names = {
        "apf_top: core_top ic": re.search(r"\bcore_top\s+ic\s*\(", apf_top) is not None,
        "core_top: mf_pll_ga2 mp": re.search(r"\bmf_pll_ga2\s+mp\s*\(", top) is not None,
        "mf_pll_ga2: pll_inst / altera_pll_i": "mf_pll_ga2_0002 pll_inst" in pll and ") altera_pll_i (" in pll,
        "core_top: altddio_out sdramclk_ddr": re.search(r"\)\s*sdramclk_ddr\s*\(", top) is not None,
        "core_top: d2_sram_port sram_port": re.search(r"\bd2_sram_port\s+sram_port\s*\(", top) is not None,
        "d2_sram_port: reg q": re.search(r"output\s+reg\s+\[15:0\]\s+q\b", sram) is not None,
        "s32_core: s32_v60 v60": re.search(r"\bs32_v60\b[^;]*?\)\s*v60\s*\(", core_v, re.S) is not None,
        "s32_v60: v2_core core": re.search(r"\bv2_core\b[^;]*?\)\s*core\s*\(", v60, re.S) is not None,
        "s32_core: s32_sprite sprite": sprite_ok,
        "core_top ports dram_clk / sram_dq": re.search(r"output[^;,]*\bdram_clk\b", top + apf_top) is not None and "sram_dq" in top,
    }
    if config in ("au", "au3"):   # the appendix's object names: wvps|sdr_p4_dout, the cram0_dq port
        wv = txt("experiments/D2-pocket-top/rtl/d2_wave_psram.sv")
        names["core_top: d2_wave_psram wvps"] = re.search(r"\bd2_wave_psram\s+wvps\s*\(", top) is not None
        names["d2_wave_psram: reg sdr_p4_dout"] = re.search(r"output\s+reg\s+\[15:0\]\s+sdr_p4_dout\b", wv) is not None
        names["core_top port cram0_dq"] = re.search(r"inout\s+wire\s+\[15:0\]\s+cram0_dq\b", top) is not None
    if config == "au3":   # the au3 appendix's object names: rmps|sdr_p3_dout, the cram1_dq port
        rp = txt("experiments/D2-pocket-top/rtl/d2_rom_psram.sv")
        names["core_top: d2_rom_psram rmps"] = re.search(r"\bd2_rom_psram\s+rmps\s*\(", top) is not None
        names["d2_rom_psram: reg sdr_p3_dout"] = re.search(r"output\s+reg\s+\[15:0\]\s+sdr_p3_dout\b", rp) is not None
        names["core_top port cram1_dq"] = re.search(r"inout\s+wire\s+\[15:0\]\s+cram1_dq\b", top) is not None
    check("C13", all(names.values()), "C13 SDC object names exist in the RTL" + "".join(f"; MISSING {k}" for k, v in names.items() if not v))

    # ---- C14 accepted PLL
    sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest() if Path(p).is_file() else None
    compiled = [p for n, p in files if p.name == "mf_pll_ga2.v" and p.is_file()]
    hashes = {"repo rtl/mf_pll_ga2.v": sha(D2 / "rtl/mf_pll_ga2.v"), "PLL-diag copy": sha(ROOT / "experiments/PLL-diag/pocket/mf_pll_ga2.v")}
    hashes.update({f"compiled {p.name}": sha(p) for p in compiled})
    check("C14", len(compiled) == 1 and all(h == ACCEPTED_PLL_SHA256 for h in hashes.values()),
          f"C14 mf_pll_ga2.v (repo, PLL-diag copy, {len(compiled)} compiled) sha256 == accepted {ACCEPTED_PLL_SHA256[:12]}..."
          + "".join(f"; {k} {h and h[:12]}" for k, h in hashes.items() if h != ACCEPTED_PLL_SHA256))

    # ---- C15 upstream pin
    r = subprocess.run([sys.executable, "-B", str(ROOT / "tools/check_upstream.py")], capture_output=True, text=True)
    check("C15", r.returncode == 0, "C15 references/s32 at its pin and clean: " + ((r.stdout + r.stderr).strip().splitlines() or ["?"])[-1][:120])

    # ---- C16 seed in the allowed set
    try:
        from ci_request import SEEDS
        allowed = SEEDS[{"sp": "d2sp", "spid": "spid", "univ": "univ", "au": "d2au", "au3": "d2au3", "rel": "rel"}.get(config, "d2")]   # fit = d2 / d2rbf
    except Exception as e:                                           # noqa: BLE001
        allowed, _e = set(), e
    rs = run.get("seed") if run else None
    check("C16", bool(allowed) and rs in allowed and (seed is None or (seed in allowed and seed == rs)),
          f"C16 seed: run.json {rs}, requested {seed}, allowed by ci_request.py {sorted(allowed)}")

    # ---- C17 the univ-only SDC fragment (A12)
    if config in ("univ", "rel"):
        shared = src_sdc.read_bytes() + ((HERE / "d2_au_constraints.sdc").read_bytes() + (HERE / "d2_au3_constraints.sdc").read_bytes() if config == "rel" else b"")
        got = core_sdc.read_bytes() if core_sdc.is_file() else b""
        tail = got[len(shared):] if got.startswith(shared) else got
        for label, text in (("repository fragment", frag_p.read_text(errors="replace") if frag_p.is_file() else ""),
                            ("project core SDC after the shared bytes", tail.decode(errors="replace"))):
            code, cont = sdc_code(text)
            extra = [l for l in code if l not in UNIV_FRAGMENT_CODE] + [l for l in UNIV_FRAGMENT_CODE if l not in code]
            check("C17", tuple(code) == UNIV_FRAGMENT_CODE and not cont,
                  f"C17 {label}: code is exactly the approved A12 block ({len(UNIV_FRAGMENT_CODE)} lines)"
                  + (f"; differs at {[x[:70] for x in extra[:3]]}" if extra else "") + (f"; comment continued by a backslash: {cont[:2]}" if cont else ""))
        want = {"path": prep.UNIV_SDC_FRAGMENT, "sha256": sha(frag_p) if frag_p.is_file() else None}
        check("C17", run.get("sdc_univ_fragment") == want, f"C17 run.json sdc_univ_fragment {run.get('sdc_univ_fragment')} == {want}")
    else:
        check("C17", "sdc_univ_fragment" not in run, f"C17 run.json records no univ-only SDC fragment (--config {config})")
    return res


def report(res):
    fails = 0
    for cid, ok, msg in res:
        print(("ok    " if ok else "FAIL  ") + msg)
        fails += not ok
    return fails


# ---- negative controls --------------------------------------------------------------------------------------------------
def selftest(proj, tpl, tag, seed=None, pin=None, config="fit"):
    proj = proj.resolve()
    assert "build" in proj.parts, "negative controls write tampered copies; only inside build/"
    base = run_checks(proj, tpl, seed, pin, config)
    nbase = report(base)
    print(f"\nbaseline: {'PASS' if not nbase else 'FAIL'}\n")
    if nbase:
        return 1
    tampers = []

    def neg(name, expect, fn):
        tampers.append((name, expect, fn))      # fn(dir) may return a replacement pin path (tampered pins live next to the copy)

    def edit(f, fn):
        def go(d):
            p = d / f
            p.write_bytes(fn(p.read_bytes().decode()).encode())
        return go

    def qsf_sub(a, b, count=1):
        return edit("ga2_d2.qsf", lambda t: (_ for _ in ()).throw(AssertionError(a)) if a not in t else t.replace(a, b, count))

    neg("qsf-bare-lf", "C6", edit("ga2_d2.qsf", lambda t: t.replace("\r\n", "\n", 1)))
    neg("hex-flip", "C8", edit("v2_cstore.hex", lambda t: t[:-2] + ("0" if t[-2] != "0" else "1") + t[-1:]))
    neg("v25-microcode-flip", "C8", edit("microcode.bin", lambda t: ("1" if t[0] != "1" else "0") + t[1:]))
    neg("sdc-changed", "C4", edit("core/core_constraints.sdc", lambda t: t + "# tampered\n"))
    neg("sdc-file-added-to-qsf", "C4", edit("ga2_d2.qsf", lambda t: t + "set_global_assignment -name SDC_FILE core/core_constraints.sdc\n"))

    def second_sdc(d):
        (d / "core" / "extra.sdc").write_text("derive_clock_uncertainty\n")
        edit("ga2_d2.qsf", lambda t: t + "set_global_assignment -name SDC_FILE core/extra.sdc\n")(d)
    neg("second-sdc", "C4", second_sdc)
    neg("sdc-read-twice", "C4", edit("apf/apf_constraints.sdc", lambda t: t + '\nread_sdc "core/core_constraints.sdc"\n'))
    neg("sram-lvcmos", "C6", qsf_sub('IO_STANDARD "3.3-V LVTTL" -to sram_a[0]', 'IO_STANDARD "3.3-V LVCMOS" -to sram_a[0]'))
    neg("io-line-removed", "C6", qsf_sub("set_instance_assignment -name FAST_INPUT_REGISTER ON -to sram_dq[*]\r\n", ""))
    neg("pin-moved", "C6", edit("ga2_d2.qsf", lambda t: re.sub(r"(set_location_assignment PIN_\w+ -to sram_a\[0\])", lambda m: m.group(1).replace("PIN_", "PIN_X", 1), t, count=1)))

    def io_not_applied(d):
        kept = [l for l in (Path(tpl) / "ap_core.qsf").read_bytes().decode().splitlines(keepends=True) if not any(x in l for x in prep.DROP)]
        old = (d / "ga2_d2.qsf").read_bytes().decode()
        tail = old[old.index("\n# ---- D2 Pocket top (prepare_quartus_d2.py) ----"):]
        (d / "ga2_d2.qsf").write_bytes(("".join(kept) + tail).encode())
    neg("io-not-applied", "C6", io_not_applied)

    def pll_copy(mod):
        def go(d):
            t = (D2 / "rtl/mf_pll_ga2.v").read_text()
            (d / "mf_pll_ga2.v").write_text(mod(t))
            edit("d2_core.qip", lambda q: re.sub(r"\[file join \$::quartus\(qip_path\) \S*rtl/mf_pll_ga2\.v\]", "[file join $::quartus(qip_path) mf_pll_ga2.v]", q))(d)
        return go
    neg("pll-vco-infeasible", "C7", pll_copy(lambda t: t.replace('"96.634615 MHz"', '"100.000000 MHz"')))
    neg("pll-4-outputs", "C7", pll_copy(lambda t: t.replace('.output_clock_frequency4    ("12.079326 MHz")', '.output_clock_frequency4    ("0 MHz")')))

    def second_pll(d):
        t = (D2 / "rtl/mf_pll_ga2.v").read_text()
        (d / "extra_pll.v").write_text(t.replace("mf_pll_ga2", "mf_pll_extra"))
        edit("d2_core.qip", lambda q: q + "set_global_assignment -name VERILOG_FILE [file join $::quartus(qip_path) extra_pll.v]\n")(d)
    neg("second-pll", "C7", second_pll)
    neg("template-core-file", "C3", edit("ga2_d2.qsf", lambda t: t + "set_global_assignment -name VERILOG_FILE core/core_top.v\n"))
    neg("signaltap-on", "C5", qsf_sub("ENABLE_SIGNALTAP OFF", "ENABLE_SIGNALTAP ON"))
    neg("wrong-device", "C5", qsf_sub("DEVICE 5CEBA4F23C8", "DEVICE 5CEBA2F23C8"))
    neg("no-synthesis-macro", "C1", qsf_sub('set_global_assignment -name VERILOG_MACRO "SYNTHESIS=1"\r\n', ""))
    neg("macro-in-qip", "C1", edit("d2_core.qip", lambda t: t + 'set_global_assignment -name VERILOG_MACRO "FOO=1"\n'))
    neg("qip-path-missing", "C2", edit("d2_core.qip", lambda t: t.replace("rtl/d2_ack_stub.sv", "rtl/d2_ack_stub_missing.sv", 1)))

    def qip_sub(a, b):
        return edit("d2_core.qip", lambda q: (_ for _ in ()).throw(AssertionError(a)) if a not in q else q.replace(a, b))
    if config == "rel":   # release/s32-v1: au3's controls re-targeted to the rel top / univ core, + univ's
        U = "U1-universal/rtl/derived/"
        neg("s2-core", "C9", qip_sub(U + "s32_core_d2_univ.sv", "D2-pocket-top/rtl/derived/s32_core_d2.sv"))
        neg("fit-core", "C9", qip_sub(U + "s32_core_d2_univ.sv", "D2-pocket-top/rtl/derived/s32_core_d2_fit.sv"))
        neg("sp-core", "C9", qip_sub(U + "s32_core_d2_univ.sv", "D2-pocket-top/rtl/derived/s32_core_d2_sp.sv"))
        neg("window-missing", "C9", edit("d2_core.qip", lambda q: "\n".join(l for l in q.splitlines() if "sp_spriteram_win" not in l) + "\n"))
        neg("upstream-sdram", "C9", qip_sub("SP-sprite-path/rtl/derived/sp_sdram.sv", "references/s32/rtl/mem/sdram.sv"))
        neg("upstream-sprite", "C9", qip_sub("D2-pocket-top/rtl/derived/s32_sprite_d2_au3.sv", "references/s32/rtl/video/s32_sprite.sv"))
        neg("r3-sprite", "C9", qip_sub("rtl/derived/s32_sprite_d2_au3.sv", "rtl/derived/s32_sprite_d2.sv"))
        neg("fit-top", "C9", qip_sub(U + "d2_core_top_rel.sv", "D2-pocket-top/rtl/d2_core_top.sv"))
        neg("au3-top", "C9", qip_sub(U + "d2_core_top_rel.sv", "D2-pocket-top/rtl/derived/d2_core_top_au3.sv"))
        neg("univ-top", "C9", qip_sub(U + "d2_core_top_rel.sv", U + "d2_core_top_univ.sv"))
        neg("wq4-queue", "C9", qip_sub("rtl/derived/d2_load_wq3.sv", "rtl/derived/d2_load_wq4.sv"))
        neg("rom-psram-missing", "C9", edit("d2_core.qip", lambda q: "\n".join(l for l in q.splitlines() if "d2_rom_psram" not in l) + "\n"))
        neg("wave-psram-missing", "C9", edit("d2_core.qip", lambda q: "\n".join(l for l in q.splitlines() if "d2_wave_psram" not in l) + "\n"))
        neg("sound-i2s-missing", "C9", edit("d2_core.qip", lambda q: "\n".join(l for l in q.splitlines() if "sound_i2s" not in l) + "\n"))
        neg("t80-vhdl-missing", "C9", edit("d2_core.qip", lambda q: "\n".join(l for l in q.splitlines() if not l.rstrip().endswith("T80s.vhd]")) + "\n"))
        neg("eeprom-variant", "C9", qip_sub("references/s32/rtl/io/s32_io.sv", "experiments/WC-sim/rtl/sim_variants/s32_io_eep_mame.sv"))
        neg("eep-readback-missing", "C9", edit("d2_core.qip", lambda q: "\n".join(l for l in q.splitlines() if "sm_eep_bridge_rd" not in l) + "\n"))
        neg("board-cfg-missing", "C9", edit("d2_core.qip", lambda q: "\n".join(l for l in q.splitlines() if "u1_board_cfg" not in l) + "\n"))
        def qip_add_r(rel_to_root):
            def go(d):
                r_ = os.path.relpath(ROOT / rel_to_root, d).replace(os.sep, "/")
                edit("d2_core.qip", lambda q: q + f"set_global_assignment -name SYSTEMVERILOG_FILE [file join $::quartus(qip_path) {r_}]\n")(d)
            return go
        neg("au3-top-also", "C9", qip_add_r("experiments/D2-pocket-top/rtl/derived/d2_core_top_au3.sv"))
        neg("sp-core-also", "C9", qip_add_r("experiments/D2-pocket-top/rtl/derived/s32_core_d2_sp.sv"))
        neg("univ-top-also", "C9", qip_add_r("experiments/U1-universal/rtl/derived/d2_core_top_univ.sv"))
        SC, UNC = "core/core_constraints.sdc", "set_clock_uncertainty -hold -add -enable_same_physical_edge -from $sys_clk -to $u1_ram_clk 0.050"
        def sdc_sub_r(a, b):
            return edit(SC, lambda s: (_ for _ in ()).throw(AssertionError(a)) if a not in s else s.replace(a, b))
        neg("rel-fragment-missing", "C4", lambda d: (d / SC).write_bytes((HERE / "d2_core_constraints.sdc").read_bytes() + (HERE / "d2_au_constraints.sdc").read_bytes()
                                                                       + (HERE / "d2_au3_constraints.sdc").read_bytes()) and None)
        neg("rel-au3-appendix-dropped", "C4", lambda d: (d / SC).write_bytes((HERE / "d2_core_constraints.sdc").read_bytes() + (HERE / "d2_au_constraints.sdc").read_bytes()
                                                                           + (ROOT / prep.UNIV_SDC_FRAGMENT).read_bytes()) and None)
        neg("rel-base-constraint-changed", "C4", edit(SC, lambda t: t.replace("-max 19.0 [get_ports {sram_dq[*]}]", "-max 9.0 [get_ports {sram_dq[*]}]", 1)))
        neg("rel-appendix-false-path", "C4", edit(SC, lambda t: t.replace("# ---- ", "set_false_path -to [get_ports {cram1_*}]\n# ---- ", 1) if False else t + "set_false_path -to [get_ports {cram1_*}]\n"))
        neg("rel-hold-value", "C17", sdc_sub_r(UNC, UNC.replace(" 0.050", " 0.150")))
        neg("rel-hold-also-setup", "C17", sdc_sub_r(UNC, UNC.replace("-hold ", "")))
        neg("rel-hold-wrong-direction", "C17", sdc_sub_r(UNC, UNC.replace("-from $sys_clk -to $u1_ram_clk", "-from $u1_ram_clk -to $sys_clk")))
        neg("rel-false-path-added", "C17", sdc_sub_r(UNC, UNC + "\n    set_false_path -from $sys_clk -to $u1_ram_clk"))
        neg("rel-fragment-run-json", "C17", edit("run.json", lambda s: re.sub(r'("sdc_univ_fragment": \{\s*"path": "[^"]+",\s*"sha256": ")(.)',
                                                                            lambda m: m.group(1) + ("1" if m.group(2) != "1" else "0"), s, count=1)))
        neg("rel-a12-tag-removed", "C12", sdc_sub_r("ASSUMPTION: A12", "NOTE: A12"))
        # Codex REL-1 r1 finding 4: the parents' remaining fragment / appendix tamper classes
        neg("rel-hold-replaces-derived", "C17", sdc_sub_r(UNC, UNC.replace("-add ", "")))
        neg("rel-hold-sign-off-too", "C17", sdc_sub_r('if {$::TimeQuestInfo(nameofexecutable) eq "quartus_fit"} {\n    set u1_ram_clk',
                                                      'if {$::TimeQuestInfo(nameofexecutable) ne "quartus_map"} {\n    set u1_ram_clk'))
        neg("rel-multicycle-added", "C17", sdc_sub_r(UNC, UNC + "\n    set_multicycle_path -hold 1 -from $sys_clk -to $u1_ram_clk"))
        neg("rel-comment-continuation", "C17", sdc_sub_r("    " + UNC, "    # note \\\n    " + UNC))
        neg("rel-au-appendix-dropped", "C4", lambda d: (d / SC).write_bytes((HERE / "d2_core_constraints.sdc").read_bytes() + (HERE / "d2_au3_constraints.sdc").read_bytes()
                                                                          + (ROOT / prep.UNIV_SDC_FRAGMENT).read_bytes()) and None)
        neg("rel-shared-sdc-changed", "C4", sdc_sub_r("-from $sys_clk -to $sys_clk 0.050", "-from $sys_clk -to $sys_clk 0.040"))
        neg("rel-ar-assumption-removed", "C12", edit(SC, lambda t: t.replace("ASSUMPTION: AR1", "NOTE: AR1")))
        neg("rel-au-assumption-removed", "C12", edit(SC, lambda t: t.replace("ASSUMPTION: AU1", "NOTE: AU1")))
    elif config in ("sp", "spid", "univ"):
        core_name = "U1-universal/rtl/derived/s32_core_d2_univ.sv" if config == "univ" else "s32_core_d2_sp.sv"
        core_rep = (lambda n: "D2-pocket-top/rtl/derived/" + n) if config == "univ" else (lambda n: n)
        neg("s2-core", "C9", qip_sub(core_name, core_rep("s32_core_d2.sv")))
        neg("fit-core", "C9", qip_sub(core_name, core_rep("s32_core_d2_fit.sv")))
        neg("window-missing", "C9", edit("d2_core.qip", lambda q: "\n".join(l for l in q.splitlines() if "sp_spriteram_win" not in l) + "\n"))
        neg("upstream-sdram", "C9", qip_sub("SP-sprite-path/rtl/derived/sp_sdram.sv", "references/s32/rtl/mem/sdram.sv"))
        neg("upstream-sprite", "C9", qip_sub("D2-pocket-top/rtl/derived/s32_sprite_d2.sv", "references/s32/rtl/video/s32_sprite.sv"))
        if config == "sp":
            neg("fit-top", "C9", qip_sub("rtl/derived/d2_core_top_sp.sv", "rtl/d2_core_top.sv"))
        elif config == "univ":
            U = "U1-universal/rtl/derived/"
            neg("fit-top", "C9", qip_sub(U + "d2_core_top_univ.sv", "D2-pocket-top/rtl/d2_core_top.sv"))
            neg("sp-top", "C9", qip_sub(U + "d2_core_top_univ.sv", "D2-pocket-top/rtl/derived/d2_core_top_sp.sv"))
            neg("spid-top", "C9", qip_sub(U + "d2_core_top_univ.sv", "SM1-spidman/rtl/derived/d2_core_top_spid.sv"))
            neg("sp-core", "C9", qip_sub(U + "s32_core_d2_univ.sv", "D2-pocket-top/rtl/derived/s32_core_d2_sp.sv"))
            neg("eeprom-variant", "C9", qip_sub("references/s32/rtl/io/s32_io.sv", "experiments/WC-sim/rtl/sim_variants/s32_io_eep_mame.sv"))
            neg("eep-readback-missing", "C9", edit("d2_core.qip", lambda q: "\n".join(l for l in q.splitlines() if "sm_eep_bridge_rd" not in l) + "\n"))
            neg("board-cfg-missing", "C9", edit("d2_core.qip", lambda q: "\n".join(l for l in q.splitlines() if "u1_board_cfg" not in l) + "\n"))
            def qip_add(rel_to_root):       # a second top / core compiled IN ADDITION to the univ one (the not-compiled list, not the once-each list)
                def go(d):
                    rel = os.path.relpath(ROOT / rel_to_root, d).replace(os.sep, "/")
                    edit("d2_core.qip", lambda q: q + f"set_global_assignment -name SYSTEMVERILOG_FILE [file join $::quartus(qip_path) {rel}]\n")(d)
                return go
            neg("sp-top-also", "C9", qip_add("experiments/D2-pocket-top/rtl/derived/d2_core_top_sp.sv"))
            neg("sp-core-also", "C9", qip_add("experiments/D2-pocket-top/rtl/derived/s32_core_d2_sp.sv"))
            neg("spid-top-also", "C9", qip_add("experiments/SM1-spidman/rtl/derived/d2_core_top_spid.sv"))
            # the univ-only SDC fragment A12 (C4 / C12 / C17): every tamper of the constraint, in the project's copy, fails
            SC, UNC = "core/core_constraints.sdc", "set_clock_uncertainty -hold -add -enable_same_physical_edge -from $sys_clk -to $u1_ram_clk 0.050"
            def sdc_sub(a, b):
                return edit(SC, lambda s: (_ for _ in ()).throw(AssertionError(a)) if a not in s else s.replace(a, b))
            def shared_only(d):            # the univ project with the shared SDC alone (returns None: no replacement pin)
                (d / SC).write_bytes((HERE / "d2_core_constraints.sdc").read_bytes())
            neg("univ-fragment-missing", "C4", shared_only)
            neg("univ-hold-value", "C17", sdc_sub(UNC, UNC.replace(" 0.050", " 0.150")))
            neg("univ-hold-also-setup", "C17", sdc_sub(UNC, UNC.replace("-hold ", "")))
            neg("univ-hold-replaces-derived", "C17", sdc_sub(UNC, UNC.replace("-add ", "")))
            neg("univ-hold-wrong-direction", "C17", sdc_sub(UNC, UNC.replace("-from $sys_clk -to $u1_ram_clk", "-from $u1_ram_clk -to $sys_clk")))
            neg("univ-hold-sign-off-too", "C17", sdc_sub('if {$::TimeQuestInfo(nameofexecutable) eq "quartus_fit"} {\n    set u1_ram_clk',
                                                         'if {$::TimeQuestInfo(nameofexecutable) ne "quartus_map"} {\n    set u1_ram_clk'))
            neg("univ-false-path-added", "C17", sdc_sub(UNC, UNC + "\n    set_false_path -from $sys_clk -to $u1_ram_clk"))
            neg("univ-multicycle-added", "C17", sdc_sub(UNC, UNC + "\n    set_multicycle_path -hold 1 -from $sys_clk -to $u1_ram_clk"))
            neg("univ-comment-continuation", "C17", sdc_sub("    " + UNC, "    # note \\\n    " + UNC))
            neg("univ-fragment-run-json", "C17", edit("run.json", lambda s: re.sub(r'("sdc_univ_fragment": \{\s*"path": "[^"]+",\s*"sha256": ")(.)',
                                                                             lambda m: m.group(1) + ("1" if m.group(2) != "1" else "0"), s, count=1)))
            neg("univ-a12-tag-removed", "C12", sdc_sub("ASSUMPTION: A12", "NOTE: A12"))
            neg("univ-shared-sdc-changed", "C4", sdc_sub("-from $sys_clk -to $sys_clk 0.050", "-from $sys_clk -to $sys_clk 0.040"))
        else:
            neg("fit-top", "C9", qip_sub("SM1-spidman/rtl/derived/d2_core_top_spid.sv", "D2-pocket-top/rtl/d2_core_top.sv"))
            neg("sp-top", "C9", qip_sub("SM1-spidman/rtl/derived/d2_core_top_spid.sv", "D2-pocket-top/rtl/derived/d2_core_top_sp.sv"))
            neg("eeprom-variant", "C9", qip_sub("references/s32/rtl/io/s32_io.sv", "experiments/WC-sim/rtl/sim_variants/s32_io_eep_mame.sv"))
            neg("eep-readback-missing", "C9", edit("d2_core.qip", lambda q: "\n".join(l for l in q.splitlines() if "sm_eep_bridge_rd" not in l) + "\n"))
    elif config in ("au", "au3"):
        neg("s2-core", "C9", qip_sub("s32_core_d2_sp.sv", "s32_core_d2.sv"))
        neg("fit-core", "C9", qip_sub("s32_core_d2_sp.sv", "s32_core_d2_fit.sv"))
        neg("window-missing", "C9", edit("d2_core.qip", lambda q: "\n".join(l for l in q.splitlines() if "sp_spriteram_win" not in l) + "\n"))
        neg("upstream-sdram", "C9", qip_sub("SP-sprite-path/rtl/derived/sp_sdram.sv", "references/s32/rtl/mem/sdram.sv"))
        neg("upstream-sprite", "C9", qip_sub("D2-pocket-top/rtl/derived/s32_sprite_d2" + ("_au3" if config == "au3" else "") + ".sv",
                                             "references/s32/rtl/video/s32_sprite.sv"))
        neg("fit-top", "C9", qip_sub("rtl/derived/d2_core_top_" + config + ".sv", "rtl/d2_core_top.sv"))
        if config == "au":   # AU negative controls (nested: the else below stays the fit branch)
            neg("sp-top", "C9", qip_sub("rtl/derived/d2_core_top_au.sv", "rtl/derived/d2_core_top_sp.sv"))
            neg("wave-psram-missing", "C9", edit("d2_core.qip", lambda q: "\n".join(l for l in q.splitlines() if "d2_wave_psram" not in l) + "\n"))
            neg("sound-i2s-missing", "C9", edit("d2_core.qip", lambda q: "\n".join(l for l in q.splitlines() if "sound_i2s" not in l) + "\n"))
            neg("t80-vhdl-missing", "C9", edit("d2_core.qip", lambda q: "\n".join(l for l in q.splitlines() if not l.rstrip().endswith("T80s.vhd]")) + "\n"))
            neg("au-appendix-dropped", "C4", lambda d: (d / "core" / "core_constraints.sdc").write_bytes((HERE / "d2_core_constraints.sdc").read_bytes()) and None)
            neg("au-base-constraint-changed", "C4", edit("core/core_constraints.sdc", lambda t: t.replace("-max 19.0 [get_ports {sram_dq[*]}]", "-max 9.0 [get_ports {sram_dq[*]}]", 1)))
            neg("au-appendix-false-path", "C4", edit("core/core_constraints.sdc", lambda t: t + "set_false_path -to [get_ports {cram0_*}]\n"))
            neg("au-assumption-removed", "C12", edit("core/core_constraints.sdc", lambda t: t.replace("ASSUMPTION: AU1", "NOTE: AU1")))
        if config == "au3":   # AU3 negative controls (nested: the else below stays the fit branch)
            neg("au-top", "C9", qip_sub("rtl/derived/d2_core_top_au3.sv", "rtl/derived/d2_core_top_au.sv"))
            neg("r3-sprite", "C9", qip_sub("rtl/derived/s32_sprite_d2_au3.sv", "rtl/derived/s32_sprite_d2.sv"))   # au3 r4: the r3 engine is refused
            neg("sp-top", "C9", qip_sub("rtl/derived/d2_core_top_au3.sv", "rtl/derived/d2_core_top_sp.sv"))
            neg("wq4-queue", "C9", qip_sub("rtl/derived/d2_load_wq3.sv", "rtl/derived/d2_load_wq4.sv"))
            neg("rom-psram-missing", "C9", edit("d2_core.qip", lambda q: "\n".join(l for l in q.splitlines() if "d2_rom_psram" not in l) + "\n"))
            neg("wave-psram-missing", "C9", edit("d2_core.qip", lambda q: "\n".join(l for l in q.splitlines() if "d2_wave_psram" not in l) + "\n"))
            neg("sound-i2s-missing", "C9", edit("d2_core.qip", lambda q: "\n".join(l for l in q.splitlines() if "sound_i2s" not in l) + "\n"))
            neg("t80-vhdl-missing", "C9", edit("d2_core.qip", lambda q: "\n".join(l for l in q.splitlines() if not l.rstrip().endswith("T80s.vhd]")) + "\n"))
            neg("au3-appendix-dropped", "C4", lambda d: (d / "core" / "core_constraints.sdc").write_bytes((HERE / "d2_core_constraints.sdc").read_bytes() + (HERE / "d2_au_constraints.sdc").read_bytes()) and None)
            neg("au-appendix-dropped-in-au3", "C4", lambda d: (d / "core" / "core_constraints.sdc").write_bytes((HERE / "d2_core_constraints.sdc").read_bytes() + (HERE / "d2_au3_constraints.sdc").read_bytes()) and None)
            neg("au3-base-constraint-changed", "C4", edit("core/core_constraints.sdc", lambda t: t.replace("-max 19.0 [get_ports {sram_dq[*]}]", "-max 9.0 [get_ports {sram_dq[*]}]", 1)))
            neg("au3-appendix-false-path", "C4", edit("core/core_constraints.sdc", lambda t: t + "set_false_path -to [get_ports {cram1_*}]\n"))
            neg("au3-ar-assumption-removed", "C12", edit("core/core_constraints.sdc", lambda t: t.replace("ASSUMPTION: AR1", "NOTE: AR1")))
            neg("au3-au-assumption-removed", "C12", edit("core/core_constraints.sdc", lambda t: t.replace("ASSUMPTION: AU1", "NOTE: AU1")))
    else:
        def s2_core(d):
            edit("d2_core.qip", lambda q: q.replace("s32_core_d2_fit.sv", "s32_core_d2.sv"))(d)
        neg("s2-core", "C9", s2_core)
        neg("standin-missing", "C9", edit("d2_core.qip", lambda q: "\n".join(l for l in q.splitlines() if "f2_xram_standin" not in l) + "\n"))
    neg("internal-wave-ram", "C9", edit("d2_core.qip", lambda q: re.sub(r"\S*s32_soundsys_d2(_au)?\.sv", "../../references/s32/rtl/audio/s32_soundsys.sv", q)))
    def sim_only(d):        # the path is rebased to the copy's depth under build/ (CI projects are build/D2/seed<N>, one level deeper than build/D2q)
        rel = os.path.relpath(D2 / "tb/d2_tb.sv", d).replace(os.sep, "/")
        edit("d2_core.qip", lambda q: q + f"set_global_assignment -name SYSTEMVERILOG_FILE [file join $::quartus(qip_path) {rel}]\n")(d)
    neg("sim-only-file", "C10", sim_only)
    if config != "univ":           # the univ-only fragment can never enter another configuration's project
        neg("univ-fragment-appended", "C4", edit("core/core_constraints.sdc", lambda s: s + (ROOT / prep.UNIV_SDC_FRAGMENT).read_text()))
    neg("assumption-tag-removed", "C12", edit("core/core_constraints.sdc", lambda t: t.replace("ASSUMPTION: A6", "NOTE: A6")))
    neg("pll-changed-one-comment", "C14", pll_copy(lambda t: t + "// edited\n"))
    neg("seed-not-allowed", "C16", edit("run.json", lambda t: re.sub(r'"seed":\s*\d+', '"seed": 9', t, count=1)))
    neg("run-json-hash", "C11", edit("run.json", lambda t: t.replace(f'"config": "{config}"', '"config": "s2"', 1)))
    if pin is not None:
        def pin_variant(mut):
            def go(d):
                pj = json.loads(Path(pin).read_text())
                mut(pj)
                q = d.parent / (d.name + ".pin.json")
                q.write_text(json.dumps(pj, indent=1, sort_keys=True) + "\n")
                return q
            return go
        flip = lambda h: ("1" if h[0] != "1" else "0") + h[1:]
        # the pin is part of the expectation: a different pin (or a damaged one) must fail the SAME project, in C8 and C11
        neg("pin-wrong-image-hash", "C8", pin_variant(lambda pj: pj["images_sha256"].__setitem__("m1_mode.hex", flip(pj["images_sha256"]["m1_mode.hex"]))))
        neg("pin-v25-differs-from-baseline", "C8", pin_variant(lambda pj: pj.__setitem__("v25_microcode_bin_sha256", flip(pj["v25_microcode_bin_sha256"]))))
        neg("pin-builder-sha-differs", "C11", pin_variant(lambda pj: pj.__setitem__("builder_sha256", flip(pj["builder_sha256"]))))
        neg("pin-missing", "C8", lambda d: d.parent / (d.name + ".no-such-pin.json"))
        neg("pin-extra-key", "C8", pin_variant(lambda pj: pj.__setitem__("note", "x")))
        neg("run-json-pin-sha", "C11", edit("run.json", lambda t: re.sub(r'("pin_sha256": ")(.)', lambda m: m.group(1) + ("1" if m.group(2) != "1" else "0"), t, count=1)))
        if "args" in json.loads(Path(pin).read_text()):
            neg("run-json-builder-args-dropped", "C11", edit("run.json", lambda t: re.sub(r'\s*"builder_args": \[[^\]]*\],?', "", t, count=1)))
            neg("run-json-builder-args-unpacked", "C11", edit("run.json", lambda t: re.sub(r',\s*"--mc-pack=[^"]*"', "", t, count=1)))
            neg("pin-args-unpacked", "C11", pin_variant(lambda pj: pj.update(args=["--reset-native"], command="m1_m11_microcode.py <out> --reset-native")))
            neg("pin-args-unknown", "C8", pin_variant(lambda pj: pj.update(args=["--reset-native", "--mc-pack=tail"], command="m1_m11_microcode.py <out> --reset-native --mc-pack=tail")))
        neg("run-json-no-pin", "C11", edit("run.json", lambda t: re.sub(r'\s*"pin_sha256": "[0-9a-f]{64}",?', "", t, count=1)))
    else:
        neg("run-json-pin-recorded-without-flag", "C11", edit("run.json", lambda t: t.replace('"v25_microcode_bin_sha256"', '"pin_sha256": "' + "0" * 64 + '",\n      "v25_microcode_bin_sha256"', 1)))

    bad = 0
    for name, expect, fn in tampers:
        d = proj.parent / f"{proj.name}_neg_{tag}_{name}"
        if d.exists():
            print(f"FAIL  negative control {name}: {d} exists (use another --neg-tag)")
            bad += 1
            continue
        shutil.copytree(proj, d)
        try:
            alt = fn(d)
        except AssertionError as e:
            print(f"FAIL  negative control {name}: tamper did not apply ({e})")
            bad += 1
            continue
        res = run_checks(d, tpl, seed, alt if alt is not None else pin, config)
        failed = sorted({cid for cid, ok, _ in res if not ok})
        good = expect in failed
        print(("ok    " if good else "FAIL  ") + f"negative control {name}: expected {expect} to fail; failing checks {failed}")
        bad += not good
    print(f"\nNEGATIVE CONTROLS {'PASS' if not bad else 'FAIL'} ({len(tampers)} tampered copies, {bad} not detected)")
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project", type=Path)
    ap.add_argument("--template", type=Path, required=True)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--neg-tag", default="1")
    ap.add_argument("--seed", type=int, help="the request seed (checked against ci_request.py's allowed set and run.json)")
    ap.add_argument("--microcode-pin", type=Path, help="expected microcode hashes from this pin (tools/microcode_pin.py) instead of BASELINE_S2.md section 9")
    ap.add_argument("--config", choices=("fit", "sp", "spid", "univ", "au", "au3", "rel"), default="fit", help="the configuration the project must be (default fit)")
    a = ap.parse_args()
    if a.selftest:
        return selftest(a.project, a.template, a.neg_tag, a.seed, a.microcode_pin, a.config)
    n = report(run_checks(a.project, a.template, a.seed, a.microcode_pin, a.config))
    print(f"\nD2 PROJECT CHECK {'PASS' if not n else 'FAIL (' + str(n) + ')'}")
    return 1 if n else 0


if __name__ == "__main__":
    sys.exit(main())
