#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""TEST-ONLY simulated R2b TABLE state (R2a Codex round 1 #1): patches m1_m11_microcode.build IN MEMORY so that
dtab[0x12] points at an `ldpr` symbol with the privileged flag and the trap_priv / trap_regid labels exist, as a
correct R2b builder would leave them. The routines are inert self-loop stubs: this is NOT LDPR microcode and
implements no semantics. Its only purpose is to show that R2_PHASE=r2b passes the driver's structural stages and
reaches validation. m1_m11_microcode.py itself is never edited.

  m1_r2b_simbuilder.py <out-dir> [builder flags]     (same CLI as m1_m11_microcode.py)
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import m1_m11_microcode as B  # noqa: E402
from microasm import word  # noqa: E402

_orig = B.build


def build(smc_test=False, smc_merged=False, reset_native=False):
    p, sym, dtab, mtab = _orig(smc_test, smc_merged, reset_native)
    p, sym, dtab = list(p), dict(sym), dict(dtab)
    for name in ("ldpr", "trap_priv", "trap_regid"):
        sym[name] = len(p)
        p.append(word(NXT=1, JADDR=len(p)))               # self-loop stub
    dtab[0x12] = ((1 << 7) << 12) | sym["ldpr"]           # fmt bit 7 = privileged
    return p, sym, dtab, mtab


def install():
    B.build = build


if __name__ == "__main__":
    install()
    B.main()
