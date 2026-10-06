# Project.SramClockAdvanceTcl runs after CTS; the composite step legalizes next.
source $::env(SCRIPTS_DIR)/openroad/common/io.tcl
read_current_odb
source $::env(SCRIPTS_DIR)/openroad/common/set_rc.tcl
set_propagated_clock [all_clocks]
source [file join [file dirname [info script]] sram_clock_advance.tcl]
# Fail fast: OpenROAD CTS (sink clustering 8, some placements) has left an
# ICG's CLK pin unconnected (runs t48l/n/o/q); the flow's disconnected-pin
# check only catches it after detailed routing.
foreach inst [[ord::get_db_block] getInsts] {
    foreach pin {CLK A_CLK} {
        set it [$inst findITerm $pin]
        if {$it ne "NULL" && [$it getNet] eq "NULL"} { error "clock pin [$inst getName]/$pin is unconnected after CTS" }
    }
}
protoemu_sram_clk::run
if {[info exists ::env(PROTOEMU_LATE_CLOCK_REGS)] && [llength $::env(PROTOEMU_LATE_CLOCK_REGS)]} {
    protoemu_late_clk::run $::env(PROTOEMU_LATE_CLOCK_REGS) $::env(PROTOEMU_LATE_CLOCK_STAGES)
}
if {[info exists ::env(PROTOEMU_CLOCK_FANOUT_FIX)] && $::env(PROTOEMU_CLOCK_FANOUT_FIX)} {
    source [file join [file dirname [info script]] clock_fanout_repair.tcl]
    protoemu_clock::run
}
estimate_parasitics -placement
write_views
