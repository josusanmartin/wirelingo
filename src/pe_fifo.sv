/*
 * FIFO of depth 2**LOG2. Full/empty are judged on start-of-cycle state (docs/isa.md §5):
 * a push while full is dropped even if a pop happens in the same cycle.
 * SPDX-License-Identifier: Apache-2.0
 */
`default_nettype none

module pe_fifo #(
    parameter W = 32,
    parameter LOG2 = 2          // depth = 2**LOG2 (4 or 8)
) (
    input  wire         clk,
    input  wire         rst_n,
    input  wire         clear,
    input  wire         push,
    input  wire [W-1:0] push_data,
    input  wire         pop,
    output wire [W-1:0] head,
    output wire [W-1:0] head2,       // the entry after the head (host read-ahead)
    output wire [3:0]   level
`ifdef FORMAL
    ,
    output wire [(1<<LOG2)*W-1:0] f_mem,  // storage and read pointer, for formal/fifos.sby
    output wire [LOG2-1:0]        f_rd
`endif
);
  localparam DEPTH = 1 << LOG2;
  reg [LOG2-1:0] rd, wr;
  reg [LOG2:0]   cnt;

  wire do_push = push && !cnt[LOG2];
  wire do_pop  = pop && cnt != 0;

  // storage: one latch per entry bit. A push stores the word in wd (one
  // clock-gated register per FIFO) at the clock edge; the entry's clock gate
  // then opens its latches for the high half of the next cycle, while wd holds
  // that word, so a read in that cycle sees it through the open latch. Latches
  // (dlhq, 31 um2, no reset) instead of flops (dfrbpq, 49 um2 plus a tie cell:
  // IHP's flops all have resets and no enable). Entries are only read after
  // being written, so they need no reset.
  // PE_FIFO_NO_CG selects functionally identical enable flops for alternate
  // builds. Both formal/core.sby and formal/fifos.sby use multiclock and prove
  // the latch implementation; the FIFO harness checks reference queues.
  wire [W-1:0] ent[0:DEPTH-1];
`ifndef PE_FIFO_NO_CG
  reg  [W-1:0] wd;
  wire         wd_gclk;
  pe_cg u_wd_cg (.clk(clk), .en(do_push), .gclk(wd_gclk));
  always @(posedge wd_gclk) wd <= push_data;
`endif
  genvar e;
  generate
    for (e = 0; e < DEPTH; e = e + 1) begin : g_ent
      reg [W-1:0] q;
      wire        wen = do_push && wr == e;
`ifdef PE_FIFO_NO_CG
      always @(posedge clk) if (wen) q <= push_data;
`else
      wire gclk;
      wire [W-1:0] lq;
      pe_cg u_cg (.clk(clk), .en(wen), .gclk(gclk));
      pe_latch #(.W(W)) u_lat (.en(gclk), .d(wd), .q(lq));
      always @(*) q = lq;
`endif
      assign ent[e] = q;
    end
  endgenerate

  assign head  = ent[rd];
  assign head2 = ent[rd + 1'b1];
  generate
    if (LOG2 == 3) begin : g_lvl8
      assign level = cnt;
    end else begin : g_lvl
      assign level = {{(3 - LOG2){1'b0}}, cnt};
    end
  endgenerate

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      rd <= 0;
      wr <= 0;
      cnt <= 0;
    end else if (clear) begin
      rd <= 0;
      wr <= 0;
      cnt <= 0;
    end else begin
      if (do_push) wr <= wr + 1'b1;
      if (do_pop) rd <= rd + 1'b1;
      cnt <= cnt + do_push - do_pop;
    end
  end

`ifdef FORMAL
  genvar fe;
  generate
    for (fe = 0; fe < DEPTH; fe = fe + 1) begin : g_fmem
      assign f_mem[fe*W +: W] = ent[fe];
    end
  endgenerate
  assign f_rd = rd;
  // Internal invariants (used by formal/fifos.sby for induction).
  always @(*) begin
    if (rst_n) begin
      assert (cnt <= DEPTH);
      assert (cnt[LOG2-1:0] == wr - rd);
    end
  end
`endif
endmodule
