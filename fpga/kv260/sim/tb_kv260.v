`default_nettype none
`timescale 1ns / 1ps
// Simulation harness for kv260_top: the PS block design and the Xilinx
// primitives are replaced by the models below; cocotb drives the GPIO words.
module tb_kv260 ();
  reg         pl_clk, pe_clk;
  reg  [31:0] gpio0_o;
  wire [31:0] gpio0_i, gpio1_i;
  kv260_top dut ();
  assign gpio0_i = dut.gpio0_i;
  assign gpio1_i = dut.gpio1_i;
endmodule

module pe_ps_wrapper (
    output wire pl_clk0, output wire pl_resetn0, output wire [31:0] gpio0_o_tri_o,
    input wire [31:0] gpio0_i_tri_i, input wire [31:0] gpio1_i_tri_i);
  assign pl_clk0 = tb_kv260.pl_clk;
  assign pl_resetn0 = 1'b1;
  assign gpio0_o_tri_o = tb_kv260.gpio0_o;
endmodule

module BUFG (input wire I, output wire O);
  assign O = I;
endmodule

module MMCME4_BASE #(
    parameter real CLKIN1_PERIOD = 10.0, parameter integer DIVCLK_DIVIDE = 1,
    parameter real CLKFBOUT_MULT_F = 12.0, parameter real CLKOUT0_DIVIDE_F = 24.0
) (
    input wire CLKIN1, CLKFBIN, RST, PWRDWN,
    output wire CLKFBOUT, CLKOUT0, LOCKED,
    output wire CLKFBOUTB, CLKOUT0B, CLKOUT1, CLKOUT1B, CLKOUT2, CLKOUT2B, CLKOUT3, CLKOUT3B,
    output wire CLKOUT4, CLKOUT5, CLKOUT6);
  assign CLKOUT0 = tb_kv260.pe_clk;   // asynchronous to pl_clk, as on the board
  assign LOCKED = !RST;
  assign CLKFBOUT = 1'b0;
endmodule
