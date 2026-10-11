`timescale 1ns/1ps
`default_nettype none

// Actual core/dependencies, FIFO depth8. Reset OSR/main pin data stays zero.
// Two reset edges, ONE low-clock raw/config deposit, then input ports only.
module rtl_sideset_tb #(parameter [1:0] IDX = 1);
  reg clk = 0, rst_n = 1;
  reg enable = 0, restart = 0;
  reg [31:0] instr = 0;
  reg [7:0] flags = 0;
  wire [31:0] wr_out_en, wr_out_val, wr_oe_en, wr_oe_val;
  pe_core #(.IDX(IDX), .FIFO_LOG2(3)) dut (
    .clk(clk), .rst_n(rst_n), .enable(enable), .restart(restart), .fifo_clear(1'b0),
    .fetch_addr(), .instr(instr), .pins_in(32'd0), .pin_out_cur(32'd0),
    .wr_out_en(wr_out_en), .wr_out_val(wr_out_val),
    .wr_oe_en(wr_oe_en), .wr_oe_val(wr_oe_val),
    .flags(flags), .flag_set(), .flag_clr(),
    .bus_sel(1'b0), .bus_off(5'd0), .bus_we(1'b0), .bus_wdata(32'd0),
    .bus_commit(1'b0), .bus_snap(1'b0), .bus_cancel(1'b0), .bus_rdata(),
    .line_room(1'b0), .line_ok(1'b0), .line_k(6'd0), .line_t(1'b0), .line_val(32'd0),
    .line_push(), .line_push_data(), .line_push_n(), .line_push_msb(),
    .line_in_n(), .line_in_shr(), .line_take()
  );

  string input_path;
  integer fd, n, clocks, j;
  integer sc, base, opt, pindir, sd, di, df, cc, ffacc, dc, hh, fault_seed, stalled_seed, pc_seed;
  integer en_input, restart_input, f0_input;
  reg [31:0] pre_out_en, pre_out_val, pre_oe_en, pre_oe_val;
  reg pre_exec, pre_complete;

  initial begin
    if (!$value$plusargs("INPUT=%s", input_path)) $fatal(1, "missing INPUT");
    fd = $fopen(input_path, "r");
    if (!fd) $fatal(1, "cannot read INPUT");
    n = $fscanf(fd, "%d\n", clocks);
    if (n != 1 || clocks < 1 || clocks > 24) $fatal(1, "invalid clock count");
    n = $fscanf(fd, "%d %d %d %d %d %d %d %d %d %d %d %d %d %d\n",
      sc, base, opt, pindir, sd, di, df, cc, ffacc, dc, hh, fault_seed, stalled_seed, pc_seed);
    if (n != 14) $fatal(1, "invalid initial state");
    #1 rst_n = 0;
    repeat (2) begin #4 clk = 1; #5 clk = 0; end
    $display("RESET 2");
    rst_n = 1;
    #1;
    // Only deposit site. Includes unreachable halt/fault and counter seeds.
    dut.side_count = sc[2:0]; dut.side_base = base[4:0];
    dut.side_opt = opt[0]; dut.side_pindir = pindir[0];
    dut.div_int = di[15:0]; dut.div_frac = df[7:0];
    dut.entry = {IDX, 8'd0}; dut.wrap_bottom = {IDX, 8'd0}; dut.wrap_top = {IDX, 8'd0};
    dut.pc = pc_seed[9:0]; dut.cnt = cc[15:0]; dut.facc = ffacc[7:0];
    dut.dcnt = dc[7:0]; dut.halted = hh[0]; dut.fault = fault_seed[0];
    dut.stalled = stalled_seed[0];
    // Kind5 FLAG1, flag0, no qualifier/timeout/auto-clear. Config is fixed.
    instr = (32'd1 << 27) | (sd << 19) | (32'd5 << 16);
    $display("DEPOSIT 1 INSTR %08x IDX %0d FIFO_LOG2 3", instr, IDX);
    for (j = 0; j < clocks; j = j + 1) begin
      n = $fscanf(fd, "%d %d %d\n", en_input, restart_input, f0_input);
      if (n != 3) $fatal(1, "invalid external drive");
      enable = en_input[0]; restart = restart_input[0]; flags = {7'd0, f0_input[0]};
      // Low-clock drive; four ns settle. Capture ALL write buses, exec and
      // qualified completion before the rising edge, never after NBA.
      #4;
      pre_out_en = wr_out_en; pre_out_val = wr_out_val;
      pre_oe_en = wr_oe_en; pre_oe_val = wr_oe_val;
      pre_exec = dut.exec; pre_complete = dut.exec && dut.complete;
      clk = 1;
      #1; // Post-edge observation after NBA, no feedback or accumulator.
      $display("ROW %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d",
        j, pre_out_en, pre_out_val, pre_oe_en, pre_oe_val, pre_exec, pre_complete,
        dut.pc, dut.stalled, dut.cnt, dut.facc, dut.dcnt, dut.halted, dut.fault);
      if (pc_seed[9:8] != IDX)
        $display("ASSUMPTION wrong-bank exec=%0d fault=%0d halted=%0d", pre_exec, dut.fault, dut.halted);
      #4 clk = 0;
    end
    $fclose(fd);
    $finish;
  end
endmodule
