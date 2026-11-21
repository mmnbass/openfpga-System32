# SPDX-License-Identifier: GPL-3.0-or-later
# D2 fit: slacks per corner, internal vs I/O, for tools/d2_gate.py. Derived from experiments/PLL-diag/pocket/pll_timing.tcl (same API
# calls, which ran under this toolchain in run 42116132593) and keeps its PLLT_* line formats so tools/pll_gate.py's parser is
# reused unchanged. QUARTUS-ONLY: never executed against a real D2 netlist; tools/d2_tcl_mock.tcl runs it under plain tclsh.
#
# quartus_sta's own summary lumps the dram_dq / sram_dq input paths and the assumed I/O delays into the destination clock's
# setup slack, so it cannot separate "internal clock-domain slack" (a criterion) from "slack under ASSUMED board delays"
# (informational). This script asks for them separately, per corner.
#
# Output lines (tab-free unless noted; one fact per line; nothing else is interpreted):
#   PLLT_CLOCK <clock name> <period ns>
#   PLLT_INT   <corner> <setup|hold|recovery|removal> <to-clock name> <worst slack ns | NA>   register-to-register only
#   PLLT_IO    <corner> <dram_out|dram_in|sram_out|sram_in> <setup|hold> <worst slack ns | NA>   pin paths, ASSUMED board delays
#   D2T_SRAMOUT <corner> <ad|dq|strobe> <setup|hold> <worst slack ns | NA>   the SRAM-output group split (A10 ad+dq, A11 strobes)
#   D2T_PATH <TAB> <corner> <TAB> <slack> <TAB> <to node> <TAB> <from node>   the 5 worst register-to-register setup paths per corner
#   D2T_HOLD <TAB> <corner> <TAB> <slack> <TAB> <to> <TAB> <from> <TAB> <launch clk> <TAB> <latch clk> <TAB> <clock skew> <TAB> <data delay> <TAB> <logic levels>
#                                the 10 worst register-to-register HOLD paths per corner (added after run 42126911324's unnamed
#                                -0.002 ns clk_ram hold: endpoint, clocks, skew and data delay classify it as same-clock / crossing)
#   D2T_HOLDCLK <TAB> <same fields>   the 5 worst hold paths latched by clk_ram with NO node filter (also RAM/DSP/IO endpoints)
#   D2T_CORE <TAB> <corner> <TAB> <setup> <TAB> <slack> <TAB> <to> <TAB> <from> <TAB> <latch clk>   the 5 worst setup paths ending
#                                in s32_core (the GA2 core) latched by clk_ram, and the 5 worst ending in s32_core on any clock
#   PLLT_ERR   <message>        a section failed (the gate treats a missing row as an instrumentation failure, never as a pass)
#   PLLT_DONE                    printed last, only after every corner was visited
#
# The SRAM-output rows are constrained only by quartus_sta (SDC A10/A11, assumption-based): this script runs inside quartus_sta,
# so read_sdc applies them; the fitter never saw them. NA there means the constraint did not apply or no path exists.
#
# Usage, from the project directory (build/D2/seed<N>):   quartus_sta -t ../../../experiments/D2-pocket-top/pocket/d2_timing.tcl ga2_d2
set rev [lindex $quartus(args) 0]
if {$rev eq ""} { set rev "ga2_d2" }
project_open -revision $rev $rev
create_timing_netlist
read_sdc
update_timing_netlist

if {[catch {
    foreach_in_collection c [get_clocks] {
        set nm [get_clock_info -name $c]
        puts "PLLT_CLOCK $nm [get_clock_info -period $c]"
    }
} err]} { puts "PLLT_ERR clocks: $err" }

# worst slack of one get_timing_paths query, or NA when it returns no path
proc worst {opts} {
    set best NA
    if {[catch {
        foreach_in_collection p [get_timing_paths {*}$opts -npaths 1] {
            set s [get_path_info -slack $p]
            if {$best eq "NA" || $s < $best} { set best $s }
        }
    } err]} {
        puts "PLLT_ERR query {$opts}: $err"
        return ERR
    }
    return $best
}

set ioports(dram_out) {dram_a[*] dram_ba[*] dram_dqm[*] dram_ras_n dram_cas_n dram_we_n dram_cke dram_dq[*]}
set ioports(sram_out) {sram_a[*] sram_oe_n sram_we_n sram_ub_n sram_lb_n sram_dq[*]}
set ioports(dram_in)  {dram_dq[*]}
set ioports(sram_in)  {sram_dq[*]}
set sramsplit(ad)     {sram_a[*] sram_ub_n sram_lb_n}
set sramsplit(dq)     {sram_dq[*]}
set sramsplit(strobe) {sram_oe_n sram_we_n}

foreach c [get_available_operating_conditions] {
    if {[catch {
        set_operating_conditions $c
        update_timing_netlist
        set regs [get_registers *]
        foreach_in_collection clk [get_clocks] {
            set nm [get_clock_info -name $clk]
            foreach kind {setup hold recovery removal} {
                puts "PLLT_INT $c $kind $nm [worst [list -$kind -to_clock $nm -from $regs -to $regs]]"
            }
        }
        foreach grp {dram_out sram_out} {
            set ports [get_ports -nowarn $ioports($grp)]
            foreach kind {setup hold} {
                puts "PLLT_IO $c $grp $kind [worst [list -$kind -from $regs -to $ports]]"
            }
        }
        foreach grp {dram_in sram_in} {
            set ports [get_ports -nowarn $ioports($grp)]
            foreach kind {setup hold} {
                puts "PLLT_IO $c $grp $kind [worst [list -$kind -from $ports -to $regs]]"
            }
        }
        foreach sub {ad dq strobe} {
            set ports [get_ports -nowarn $sramsplit($sub)]
            foreach kind {setup hold} {
                puts "D2T_SRAMOUT $c $sub $kind [worst [list -$kind -from $regs -to $ports]]"
            }
        }
        # clk_ram = the PLL output 0 clock
        set ramclk ""
        foreach_in_collection clk [get_clocks] {
            set nm [get_clock_info -name $clk]
            if {[string match {*general\[0\]*PLL_OUTPUT_COUNTER*divclk} $nm]} { set ramclk $nm }
        }
        # the 10 worst hold paths, register to register, any clock; and the 5 worst latched by clk_ram, no node filter
        foreach {tag q} [list D2T_HOLD [list -hold -npaths 10 -from $regs -to $regs -detail path_only] \
                              D2T_HOLDCLK [list -hold -npaths 5 -to_clock $ramclk -detail path_only]] {
            if {$tag eq "D2T_HOLDCLK" && $ramclk eq ""} { puts "PLLT_ERR $tag $c: clk_ram clock not found"; continue }
            if {[catch {
                foreach_in_collection p [get_timing_paths {*}$q] {
                    set from "?"; set to "?"; set fc "?"; set tc "?"; set sk NA; set dd NA; set ll NA
                    catch { set from [get_node_info -name [get_path_info -from $p]] }
                    catch { set to   [get_node_info -name [get_path_info -to   $p]] }
                    catch { set fc [get_clock_info -name [get_path_info -from_clock $p]] }
                    catch { set tc [get_clock_info -name [get_path_info -to_clock $p]] }
                    catch { set sk [get_path_info -clock_skew $p] }
                    catch { set dd [get_path_info -data_delay $p] }
                    catch { set ll [get_path_info -num_logic_levels $p] }
                    puts "$tag\t$c\t[get_path_info -slack $p]\t$to\t$from\t$fc\t$tc\t$sk\t$dd\t$ll"
                }
            } herr]} { puts "PLLT_ERR $tag $c: $herr" }
        }
        # the GA2 core's own worst setup paths: latched by clk_ram, and on any clock (excludes the verifier and the Pocket top)
        set coreregs [get_registers -nowarn {*|s32_core:core|*}]
        foreach {lbl q} [list clk_ram [list -setup -npaths 5 -to_clock $ramclk -from $regs -to $coreregs -detail path_only] \
                              any     [list -setup -npaths 5 -from $regs -to $coreregs -detail path_only]] {
            if {$lbl eq "clk_ram" && $ramclk eq ""} { continue }
            if {[catch {
                foreach_in_collection p [get_timing_paths {*}$q] {
                    set from "?"; set to "?"; set tc "?"
                    catch { set from [get_node_info -name [get_path_info -from $p]] }
                    catch { set to   [get_node_info -name [get_path_info -to   $p]] }
                    catch { set tc [get_clock_info -name [get_path_info -to_clock $p]] }
                    puts "D2T_CORE\t$c\t$lbl\t[get_path_info -slack $p]\t$to\t$from\t$tc"
                }
            } cerr]} { puts "PLLT_ERR D2T_CORE $lbl $c: $cerr" }
        }
        # the 5 worst register-to-register setup paths (informational "top critical paths")
        if {[catch {
            foreach_in_collection p [get_timing_paths -setup -npaths 5 -from $regs -to $regs -detail path_only] {
                set from "?"; set to "?"
                catch { set from [get_node_info -name [get_path_info -from $p]] }
                catch { set to   [get_node_info -name [get_path_info -to   $p]] }
                puts "D2T_PATH\t$c\t[get_path_info -slack $p]\t$to\t$from"
            }
        } perr]} { puts "PLLT_ERR paths $c: $perr" }
    } err]} {
        puts "PLLT_ERR corner $c: $err"
    }
}
puts "PLLT_DONE"
project_close
