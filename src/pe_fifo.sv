/*
 * 4-deep FIFO. Full/empty are judged on start-of-cycle state (docs/isa.md §5.3):
 * a push while full is dropped even if a pop happens in the same cycle.
 * SPDX-License-Identifier: Apache-2.0
 */
`default_nettype none

module pe_fifo #(
    parameter W = 32
) (
    input  wire         clk,
    input  wire         rst_n,
    input  wire         clear,
    input  wire         push,
    input  wire [W-1:0] push_data,
    input  wire         pop,
    output wire [W-1:0] head,
    output wire [2:0]   level
`ifdef FORMAL
    ,
    output wire [4*W-1:0] f_mem,  // storage and read pointer, for formal/fifo.sby
    output wire [1:0]     f_rd
`endif
);
  reg [1:0]   rd, wr;
  reg [2:0]   cnt;

  wire do_push = push && !cnt[2];
  wire do_pop  = pop && cnt != 3'd0;

  // storage: one register per entry. Normally each entry has its own clock
  // gate instead of a hold mux per bit (IHP flops have no enable input).
  // PE_FIFO_NO_CG selects functionally identical enable flops; the core proof
  // (formal/core.sby) uses it to stay single-clock, while formal/fifo.sby
  // proves the clock-gated version equal to a reference queue.
  wire [W-1:0] ent[0:3];
  genvar e;
  generate
    for (e = 0; e < 4; e = e + 1) begin : g_ent
      reg [W-1:0] q;
      wire        wen = do_push && wr == e;
`ifdef PE_FIFO_NO_CG
      always @(posedge clk) if (wen) q <= push_data;
`else
      wire gclk;
      pe_cg u_cg (.clk(clk), .en(wen), .gclk(gclk));
      always @(posedge gclk) q <= push_data;
`endif
      assign ent[e] = q;
    end
  endgenerate

  assign head  = ent[rd];
  assign level = cnt;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      rd <= 2'd0;
      wr <= 2'd0;
      cnt <= 3'd0;
    end else if (clear) begin
      rd <= 2'd0;
      wr <= 2'd0;
      cnt <= 3'd0;
    end else begin
      if (do_push) wr <= wr + 2'd1;
      if (do_pop) rd <= rd + 2'd1;
      cnt <= cnt + {2'd0, do_push} - {2'd0, do_pop};
    end
  end

`ifdef FORMAL
  assign f_mem = {ent[3], ent[2], ent[1], ent[0]};
  assign f_rd = rd;
  // Internal invariants (used by formal/fifo.sby for induction).
  always @(*) begin
    if (rst_n) begin
      assert (cnt <= 3'd4);
      assert (cnt[1:0] == wr - rd);
    end
  end
`endif
endmodule
