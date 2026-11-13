# Report-only evidence for flow/release_gates.py, sourced as the corner Tcl of
# the "signoff" pass of flow/check_rerun.py (never by a harden). For each kind
# of check it prints the worst check, not only violators, so that "no
# violation" can be told from "no check at all".
puts "%OL_CREATE_REPORT check_types.rpt"
puts "== corner $corner_name"
foreach kind {min_pulse_width min_period recovery removal clock_gating_setup clock_gating_hold} {
    puts "== worst $kind"
    report_check_types -$kind -verbose -corner $corner_name
    puts "== violators $kind"
    report_check_types -$kind -violators -verbose -corner $corner_name
}
puts "== end of checks"
puts "%OL_END_REPORT"
