#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""TEST-ONLY: the FROZEN pre-R4 (R3b) builder state, installed in place of the live builder, so that the R3b-phase suite can be
reproduced on the R3b tables and control stores after R4b changes the live builder (docs/r4-movea-pcrel-plan.md section 7.1:
"the R3b-phase suite re-runs against the pre-R4 fixture"). build() returns the fixture's control store, symbols and tables for the
requested (smc_test, smc_merged, reset_native) combination, exactly as the builder at sha256 4ac7fa34... produced them
(results/r4a_pre_r4_fixture_2026-11-04.json). The mutant knobs are not available (mut must be empty).

  m1_r4_fixbuilder.py <out-dir> [builder flags]     same CLI as m1_m11_microcode.py (used as run_r3.sh's R3_MCGEN)
  install()                                          patches m1_m11_microcode.build in memory (used by m1_r3_run.py --builder-module)
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "ref"))
import m1_m11_microcode as B  # noqa: E402

FIXTURE = HERE / "results/r4a_pre_r4_fixture_2026-11-04.json"
_fx = None


def build(smc_test=False, smc_merged=False, reset_native=False, mut=None):
    global _fx
    assert not mut, "the frozen pre-R4 builder state has no mutant knobs"
    if _fx is None:
        _fx = json.loads(FIXTURE.read_text())
    c = _fx["configs"][f"smc_test={int(smc_test)} smc_merged={int(smc_merged)} reset_native={int(reset_native)}"]
    return ([int(w, 16) for w in c["cstore"]], dict(c["sym"]), dict(enumerate(c["dtab"])), dict(enumerate(c["mtab"])))


def install():
    B.build = build


def main():
    install()
    B.main()


if __name__ == "__main__":
    main()
