/*
 * Clock gate: gclk = clk while en (sampled while clk is low) is 1.
 * Replaces a flop's hold mux for registers that are written rarely
 * (IHP's flops have no enable input). Uses the library's latch-based
 * ICG in synthesis; a behavioral equivalent otherwise.
 * SPDX-License-Identifier: Apache-2.0
 */
`default_nettype none

module pe_cg (
    input  wire clk,
    input  wire en,
    output wire gclk
);
`ifdef SCL_sg13cmos5l_stdcell
  sg13cmos5l_lgcp_1 cg (.CLK(clk), .GATE(en), .GCLK(gclk));
`else
  reg en_l;
  /* verilator lint_off LATCH */
  always @(*) if (!clk) en_l = en;
  /* verilator lint_on LATCH */
  assign gclk = clk & en_l;
`endif
endmodule
