`default_nettype none
`timescale 1ns / 1ps
// Simulation harness for kv260_top: the PS block design and the Xilinx
// primitives are replaced by the models below; cocotb drives the GPIO words
// and pe_spim's AXI4-Lite port.
module tb_kv260 ();
  reg         pl_clk, pe_clk, alt_clk, aresetn;
  reg  [31:0] gpio0_o;
  wire [31:0] gpio0_i, gpio1_i;
  reg  [15:0] s_awaddr, s_araddr;
  reg  [31:0] s_wdata;
  reg         s_awvalid, s_wvalid, s_bready, s_arvalid, s_rready;
  wire        s_awready, s_wready, s_bvalid, s_arready, s_rvalid;
  wire [31:0] s_rdata;
  wire [7:0]  pmod;
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
    output wire spim_axi_rready);
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
    parameter integer CLKOUT1_DIVIDE = 24
) (
    input wire CLKIN1, CLKFBIN, RST, PWRDWN,
    output wire CLKFBOUT, CLKOUT0, CLKOUT1, LOCKED,
    output wire CLKFBOUTB, CLKOUT0B, CLKOUT1B, CLKOUT2, CLKOUT2B, CLKOUT3, CLKOUT3B,
    output wire CLKOUT4, CLKOUT5, CLKOUT6);
  assign CLKOUT0 = tb_kv260.alt_clk;  // the alternate master clock
  assign CLKOUT1 = tb_kv260.pe_clk;   // protoemu's clock
  assign LOCKED = !RST;
  assign CLKFBOUT = 1'b0;
endmodule
