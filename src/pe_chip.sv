/*
 * protoemu chip core: 4 cores, IMEM, pins, flags, global registers.
 * Everything except the host SPI slave. Register map: docs/isa.md §6.2.
 * SPDX-License-Identifier: Apache-2.0
 */
`default_nettype none

module pe_chip #(
    parameter BANK_AW = 4,
    parameter NCORES = 4,  // cores >= NCORES are tied off
    parameter NBANKS = 4,  // IMEM banks >= NBANKS don't exist
    parameter FIFO_LOG2 = 2  // FIFO depth 2**FIFO_LOG2
) (
    input  wire        clk,
    input  wire        rst_n,

    input  wire [4:0]  ui_in,     // ui[4:0] (ui[7:5] are the host port)
    input  wire [7:0]  uio_in,
    output wire [6:0]  uo_out,    // uo[6:0] (uo[7] is the host port's; uo[6:4] too in quad reads)
    output wire [7:0]  uio_out,
    output wire [7:0]  uio_oe,

    // host bus
    input  wire [15:0] bus_addr,
    input  wire        bus_we,
    input  wire [31:0] bus_wdata,
    input  wire        bus_commit,
    input  wire        bus_snap,     // bus_rdata captured for a read word (precedes its commit)
    input  wire        bus_cancel,   // transaction over: drop snapped, uncommitted reads
    output reg  [31:0] bus_rdata,
    output wire        quad_en       // HOSTCFG.QUAD_EN
);
  localparam [31:0] ID_VALUE = 32'h5045_0002;

  // ---------------------------------------------------------------- global state
  reg [3:0]  enable;
  reg        hostcfg;                    // HOSTCFG.QUAD_EN
  assign quad_en = hostcfg;
  reg [7:0]  flags;
  reg [7:0]  bank_owner;
  reg [31:0] pin_owner_lo, pin_owner_hi;
  wire [63:0] pin_owner = {pin_owner_hi, pin_owner_lo};
  reg [7:0]  od_mask;
  reg [31:0] pin_out, pin_oe;
  reg [12:0] sync1, sync2;

  // pins as driven: the registers, with the line unit's transmit pins over them
  wire [31:0] line_mask, line_val_o;
  wire [31:0] eff_out = (pin_out & ~line_mask) | (line_val_o & line_mask);
  wire [31:0] eff_oe  = pin_oe | line_mask;
  wire [31:0] pins_in = {eff_out[31:24], 3'd0, sync2[12:8], 1'b0, eff_out[14:8], sync2[7:0]};

  // ---------------------------------------------------------------- bus decode
  wire        sel_imem = bus_addr[15:10] == 6'd0;
  wire        sel_core = bus_addr[15:7] == 9'h008;     // 0x0400-0x047F
  wire        sel_glob = bus_addr[15:4] == 12'h080;    // 0x0800-0x080F
  wire [1:0]  core_idx = bus_addr[6:5];
  wire [3:0]  goff = bus_addr[3:0];
  wire        ctrl_w = bus_we && sel_glob && goff == 4'h1;
  // CTRL writes act one cycle late (docs/isa.md §5): restart gates every
  // core's execute stage, so it must come straight from a flop. The data
  // flops need no hold mux: they are only used while ctrl_pend is set.
  reg         ctrl_pend;
  reg  [11:0] ctrl_data;                  // {fclear, restart, enable}
  wire [3:0]  restart = ctrl_pend ? ctrl_data[7:4] : 4'd0;
  wire [3:0]  fclear  = ctrl_pend ? ctrl_data[11:8] : 4'd0;

  // ---------------------------------------------------------------- IMEM
  wire [39:0]  fetch_addr;
  wire [127:0] bank_rdata;

  pe_imem #(.BANK_AW(BANK_AW), .NBANKS(NBANKS)) u_imem (
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
  // line unit <-> core 0 (the other cores' line outputs are unused)
  wire        line_room, line_ok, line_t;
  wire [5:0]  line_k;
  wire [31:0] line_val, line_r_stat;
  wire [3:0]  lp_push, lp_msb, lp_in_shr, lp_take;
  wire [127:0] lp_data;
  wire [23:0] lp_n, lp_in_n;

  generate
    for (c = 0; c < 4; c = c + 1) begin : g_core
      if (c < NCORES) begin : g_on
        // instr = bank read data for the bank the core fetched from last cycle
        reg  [1:0] fbank;
        always @(posedge clk or negedge rst_n) begin
          if (!rst_n) fbank <= c[1:0];
          else fbank <= fetch_addr[10*c+8 +: 2];
        end
        pe_core #(.IDX(c[1:0]), .FIFO_LOG2(FIFO_LOG2)) u_core (
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
            .bus_snap(bus_snap),
            .bus_cancel(bus_cancel),
            .bus_rdata(c_rdata[32*c +: 32]),
            .line_room(c == 0 && line_room),
            .line_ok(c == 0 && line_ok),
            .line_k(c == 0 ? line_k : 6'd0),
            .line_t(c == 0 && line_t),
            .line_val(c == 0 ? line_val : 32'd0),
            .line_push(lp_push[c]),
            .line_push_data(lp_data[32*c +: 32]),
            .line_push_n(lp_n[6*c +: 6]),
            .line_push_msb(lp_msb[c]),
            .line_in_n(lp_in_n[6*c +: 6]),
            .line_in_shr(lp_in_shr[c]),
            .line_take(lp_take[c]));
      end else begin : g_off
        assign fetch_addr[10*c +: 10] = 10'd0;
        assign w_out_en[32*c +: 32] = 32'd0;
        assign w_out_val[32*c +: 32] = 32'd0;
        assign w_oe_en[32*c +: 32] = 32'd0;
        assign w_oe_val[32*c +: 32] = 32'd0;
        assign c_fset[8*c +: 8] = 8'd0;
        assign c_fclr[8*c +: 8] = 8'd0;
        assign c_rdata[32*c +: 32] = 32'd0;
        assign {lp_push[c], lp_msb[c], lp_in_shr[c], lp_take[c]} = 4'd0;
        assign lp_data[32*c +: 32] = 32'd0;
        assign lp_n[6*c +: 6] = 6'd0;
        assign lp_in_n[6*c +: 6] = 6'd0;
      end
    end
  endgenerate

  // ---------------------------------------------------------------- line unit (core 0)
  pe_line u_line (
      .clk(clk), .rst_n(rst_n),
      .cfg_we(bus_we && sel_glob && goff == 4'hC),
      .div_we(bus_we && sel_glob && goff == 4'hD),
      .pins_we(bus_we && sel_glob && goff == 4'hE),
      .wdata(bus_wdata),
      .r_stat(line_r_stat),
      .pins_in(pins_in), .drv_mask(line_mask), .drv_val(line_val_o),
      .room(line_room), .push(lp_push[0]), .push_data(lp_data[31:0]), .push_n(lp_n[5:0]),
      .push_msb(lp_msb[0]), .in_n(lp_in_n[5:0]), .in_shr(lp_in_shr[0]),
      .in_ok(line_ok), .in_k(line_k), .in_t(line_t), .in_val(line_val), .take(lp_take[0]));

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

  assign uo_out  = eff_out[14:8];
  assign uio_out = eff_out[7:0] & ~od_mask;
  assign uio_oe  = (eff_oe[7:0] & ~od_mask) | (~eff_out[7:0] & od_mask);

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
        4'h8: bus_rdata = pins_in;
        4'h9: bus_rdata = pin_out;
        4'hA: bus_rdata = pin_oe;
        4'hF: bus_rdata = line_r_stat;
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
      ctrl_pend <= 1'b0;
      ctrl_data <= 12'd0;
      flags <= 8'd0;
      pin_out <= 32'd0;
      pin_oe <= 32'd0;
      hostcfg <= 1'b0;
      sync1 <= 13'd0;
      sync2 <= 13'd0;
    end else begin
      sync1 <= {ui_in, uio_in};
      sync2 <= sync1;
      pin_out <= (bus_we && sel_glob && goff == 4'h9) ? bus_wdata : pin_out_n;
      pin_oe <= (bus_we && sel_glob && goff == 4'hA) ? bus_wdata : pin_oe_n;
      if (bus_we && sel_glob && goff == 4'hB) hostcfg <= bus_wdata[0];
      flags <= (flags & ~all_clr) | all_set;
      ctrl_pend <= ctrl_w;
      ctrl_data <= {bus_wdata[19:16], bus_wdata[11:8], bus_wdata[3:0]};
      if (ctrl_pend) enable <= ctrl_data[3:0];
    end
  end
endmodule
