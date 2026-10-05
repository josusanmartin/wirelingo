# Additional reports at every STA corner, including final extracted signoff.
# Results still require review: setup/hold aggregates alone omit pulse/gating
# checks, and synchronizer MTBF requires process characterization.
puts "%OL_CREATE_REPORT host_cdc.rpt"
puts "CDC contract: maximum cross-clock datapath <= CLOCK_PERIOD, no setup cuts"
report_checks -from [get_clocks sck] -to [get_clocks clk] -path_delay max \
    -group_path_count 1000 -format full_clock_expanded -fields {slew cap input net fanout} -corner $corner_name
report_checks -from [get_clocks clk] -to [get_clocks sck] -path_delay max \
    -group_path_count 1000 -format full_clock_expanded -fields {slew cap input net fanout} -corner $corner_name
puts "%OL_END_REPORT"
puts "%OL_CREATE_REPORT gating_pulse_reset.rpt"
report_check_types -clock_gating_setup -clock_gating_hold -min_pulse_width -min_period \
    -recovery -removal -violators -verbose -corner $corner_name
report_pulse_width_checks -verbose -corner $corner_name
puts "%OL_END_REPORT"
