`default_nettype none
`timescale 1ns / 1ps
`include "pe_config.vh"

// Testbench wrapper around pe_chip (no SPI) for lockstep tests against the model.
module tb_chip ();
  initial begin
    if ($test$plusargs("dump")) begin
      $dumpfile("tb_chip.fst");
      $dumpvars(0, tb_chip);
    end
  end

  reg         clk;
  reg         rst_n;
  reg  [4:0]  ui_in;
  reg  [7:0]  uio_in;
  wire [6:0]  uo_out;
  wire [7:0]  uio_out;
  wire [7:0]  uio_oe;
  reg  [15:0] bus_addr;
  reg         bus_we;
  reg  [31:0] bus_wdata;
  reg         bus_commit;
  wire [31:0] bus_rdata;

  pe_chip #(.BANK_AW(`PE_BANK_AW)) u_chip (
      .clk(clk),
      .rst_n(rst_n),
      .ui_in(ui_in),
      .uio_in(uio_in),
      .uo_out(uo_out),
      .uio_out(uio_out),
      .uio_oe(uio_oe),
      .bus_addr(bus_addr),
      .bus_we(bus_we),
      .bus_wdata(bus_wdata),
      .bus_commit(bus_commit),
      .bus_rdata(bus_rdata));
endmodule
