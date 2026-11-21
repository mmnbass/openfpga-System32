#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""P1 Pocket-top sim (stage S2): Verilator source list. Same upstream filter as experiments/WC-sim/gen_wc_v2.py (E1's qsf
expansion; MiSTer-only files out; upstream s32_v60.sv out) with these differences:
  * upstream rtl/mem/sdram.sv is IN (the real SDRAM controller); s32_fb_if.sv and s32_rom_loader.sv stay out;
  * s32_core.sv is replaced by the derived rtl/sim_variants/s32_core_p1.sv (WC-sim V25 stub + external work RAM);
  * s32_io.sv is replaced by the WC-sim MAME-EEPROM variant (as the milestone), unless 'upstream' is given;
  * the top is rtl/p1_top.sv (module wc_top) with p1_sram_port, p1_load_wq, p1_slot_verify, ga2_video, the verbatim
    data_loader.sv and the simulation-only dcfifo stand-in;
  * the CPU slot is the unmodified WC-sim wrapper (experiments/WC-sim/rtl/wc_v2_shim.sv) + V2 files.f + M1 observer/checks.
Usage: gen_p1.py <fresh-out-dir> [mame|upstream]"""
import json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WC = ROOT / "experiments" / "WC-sim"
sys.path.insert(0, str(ROOT / "experiments" / "E1-baseline"))
sys.path.insert(0, str(WC))
import gen_project as e1  # noqa: E402
import lint  # noqa: E402
from gen_wc_v2 import check_pin_tolerant  # noqa: E402

REPLACED = {"rtl/cpu/v60/s32_v60.sv"}
MISTER_ONLY = {"rtl/mem/s32_fb_if.sv", "rtl/mem/s32_rom_loader.sv"}
V2R = ROOT / "experiments" / "V2-datapath-floor" / "rtl"
M1TB = ROOT / "experiments" / "M1-functional" / "tb"

def main():
    out = Path(sys.argv[1]).resolve(); out.mkdir(parents=True, exist_ok=False)
    check_pin_tolerant()
    kept, _d, qsf_sources = e1.upstream_assignments()
    sources, _ds = e1.upstream_sources(qsf_sources)
    sources = [s for s in sources if s[1] not in REPLACED | MISTER_ONLY and s[0] != "SDC_FILE"]
    q = [f'set_global_assignment -name {n} "{(e1.S32 / v).as_posix()}"' for n, v in sources]
    q += [f"set_global_assignment -name {n} {v}" for n, v in kept if n == "VERILOG_MACRO"]
    (out / "p1.qsf").write_text("\n".join(q) + "\n")
    files, incdirs = [], []
    lint.expand(out / "p1.qsf", out, files, incdirs)
    files = [f for f in files if f.exists()]
    sd = ROOT / "references/s32/rtl/mem/sdram.sv"
    assert sd in files, "upstream sdram.sv not in the list"
    core = ROOT / "references/s32/rtl/s32_core.sv"
    assert core in files, "upstream s32_core.sv not in the list"
    files = [HERE / "rtl/sim_variants/s32_core_p1.sv" if f == core else f for f in files] + [WC / "rtl/wc_v25_stub.sv"]
    model = sys.argv[2] if len(sys.argv) > 2 else "mame"
    assert model in ("mame", "upstream"), model
    if model == "mame":
        io = ROOT / "references/s32/rtl/io/s32_io.sv"
        assert io in files, "upstream s32_io.sv not in the list"
        files = [WC / "rtl/sim_variants/s32_io_eep_mame.sv" if f == io else f for f in files]
    macros = lint.macros(out / "p1.qsf")
    v2 = [V2R / f for f in (V2R / "files.f").read_text().split()]
    files += v2 + [M1TB / "v2_sim_observer.sv", M1TB / "v2_sim_checks.sv", WC / "rtl/wc_watchdog.sv", WC / "rtl/wc_v2_shim.sv",
                   HERE / "rtl/sim/dcfifo_sim.sv", HERE / "rtl/third_party/data_loader.sv",
                   HERE / "rtl/p1_sram_port.sv", HERE / "rtl/p1_load_wq.sv", HERE / "rtl/p1_slot_verify.sv",
                   HERE / "rtl/ga2_video.sv", HERE / "rtl/p1_top.sv"]
    (out / "files.f").write_text("\n".join(str(f) for f in files) + "\n")
    flags = [f"+define+{m}" for m in ["SIMULATION"] + macros] + [f"-I{d}" for d in incdirs] + [f"-I{V2R}"]
    (out / "flags.txt").write_text("\n".join(flags) + "\n")
    (out / "sources.json").write_text(json.dumps({"upstream_s32": e1.sh("git", "-C", str(e1.S32), "rev-parse", "HEAD"),
        "n_files": len(files), "macros": macros, "cpu": "wc_v2_shim + V2 files.f", "replaced": sorted(REPLACED),
        "core": "rtl/sim_variants/s32_core_p1.sv", "eeprom_model": model}, indent=2))
    print(f"{len(files)} files -> {out}")

if __name__ == "__main__":
    main()
