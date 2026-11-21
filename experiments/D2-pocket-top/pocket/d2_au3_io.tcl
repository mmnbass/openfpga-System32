# SPDX-License-Identifier: GPL-3.0-or-later
# AU3 (exp/audio3, GA2Audio3): pocket/d2_au_io.tcl for PSRAM chip 1 (cram1, rtl/d2_rom_psram.sv instance rmps), INFORMATIONAL (no
# pass/fail; tools/d2_gate.py does not read it): the slowest / fastest register-to-pin data delay per pin group, the cram1_dq ->
# sdr_p3_dout input path (pocket/d2_au3_constraints.sdc AR1-AR3) and its constrained setup / hold slack. Same lines as d2_au_io.tcl with
# the prefix D2AU3 (D2AU3IO / D2AU3IN / D2AU3SLACK / D2AU3_DONE / D2AU3_FAILED); report files au3_io_<corner>_out.rpt.
# Usage, from the project directory:  quartus_sta -t ../../../experiments/D2-pocket-top/pocket/d2_au3_io.tcl ga2_d2
set rev [lindex $quartus(args) 0]
if {$rev eq ""} { set rev "ga2_d2" }
project_open -revision $rev $rev
create_timing_netlist
read_sdc
update_timing_netlist

proc pdelay {from to kind} {
    set opts [list -from $from -to $to -npaths 100]
    if {$kind eq "min"} { lappend opts -min_path }
    set best "NA"
    foreach_in_collection p [get_path {*}$opts] {
        set d [get_path_info $p -data_delay]
        if {$best eq "NA" || ($kind eq "max" && $d > $best) || ($kind eq "min" && $d < $best)} { set best $d }
    }
    return $best
}
proc wslack {opts} {
    set best "NA"
    foreach_in_collection p [get_timing_paths {*}$opts -npaths 1] {
        set s [get_path_info -slack $p]
        if {$best eq "NA" || $s < $best} { set best $s }
    }
    return $best
}

foreach c [get_available_operating_conditions] {
    if {[catch {
        set_operating_conditions $c
        update_timing_netlist
        regsub -all {[^A-Za-z0-9]+} $c "_" tag
        foreach {grp from to} [list \
                addr_hi  "*|d2_rom_psram:rmps|cram_a\[*"     "cram1_a*" \
                dq_data  "*|d2_rom_psram:rmps|dq_o\[*"       "cram1_dq*" \
                dq_oe    "*|d2_rom_psram:rmps|dq_oe*"        "cram1_dq*" \
                adv_n    "*|d2_rom_psram:rmps|cram_adv_n*"   "cram1_adv_n" \
                ce0_n    "*|d2_rom_psram:rmps|cram_ce0_n*"   "cram1_ce0_n" \
                oe_n     "*|d2_rom_psram:rmps|cram_oe_n*"    "cram1_oe_n" \
                we_n     "*|d2_rom_psram:rmps|cram_we_n*"    "cram1_we_n" \
                ub_lb    "*|d2_rom_psram:rmps|cram_?b_n*"    "cram1_?b_n"] {
            set regs [get_registers -nowarn $from]
            set ports [get_ports -nowarn $to]
            foreach kind {max min} {
                if {[get_collection_size $regs] == 0 || [get_collection_size $ports] == 0} { puts "D2AU3IO $tag $grp $kind NA"; continue }
                puts "D2AU3IO $tag $grp $kind [pdelay $regs $ports $kind]"
            }
        }
        set dqp [get_ports -nowarn {cram1_dq[*]}]
        set q   [get_registers -nowarn {*|d2_rom_psram:rmps|sdr_p3_dout[*]}]
        foreach kind {max min} {
            if {[get_collection_size $dqp] == 0 || [get_collection_size $q] == 0} { puts "D2AU3IN $tag $kind NA"; continue }
            puts "D2AU3IN $tag $kind [pdelay $dqp $q $kind]"
        }
        foreach kind {setup hold} {
            if {[get_collection_size $dqp] == 0 || [get_collection_size $q] == 0} { puts "D2AU3SLACK $tag $kind NA"; continue }
            puts "D2AU3SLACK $tag $kind [wslack [list -$kind -from $dqp -to $q]]"
        }
        catch {report_path -from [get_registers -nowarn {*|d2_rom_psram:rmps|*}] -to [get_ports -nowarn {cram1_*}] -npaths 40 \
               -panel_name "au3 cram1 out $c" -file "au3_io_${tag}_out.rpt"}
        puts "D2AU3_DONE $tag"
    } err]} {
        puts "D2AU3_FAILED $c: $err"
    }
}
project_close
