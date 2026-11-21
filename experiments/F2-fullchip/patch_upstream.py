#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Write F2's derived copies of two upstream files into a build directory.

The pinned upstream tree (references/s32) is never modified. Each patch is an
exact text replacement that must match exactly once, or the script fails; the
sha256 of every input and output is printed for run.json.

  s32_core.sv        work_ram and sprite_ram: s32_big_dpram -> f2_xram_standin
                     (the two RAMs the Pocket port moves off-chip; VRAM stays)
  audio/s32_soundsys.sv
                     s32_rf5c68 EXTERNAL_WAVE_RAM 1'b0 -> 1'b1: upstream's own
                     external wave-RAM path, through the existing s32_core
                     ports sdr_p4 / sdr_wave_wr. Upstream reverted to the
                     internal RAM after a fail-silent hardware fault on that
                     path (see the comment above the instance); F2 uses it
                     only to measure its logic.

Usage: patch_upstream.py <out-dir>   -> <out-dir>/s32_core.sv, s32_soundsys.sv
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
S32 = ROOT / "references" / "s32" / "rtl"

PATCHES = {
    "s32_core.sv": [
        ("s32_big_dpram #(\n    .ADDR_WIDTH(WRAM_ADDR_WIDTH), .NUM_WORDS(WRAM_WORDS)\n) work_ram (",
         "f2_xram_standin #(   // F2: work RAM moved off-chip\n"
         "    .ADDR_WIDTH(WRAM_ADDR_WIDTH), .NUM_WORDS(WRAM_WORDS)\n) work_ram ("),
        ("s32_big_dpram #(\n    .ADDR_WIDTH(16), .NUM_WORDS(65536), .MIXED_RDW_MODE(\"DONT_CARE\"),\n"
         "    .PORT_B_READ_ONLY(1'b1)\n) sprite_ram (",
         "f2_xram_standin #(   // F2: sprite RAM moved off-chip\n"
         "    .ADDR_WIDTH(16), .NUM_WORDS(65536), .MIXED_RDW_MODE(\"DONT_CARE\"),\n"
         "    .PORT_B_READ_ONLY(1'b1)\n) sprite_ram ("),
    ],
    "audio/s32_soundsys.sv": [
        ("s32_rf5c68 #(.EXTERNAL_WAVE_RAM(1'b0)) rf5c68 (",
         "s32_rf5c68 #(.EXTERNAL_WAVE_RAM(1'b1)) rf5c68 (   // F2: wave RAM external"),
    ],
}


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    record = {}
    for rel, reps in PATCHES.items():
        src = (S32 / rel).read_text()
        txt = src
        for old, new in reps:
            n = txt.count(old)
            if n != 1:
                sys.exit(f"patch anchor matched {n} times in {rel}: {old[:60]!r}")
            txt = txt.replace(old, new)
        dst = out / Path(rel).name
        dst.write_text(txt)
        record[rel] = {"in_sha256": hashlib.sha256(src.encode()).hexdigest(),
                       "out": dst.name,
                       "out_sha256": hashlib.sha256(txt.encode()).hexdigest(),
                       "replacements": len(reps)}
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
