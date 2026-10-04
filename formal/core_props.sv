// Formal harness: pe_core with every input unconstrained (properties live in pe_core).
`default_nettype none
module core_props (
    input wire        clk,
    input wire        enable, restart, fifo_clear,
    input wire [31:0] instr,
    input wire [31:0] pins_in, pin_out_cur,
    input wire [7:0]  flags,
    input wire        bus_sel, bus_we, bus_commit, bus_snap, bus_cancel,
    input wire        line_room, line_ok, line_t,
    input wire [5:0]  line_k,
    input wire [31:0] line_val,
    input wire [4:0]  bus_off,
    input wire [31:0] bus_wdata
);
  reg rst_n = 1'b0;
  always @(posedge clk) rst_n <= 1'b1;
  wire [9:0]  fetch_addr;
  wire [31:0] wr_out_en, wr_out_val, wr_oe_en, wr_oe_val, bus_rdata;
  wire [7:0]  flag_set, flag_clr;
  pe_core #(.IDX(2'd1)) dut (
      .clk(clk), .rst_n(rst_n), .enable(enable), .restart(restart), .fifo_clear(fifo_clear),
      .fetch_addr(fetch_addr), .instr(instr),
      .pins_in(pins_in), .pin_out_cur(pin_out_cur),
      .wr_out_en(wr_out_en), .wr_out_val(wr_out_val), .wr_oe_en(wr_oe_en), .wr_oe_val(wr_oe_val),
      .flags(flags), .flag_set(flag_set), .flag_clr(flag_clr),
      .bus_sel(bus_sel), .bus_off(bus_off), .bus_we(bus_we), .bus_wdata(bus_wdata),
      .bus_commit(bus_commit), .bus_snap(bus_snap), .bus_cancel(bus_cancel), .bus_rdata(bus_rdata),
      .line_room(line_room), .line_ok(line_ok), .line_k(line_k), .line_t(line_t), .line_val(line_val),
      .line_push(), .line_push_data(), .line_push_n(), .line_push_msb(), .line_in_n(), .line_in_shr(),
      .line_take());
endmodule
