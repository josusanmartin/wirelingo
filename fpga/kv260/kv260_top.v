/*
 * KV260 bring-up wrapper for protoemu (tt_um_josusanmartin_protoemu).
 *
 * The Zynq PS reaches the Tiny Tapeout pins through AXI GPIO (pe_ps block
 * design, build.tcl), so Linux can bit-bang the SPI host port like the TT demo
 * board's RP2350 does:
 *
 *   gpio0 ch1 (out, 0xA000_0000): [7:0] ui_in, [8] rst_n, [23:16] uio pad
 *                                 value for pins protoemu doesn't drive
 *   gpio0 ch2 (in,  0xA000_0008): [7:0] uo_out, [15:8] uio_out, [23:16] uio_oe,
 *                                 [24] MMCM locked
 *   gpio1 ch1 (in,  0xA001_0000): free-running Gray-coded count of protoemu
 *                                 clk cycles (frequency check from Linux)
 *
 * pe_spim (AXI4-Lite at 0xA002_0000) is a fabric SPI master; SEL.FABRIC gives
 * it the host-port pins (CS_n, SCK, IO[3:0]) instead of the GPIO path. Its
 * clock (mclk) is protoemu's clock or, with SEL.ALT, an alternate MMCM clock
 * (1200 MHz / ALT_DIV) that is not phase-aligned with it. SCK is mclk gated
 * by a BUFGCE, then a BUFGCTRL selects it or the GPIO's SCK.
 *
 * protoemu runs on an MMCM clock derived from pl_clk0 (100 MHz), so it is
 * asynchronous to the AXI side. The uio pads are modelled:
 * uio_in[i] = uio_oe[i] ? uio_out[i] : the PS's value, unless a PMOD pin
 * carries uio[i] (then the pad's level).
 *
 * PMOD J2: pmod[i] is enabled by PMODn[8i+7] and carries TT pin PMODn[8i+4:8i]:
 * 0-7 uio[0..7] (driven when uio_oe, pad level to uio_in), 8-14 uo[0..6]
 * (driven), 16-20 ui[0..4] (input, replaces the GPIO bit). Disabled pins are
 * inputs only (high impedance). Reset: all disabled.
 * SPDX-License-Identifier: Apache-2.0
 */
`default_nettype none

module kv260_top #(
    parameter integer CLK_DIV = 24,      // protoemu clock: 1200 MHz / CLK_DIV (24 -> 50 MHz, 25 -> 48 MHz)
    parameter real    ALT_DIV = 26.125   // alternate master clock: 1200 MHz / ALT_DIV (45.93 MHz)
) (
    // KV260 carrier fan enable (active low; package pin A12, kv260.xdc). The
    // starter-kit platform drives it with the TTC0 PWM; this design keeps the
    // fan fully on.
    output wire       fan_en_b,
    inout  wire [7:0] pmod
);
  assign fan_en_b = 1'b0;

  wire        pl_clk0, pl_resetn0, aresetn;
  wire [31:0] gpio0_o, gpio0_i, gpio1_i;
  wire [15:0] awaddr, araddr;
  wire        awvalid, awready, wvalid, wready, bvalid, bready, arvalid, arready, rvalid, rready;
  wire [31:0] wdata, rdata;

  pe_ps_wrapper u_ps (
      .pl_clk0(pl_clk0),
      .pl_resetn0(pl_resetn0),
      .periph_aresetn(aresetn),
      .gpio0_o_tri_o(gpio0_o),
      .gpio0_i_tri_i(gpio0_i),
      .gpio1_i_tri_i(gpio1_i),
      .spim_axi_awaddr(awaddr),
      .spim_axi_awprot(),
      .spim_axi_awvalid(awvalid),
      .spim_axi_awready(awready),
      .spim_axi_wdata(wdata),
      .spim_axi_wstrb(),
      .spim_axi_wvalid(wvalid),
      .spim_axi_wready(wready),
      .spim_axi_bresp(2'b00),
      .spim_axi_bvalid(bvalid),
      .spim_axi_bready(bready),
      .spim_axi_araddr(araddr),
      .spim_axi_arprot(),
      .spim_axi_arvalid(arvalid),
      .spim_axi_arready(arready),
      .spim_axi_rdata(rdata),
      .spim_axi_rresp(2'b00),
      .spim_axi_rvalid(rvalid),
      .spim_axi_rready(rready));

  // clocks: 100 MHz * 12 = 1200 MHz VCO
  wire clk_fb, clk_mmcm, clk_alt_mmcm, clk, mclk, locked;
  MMCME4_BASE #(
      .CLKIN1_PERIOD(10.0),
      .DIVCLK_DIVIDE(1),
      .CLKFBOUT_MULT_F(12.0),
      .CLKOUT0_DIVIDE_F(ALT_DIV),
      .CLKOUT1_DIVIDE(CLK_DIV)
  ) u_mmcm (
      .CLKIN1(pl_clk0),
      .CLKFBIN(clk_fb),
      .CLKFBOUT(clk_fb),
      .CLKOUT0(clk_alt_mmcm),
      .CLKOUT1(clk_mmcm),
      .LOCKED(locked),
      .RST(!pl_resetn0),
      .PWRDWN(1'b0),
      .CLKFBOUTB(), .CLKOUT0B(), .CLKOUT1B(), .CLKOUT2(), .CLKOUT2B(),
      .CLKOUT3(), .CLKOUT3B(), .CLKOUT4(), .CLKOUT5(), .CLKOUT6());
  BUFG u_clk_buf (.I(clk_mmcm), .O(clk));

  wire sel_fabric, sel_alt, quad_pins, sck_ce, m_cs_n;
  wire [3:0]  m_io;
  wire [31:0] pmod0, pmod1;
  wire [7:0]  pad_i;
  BUFGMUX_CTRL u_mclk_mux (.I0(clk_mmcm), .I1(clk_alt_mmcm), .S(sel_alt), .O(mclk));

  // SCK: the GPIO bit, or mclk gated per beat. CS_n is high whenever the
  // selection changes, which resets the port's SCK domain, so the BUFGCTRL
  // may switch asynchronously.
  wire sck_fab, sck;
  BUFGCE u_sck_gate (.I(mclk), .CE(sck_ce), .O(sck_fab));
  BUFGCTRL #(.PRESELECT_I0("TRUE"), .PRESELECT_I1("FALSE")) u_sck_mux (
      .I0(gpio0_o[6]), .I1(sck_fab),
      .S0(!sel_fabric), .S1(sel_fabric),
      .CE0(1'b1), .CE1(1'b1),
      .IGNORE0(1'b1), .IGNORE1(1'b1),
      .O(sck));

  wire [7:0] uo_out, uio_out, uio_oe;

  // PMOD routing
  reg  [7:0] pad_o, pad_oe, uio_ext, ui_ext;
  reg  [7:0] uio_mapped, ui_mapped, f;
  integer i;
  always @(*) begin
    uio_mapped = 8'd0;
    ui_mapped = 8'd0;
    uio_ext = gpio0_o[23:16];
    ui_ext = {3'd0, gpio0_o[4:0]};
    pad_o = 8'd0;
    pad_oe = 8'd0;
    for (i = 7; i >= 0; i = i - 1) begin              // pin 0 wins if two carry one signal
      f = (i < 4) ? pmod0[8*i +: 8] : pmod1[8*(i-4) +: 8];
      if (f[7]) begin
        if (f[4:3] == 2'b00) begin                    // uio[f[2:0]]
          pad_o[i] = uio_out[f[2:0]];
          pad_oe[i] = uio_oe[f[2:0]];
          uio_mapped[f[2:0]] = 1'b1;
          uio_ext[f[2:0]] = pad_i[i];
        end else if (f[4:3] == 2'b01 && f[2:0] != 3'd7) begin   // uo[f[2:0]]
          pad_o[i] = uo_out[f[2:0]];
          pad_oe[i] = 1'b1;
        end else if (f[4:3] == 2'b10 && f[2:0] <= 3'd4) begin   // ui[f[2:0]]
          ui_mapped[f[2:0]] = 1'b1;
          ui_ext[f[2:0]] = pad_i[i];
        end
      end
    end
  end
  genvar g;
  generate
    for (g = 0; g < 8; g = g + 1) begin : g_pmod
      IOBUF u_io (.I(pad_o[g]), .T(!pad_oe[g]), .O(pad_i[g]), .IO(pmod[g]));
    end
  endgenerate

  // host-port pins: GPIO path or fabric master
  wire [7:0] ui_in;
  assign ui_in[7]   = sel_fabric ? m_cs_n : gpio0_o[7];
  assign ui_in[6]   = sck;
  assign ui_in[5]   = sel_fabric ? m_io[3] : gpio0_o[5];
  assign ui_in[4:2] = (sel_fabric && quad_pins) ? m_io[2:0] : ui_ext[4:2];
  assign ui_in[1:0] = ui_ext[1:0];

  wire [7:0] uio_in = (uio_mapped & uio_ext) | (~uio_mapped & ((uio_oe & uio_out) | (~uio_oe & uio_ext)));

  tt_um_josusanmartin_protoemu u_pe (
      .ui_in(ui_in),
      .uo_out(uo_out),
      .uio_in(uio_in),
      .uio_out(uio_out),
      .uio_oe(uio_oe),
      .ena(1'b1),
      .clk(clk),
      .rst_n(gpio0_o[8] & locked));

  pe_spim u_spim (
      .aclk(pl_clk0),
      .aresetn(aresetn),
      .s_awaddr(awaddr),
      .s_awvalid(awvalid),
      .s_awready(awready),
      .s_wdata(wdata),
      .s_wvalid(wvalid),
      .s_wready(wready),
      .s_bvalid(bvalid),
      .s_bready(bready),
      .s_araddr(araddr),
      .s_arvalid(arvalid),
      .s_arready(arready),
      .s_rdata(rdata),
      .s_rvalid(rvalid),
      .s_rready(rready),
      .sel_fabric(sel_fabric),
      .sel_alt(sel_alt),
      .quad_pins(quad_pins),
      .pmod0(pmod0),
      .pmod1(pmod1),
      .pmod_in(pad_i),
      .mclk(mclk),
      .sck_ce(sck_ce),
      .cs_n(m_cs_n),
      .io_o(m_io),
      .io_i(uo_out[7:4]),
      .pclk(clk),
      .cs_pin(ui_in[7]));

  assign gpio0_i = {7'd0, locked, uio_oe, uio_out, uo_out};

  // cycle counter, Gray-coded so the asynchronous GPIO sampling can't tear it
  reg [31:0] cyc, cyc_gray;
  always @(posedge clk) begin
    cyc <= cyc + 1'b1;
    cyc_gray <= cyc ^ (cyc >> 1);
  end
  assign gpio1_i = cyc_gray;
endmodule
