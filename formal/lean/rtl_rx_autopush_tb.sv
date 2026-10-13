`timescale 1ns/1ps
`default_nettype none
// All DUT writes use real bus/pin ports. Hierarchy below is read-only.
module rtl_rx_autopush_tb;
  reg clk = 0, rst_n = 1;
  reg [7:0] raw_byte = 0;
  reg [15:0] bus_addr = 16'hffff;
  reg bus_we = 0, bus_snap = 0, bus_commit = 0, bus_cancel = 0;
  reg [31:0] bus_wdata = 0;
  wire [31:0] bus_rdata;
  wire [6:0] uo_out;
  wire [7:0] uio_out, uio_oe;
  pe_chip #(.NCORES(2), .NBANKS(2), .BANK_AW(8), .FIFO_LOG2(3)) dut (
    .clk(clk), .rst_n(rst_n), .ui_in(5'd0), .uio_in(raw_byte),
    .uo_out(uo_out), .uio_out(uio_out), .uio_oe(uio_oe),
    .bus_addr(bus_addr), .bus_we(bus_we), .bus_wdata(bus_wdata),
    .bus_snap(bus_snap), .bus_commit(bus_commit), .bus_cancel(bus_cancel),
    .bus_rdata(bus_rdata), .quad_en()
  );
`define C dut.g_core[0].g_on.u_core
  string path;
  integer fd, n, clocks, j, rn, we, addr, raw, snap, commit, cancel;
  reg [31:0] data;
  reg [31:0] pre [0:18];
  initial begin
    if (!$value$plusargs("INPUT=%s", path)) $fatal(1,"missing INPUT");
    fd=$fopen(path,"r"); if (!fd) $fatal(1,"input open failed");
    n=$fscanf(fd,"%d\n",clocks);
    if (n!=1 || clocks<2 || clocks>200) $fatal(1,"invalid clock count");
    #1;
    for(j=0;j<clocks;j=j+1) begin
      n=$fscanf(fd,"%d %d %d %d %d %d %d %d\n",rn,we,addr,data,raw,snap,commit,cancel);
      if(n!=8 || snap+commit+cancel>1) $fatal(1,"invalid external drive");
      rst_n=rn[0]; bus_we=we[0]; bus_addr=addr[15:0]; bus_wdata=data;
      raw_byte=raw[7:0]; bus_snap=snap[0]; bus_commit=commit[0]; bus_cancel=cancel[0];
      #4; // Low-clock settled PRE-EDGE: reservations/full/shift candidate.
      if(bus_we && bus_addr<16'h0400 && dut.enable!=0) $fatal(1,"enabled IMEM write");
      if(bus_we && bus_addr>=16'h0400 && bus_addr<16'h0480 && dut.enable!=0)
        $fatal(1,"enabled configuration/register write");
      if(dut.line_mask!=0 || uio_oe!=0 || dut.fclear!=0) $fatal(1,"excluded line/output/FIFO-clear interference");
      pre[0]=dut.enable[0]; pre[1]=dut.restart[0]; pre[2]=dut.pins_in[7:0];
      pre[3]=dut.bank_rdata[31:0]; pre[4]=`C.pc; pre[5]=`C.isr; pre[6]=`C.isr_cnt;
      pre[7]=`C.rxf_level; pre[8]=`C.res_n; pre[9]=`C.res_real; pre[10]=`C.rd_real;
      pre[11]=(bus_addr==16'h0410)?bus_rdata:32'd0;
      pre[12]=`C.exec; pre[13]=`C.exec && `C.complete;
      pre[14]=`C.exec && `C.core_rxpush; pre[15]=`C.core_rxdata;
      pre[16]=`C.exec && `C.isr_we; pre[17]=`C.host_pop;
      pre[18]=(`C.rxf_level!=0)?`C.rxf_head:32'd0;
      clk=1;
      #1; // POST-EDGE after NBA and actual latch FIFO settling.
      $write("ROW %0d",j);
      for(n=0;n<19;n=n+1)
        if(n==3) $write(" %08x",pre[n]); else $write(" %0d",pre[n]);
      $display(" %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d",
        `C.pc,`C.isr,`C.isr_cnt,`C.rxf_level,`C.res_n,`C.res_real,`C.stalled,
        `C.halted,`C.fault,dut.sync1[7:0],dut.sync2[7:0],`C.cnt,`C.dcnt,`C.facc,
        dut.g_core[1].g_on.u_core.exec && dut.g_core[1].g_on.u_core.core_rxpush);
      $display("DRIVE %0d reset_n=%0d we=%0d addr=%04x data=%08x raw=%02x snap=%0d commit=%0d cancel=%0d",
        j,rn,we,bus_addr,bus_wdata,raw_byte,snap,commit,cancel);
      #4 clk=0;
    end
    $fclose(fd); $finish;
  end
`undef C
endmodule
