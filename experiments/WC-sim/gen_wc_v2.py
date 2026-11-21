#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""WC-sim wc_v2: Verilator source list for the whole-chip sim with the REAL V2 CPU.
Like gen_wc.py (E1's upstream filter; s32_core and s32_soundsys UNMODIFIED; MiSTer-only files out;
upstream s32_v60.sv out) but the CPU slot is rtl/wc_v2_shim.sv + V2's production RTL
(experiments/V2-datapath-floor/rtl/files.f) + M1's sim-only observer/checks (read-only import).
Usage: gen_wc_v2.py <fresh-out-dir> [mame|upstream] [stub|upstream]   (EEPROM model; V25: stub = sim-only s32_core variant routing 0xA00100-0xA0015E reads to wc_v25_stub)"""
import json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "experiments" / "E1-baseline"))
import gen_project as e1  # noqa: E402
import lint  # noqa: E402

REPLACED = {"rtl/cpu/v60/s32_v60.sv"}
MISTER_ONLY = {"rtl/mem/sdram.sv", "rtl/mem/s32_fb_if.sv", "rtl/mem/s32_rom_loader.sv"}
V2R = ROOT / "experiments" / "V2-datapath-floor" / "rtl"
M1TB = ROOT / "experiments" / "M1-functional" / "tb"

STRAY = "?? rtl/cpu/v25/s80x86/generated/jt12.raw"   # left by WC-sim's first runs; the owner decides its fate

def check_pin_tolerant():
    """tools/check_upstream.py, except that the one known stray untracked file (never deleted here) is tolerated."""
    import subprocess
    rc = subprocess.run([sys.executable, "-B", str(ROOT / "tools" / "check_upstream.py")], capture_output=True, text=True)
    out = (rc.stdout + rc.stderr).strip()
    if rc.returncode == 0:
        print(out); return
    lines = [l for l in out.splitlines() if l.strip() and not l.startswith("FAIL s32: working tree modified")]
    if lines == [STRAY]:
        print("upstream pin OK except the known stray untracked file:", STRAY); return
    sys.exit("upstream pin check failed:\n" + out)

def main():
    out = Path(sys.argv[1]).resolve(); out.mkdir(parents=True, exist_ok=False)
    check_pin_tolerant()
    kept, _d, qsf_sources = e1.upstream_assignments()
    sources, _ds = e1.upstream_sources(qsf_sources)
    sources = [s for s in sources if s[1] not in REPLACED | MISTER_ONLY and s[0] != "SDC_FILE"]
    q = [f'set_global_assignment -name {n} "{(e1.S32 / v).as_posix()}"' for n, v in sources]
    q += [f"set_global_assignment -name {n} {v}" for n, v in kept if n == "VERILOG_MACRO"]
    (out / "wc.qsf").write_text("\n".join(q) + "\n")
    files, incdirs = [], []
    lint.expand(out / "wc.qsf", out, files, incdirs)
    files = [f for f in files if f.exists()]
    model = sys.argv[2] if len(sys.argv) > 2 else "mame"
    assert model in ("mame", "upstream"), model
    v25 = sys.argv[3] if len(sys.argv) > 3 else "stub"
    assert v25 in ("stub", "upstream"), v25
    if v25 == "stub":
        core = ROOT / "references/s32/rtl/s32_core.sv"
        assert core in files, "upstream s32_core.sv not in the list"
        files = [HERE / "rtl/sim_variants/s32_core_v25stub.sv" if f == core else f for f in files] + [HERE / "rtl/wc_v25_stub.sv"]
    if model == "mame":
        io = ROOT / "references/s32/rtl/io/s32_io.sv"
        assert io in files, "upstream s32_io.sv not in the list"
        files = [HERE / "rtl/sim_variants/s32_io_eep_mame.sv" if f == io else f for f in files]
    macros = lint.macros(out / "wc.qsf")
    v2 = [V2R / f for f in (V2R / "files.f").read_text().split()]
    files += v2 + [M1TB / "v2_sim_observer.sv", M1TB / "v2_sim_checks.sv",
                   HERE / "rtl" / "wc_watchdog.sv", HERE / "rtl" / "wc_v2_shim.sv", HERE / "rtl" / "wc_top_v2.sv"]
    (out / "files.f").write_text("\n".join(str(f) for f in files) + "\n")
    flags = [f"+define+{m}" for m in ["SIMULATION"] + macros] + [f"-I{d}" for d in incdirs] + [f"-I{V2R}"]
    (out / "flags.txt").write_text("\n".join(flags) + "\n")
    (out / "sources.json").write_text(json.dumps({"upstream_s32": e1.sh("git", "-C", str(e1.S32), "rev-parse", "HEAD"),
        "n_files": len(files), "macros": macros, "cpu": "wc_v2_shim + V2 files.f", "replaced": sorted(REPLACED)}, indent=2))
    print(f"{len(files)} files -> {out}")

if __name__ == "__main__":
    main()
