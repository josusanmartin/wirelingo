// Build configuration shared by RTL and testbenches.
`ifndef PE_CONFIG_VH
`define PE_CONFIG_VH
// Address bits per IMEM bank in the flop build (4 => 16 words/bank).
`ifndef PE_BANK_AW
`define PE_BANK_AW 4
`endif
`endif
