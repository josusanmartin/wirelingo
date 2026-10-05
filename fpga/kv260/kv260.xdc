# protoemu on the KV260.
#
# ---- package pins --------------------------------------------------------------
# Carrier fan enable, active low, as in AMD's KV260/K26 starter-kit platforms
# (Kria-Vitis-Platforms k26_base_starter_kit/xdc/default.xdc, where TTC0's PWM
# drives it; Linux's pwm-fan node uses PWM_POLARITY_INVERTED). Held low: fan on.
set_property PACKAGE_PIN A12 [get_ports fan_en_b]
set_property IOSTANDARD LVCMOS33 [get_ports fan_en_b]
set_property SLEW SLOW [get_ports fan_en_b]
set_property DRIVE 4 [get_ports fan_en_b]

# PMOD J2, as AMD's kv260_bist platform maps PMOD_GPIO[0..7]
# (Kria-Vitis-Platforms kv260/platforms/kv260_bist/xdc/pin.xdc). Header pins:
# pmod[0..3] = J2 pins 1-4 (top row), pmod[4..7] = pins 7-10 (bottom row), per
# kv260_ispMipiRx_vcu_DP's Pmod I2S2 assignment (README.md).
set_property PACKAGE_PIN H12 [get_ports {pmod[0]}]
set_property PACKAGE_PIN E10 [get_ports {pmod[1]}]
set_property PACKAGE_PIN D10 [get_ports {pmod[2]}]
set_property PACKAGE_PIN C11 [get_ports {pmod[3]}]
set_property PACKAGE_PIN B10 [get_ports {pmod[4]}]
set_property PACKAGE_PIN E12 [get_ports {pmod[5]}]
set_property PACKAGE_PIN D11 [get_ports {pmod[6]}]
set_property PACKAGE_PIN B11 [get_ports {pmod[7]}]
set_property IOSTANDARD LVCMOS33 [get_ports {pmod[*]}]
set_property SLEW SLOW [get_ports {pmod[*]}]
set_property DRIVE 4 [get_ports {pmod[*]}]
# protoemu synchronises its pad inputs; the protocols are slow next to clk
set_false_path -from [get_ports {pmod[*]}]
set_false_path -to [get_ports {pmod[*]}]

# ---- clocks --------------------------------------------------------------------
# The PS IP, configured without the board preset, models pl_clk0 as 96.97 MHz;
# on the board Linux sets it to 100 MHz (IOPLL 999.99 MHz / 10, protoemu.dtsi).
create_clock -name clk_pl_0 -period 10.000 [get_pins -hierarchical -filter {NAME =~ *PS8_i/PLCLK[0]}]
set clk_pe  [get_clocks -of_objects [get_pins u_mmcm/CLKOUT1]]
set clk_alt [get_clocks -of_objects [get_pins u_mmcm/CLKOUT0]]
set period  [get_property PERIOD $clk_pe]

# master clock: protoemu's clock or the alternate one (BUFGMUX_CTRL)
create_generated_clock -name mclk_pe -divide_by 1 \
    -source [get_pins u_mclk_mux/I0] [get_pins u_mclk_mux/O]
create_generated_clock -name mclk_alt -divide_by 1 -add -master_clock $clk_alt \
    -source [get_pins u_mclk_mux/I1] [get_pins u_mclk_mux/O]
set_clock_groups -physically_exclusive -group mclk_pe -group mclk_alt

# GPIO SCK (a software-toggled GPIO bit) into the SCK BUFGCTRL; the gated
# master clock comes in on I1. Only one drives SCK; the master is idle while
# the GPIO path is selected.
create_clock -name sck -period $period [get_pins u_sck_mux/I0]
set_clock_groups -asynchronous -group sck -group [get_clocks {mclk_pe mclk_alt}]

# protoemu's port: SCK domain <-> clk crossings are bundled data that must
# arrive within one clk period (src/protoemu.sdc); their hold checks are cut.
# With mclk_pe the master and protoemu share a clock and are timed normally.
set_max_delay -datapath_only $period -from [get_clocks sck] -to $clk_pe
set_max_delay -datapath_only $period -from $clk_pe -to [get_clocks sck]
set_false_path -hold -from [get_clocks sck] -to $clk_pe
set_false_path -hold -from $clk_pe -to [get_clocks sck]
set_max_delay -datapath_only $period -from [get_clocks mclk_alt] -to $clk_pe
set_max_delay -datapath_only $period -from $clk_pe -to [get_clocks mclk_alt]
set_false_path -hold -from [get_clocks mclk_alt] -to $clk_pe
set_false_path -hold -from $clk_pe -to [get_clocks mclk_alt]

# AXI side <-> everything else: GPIO inputs to protoemu are synchronised (or
# held by software around SCK edges), the GPIO input channel has its own
# synchroniser, the cycle counter is Gray-coded, pe_spim's registers are
# quasi-static and its start/done handshakes are synchronised.
set_clock_groups -asynchronous -group [get_clocks clk_pl_0] \
    -group [list $clk_pe $clk_alt [get_clocks {mclk_pe mclk_alt sck}]]
