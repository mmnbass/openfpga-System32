#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""R4a: write the FROZEN pre-R4 fixture and table pins from the builder AS IT IS BEFORE R4b (docs/r4-movea-pcrel-plan.md
section 6 infrastructure #2 and 7.1). The fixture holds the control store, symbols and tables of ALL EIGHT builder
configurations (smc_test x smc_merged x reset_native) so that the R3b-phase suite can be reproduced on the R3b state
(m1_r4_fixbuilder.py installs it in place of the live builder). The r4b-phase production behaviour tests never read it.
Refuses to run unless the builder sha256 is the R3b one (4ac7fa34...) and mtab[0x0F2] is trap_mode.

  m1_r4_fixture.py <fixture.json> <pins.json>
"""
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "ref"))
import m1_m11_microcode as B  # noqa: E402

PRE_R4_SHA = "4ac7fa340674fc73ce4166adf07b01efde7c417e1c409c00c40913a40f94c510"


def tables_hash(dtab, mtab):
    h = hashlib.sha256()
    for i in range(256):
        h.update(dtab[i].to_bytes(4, "little"))
    for i in range(512):
        h.update(mtab[i].to_bytes(4, "little"))
    return h.hexdigest()


def cstore_hash(p):
    h = hashlib.sha256()
    for w in p:
        h.update(int(w).to_bytes(16, "little"))
    return h.hexdigest()


def key(st, sm, rn):
    return f"smc_test={int(st)} smc_merged={int(sm)} reset_native={int(rn)}"


def main():
    fx_path, pin_path = Path(sys.argv[1]), Path(sys.argv[2])
    sha = hashlib.sha256(Path(B.__file__).read_bytes()).hexdigest()
    assert sha == PRE_R4_SHA, f"builder sha256 {sha} is not the pre-R4 (R3b) builder {PRE_R4_SHA}"
    cfg, pins = {}, {"builder_sha256": sha}
    for st in (False, True):
        for sm in (False, True):
            for rn in (False, True):
                p, sym, dtab, mtab = B.build(st, sm, rn)
                assert (mtab[0x0F2] & 0xFFF) == sym["trap_mode"], "mtab[0x0F2] is not trap_mode: not the pre-R4 builder"
                cfg[key(st, sm, rn)] = {"cstore": ["%x" % w for w in p], "sym": sym, "dtab": [dtab[i] for i in range(256)],
                                        "mtab": [mtab[i] for i in range(512)]}
                pins[key(st, sm, rn)] = {"tables_full": tables_hash(dtab, mtab), "cstore_len": len(p), "cstore_sha": cstore_hash(p)}
    fx_path.write_text(json.dumps({"builder_sha256": sha, "builder_git_blob": "4b2a0dafc8914fd7be7a518e94fc06049ddfdac9",
                                   "note": "pre-R4 (R3b) tables and control stores of all eight builder configurations, frozen so the R3b-phase suite "
                                           "is reproduced on the R3b state and never depends on the live builder", "configs": cfg}))
    pin_path.write_text(json.dumps(pins, indent=1) + "\n")
    print(f"builder sha256 {sha}; fixture: 8 configurations, production {len(cfg[key(0, 0, 0)]['cstore'])} words")


if __name__ == "__main__":
    main()
