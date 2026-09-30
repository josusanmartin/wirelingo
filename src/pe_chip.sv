/*
 * protoemu chip core: 4 cores, IMEM, pins, flags, global registers.
 * Everything except the host SPI slave. Register map: docs/isa.md §6.2.
 * SPDX-License-Identifier: Apache-2.0
 */
`default_nettype none

module pe_chip #(
    parameter BANK_AW = 4
) (
    input  wire        clk,
    input  wire        rst_n,

    input  wire [4:0]  ui_in,     // ui[4:0] (ui[7:5] are the host port)
    input  wire [7:0]  uio_in,
    output wire [6:0]  uo_out,    // uo[6:0] (uo[7] is MISO)
    output wire [7:0]  uio_out,
    output wire [7:0]  uio_oe,

    // host bus
    input  wire [15:0] bus_addr,
    input  wire        bus_we,
    input  wire [31:0] bus_wdata,
    input  wire        bus_commit,
    output reg  [31:0] bus_rdata
);
  localparam [31:0] ID_VALUE = 32'h5045_0002;

  // ---------------------------------------------------------------- global state
  reg [3:0]  enable;
  reg [7:0]  flags;
  reg [7:0]  bank_owner;
  reg [31:0] pin_owner_lo, pin_owner_hi;
  wire [63:0] pin_owner = {pin_owner_hi, pin_owner_lo};
  reg [7:0]  od_mask;
  reg [31:0] pin_out, pin_oe;
  reg [12:0] sync1, sync2;

  wire [31:0] pins_in = {pin_out[31:24], 3'd0, sync2[12:8], 1'b0, pin_out[14:8], sync2[7:0]};

  // ---------------------------------------------------------------- bus decode
  wire        sel_imem = bus_addr[15:10] == 6'd0;
  wire        sel_core = bus_addr[15:7] == 9'h008;     // 0x0400-0x047F
  wire        sel_glob = bus_addr[15:4] == 12'h080;    // 0x0800-0x080F
  wire [1:0]  core_idx = bus_addr[6:5];
  wire [3:0]  goff = bus_addr[3:0];
  wire        ctrl_w = bus_we && sel_glob && goff == 4'h1;
  wire [3:0]  restart = ctrl_w ? bus_wdata[11:8] : 4'd0;
  wire [3:0]  fclear  = ctrl_w ? bus_wdata[19:16] : 4'd0;

  // ---------------------------------------------------------------- IMEM
  wire [39:0]  fetch_addr;
  wire [127:0] bank_rdata;

  pe_imem #(.BANK_AW(BANK_AW)) u_imem (
      .clk(clk),
      .fetch_addr(fetch_addr),
      .bank_owner(bank_owner),
      .we(bus_we && sel_imem),
      .waddr(bus_addr[9:0]),
      .wdata(bus_wdata),
      .bank_rdata(bank_rdata));

  // ---------------------------------------------------------------- cores
  wire [127:0] w_out_en, w_out_val, w_oe_en, w_oe_val, c_rdata;
  wire [31:0]  c_fset, c_fclr;

  genvar c;
  generate
    for (c = 0; c < 4; c = c + 1) begin : g_core
      // instr = bank read data for the bank the core fetched from last cycle
      reg  [1:0] fbank;
      always @(posedge clk or negedge rst_n) begin
        if (!rst_n) fbank <= c[1:0];
        else fbank <= fetch_addr[10*c+8 +: 2];
      end
      pe_core #(.IDX(c[1:0])) u_core (
          .clk(clk),
          .rst_n(rst_n),
          .enable(enable[c]),
          .restart(restart[c]),
          .fifo_clear(fclear[c]),
          .fetch_addr(fetch_addr[10*c +: 10]),
          .instr(bank_rdata[32*fbank +: 32]),
          .bank_owner(bank_owner),
          .pins_in(pins_in),
          .pin_out_cur(pin_out),
          .wr_out_en(w_out_en[32*c +: 32]),
          .wr_out_val(w_out_val[32*c +: 32]),
          .wr_oe_en(w_oe_en[32*c +: 32]),
          .wr_oe_val(w_oe_val[32*c +: 32]),
          .flags(flags),
          .flag_set(c_fset[8*c +: 8]),
          .flag_clr(c_fclr[8*c +: 8]),
          .bus_sel(sel_core && core_idx == c),
          .bus_off(bus_addr[4:0]),
          .bus_we(bus_we),
          .bus_wdata(bus_wdata),
          .bus_commit(bus_commit),
          .bus_rdata(c_rdata[32*c +: 32]));
    end
  endgenerate

  // ---------------------------------------------------------------- pins
  reg [31:0] pin_out_n, pin_oe_n;
  integer p;
  always @(*) begin
    pin_out_n = pin_out;
    pin_oe_n = pin_oe;
    for (p = 0; p < 32; p = p + 1) begin
      if (w_out_en[32*pin_owner[2*p +: 2] + p]) pin_out_n[p] = w_out_val[32*pin_owner[2*p +: 2] + p];
      if (w_oe_en[32*pin_owner[2*p +: 2] + p])  pin_oe_n[p]  = w_oe_val[32*pin_owner[2*p +: 2] + p];
    end
  end

  assign uo_out  = pin_out[14:8];
  assign uio_out = pin_out[7:0] & ~od_mask;
  assign uio_oe  = (pin_oe[7:0] & ~od_mask) | (~pin_out[7:0] & od_mask);

  // ---------------------------------------------------------------- flags
  wire [7:0] host_fset = (bus_we && sel_glob && goff == 4'h2) ? bus_wdata[7:0] : 8'd0;
  wire [7:0] host_fclr = (bus_we && sel_glob && goff == 4'h3) ? bus_wdata[7:0] : 8'd0;
  wire [7:0] all_set = c_fset[7:0] | c_fset[15:8] | c_fset[23:16] | c_fset[31:24] | host_fset;
  wire [7:0] all_clr = c_fclr[7:0] | c_fclr[15:8] | c_fclr[23:16] | c_fclr[31:24] | host_fclr;

  // ---------------------------------------------------------------- bus read
  always @(*) begin
    bus_rdata = 32'd0;
    if (sel_core) bus_rdata = c_rdata[32*core_idx +: 32];
    else if (sel_glob) begin
      case (goff)
        4'h0: bus_rdata = ID_VALUE;
        4'h1: bus_rdata = {28'd0, enable};
        4'h2, 4'h3: bus_rdata = {24'd0, flags};
        4'h4: bus_rdata = {24'd0, bank_owner};
        4'h5: bus_rdata = pin_owner[31:0];
        4'h6: bus_rdata = pin_owner[63:32];
        4'h7: bus_rdata = {24'd0, od_mask};
        4'h8: bus_rdata = pins_in;
        4'h9: bus_rdata = pin_out;
        4'hA: bus_rdata = pin_oe;
        default: bus_rdata = 32'd0;
      endcase
    end
  end

  // ---------------------------------------------------------------- config registers
  // host-only writes: clock-gated instead of a hold mux per bit
  wire [3:0] gcfg_gclk;
  genvar gi;
  generate
    for (gi = 0; gi < 4; gi = gi + 1) begin : g_gcfg_cg
      pe_cg u_cg (.clk(clk), .en(bus_we && sel_glob && goff == 4'h4 + gi), .gclk(gcfg_gclk[gi]));
    end
  endgenerate
  always @(posedge gcfg_gclk[0] or negedge rst_n) if (!rst_n) bank_owner <= 8'hE4; else bank_owner <= bus_wdata[7:0];
  always @(posedge gcfg_gclk[1] or negedge rst_n) if (!rst_n) pin_owner_lo <= 32'd0; else pin_owner_lo <= bus_wdata;
  always @(posedge gcfg_gclk[2] or negedge rst_n) if (!rst_n) pin_owner_hi <= 32'd0; else pin_owner_hi <= bus_wdata;
  always @(posedge gcfg_gclk[3] or negedge rst_n) if (!rst_n) od_mask <= 8'd0; else od_mask <= bus_wdata[7:0];

  // ---------------------------------------------------------------- sequential
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      enable <= 4'd0;
      flags <= 8'd0;
      pin_out <= 32'd0;
      pin_oe <= 32'd0;
      sync1 <= 13'd0;
      sync2 <= 13'd0;
    end else begin
      sync1 <= {ui_in, uio_in};
      sync2 <= sync1;
      pin_out <= (bus_we && sel_glob && goff == 4'h9) ? bus_wdata : pin_out_n;
      pin_oe <= (bus_we && sel_glob && goff == 4'hA) ? bus_wdata : pin_oe_n;
      flags <= (flags & ~all_clr) | all_set;
      if (bus_we && sel_glob) begin
        case (goff)
          4'h1: enable <= bus_wdata[3:0];
          default: ;
        endcase
      end
    end
  end
endmodule
