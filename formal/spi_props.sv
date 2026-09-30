// Formal: host SPI slave bus-interface safety properties.
`default_nettype none
module spi_props (
    input wire        clk,
    input wire        sck,
    input wire        cs_n,
    input wire        mosi,
    input wire [31:0] bus_rdata
);
  reg rst_n = 1'b0;
  always @(posedge clk) rst_n <= 1'b1;

  wire        miso, bus_we, bus_commit;
  wire [15:0] bus_addr;
  wire [31:0] bus_wdata;
  pe_spi_slave dut (
      .clk(clk), .rst_n(rst_n), .sck(sck), .cs_n(cs_n), .mosi(mosi), .miso(miso),
      .bus_addr(bus_addr), .bus_we(bus_we), .bus_wdata(bus_wdata), .bus_commit(bus_commit),
      .bus_rdata(bus_rdata), .f_cs_idle(f_cs_idle), .f_cs_idle_all(f_cs_idle_all), .f_idle(f_idle));
  wire f_cs_idle, f_cs_idle_all, f_idle;

  reg        past_valid = 1'b0;
  reg        we_q = 1'b0, commit_q = 1'b0;
  reg [2:0]  cs_hist = 3'b111;
  always @(posedge clk) begin
    past_valid <= 1'b1;
    we_q <= bus_we;
    commit_q <= bus_commit;
    cs_hist <= {cs_hist[1:0], cs_n};
  end

  always @(*) begin
    if (rst_n) begin
      // at most one bus operation per cycle, each a single-cycle pulse
      assert (!(bus_we && bus_commit));
      if (past_valid) begin
        assert (!(bus_we && we_q));
        assert (!(bus_commit && commit_q));
      end
    end
  end

  // MISO is only driven while the (synchronised) chip select is active
  always @(*) if (rst_n && f_cs_idle) assert (!miso);

  // a transaction that ends mid-way leaves the slave idle
  always @(posedge clk) if (rst_n && past_valid && cs_hist == 3'b111 && f_cs_idle_all)
    assert (f_idle);

  always @(*) begin
    cover (rst_n && bus_we);
    cover (rst_n && bus_commit);
  end
endmodule
