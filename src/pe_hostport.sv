/*
 * Host port: SPI mode 0, source-synchronous (SCK clocks the shift registers),
 * 1-bit or 4-bit (quad). docs/isa.md §6.1.
 *
 *   cmd[7:0] (always 1-bit on IO3)  addr[15:0]  [8 dummy SCK for reads]  32-bit words...
 *   1-bit: 0x02 WRITE (inc)  0x0A WRITE_FIXED  0x03 READ (inc)  0x0B READ_FIXED
 *   quad (QUAD_EN): 0x42 / 0x4A / 0x43 / 0x4B, address and data 4 bits per SCK
 *
 * Lines: IO[3:0] in = ui[5:2] (1-bit MOSI = IO3 = ui[5]); out = uo[7:4]
 * (1-bit MISO = IO3 = uo[7]); io_oe drives uo[6:4] only in a quad read's data.
 *
 * SCK domain: inputs sampled on SCK rise, outputs change on SCK fall; reset
 * while CS_n is high. Events cross to clk as toggles (address in, word in,
 * read word sent); their data sits in hold registers that stay stable for at
 * least 8 SCK, longer than the 2-flop synchronizers. The clk side issues one
 * bus operation per cycle and keeps two read words ahead (buf[0], buf[1]):
 * word n+1 is fetched while word n shifts out, so SCK can run at up to clk.
 * SPDX-License-Identifier: Apache-2.0
 */
`default_nettype none

module pe_hostport (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        sck,
    input  wire        cs_n,
    input  wire [3:0]  io_in,
    output wire [3:0]  io_out,
    output wire        io_oe,         // quad read data: drive uo[6:4]
    input  wire        quad_en,       // HOSTCFG.QUAD_EN (change only between transactions)

    output reg  [15:0] bus_addr,
    output reg         bus_we,
    output reg  [31:0] bus_wdata,
    output reg         bus_snap,
    output reg         bus_commit,
    output reg         bus_cancel,
    input  wire [31:0] bus_rdata
`ifdef FORMAL
    ,
    output wire [1:0]  f_outstanding  // read words snapped and not yet committed
`endif
);
  localparam PH_CMD = 3'd0, PH_ADDR = 3'd1, PH_DUMMY = 3'd2, PH_DATA = 3'd3, PH_IGNORE = 3'd4;

  // ---------------------------------------------------------------- SCK domain
  // (async reset while CS_n is high: every transaction starts from the command)
  reg  [2:0]  phase;
  reg  [5:0]  cnt;          // SCK edges in this phase
  reg  [7:0]  cmd;
  reg         quad, rd, inc;
  reg  [31:0] sh;           // input shift register
  reg         wpar;         // read: which buffer the current word comes from
  wire [31:0] sh_n1 = {sh[30:0], io_in[3]};
  wire [31:0] sh_n4 = {sh[27:0], io_in};
  wire [31:0] sh_n  = quad ? sh_n4 : sh_n1;
  wire [7:0]  cmd_n = {cmd[6:0], io_in[3]};
  wire        cmd_ok = (cmd_n[7:4] == 4'h0 || (cmd_n[7:4] == 4'h4 && quad_en)) &&
                       (cmd_n[3:0] == 4'h2 || cmd_n[3:0] == 4'hA || cmd_n[3:0] == 4'h3 || cmd_n[3:0] == 4'hB);
  wire [5:0]  addr_len = quad ? 6'd3 : 6'd15;     // last SCK of the address
  wire [5:0]  word_len = quad ? 6'd7 : 6'd31;     // last SCK of a data word

  // hold registers and toggles: not reset by CS_n, so an event raised on the
  // last SCK of a transaction still reaches the clk side
  reg [15:0] a_hold;
  reg [1:0]  m_hold;        // {rd, inc}
  reg [31:0] w_hold;
  reg        a_tog, w_tog, r_tog;

  // After global reset, require an idle CS interval in clk before accepting
  // SCK. A transaction held selected across reset is ignored until CS rises.
  reg host_ready;
  wire parser_reset = cs_n || !rst_n || !host_ready;
  always @(posedge sck or posedge parser_reset) begin
    if (parser_reset) begin
      phase <= PH_CMD;
      cnt <= 6'd0;
      cmd <= 8'd0;
      quad <= 1'b0;
      rd <= 1'b0;
      inc <= 1'b0;
      sh <= 32'd0;
      wpar <= 1'b0;
    end else begin
      cnt <= cnt + 6'd1;
      case (phase)
        PH_CMD: begin
          cmd <= cmd_n;
          if (cnt == 6'd7) begin
            cnt <= 6'd0;
            phase <= cmd_ok ? PH_ADDR : PH_IGNORE;
            quad <= cmd_n[6];
            rd <= cmd_n[0];
            inc <= !cmd_n[3];
          end
        end
        PH_ADDR: begin
          sh <= sh_n;
          if (cnt == addr_len) begin
            cnt <= 6'd0;
            phase <= rd ? PH_DUMMY : PH_DATA;
          end
        end
        PH_DUMMY:
          if (cnt == 6'd7) begin
            cnt <= 6'd0;
            phase <= PH_DATA;
          end
        PH_DATA: begin
          sh <= sh_n;
          if (cnt == word_len) begin
            cnt <= 6'd0;
            wpar <= !wpar;
          end
        end
        default: ;
      endcase
    end
  end

  always @(posedge sck or negedge rst_n) begin
    if (!rst_n) begin
      a_hold <= 16'd0;
      m_hold <= 2'd0;
      w_hold <= 32'd0;
      a_tog <= 1'b0;
      w_tog <= 1'b0;
      r_tog <= 1'b0;
    end else if (!cs_n && host_ready) begin
      if (phase == PH_ADDR && cnt == addr_len) begin
        a_hold <= sh_n[15:0];
        m_hold <= {rd, inc};
        a_tog <= !a_tog;
      end
      if (phase == PH_DATA && cnt == word_len) begin
        if (rd) r_tog <= !r_tog;              // the host has sampled the last bit
        else begin
          w_hold <= sh_n;
          w_tog <= !w_tog;
        end
      end
    end
  end

  // output side: changes on SCK fall, half a period before the host samples
  reg [31:0] osh;
  reg [31:0] buf0, buf1;                      // clk domain, stable when used
  always @(negedge sck or posedge parser_reset) begin
    if (parser_reset) osh <= 32'd0;
    else if (phase == PH_DATA && rd)
      osh <= (cnt == 6'd0) ? (wpar ? buf1 : buf0) : (quad ? {osh[27:0], 4'd0} : {osh[30:0], 1'b0});
  end
  assign io_out = (!parser_reset && phase == PH_DATA && rd) ? (quad ? osh[31:28] : {osh[31], 3'b000}) : 4'd0;
  assign io_oe  = !parser_reset && phase == PH_DATA && rd && quad;

  // ---------------------------------------------------------------- clk domain
  (* async_reg = "true", keep = 1 *) reg [2:0] a_s, w_s, r_s, cs_s;
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) host_ready <= 1'b0;
    else if (cs_n && &cs_s) host_ready <= 1'b1;
  end
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      a_s <= 3'd0;
      w_s <= 3'd0;
      r_s <= 3'd0;
      cs_s <= 3'b111;
    end else begin
      a_s <= {a_s[1:0], a_tog};
      w_s <= {w_s[1:0], w_tog};
      r_s <= {r_s[1:0], r_tog};
      cs_s <= {cs_s[1:0], cs_n};
    end
  end
  wire a_ev = a_s[2] != a_s[1];
  wire w_ev = w_s[2] != w_s[1];
  wire r_ev = r_s[2] != r_s[1];

  // read state: snap_addr is the next word to fetch, com_addr the oldest unsent
  reg        active, c_rd, c_inc;
  reg [15:0] snap_addr, com_addr;
  reg [1:0]  outstanding;                     // snapped, not committed (0..2)
  reg        fill;                            // buffer the next snapshot goes to
  reg        a_pend, w_pend, need_commit, need_snap;
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      active <= 1'b0;
      c_rd <= 1'b0;
      c_inc <= 1'b0;
      snap_addr <= 16'd0;
      com_addr <= 16'd0;
      outstanding <= 2'd0;
      fill <= 1'b0;
      a_pend <= 1'b0;
      w_pend <= 1'b0;
      need_commit <= 1'b0;
      need_snap <= 1'b0;
      bus_addr <= 16'd0;
      bus_we <= 1'b0;
      bus_wdata <= 32'd0;
      bus_snap <= 1'b0;
      bus_commit <= 1'b0;
      bus_cancel <= 1'b0;
      buf0 <= 32'd0;
      buf1 <= 32'd0;
    end else begin
      bus_we <= 1'b0;
      bus_snap <= 1'b0;
      bus_commit <= 1'b0;
      bus_cancel <= 1'b0;
      // capture read data in the cycle after a snapshot (bus_addr still holds it)
      if (bus_snap) begin
        if (fill) buf1 <= bus_rdata; else buf0 <= bus_rdata;
        fill <= !fill;
      end
      if (a_ev) a_pend <= 1'b1;
      if (w_ev) w_pend <= 1'b1;
      if (r_ev) need_commit <= 1'b1;
      // One bus operation per cycle, oldest work first. Events that a host
      // outside the timing contract can produce out of order (a word sent with
      // nothing outstanding, a new address before the last read was cancelled)
      // are dropped or resolved, never miscounted.
      if (need_commit && outstanding == 2'd0) begin
        need_commit <= 1'b0;
      end else if (need_commit) begin          // a read word was fully sent
        need_commit <= 1'b0;
        bus_commit <= 1'b1;
        bus_addr <= com_addr;
        com_addr <= com_addr + {15'd0, c_inc};
        outstanding <= outstanding - 2'd1;
        need_snap <= 1'b1;
      end else if (w_pend) begin               // a write word arrived
        w_pend <= 1'b0;
        if (active && !c_rd) begin
          bus_we <= 1'b1;
          bus_wdata <= w_hold;
          bus_addr <= snap_addr;
          snap_addr <= snap_addr + {15'd0, c_inc};
        end
      end else if ((a_pend || cs_s[2]) && active) begin
        active <= 1'b0;                        // the transaction is over
        need_snap <= 1'b0;
        outstanding <= 2'd0;
        if (c_rd) bus_cancel <= 1'b1;          // drop the words fetched ahead
      end else if (a_pend) begin               // a new transaction's address
        a_pend <= 1'b0;
        active <= 1'b1;
        {c_rd, c_inc} <= m_hold;
        snap_addr <= a_hold;
        com_addr <= a_hold;
        fill <= 1'b0;
        need_snap <= m_hold[1];                // reads: fetch two words ahead
      end else if (need_snap && !bus_snap && outstanding != 2'd2 && c_rd && active) begin
        bus_snap <= 1'b1;
        bus_addr <= snap_addr;
        snap_addr <= snap_addr + {15'd0, c_inc};
        outstanding <= outstanding + 2'd1;
        need_snap <= outstanding == 2'd0;      // after the first, fetch the second
      end
    end
  end

`ifdef FORMAL
  assign f_outstanding = outstanding;
  // invariants for formal/hostport.sby's induction
  always @(*) if (rst_n) begin
    assert (outstanding != 2'd3);
    if (!(active && c_rd)) assert (outstanding == 2'd0);
  end
`endif
endmodule
