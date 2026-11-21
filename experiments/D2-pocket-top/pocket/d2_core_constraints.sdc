# SPDX-License-Identifier: GPL-3.0-or-later
#
# DRAFT core constraints for a D2 fit of the GA2 first-pixels Pocket top (experiments/D2-pocket-top/rtl/d2_core_top.sv).
# NOT USED BY ANY BUILD YET; no Quartus has read it. It would sit at the template's core/core_constraints.sdc, which
# apf/apf_constraints.sdc reads after create_clock (clk_74a, clk_74b, bridge_spiclk) and derive_pll_clocks (as HW-001).
# Every number marked ASSUMPTION is not a Pocket measurement and must be listed as such in the fit report.
# AUDIT (owner requirement): every constraint below that is not backed by verified Pocket hardware data carries an
# "ASSUMPTION: A<n>" comment naming its source; the table (value, source, what would verify it) is in README.md
# ("SDC assumption audit"), and pocket/d2_project_check.py checks that the ids here and there are the same set and that
# every section with a timing command carries one. Nothing in this file is a Pocket board measurement except where a
# line says K(J) (Jaguar core on the Pocket) and even those are translated, not copied (A6).
# SRAM output delays (A10, A11) are applied by quartus_sta only and are informational.
# Not an assumption: derive_clock_uncertainty (Quartus' own derivation), the d2_require proc (error handling only).
# Object names (ic|mp|..., ic|sdramclk_ddr|...) follow HW-001's patterns and are UNVERIFIED until a map run prints them.
derive_clock_uncertainty

proc d2_require {present what} {
    if {$present} { return 1 }
    if {[string match "quartus_map" $::TimeQuestInfo(nameofexecutable)]} {
        post_message -type warning "D2 SDC: $what not elaborated yet; deferring to fit/STA"
        return 0
    }
    error "D2 SDC: expected $what but it is missing"
}

# ---- clock groups ------------------------------------------------------------------------------------------------------
# One PLL (ic|mp): [0] clk_ram 96.63, [1] clk_sys 48.32, [3] clk_vid 12.08, [4] clk_vid_90 are related (integer
# divisions of one VCO, edges aligned) and MUST stay in one group: the load queue (clk_sys -> clk_ram), the reset release
# and ga2_video's synchronous /4 transfer (clk_sys -> clk_vid) are timed as synchronous paths. [2] clk_v25 is declared
# asynchronous, as upstream (Arcade-SegaSystem32.sdc: the V25 crossings are synchronised in RTL). clk_74a is asynchronous
# to the core (data_loader's dcfifo, synch_3 and the quasi-static slot sizes, see d2_core_top.sv).
# ASSUMPTION: A1 the grouping itself. Source: design analysis of d2_core_top.sv / ga2_video.sv / p1_load_wq.sv (which
# crossings exist and how they are synchronised), upstream's clk_v25 group (Arcade-SegaSystem32.sdc), and the same
# clk_74a/clk_74b/bridge_spiclk grouping in the Jaguar and HW-001 SDCs. Those two built cores that ran on the Pocket, but
# the CDC correctness of THIS design is not proven by that. Wrong grouping = a real crossing not timed (async) or a
# non-crossing over-constrained (sync).
set_clock_groups -asynchronous \
    -group { clk_74a } \
    -group { clk_74b } \
    -group { bridge_spiclk } \
    -group { ic|mp|pll_inst|altera_pll_i|*[0].*|divclk \
             ic|mp|pll_inst|altera_pll_i|*[1].*|divclk \
             ic|mp|pll_inst|altera_pll_i|*[3].*|divclk \
             ic|mp|pll_inst|altera_pll_i|*[4].*|divclk } \
    -group { ic|mp|pll_inst|altera_pll_i|*[2].*|divclk }

# ---- CPU: NO two-cycle exception -----------------------------------------------------------------------------------------
# Upstream's set_multicycle_path -setup 2 on s32_v60:v60|* is for the original V60. Here that instance is V2
# (rtl/d2_v60_hw.sv), which steps every clk_sys edge: it is timed single-cycle, as in F1/F2/HW-001. V2 must be present.
# (Omission, not an assumption: single-cycle is the conservative reading; HW-001 ran V2 at 48.3 MHz on the Pocket.)
set v2_regs [get_registers -nowarn {*|s32_v60:v60|v2_core:core|*}]
d2_require [expr {[get_collection_size $v2_regs] > 0}] "V2 (s32_v60:v60|v2_core:core) registers"

# ---- copied from upstream (Arcade-SegaSystem32.sdc @ accd644): sprite state-exclusive two-cycle -------------------------
# ASSUMPTION: A2 upstream's claim that sw[0..6]/x0/y0 are loaded >= 2 clocks before they are consumed (state-exclusive).
# Source: MiSTer upstream SDC comments (Arcade-SegaSystem32.sdc lines 108-115), not a Pocket measurement and not re-proved
# in this repository. A wrong claim would hide a real single-cycle path (an exception STA cannot check).
set sprite_deferred_sources [get_registers -nowarn \
    {*|s32_sprite:sprite|sw[0][*] *|s32_sprite:sprite|sw[1][*] \
     *|s32_sprite:sprite|sw[2][*] *|s32_sprite:sprite|sw[3][*] \
     *|s32_sprite:sprite|sw[4][*] *|s32_sprite:sprite|sw[5][*] \
     *|s32_sprite:sprite|sw[6][*] *|s32_sprite:sprite|x0[*] \
     *|s32_sprite:sprite|y0[*]}]
set sprite_regs [get_registers -nowarn {*|s32_sprite:sprite|*}]
if {[d2_require [expr {[get_collection_size $sprite_deferred_sources] > 0 && [get_collection_size $sprite_regs] > 0}] "sprite registers"]} {
    set_multicycle_path -setup 2 -from $sprite_deferred_sources -to $sprite_regs
    set_multicycle_path -hold  1 -from $sprite_deferred_sources -to $sprite_regs
}

# ---- SDRAM interface ---------------------------------------------------------------------------------------------------
# ASSUMPTION: A3 (the generated clock) dram_clk_out = clk_ram PLL output [0], inverted, on the dram_clk port. Source: this
# repository's RTL (altddio_out datain_h 0 / datain_l 1) and the S2/D2 simulation chip model; the clock PIN name is
# unverified until a map run prints it. Not hardware data.
# ASSUMPTION: A4 the I/O delays 6.4 / 3.2 in, 1.5 / -0.8 out: upstream's numbers for a MiSTer SDRAM module (-7 grade),
# unchanged. No Pocket SDRAM board or part numbers are used.
# ASSUMPTION: A5 the CL2 capture multicycle 2 / 1 (SDRAM data captured one clk_ram period later than the nominal edge):
# upstream's, for upstream's sdram.sv capture scheme and a 180-degree forwarded clock; same source, same caveat.
# dram_clk = clk_ram inverted, forwarded by altddio_out (d2_core_top.sv sdramclk_ddr: datain_h 0, datain_l 1).
# Profile selected by the Tcl variable d2_sdram_io (set before this file is read; default "upstream"):
#   "upstream"  upstream's board+chip delays for a MiSTer SDRAM module (-7 grade) and its CL2 two-cycle capture
#               multicycle (Arcade-SegaSystem32.sdc:57-83). ASSUMPTION: these are NOT Pocket board numbers (none are
#               published; the Pocket SDRAM is 1.8 V I/O per the template ap_core.qsf). Used to see how much margin the
#               sdram.sv capture point has against a plausible budget.
#   "none"      the Jaguar core's arrangement: no SDRAM I/O delays (pocket_jaguar core_constraints.sdc explains why);
#               empirically good at 106.4 MHz on this board with FAST_INPUT/OUTPUT_REGISTER (K(J)). I/O paths unconstrained.
# OWNER DECISION 2026-11-05: the first D2 fit uses the "upstream" profile. Its SDRAM I/O delay and multicycle values (A4, A5)
# are UNVERIFIED Pocket-board assumptions; their slack is informational, not a hardware qualification.
if {![info exists ::d2_sdram_io]} { set ::d2_sdram_io "upstream" }
set dram_fwd [get_pins -nowarn -compatibility_mode {ic|sdramclk_ddr|*|dataout}]
set dram_src [get_clocks -nowarn {ic|mp|pll_inst|altera_pll_i|*[0].*|divclk}]
if {$::d2_sdram_io eq "upstream" && [d2_require [expr {[get_collection_size $dram_src] == 1}] "clk_ram PLL output clock"]} {
    create_generated_clock -name dram_clk_out -source [get_pins -compatibility_mode {ic|mp|pll_inst|altera_pll_i|*[0].*|divclk}] \
        -invert [get_ports dram_clk]
    set_input_delay  -clock dram_clk_out -max 6.4 [get_ports {dram_dq[*]}]
    set_input_delay  -clock dram_clk_out -min 3.2 [get_ports {dram_dq[*]}]
    set_output_delay -clock dram_clk_out -max 1.5 [get_ports {dram_a[*] dram_ba[*] dram_dq[*] dram_dqm[*] dram_ras_n dram_cas_n dram_we_n dram_cke}]
    set_output_delay -clock dram_clk_out -min -0.8 [get_ports {dram_a[*] dram_ba[*] dram_dq[*] dram_dqm[*] dram_ras_n dram_cas_n dram_we_n dram_cke}]
    set_multicycle_path -setup -end -from [get_clocks dram_clk_out] -to $dram_src 2
    set_multicycle_path -hold  -end -from [get_clocks dram_clk_out] -to $dram_src 1
}

# ---- SRAM interface (asynchronous 128K x 16; work RAM) -----------------------------------------------------------------
# All SRAM pins are registers in clk_sys (d2_sram_port; FAST_*_REGISTER in the QSF). A read is sampled RD_EDGES = 3 clk_sys
# (62.1 ns) after the edge that registers the address and OE. ASSUMPTION (K(J) measurement, not a datasheet): the Jaguar
# core measured clean data 19 ns after the address register's edge at 3.3-V LVTTL 8 mA (ENGINEERING_LOG #42), i.e. the
# FPGA output delay + board + SRAM access together; expressed as an input delay against clk_sys with a 3-cycle setup
# window. Outputs carry no delay requirement beyond the clock-to-out report (writes hold address/data a full cycle around
# the 2-cycle WE pulse). Informational: the sim already shows the window (S2: margin >= 22 ns at 2 edges).
# ASSUMPTION: A6 (max 19.0 ns): Jaguar ENGINEERING_LOG #42 measurement at 3.3-V LVTTL 8 mA, used here as the input delay
# against clk_sys; it is a clean-read time seen by Jaguar's own register, not a board+device SDC delay (see above), and the
# SRAM part is not identified in this repository.
# ASSUMPTION: A7 (min 0.0 ns): no source. It means "data may change right after the launching edge" (no hold credit); the
# real SRAM output hold (tOH) is unknown here.
# ASSUMPTION: A8 (multicycle 3 / 2): RD_EDGES = 3 in rtl/derived/d2_sram_port.sv, i.e. this design's own sampling schedule,
# verified in simulation only (S2/D2 SRAM model), not on the Pocket.
set sys_clk [get_clocks -nowarn {ic|mp|pll_inst|altera_pll_i|*[1].*|divclk}]
if {[d2_require [expr {[get_collection_size $sys_clk] == 1}] "clk_sys PLL output clock"]} {
    set_input_delay -clock $sys_clk -max 19.0 [get_ports {sram_dq[*]}]
    set_input_delay -clock $sys_clk -min 0.0  [get_ports {sram_dq[*]}]
    set_multicycle_path -setup -end -from [get_ports {sram_dq[*]}] -to [get_registers -nowarn {*|d2_sram_port:sram_port|q[*]}] 3
    set_multicycle_path -hold  -end -from [get_ports {sram_dq[*]}] -to [get_registers -nowarn {*|d2_sram_port:sram_port|q[*]}] 2
}

# ---- SRAM outputs: ASSUMPTION-BASED, quartus_sta ONLY, informational (never read by the fitter) --------------------------
# Why: without these, the SRAM output pins have no output-delay constraint, so their slack is "NA" (PLL diagnostic, run
# 42116132593) and nothing says whether A / DQ / UB / LB are valid long enough before WE rises. They are applied ONLY when the
# executable is quartus_sta ($::TimeQuestInfo(nameofexecutable)), so quartus_map and quartus_fit never see them and cannot
# steer placement or routing by them: the fitted netlist is the same with or without this block.
# Cycle structure (rtl/derived/d2_sram_port.sv, one clk_sys = 20.696 ns), write: E1 registers A, DQ (driven), UB/LB; E2 WE falls;
# E4 WE rises (WE low 2 clk_sys = 41.4 ns); E5 DQ released and UB/LB deasserted. So A, UB/LB and DQ are launched 3 clk_sys
# (62.1 ns) before the write-end edge (WE rising, E4) and stay stable 1 clk_sys after it. Modelled as: output delay = the SRAM's
# required setup before the write end, setup multicycle 3 / hold multicycle 2 from clk_sys (the standard pair: hold is then
# checked against the same launch edge, which is conservative by one cycle for this port). The write-end edge itself (WE's own
# clock-to-out) is not subtracted and board skew between pins is not modelled: STA only sees the pin against clk_sys.
# NOT modelled (STA cannot check these two registered-output relations): address setup to WE falling (tAS, E1 -> E2, 1 clk_sys)
# and the WE pulse width (tWP: 41.4 ns nominal here); a part needing more than that needs a longer WE pulse in d2_sram_port.
# ASSUMPTION: A10 write-end setup/hold of a GENERIC asynchronous 128K x 16 SRAM: address and byte enables (UB/LB) valid 45.0 ns
# before the write end (tAW / tBW), write data valid 25.0 ns before it (tDW), hold after the write end 0.0 ns (tAH / tDH).
# Source: representative figures of 55-ns-class asynchronous SRAM datasheets, from memory, NOT checked against a datasheet in
# this repository and NOT any Pocket part (its SRAM is not identified here): a deliberately conservative generic profile
# (10-12-ns-class parts need far less). No board trace delay and no Pocket SRAM part number is used. Window: 3 clk_sys, from
# this design's own schedule (simulation only).
# ASSUMPTION: A11 the strobes sram_oe_n / sram_we_n get output delay 0.0 / 0.0 ns with the default single-cycle relation: they
# are the reference edges (OE only matters for reads, WE is the write-end reference), so the report shows their clock-to-out
# margin only; the data-to-strobe skew they define is exactly what A10 assumes away and is not measured here.
if {$::TimeQuestInfo(nameofexecutable) eq "quartus_sta" && [d2_require [expr {[get_collection_size $sys_clk] == 1}] "clk_sys PLL output clock (SRAM outputs)"]} {
    set sram_ad_ports [get_ports -nowarn {sram_a[*] sram_ub_n sram_lb_n}]
    set sram_dq_ports [get_ports -nowarn {sram_dq[*]}]
    set sram_st_ports [get_ports -nowarn {sram_oe_n sram_we_n}]
    set_output_delay -clock $sys_clk -max 45.0 $sram_ad_ports
    set_output_delay -clock $sys_clk -min  0.0 $sram_ad_ports
    set_output_delay -clock $sys_clk -max 25.0 $sram_dq_ports
    set_output_delay -clock $sys_clk -min  0.0 $sram_dq_ports
    set_multicycle_path -setup -end -from $sys_clk -to $sram_ad_ports 3
    set_multicycle_path -hold  -end -from $sys_clk -to $sram_ad_ports 2
    set_multicycle_path -setup -end -from $sys_clk -to $sram_dq_ports 3
    set_multicycle_path -hold  -end -from $sys_clk -to $sram_dq_ports 2
    set_output_delay -clock $sys_clk -max 0.0 $sram_st_ports
    set_output_delay -clock $sys_clk -min 0.0 $sram_st_ports
    post_message -type info "D2 SDC: SRAM output delays A10/A11 applied (quartus_sta only; assumption-based, informational)"
}

# ---- fitter-only hold margin (HW-001's remedy, run 40375644108), clk_sys -> clk_sys -------------------------------------
# ASSUMPTION: A9 +0.050 ns: HW-001's value, chosen from one fitter result (a -0.010 ns hold path), not board or silicon
# data. Fitter-only: sign-off STA is unchanged, so it makes no claim about the hardware.
if {($::TimeQuestInfo(nameofexecutable) eq "quartus_fit") && [get_collection_size $sys_clk] > 0} {
    set_clock_uncertainty -hold -add -enable_same_physical_edge -from $sys_clk -to $sys_clk 0.050
    post_message -type info "D2: fitter hold margin request +0.050 ns on clk_sys"
}
