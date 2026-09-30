/*
 * Banked instruction memory with registered (SRAM-like) reads.
 * Bank b is read at the fetch address of its owner core (BANK_OWNER).
 * This is the flop build: BANK_AW address bits per bank, higher bits alias.
 * SPDX-License-Identifier: Apache-2.0
 */
`default_nettype none

module pe_imem #(
    parameter BANK_AW = 4
) (
    input  wire         clk,
    input  wire [39:0]  fetch_addr,   // {core3, core2, core1, core0} x 10 bits
    input  wire [7:0]   bank_owner,
    input  wire         we,
    input  wire [9:0]   waddr,
    input  wire [31:0]  wdata,
    output wire [127:0] bank_rdata    // {bank3, bank2, bank1, bank0}
);
  genvar b;
  generate
    for (b = 0; b < 4; b = b + 1) begin : g_bank
      wire [1:0] owner = bank_owner[2*b+1:2*b];
      wire [9:0] a = fetch_addr[10*owner +: 10];
      pe_imem_bank #(.AW(BANK_AW)) u_bank (
          .clk(clk),
          .raddr(a[BANK_AW-1:0]),
          .we(we && waddr[9:8] == b),
          .waddr(waddr[BANK_AW-1:0]),
          .wdata(wdata),
          .rdata(bank_rdata[32*b +: 32]));
    end
  endgenerate
endmodule

module pe_imem_bank #(
    parameter AW = 4
) (
    input  wire          clk,
    input  wire [AW-1:0] raddr,
    input  wire          we,
    input  wire [AW-1:0] waddr,
    input  wire [31:0]   wdata,
    output reg  [31:0]   rdata
);
  reg [31:0] mem[0:(1<<AW)-1];
  always @(posedge clk) begin
    if (we) mem[waddr] <= wdata;
    rdata <= mem[raddr];
  end
endmodule
