# Project.ClockFanoutRepair runs after CTS; the composite step legalizes next.
source $::env(SCRIPTS_DIR)/openroad/common/io.tcl
read_current_odb
source $::env(SCRIPTS_DIR)/openroad/common/set_rc.tcl
set_propagated_clock [all_clocks]
source [file join [file dirname [info script]] clock_fanout_repair.tcl]
protoemu_clock::run
estimate_parasitics -placement
write_views
