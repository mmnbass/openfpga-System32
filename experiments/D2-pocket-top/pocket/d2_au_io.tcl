# SPDX-License-Identifier: GPL-3.0-or-later
# AU (exp/audio1): the FPGA's own delays on the PSRAM chip-0 pins of the au fit, per operating corner, INFORMATIONAL (no pass/fail;
# tools/d2_gate.py does not read it). Pattern of experiments/PSRAM-probe/pocket/probe_io_delays.tcl (HW-002's probe report), for the
# one-clock gaps d2_wave_psram's protocol relies on (pocket/d2_au_constraints.sdc AU4): the slowest / fastest register-to-pin data delay
# per pin group, the cram0_dq -> sdr_p4_dout input path (AU1-AU3) and its constrained setup / hold slack.
# Output lines:
#   D2AUIO <corner> <group> <max|min> <ns | NA>      register (d2_wave_psram:wvps) -> cram0 port data delay
#   D2AUIN <corner> <max|min> <ns | NA>              cram0_dq port -> d2_wave_psram:wvps|sdr_p4_dout data delay
#   D2AUSLACK <corner> <setup|hold> <ns | NA>        worst slack of the constrained cram0_dq input paths
#   D2AU_DONE <corner> / D2AU_FAILED <corner>: <message>
# Usage, from the project directory:  quartus_sta -t ../../../experiments/D2-pocket-top/pocket/d2_au_io.tcl ga2_d2
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
                addr_hi  "*|d2_wave_psram:wvps|cram_a\[*"     "cram0_a*" \
                dq_data  "*|d2_wave_psram:wvps|dq_o\[*"       "cram0_dq*" \
                dq_oe    "*|d2_wave_psram:wvps|dq_oe*"        "cram0_dq*" \
                adv_n    "*|d2_wave_psram:wvps|cram_adv_n*"   "cram0_adv_n" \
                ce0_n    "*|d2_wave_psram:wvps|cram_ce0_n*"   "cram0_ce0_n" \
                oe_n     "*|d2_wave_psram:wvps|cram_oe_n*"    "cram0_oe_n" \
                we_n     "*|d2_wave_psram:wvps|cram_we_n*"    "cram0_we_n" \
                ub_lb    "*|d2_wave_psram:wvps|cram_?b_n*"    "cram0_?b_n"] {
            set regs [get_registers -nowarn $from]
            set ports [get_ports -nowarn $to]
            foreach kind {max min} {
                if {[get_collection_size $regs] == 0 || [get_collection_size $ports] == 0} { puts "D2AUIO $tag $grp $kind NA"; continue }
                puts "D2AUIO $tag $grp $kind [pdelay $regs $ports $kind]"
            }
        }
        set dqp [get_ports -nowarn {cram0_dq[*]}]
        set q   [get_registers -nowarn {*|d2_wave_psram:wvps|sdr_p4_dout[*]}]
        foreach kind {max min} {
            if {[get_collection_size $dqp] == 0 || [get_collection_size $q] == 0} { puts "D2AUIN $tag $kind NA"; continue }
            puts "D2AUIN $tag $kind [pdelay $dqp $q $kind]"
        }
        foreach kind {setup hold} {
            if {[get_collection_size $dqp] == 0 || [get_collection_size $q] == 0} { puts "D2AUSLACK $tag $kind NA"; continue }
            puts "D2AUSLACK $tag $kind [wslack [list -$kind -from $dqp -to $q]]"
        }
        catch {report_path -from [get_registers -nowarn {*|d2_wave_psram:wvps|*}] -to [get_ports -nowarn {cram0_*}] -npaths 40 \
               -panel_name "au cram0 out $c" -file "au_io_${tag}_out.rpt"}
        puts "D2AU_DONE $tag"
    } err]} {
        puts "D2AU_FAILED $c: $err"
    }
}
project_close
