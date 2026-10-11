`timescale 1ns/1ps
`default_nettype none

// Native pinned pe_core, not an SV replacement for the Lean transition.
// One asynchronous reset + two reset rising edges, then one low-clock deposit
// of the declared raw/config state. Only DUT input ports change thereafter.
module rtl_gating_tb #(parameter [1:0] IDX = 0);
  reg clk = 0, rst_n = 1;
  reg enable = 0, restart = 0;
  reg [31:0] instr = 0, pins_in = 0;
  reg bus_sel = 0, bus_we = 0;
  reg [31:0] bus_wdata = 0;
  wire [9:0] fetch_addr;

  pe_core #(.IDX(IDX), .FIFO_LOG2(3)) dut (
    .clk(clk), .rst_n(rst_n), .enable(enable), .restart(restart), .fifo_clear(1'b0),
    .fetch_addr(fetch_addr), .instr(instr), .pins_in(pins_in), .pin_out_cur(32'd0),
    .wr_out_en(), .wr_out_val(), .wr_oe_en(), .wr_oe_val(),
    .flags(8'd0), .flag_set(), .flag_clr(),
    .bus_sel(bus_sel), .bus_off(5'h08), .bus_we(bus_we), .bus_wdata(bus_wdata),
    .bus_commit(1'b0), .bus_snap(1'b0), .bus_cancel(1'b0), .bus_rdata(),
    .line_room(1'b0), .line_ok(1'b0), .line_k(6'd0), .line_t(1'b0), .line_val(32'd0),
    .line_push(), .line_push_data(), .line_push_n(), .line_push_msb(),
    .line_in_n(), .line_in_shr(), .line_take()
  );

  string input_path;
  integer fd, n, clocks, bad_bank, j;
  integer operand, base, di, df, delay_seed, iw, prev, ev;
  integer ar0, ar1, ar2, ar3, tt, count_seed, frac_seed, delay_count, hh, ff;
  integer en_input, restart_input, pc_input;
  reg [31:0] pins_input;
  reg pre_exec, pre_complete, pre_tick, pre_active, pre_attempt, pre_bank;
  reg [8:0] pre_fsum;
  reg [15:0] pre_cnt_next;
  reg [9:0] own_pc;

  initial begin
    if (!$value$plusargs("INPUT=%s", input_path)) $fatal(1, "missing INPUT");
    fd = $fopen(input_path, "r");
    if (!fd) $fatal(1, "cannot read INPUT");
    n = $fscanf(fd, "%d %d\n", clocks, bad_bank);
    if (n != 2 || clocks < 1 || clocks > 32) $fatal(1, "invalid trace header");
    n = $fscanf(fd, "%d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d\n",
      operand, base, di, df, delay_seed, iw, prev, ev, ar0, ar1, ar2, ar3,
      tt, count_seed, frac_seed, delay_count, hh, ff);
    if (n != 18) $fatal(1, "invalid initial state");
    own_pc = {IDX, 8'd0};
    // Explicit reset event; inputs remain quiescent through both reset clocks.
    #1 rst_n = 0;
    repeat (2) begin #4 clk = 1; #5 clk = 0; end
    $display("RESET 2");
    rst_n = 1;
    #1;
    // Simulation deposits, once only; no force/release and no later state writes.
    dut.div_int = di[15:0]; dut.div_frac = df[7:0]; dut.in_base = base[4:0];
    dut.entry = own_pc; dut.wrap_bottom = own_pc; dut.wrap_top = own_pc;
    dut.pc = bad_bank ? (own_pc ^ 10'd256) : own_pc;
    dut.in_wait = iw[0]; dut.wprev = prev[0]; dut.wevent = ev[0];
    dut.r0 = ar0[15:0]; dut.r1 = ar1[15:0]; dut.r2 = ar2[15:0]; dut.r3 = ar3[15:0];
    dut.t = tt[0]; dut.cnt = count_seed[15:0]; dut.facc = frac_seed[7:0];
    dut.dcnt = delay_count[7:0]; dut.halted = hh[0]; dut.fault = ff[0];
    instr = (32'd1 << 27) | (delay_seed << 19) | operand;
    $display("DEPOSIT 1 INSTR %08x IDX %0d FIFO_LOG2 3", instr, IDX);
    for (j = 0; j < clocks; j = j + 1) begin
      n = $fscanf(fd, "%d %d %d %d\n", pins_input, en_input, restart_input, pc_input);
      if (n != 4 || (restart_input && pc_input)) $fatal(1, "invalid external drive");
      // Drive only with clock low. Four ns settle, then capture combinational
      // exec and qualified completion BEFORE the rising edge.
      pins_in = pins_input; enable = en_input[0]; restart = restart_input[0];
      bus_sel = pc_input[0]; bus_we = pc_input[0]; bus_wdata = {22'd0, own_pc};
      #4;
      pre_exec = dut.exec; pre_complete = dut.exec && dut.complete;
      pre_tick = dut.tick; pre_active = dut.active; pre_attempt = dut.attempt;
      pre_bank = dut.bank_ok; pre_fsum = dut.fsum; pre_cnt_next = dut.cnt_next;
      clk = 1;
      // Sample one ns AFTER the edge, after all NBA updates have settled.
      #1;
      $display("ROW %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d",
        j, dut.in_wait, dut.wprev, dut.wevent, dut.r0, dut.r1, dut.r2, dut.r3,
        dut.t, dut.cnt, dut.facc, dut.dcnt, dut.halted, dut.fault,
        pre_exec, pre_complete, pre_tick, pre_active, pre_attempt, pre_bank,
        pre_fsum, pre_cnt_next, dut.pc);
      if (bad_bank) $display("ASSUMPTION wrong-bank attempt=%0d bank_ok=%0d exec=%0d halted=%0d fault=%0d",
        pre_attempt, pre_bank, pre_exec, dut.halted, dut.fault);
      #4 clk = 0;
    end
    $fclose(fd);
    $finish;
  end
endmodule
