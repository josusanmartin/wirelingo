# Hold-only repair after global routing, run after the last design repair
# (Project.RepairDesignThenHold, gen_config.py --post-repair-hold).
#
# LibreLane's ResizerTimingPostGRT (rsz_timing_postgrt.tcl) repairs setup and
# then hold; the flow then buffers the SRAM pins and repeats the design repair
# (slew, capacitance, fanout), which inserts buffers that nothing re-checks for
# hold. On aqp-base that took the fast corner from -0.039 ns to -0.197 ns
# (window 5's STA, GRT-estimated). This is the same script without the setup
# pass: the same parasitics (re-run global routing, estimate from it), the same
# hold arguments and margins, then detailed placement and global routing again.
source $::env(SCRIPTS_DIR)/openroad/common/io.tcl
source $::env(SCRIPTS_DIR)/openroad/common/resizer.tcl

read_current_odb

set_propagated_clock [all_clocks]

set_dont_touch_objects

source $::env(SCRIPTS_DIR)/openroad/common/set_rc.tcl

source $::env(SCRIPTS_DIR)/openroad/common/grt.tcl
estimate_parasitics -global_routing

set hold_args [list]
lappend hold_args -verbose
lappend hold_args -hold
lappend hold_args -setup_margin $::env(GRT_RESIZER_SETUP_SLACK_MARGIN)
lappend hold_args -hold_margin $::env(GRT_RESIZER_HOLD_SLACK_MARGIN)
lappend hold_args -max_buffer_percent $::env(GRT_RESIZER_HOLD_MAX_BUFFER_PCT)
append_if_flag hold_args GRT_RESIZER_ALLOW_SETUP_VIOS -allow_setup_violations
append_if_exists_argument hold_args GRT_RESIZER_HOLD_REPAIR_TNS_PCT -repair_tns
append_if_exists_argument hold_args GRT_RESIZER_HOLD_MAX_UTIL_PCT -max_utilization

log_cmd repair_timing {*}$hold_args

source $::env(SCRIPTS_DIR)/openroad/common/dpl.tcl
unset_dont_touch_objects
if { $::env(GRT_RESIZER_RUN_GRT) } {
    source $::env(SCRIPTS_DIR)/openroad/common/grt.tcl
}

write_views
