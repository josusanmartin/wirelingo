/*
 * A core's FIFO pair: TXF (host -> core) and RXF (core -> host), plus the
 * SHIFTCTRL.FJOIN_TX join (docs/isa.md §4.4), where RXF becomes a second TX
 * stage: TXF -> RXF -> core, for 2*D words of TX buffering. While joined, core
 * pushes and host pops are ignored (pe_core stalls the pushes via rx_block and
 * returns 0 to host reads). formal/fifos.sby proves both modes against
 * reference queues.
 * SPDX-License-Identifier: Apache-2.0
 */
`default_nettype none

module pe_fifos #(
    parameter W = 32,
    parameter LOG2 = 2          // depth D = 2**LOG2 per FIFO (4 or 8)
) (
    input  wire         clk,
    input  wire         rst_n,
    input  wire         clear,
    input  wire         fjoin,
    input  wire         host_push,
    input  wire [W-1:0] host_data,
    input  wire         host_pop,
    input  wire         core_pop,    // PULL / autopull (TXF, or RXF when joined)
    input  wire         core_push,   // PUSH / autopush (RXF)
    input  wire [W-1:0] core_data,
    output wire [W-1:0] txf_head,
    output wire [W-1:0] rxf_head,
    output wire [W-1:0] rxf_head2,
    output wire [3:0]   txf_level,
    output wire [3:0]   rxf_level,
    output wire [W-1:0] pq_head,     // the word PULL / autopull take
    output wire         pq_empty,
    output wire         rx_block     // PUSH / autopush must stall
`ifdef FORMAL
    ,
    output wire [(1<<LOG2)*W-1:0] f_tmem, f_rmem,
    output wire [LOG2-1:0]        f_trd, f_rrd
`endif
);
  wire txf_empty = txf_level == 4'd0;
  wire rxf_empty = rxf_level == 4'd0;
  wire rxf_full  = rxf_level[LOG2];
  wire xfer      = fjoin && !txf_empty && !rxf_full;   // start-of-cycle levels

  assign pq_head  = fjoin ? rxf_head : txf_head;
  assign pq_empty = fjoin ? rxf_empty : txf_empty;
  assign rx_block = fjoin || rxf_full;

  pe_fifo #(.W(W), .LOG2(LOG2)) txf (
      .clk(clk), .rst_n(rst_n), .clear(clear),
      .push(host_push), .push_data(host_data),
      /* verilator lint_off PINCONNECTEMPTY */  // TXF needs no read-ahead
      .pop(fjoin ? xfer : core_pop), .head(txf_head), .head2(), .level(txf_level)
      /* verilator lint_on PINCONNECTEMPTY */
`ifdef FORMAL
      , .f_mem(f_tmem), .f_rd(f_trd)
`endif
  );

  pe_fifo #(.W(W), .LOG2(LOG2)) rxf (
      .clk(clk), .rst_n(rst_n), .clear(clear),
      .push(fjoin ? xfer : core_push), .push_data(fjoin ? txf_head : core_data),
      .pop(fjoin ? core_pop : host_pop), .head(rxf_head), .head2(rxf_head2), .level(rxf_level)
`ifdef FORMAL
      , .f_mem(f_rmem), .f_rd(f_rrd)
`endif
  );
endmodule
