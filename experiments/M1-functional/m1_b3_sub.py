#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""B3 subset generator for fast iterations: the m1_b2.py programs whose name starts with the prefix in $B3_PREFIX (default b3_pm). Same outputs as m1_b2.py."""
import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import m1_b2 as G
from m1_image import write

def main():
    out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    pre = os.environ.get("B3_PREFIX", "b3_pm"); lines = []
    for name, p in G.directed().items():
        if name.startswith(pre):
            write(bytes(p.b), str(out / name)); lines.append(f"agree {name}")
    (out / "m11_list.txt").write_text("\n".join(lines) + "\n"); (out / "m11_smc_list.txt").write_text("")
    print(f"b3 subset: {len(lines)} programs")
main()
