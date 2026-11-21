
# ===================================================================================================================================
# AU3 APPENDIX (exp/audio3, gen_d2.py --config au3; GA2Audio3 = option B, experiments/AUDIO/B_PSRAM_ROM_NOTE.md). prepare_quartus_d2.py
# writes the au3 project's core/core_constraints.sdc as pocket/d2_core_constraints.sdc, then pocket/d2_au_constraints.sdc, then this
# file, each BYTE FOR BYTE; every constraint above is therefore unchanged (d2_project_check.py C4 and tools/d2rbf_identity.py
# self-reference S4 check both prefixes). The ids below are AR<n> rows of the README table "AU3 SDC appendix" (C12 checks both id
# sets). They are the AU1-AU4 constraints of PSRAM chip 0 applied, unchanged in value, to PSRAM chip 1 and its client d2_rom_psram
# (same pad conventions, same 5 + 1 clk_sys schedule). Nothing here changes a clock, a clock group, an existing exception or a
# fitter setting.
# ===================================================================================================================================

# ---- AU3: PSRAM chip 1 (cram1) read data, the Z80 sound ROM (rtl/d2_rom_psram.sv, instance rmps) --------------------------------
# d2_rom_psram registers every cram1 output in clk_sys (power-up idle) and takes cram1_dq into sdr_p3_dout ONLY at edge E5 of a read
# (Z80 ROM read or read-back verification): 5 clk_sys (103.48 ns) after the edge E0 that launches ce0_n / adv_n / the address,
# 2 clk_sys (41.4 ns) after oe_n falls (E3). The read-back fold and the s32_soundsys ROM cache read sdr_p3_dout one clock later.
# ASSUMPTION: AR1 (max 75.2 ns): as AU1 for chip 0. HW-002 (the PSRAM probe on this Pocket) read BOTH chips cleanly at 8 clocks of
# 9.402 ns = 75.2 ns after the probe's launch edge (7 clocks never passed, on either chip); used as an input delay against clk_sys,
# STA adds this design's FPGA input path once more (conservative). The PSRAM part is not identified.
# ASSUMPTION: AR2 (min 0.0 ns): no source (no hold credit), as AU2 / A7.
# ASSUMPTION: AR3 (multicycle 5 / 4): d2_rom_psram's own read schedule (dq sampled at E5 only), verified in simulation only (its unit
# bench, the A1 bench with the real client and the A2 integration bench, PSRAM models at tAA 70-100 / 75 / 75.2 ns), not on the Pocket.
set ar_sys_clk [get_clocks -nowarn {ic|mp|pll_inst|altera_pll_i|*[1].*|divclk}]
set ar_rp_q    [get_registers -nowarn {*|d2_rom_psram:rmps|sdr_p3_dout[*]}]
if {[d2_require [expr {[get_collection_size $ar_sys_clk] == 1}] "clk_sys PLL output clock (AU3 PSRAM chip 1)"] &&
    [d2_require [expr {[get_collection_size $ar_rp_q] > 0}] "d2_rom_psram:rmps|sdr_p3_dout registers"]} {
    set_input_delay -clock $ar_sys_clk -max 75.2 [get_ports {cram1_dq[*]}]
    set_input_delay -clock $ar_sys_clk -min 0.0  [get_ports {cram1_dq[*]}]
    set_multicycle_path -setup -end -from [get_ports {cram1_dq[*]}] -to $ar_rp_q 5
    set_multicycle_path -hold  -end -from [get_ports {cram1_dq[*]}] -to $ar_rp_q 4
}

# ---- AU3: PSRAM chip 1 outputs: no output delay (no timing command in this section) ----------------------------------------------
# ASSUMPTION: AR4 as AU4 for chip 0: asynchronous-mode PSRAM sequenced in whole clocks needs no output-delay constraint; every cram1
# address / data / strobe change of d2_rom_psram is at least one clk_sys (20.7 ns) from the strobe edge it qualifies (tVP, tCVS, tAVS,
# tAVH, tAS 1 clk; tWP 3 clk; tAW / tCW / tBW 4 clk; tDW 2 clk; tDH 1 clk; ce0_n high >= 1 clk between accesses). Source: the PSRAM
# probe's convention (HW-002, both chips, no PSRAM I/O constraint). The fitted register-to-pin delays and their skew per corner are
# REPORTED by pocket/d2_au_io.tcl and pocket/d2_au3_io.tcl (informational).
