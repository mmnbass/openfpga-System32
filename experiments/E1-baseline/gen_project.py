#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Generate an E1 Quartus project from the pinned upstream s32 sources.

The QSF is derived, not hand-written:
  * compiler settings and Verilog macros come from upstream
    Arcade-SegaSystem32.qsf, minus the MiSTer-framework (MISTER_*) macros and
    per-build bookkeeping (seed, output dir, RBF generation, ...);
  * the source list comes from upstream files.qip plus the sources the
    upstream QSF adds itself, minus the MiSTer framework (sys/), the MiSTer
    PLL and the MiSTer top-level emu file;
  * the E1 harness, the selected measurement top and the E1 SDC are added.

Usage:
  gen_project.py --config vp|sr --seed N [--device 5CSEBA6U23I7] [--out DIR]
                 [--lint-filelist FILE]

Writes <out>/ga2_e1.qpf, ga2_e1.qsf and run.json. Refuses to run if the
submodule is not at its UPSTREAM.lock commit or has local changes.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
S32 = ROOT / "references" / "s32"

UPSTREAM_DEVICE = "5CSEBA6U23I7"          # references/s32/sys/sys.tcl
TOPS = {"vp": "ga2_e1_vp_top", "sr": "ga2_e1_sr_top"}
N_IN, N_OUT = 664, 361

# Upstream QSF assignments that describe one MiSTer build rather than the
# compiler's treatment of the design. Everything else is copied verbatim.
DROP_NAMES = {
    "TOP_LEVEL_ENTITY", "LAST_QUARTUS_VERSION", "GENERATE_RBF_FILE",
    "COMPRESSION_MODE", "PROJECT_OUTPUT_DIRECTORY", "SMART_RECOMPILE",
    "SEED", "NUM_PARALLEL_PROCESSORS", "SAVE_DISK_SPACE",
    # sources and constraints are handled explicitly below
    "SYSTEMVERILOG_FILE", "VERILOG_FILE", "VHDL_FILE", "QIP_FILE", "SDC_FILE",
}
# Upstream sources that belong to the MiSTer framework or its top level.
DROP_SOURCES = {
    "sys/sys.qip",                 # MiSTer framework (sys_top, ascal, OSD, hps_io, ...)
    "rtl/pll/pll.qip",             # MiSTer 50 MHz PLL; E1 clocks are top-level ports
    "Arcade-SegaSystem32.sv",      # MiSTer emu top (loader, sdram, fb_if, OSD glue)
    "Arcade-SegaSystem32.sdc",     # replaced by ga2_e1.sdc
}

ASSIGN_RE = re.compile(r'^\s*set_global_assignment\s+-name\s+(\S+)\s+(.*?)\s*$')


def sh(*cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_pin():
    rc = subprocess.run([sys.executable, str(ROOT / "tools" / "check_upstream.py")],
                        capture_output=True, text=True)
    sys.stdout.write(rc.stdout)
    if rc.returncode != 0:
        sys.exit("refusing to generate: upstream pin check failed")


def upstream_assignments():
    """(name, value) pairs from upstream QSF that E1 keeps, in order."""
    kept, dropped, sources = [], [], []
    for line in (S32 / "Arcade-SegaSystem32.qsf").read_text().splitlines():
        m = ASSIGN_RE.match(line)
        if not m:
            continue
        name, value = m.group(1), m.group(2)
        if name in ("SYSTEMVERILOG_FILE", "VERILOG_FILE", "VHDL_FILE", "QIP_FILE"):
            sources.append((name, value.strip('"')))
            continue
        if name in DROP_NAMES:
            dropped.append((name, value))
            continue
        if name == "VERILOG_MACRO" and value.strip('"').startswith("MISTER_"):
            dropped.append((name, value))
            continue
        kept.append((name, value))
    return kept, dropped, sources


def upstream_sources(extra):
    """Source assignments from files.qip plus the upstream QSF, filtered."""
    out, dropped = [], []
    for line in (S32 / "files.qip").read_text().splitlines():
        m = ASSIGN_RE.match(line)
        if not m:
            continue
        name, value = m.group(1), m.group(2).strip('"')
        if name not in ("SYSTEMVERILOG_FILE", "VERILOG_FILE", "VHDL_FILE", "QIP_FILE", "SDC_FILE"):
            continue
        (dropped if value in DROP_SOURCES else out).append((name, value))
    for name, value in extra:
        (dropped if value in DROP_SOURCES else out).append((name, value))
    seen, uniq = set(), []
    for item in out:
        if item[1] not in seen:
            seen.add(item[1])
            uniq.append(item)
    for _, value in uniq:
        if not (S32 / value).exists():
            sys.exit(f"upstream source listed but missing: {value}")
    return uniq, dropped


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", choices=sorted(TOPS), required=True)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--device", default=UPSTREAM_DEVICE)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--parallel", default="2",
                    help="NUM_PARALLEL_PROCESSORS (upstream uses 2)")
    a = ap.parse_args()

    check_pin()
    out = a.out or ROOT / "build" / "E1" / f"{a.config}-seed{a.seed}"
    out.mkdir(parents=True, exist_ok=True)
    rel = Path(os.path.relpath(ROOT, out))          # project dir -> repo root
    s32_rel = rel / "references" / "s32"
    exp_rel = rel / "experiments" / "E1-baseline"

    kept, dropped_settings, qsf_sources = upstream_assignments()
    sources, dropped_sources = upstream_sources(qsf_sources)
    top = TOPS[a.config]

    q = []
    w = q.append
    w("# GENERATED by experiments/E1-baseline/gen_project.py -- do not edit.")
    w(f"# config={a.config} seed={a.seed} device={a.device}")
    w(f"# upstream s32 @ {sh('git', '-C', str(S32), 'rev-parse', 'HEAD')}")
    w("")
    w('set_global_assignment -name FAMILY "Cyclone V"')
    w(f"set_global_assignment -name DEVICE {a.device}")
    w(f"set_global_assignment -name TOP_LEVEL_ENTITY {top}")
    w("set_global_assignment -name PROJECT_OUTPUT_DIRECTORY output_files")
    w(f"set_global_assignment -name SEED {a.seed}")
    w(f"set_global_assignment -name NUM_PARALLEL_PROCESSORS {a.parallel}")
    w("set_global_assignment -name GENERATE_RBF_FILE OFF")
    w("")
    w("# --- Compiler settings and macros copied from upstream Arcade-SegaSystem32.qsf")
    for name, value in kept:
        w(f"set_global_assignment -name {name} {value}")
    w("")
    w("# --- Upstream sources (files.qip + upstream QSF), MiSTer framework removed")
    for name, value in sources:
        if name == "SDC_FILE":
            continue
        w(f'set_global_assignment -name {name} "{(s32_rel / value).as_posix()}"')
    w("")
    w("# --- E1 harness")
    for f in ("rtl/ga2_core_harness.sv", f"rtl/{top}.sv"):
        w(f'set_global_assignment -name SYSTEMVERILOG_FILE "{(exp_rel / f).as_posix()}"')
    w(f'set_global_assignment -name SDC_FILE "{(exp_rel / "ga2_e1.sdc").as_posix()}"')
    if a.config == "vp":
        w("")
        w("# --- Every harness input/output is a virtual pin (clocks stay real pins)")
        for i in range(N_IN):
            w(f"set_instance_assignment -name VIRTUAL_PIN ON -to din[{i}]")
        for i in range(N_OUT):
            w(f"set_instance_assignment -name VIRTUAL_PIN ON -to dout[{i}]")
    (out / "ga2_e1.qsf").write_text("\n".join(q) + "\n")
    (out / "ga2_e1.qpf").write_text('PROJECT_REVISION = "ga2_e1"\n')

    manifest = {
        "experiment": "E1",
        "config": a.config,
        "top": top,
        "seed": a.seed,
        "device": a.device,
        "upstream": {"s32": sh("git", "-C", str(S32), "rev-parse", "HEAD")},
        "repo_commit": sh("git", "-C", str(ROOT), "rev-parse", "HEAD"),
        "repo_dirty": bool(sh("git", "-C", str(ROOT), "status", "--porcelain",
                              "--", "experiments", "tools")),
        "harness_sha256": {f: sha256(HERE / f) for f in (
            "rtl/ga2_core_harness.sv", f"rtl/{top}.sv", "ga2_e1.sdc", "gen_project.py")},
        "settings_copied": kept,
        "settings_dropped": dropped_settings,
        "sources_dropped": dropped_sources,
        "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    }
    (out / "run.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote {out}/ga2_e1.qsf ({len(sources)} upstream sources, "
          f"{len(kept)} settings copied, {len(dropped_settings)} dropped)")


if __name__ == "__main__":
    main()
