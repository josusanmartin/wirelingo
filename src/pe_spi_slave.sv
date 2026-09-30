/*
 * Host SPI slave (mode 0, MSB first), oversampled by clk. docs/isa.md §6.1.
 *   cmd[7:0] addr[15:0] { 32-bit words }
 *   0x02 WRITE (inc)  0x0A WRITE_FIXED  0x03 READ (inc)  0x0B READ_FIXED
 * SPDX-License-Identifier: Apache-2.0
 */
`default_nettype none

module pe_spi_slave (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        sck,
    input  wire        cs_n,
    input  wire        mosi,
    output wire        miso,

    output reg  [15:0] bus_addr,
    output reg         bus_we,
    output reg  [31:0] bus_wdata,
    output reg         bus_commit,
    input  wire [31:0] bus_rdata
);
  // synchronisers
  reg [2:0] sck_s, cs_s, mosi_s;
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      sck_s <= 3'b000;
      cs_s <= 3'b111;
      mosi_s <= 3'b000;
    end else begin
      sck_s <= {sck_s[1:0], sck};
      cs_s <= {cs_s[1:0], cs_n};
      mosi_s <= {mosi_s[1:0], mosi};
    end
  end
  wire sck_rise = sck_s[1] && !sck_s[2];
  wire active   = !cs_s[1];
  wire din      = mosi_s[1];

  localparam PH_CMD = 2'd0, PH_ADDR = 2'd1, PH_DATA = 2'd2, PH_IGNORE = 2'd3;

  reg [1:0]  phase;
  reg [4:0]  bitcnt;
  reg [31:0] sh_in, sh_out;
  reg [7:0]  cmd;
  reg        load;   // load sh_out from bus_rdata this cycle

  wire is_read  = cmd == 8'h03 || cmd == 8'h0B;
  wire is_write = cmd == 8'h02 || cmd == 8'h0A;
  wire inc      = !cmd[3];

  assign miso = active && sh_out[31];

  wire [31:0] sh_in_n = {sh_in[30:0], din};

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      phase <= PH_CMD;
      bitcnt <= 5'd0;
      sh_in <= 32'd0;
      sh_out <= 32'd0;
      cmd <= 8'd0;
      load <= 1'b0;
      bus_addr <= 16'd0;
      bus_we <= 1'b0;
      bus_wdata <= 32'd0;
      bus_commit <= 1'b0;
    end else begin
      bus_we <= 1'b0;
      bus_commit <= 1'b0;
      load <= 1'b0;
      if (load) sh_out <= bus_rdata;
      if (bus_we || bus_commit) begin
        if (inc) bus_addr <= bus_addr + 16'd1;
        if (bus_commit) load <= 1'b1;  // next word, after the address update
      end

      if (!active) begin
        phase <= PH_CMD;
        bitcnt <= 5'd0;
        sh_out <= 32'd0;
      end else if (sck_rise) begin
        sh_in <= sh_in_n;
        sh_out <= {sh_out[30:0], 1'b0};
        bitcnt <= bitcnt + 5'd1;
        case (phase)
          PH_CMD:
            if (bitcnt == 5'd7) begin
              cmd <= sh_in_n[7:0];
              bitcnt <= 5'd0;
              phase <= (sh_in_n[7:0] == 8'h02 || sh_in_n[7:0] == 8'h0A ||
                        sh_in_n[7:0] == 8'h03 || sh_in_n[7:0] == 8'h0B) ? PH_ADDR : PH_IGNORE;
            end
          PH_ADDR:
            if (bitcnt == 5'd15) begin
              bus_addr <= sh_in_n[15:0];
              bitcnt <= 5'd0;
              phase <= PH_DATA;
              if (is_read) load <= 1'b1;
            end
          PH_DATA:
            if (bitcnt == 5'd31) begin
              if (is_write) begin
                bus_we <= 1'b1;
                bus_wdata <= sh_in_n;
              end else begin
                bus_commit <= 1'b1;
              end
            end
          default: ;
        endcase
      end
    end
  end
endmodule
