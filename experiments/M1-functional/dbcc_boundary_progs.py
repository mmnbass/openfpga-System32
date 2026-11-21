#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Programs for run_dbcc_boundary.sh (instruction-boundary regression): taken
DBcc/TB loops that the upstream core runs through its S_DECODE -> S_DECODE
loop-window restore. Writes <out>/<name>.bin/.hex and prints, per program,
"<name> <expected retired instructions>" from the MAME-derived reference
(m1_ref_ext.V60X; DBcc semantics as S1's vectors).

  dbcc_boundary_progs.py <outdir>
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import m1_ref_ext as X           # noqa: E402
from m1_image import write       # noqa: E402


def le32(v):
    return list((v & 0xFFFFFFFF).to_bytes(4, "little"))


def mov_w_imm(n, v):             # MOV.W #v, Rn
    return [0x2D, 0x20 | n, 0xF4] + le32(v)


def db(op, sub, reg, disp):      # DBcc/TB Rn, disp16 (disp from the DBcc's own address)
    return [op, (sub << 5) | reg, disp & 0xFF, (disp >> 8) & 0xFF]


def progs():
    p = {}
    # loop body + DBR (the GA2 boot copy-loop shape): MOV.W #4,R1; L: MOV.W #7,R2; DBR R1,L; HALT
    p["dbr_body"] = mov_w_imm(1, 4) + mov_w_imm(2, 7) + db(0xC6, 5, 1, -7) + [0x00]
    # longer run: 40 iterations
    p["dbr_body40"] = mov_w_imm(1, 40) + mov_w_imm(2, 7) + db(0xC6, 5, 1, -7) + [0x00]
    # DBR branching to itself: MOV.W #4,R1; L: DBR R1,L; HALT
    p["dbr_self"] = mov_w_imm(1, 4) + db(0xC6, 5, 1, 0) + [0x00]
    # DBNE: Z cleared by the body (MOV does not touch flags; R3 = 0 so CMP-free): MOV.W #3,R1; L: MOV.W #7,R2; DBNE R1,L
    p["dbne_body"] = mov_w_imm(1, 3) + mov_w_imm(2, 7) + db(0xC7, 2, 1, -7) + [0x00]
    return p


def main():
    out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    for name, code in progs().items():
        img = bytes(code)
        write(img, str(out / name))
        cpu, m, n = X.run(img, max_instrs=10000)
        assert cpu.halted, name
        print(name, n)


if __name__ == "__main__":
    main()
