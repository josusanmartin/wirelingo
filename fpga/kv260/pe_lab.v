/*
 * Build identity and v2 feature-demo fabric for the KV260 design, with an
 * AXI4-Lite slave (0xA003_0000, 64 KB window; byte offsets below).
 *
 * Identity (read-only constants from build_id.vh, written by build.sh):
 *   0x0000 MAGIC      0x4C414231 ("LAB1")
 *   0x0004 FLAGS      [0] dirty (RTL inputs differed from RTL_REV when built),
 *                     [15:8] CLK_DIV
 *   0x0008 BUILD_TIME Unix time of the build (UTC)
 *   0x000C DIGEST     first 32 bits of the SHA-256 of the staged RTL and the
 *                     fabric sources (build.sh)
 *   0x0010-0x0020 RTL_REV   last commit that touched the RTL inputs (src/,
 *                     flow/fpga_profile.py, fpga/kv260 HDL/XDC/Tcl), 160 bits,
 *                     word 0 = the first 8 hex digits
 *   0x0030-0x0040 HEAD_REV  the checkout's HEAD when built
 *
 * Demo control:
 *   0x0100 MODE    [1:0] what drives the uio pads protoemu doesn't drive
 *                  (kv260_top.v): 0 the GPIO path (reset), 1 the pattern
 *                  generator (all 8), 2 the SPI device (MISO on uio4; SCK =
 *                  uio2, MOSI = uio3 from protoemu), 3 an I2C bus between
 *                  uio0/uio2 (SDA) and uio1/uio3 (SCL), wired-AND with pull-ups
 *
 * Pattern generator (gclk = protoemu's clock, or with PG_CTRL.ALT the
 * 45.93 MHz alternate MMCM clock; drives pg_out, MODE 1):
 *   0x0110 PG_LEN   [10:0] entries to play (0..1024)
 *   0x0114 PG_CTRL  [0] ALT (generator clock; change only while idle)
 *   0x0118 PG_GO    W: start.  R: [0] busy
 *   0x011C PG_INIT  [7:0] value driven at the start (and kept when LEN = 0)
 *   0x0120 PG_D0    [23:0] gclk cycles from the start to entry 0 (>= 1)
 *   0x0124 PG_CYC   R: gclk cycles from the start to the last entry
 *   0x4000-0x4FFC   entry i (write-only): [7:0] value, [31:8] gclk cycles
 *                   from entry i to entry i+1 (>= 1; ignored for the last)
 * So entry i is driven sum(D0, d0 .. d(i-1)) gclk cycles after the start,
 * exactly: every change is one register in the gclk domain.
 *
 * SPI device (dclk = 400 MHz, 8 per protoemu clock, phase-aligned with it):
 * mode 0, MSB first, no chip select: SPID_ARM puts bit 7 of byte 0 on MISO;
 * SCK and MOSI are registered at every dclk edge (s1, m1; the first dclk edge
 * after the protoemu clock edge that changed them, 2.5 ns later), and after
 * each SCK fall the next bit is on MISO DELAY+2 dclk cycles after the protoemu
 * clock edge that dropped SCK (2.5 ns steps). MOSI is taken when the rise is
 * seen, two clocks after it last changed.
 *   0x0200 SPID_DELAY [4:0] DELAY (0..31)
 *   0x0204 SPID_ARM   W: arm.  R: [15:0] SCK rises since the arm
 *   0x0208 SPID_PER   R: [7:0] shortest, [15:8] longest SCK rise-to-rise
 *                     interval since the arm, in dclk cycles
 *   0x0210-0x021C     RESP: the 16 bytes to send, word 0 [31:24] = byte 0
 *   0x0220-0x022C     R: the last 16 bytes received on MOSI, word 3 [7:0] =
 *                     the most recent
 * Software changes registers only while the generator or device is idle;
 * they cross clock domains as quasi-static values.
 * SPDX-License-Identifier: Apache-2.0
 */
`default_nettype none
`include "build_id.vh"

module pe_lab #(
    parameter integer CLK_DIV = 24
) (
    input  wire        aclk,
    input  wire        aresetn,
    input  wire [15:0] s_awaddr,
    input  wire        s_awvalid,
    output wire        s_awready,
    input  wire [31:0] s_wdata,
    input  wire        s_wvalid,
    output wire        s_wready,
    output reg         s_bvalid,
    input  wire        s_bready,
    input  wire [15:0] s_araddr,
    input  wire        s_arvalid,
    output wire        s_arready,
    output reg  [31:0] s_rdata,
    output reg         s_rvalid,
    input  wire        s_rready,
    // configuration (aclk domain, quasi-static)
    output reg  [1:0]  mode,
    output reg         pg_alt,
    // pattern generator (gclk domain)
    input  wire        gclk,
    output reg  [7:0]  pg_out,
    // SPI device (dclk domain; sck and mosi from protoemu's clock domain)
    input  wire        dclk,
    input  wire        sck,
    input  wire        mosi,
    output reg         miso
);
  localparam [159:0] RTL_REV = `BUILD_RTL_REV;
  localparam [159:0] HEAD_REV = `BUILD_HEAD_REV;

  // ---------------------------------------------------------------- AXI-Lite
  reg  [10:0]  r_pg_len;
  reg  [7:0]   r_pg_init;
  reg  [23:0]  r_pg_d0;
  reg  [4:0]   r_dly;
  reg  [127:0] r_resp;
  reg          pg_go_tog, arm_tog;
  reg          rpend;
  reg  [15:0]  rd_addr;
  wire         wr_go = s_awvalid && s_wvalid && !s_bvalid;
  wire         rd_go = s_arvalid && !s_rvalid && !rpend && !wr_go;
  assign s_awready = wr_go;
  assign s_wready  = wr_go;
  assign s_arready = rd_go;

  (* ram_style = "distributed" *) reg [31:0] pgmem[0:1023];
  always @(posedge aclk)
    if (wr_go && s_awaddr[15:12] == 4'h4) pgmem[s_awaddr[11:2]] <= s_wdata;

  reg         pg_done_tog;
  reg  [31:0] pg_cyc;
  reg  [15:0] d_rises;
  reg  [7:0]  d_pmin, d_pmax;
  reg  [127:0] d_rx;
  (* ASYNC_REG = "TRUE" *) reg [1:0] pg_done_s;
  always @(posedge aclk) pg_done_s <= {pg_done_s[0], pg_done_tog};
  wire pg_busy = pg_go_tog ^ pg_done_s[1];

  reg [31:0] reg_rd;
  always @(*) begin
    case (rd_addr[11:2])
      10'h000: reg_rd = 32'h4C414231;
      10'h001: reg_rd = {16'd0, CLK_DIV[7:0], 7'd0, `BUILD_DIRTY};
      10'h002: reg_rd = `BUILD_TIME;
      10'h003: reg_rd = `BUILD_DIGEST;
      10'h004: reg_rd = RTL_REV[159:128];
      10'h005: reg_rd = RTL_REV[127:96];
      10'h006: reg_rd = RTL_REV[95:64];
      10'h007: reg_rd = RTL_REV[63:32];
      10'h008: reg_rd = RTL_REV[31:0];
      10'h00C: reg_rd = HEAD_REV[159:128];
      10'h00D: reg_rd = HEAD_REV[127:96];
      10'h00E: reg_rd = HEAD_REV[95:64];
      10'h00F: reg_rd = HEAD_REV[63:32];
      10'h010: reg_rd = HEAD_REV[31:0];
      10'h040: reg_rd = {30'd0, mode};
      10'h044: reg_rd = {21'd0, r_pg_len};
      10'h045: reg_rd = {31'd0, pg_alt};
      10'h046: reg_rd = {31'd0, pg_busy};
      10'h047: reg_rd = {24'd0, r_pg_init};
      10'h048: reg_rd = {8'd0, r_pg_d0};
      10'h049: reg_rd = pg_cyc;
      10'h080: reg_rd = {27'd0, r_dly};
      10'h081: reg_rd = {16'd0, d_rises};
      10'h082: reg_rd = {16'd0, d_pmax, d_pmin};
      10'h084: reg_rd = r_resp[127:96];
      10'h085: reg_rd = r_resp[95:64];
      10'h086: reg_rd = r_resp[63:32];
      10'h087: reg_rd = r_resp[31:0];
      10'h088: reg_rd = d_rx[127:96];
      10'h089: reg_rd = d_rx[95:64];
      10'h08A: reg_rd = d_rx[63:32];
      10'h08B: reg_rd = d_rx[31:0];
      default: reg_rd = 32'd0;
    endcase
  end

  always @(posedge aclk) begin
    if (!aresetn) begin
      s_bvalid <= 1'b0;
      s_rvalid <= 1'b0;
      rpend <= 1'b0;
      mode <= 2'd0;
      pg_alt <= 1'b0;
      r_pg_len <= 11'd0;
      r_pg_init <= 8'hFF;
      r_pg_d0 <= 24'd1;
      r_dly <= 5'd0;
      r_resp <= 128'd0;
      pg_go_tog <= pg_done_s[1];
    end else begin
      if (s_bvalid && s_bready) s_bvalid <= 1'b0;
      if (wr_go) begin
        s_bvalid <= 1'b1;
        if (s_awaddr[15:12] == 4'h0)
          case (s_awaddr[11:2])
            10'h040: mode <= s_wdata[1:0];
            10'h044: r_pg_len <= s_wdata[10:0];
            10'h045: pg_alt <= s_wdata[0];
            10'h046: if (!pg_busy) pg_go_tog <= ~pg_go_tog;
            10'h047: r_pg_init <= s_wdata[7:0];
            10'h048: r_pg_d0 <= s_wdata[23:0];
            10'h080: r_dly <= s_wdata[4:0];
            10'h081: arm_tog <= ~arm_tog;
            10'h084: r_resp[127:96] <= s_wdata;
            10'h085: r_resp[95:64] <= s_wdata;
            10'h086: r_resp[63:32] <= s_wdata;
            10'h087: r_resp[31:0] <= s_wdata;
            default: ;
          endcase
      end
      if (s_rvalid && s_rready) s_rvalid <= 1'b0;
      if (rd_go) begin
        rpend <= 1'b1;
        rd_addr <= s_araddr;
      end
      if (rpend) begin
        rpend <= 1'b0;
        s_rvalid <= 1'b1;
        s_rdata <= reg_rd;
      end
    end
  end

  // ---------------------------------------------------------------- pattern generator
  (* ASYNC_REG = "TRUE" *) reg [1:0] pg_go_s;
  reg        pg_go_q, pg_run;
  reg [9:0]  pg_idx;
  reg [23:0] pg_cnt;
  reg [31:0] pg_run_cyc;
  wire [31:0] pg_ent = pgmem[pg_idx];
  always @(posedge gclk) begin
    pg_go_s <= {pg_go_s[0], pg_go_tog};
    pg_go_q <= pg_go_s[1];
    if (pg_go_s[1] != pg_go_q) begin
      pg_out <= r_pg_init;
      pg_idx <= 10'd0;
      pg_cnt <= r_pg_d0;
      pg_run_cyc <= 32'd0;
      if (r_pg_len == 11'd0) pg_done_tog <= ~pg_done_tog;
      else pg_run <= 1'b1;
    end else if (pg_run) begin
      pg_run_cyc <= pg_run_cyc + 1'b1;
      if (pg_cnt <= 24'd1) begin
        pg_out <= pg_ent[7:0];
        pg_cnt <= pg_ent[31:8];
        pg_idx <= pg_idx + 1'b1;
        if ({1'b0, pg_idx} + 11'd1 == r_pg_len) begin
          pg_run <= 1'b0;
          pg_cyc <= pg_run_cyc + 1'b1;
          pg_done_tog <= ~pg_done_tog;
        end
      end else
        pg_cnt <= pg_cnt - 1'b1;
    end
  end

  // ---------------------------------------------------------------- SPI device
  (* ASYNC_REG = "TRUE" *) reg [1:0] arm_s;
  reg         arm_q, s1, s2, m1, pend, seen;
  reg [4:0]   cnt;
  reg [7:0]   per;
  reg [127:0] sh;
  always @(posedge dclk) begin
    arm_s <= {arm_s[0], arm_tog};
    arm_q <= arm_s[1];
    s1 <= sck;                               // the only flops that see protoemu's signals
    m1 <= mosi;
    s2 <= s1;
    if (arm_s[1] != arm_q) begin
      miso <= r_resp[127];
      sh <= {r_resp[126:0], r_resp[127]};
      pend <= 1'b0;
      seen <= 1'b0;
      d_rises <= 16'd0;
      d_rx <= 128'd0;
      d_pmin <= 8'hFF;
      d_pmax <= 8'd0;
      per <= 8'd0;
    end else begin
      if (per != 8'hFF) per <= per + 1'b1;
      // SCK fall seen at this edge: the next bit after DELAY more edges
      if (s2 && !s1) begin
        if (r_dly == 5'd0) begin
          miso <= sh[127];
          sh <= {sh[126:0], sh[127]};
        end else begin
          pend <= 1'b1;
          cnt <= r_dly;
        end
      end else if (pend) begin
        if (cnt == 5'd1) begin
          miso <= sh[127];
          sh <= {sh[126:0], sh[127]};
          pend <= 1'b0;
        end
        cnt <= cnt - 1'b1;
      end
      if (!s2 && s1) begin
        d_rx <= {d_rx[126:0], m1};
        d_rises <= d_rises + 1'b1;
        per <= 8'd0;
        seen <= 1'b1;
        if (seen) begin
          if (per + 8'd1 < d_pmin) d_pmin <= per + 8'd1;
          if (per + 8'd1 > d_pmax) d_pmax <= per + 8'd1;
        end
      end
    end
  end

  initial begin                              // FPGA power-up values (and simulation)
    pg_done_tog = 1'b0;
    pg_go_s = 2'b00;
    pg_go_q = 1'b0;
    pg_go_tog = 1'b0;
    pg_run = 1'b0;
    pg_out = 8'hFF;
    pg_cyc = 32'd0;
    arm_tog = 1'b0;
    arm_s = 2'b00;
    arm_q = 1'b0;
    miso = 1'b1;
    pend = 1'b0;
    d_rises = 16'd0;
    d_pmin = 8'hFF;
    d_pmax = 8'd0;
    d_rx = 128'd0;
    pg_done_s = 2'b00;
  end
endmodule
