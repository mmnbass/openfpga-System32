#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pocket SDRAM/SRAM I/O assignments for GA2 Pocket fits: the single source for D2 and the PLL diagnostic.

    apply(qsf_text) -> qsf_text     # template ap_core.qsf (core-template da3a021) in, patched text out
    python3 d2_pocket_io.py <ap_core.qsf> <out.qsf>   # same, as a command; prints a change summary

What it changes (provenance K(J) = proven on the Pocket by the pocket_jaguar core, projects/jaguar_pocket.qsf):
  1. IO_STANDARD "3.3-V LVCMOS" -> "3.3-V LVTTL" on every sram_* pin, rewritten in place (no duplicate assignment).
     Cyclone V allows only 2 mA at 3.3-V LVCMOS; Jaguar measured 28-38 ns SRAM reads at 2 mA and 19 ns at 8 mA
     (ENGINEERING_LOG #41/#42 there). Pin LOCATIONS are never touched.
  2. Appends the I/O-register and drive-strength block of jaguar_pocket.qsf:826-842: FAST_OUTPUT_REGISTER on dram_* and
     sram_*, FAST_OUTPUT_ENABLE_REGISTER and FAST_INPUT_REGISTER on dram_dq[*] and sram_dq[*], CURRENT_STRENGTH_NEW 8MA on
     all sram_* pins. The SDRAM pins keep the template's 1.8 V standard and drive (as Jaguar).
The Jaguar-only ALLOW_SYNCH_CTRL_USAGE line (*|SDRAM_*, a Jaguar instance name) is not copied.

Not covered here (no Pocket board data exists): SDRAM/SRAM trace delays and set_input/output_delay values. Those are SDC
ASSUMPTIONS, labelled in pocket/d2_core_constraints.sdc.
"""
import re
import sys

SRAM_STD = re.compile(r'^(set_instance_assignment -name IO_STANDARD )"3\.3-V LVCMOS"( -to sram_\S+\s*)$')

APPEND = """
# ---- d2_pocket_io.py: Pocket SDRAM/SRAM I/O (K(J): pocket_jaguar projects/jaguar_pocket.qsf:826-842) ----
set_instance_assignment -name FAST_OUTPUT_REGISTER ON -to dram_*
set_instance_assignment -name FAST_OUTPUT_ENABLE_REGISTER ON -to dram_dq[*]
set_instance_assignment -name FAST_INPUT_REGISTER ON -to dram_dq[*]
set_instance_assignment -name FAST_OUTPUT_REGISTER ON -to sram_*
set_instance_assignment -name FAST_OUTPUT_ENABLE_REGISTER ON -to sram_dq[*]
set_instance_assignment -name FAST_INPUT_REGISTER ON -to sram_dq[*]
set_instance_assignment -name CURRENT_STRENGTH_NEW 8MA -to sram_a[*]
set_instance_assignment -name CURRENT_STRENGTH_NEW 8MA -to sram_dq[*]
set_instance_assignment -name CURRENT_STRENGTH_NEW 8MA -to sram_oe_n
set_instance_assignment -name CURRENT_STRENGTH_NEW 8MA -to sram_we_n
set_instance_assignment -name CURRENT_STRENGTH_NEW 8MA -to sram_ub_n
set_instance_assignment -name CURRENT_STRENGTH_NEW 8MA -to sram_lb_n
# ---- end d2_pocket_io.py ----
"""

EXPECTED_SRAM_STD_LINES = 37   # template da3a021: sram_a[16:0] + sram_dq[15:0] + oe/we/ub/lb


def apply(text):
    out, n = [], 0
    for line in text.splitlines(keepends=True):
        m = SRAM_STD.match(line.rstrip("\r\n"))
        if m:
            eol = line[len(line.rstrip("\r\n")):]           # keep the template's own line ending (CRLF)
            line = f'{m.group(1)}"3.3-V LVTTL"{m.group(2).rstrip()}{eol}'
            n += 1
        out.append(line)
    if n != EXPECTED_SRAM_STD_LINES:
        raise SystemExit(f"d2_pocket_io: rewrote {n} sram IO_STANDARD lines, expected {EXPECTED_SRAM_STD_LINES}")
    if "d2_pocket_io.py" in text:
        raise SystemExit("d2_pocket_io: input already patched")
    s = "".join(out)
    if not s.endswith("\n"):
        s += "\n"
    return s + APPEND


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    src = open(sys.argv[1], newline="").read()                 # newline="": no line-ending translation
    open(sys.argv[2], "w", newline="").write(apply(src))
    print(f"d2_pocket_io: {EXPECTED_SRAM_STD_LINES} sram IO_STANDARD lines -> 3.3-V LVTTL; {APPEND.count('set_instance')} I/O lines appended")
