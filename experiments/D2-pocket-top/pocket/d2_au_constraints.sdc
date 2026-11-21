
# ===================================================================================================================================
# AU APPENDIX (exp/audio1, gen_d2.py --config au; experiments/AUDIO/PLAN.md). prepare_quartus_d2.py writes the au project's
# core/core_constraints.sdc as pocket/d2_core_constraints.sdc BYTE FOR BYTE followed by this file; every constraint above is therefore
# unchanged (d2_project_check.py C4 and tools/d2rbf_identity.py self-reference S4 check that prefix). The ids below are AU<n> rows of
# the README table "AU SDC appendix" (C12 checks both id sets). Nothing here changes a clock, a clock group, an existing exception or
# a fitter setting.
# ===================================================================================================================================

# ---- AU: PSRAM chip 0 (cram0) read data, the RF5C68 wave RAM (rtl/d2_wave_psram.sv, instance wvps) -------------------------------
# d2_wave_psram registers every cram0 output in clk_sys (power-up idle) and takes cram0_dq into sdr_p4_dout ONLY at edge E5 of a read:
# 5 clk_sys (103.48 ns) after the edge E0 that launches ce0_n / adv_n / the address, 2 clk_sys (41.4 ns) after oe_n falls (E3).
# ASSUMPTION: AU1 (max 75.2 ns): HW-002, the PSRAM probe on this Pocket (experiments/PSRAM-probe, HARDWARE_TEST.md): asynchronous reads
# of both chips were clean at 8 clocks of 9.402 ns = 75.2 ns after the probe's launch edge (7 clocks = 65.8 ns never passed). As A6,
# that is a clean-read time seen by the probe's own capture register (FPGA output + board + device + FPGA input), used as an input
# delay against clk_sys; STA adds this design's FPGA input path once more (conservative). The PSRAM part is not identified.
# ASSUMPTION: AU2 (min 0.0 ns): no source (no hold credit), as A7.
# ASSUMPTION: AU3 (multicycle 5 / 4): d2_wave_psram's own read schedule (dq sampled at E5 only), verified in simulation only (the A1
# unit bench and the A2 integration bench with the PSRAM model at tAA = 75.2 ns), not on the Pocket.
set au_sys_clk [get_clocks -nowarn {ic|mp|pll_inst|altera_pll_i|*[1].*|divclk}]
set au_wv_q    [get_registers -nowarn {*|d2_wave_psram:wvps|sdr_p4_dout[*]}]
if {[d2_require [expr {[get_collection_size $au_sys_clk] == 1}] "clk_sys PLL output clock (AU PSRAM)"] &&
    [d2_require [expr {[get_collection_size $au_wv_q] > 0}] "d2_wave_psram:wvps|sdr_p4_dout registers"]} {
    set_input_delay -clock $au_sys_clk -max 75.2 [get_ports {cram0_dq[*]}]
    set_input_delay -clock $au_sys_clk -min 0.0  [get_ports {cram0_dq[*]}]
    set_multicycle_path -setup -end -from [get_ports {cram0_dq[*]}] -to $au_wv_q 5
    set_multicycle_path -hold  -end -from [get_ports {cram0_dq[*]}] -to $au_wv_q 4
}

# ---- AU: PSRAM chip 0 outputs: no output delay (no timing command in this section) -----------------------------------------------
# ASSUMPTION: AU4 asynchronous-mode PSRAM sequenced in whole clocks needs no output-delay constraint: every cram0 address / data / strobe
# change of d2_wave_psram is at least one clk_sys (20.7 ns) from the strobe edge it qualifies (tVP, tCVS, tAVS, tAVH, tAS 1 clk; tWP
# 3 clk; tAW / tCW / tBW 4 clk; tDW 2 clk; tDH 1 clk; ce0_n high >= 1 clk between accesses). Source: the PSRAM probe's convention, which
# ran on this Pocket in HW-002 at 9.4-ns clocks with NO PSRAM I/O constraint (experiments/PSRAM-probe/pocket/core_constraints.sdc).
# The fitted register-to-pin delays and their skew per corner are REPORTED by pocket/d2_au_io.tcl (informational), as the probe's
# probe_io_delays.tcl did; cram1 is tied to constants.

# ---- AU: audio (sound_i2s) clk_sys -> clk_74a, and the I2S pins (no timing command in this section) ------------------------------
# ASSUMPTION: AU5 the mixer register (clk_sys) reaches clk_74a only through sound_i2s's sync_fifo dcfifo (use_eab OFF, gray-code
# pointers through rdsync / wrsync delay pipes of 5): the existing asynchronous groups (A1: clk_74a vs the PLL group) cut that
# crossing, exactly as they cut data_loader's clk_74a -> clk_sys dcfifo (hardware-proven since GA2Play2) and as pocket_jaguar's
# core_constraints.sdc does for its sound_i2s (K(J)). audio_mclk / audio_lrck / audio_dac are clk_74a registers (12.288 MHz MCLK,
# 3.072 MHz bit rate) with no output delay, as the template's own silence generator (core-template core/core_top.v) and
# pocket_jaguar. No constraint is added and none is relaxed.
