# protoemu timing constraints (LibreLane PNR_SDC_FILE / SIGNOFF_SDC_FILE).
# Based on LibreLane's base.sdc, plus the host port's SCK domain (docs/isa.md §6.1).
#
# Two clocks: clk (CLOCK_PERIOD) and SCK on ui_in[6], at most the same frequency.
# They are asynchronous. Events use synchronizers; held buses must reach their
# consumers within one minimum clock period. Do not use set_clock_groups:
# its false paths would override the bundled-data max-delay checks.
# SCK's period is CLOCK_PERIOD (SCK = clk, the fastest the RTL allows) unless
# PROTOEMU_SCK_PERIOD (ns, >= CLOCK_PERIOD) times a slower host SCK.

set period $::env(CLOCK_PERIOD)
set sck_period [expr {[info exists ::env(PROTOEMU_SCK_PERIOD)] ? double($::env(PROTOEMU_SCK_PERIOD)) : $period}]
if {$sck_period < $period} { error "PROTOEMU_SCK_PERIOD $sck_period is below CLOCK_PERIOD $period (SCK <= clk)" }
create_clock -name clk -period $period [get_ports clk]
create_clock -name sck -period $sck_period [get_ports {ui_in[6]}]
# OpenSTA's supported datapath constraint excludes clock latency. Apply it to
# ALL cross-clock register paths, so synthesis renaming cannot omit a bus bit.
# Same-clock synchronizer stages retain ordinary setup/hold timing.
# Cross-clock hold has no defined phase relationship and is cut separately.
set_max_delay -ignore_clock_latency $period -from [get_clocks sck] -to [get_clocks clk]
set_max_delay -ignore_clock_latency $period -from [get_clocks clk] -to [get_clocks sck]
set_false_path -hold -from [get_clocks sck] -to [get_clocks clk]
set_false_path -hold -from [get_clocks clk] -to [get_clocks sck]

set io_delay [expr $period * $::env(IO_DELAY_CONSTRAINT) / 100]
set sck_io  [get_ports {ui_in[5] ui_in[4] ui_in[3] ui_in[2]}]
set sck_cs  [get_ports {ui_in[7]}]
set sck_out [get_ports {uo_out[7] uo_out[6] uo_out[5] uo_out[4]}]

# Host port lines against SCK, SPI mode 0 (docs/isa.md §6.1, "Pad timing").
# The host changes MOSI/IO on SCK falls and samples MISO/IO on SCK rises; the
# chip samples on rises and changes its outputs on falls (src/pe_hostport.sv).
# The external delays are ASSUMPTIONS at the tile's pins, in ns; no pad,
# package or board delay has been measured:
#   PROTOEMU_HOST_BOARD_NS (default 2.0): one-way flight between a host pin
#     and the tile pin, the same on every host line. The default is a short
#     board trace; Tiny Tapeout's pads and pin mux are NOT in it.
#   PROTOEMU_HOST_SKEW_NS (default 1.0): the host's launch skew of CS_n, MOSI
#     and IO against its own SCK fall (either way), and its MISO/IO setup and
#     hold.
# No flow step passes these variables: hardens and signoff use the defaults.
# A run with an override is a study, and its numbers must say so.
set host_board [expr {[info exists ::env(PROTOEMU_HOST_BOARD_NS)] ? double($::env(PROTOEMU_HOST_BOARD_NS)) : 2.0}]
set host_skew  [expr {[info exists ::env(PROTOEMU_HOST_SKEW_NS)] ? double($::env(PROTOEMU_HOST_SKEW_NS)) : 1.0}]
# MOSI/IO: launched on the SCK fall; SCK and the data share the flight, so it
# cancels. Sampled half an SCK period later; hold is against the rise before.
set_input_delay -clock [get_clocks sck] -clock_fall -max $host_skew $sck_io
set_input_delay -clock [get_clocks sck] -clock_fall -min [expr {-$host_skew}] $sck_io
# CS_n changes while SCK idles low (§6.1 timing table): >= 1 clk before the
# next SCK rise (setup and recovery) and >= 1 clk after the last SCK fall
# (hold and removal). Both are written against the SCK fall before the
# change, so the rising- and falling-edge flops each get their real budget.
set_input_delay -clock [get_clocks sck] -clock_fall -max [expr {$sck_period / 2.0 - $period + $host_skew}] $sck_cs
set_input_delay -clock [get_clocks sck] -clock_fall -min [expr {$period - $host_skew}] $sck_cs
# MISO/IO: launched on the chip's SCK fall, sampled at the host's next rise:
# SCK's flight in and the data's flight back, plus the host's setup (max);
# the same two flights less the host's hold (min). A shorter board helps
# setup and costs hold: set PROTOEMU_HOST_BOARD_NS to the board's real value.
set_output_delay -clock [get_clocks sck] -max [expr {2 * $host_board + $host_skew}] $sck_out
set_output_delay -clock [get_clocks sck] -min [expr {2 * $host_board - $host_skew}] $sck_out
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
