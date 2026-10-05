// Small native STA reporting control, independent of the protoemu netlist.
module pulse_control(input clk, rst_n, d, output q, qn);
  sg13cmos5l_dfrbp_1 u_ff (
      .CLK(clk), .RESET_B(rst_n), .D(d), .Q(q), .Q_N(qn));
endmodule
