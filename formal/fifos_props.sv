// Formal: pe_fifos (the FIFO pair and FJOIN_TX) against reference queues, for
// any inputs. Unjoined: two independent queues (TX: host push / core pop, RX:
// core push / host pop). Joined: one queue in order, held as RXF (front) then
// TXF, with the stage levels the spec defines: a host push is accepted iff TXF
// has room, the core pops iff RXF is non-empty, and each cycle the TXF head
// moves to RXF iff TXF is non-empty and RXF has room (start-of-cycle levels).
// FJOIN_TX is held constant between clears, as docs/isa.md requires.
// (multiclock: a clock cycle is two solver steps, hence the depths in fifos.sby)
`default_nettype none
module fifos_props #(parameter LOG2 = 2) (
    input wire       clk,
    input wire       clear,
    input wire       fjoin_in,
    input wire       host_push,
    input wire [7:0] host_data,
    input wire       host_pop,
    input wire       core_pop,
    input wire       core_push,
    input wire [7:0] core_data
);
  localparam D = 1 << LOG2;
  reg rst_n = 1'b0;
  always @(posedge clk) rst_n <= 1'b1;

  // FJOIN_TX may change only together with a clear
  reg fjoin = 1'b0;
  always @(posedge clk) if (!rst_n || clear) fjoin <= fjoin_in;

  wire [7:0] txf_head, rxf_head, pq_head;
  wire [3:0] txf_level, rxf_level;
  wire       pq_empty, rx_block;
  wire [D*8-1:0] f_tmem, f_rmem;
  wire [LOG2-1:0] f_trd, f_rrd;
  pe_fifos #(.W(8), .LOG2(LOG2)) dut (
      .clk(clk), .rst_n(rst_n), .clear(clear), .fjoin(fjoin),
      .host_push(host_push), .host_data(host_data), .host_pop(host_pop),
      .core_pop(core_pop), .core_push(core_push), .core_data(core_data),
      .txf_head(txf_head), .rxf_head(rxf_head), .txf_level(txf_level), .rxf_level(rxf_level),
      .pq_head(pq_head), .pq_empty(pq_empty), .rx_block(rx_block),
      .f_tmem(f_tmem), .f_rmem(f_rmem), .f_trd(f_trd), .f_rrd(f_rrd));

  // ---- reference: stage levels and contents. Joined: q holds RXF then TXF.
  // Unjoined: q[0..lr-1] is RX, t[0..lt-1] is TX.
  reg [7:0] q[0:2*D-1];
  reg [7:0] t[0:D-1];
  reg [4:0] lt = 5'd0, lr = 5'd0;
  integer i;

  wire tx_push = host_push && lt != D;
  wire j_pop   = fjoin && core_pop && lr != 0;
  wire j_move  = fjoin && lt != 0 && lr != D;
  wire u_tpop  = !fjoin && core_pop && lt != 0;
  wire u_rpush = !fjoin && core_push && lr != D;
  wire u_rpop  = !fjoin && host_pop && lr != 0;

  always @(posedge clk) begin
    if (!rst_n || clear) begin
      lt <= 5'd0;
      lr <= 5'd0;
    end else if (fjoin) begin
      // queue order is unchanged by the move (it only shifts the RXF/TXF split)
      for (i = 0; i < 2 * D - 1; i = i + 1)
        if (j_pop) q[i] <= q[i+1];
      if (tx_push) q[lr + lt - j_pop] <= host_data;
      lt <= lt + tx_push - j_move;
      lr <= lr + j_move - j_pop;
    end else begin
      for (i = 0; i < D - 1; i = i + 1) begin
        if (u_tpop) t[i] <= t[i+1];
        if (u_rpop) q[i] <= q[i+1];
      end
      if (tx_push) t[lt - u_tpop] <= host_data;
      if (u_rpush) q[lr - u_rpop] <= core_data;
      lt <= lt + tx_push - u_tpop;
      lr <= lr + u_rpush - u_rpop;
    end
  end

  // ---- the RTL agrees with the reference
  always @(*) begin
    if (rst_n) begin
      assert (lt <= D && lr <= D);
      assert (txf_level == lt[3:0] && rxf_level == lr[3:0]);
      assert (rx_block == (fjoin || lr == D));
      assert (pq_empty == (fjoin ? lr == 0 : lt == 0));
      if (fjoin && lr != 0) assert (pq_head == q[0]);
      if (!fjoin && lt != 0) assert (pq_head == t[0]);
      if (!fjoin && lr != 0) assert (rxf_head == q[0]);
    end
  end

  genvar k;
  generate
    for (k = 0; k < D; k = k + 1) begin : g_agree
      wire [LOG2-1:0] rpos = f_rrd + k;   // wraps mod D
      wire [LOG2-1:0] tpos = f_trd + k;
      always @(*) if (rst_n) begin
        if (k < lr) assert (f_rmem[8*rpos +: 8] == q[k]);
        if (k < lt) assert (f_tmem[8*tpos +: 8] == (fjoin ? q[lr + k] : t[k]));
      end
    end
  endgenerate

  // reachability sanity
  always @(*) begin
    cover (rst_n && fjoin && lt == D && lr == D);                 // 2*D words buffered
    cover (rst_n && fjoin && lt == D && lr == D && host_push && core_pop);
    cover (rst_n && !fjoin && lt == D && lr == D);
  end
endmodule
