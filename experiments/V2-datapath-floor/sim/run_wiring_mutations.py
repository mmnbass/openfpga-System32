#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Wiring mutations against the V2 smoke test.

Each mutation disconnects or mis-wires one path in a copy of rtl/ and
rebuilds the smoke test (UNIT_STYLE 0); the smoke test must FAIL on every
one. Every mutation must apply exactly once (or, where marked "all", at least
once, every occurrence replaced), or the run fails: a mutation
that silently stops applying after an RTL edit would otherwise count as
"not caught" or, worse, be skipped.

  original 9 (V2 skeleton): decoder fields, mode descriptor, redirect, DEC_EA,
                            consume code 10, COND_HI
  M1.1 (front end):         format-byte conditions (N1), mode-table bank (N2),
                            mode-byte register lifetime (N4), instruction-start
                            PC (N5)
Mutations that the smoke program cannot tell apart (which format-byte bit or
bank select is read, the decode-ready condition N3) are covered by the M1.1
MOV.W / BR8 suite instead (experiments/M1-functional/m11_mutants.py).

Usage: run_wiring_mutations.py <work-dir>
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
V = os.path.dirname(HERE)
LATCH = "            mreg    <= mbyte_q[4:0];      // stable while the specifier is consumed (M1.1 N4)"

MUTS = [
    ("size1 from window heuristic", "v2_useq.sv", [("size1  <= fmt[1:0];", "size1  <= window[2:1];")]),
    ("field layout ignored", "v2_useq.sv", [("case (fmt[5:4])", "case (2'd0)")]),
    ("dflags not latched", "v2_useq.sv", [("dflags <= fmt[11:7];", "dflags <= 5'd0;")]),
    ("ea_desc not latched", "v2_useq.sv", [("            ea_desc <= mode_entry[23:12];", "            ea_desc <= 12'd0;")]),
    ("REDIRECT ignored (WR_DST)", "v2_core.sv", [("cw3[`CW_REDIRECT]", "cw3[`CW_WR_DST] == 3'd5", "all")]),
    ("DEC_EA ignored in AGU", "v2_core.sv", [("wire       dec2     = cw2[`CW_DEC_EA];", "wire       dec2     = 1'b0;")]),
    ("DEC_EA ignored in imm", "v2_core.sv", [(".dec_ea(cw[`CW_DEC_EA])", ".dec_ea(1'b0)")]),
    ("consume code 10 = literal", "v2_glue.sv",
     [("4'd10:   consume_eff = 4'd1 + {3'd0, idx} + dbytes;", "4'd10:   consume_eff = 4'd0;")]),
    ("COND_HI ignored", "v2_useq.sv", [("{cw[`CW_COND_HI], cw[`CW_COND]}", "{1'b0, cw[`CW_COND]}")]),
    # M1.1
    ("N1 F1 condition tied 0", "v2_core.sv", [("assign ucond[24] = ifl[7];", "assign ucond[24] = 1'b0;")]),
    ("N1 D condition tied 0", "v2_core.sv", [("assign ucond[25] = ifl[5];", "assign ucond[25] = 1'b0;")]),
    ("N2 mode-table bank bit ignored", "v2_useq.sv",
     [("wire  [8:0] mt_addr   = {modm, mode_byte};", "wire  [8:0] mt_addr   = {1'b0, mode_byte};")]),
    ("N4 mode-byte register never latched", "v2_useq.sv", [(LATCH, "            mreg    <= mreg;")]),
    ("N4 mode-byte register free-running (pre-M1.1)", "v2_useq.sv",
     [(LATCH, ""), ("        if (dispatch) begin\n            ifl <= ib[15:8];",
                    "        mreg <= mode_byte[4:0];\n        if (dispatch) begin\n            ifl <= ib[15:8];")]),
    ("N5 PC source is the fetch head", "v2_core.sv",
     [(".rt(rt_data), .pc(ipc), .mdr(mdr),", ".rt(rt_data), .pc(head_pc), .mdr(mdr),")]),
    ("N5 instruction-start PC not latched", "v2_core.sv",
     [("    else if (adv && cw[`CW_NXT] == 3'd5) ipc <= head_pc;", "    else if (1'b0) ipc <= head_pc;")]),
    # B3 (register index from a temp)
    ("B3 RIDX ignored on the A read port", "v2_core.sv", [(".ra_addr(cw[`CW_RIDX] ? rt_data[4:0] : pick_reg(", ".ra_addr(1'b0 ? rt_data[4:0] : pick_reg(")]),
    ("B3 RIDX ignored on the write address", "v2_core.sv", [("wire [4:0] wr_addr0 = cw[`CW_RIDX] ? rt_data[4:0] :", "wire [4:0] wr_addr0 = 1'b0 ? rt_data[4:0] :")]),
    ("B3 RIDX index is the constant field", "v2_core.sv", [(".ra_addr(cw[`CW_RIDX] ? rt_data[4:0] :", ".ra_addr(cw[`CW_RIDX] ? cw[`CW_RCONST] :")]),
]


def main():
    work = os.path.abspath(sys.argv[1])
    os.makedirs(work + "/out", exist_ok=True)
    subprocess.run(["python3", HERE + "/microasm.py", "smoke", work + "/out"], stdout=subprocess.DEVNULL, check=True)
    caught, bad = 0, 0
    for i, (name, f, reps) in enumerate(MUTS):
        d = f"{work}/m{i}"
        shutil.copytree(V + "/rtl", d + "/rtl", dirs_exist_ok=True)
        p = f"{d}/rtl/{f}"
        s = open(p).read()
        ok = True
        for rep in reps:
            a, b = rep[0], rep[1]
            n = s.count(a)
            if n != 1 and not (len(rep) > 2 and rep[2] == "all" and n >= 1):
                ok = False
                break
            s = s.replace(a, b)
        if not ok:
            print(f"{name}: MUTATION DID NOT APPLY")
            bad += 1
            continue
        open(p, "w").write(s)
        files = [f"{d}/rtl/{line.strip()}" for line in open(d + "/rtl/files.f") if line.strip()]
        subprocess.run(["verilator", "--binary", "--timing", "-Wno-fatal", "-Wno-lint", "-Wno-style", f"-I{d}/rtl",
                        "--top-module", "tb_v2_smoke", "--Mdir", d + "/obj", "-o", "sim", HERE + "/tb_v2_smoke.sv"] + files,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        out = subprocess.run([d + "/obj/sim"], cwd=work + "/out", capture_output=True, text=True).stdout
        r = [line for line in out.splitlines() if "PASS" in line or "FAIL" in line]
        r = r[-1] if r else "no result (build failed?)"
        if "FAIL" in r:
            caught += 1
        print(f"{name}: {r}")
    print(f"wiring mutations caught: {caught}/{len(MUTS)}")
    sys.exit(0 if caught == len(MUTS) and bad == 0 else 1)


if __name__ == "__main__":
    main()
