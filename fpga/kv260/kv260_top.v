/*
 * KV260 bring-up wrapper for protoemu (tt_um_josusanmartin_protoemu).
 *
 * The Zynq PS reaches the Tiny Tapeout pins through AXI GPIO (pe_ps block
 * design, build.tcl), so Linux bit-bangs the SPI host port like the TT demo
 * board's RP2350 does:
 *
 *   gpio0 ch1 (out, 0xA000_0000): [7:0] ui_in, [8] rst_n, [23:16] uio pad
 *                                 value for pins protoemu doesn't drive
 *   gpio0 ch2 (in,  0xA000_0008): [7:0] uo_out, [15:8] uio_out, [23:16] uio_oe,
 *                                 [24] MMCM locked
 *   gpio1 ch1 (in,  0xA001_0000): free-running Gray-coded count of protoemu
 *                                 clk cycles (frequency check from Linux)
 *
 * protoemu runs on an MMCM clock derived from pl_clk0 (100 MHz), so it is
 * asynchronous to the AXI side, as the chip is to its host. The uio pads are
 * modelled: uio_in[i] = uio_oe[i] ? uio_out[i] : the PS's value.
 * The only package pin is the carrier's fan enable, held on.
 * SPDX-License-Identifier: Apache-2.0
 */
`default_nettype none

module kv260_top #(
    parameter integer CLK_DIV = 24  // 1200 MHz VCO / CLK_DIV: 24 -> 50 MHz, 25 -> 48 MHz
) (
    // KV260 carrier fan enable (active low; package pin A12, kv260.xdc). The
    // starter-kit platform drives it with the TTC0 PWM; this design keeps the
    // fan fully on.
    output wire fan_en_b
);
  assign fan_en_b = 1'b0;

  wire        pl_clk0, pl_resetn0;
  wire [31:0] gpio0_o, gpio0_i, gpio1_i;

  pe_ps_wrapper u_ps (
      .pl_clk0(pl_clk0),
      .pl_resetn0(pl_resetn0),
      .gpio0_o_tri_o(gpio0_o),
      .gpio0_i_tri_i(gpio0_i),
      .gpio1_i_tri_i(gpio1_i));

  // protoemu clock: 100 MHz * 12 = 1200 MHz VCO
  wire clk_fb, clk_mmcm, clk, locked;
  MMCME4_BASE #(
      .CLKIN1_PERIOD(10.0),
      .DIVCLK_DIVIDE(1),
      .CLKFBOUT_MULT_F(12.0),
      .CLKOUT0_DIVIDE_F(CLK_DIV)
  ) u_mmcm (
      .CLKIN1(pl_clk0),
      .CLKFBIN(clk_fb),
      .CLKFBOUT(clk_fb),
      .CLKOUT0(clk_mmcm),
      .LOCKED(locked),
      .RST(!pl_resetn0),
      .PWRDWN(1'b0),
      .CLKFBOUTB(), .CLKOUT0B(), .CLKOUT1(), .CLKOUT1B(), .CLKOUT2(), .CLKOUT2B(),
      .CLKOUT3(), .CLKOUT3B(), .CLKOUT4(), .CLKOUT5(), .CLKOUT6());
  BUFG u_clk_buf (.I(clk_mmcm), .O(clk));

  // SCK clocks the host port's shift registers directly (source-synchronous
  // port): give it a global buffer as the chip's clock tree does.
  wire [7:0] ui_in;
  wire       sck;
  BUFG u_sck_buf (.I(gpio0_o[6]), .O(sck));
  assign ui_in = {gpio0_o[7], sck, gpio0_o[5:0]};

  wire [7:0] uo_out, uio_out, uio_oe;
  wire [7:0] uio_in = (uio_oe & uio_out) | (~uio_oe & gpio0_o[23:16]);

  tt_um_josusanmartin_protoemu u_pe (
      .ui_in(ui_in),
      .uo_out(uo_out),
      .uio_in(uio_in),
      .uio_out(uio_out),
      .uio_oe(uio_oe),
      .ena(1'b1),
      .clk(clk),
      .rst_n(gpio0_o[8] & locked));

  assign gpio0_i = {7'd0, locked, uio_oe, uio_out, uo_out};

  // cycle counter, Gray-coded so the asynchronous GPIO sampling can't tear it
  reg [31:0] cyc, cyc_gray;
  always @(posedge clk) begin
    cyc <= cyc + 1'b1;
    cyc_gray <= cyc ^ (cyc >> 1);
  end
  assign gpio1_i = cyc_gray;
endmodule
