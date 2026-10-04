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

/*
 * W-bit latch, transparent while en is high, no reset: the library's dlhq
 * cell in synthesis, a behavioral equivalent otherwise. (FIFO storage: an
 * entry is read only after it was written. A reset would put every bit on
 * the reset tree: 1024 more loads, which congested the chip.)
 */
module pe_latch #(
    parameter W = 32
) (
    input  wire         en,
    input  wire [W-1:0] d,
    output wire [W-1:0] q
);
`ifdef SCL_sg13cmos5l_stdcell
  genvar i;
  generate
    for (i = 0; i < W; i = i + 1) begin : g_bit
      sg13cmos5l_dlhq_1 l (.D(d[i]), .GATE(en), .Q(q[i]));
    end
  endgenerate
`else
  reg [W-1:0] r;
  /* verilator lint_off LATCH */
  always @(*) if (en) r = d;
  /* verilator lint_on LATCH */
  assign q = r;
`endif
endmodule
