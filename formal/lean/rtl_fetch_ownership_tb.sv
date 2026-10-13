`timescale 1ns/1ps
`default_nettype none

// Unchanged actual chip, actual SRAM macro interface/behavioral SRAM model.
// Bus input ports only: no deposits, force/release, instruction injection or
// output feedback. All hierarchical references below are observations.
module rtl_fetch_ownership_tb;
  reg clk = 0, rst_n = 1;
  reg [15:0] bus_addr = 16'hffff;
  reg bus_we = 0;
  reg [31:0] bus_wdata = 0;
  wire [6:0] uo_out;
  wire [7:0] uio_out, uio_oe;
  pe_chip #(.NCORES(2), .NBANKS(2), .BANK_AW(8), .FIFO_LOG2(3)) dut (
    .clk(clk), .rst_n(rst_n), .ui_in(5'd0), .uio_in(8'd0),
    .uo_out(uo_out), .uio_out(uio_out), .uio_oe(uio_oe),
    .bus_addr(bus_addr), .bus_we(bus_we), .bus_wdata(bus_wdata),
    .bus_commit(1'b0), .bus_snap(1'b0), .bus_cancel(1'b0),
    .bus_rdata(), .quad_en()
  );

  string input_path;
  integer fd, n, clocks, j, rn, we, addr, phase;
  reg [31:0] data;
  reg [31:0] pre_owner_lo, pre_owner_hi, pre_instr0, pre_instr1;
  reg [3:0] pre_enable, pre_restart;
  reg [7:0] pre_flags;
  reg [9:0] pre_pc0, pre_pc1, pre_fetch0, pre_fetch1;
  reg [15:0] pre_cnt0, pre_cnt1;
  reg pre_exec0, pre_exec1, pre_done0, pre_done1;
  reg [127:0] pre_en, pre_val, pre_oe_en, pre_oe_val;
  reg [31:0] pre_line;

  initial begin
    if (!$value$plusargs("INPUT=%s", input_path)) $fatal(1, "missing INPUT");
    fd = $fopen(input_path, "r");
    if (!fd) $fatal(1, "cannot open INPUT");
    n = $fscanf(fd, "%d\n", clocks);
    if (n != 1 || clocks < 2 || clocks > 150) $fatal(1, "invalid clock count");
    #1;
    for (j = 0; j < clocks; j = j + 1) begin
      n = $fscanf(fd, "%d %d %d %d %d\n", rn, we, addr, data, phase);
      if (n != 5) $fatal(1, "invalid bus drive");
      rst_n = rn[0]; bus_we = we[0]; bus_addr = addr[15:0]; bus_wdata = data;
      #4; // Clock LOW, bus/CTRL/fetch/combinational writes settle.
      if (bus_we && bus_addr < 16'h0400 && dut.enable != 0)
        $fatal(1, "program write with core enabled");
      pre_owner_lo = dut.pin_owner_lo; pre_owner_hi = dut.pin_owner_hi;
      pre_enable = dut.enable; pre_restart = dut.restart; pre_flags = dut.flags;
      pre_pc0 = dut.g_core[0].g_on.u_core.pc; pre_pc1 = dut.g_core[1].g_on.u_core.pc;
      pre_instr0 = dut.bank_rdata[31:0]; pre_instr1 = dut.bank_rdata[63:32];
      pre_fetch0 = dut.fetch_addr[9:0]; pre_fetch1 = dut.fetch_addr[19:10];
      pre_cnt0 = dut.g_core[0].g_on.u_core.cnt; pre_cnt1 = dut.g_core[1].g_on.u_core.cnt;
      pre_exec0 = dut.g_core[0].g_on.u_core.exec; pre_exec1 = dut.g_core[1].g_on.u_core.exec;
      pre_done0 = pre_exec0 && dut.g_core[0].g_on.u_core.complete;
      pre_done1 = pre_exec1 && dut.g_core[1].g_on.u_core.complete;
      pre_en = dut.w_out_en; pre_val = dut.w_out_val;
      pre_oe_en = dut.w_oe_en; pre_oe_val = dut.w_oe_val; pre_line = dut.line_mask;
      clk = 1;
      #1; // After NBA: chip registers and SRAM DOUT, then combinational port.
      $display("ROW %0d %0d %0d %0d %0d %0d %0d %0d %08x %08x %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %08x %08x %0d %0d %0d %0d",
        j, pre_owner_lo, pre_owner_hi, pre_enable, pre_restart, pre_flags,
        pre_pc0, pre_pc1, pre_instr0, pre_instr1, pre_fetch0, pre_fetch1,
        pre_cnt0, pre_cnt1, pre_exec0, pre_exec1, pre_done0, pre_done1,
        pre_en[31:0], pre_val[31:0], pre_en[63:32], pre_val[63:32],
        pre_oe_en[31:0], pre_oe_en[63:32],
        (pre_en[127:64] | pre_val[127:64] | pre_oe_en[127:64] | pre_oe_val[127:64]),
        pre_line, dut.pin_out, dut.pin_oe, uo_out,
        dut.g_core[0].g_on.u_core.pc, dut.g_core[1].g_on.u_core.pc,
        dut.bank_rdata[31:0], dut.bank_rdata[63:32],
        dut.g_core[0].g_on.u_core.stalled, dut.g_core[1].g_on.u_core.stalled,
        (dut.g_core[0].g_on.u_core.fault | dut.g_core[1].g_on.u_core.fault),
        (dut.g_core[0].g_on.u_core.halted | dut.g_core[1].g_on.u_core.halted));
      $display("DRIVE %0d reset_n=%0d phase=%0d we=%0d addr=%04x data=%08x uio_out=%02x uio_oe=%02x pre_oe_vals=%032x",
        j, rn, phase, we, bus_addr, bus_wdata, uio_out, uio_oe, pre_oe_val);
      #4 clk = 0;
    end
    $fclose(fd);
    $finish;
  end
endmodule
