# Use the same OpenROAD image and PDK as the candidate. A 0.01-ns clock must
# report pulse-width violations; the 20-ns control must not. Report text, not
# process exit alone, determines those expectations. This is no ASIC signoff.
if {![info exists ::env(PDK_ROOT)] || ![info exists ::env(PROTOEMU_PULSE_PERIOD_NS)]} {
    error "PDK_ROOT and PROTOEMU_PULSE_PERIOD_NS are required"
}
set period $::env(PROTOEMU_PULSE_PERIOD_NS)
if {![string is double -strict $period] || $period <= 0} {
    error "pulse-control period must be positive"
}
set std [file join $::env(PDK_ROOT) ihp-sg13cmos5l libs.ref sg13cmos5l_stdcell]
read_lef [file join $std lef sg13cmos5l_tech.lef]
read_lef [file join $std lef sg13cmos5l_stdcell.lef]
read_liberty [file join $std lib sg13cmos5l_stdcell_typ_1p20V_25C.lib]
read_verilog [file join [file dirname [info script]] pulse_control.v]
link_design pulse_control
create_clock -name clk -period $period [get_ports clk]
set_clock_transition 0.05 [get_clocks clk]
set_case_analysis 1 [get_ports rst_n]
report_check_types -min_pulse_width -verbose
report_pulse_width_checks -verbose [get_pins u_ff/CLK]
