`default_nettype none
// Behavioral model of RM_IHPSG13_1P_256x32_c2_bm_bist for RTL simulation without the PDK.
// Follows the datasheet operation table (BIST port unused): MEN=1, WEN=1 writes (bit mask BM);
// REN=1 reads (write-through if WEN=1). A_DOUT holds its value otherwise.
module RM_IHPSG13_1P_256x32_c2_bm_bist (
    input  wire        A_CLK,
    input  wire        A_MEN,
    input  wire        A_WEN,
    input  wire        A_REN,
    input  wire [7:0]  A_ADDR,
    input  wire [31:0] A_DIN,
    input  wire        A_DLY,
    output reg  [31:0] A_DOUT,
    input  wire [31:0] A_BM,
    input  wire        A_BIST_CLK,
    input  wire        A_BIST_EN,
    input  wire        A_BIST_MEN,
    input  wire        A_BIST_WEN,
    input  wire        A_BIST_REN,
    input  wire [7:0]  A_BIST_ADDR,
    input  wire [31:0] A_BIST_DIN,
    input  wire [31:0] A_BIST_BM
);
  reg [31:0] mem[0:255];
  always @(posedge A_CLK) begin
    if (A_MEN && !A_BIST_EN) begin
      if (A_WEN) mem[A_ADDR] <= (mem[A_ADDR] & ~A_BM) | (A_DIN & A_BM);
      if (A_REN) A_DOUT <= A_WEN ? ((mem[A_ADDR] & ~A_BM) | (A_DIN & A_BM)) : mem[A_ADDR];
    end
  end
  initial if (A_DLY !== 1'b1) #1 if (A_DLY !== 1'b1) $display("%m: A_DLY must be tied to 1");
  wire _unused = &{A_BIST_CLK, A_BIST_MEN, A_BIST_WEN, A_BIST_REN, A_BIST_ADDR, A_BIST_DIN, A_BIST_BM, 1'b0};
endmodule
