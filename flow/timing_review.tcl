# Additional IHP timing diagnostics, sourced after a corner's design, SDC and
# parasitics have been loaded. Reports are evidence for review, not a pass flag.
# Keep placement estimates separate from final extracted timing and MTBF.
source [file join [file dirname [info script]] .. src timing_checks.tcl]

puts "%OL_CREATE_REPORT synchronizer_stages.rpt"
puts "Synchronizer startpoints resolved through keeper inversions"
set patterns {
    {rst_s\[0\]}
    {.*\.a_s\[0\]} {.*\.w_s\[0\]} {.*\.r_s\[0\]}
    {.*\.cs_s\[0\]} {.*\.rx_n1} {.*\.rx_hq}
}
for {set bit 0} {$bit < 13} {incr bit} {
    lappend patterns [format {.*\.sync1\[%d\]} $bit]
}
foreach pattern $patterns {
    set nets [get_nets -regexp $pattern]
    puts "Synchronizer net pattern $pattern; found [llength $nets]"
    if {[llength $nets] != 1} {
        error "missing or ambiguous synchronizer net: $pattern"
    }
    # get_fanin returns register clock pins as startpoints in OpenSTA 2.7.
    # This also finds the real flop when a kept net is an unused inverter
    # output and the live circuit uses the flop's complemented output instead.
    set starts [get_fanin -to $nets -flat -startpoints_only]
    if {[llength $starts] != 1} {
        error "missing or ambiguous synchronizer register: $pattern"
    }
    foreach pin $starts {
        puts "Resolved startpoint [get_property $pin full_name]"
        if {![string match */CLK [get_property $pin full_name]]} {
            error "synchronizer startpoint is not a register clock pin"
        }
    }
    report_checks -from $starts -path_delay max -format full_clock_expanded \
        -fields {slew cap input net fanout} -group_path_count 10 -corner $corner_name
}
puts "%OL_END_REPORT"

set icg_enables {}
set latch_data {}
set clock_pins [all_registers -clock_pins]
foreach cell [get_cells * -hierarchical] {
    set ref [get_property $cell ref_name]
    if {$ref eq "RM_IHPSG13_1P_256x32_c2_bm_bist"} {
        foreach pin [get_pins -of_objects $cell] {
            if {[string match */A_CLK [get_property $pin full_name]]} {
                lappend clock_pins $pin
            }
        }
    }
    if {$ref eq "sg13cmos5l_lgcp_1" || $ref eq "sg13cmos5l_dlhq_1"} {
        foreach pin [get_pins -of_objects $cell] {
            set name [get_property $pin full_name]
            if {$ref eq "sg13cmos5l_lgcp_1" && [string match */GATE $name]} {
                lappend icg_enables $pin
            } elseif {$ref eq "sg13cmos5l_dlhq_1" && [string match */D $name]} {
                lappend latch_data $pin
            }
        }
    }
}
if {[llength $icg_enables] == 0 || [llength $latch_data] == 0} {
    error "missing IHP clock-gate or FIFO latch timing targets"
}
puts "%OL_CREATE_REPORT integrated_gate_enables.rpt"
puts "ICG enable pin count [llength $icg_enables]"
# IHP lgcp has setup_rising/hold_rising enable arcs. An empty report from
# report_check_types -clock_gating_setup/-clock_gating_hold alone is insufficient.
report_checks -to $icg_enables -path_delay min_max -format full_clock_expanded \
    -fields {slew cap input net fanout} -group_path_count 1000 -corner $corner_name
puts "%OL_END_REPORT"

puts "%OL_CREATE_REPORT clock_pulse_widths.rpt"
set clock_pins [lsort -unique $clock_pins]
puts "Clock pulse-width target count [llength $clock_pins]"
if {[llength $clock_pins] == 0} {error "missing clock pulse-width targets"}
# Retain targets and library identities. Native controls print passing and
# failing margins, but the candidate's cross-clock exceptions suppress this
# report in the recorded OpenSTA 2.7 image. Run pulse_width_review.tcl in a
# separate clock-only process; an empty report here is not a verified pass.
foreach pin $clock_pins {puts "Pulse-width pin [get_property $pin full_name]"}
report_pulse_width_checks -verbose -corner $corner_name $clock_pins
puts "%OL_END_REPORT"

puts "%OL_CREATE_REPORT fifo_latch_timing.rpt"
puts "FIFO latch data pin count [llength $latch_data]"
# Latch borrowing can give exactly zero setup slack. Inspect the actual latch
# paths and window, along with ordinary corner setup/hold and pulse reports.
report_checks -to $latch_data -path_delay min_max -format full_clock_expanded \
    -fields {slew cap input net fanout} -group_path_count 2048 -corner $corner_name
puts "%OL_END_REPORT"
