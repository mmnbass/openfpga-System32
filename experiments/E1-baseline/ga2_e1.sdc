# SPDX-License-Identifier: GPL-3.0-or-later
#
# E1 timing constraints. Clocks are top-level ports here instead of PLL
# outputs; the core-specific exceptions are copied from upstream
# references/s32/Arcade-SegaSystem32.sdc @ accd644 with only the object
# lookups adapted. Omitted on purpose, because their logic is not in E1:
#   * MiSTer OSD multicycles (sys/osd.v is not compiled)
#   * SDRAM_CLK board I/O constraints (no SDRAM pins; sdram.sv is not in E1)
#
# Frequencies are the upstream PLL's generated values:
#   clk_ram 96.634615 MHz, clk_sys 48.317307 MHz (outclk0/outclk1, same PLL,
#   rising edges aligned), clk_v25 24.158653 MHz (outclk3, declared
#   asynchronous by upstream).

create_clock -name clk_ram -period 10.348 [get_ports clk_ram]
create_clock -name clk_sys -period 20.697 [get_ports clk_sys]
create_clock -name clk_v25 -period 41.393 [get_ports clk_v25]
derive_clock_uncertainty

# Upstream: "Declaring clk_v25 asynchronous to the rest false-paths" the V25
# crossings, which the RTL handles with toggle synchronisers and a TDP RAM.
set_clock_groups -asynchronous -group [get_clocks clk_v25]

# Unlike upstream, a missing register set is an error at every stage after
# synthesis, so a renamed hierarchy cannot silently drop an exception.
proc e1_require {present what} {
    if {$present} { return 1 }
    if {[string match "quartus_map" $::quartus(nameofexecutable)]} {
        post_message -type warning "E1 SDC: $what not elaborated yet"
        return 0
    }
    error "E1 SDC: expected $what but it is missing"
}

# --- Copied: V60 fixed-CE two-cycle constraint -----------------------------
set v60_regs [get_registers -nowarn {*|s32_v60:v60|*}]
if {[e1_require [expr {[get_collection_size $v60_regs] > 0}] "V60 registers"]} {
    set_multicycle_path -setup 2 -from $v60_regs -to $v60_regs
    set_multicycle_path -hold 1 -from $v60_regs -to $v60_regs
    set v60_fp_a [get_registers -nowarn {*|s32_v60:v60|fp_a[*]}]
    if {[get_collection_size $v60_fp_a] > 0} {
        # S32_V60_NO_FP is defined, so this branch should never run.
        post_message -type critical_warning "E1 SDC: fp_a registers present although S32_V60_NO_FP is set"
        set_multicycle_path -setup 3 -from $v60_fp_a -to $v60_regs
        set_multicycle_path -hold 2 -from $v60_fp_a -to $v60_regs
    }
}

# --- Copied: sprite state-exclusive two-cycle constraint -------------------
set sprite_deferred_sources [get_registers -nowarn \
    {*|s32_sprite:sprite|sw[0][*] *|s32_sprite:sprite|sw[1][*] \
     *|s32_sprite:sprite|sw[2][*] *|s32_sprite:sprite|sw[3][*] \
     *|s32_sprite:sprite|sw[4][*] *|s32_sprite:sprite|sw[5][*] \
     *|s32_sprite:sprite|sw[6][*] *|s32_sprite:sprite|x0[*] \
     *|s32_sprite:sprite|y0[*]}]
set sprite_regs [get_registers -nowarn {*|s32_sprite:sprite|*}]
if {[e1_require [expr {[get_collection_size $sprite_deferred_sources] > 0 && \
                       [get_collection_size $sprite_regs] > 0}] "sprite registers"]} {
    set_multicycle_path -setup 2 -from $sprite_deferred_sources -to $sprite_regs
    set_multicycle_path -hold  1 -from $sprite_deferred_sources -to $sprite_regs
}

# --- Real V25 must be present (GA2 baseline requirement) -------------------
set v25_regs [get_registers -nowarn {*|s32_v25_cpu:v25|*}]
e1_require [expr {[get_collection_size $v25_regs] > 0}] "real V25 (s32_v25_cpu) registers"

# --- Measurement scaffolding only (configuration "sr") ---------------------
# The scan chains are not part of the design under test.
set sr_chain [get_registers -nowarn {in_chain[*] out_chain[*]}]
if {[get_collection_size $sr_chain] > 0} {
    set_false_path -from [get_registers {in_chain[*]}]
    set_false_path -to   [get_registers {out_chain[*]}]
}
