`default_nettype none
`timescale 1ns / 1ps

/* Top-level testbench: the Tiny Tapeout module plus a model of the board-side
   wiring for the bidirectional pins. External devices (driven by cocotb) set
   ext_oe/ext_val; pull_up[i] adds a pull-up on uio[i]. Contention resolves as
   wired-AND, which is what open-drain buses need. */
module tb ();

  initial begin
    $dumpfile("tb.fst");
    $dumpvars(0, tb);
    #1;
  end

  reg clk;
  reg rst_n;
  reg ena;
  reg [7:0] ui_in;
  wire [7:0] uio_in;
  wire [7:0] uo_out;
  wire [7:0] uio_out;
  wire [7:0] uio_oe;

  reg [7:0] ext_oe;
  reg [7:0] ext_val;
  reg [7:0] pull_up;

  genvar i;
  generate
    for (i = 0; i < 8; i = i + 1) begin : g_bus
      assign uio_in[i] = (uio_oe[i] && ext_oe[i]) ? (uio_out[i] & ext_val[i]) :
                         uio_oe[i] ? uio_out[i] :
                         ext_oe[i] ? ext_val[i] : pull_up[i];
    end
  endgenerate

`ifdef GL_TEST
  wire VPWR = 1'b1;
  wire VGND = 1'b0;
`endif

  tt_um_josusanmartin_protoemu user_project (
`ifdef GL_TEST
      .VPWR(VPWR),
      .VGND(VGND),
`endif
      .ui_in  (ui_in),
      .uo_out (uo_out),
      .uio_in (uio_in),
      .uio_out(uio_out),
      .uio_oe (uio_oe),
      .ena    (ena),
      .clk    (clk),
      .rst_n  (rst_n)
  );

endmodule
