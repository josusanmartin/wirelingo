# protoemu on the Kria KV260: batch (non-project) Vivado flow.
#
#   vivado -mode batch -source build.tcl -tclargs <staged src dir> <out dir> [clk_div]
#
# <staged src dir> comes from flow/fpga_profile.py (see build.sh). clk_div sets
# protoemu's clock: 1200 MHz / clk_div (24 = 50 MHz, the default; 25 = 48 MHz).
# Writes <out dir>/protoemu.bit and the utilization/timing reports.

set src_dir [file normalize [lindex $argv 0]]
set out_dir [file normalize [lindex $argv 1]]
set clk_div [expr {[llength $argv] > 2 ? [lindex $argv 2] : 24}]
set here [file dirname [file normalize [info script]]]
set part xck26-sfvc784-2LV-c
file mkdir $out_dir
set_param general.maxThreads 8

create_project -in_memory -part $part
set_property target_language Verilog [current_project]

# ---- block design: PS (HPM0_FPD, pl_clk0) + two AXI GPIO -------------------
create_bd_design pe_ps
set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:zynq_ultra_ps_e ps]
# Only what the PL design needs. Under Linux the PS itself is configured by the
# Kria boot firmware; the bitstream just has to agree on the HPM0_FPD port
# (128 bit, matching config-afi in protoemu.dtsi) and on pl_clk0 (100 MHz).
set_property -dict [list \
    CONFIG.PSU__USE__M_AXI_GP0 {1} \
    CONFIG.PSU__MAXIGP0__DATA_WIDTH {128} \
    CONFIG.PSU__USE__M_AXI_GP1 {0} \
    CONFIG.PSU__USE__M_AXI_GP2 {0} \
    CONFIG.PSU__FPGA_PL0_ENABLE {1} \
    CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ {100} \
    CONFIG.PSU__NUM_FABRIC_RESETS {1} \
] $ps

set g0 [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio gpio0]
set_property -dict [list \
    CONFIG.C_GPIO_WIDTH {32} CONFIG.C_ALL_OUTPUTS {1} CONFIG.C_DOUT_DEFAULT {0x00FF0080} \
    CONFIG.C_IS_DUAL {1} CONFIG.C_GPIO2_WIDTH {32} CONFIG.C_ALL_INPUTS_2 {1} \
] $g0
set g1 [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio gpio1]
set_property -dict [list CONFIG.C_GPIO_WIDTH {32} CONFIG.C_ALL_INPUTS {1}] $g1

set ic [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect ic]
set_property -dict [list CONFIG.NUM_MI {2}] $ic
set rst [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset rst]

connect_bd_net [get_bd_pins ps/pl_clk0] [get_bd_pins ps/maxihpm0_fpd_aclk] \
    [get_bd_pins ic/ACLK] [get_bd_pins ic/S00_ACLK] [get_bd_pins ic/M00_ACLK] [get_bd_pins ic/M01_ACLK] \
    [get_bd_pins gpio0/s_axi_aclk] [get_bd_pins gpio1/s_axi_aclk] [get_bd_pins rst/slowest_sync_clk]
connect_bd_net [get_bd_pins ps/pl_resetn0] [get_bd_pins rst/ext_reset_in]
connect_bd_net [get_bd_pins rst/interconnect_aresetn] [get_bd_pins ic/ARESETN]
connect_bd_net [get_bd_pins rst/peripheral_aresetn] [get_bd_pins ic/S00_ARESETN] \
    [get_bd_pins ic/M00_ARESETN] [get_bd_pins ic/M01_ARESETN] \
    [get_bd_pins gpio0/s_axi_aresetn] [get_bd_pins gpio1/s_axi_aresetn]
connect_bd_intf_net [get_bd_intf_pins ps/M_AXI_HPM0_FPD] [get_bd_intf_pins ic/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins ic/M00_AXI] [get_bd_intf_pins gpio0/S_AXI]
connect_bd_intf_net [get_bd_intf_pins ic/M01_AXI] [get_bd_intf_pins gpio1/S_AXI]

create_bd_port -dir O -type clk pl_clk0
connect_bd_net [get_bd_pins ps/pl_clk0] [get_bd_ports pl_clk0]
create_bd_port -dir O -type rst pl_resetn0
connect_bd_net [get_bd_pins ps/pl_resetn0] [get_bd_ports pl_resetn0]
make_bd_intf_pins_external -name gpio0_o [get_bd_intf_pins gpio0/GPIO]
make_bd_intf_pins_external -name gpio0_i [get_bd_intf_pins gpio0/GPIO2]
make_bd_intf_pins_external -name gpio1_i [get_bd_intf_pins gpio1/GPIO]

assign_bd_address -offset 0xA0000000 -range 64K [get_bd_addr_segs gpio0/S_AXI/Reg]
assign_bd_address -offset 0xA0010000 -range 64K [get_bd_addr_segs gpio1/S_AXI/Reg]
validate_bd_design
save_bd_design
set bd_file [get_files pe_ps.bd]
# global synthesis: the BD's IP is synthesized with the top (no OOC IP runs)
set_property synth_checkpoint_mode None $bd_file
generate_target all $bd_file
set wrapper [make_wrapper -files $bd_file -top]
read_verilog $wrapper

# ---- RTL ---------------------------------------------------------------------
read_verilog $here/kv260_top.v
foreach f [glob $src_dir/*.sv $src_dir/project.v] { read_verilog -sv $f }
set_property include_dirs [list $src_dir] [current_fileset]
read_xdc $here/kv260.xdc

synth_design -top kv260_top -part $part -include_dirs $src_dir -generic CLK_DIV=$clk_div
report_utilization -file $out_dir/synth_utilization.rpt
opt_design
place_design
phys_opt_design
route_design
report_utilization -file $out_dir/utilization.rpt
report_utilization -hierarchical -hierarchical_depth 3 -file $out_dir/utilization_hier.rpt
report_timing_summary -max_paths 20 -file $out_dir/timing_summary.rpt
report_clock_interaction -file $out_dir/clock_interaction.rpt
report_clocks -file $out_dir/clocks.rpt
report_methodology -file $out_dir/methodology.rpt
report_drc -file $out_dir/drc.rpt
write_checkpoint -force $out_dir/routed.dcp
write_bitstream -force -bin_file $out_dir/protoemu.bit

set wns [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -setup]]
set whs [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -hold]]
puts "PROTOEMU_RESULT WNS=$wns WHS=$whs clk_div=$clk_div"
