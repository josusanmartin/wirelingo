# protoemu on the KV260: timing only (no package pins; all I/O goes through the PS).
#
# Clocks: clk_pl_0 (100 MHz, AXI GPIO), the MMCM clock that runs protoemu, and
# SCK (a GPIO output on a BUFG). Mirrors src/protoemu.sdc: SCK and clk are
# asynchronous, cross-domain words are bundled data that must reach their
# consumers within one clk period; their hold checks are cut.
set clk_pe [get_clocks -of_objects [get_pins u_mmcm/CLKOUT0]]
set period [get_property PERIOD $clk_pe]
create_clock -name sck -period $period [get_pins u_sck_buf/O]

set_max_delay -datapath_only $period -from [get_clocks sck] -to $clk_pe
set_max_delay -datapath_only $period -from $clk_pe -to [get_clocks sck]
set_false_path -hold -from [get_clocks sck] -to $clk_pe
set_false_path -hold -from $clk_pe -to [get_clocks sck]

# AXI GPIO <-> protoemu: every protoemu input is synchronised (pin inputs, CS_n
# and reset) or held by software for microseconds around SCK edges (MOSI); the
# GPIO input channel has its own two-flop synchroniser. The cycle counter is
# Gray-coded.
set_clock_groups -asynchronous -group [get_clocks clk_pl_0] -group [list $clk_pe [get_clocks sck]]
