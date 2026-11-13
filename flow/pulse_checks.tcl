# Report-only evidence for flow/release_gates.py, sourced as the corner Tcl of
# the "pulse" pass of flow/check_rerun.py, whose signoff SDC has no
# clock-to-clock exceptions (with exceptions both ways between two clocks,
# OpenSTA 2.7.0 reports no pulse width check at all). It prints the worst
# minimum pulse width check, every violator, and each SRAM clock pin's checks.
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
puts "== end of checks"
puts "%OL_END_REPORT"
