// Interfaces of the library cells and the SRAM macro for reading src/pe_cg.sv
// and src/pe_imem.sv under SCL_sg13cmos5l_stdcell. Both sides of the port
// equivalence instantiate them from the same Verilog; they are cut points, so
// only their pin lists matter here.
(* blackbox *)
module sg13cmos5l_lgcp_1 (input CLK, input GATE, output GCLK);
endmodule

(* blackbox *)
module sg13cmos5l_dlhq_1 (input D, input GATE, output Q);
endmodule

(* blackbox *)
module RM_IHPSG13_1P_256x32_c2_bm_bist (
    input         A_CLK,
    input         A_MEN,
    input         A_WEN,
    input         A_REN,
    input  [7:0]  A_ADDR,
    input  [31:0] A_DIN,
    input         A_DLY,
    output [31:0] A_DOUT,
    input  [31:0] A_BM,
    input         A_BIST_CLK,
    input         A_BIST_EN,
    input         A_BIST_MEN,
    input         A_BIST_WEN,
    input         A_BIST_REN,
    input  [7:0]  A_BIST_ADDR,
    input  [31:0] A_BIST_DIN,
    input  [31:0] A_BIST_BM
);
endmodule
