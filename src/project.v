/*
 * protoemu — programmable protocol emulator (Tiny Tapeout top level)
 * Copyright (c) 2026 Josu San Martin
 * SPDX-License-Identifier: Apache-2.0
 *
 * Pins: ui[5]=MOSI ui[6]=SCK ui[7]=CS_n uo[7]=MISO (host SPI port);
 * everything else belongs to the protocol cores (docs/isa.md §2; NCORES = 2
 * on the IHP tapeout, up to 4 in the RTL).
 */
`default_nettype none
// IMEM: SRAM macros (256 words/bank) by default. Define PE_FLOP_IMEM (and
// optionally PE_BANK_AW) for the flop build used in fast simulations.
`ifdef PE_FLOP_IMEM
`ifndef PE_BANK_AW
`define PE_BANK_AW 4
`endif
`else
`undef PE_BANK_AW
`define PE_BANK_AW 8
`endif
// Number of cores and IMEM banks in this build (the ISA allows up to 4 of each).
// The IHP 6x4 tapeout has 2 of each: 4 cores don't route on this tile's single
// horizontal routing layer (docs/physical.md).
`ifndef PE_NCORES
`define PE_NCORES 2
`endif
`ifndef PE_NBANKS
`define PE_NBANKS `PE_NCORES
`endif
// FIFO depth per direction: 2**PE_FIFO_LOG2 words.
`ifndef PE_FIFO_LOG2
`define PE_FIFO_LOG2 3
`endif

// Two intentional sync/async uses: the reset synchroniser, and CS_n (ui[7]),
// which resets the host port's SCK domain and is synchronised in clk.
/* verilator lint_off SYNCASYNCNET */
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
  (* async_reg = "true", keep = 1 *) reg [1:0] rst_s;
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) rst_s <= 2'b00;
    else rst_s <= {rst_s[0], 1'b1};
  end
  wire rstn = rst_s[1];

  wire [15:0] bus_addr;
  wire        bus_we, bus_commit, bus_snap, bus_cancel;
  wire [31:0] bus_wdata, bus_rdata;
  wire [3:0]  io_out;
  wire        io_oe, quad_en;
  wire [6:0]  chip_uo;

  // host port: SCK = ui[6], CS_n = ui[7], IO[3:0] in = ui[5:2], out = uo[7:4]
  pe_hostport u_host (
      .clk(clk),
      .rst_n(rstn),
      .sck(ui_in[6]),
      .cs_n(ui_in[7]),
      .io_in(ui_in[5:2]),
      .io_out(io_out),
      .io_oe(io_oe),
      .quad_en(quad_en),
      .bus_addr(bus_addr),
      .bus_we(bus_we),
      .bus_wdata(bus_wdata),
      .bus_snap(bus_snap),
      .bus_commit(bus_commit),
      .bus_cancel(bus_cancel),
      .bus_rdata(bus_rdata));

  pe_chip #(.BANK_AW(`PE_BANK_AW), .NCORES(`PE_NCORES), .NBANKS(`PE_NBANKS), .FIFO_LOG2(`PE_FIFO_LOG2)) u_chip (
      .clk(clk),
      .rst_n(rstn),
      .ui_in(ui_in[4:0]),
      .uio_in(uio_in),
      .uo_out(chip_uo),
      .uio_out(uio_out),
      .uio_oe(uio_oe),
      .bus_addr(bus_addr),
      .bus_we(bus_we),
      .bus_wdata(bus_wdata),
      .bus_commit(bus_commit),
      .bus_snap(bus_snap),
      .bus_cancel(bus_cancel),
      .bus_rdata(bus_rdata),
      .quad_en(quad_en));

  assign uo_out[7]   = io_out[3];                       // MISO / IO3
  assign uo_out[6:4] = io_oe ? io_out[2:0] : chip_uo[6:4];  // IO2..0 during quad read data
  assign uo_out[3:0] = chip_uo[3:0];

  wire _unused = &{ena, 1'b0};
endmodule
