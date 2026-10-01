// Formal: host port (pe_hostport) safety, with SCK, CS_n, the IO lines and the
// clock relationship completely unconstrained (multiclock: clk and sck are
// independent). The clk side must issue at most one bus operation per cycle,
// keep at most two read words outstanding, commit only words it snapped, and
// cancel only after a read. Data integrity is checked by the cocotb tests at
// random SCK phases and rates (test_host_port_fast, test_spi_port_timing).
`default_nettype none
module hostport_props (
    input wire        clk,
    input wire        sck,
    input wire        cs_n,
    input wire [3:0]  io_in,
    input wire        quad_en,
    input wire [31:0] bus_rdata
);
  reg rst_n = 1'b0;
  always @(posedge clk) rst_n <= 1'b1;

  wire [3:0]  io_out;
  wire        io_oe, bus_we, bus_snap, bus_commit, bus_cancel;
  wire [15:0] bus_addr;
  wire [31:0] bus_wdata;
  wire [1:0]  out;
  pe_hostport dut (
      .clk(clk), .rst_n(rst_n), .sck(sck), .cs_n(cs_n), .io_in(io_in), .io_out(io_out),
      .io_oe(io_oe), .quad_en(quad_en), .bus_addr(bus_addr), .bus_we(bus_we),
      .bus_wdata(bus_wdata), .bus_snap(bus_snap), .bus_commit(bus_commit),
      .bus_cancel(bus_cancel), .bus_rdata(bus_rdata), .f_outstanding(out));

  // the port's outstanding count, step by step: a snapshot adds a word (with
  // room), a commit removes one that was there, a cancel clears them, nothing
  // else changes them (the count already includes the operation being issued)
  reg [1:0] out_q = 2'd0;
  reg       past = 1'b0;
  always @(posedge clk) begin
    out_q <= out;
    past <= rst_n;
  end

  always @(*) begin
    if (rst_n) begin
      assert ((bus_we + bus_snap + bus_commit + bus_cancel) <= 1);   // one operation per cycle
      if (past) begin
        if (bus_snap)        assert (out_q != 2'd2 && out == out_q + 2'd1);
        else if (bus_commit) assert (out_q != 2'd0 && out == out_q - 2'd1);
        else if (bus_cancel) assert (out == 2'd0);
        else                 assert (out == out_q || out == 2'd0);    // a write-only end
      end
      assert (!io_oe || !cs_n);                                      // uo[6:4] only inside a transaction
    end
  end

  always @(*) begin
    cover (rst_n && bus_we);
    cover (rst_n && bus_commit && out_q == 2'd2);
    cover (rst_n && bus_cancel);
  end
endmodule
