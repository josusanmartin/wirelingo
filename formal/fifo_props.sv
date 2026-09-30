// Formal: pe_fifo matches a reference queue for all input sequences.
`default_nettype none
module fifo_props (
    input wire        clk,
    input wire        clear,
    input wire        push,
    input wire [7:0]  push_data,
    input wire        pop
);
  reg rst_n = 1'b0;
  always @(posedge clk) rst_n <= 1'b1;

  wire [7:0] head;
  wire [2:0] level;
  pe_fifo #(.W(8)) dut (
      .clk(clk), .rst_n(rst_n), .clear(clear), .push(push), .push_data(push_data),
      .pop(pop), .head(head), .level(level), .f_mem(f_mem), .f_rd(f_rd));
  wire [31:0] f_mem;
  wire [1:0]  f_rd;

  // reference model: q[0] is the head
  reg [7:0] q[0:3];
  reg [2:0] n = 3'd0;
  integer i;
  always @(posedge clk) begin
    if (!rst_n || clear) n <= 3'd0;
    else begin
      // start-of-cycle full/empty (docs/isa.md §5.3)
      if (pop && n != 0) begin
        for (i = 0; i < 3; i = i + 1) q[i] <= q[i+1];
      end
      if (push && n != 4) q[(pop && n != 0) ? n - 1 : n] <= push_data;
      n <= n + (push && n != 4) - (pop && n != 0);
    end
  end

  // reference contents agree with the RTL storage at every position in use
  genvar k;
  generate
    for (k = 0; k < 4; k = k + 1) begin : g_agree
      wire [1:0] pos = f_rd + k[1:0];  // wraps mod 4
      always @(*) if (rst_n && k < n) assert (f_mem[8*pos +: 8] == q[k]);
    end
  endgenerate

  always @(*) begin
    if (rst_n) begin
      assert (level == n);
      assert (level <= 3'd4);
      if (n != 0) assert (head == q[0]);
    end
  end

  // reachability sanity
  always @(*) begin
    cover (rst_n && level == 3'd4);
    cover (rst_n && level == 3'd4 && push && pop);
  end
endmodule
