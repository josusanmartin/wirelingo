# Run only in a separate diagnostic STA process after loading the candidate's
# exact libraries, design, SDC and parasitics. Never use as the regular flow's
# STA_EXTRA_CORNER_TCL_FILE: this process is for clock pulse widths only.
if {![info exists ::env(PROTOEMU_CLOCK_ONLY_PULSE_REVIEW)] ||
    $::env(PROTOEMU_CLOCK_ONLY_PULSE_REVIEW) ne "1" ||
    ![info exists ::env(PROTOEMU_ORIGINAL_SDC)] ||
    ![info exists ::env(_SDC_IN)]} {
    error "separate clock-only pulse analysis requires opt-in and both SDC paths"
}
if {[llength [get_clocks clk]] != 1 || [llength [get_clocks sck]] != 1} {
    error "expected exactly one clk and one sck clock"
}
# OpenSTA 2.7 in the recorded image returns no pulse checks with the candidate's
# cross-clock max-delay and hold exceptions. The same database/libraries yield
# numerical pulse checks when only those exceptions are removed. Keep clocks,
# uncertainty, derates, clock trees and parasitics unchanged. Load the derived
# SDC in a fresh process, then verify that exactly those four lines differ.
# Do not take any setup/hold or CDC result from this diagnostic view.
set fd [open $::env(PROTOEMU_ORIGINAL_SDC) r]
set original [read $fd]
close $fd
set filtered {}
set maxima 0
set holds 0
foreach line [split $original "\n"] {
    if {[string match {set_max_delay -ignore_clock_latency *} $line]} {
        incr maxima
    } elseif {[string match {set_false_path -hold *} $line]} {
        incr holds
    } else {
        lappend filtered $line
    }
}
set fd [open $::env(_SDC_IN) r]
set diagnostic [read $fd]
close $fd
if {$maxima != 2 || $holds != 2 || $diagnostic ne [join $filtered "\n"]} {
    error "pulse-only SDC must differ by exactly two CDC maxima and two hold cuts"
}

set clock_pins [all_registers -clock_pins]
foreach cell [get_cells * -hierarchical] {
    set ref [get_property $cell ref_name]
    if {$ref eq "RM_IHPSG13_1P_256x32_c2_bm_bist" ||
        $ref eq "sg13cmos5l_lgcp_1"} {
        foreach pin [get_pins -of_objects $cell] {
            set name [get_property $pin full_name]
            if {[string match */A_CLK $name] || [string match */CLK $name]} {
                lappend clock_pins $pin
            }
        }
    }
}
set clock_pins [lsort -unique $clock_pins]
if {[llength $clock_pins] == 0} {error "missing pulse-width targets"}
puts "%OL_CREATE_REPORT clock_only_pulse_widths.rpt"
puts "Clock-only diagnostic: clk/SCK cross-domain exceptions removed in this process"
puts "No setup/hold or CDC result from this view is release evidence"
puts "Clock pulse-width target count [llength $clock_pins]"
foreach pin $clock_pins {puts "Pulse-width pin [get_property $pin full_name]"}
report_check_types -min_pulse_width -verbose -corner $corner_name
report_pulse_width_checks -verbose -corner $corner_name $clock_pins
puts "%OL_END_REPORT"
