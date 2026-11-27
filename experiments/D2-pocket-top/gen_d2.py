#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""D2 Pocket top: source lists. Writes into <fresh-out-dir>:
  files_synth.f   the SYNTHESIZABLE set (what a Quartus project would compile; core_bridge_cmd.v / common.v from the core
                  template, mf_pll_ga2.v the real altera_pll instance; Intel primitives dcfifo / altera_pll / altddio_out /
                  altsyncram come from Quartus' libraries)
  files_sim.f     the Verilator set: files_synth.f minus rtl/mf_pll_ga2.v, plus tb/sim_models/* (PLL, DDIO, data table),
                  the S2 dcfifo stand-in, the V2 observer/checks and their binds, the WC-sim watchdog and tb/*.sv
  flags.txt       Verilator defines/includes (SIMULATION + upstream's VERILOG_MACROs, as gen_p1.py)
  d2_core.qip     DRAFT QIP of the synthesizable set (paths relative to this experiment, flat assignments), for the D2 fit
                  plan; not used by any build
  files_synth_vhdl.f  upstream VHDL in the build (the T80 Z80)
  files_lint_synth.f / flags_synth.txt  synthesis-mode Verilator lint (SYNTHESIS=1, no SIMULATION; black boxes)
  sources.json    provenance
Upstream filter as experiments/P1-pocket-top/gen_p1.py (E1's qsf expansion; MiSTer-only files out; upstream s32_v60.sv out);
s32_core.sv -> rtl/derived/s32_core_d2.sv, (fit, sp) s32_sprite.sv -> rtl/derived/s32_sprite_d2.sv, s32_io.sv -> the WC-sim MAME-EEPROM variant (as S2), + rtl/derived/wc_v25_stub_hw.sv.
sp (GA2Play5 freeze readout): V2's v2_core.sv -> rtl/derived/v2_core.sv, rtl/d2_v60_hw.sv -> rtl/derived/d2_v60_hw.sv (both + the
dbg_v2 observation output, module names kept), + rtl/d2_freeze_readout.sv. The fit and s2 lists are unchanged.
au (exp/audio1, experiments/AUDIO/PLAN.md): the sp list with the sound subsystem enabled: rtl/derived/d2_core_top_au.sv,
d2_load_wq4.sv, d2_slot_verify4.sv, d2_diag_band_au.sv INSTEAD of the sp top / d2_load_wq3 / d2_slot_verify3 / d2_diag_band, + rtl/d2_wave_psram.sv
and rtl/third_party/{sync_fifo,sound_i2s}.sv. Simulation only: --t80-sim <t80s_ghdl.v> adds the GHDL netlist of upstream's T80s and
S32_REAL_Z80_SIM to the Verilator set (the Quartus set always compiles upstream's VHDL); without it the sim uses upstream's Z80 stub.
The fit / s2 / sp lists are unchanged by the au option (byte-identical outputs).
au3 (exp/audio3, GA2Audio3 = option B): the au list with the Z80 sound ROM in PSRAM chip 1: rtl/derived/d2_core_top_au3.sv and
d2_diag_band_au3.sv instead of the au top / band, the sp write queue and read-back (d2_load_wq3.sv, d2_slot_verify3.sv) instead of
d2_load_wq4 / d2_slot_verify4, + rtl/d2_rom_psram.sv; --t80-sim as for au. The fit / s2 / sp / au lists are unchanged by it.
au3 r4 (sprite timing): rtl/derived/s32_sprite_d2_au3.sv (s32_sprite_d2.sv + the r4 row-byte-base patch) instead of s32_sprite_d2.sv;
au3 only (fit / sp / au still compile s32_sprite_d2.sv).
Usage: gen_d2.py <fresh-out-dir> --template <core-template>/src/fpga [--config fit|s2|sp|au|au3] [--t80-sim FILE (au / au3 only)]"""
import argparse, hashlib, json, os, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
P1 = ROOT / "experiments/P1-pocket-top"
WC = ROOT / "experiments/WC-sim"
V2R = ROOT / "experiments/V2-datapath-floor/rtl"
M1TB = ROOT / "experiments/M1-functional/tb"
sys.path.insert(0, str(ROOT / "experiments/E1-baseline")); sys.path.insert(0, str(WC))
import gen_project as e1  # noqa: E402
import lint  # noqa: E402
from gen_wc_v2 import check_pin_tolerant  # noqa: E402

REPLACED = {"rtl/cpu/v60/s32_v60.sv"}
MISTER_ONLY = {"rtl/mem/s32_fb_if.sv", "rtl/mem/s32_rom_loader.sv"}

def vhdl_files(qsf, base, variables=None):
    """The VHDL_FILE entries reachable from a QSF/QIP (lint.expand collects only (System)Verilog)."""
    out, variables = [], dict(variables or {})
    for raw in Path(qsf).read_text().splitlines():
        m = lint.ASSIGN.match(raw.strip())
        if not m: continue
        variables["$::quartus(qip_path)"] = base
        path = lint.resolve(m.group(2), base, variables)
        if not path.is_absolute(): path = (base / path).resolve()
        if m.group(1) == "QIP_FILE": out += vhdl_files(path, path.parent, variables)
        elif m.group(1) == "VHDL_FILE": out.append(path)
    return out

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("out"); ap.add_argument("--template", required=True)
    ap.add_argument("--config", choices=("fit", "s2", "sp", "au", "au3", "univ", "rel"), default="fit",
                    help="fit: F2's sprite-RAM stand-in + external wave RAM (the plan's first-pixels core); s2: the S2 core (full sprite RAM); "
                         "sp: fit + the SP sprite path (sprite-RAM window, sp_sdram, sp_fb_sdram, slot 3; rtl/derived/d2_core_top_sp.sv); "
                         "au: sp + sound (experiments/AUDIO/PLAN.md; rtl/derived/d2_core_top_au.sv); "
                         "au3: au with the Z80 sound ROM in PSRAM chip 1 (rtl/derived/d2_core_top_au3.sv, rtl/d2_rom_psram.sv); "
                         "univ (U1): sp with the universal top/core (experiments/U1-universal: run-time board configuration slot, EEPROM "
                         "persistence, V25 stub gated by has_v25) and upstream s32_io.sv (UNIV_EEP_MODEL=mame: the WC-sim variant, diagnostic only); "
                         "rel (release/s32-v1): au3 with the universal top/core (experiments/U1-universal/rtl/derived/d2_core_top_rel.sv) and upstream s32_io.sv")
    ap.add_argument("--t80-sim", help="au / au3 / rel only, SIMULATION list only: the GHDL Verilog netlist of upstream T80s (never synthesised)")
    a = ap.parse_args()
    if a.t80_sim and a.config not in ("au", "au3", "rel"):
        ap.error("--t80-sim is for --config au / au3 / rel only")
    sp_like = a.config in ("sp", "au", "au3", "univ", "rel")   # au = sp + sound: every sp substitution applies (au3 = au with the ROM in PSRAM)
    au_like = a.config in ("au", "au3", "rel")                 # rel = au3 + the universal top / core
    u1_like = a.config in ("univ", "rel")
    U1 = ROOT / "experiments/U1-universal"
    out = Path(a.out).resolve(); out.mkdir(parents=True, exist_ok=False)
    T = Path(a.template).resolve()
    for f in ("core/core_bridge_cmd.v", "apf/common.v", "apf/apf_top.v", "ap_core.qsf"):
        assert (T / f).is_file(), T / f
    check_pin_tolerant()
    kept, _d, qsf_sources = e1.upstream_assignments()
    sources, _ds = e1.upstream_sources(qsf_sources)
    sources = [s for s in sources if s[1] not in REPLACED | MISTER_ONLY and s[0] != "SDC_FILE"]
    q = [f'set_global_assignment -name {n} "{(e1.S32 / v).as_posix()}"' for n, v in sources]
    q += [f"set_global_assignment -name {n} {v}" for n, v in kept if n == "VERILOG_MACRO"]
    (out / "upstream.qsf").write_text("\n".join(q) + "\n")
    files, incdirs = [], []
    lint.expand(out / "upstream.qsf", out, files, incdirs)
    vhdl = vhdl_files(out / "upstream.qsf", out)          # Quartus compiles these (T80); Verilator uses upstream's Z80 stub
    files = [f for f in files if f.exists()]
    core = ROOT / "references/s32/rtl/s32_core.sv"; io = ROOT / "references/s32/rtl/io/s32_io.sv"
    assert core in files and io in files and ROOT / "references/s32/rtl/mem/sdram.sv" in files
    snd = ROOT / "references/s32/rtl/audio/s32_soundsys.sv"; assert snd in files
    dcore = HERE / {"fit": "rtl/derived/s32_core_d2_fit.sv", "s2": "rtl/derived/s32_core_d2.sv", "sp": "rtl/derived/s32_core_d2_sp.sv",
                    "au": "rtl/derived/s32_core_d2_sp.sv", "au3": "rtl/derived/s32_core_d2_sp.sv",
                    "univ": "../U1-universal/rtl/derived/s32_core_d2_univ.sv", "rel": "../U1-universal/rtl/derived/s32_core_d2_univ.sv"}[a.config]
    dsnd = (HERE / "rtl/derived/s32_soundsys_d2_au.sv" if au_like else          # au / au3 / rel: + the mixer-input registers (timing)
            HERE / "rtl/derived/s32_soundsys_d2.sv" if a.config in ("fit", "sp", "univ") else snd)
    spr = ROOT / "references/s32/rtl/video/s32_sprite.sv"; assert spr in files
    dspr = (HERE / "rtl/derived/s32_sprite_d2_au3.sv" if a.config in ("au3", "rel") else          # au3 / rel: the r3 engine + the r4 patch
            HERE / "rtl/derived/s32_sprite_d2.sv" if a.config in ("fit", "sp", "au", "univ") else spr)   # fit / sp / au / univ: the r3 derived sprite engine
    SP = ROOT / "experiments/SP-sprite-path/rtl"
    usdram = ROOT / "references/s32/rtl/mem/sdram.sv"
    # U1: univ / rel keep the pinned upstream s32_io.sv (93C46 datasheet behaviour, as SpidPlay1); UNIV_EEP_MODEL=mame = the WC-sim variant
    # (diagnostic only: isolates the plumbing from the EEPROM model in the GA2 equivalence check). Every other configuration unchanged.
    eep_upstream = u1_like and os.environ.get("UNIV_EEP_MODEL") != "mame"
    files = [dcore if f == core else (io if eep_upstream else WC / "rtl/sim_variants/s32_io_eep_mame.sv") if f == io else dsnd if f == snd else
             dspr if f == spr else (SP / "derived/sp_sdram.sv") if (f == usdram and sp_like) else f
             for f in files]
    files += [HERE / "rtl/derived/wc_v25_stub_hw.sv"]
    if a.config == "fit": files += [ROOT / "experiments/F2-fullchip/rtl/f2_xram_standin.sv"]
    if sp_like: files += [SP / "sp_fb_sdram.sv", SP / "sp_spriteram_win.sv"]
    wq = HERE / {"sp": "rtl/derived/d2_load_wq3.sv", "au": "rtl/derived/d2_load_wq4.sv", "au3": "rtl/derived/d2_load_wq3.sv",
                 "univ": "rtl/derived/d2_load_wq3.sv", "rel": "rtl/derived/d2_load_wq3.sv"}[a.config] if sp_like else P1 / "rtl/p1_load_wq.sv"
    topf = ({"univ": U1 / "rtl/derived/d2_core_top_univ.sv", "rel": U1 / "rtl/derived/d2_core_top_rel.sv"}.get(a.config) or
            HERE / {"sp": "rtl/derived/d2_core_top_sp.sv", "au": "rtl/derived/d2_core_top_au.sv", "au3": "rtl/derived/d2_core_top_au3.sv"}[a.config]) if sp_like else HERE / "rtl/d2_core_top.sv"
    verify = HERE / {"sp": "rtl/derived/d2_slot_verify3.sv", "au": "rtl/derived/d2_slot_verify4.sv", "au3": "rtl/derived/d2_slot_verify3.sv",
                     "univ": "rtl/derived/d2_slot_verify3.sv", "rel": "rtl/derived/d2_slot_verify3.sv"}[a.config] if sp_like else HERE / "rtl/d2_slot_verify.sv"
    band = HERE / {"au": "rtl/derived/d2_diag_band_au.sv", "au3": "rtl/derived/d2_diag_band_au3.sv", "rel": "rtl/derived/d2_diag_band_au3.sv"}.get(a.config, "rtl/d2_diag_band.sv")
    au_extra = ([HERE / "rtl/d2_wave_psram.sv"] + ([HERE / "rtl/d2_rom_psram.sv"] if a.config in ("au3", "rel") else [])
                + [HERE / "rtl/third_party/sync_fifo.sv", HERE / "rtl/third_party/sound_i2s.sv"]) if au_like else []
    v2 = [V2R / f for f in (V2R / "files.f").read_text().split()]
    # GA2Play5 (sp only): the freeze readout's derived v2_core / s32_v60 (module names kept, + the dbg_v2 observation output)
    # replace the V2 tree's v2_core.sv and rtl/d2_v60_hw.sv; rtl/d2_freeze_readout.sv is added. fit / s2 are unchanged.
    if sp_like:
        v2 = [HERE / "rtl/derived/v2_core.sv" if f == V2R / "v2_core.sv" else f for f in v2]
    synth = files + v2 + [
        HERE / ("rtl/derived/d2_v60_hw.sv" if sp_like else "rtl/d2_v60_hw.sv"),
        T / "apf/common.v", T / "core/core_bridge_cmd.v",
        P1 / "rtl/third_party/data_loader.sv", wq, verify,
        P1 / "rtl/ga2_video.sv",
        HERE / "rtl/derived/d2_sram_port.sv", HERE / "rtl/d2_ack_stub.sv", band] + (
        [HERE / "rtl/d2_freeze_readout.sv"] if sp_like else []) + au_extra + [
        HERE / "rtl/mf_pll_ga2.v", topf]
    if u1_like:   # U1: Spider-Man's Save-slot read-back and the configuration register file, before the top
        synth[synth.index(topf):synth.index(topf)] = [ROOT / "experiments/SM1-spidman/rtl/sm_eep_bridge_rd.sv", U1 / "rtl/u1_board_cfg.sv",
                                                      U1 / "rtl/u1_wram_pr.sv", U1 / "rtl/u1_hiscore.sv"] + (
                                                      [U1 / "rtl/u1_video.sv", U1 / "rtl/u1_save_rd.sv"] if a.config == "rel" else [])   # SN: the work RAM's protection port; SN2: rel's video bridge
    # au: sync_fifo.sv names every dcfifo port; tb/sim_models/dcfifo_au_sim.sv = the P1 model + those ports (logic unchanged)
    dcf_sim = HERE / "tb/sim_models/dcfifo_au_sim.sv" if au_like else P1 / "rtl/sim/dcfifo_sim.sv"
    sim = [f for f in synth if f != HERE / "rtl/mf_pll_ga2.v"] + [
        HERE / "tb/sim_models/mf_pll_ga2_sim.v", HERE / "tb/sim_models/altddio_out_sim.v", HERE / "tb/sim_models/mf_datatable_sim.v",
        dcf_sim,
        M1TB / "v2_sim_observer.sv", M1TB / "v2_sim_checks.sv", WC / "rtl/wc_watchdog.sv",
        HERE / "tb/d2_v2_binds.sv", HERE / "tb/d2_v2_monitor.sv"] + ([HERE / "tb/d2_au_monitor.sv"] if au_like else []) + [
        HERE / "tb/d2_tb.sv"]
    t80_sim = Path(a.t80_sim).resolve() if a.t80_sim else None
    if t80_sim is not None:                   # au simulation: the GHDL T80s netlist before everything (module T80s)
        sim = [t80_sim] + sim
    for f in synth + sim: assert f.is_file(), f
    (out / "files_synth.f").write_text("\n".join(str(f) for f in synth) + "\n")
    (out / "files_synth_vhdl.f").write_text("\n".join(str(f) for f in vhdl) + "\n")
    # synthesis-mode lint list: files_synth.f + the template's mf_datatable.v + lint-only black boxes / primitive stand-ins
    (out / "files_lint_synth.f").write_text("\n".join(str(f) for f in synth + [T / "apf/mf_datatable.v",
        HERE / "tb/lint_stubs/intel_prims_lint.v", dcf_sim, HERE / "tb/sim_models/altddio_out_sim.v"]) + "\n")
    (out / "flags_synth.txt").write_text("\n".join([f"+define+{m}" for m in ["SYNTHESIS=1"] + lint.macros(out / "upstream.qsf")]
        + [f"-I{d}" for d in incdirs] + [f"-I{V2R}"]) + "\n")
    (out / "files_sim.f").write_text("\n".join(str(f) for f in sim) + "\n")
    macros = lint.macros(out / "upstream.qsf")
    flags = [f"+define+{m}" for m in ["SIMULATION"] + macros + (["D2_SP"] if sp_like else []) + (["D2_AU"] if au_like else [])
             + (["D2_AU3"] if a.config in ("au3", "rel") else []) + (["U1_UNIV"] if u1_like else []) + (["S32_REL"] if a.config == "rel" else [])
             + (["S32_REAL_Z80_SIM"] if t80_sim is not None else [])] + [f"-I{d}" for d in incdirs] + [f"-I{V2R}"]
    (out / "flags.txt").write_text("\n".join(flags) + "\n")
    # draft QIP: experiment-relative paths; template files are copied into the project by the (future) prepare step
    def rel(f):
        f = Path(f)
        if str(f).startswith(str(T)): return None
        return Path(os.path.relpath(f, HERE)).as_posix()
    lines = ["# SPDX-License-Identifier: GPL-3.0-or-later",
             "# DRAFT, generated by experiments/D2-pocket-top/gen_d2.py: the D2 synthesizable set. NOT USED BY ANY BUILD.",
             "# Template files (apf/*, core_bridge_cmd.v) come from the pinned core template; VERILOG_MACROs and SEARCH_PATHs",
             "# must be QSF assignments (Quartus rejects VERILOG_MACRO in a QIP, run 40262881111):"]
    lines += [f"#   VERILOG_MACRO \"{m}\"" for m in ["SYNTHESIS=1"] + macros]
    lines += [f"#   SEARCH_PATH {Path(os.path.relpath(d, HERE)).as_posix()}" for d in list(incdirs) + [V2R]]
    for f in vhdl:
        lines.append(f"set_global_assignment -name VHDL_FILE [file join $::quartus(qip_path) {rel(f)}]")
    for f in synth:
        r = rel(f)
        if r is None: continue
        kind = "VERILOG_FILE" if r.endswith(".v") else "SYSTEMVERILOG_FILE"
        lines.append(f"set_global_assignment -name {kind} [file join $::quartus(qip_path) {r}]")
    (out / "d2_core.qip").write_text("\n".join(lines) + "\n")
    tsha = {f: hashlib.sha256((T / f).read_bytes()).hexdigest() for f in ("core/core_bridge_cmd.v", "apf/common.v", "apf/apf_top.v", "ap_core.qsf")}
    (out / "sources.json").write_text(json.dumps({"upstream_s32": e1.sh("git", "-C", str(e1.S32), "rev-parse", "HEAD"),
        "n_synth": len(synth), "n_sim": len(sim), "macros": macros, "template": str(T), "template_sha256": tsha,
        "config": a.config, "core": str(dcore.relative_to(HERE)), "soundsys": str(dsnd), "sprite": str(dspr),
        "eeprom_model": "upstream (s32_io.sv)" if eep_upstream else "mame (WC-sim s32_io_eep_mame.sv)",
        **({"t80_sim": str(t80_sim) if t80_sim else None} if au_like else {})}, indent=2))
    print(f"config {a.config}: {len(synth)} synth / {len(sim)} sim files -> {out}")

if __name__ == "__main__":
    main()
