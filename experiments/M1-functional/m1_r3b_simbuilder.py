#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""TEST-ONLY simulated R3b TABLE state (as m1_r2b_simbuilder.py): patches m1_m11_microcode.build IN MEMORY so that, in
production builds, dtab[0x40] points at a `movea` symbol (the fmt_movw layout), as a correct R3b builder would leave
it; the --smc-test builds keep dtab[0x40] = trap. The routine is an inert self-loop stub: this is NOT MOVEA microcode and
implements no semantics. Its only purpose is to show that R3_PHASE=r3b passes the driver's structural stages, reaches
validation, and FAILS the V2 behaviour groups (the stub hangs). m1_m11_microcode.py itself is never edited.

  m1_r3b_simbuilder.py <out-dir> [builder flags]     (same CLI as m1_m11_microcode.py)
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import m1_m11_microcode as B  # noqa: E402
from microasm import word  # noqa: E402

_orig = B.build


def build(smc_test=False, smc_merged=False, reset_native=False, mut=None):
    p, sym, dtab, mtab = _orig(smc_test, smc_merged, reset_native, mut)
    p, sym, dtab = list(p), dict(sym), dict(dtab)
    sym["movea"] = len(p)
    p.append(word(NXT=1, JADDR=len(p)))                    # self-loop stub
    if not smc_test:
        fmt_movw = 2 | (2 << 2) | (0 << 4) | (1 << 8) | (1 << 10)
        dtab[0x40] = (fmt_movw << 12) | sym["movea"]
    return p, sym, dtab, mtab


def install():
    B.build = build


if __name__ == "__main__":
    install()
    B.main()
