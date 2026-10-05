# protoemu on the KV260. All protoemu I/O goes through the PS; the only package
# pin is the carrier fan enable, active low, as in AMD's KV260/K26 starter-kit
# platforms (Kria-Vitis-Platforms k26_base_starter_kit/xdc/default.xdc, where
# TTC0's PWM drives it; Linux's pwm-fan node uses PWM_POLARITY_INVERTED).
set_property PACKAGE_PIN A12 [get_ports fan_en_b]
set_property IOSTANDARD LVCMOS33 [get_ports fan_en_b]
set_property SLEW SLOW [get_ports fan_en_b]
set_property DRIVE 4 [get_ports fan_en_b]

#
# Clocks: clk_pl_0 (100 MHz, AXI GPIO), the MMCM clock that runs protoemu, and
# SCK (a GPIO output on a BUFG). Mirrors src/protoemu.sdc: SCK and clk are
# asynchronous, cross-domain words are bundled data that must reach their
# consumers within one clk period; their hold checks are cut.
# The PS IP, configured without the board preset, models pl_clk0 as 96.97 MHz;
# on the board Linux sets it to 100 MHz (IOPLL 999.99 MHz / 10, protoemu.dtsi).
# Time it at 100 MHz, so the MMCM clock is analysed at 50 MHz (clk_div 24).
create_clock -name clk_pl_0 -period 10.000 [get_pins -hierarchical -filter {NAME =~ *PS8_i/PLCLK[0]}]
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
