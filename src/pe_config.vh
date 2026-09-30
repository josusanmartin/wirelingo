// Build configuration for testbenches (mirrors the logic at the top of project.v).
`ifndef PE_CONFIG_VH
`define PE_CONFIG_VH
`ifdef PE_FLOP_IMEM
`ifndef PE_BANK_AW
`define PE_BANK_AW 4
`endif
`else
`undef PE_BANK_AW
`define PE_BANK_AW 8
`endif
`endif
