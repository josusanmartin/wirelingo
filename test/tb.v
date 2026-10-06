`default_nettype none
`timescale 1ns / 1ps

/* Top-level testbench: the Tiny Tapeout module plus a model of the board-side
   wiring for the bidirectional pins. External devices (driven by cocotb) set
   ext_oe/ext_val; pull_up[i] adds a pull-up on uio[i]. Contention resolves as
   wired-AND, which is what open-drain buses need. */
module tb ();

  reg [4095:0] dump_file;
  initial begin
    if (!$value$plusargs("DUMP_FILE=%s", dump_file)) dump_file = "tb.fst";
    $dumpfile(dump_file);
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

  // The external devices' outputs reach the pins TB_EXT_DELAY ns after cocotb
  // sets them (timing-annotated gate level only, test/Makefile: 4 ns, the
  // input delay STA constrains, IO_DELAY_CONSTRAINT = 20 % of 20 ns). Set at
  // a clock edge with no delay, they would race the propagated clock: a pad
  // flop whose clock arrives later than the pad input would take the new
  // value at that same edge, a clock earlier than RTL and STA assume. A
  // transport delay, so no pulse is swallowed.
`ifdef TB_EXT_DELAY
  reg [7:0] ext_oe_d, ext_val_d;
  always @(ext_oe) ext_oe_d <= #(`TB_EXT_DELAY) ext_oe;
  always @(ext_val) ext_val_d <= #(`TB_EXT_DELAY) ext_val;
`else
  wire [7:0] ext_oe_d = ext_oe, ext_val_d = ext_val;
`endif

  genvar i;
  generate
    for (i = 0; i < 8; i = i + 1) begin : g_bus
      assign uio_in[i] = (uio_oe[i] && ext_oe_d[i]) ? (uio_out[i] & ext_val_d[i]) :
                         uio_oe[i] ? uio_out[i] :
                         ext_oe_d[i] ? ext_val_d[i] : pull_up[i];
    end
  endgenerate

`ifdef USE_POWER_PINS  // the IHP gate-level netlist (TT uses final/nl) has no power ports
  wire VPWR = 1'b1;
  wire VGND = 1'b0;
`endif

  tt_um_josusanmartin_protoemu user_project (
`ifdef USE_POWER_PINS
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
