#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""R3a: write the FROZEN pre-R3 fixture and table pins from the builder AS IT IS BEFORE R3b (docs/r3-movea-plan.md
section 6 #3). The R3b-phase suite and the probe image read results/r3a_pre_r3_fixture_*.json, never the live builder
(R2a round-1 lesson). Run once, at R3a; refuses to run if any MOVEA dispatch entry already exists.

  m1_r3_fixture.py <fixture.json> <pins.json>
"""
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "ref"))
import m1_m11_microcode as B  # noqa: E402
import m1_r3_run as RUN  # noqa: E402


def main():
    fx_path, pin_path = Path(sys.argv[1]), Path(sys.argv[2])
    sha = hashlib.sha256(Path(B.__file__).read_bytes()).hexdigest()
    p, sym, dtab, mtab = B.build()
    assert all(dtab[o] == sym["trap"] for o in (0x40, 0x42, 0x44)), "a MOVEA dispatch entry already exists"
    fx_path.write_text(json.dumps({
        "builder_sha256": sha,
        "note": "pre-R3 production tables (R2b builder, production build): frozen so the R3b-phase suite and the TEST-ONLY probe image never depend on the live builder",
        "cstore": ["%x" % w for w in p], "sym": sym, "dtab": [dtab[i] for i in range(256)], "mtab": [mtab[i] for i in range(512)],
    }))
    pins = {"builder_sha256": sha}
    for st in (False, True):
        for sm in (False, True):
            for rn in (False, True):
                p2, s2, d2, m2 = B.build(st, sm, rn)
                pins[f"smc_test={int(st)} smc_merged={int(sm)} reset_native={int(rn)}"] = {
                    "tables_full": RUN.tables_hash(d2, m2), "tables_except_dtab40": RUN.tables_hash(d2, m2, (0x40,)),
                    "cstore_len": len(p2), "cstore_sha": RUN.cstore_hash(p2)}
    pin_path.write_text(json.dumps(pins, indent=1) + "\n")
    print(f"builder sha256 {sha}; fixture {len(p)} words; pins for 8 combinations")


if __name__ == "__main__":
    main()
