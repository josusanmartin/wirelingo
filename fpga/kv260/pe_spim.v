/*
 * Fabric SPI master for protoemu's host port, with an AXI4-Lite slave.
 *
 * The host fills a 4096-word buffer, sets CMD/LEN/CFG and writes GO. The
 * master then runs one transaction on the host port: CS_n low, the command
 * byte 1-bit on IO3, the 16-bit address and the data 1-bit or 4 bits per SCK
 * (CFG.QUAD), 8 dummy SCK before read data (CFG.READ), CS_n high. Writes send
 * buffer words 0..LEN-1; reads store the received words there. SCK is the
 * master clock gated by a BUFGCE (sck_ce, kv260_top.v): a beat every CFG.DIV
 * master clocks, each a high pulse of half a master clock, so DIV = 1 gives
 * SCK = mclk. The master changes IO right after the SCK rise that sampled the
 * previous beat and samples the port's IO on each SCK rise (SPI mode 0).
 *
 * Registers (byte offsets; the buffer is word i at 4*i, 0x0000-0x3FFC):
 *   0x8000 CMD     [7:0] command byte, [23:8] address
 *   0x8004 LEN     [12:0] data words (0..4096)
 *   0x8008 CFG     [7:0] DIV (SCK period in master clocks; 0 acts as 1), [8] QUAD, [9] READ
 *   0x800C SEL     [0] FABRIC: host-port pins from this master (else the GPIO path),
 *                  [1] ALT: master clock = alternate clock (else protoemu's clock)
 *   0x8010 GO      W: start a transaction.  R: STATUS [0] busy
 *   0x8018 PCYC    R: last CS_n-low time on ui_in[7], in protoemu clocks (either path)
 *   0x801C MCYC    R: last CS_n-low time of this master, in master clocks
 *   0x8020 PMOD0   PMOD pins 0..3, 8 bits each: [7] enable, [4:0] TT pin (kv260_top.v)
 *   0x8024 PMOD1   PMOD pins 4..7
 *   0x8028 PMODIN  R: [7:0] PMOD pad inputs
 *   0x802C VERSION R: 0x53504D01
 *   0x8030 TIMING  [3:0] CS setup, [7:4] CS hold, [11:8] CS gap, in master clocks (reset 2/3/8)
 * Software changes the registers only while the master is idle; they cross to
 * the master clock as quasi-static values.
 * SPDX-License-Identifier: Apache-2.0
 */
`default_nettype none

module pe_spim (
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
    output reg         sel_fabric,
    output reg         sel_alt,
    output wire        quad_pins,
    output reg  [31:0] pmod0,
    output reg  [31:0] pmod1,
    input  wire [7:0]  pmod_in,
    // master (mclk domain)
    input  wire        mclk,
    output reg         sck_ce,
    output reg         cs_n,
    output reg  [3:0]  io_o,
    input  wire [3:0]  io_i,
    // protoemu clock domain: CS_n-low timer on the pin protoemu sees
    input  wire        pclk,
    input  wire        cs_pin
);
  // ---------------------------------------------------------------- buffer
  reg [31:0] mem[0:4095];
  reg [31:0] a_q, b_q;

  // ---------------------------------------------------------------- AXI-Lite
  reg  [23:0] r_cmd;
  reg  [12:0] r_len;
  reg  [9:0]  r_cfg;
  reg  [11:0] r_tim;
  reg         go_tog;
  reg         rpend;
  reg  [15:0] rd_addr;
  wire        wr_go = s_awvalid && s_wvalid && !s_bvalid;
  wire        rd_go = s_arvalid && !s_rvalid && !rpend && !wr_go;
  assign s_awready = wr_go;
  assign s_wready  = wr_go;
  assign s_arready = rd_go;
  assign quad_pins = r_cfg[8];

  wire [11:0] a_addr = wr_go ? s_awaddr[13:2] : s_araddr[13:2];
  always @(posedge aclk) begin
    if (wr_go && !s_awaddr[15]) mem[a_addr] <= s_wdata;
    a_q <= mem[a_addr];
  end

  reg        done_tog;
  (* ASYNC_REG = "TRUE" *) reg [1:0] done_s;
  (* ASYNC_REG = "TRUE" *) reg [7:0] pin_s0, pin_s1;
  reg [31:0] pcyc, mcyc;
  always @(posedge aclk) begin
    done_s <= {done_s[0], done_tog};
    pin_s0 <= pmod_in;
    pin_s1 <= pin_s0;
  end
  wire busy = go_tog ^ done_s[1];

  reg [31:0] reg_rd;
  always @(*) begin
    case (rd_addr[7:2])
      6'd0: reg_rd = {8'd0, r_cmd};
      6'd1: reg_rd = {19'd0, r_len};
      6'd2: reg_rd = {22'd0, r_cfg};
      6'd3: reg_rd = {30'd0, sel_alt, sel_fabric};
      6'd4: reg_rd = {31'd0, busy};
      6'd6: reg_rd = pcyc;
      6'd7: reg_rd = mcyc;
      6'd8: reg_rd = pmod0;
      6'd9: reg_rd = pmod1;
      6'd10: reg_rd = {24'd0, pin_s1};
      6'd11: reg_rd = 32'h53504D01;
      6'd12: reg_rd = {20'd0, r_tim};
      default: reg_rd = 32'd0;
    endcase
  end

  always @(posedge aclk) begin
    if (!aresetn) begin
      s_bvalid <= 1'b0;
      s_rvalid <= 1'b0;
      rpend <= 1'b0;
      r_cmd <= 24'd0;
      r_len <= 13'd0;
      r_cfg <= 10'd4;
      r_tim <= 12'h832;
      sel_fabric <= 1'b0;
      sel_alt <= 1'b0;
      pmod0 <= 32'd0;
      pmod1 <= 32'd0;
      go_tog <= done_s[1];
    end else begin
      if (s_bvalid && s_bready) s_bvalid <= 1'b0;
      if (wr_go) begin
        s_bvalid <= 1'b1;
        if (s_awaddr[15])
          case (s_awaddr[7:2])
            6'd0: r_cmd <= s_wdata[23:0];
            6'd1: r_len <= s_wdata[12:0];
            6'd2: r_cfg <= s_wdata[9:0];
            6'd3: {sel_alt, sel_fabric} <= s_wdata[1:0];
            6'd4: if (!busy) go_tog <= ~go_tog;
            6'd8: pmod0 <= s_wdata;
            6'd9: pmod1 <= s_wdata;
            6'd12: r_tim <= s_wdata[11:0];
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
        s_rdata <= rd_addr[15] ? reg_rd : a_q;
      end
    end
  end

  // ---------------------------------------------------------------- master
  localparam IDLE = 3'd0, SETUP = 3'd1, SHIFT = 3'd2, HOLD = 3'd3, GAP = 3'd4;
  localparam PH_CMD = 2'd0, PH_ADDR = 2'd1, PH_DUMMY = 2'd2, PH_DATA = 2'd3;
  (* ASYNC_REG = "TRUE" *) reg [1:0] go_s;
  reg        go_q;
  reg [2:0]  st = IDLE;
  reg [1:0]  ph;
  reg [31:0] sh, isr;
  reg [4:0]  bc, blast, cbc;
  reg [12:0] wcnt, len;
  reg [11:0] b_addr, b_waddr, cw;
  reg        b_we, cap_en, quad, rd;
  reg [31:0] b_wdata;
  reg [7:0]  dcnt, div;
  reg [3:0]  tcnt;
  reg [15:0] addr;
  reg [31:0] mrun;

  wire [11:0] b_port = b_we ? b_waddr : b_addr;
  always @(posedge mclk) begin
    if (b_we) mem[b_port] <= b_wdata;
    b_q <= mem[b_port];
  end

  wire        wide = quad && (ph == PH_ADDR || ph == PH_DATA);
  wire [31:0] isr_n = quad ? {isr[27:0], io_i} : {isr[30:0], io_i[3]};

  always @(posedge mclk) begin
    go_s <= {go_s[0], go_tog};
    go_q <= go_s[1];
    b_we <= 1'b0;
    sck_ce <= 1'b0;
    cap_en <= 1'b0;
    if (!cs_n) mrun <= mrun + 1'b1;
    // the SCK rise after an emitted data beat: sample the port's IO
    if (cap_en) begin
      isr <= isr_n;
      if (cbc == (quad ? 5'd7 : 5'd31)) begin
        cbc <= 5'd0;
        b_we <= 1'b1;
        b_wdata <= isr_n;
        b_waddr <= cw;
        cw <= cw + 1'b1;
      end else
        cbc <= cbc + 1'b1;
    end
    case (st)
      IDLE:
        if (go_s[1] != go_q) begin
          addr <= r_cmd[23:8];
          len <= r_len;
          div <= (r_cfg[7:0] == 8'd0) ? 8'd1 : r_cfg[7:0];
          quad <= r_cfg[8];
          rd <= r_cfg[9];
          ph <= PH_CMD;
          sh <= {r_cmd[7:0], 24'd0};
          bc <= 5'd0;
          blast <= 5'd7;
          b_addr <= 12'd0;
          cw <= 12'd0;
          cbc <= 5'd0;
          dcnt <= 8'd0;
          tcnt <= r_tim[3:0];
          mrun <= 32'd0;
          cs_n <= 1'b0;
          st <= SETUP;
        end
      SETUP:
        if (tcnt <= 4'd1) st <= SHIFT;
        else tcnt <= tcnt - 1'b1;
      SHIFT:
        if (dcnt != 8'd0) dcnt <= dcnt - 1'b1;
        else begin
          dcnt <= div - 1'b1;
          sck_ce <= 1'b1;                       // SCK rises at the next mclk edge
          io_o <= wide ? sh[31:28] : {sh[31], 3'b000};
          cap_en <= rd && ph == PH_DATA;
          if (bc != blast) begin
            bc <= bc + 1'b1;
            sh <= wide ? sh << 4 : sh << 1;
          end else begin
            bc <= 5'd0;
            case (ph)
              PH_CMD: begin
                ph <= PH_ADDR;
                sh <= {addr, 16'd0};
                blast <= quad ? 5'd3 : 5'd15;
              end
              PH_ADDR:
                if (rd) begin
                  ph <= PH_DUMMY;
                  sh <= 32'd0;
                  blast <= 5'd7;
                end else if (len != 13'd0) begin
                  ph <= PH_DATA;
                  sh <= b_q;                    // word 0 (b_addr = 0 since the start)
                  b_addr <= 12'd1;
                  wcnt <= 13'd1;
                  blast <= quad ? 5'd7 : 5'd31;
                end else begin
                  st <= HOLD;
                  tcnt <= r_tim[7:4];
                end
              PH_DUMMY:
                if (len != 13'd0) begin
                  ph <= PH_DATA;
                  sh <= 32'd0;
                  wcnt <= 13'd1;
                  blast <= quad ? 5'd7 : 5'd31;
                end else begin
                  st <= HOLD;
                  tcnt <= r_tim[7:4];
                end
              default:                          // PH_DATA: next word or done
                if (wcnt == len) begin
                  st <= HOLD;
                  tcnt <= r_tim[7:4];
                end else begin
                  wcnt <= wcnt + 1'b1;
                  sh <= rd ? 32'd0 : b_q;
                  b_addr <= b_addr + 1'b1;
                end
            endcase
          end
        end
      HOLD:                                     // the last SCK pulse is at the first edge
        if (tcnt <= 4'd1) begin
          cs_n <= 1'b1;
          mcyc <= mrun;
          tcnt <= r_tim[11:8];
          st <= GAP;
        end else tcnt <= tcnt - 1'b1;
      GAP:
        if (tcnt <= 4'd1) begin
          done_tog <= ~done_tog;
          st <= IDLE;
        end else tcnt <= tcnt - 1'b1;
      default: st <= IDLE;
    endcase
  end

  // ---------------------------------------------------------------- CS_n timer
  (* ASYNC_REG = "TRUE" *) reg [1:0] cs_s;
  reg [31:0] prun;
  always @(posedge pclk) begin
    cs_s <= {cs_s[0], cs_pin};
    if (!cs_s[1]) prun <= prun + 1'b1;
    else if (prun != 32'd0) begin
      pcyc <= prun;
      prun <= 32'd0;
    end
  end

  initial begin                              // FPGA power-up values (and simulation)
    cs_n = 1'b1;
    sck_ce = 1'b0;
    done_tog = 1'b0;
    go_s = 2'b00;
    go_q = 1'b0;
    mrun = 32'd0;
    mcyc = 32'd0;
    isr = 32'd0;
    cs_s = 2'b11;
    prun = 32'd0;
    pcyc = 32'd0;
    done_s = 2'b00;
  end
endmodule
