# Project.DetailedRouting replaces OpenROAD.DetailedRouting: LibreLane's
# drt.tcl (detailed routing and its antenna repair loop), then, in the same
# process so that re-routing stays incremental, the split of nets that antenna
# diodes took over the fanout limit (flow/antenna_fanout.tcl).
source $::env(SCRIPTS_DIR)/openroad/drt.tcl
source [file join [file dirname [info script]] antenna_fanout.tcl]
if {[protoemu_antfo::run]} {
    write_views
}
