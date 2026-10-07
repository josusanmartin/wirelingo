`default_nettype none
`timescale 1ns / 1ps
// Simulation harness for kv260_top: the PS block design and the Xilinx
// primitives are replaced by the models below; cocotb drives the GPIO words,
// pe_spim's AXI4-Lite port and what is wired to the PMOD pads.
module tb_kv260 ();
  reg         pl_clk, pe_clk, alt_clk, d_clk, aresetn;
  reg  [31:0] gpio0_o;
  wire [31:0] gpio0_i, gpio1_i;
  reg  [15:0] s_awaddr, s_araddr;
  reg  [31:0] s_wdata;
  reg         s_awvalid, s_wvalid, s_bready, s_arvalid, s_rready;
  wire        s_awready, s_wready, s_bvalid, s_arready, s_rvalid;
  wire [31:0] s_rdata;
  // pe_lab's AXI4-Lite port
  reg  [15:0] l_awaddr, l_araddr;
  reg  [31:0] l_wdata;
  reg         l_awvalid, l_wvalid, l_bready, l_arvalid, l_rready;
  wire        l_awready = dut.l_awready, l_wready = dut.l_wready, l_bvalid = dut.l_bvalid;
  wire        l_arready = dut.l_arready, l_rvalid = dut.l_rvalid;
  wire [31:0] l_rdata = dut.l_rdata;
  // PMOD J2 pads as the outside world sees them: ext_oe/ext_val = a strong
  // driver (a USB host's D+/D-), pull_up/pull_dn = resistors (the device's
  // 1.5k pull-up; the host's 15k pull-downs), and a weak keeper per pin: an
  // open pin keeps the level it was last driven to (its charge), so a pin
  // that is driven low and released reads low unless something pulls it.
  reg  [7:0]  ext_oe = 8'd0, ext_val = 8'd0, pull_up = 8'd0, pull_dn = 8'd0;
  reg  [7:0]  keep = 8'd0;
  wire [7:0]  pmod;
  genvar gp;
  generate
    for (gp = 0; gp < 8; gp = gp + 1) begin : g_pad
      assign pmod[gp] = ext_oe[gp] ? ext_val[gp] : 1'bz;
      assign (pull1, pull0) pmod[gp] = pull_up[gp] ? 1'b1 : pull_dn[gp] ? 1'b0 : 1'bz;
      assign (weak1, weak0) pmod[gp] = keep[gp];
      always @(pmod[gp]) if (pmod[gp] === 1'b0 || pmod[gp] === 1'b1) keep[gp] = pmod[gp];
    end
  endgenerate
  kv260_top dut (.fan_en_b(), .pmod(pmod));
  assign gpio0_i = dut.gpio0_i;
  assign gpio1_i = dut.gpio1_i;
  assign s_awready = dut.awready;
  assign s_wready = dut.wready;
  assign s_bvalid = dut.bvalid;
  assign s_arready = dut.arready;
  assign s_rvalid = dut.rvalid;
  assign s_rdata = dut.rdata;
endmodule

module pe_ps_wrapper (
    output wire pl_clk0, output wire pl_resetn0, output wire periph_aresetn,
    output wire [31:0] gpio0_o_tri_o, input wire [31:0] gpio0_i_tri_i, input wire [31:0] gpio1_i_tri_i,
    output wire [15:0] spim_axi_awaddr, output wire [2:0] spim_axi_awprot, output wire spim_axi_awvalid,
    input wire spim_axi_awready, output wire [31:0] spim_axi_wdata, output wire [3:0] spim_axi_wstrb,
    output wire spim_axi_wvalid, input wire spim_axi_wready, input wire [1:0] spim_axi_bresp,
    input wire spim_axi_bvalid, output wire spim_axi_bready, output wire [15:0] spim_axi_araddr,
    output wire [2:0] spim_axi_arprot, output wire spim_axi_arvalid, input wire spim_axi_arready,
    input wire [31:0] spim_axi_rdata, input wire [1:0] spim_axi_rresp, input wire spim_axi_rvalid,
    output wire spim_axi_rready,
    output wire [15:0] lab_axi_awaddr, output wire [2:0] lab_axi_awprot, output wire lab_axi_awvalid,
    input wire lab_axi_awready, output wire [31:0] lab_axi_wdata, output wire [3:0] lab_axi_wstrb,
    output wire lab_axi_wvalid, input wire lab_axi_wready, input wire [1:0] lab_axi_bresp,
    input wire lab_axi_bvalid, output wire lab_axi_bready, output wire [15:0] lab_axi_araddr,
    output wire [2:0] lab_axi_arprot, output wire lab_axi_arvalid, input wire lab_axi_arready,
    input wire [31:0] lab_axi_rdata, input wire [1:0] lab_axi_rresp, input wire lab_axi_rvalid,
    output wire lab_axi_rready);
  assign pl_clk0 = tb_kv260.pl_clk;
  assign pl_resetn0 = 1'b1;
  assign periph_aresetn = tb_kv260.aresetn;
  assign gpio0_o_tri_o = tb_kv260.gpio0_o;
  assign spim_axi_awaddr = tb_kv260.s_awaddr;
  assign spim_axi_awprot = 3'd0;
  assign spim_axi_awvalid = tb_kv260.s_awvalid;
  assign spim_axi_wdata = tb_kv260.s_wdata;
  assign spim_axi_wstrb = 4'hF;
  assign spim_axi_wvalid = tb_kv260.s_wvalid;
  assign spim_axi_bready = tb_kv260.s_bready;
  assign spim_axi_araddr = tb_kv260.s_araddr;
  assign spim_axi_arprot = 3'd0;
  assign spim_axi_arvalid = tb_kv260.s_arvalid;
  assign spim_axi_rready = tb_kv260.s_rready;
  assign lab_axi_awaddr = tb_kv260.l_awaddr;
  assign lab_axi_awprot = 3'd0;
  assign lab_axi_awvalid = tb_kv260.l_awvalid;
  assign lab_axi_wdata = tb_kv260.l_wdata;
  assign lab_axi_wstrb = 4'hF;
  assign lab_axi_wvalid = tb_kv260.l_wvalid;
  assign lab_axi_bready = tb_kv260.l_bready;
  assign lab_axi_araddr = tb_kv260.l_araddr;
  assign lab_axi_arprot = 3'd0;
  assign lab_axi_arvalid = tb_kv260.l_arvalid;
  assign lab_axi_rready = tb_kv260.l_rready;
endmodule

module BUFG (input wire I, output wire O);
  assign O = I;
endmodule

// UltraScale BUFGCE, CE_TYPE SYNC (unisims): CE is latched while I is low
module BUFGCE (input wire I, input wire CE, output wire O);
  reg en = 1'b0;
  always @(*) if (!I) en = CE;
  assign O = I & en;
endmodule

module BUFGMUX_CTRL (input wire I0, input wire I1, input wire S, output wire O);
  assign O = S ? I1 : I0;
endmodule

module BUFGCTRL #(parameter PRESELECT_I0 = "TRUE", parameter PRESELECT_I1 = "FALSE") (
    input wire I0, I1, S0, S1, CE0, CE1, IGNORE0, IGNORE1, output wire O);
  assign O = (S1 & CE1) ? I1 : (S0 & CE0) ? I0 : 1'b0;
endmodule

module IOBUF (input wire I, input wire T, output wire O, inout wire IO);
  assign IO = T ? 1'bz : I;
  assign O = IO;
endmodule

module MMCME4_BASE #(
    parameter real CLKIN1_PERIOD = 10.0, parameter integer DIVCLK_DIVIDE = 1,
    parameter real CLKFBOUT_MULT_F = 12.0, parameter real CLKOUT0_DIVIDE_F = 24.0,
    parameter integer CLKOUT1_DIVIDE = 24, parameter integer CLKOUT2_DIVIDE = 3
) (
    input wire CLKIN1, CLKFBIN, RST, PWRDWN,
    output wire CLKFBOUT, CLKOUT0, CLKOUT1, LOCKED,
    output wire CLKFBOUTB, CLKOUT0B, CLKOUT1B, CLKOUT2, CLKOUT2B, CLKOUT3, CLKOUT3B,
    output wire CLKOUT4, CLKOUT5, CLKOUT6);
  assign CLKOUT0 = tb_kv260.alt_clk;  // the alternate master clock
  assign CLKOUT1 = tb_kv260.pe_clk;   // protoemu's clock
  assign CLKOUT2 = tb_kv260.d_clk;    // the SPI device's clock (8 x pe_clk, aligned)
  assign LOCKED = !RST;
  assign CLKFBOUT = 1'b0;
endmodule
