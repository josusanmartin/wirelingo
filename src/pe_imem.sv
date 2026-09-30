/*
 * Banked instruction memory with registered (SRAM-like) reads.
 * Bank b is read at the fetch address of its owner core (BANK_OWNER).
 * Default: one IHP 256x32 SRAM macro per bank (BANK_AW = 8).
 * PE_FLOP_IMEM: flop banks with BANK_AW address bits; higher bits alias.
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
    parameter AW = 8
) (
    input  wire          clk,
    input  wire [AW-1:0] raddr,
    input  wire          we,
    input  wire [AW-1:0] waddr,
    input  wire [31:0]   wdata,
    output wire [31:0]   rdata
);
`ifdef PE_FLOP_IMEM
  // Flop build (simulation / small experiments): same registered-read behaviour.
  reg [31:0] mem[0:(1<<AW)-1];
  reg [31:0] rdata_q;
  always @(posedge clk) begin
    if (we) mem[waddr] <= wdata;
    rdata_q <= mem[raddr];
  end
  assign rdata = rdata_q;
`else
  // IHP 256x32 single-port SRAM. A write cycle doesn't read (DOUT holds), which is
  // fine because a bank is only written while its owner core is disabled (isa.md §3).
  (* keep *) RM_IHPSG13_1P_256x32_c2_bm_bist sram (
      .A_CLK(clk),
      .A_MEN(1'b1),
      .A_WEN(we),
      .A_REN(!we),
      .A_ADDR(we ? waddr : raddr),
      .A_DIN(wdata),
      .A_DLY(1'b1),
      .A_DOUT(rdata),
      .A_BM(32'hFFFF_FFFF),
      .A_BIST_CLK(1'b0),
      .A_BIST_EN(1'b0),
      .A_BIST_MEN(1'b0),
      .A_BIST_WEN(1'b0),
      .A_BIST_REN(1'b0),
      .A_BIST_ADDR(8'd0),
      .A_BIST_DIN(32'd0),
      .A_BIST_BM(32'd0));
`endif
endmodule
