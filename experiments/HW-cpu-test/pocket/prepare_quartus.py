#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Assemble a Quartus project for the CPU hardware test core. It does NOT run
Quartus: building the core needs separate approval.

  prepare_quartus.py <gen-dir> <out-dir> --template <core-template src/fpga>

<gen-dir> is run_hwt.sh's generated directory (hex images, hwt_cfg.svh), which
must be the one whose simulation passed. --template is a checkout of
Analogue's openFPGA core template (src/fpga), from which the apf platform files,
core_bridge_cmd.v and the pin assignments of ap_core.qsf are taken unchanged.

The project:
  hwt.qpf / hwt.qsf   ap_core.qsf with the template's own core files and
                      Signal Tap removed (ENABLE_SIGNALTAP OFF), plus pocket/hwt.qip
                      by a path relative to the project (the CI container
                      mounts the repository elsewhere)
  apf/, core_bridge_cmd.v       copied from the template
  core/core_constraints.sdc     pocket/core_constraints.sdc, at the path
                                apf_constraints.sdc reads (read once)
  *.hex, hwt_cfg.svh            the ROM and RAM images: $readmemh paths are
                                relative to the project directory
"""
import argparse
import os
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
DROP = ("core/core_top.v", "core/core_bridge_cmd.v", "core/core_constraints.sdc",
        "core/mf_pllbase.qip", "core/mf_pllbase.sip", "stp1.stp", "SLD_FILE",
        "ENABLE_SIGNALTAP")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gen", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--template", type=Path, required=True)
    a = ap.parse_args()
    t = a.template
    for f in ("ap_core.qsf", "apf/apf.qip", "core/core_bridge_cmd.v"):
        assert (t / f).is_file(), t / f
    for f in ("hwt_cfg.svh", "hwt_expect.hex", "v2_cstore.hex"):
        assert (a.gen / f).is_file(), a.gen / f
    a.out.mkdir(parents=True, exist_ok=True)
    shutil.copytree(t / "apf", a.out / "apf", dirs_exist_ok=True)
    shutil.copy2(t / "core" / "core_bridge_cmd.v", a.out)
    # the template's apf_constraints.sdc reads core/core_constraints.sdc
    (a.out / "core").mkdir(exist_ok=True)
    shutil.copy2(HERE / "core_constraints.sdc", a.out / "core" / "core_constraints.sdc")
    for f in list(a.gen.glob("*.hex")) + [a.gen / "hwt_cfg.svh"]:
        shutil.copy2(f, a.out)
    q = []
    for ln in (t / "ap_core.qsf").read_text().splitlines():
        if any(d in ln for d in DROP):
            continue
        q.append(ln)
    q += ["",
          "# ---- CPU hardware test core (prepare_quartus.py) ----",
          "set_global_assignment -name VERILOG_FILE core_bridge_cmd.v",
          f'set_global_assignment -name QIP_FILE "{Path(os.path.relpath(HERE / "hwt.qip", a.out.resolve())).as_posix()}"',
          "set_global_assignment -name ENABLE_SIGNALTAP OFF",
          # as upstream's Arcade-SegaSystem32.qsf: removes s32_v60_bus.sv's
          # simulation-only $isunknown assertion. A QSF assignment: Quartus
          # rejects VERILOG_MACRO inside a QIP (run 40262881111).
          'set_global_assignment -name VERILOG_MACRO "SYNTHESIS=1"',
          "set_global_assignment -name SEARCH_PATH .",
          f'set_global_assignment -name SEARCH_PATH "{Path(os.path.relpath(HERE.parent.parent / "V2-datapath-floor" / "rtl", a.out.resolve())).as_posix()}"',
          "set_global_assignment -name SEED 1"]
    (a.out / "hwt.qsf").write_text("\n".join(q) + "\n")
    (a.out / "hwt.qpf").write_text('PROJECT_REVISION = "hwt"\n')
    print(f"project in {a.out} (not built)")


if __name__ == "__main__":
    main()
