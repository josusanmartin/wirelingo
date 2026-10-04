# protoemu timing constraints (LibreLane PNR_SDC_FILE / SIGNOFF_SDC_FILE).
# Based on LibreLane's base.sdc, plus the host port's SCK domain (docs/isa.md §6.1).
#
# Two clocks: clk (CLOCK_PERIOD) and SCK on ui_in[6], at most the same frequency.
# They are asynchronous. Every crossing between them is a 2-flop synchronizer
# (toggles, CS_n) or data that is held for at least 8 SCK while a synchronizer
# carries the event (pe_hostport.sv), so the groups are not timed against each
# other.

set period $::env(CLOCK_PERIOD)
create_clock -name clk -period $period [get_ports clk]
create_clock -name sck -period $period [get_ports {ui_in[6]}]
set_clock_groups -asynchronous -group [get_clocks clk] -group [get_clocks sck]

set io_delay [expr $period * $::env(IO_DELAY_CONSTRAINT) / 100]
set sck_in  [get_ports {ui_in[7] ui_in[5] ui_in[4] ui_in[3] ui_in[2]}]
set sck_out [get_ports {uo_out[7] uo_out[6] uo_out[5] uo_out[4]}]

# host port lines: inputs sampled on SCK rise, outputs launched on SCK fall
set_input_delay $io_delay -clock [get_clocks sck] $sck_in
set_output_delay $io_delay -clock [get_clocks sck] $sck_out
# everything else, and ui[4:2] / uo[6:4] as protocol pins, against clk
set clk_in [lsearch -all -inline -not -exact [all_inputs] [get_ports clk]]
set clk_in [lsearch -all -inline -not -exact $clk_in [get_ports {ui_in[6]}]]
set_input_delay $io_delay -clock [get_clocks clk] -add_delay $clk_in
set_output_delay $io_delay -clock [get_clocks clk] -add_delay [all_outputs]

set_max_fanout $::env(MAX_FANOUT_CONSTRAINT) [current_design]
set_driving_cell -lib_cell [lindex [split $::env(SYNTH_DRIVING_CELL) "/"] 0] \
    -pin [lindex [split $::env(SYNTH_DRIVING_CELL) "/"] 1] $clk_in
set_load [expr $::env(OUTPUT_CAP_LOAD) / 1000.0] [all_outputs]

# Setup keeps LibreLane's uncertainty (jitter and margin). Hold gets 0.1 ns:
# a hold check compares one clock edge with itself, so jitter cancels, skew
# is computed from the propagated clocks and OCV is in the derates below. With
# the setup value (0.25 ns) on hold, every flop whose next state includes its
# own value (hold muxes, counters, shift registers: IHP's flops have no
# enable) failed hold at the fast corner: 743 endpoints, about 1700 delay
# buffers (6 % of the area). With 0.1 ns, 20 remain (docs/physical.md).
set_clock_uncertainty -setup $::env(CLOCK_UNCERTAINTY_CONSTRAINT) [all_clocks]
set_clock_uncertainty -hold 0.1 [all_clocks]
set_clock_transition $::env(CLOCK_TRANSITION_CONSTRAINT) [all_clocks]
set_timing_derate -early [expr 1 - $::env(TIME_DERATING_CONSTRAINT) / 100.0]
set_timing_derate -late [expr 1 + $::env(TIME_DERATING_CONSTRAINT) / 100.0]
set_propagated_clock [all_clocks]
