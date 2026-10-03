// Formal harness: pe_line with every input free except core 0's contract
// (properties live in pe_line): OUT LINE pushes only when the unit has room,
// IN LINE takes only when it may (in_ok), and bit counts are 1..32 as the
// core's n is (docs/isa.md §4.7).
`default_nettype none
module line_props (
    input wire        clk,
    input wire        cfg_we, div_we, pins_we,
    input wire [31:0] wdata,
    input wire [31:0] pins_in,
    input wire        rx_mid,
    input wire        push,
    input wire [31:0] push_data,
    input wire [5:0]  push_n,
    input wire        push_msb,
    input wire [5:0]  in_n,
    input wire        in_shr,
    input wire        take
);
  reg rst_n = 1'b0;
  always @(posedge clk) rst_n <= 1'b1;
  wire room, in_ok, in_t;
  wire [5:0] in_k;
  always @(*) begin
    assume (!push || room);
    assume (!take || in_ok);
    assume (push_n >= 6'd1 && push_n <= 6'd32);
    assume (in_n >= 6'd1 && in_n <= 6'd32);
  end
  pe_line dut (
      .clk(clk), .rst_n(rst_n), .cfg_we(cfg_we), .div_we(div_we), .pins_we(pins_we), .wdata(wdata),
      .r_stat(), .pins_in(pins_in), .rx_mid(rx_mid), .rx_pin(), .drv_mask(), .drv_val(),
      .room(room), .push(push), .push_data(push_data), .push_n(push_n), .push_msb(push_msb),
      .in_n(in_n), .in_shr(in_shr), .in_ok(in_ok), .in_k(in_k), .in_t(in_t), .in_val(), .take(take));
endmodule
