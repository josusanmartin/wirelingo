/*
 * protoemu — programmable protocol emulator (Tiny Tapeout top level)
 * Copyright (c) 2026 Josu San Martin
 * SPDX-License-Identifier: Apache-2.0
 *
 * Pins: ui[5]=MOSI ui[6]=SCK ui[7]=CS_n uo[7]=MISO (host SPI port);
 * everything else belongs to the four protocol cores (docs/isa.md §2).
 */
`default_nettype none
`include "pe_config.vh"

module tt_um_josusanmartin_protoemu (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
  // reset synchroniser (async assert, sync deassert)
  reg [1:0] rst_s;
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) rst_s <= 2'b00;
    else rst_s <= {rst_s[0], 1'b1};
  end
  wire rstn = rst_s[1];

  wire [15:0] bus_addr;
  wire        bus_we, bus_commit;
  wire [31:0] bus_wdata, bus_rdata;
  wire        miso;

  pe_spi_slave u_spi (
      .clk(clk),
      .rst_n(rstn),
      .sck(ui_in[6]),
      .cs_n(ui_in[7]),
      .mosi(ui_in[5]),
      .miso(miso),
      .bus_addr(bus_addr),
      .bus_we(bus_we),
      .bus_wdata(bus_wdata),
      .bus_commit(bus_commit),
      .bus_rdata(bus_rdata));

  pe_chip #(.BANK_AW(`PE_BANK_AW)) u_chip (
      .clk(clk),
      .rst_n(rstn),
      .ui_in(ui_in[4:0]),
      .uio_in(uio_in),
      .uo_out(uo_out[6:0]),
      .uio_out(uio_out),
      .uio_oe(uio_oe),
      .bus_addr(bus_addr),
      .bus_we(bus_we),
      .bus_wdata(bus_wdata),
      .bus_commit(bus_commit),
      .bus_rdata(bus_rdata));

  assign uo_out[7] = miso;

  wire _unused = &{ena, 1'b0};
endmodule
