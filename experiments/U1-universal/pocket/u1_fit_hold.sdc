
# ---- U1b, univ configuration only: fitter-only hold margin, clk_sys -> clk_ram ------------------------------------------
# Appended to the core SDC by experiments/D2-pocket-top/pocket/prepare_quartus_d2.py for --config univ ONLY (this file is
# experiments/U1-universal/pocket/u1_fit_hold.sdc); the GA2 (sp) and Spider-Man (spid) projects keep the shared
# d2_core_constraints.sdc byte for byte. Same command form as A9 above, on the other transfer.
# ASSUMPTION: A12 +0.050 ns on the clk_sys -> clk_ram transfer (PLL outputs [1] -> [0]): A9's value (HW-001), extended to the
# transfer that holds the worst fast 0 C hold path in 9 of the 10 GA2 / univ fits and failed sign-off in both univ fits (-0.016 ns,
# run 43616700674; -0.029 / -0.005 ns, run 43630851844; experiments/U1-universal/U1B.md section 11). A fitter target, not board or
# silicon data. Fitter-only: quartus_map and quartus_sta evaluate nothing in this block, so the sign-off hold requirement is unchanged
# and it makes no claim about the hardware. -hold: setup and recovery checks are unchanged. -add: added to derive_clock_uncertainty's
# value, never replacing it. The two clocks must resolve in the fitter (d2_require errors otherwise), so the block can never be a
# silent no-op; its message is what the univ post-fit gate (experiments/U1-universal/ci/u1_budget_gate.py B3) requires in fit.log.
if {$::TimeQuestInfo(nameofexecutable) eq "quartus_fit"} {
    set u1_ram_clk [get_clocks -nowarn {ic|mp|pll_inst|altera_pll_i|*[0].*|divclk}]
    d2_require [expr {[get_collection_size $sys_clk] == 1 && [get_collection_size $u1_ram_clk] == 1}] "clk_sys and clk_ram PLL output clocks (U1 A12)"
    set_clock_uncertainty -hold -add -enable_same_physical_edge -from $sys_clk -to $u1_ram_clk 0.050
    post_message -type info "U1: fitter hold margin request +0.050 ns on clk_sys -> clk_ram (A12)"
}
