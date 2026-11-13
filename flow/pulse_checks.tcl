# Report-only evidence for flow/release_gates.py, sourced as the corner Tcl of
# the "pulse" pass of flow/check_rerun.py, whose SDC has every path-exception
# command disabled (OpenSTA 2.7.0 reports no pulse width check on the pins an
# exception's -from clock drives). It prints the worst minimum pulse width
# check, every violator, each SRAM clock pin's checks, each clock's register
# clock pins and every pulse width check, so that a partial loss of checks
# shows as a clock or pin without one.
puts "%OL_CREATE_REPORT pulse_checks.rpt"
puts "== corner $corner_name"
puts "== worst min_pulse_width"
report_check_types -min_pulse_width -verbose -corner $corner_name
puts "== violators min_pulse_width"
report_check_types -min_pulse_width -violators -verbose -corner $corner_name
puts "== sram clock pins"
foreach pin [get_pins -hierarchical */A_CLK] {
    puts "pin [get_full_name $pin]"
    report_pulse_width_checks -verbose -corner $corner_name $pin
}
puts "== clock register pins"
foreach clk [all_clocks] {
    set pins [all_registers -clock $clk -clock_pins]
    puts "clock [get_name $clk] [llength $pins]"
    foreach pin $pins {
        puts "  [get_full_name $pin]"
    }
}
puts "== all pulse width checks"
report_pulse_width_checks -no_line_splits -corner $corner_name
puts "== end of checks"
puts "%OL_END_REPORT"
