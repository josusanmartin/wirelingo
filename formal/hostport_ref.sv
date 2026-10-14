// Formal: host port (pe_hostport) address/data ordering, read epochs and reset
// reassertion, against an independent reference model of the serial protocol
// (docs/isa.md §6.1) written here from the pins. docs/formal-hostport.md has
// the contract, the properties and what is not proved.
//
// Time: one solver step is a quarter of a clk period (clk is generated here,
// period 4 steps, free initial phase). SCK, CS_n, IO, QUAD_EN, rst_n and
// bus_rdata are free inputs, constrained only by the §6.1 host contract (the
// A* assumptions). Every real-time ordering of edges that meets the contract
// maps onto this grid (edges 0.5 clk apart land >= 2 steps apart); edges in
// the same step are seen by each flop with the old value (multiclock).
//
// The reference decodes each transaction from the pins with its own counters
// (bits received per field, not the RTL's SCK counts), predicts every bus
// operation, and checks the RTL's bus side and its MISO/IO output against it:
//   P1 writes: the n-th complete word of a WRITE goes to addr+n (0x02/0x42) or
//      addr (0x0A/0x4A) with the data the host sent, in order, exactly once,
//      within LW steps; nothing else is written.
//   P2 reads: the n-th snapshot is of addr+n (0x03/0x43) or addr (0x0B/0x4B);
//      the n-th word the host clocks out equals the data that snapshot
//      captured; the n-th commit is of the n-th word, only after its last bit
//      was clocked out, within LC steps; at most two words snapped and not
//      committed.
//   P3 epochs: a read transaction is cancelled exactly once, after CS rises,
//      after all its complete words are committed, within LX steps; nothing
//      of a transaction is pending once the host selects the chip again.
//   P4 a word cut by CS rising is neither written nor committed (from P1-P3:
//      every bus operation must match a completed word or the read epoch).
//   P5 reset at any step (SCK running or stopped, CS high or low, mid-word,
//      with reads outstanding): no later bus operation unless a new, legally
//      re-armed transaction asks for it (the checks above, with the reference
//      flushed by reset); the quad drivers stay off in an aborted transaction.
//   P6 the clock-domain crossings keep a margin: the clk side takes a write
//      word or an address >= 2 clk after its last bit, and the SCK side loads a
//      read word >= 1 clk after its snapshot was captured.
`default_nettype none
module hostport_ref (
    input wire        sck,
    input wire        cs_n,
    input wire [3:0]  io_in,
    input wire        quad_en,
    input wire        rst_n,       // the chip's synchronised reset (rstn in project.v)
    input wire [31:0] bus_rdata
);
  // ------------------------------------------------------------ time base
  reg [1:0] ph;                     // free initial phase
  reg       init = 1'b1;
  always @($global_clock) begin
    ph <= ph + 2'd1;
    init <= 1'b0;
  end
  wire clk      = ph[1];
  wire clk_rise = ph == 2'd2;       // clk rose in this step

  // previous-step values
  reg       sck_q, sck_qq, cs_q, rst_q;
  reg [3:0] io_in_q, io_out_q;
  reg       io_oe_q, snap_q, quad_q;
  reg [31:0] rdata_q;
  wire [3:0]  io_out;
  wire        io_oe, bus_we, bus_snap, bus_commit, bus_cancel;
  wire [15:0] bus_addr;
  wire [31:0] bus_wdata;
  wire [1:0]  f_out;
  always @($global_clock) begin
    sck_q <= sck;
    sck_qq <= sck_q;
    cs_q <= cs_n;
    rst_q <= rst_n;
    io_in_q <= io_in;
    io_out_q <= io_out;
    io_oe_q <= io_oe;
    snap_q <= bus_snap;
    quad_q <= quad_en;
    rdata_q <= bus_rdata;
  end
  wire sck_rise = sck && !sck_q;
  wire sck_fall = !sck && sck_q;
  wire cs_rise  = cs_n && !cs_q;
  wire cs_fall  = !cs_n && cs_q;

  pe_hostport dut (
      .clk(clk), .rst_n(rst_n), .sck(sck), .cs_n(cs_n), .io_in(io_in), .io_out(io_out),
      .io_oe(io_oe), .quad_en(quad_en), .bus_addr(bus_addr), .bus_we(bus_we),
      .bus_wdata(bus_wdata), .bus_snap(bus_snap), .bus_commit(bus_commit),
      .bus_cancel(bus_cancel), .bus_rdata(bus_rdata), .f_outstanding(f_out));

  // ------------------------------------------------------------ host contract
  // ages, saturating: steps the signal has been in its current state, up to
  // and including the previous step
  reg [4:0] cs_hi_age;              // CS_n high (sat 16)
  reg [3:0] cs_lo_age;              // CS_n low (sat 15)
  reg [2:0] sck_lo_age;             // SCK low (sat 4)
  reg [4:0] rel_age;                // rst_n high (sat 20)
  reg       lo_after_rel;           // CS_n was low at some step since rst_n rose
  always @($global_clock) begin
    cs_hi_age  <= !cs_n ? 5'd0 : (cs_hi_age == 5'd16 ? 5'd16 : cs_hi_age + 5'd1);
    cs_lo_age  <=  cs_n ? 4'd0 : (cs_lo_age == 4'd15 ? 4'd15 : cs_lo_age + 4'd1);
    sck_lo_age <=  sck  ? 3'd0 : (sck_lo_age == 3'd4 ? 3'd4 : sck_lo_age + 3'd1);
    rel_age    <= !rst_n ? 5'd0 : (rel_age == 5'd20 ? 5'd20 : rel_age + 5'd1);
    lo_after_rel <= !rst_n ? 1'b0 : (lo_after_rel || !cs_n);
  end

  always @(*) begin
    // A1 the chip starts in reset, deselected
    if (init) assume (!rst_n && cs_n);
    // A2 reset asserts at any step and is released just after a clk rise
    //    (project.v's synchroniser: async assert, deassert from a clk flop)
    if (rst_n && !rst_q) assume (ph == 2'd3);
    // A3 SCK high and low each >= 0.5 clk (so its period is >= 1 clk)
    if (sck != sck_q) assume (sck_q == sck_qq);
    // A4 mode 0: SCK idle low while deselected
    if (cs_n) assume (!sck);
    // A5 CS_n low to the first SCK rise >= 1 clk; last SCK fall to CS_n high
    //    >= 1 clk; CS_n high between transactions >= 4 clk
    if (sck_rise) assume (cs_lo_age >= 4'd4);
    if (cs_rise) assume (sck_lo_age == 3'd4);
    if (cs_fall) assume (cs_hi_age == 5'd16);
    // A6 after reset release, CS_n high >= 5 clk before a transaction, unless
    //    CS_n was held low through the release (that transaction is ignored
    //    and the next one follows the >= 4 clk gap of A5)
    if (cs_fall && rst_n) assume (rel_age == 5'd20 || lo_after_rel);
    // A7 the host changes IO on SCK falls, so IO is stable across a rise
    if (sck_rise) assume (io_in == io_in_q);
    // A8 QUAD_EN changes only between transactions, or clears under reset
    //    (HOSTCFG's reset)
    if (quad_en != quad_q) assume ((cs_n && cs_q) || (!rst_n && !quad_en));
  end

`ifdef GUIDE
  // Guided runs (covers, mutants) explore a subset of the contract, so every
  // trace they find is a legal one: a fixed start phase, and SCK edges only
  // with a clk rise or half a clk from one.
  always @(*) begin
    if (init) assume (ph == 2'd0);
    if (sck != sck_q) assume (!ph[0]);
`ifdef COVER1
    // the long 1-bit covers: SCK rises half a clk before a clk rise only
    if (sck_rise) assume (ph == 2'd0);
`endif
  end
`endif

  // ------------------------------------------------------------ reference: serial side
  // Decodes the pins as the host sees them: bits on IO3 (1-bit) or IO[3:0]
  // (quad, IO3 = MSB) at each SCK rise; read data as the chip drove it just
  // before the rise.
  localparam F_CMD = 3'd0, F_ADDR = 3'd1, F_DUMMY = 3'd2, F_DATA = 3'd3, F_IGN = 3'd4;
  reg [2:0]  r_f;       // field
  reg [5:0]  r_n;       // bits received in this field (dummy: SCK rises)
  reg [7:0]  r_cmd;
  reg [31:0] r_in;      // address and data bits from the host, in arrival order
  reg [31:0] r_out;     // read data bits the host sampled, in arrival order
  reg        r_quad, r_rd, r_inc;
  reg        r_ok;      // counted: selected with rst_n high and no reset since
  reg [15:0] r_wa;      // address of the next write word
  reg        r_wp;      // data words completed in this transaction, mod 2

  // §6.1 command table
  function automatic cmd_known(input [7:0] c, input q);
    case (c)
      8'h02, 8'h0A, 8'h03, 8'h0B: cmd_known = 1'b1;
      8'h42, 8'h4A, 8'h43, 8'h4B: cmd_known = q;
      default:                    cmd_known = 1'b0;
    endcase
  endfunction
  wire [7:0] cmd_now  = {r_cmd[6:0], io_in[3]};
  wire       cmd_rd   = cmd_now == 8'h03 || cmd_now == 8'h0B || cmd_now == 8'h43 || cmd_now == 8'h4B;
  wire       cmd_inc  = cmd_now == 8'h02 || cmd_now == 8'h03 || cmd_now == 8'h42 || cmd_now == 8'h43;
  wire       cmd_quad = cmd_now[7:4] == 4'h4;
  wire [5:0] bps = r_quad ? 6'd4 : 6'd1;
  wire [31:0] in_sh  = r_quad ? {r_in[27:0], io_in} : {r_in[30:0], io_in[3]};
  wire [31:0] out_sh = r_quad ? {r_out[27:0], io_out_q} : {r_out[30:0], io_out_q[3]};
  wire       ok = r_ok && rst_n;

  reg [2:0]  nf;
  reg [5:0]  nn;
  reg [7:0]  ncmd;
  reg [31:0] nin, nout;
  reg        nquad, nrd, ninc, nok, nwp;
  reg        ev_addr, ev_word;   // a counted address / data word completed in this step
  always @(*) begin
    nf = r_f; nn = r_n; ncmd = r_cmd; nin = r_in; nout = r_out;
    nquad = r_quad; nrd = r_rd; ninc = r_inc; nwp = r_wp;
    nok = rst_n && !cs_n && (cs_fall || r_ok);
    ev_addr = 1'b0;
    ev_word = 1'b0;
    if (cs_n) begin
      nf = F_CMD; nn = 6'd0; ncmd = 8'd0; nin = 32'd0; nout = 32'd0;
      nquad = 1'b0; nrd = 1'b0; ninc = 1'b0; nwp = 1'b0;
    end else if (sck_rise) begin
      case (r_f)
        F_CMD: begin
          ncmd = cmd_now;
          nn = r_n + 6'd1;
          if (r_n == 6'd7) begin
            nn = 6'd0;
            nf = cmd_known(cmd_now, quad_q) ? F_ADDR : F_IGN;   // QUAD_EN as the edge sees it
            nquad = cmd_quad;
            nrd = cmd_rd;
            ninc = cmd_inc;
          end
        end
        F_ADDR: begin
          nin = in_sh;
          nn = r_n + bps;
          if (nn == 6'd16) begin
            nn = 6'd0;
            nf = r_rd ? F_DUMMY : F_DATA;
            ev_addr = ok;
          end
        end
        F_DUMMY: begin
          nn = r_n + 6'd1;
          if (r_n == 6'd7) begin
            nn = 6'd0;
            nf = F_DATA;
          end
        end
        F_DATA: begin
          nin = in_sh;
          nout = out_sh;
          nn = r_n + bps;
          if (nn == 6'd32) begin
            nn = 6'd0;
            nwp = !r_wp;
            ev_word = ok;
          end
        end
        default: ;
      endcase
    end
  end

  // ------------------------------------------------------------ reference: bus side
  // Obligations the serial side creates, discharged by the RTL's bus operations
  // (an operation is counted in the step whose clk rise issues it).
  localparam LW = 6'd16, LC = 6'd16, LX = 6'd16;   // steps (clk/4)
  wire op_we     = clk_rise && bus_we;
  wire op_snap   = clk_rise && bus_snap;
  wire op_commit = clk_rise && bus_commit;
  wire op_cancel = clk_rise && bus_cancel;
  wire capture   = clk_rise && snap_q && rst_n;   // a snapshot's data is taken

  reg        ws_v;          // a completed write word not yet written
  reg [15:0] ws_a;
  reg [31:0] ws_d;
  reg        rd_live;       // a counted read's epoch: address done, not yet cancelled
  reg        rd_end;        // its CS_n has risen
  reg        rd_inc;
  reg [15:0] e_snap, e_com; // addresses of the next snapshot / commit
  reg [1:0]  n_snap, n_done, n_com;   // snapshots captured, words clocked out, commits (mod 4)
  reg [31:0] D0, D1;        // captured data of the snapshots with even / odd index
  reg [5:0]  ws_age, com_age, end_age;

  reg        nws_v, nrd_live, nrd_end, nrd_inc;
  reg [15:0] nws_a, ne_snap, ne_com, nwa;
  reg [31:0] nws_d, nD0, nD1;
  reg [1:0]  nn_snap, nn_done, nn_com;
  reg        ck_addr_clear;   // at a new address: nothing of the last transaction pending
  reg        ck_wslot;        // at a write word: the previous one was written
  reg        ck_rsnap;        // at a read word: its snapshot was captured in this transaction
  reg        ck_rword;        // at a read word: equal to what the host got
  reg        ck_rcom;         // at a read word: the previous one was committed
  always @(*) begin
    nws_v = ws_v; nws_a = ws_a; nws_d = ws_d;
    nrd_live = rd_live; nrd_end = rd_end; nrd_inc = rd_inc;
    ne_snap = e_snap; ne_com = e_com; nwa = r_wa;
    nn_snap = n_snap; nn_done = n_done; nn_com = n_com;
    nD0 = D0; nD1 = D1;
    ck_addr_clear = 1'b1; ck_wslot = 1'b1; ck_rsnap = 1'b1; ck_rword = 1'b1; ck_rcom = 1'b1;
    // bus operations (checked below against the values before this step)
    if (capture) begin
      if (n_snap[0]) nD1 = rdata_q; else nD0 = rdata_q;
      nn_snap = n_snap + 2'd1;
    end
    if (op_we) nws_v = 1'b0;
    if (op_snap) ne_snap = e_snap + {15'd0, rd_inc};
    if (op_commit) begin
      ne_com = e_com + {15'd0, rd_inc};
      nn_com = n_com + 2'd1;
    end
    if (op_cancel) begin
      nrd_live = 1'b0;
      nrd_end = 1'b0;
    end
    // serial events
    if (ev_addr) begin
      ck_addr_clear = !nws_v && !nrd_live;
      if (r_rd) begin
        nrd_live = 1'b1;
        nrd_end = 1'b0;
        nrd_inc = r_inc;
        ne_snap = nin[15:0];
        ne_com = nin[15:0];
        nn_snap = 2'd0;
        nn_done = 2'd0;
        nn_com = 2'd0;
      end else
        nwa = nin[15:0];
    end
    if (ev_word && !r_rd) begin
      ck_wslot = !nws_v;
      nws_v = 1'b1;
      nws_a = r_wa;
      nws_d = nin;
      nwa = r_wa + {15'd0, r_inc};
    end
    if (ev_word && r_rd) begin
      ck_rsnap = rd_live && (nn_snap - n_done == 2'd1 || nn_snap - n_done == 2'd2);
      ck_rword = nout == (n_done[0] ? nD1 : nD0);
      ck_rcom = nn_com == n_done;
      nn_done = n_done + 2'd1;
    end
    if (cs_rise && rd_live) nrd_end = 1'b1;
    // reset: nothing is owed any more
    if (!rst_n) begin
      nws_v = 1'b0;
      nrd_live = 1'b0;
      nrd_end = 1'b0;
      nn_snap = 2'd0;
      nn_done = 2'd0;
      nn_com = 2'd0;
      nD0 = 32'd0;
      nD1 = 32'd0;
    end
  end

  always @($global_clock) begin
    r_f <= nf; r_n <= nn; r_cmd <= ncmd; r_in <= nin; r_out <= nout;
    r_quad <= nquad; r_rd <= nrd; r_inc <= ninc; r_ok <= nok; r_wp <= nwp;
    r_wa <= nwa;
    ws_v <= nws_v; ws_a <= nws_a; ws_d <= nws_d;
    rd_live <= nrd_live; rd_end <= nrd_end; rd_inc <= nrd_inc;
    e_snap <= ne_snap; e_com <= ne_com;
    n_snap <= nn_snap; n_done <= nn_done; n_com <= nn_com;
    D0 <= nD0; D1 <= nD1;
    ws_age  <= nws_v ? (ws_v ? ws_age + 6'd1 : 6'd1) : 6'd0;
    com_age <= nn_done != nn_com ? (n_done != n_com ? com_age + 6'd1 : 6'd1) : 6'd0;
    end_age <= nrd_live && nrd_end ? (rd_live && rd_end ? end_age + 6'd1 : 6'd1) : 6'd0;
  end
  initial begin
    r_ok = 1'b0;
    ws_v = 1'b0;
    rd_live = 1'b0;
    rd_end = 1'b0;
    n_snap = 2'd0;
    n_done = 2'd0;
    n_com = 2'd0;
    ws_age = 6'd0;
    com_age = 6'd0;
    end_age = 6'd0;
  end

  // ------------------------------------------------------------ properties
  // All groups are checked unless PSEL selects some (the mutation check runs
  // one group at a time: A = operations, addresses and order, D = data).
  // With STICKY (mutation check only) a violation sets `bad`, asserted at the
  // last step, so one satisfiable query finds a mutant's counterexample.
`ifdef STICKY
`define CHK(c) if (!(c)) pv = 1'b1
`else
`define CHK(c) assert (c)
`endif
  reg pv;
`ifndef PSEL
`define P1A
`define P1D
`define P2A
`define P2D
`define P3
`define P5
`define P6
`endif
  always @(*) begin
    pv = 1'b0;
    // P1 writes
`ifdef P1A
    if (op_we) `CHK(ws_v && bus_addr == ws_a);
    `CHK(ck_wslot);
    `CHK(ws_age <= LW);
`endif
`ifdef P1D
    if (op_we) `CHK(bus_wdata == ws_d);
`endif
    // P2 reads
`ifdef P2A
    if (op_snap) `CHK(rd_live && nn_snap - n_com < 2'd2 && bus_addr == e_snap);
    if (op_commit) `CHK(rd_live && n_com != n_done && bus_addr == e_com);
    `CHK(ck_rsnap);
    `CHK(ck_rcom);
    `CHK(com_age <= LC);
`endif
`ifdef P2D
    `CHK(ck_rword);
`endif
`ifdef P3
    // P3 epochs
    if (op_cancel) `CHK(rd_live && rd_end && n_com == n_done);
    `CHK(end_age <= LX);
    `CHK(ck_addr_clear);
    if (!cs_n && nf == F_CMD) `CHK(!nws_v && !nrd_live);   // nothing pending once reselected
`endif
`ifdef P5
    // P5 one operation per cycle; the quad drivers on only in a counted quad
    // read's data, and on whenever the host samples one
    `CHK((bus_we + bus_snap + bus_commit + bus_cancel) <= 1);
    if (io_oe) `CHK(nok && nf == F_DATA && nrd && nquad);
    if (sck_rise && ok && r_f == F_DATA && r_rd && r_quad) `CHK(io_oe_q);
`endif
  end

  // ------------------------------------------------------------ RTL internals, raw events
  // RTL internal signals, bound by `connect -set` in hostport_ref.sby after
  // flattening. P6 uses `active`; the lemmas below use the rest.
  (* keep *) wire [2:0]  i_phase;
  (* keep *) wire [5:0]  i_cnt;
  (* keep *) wire [7:0]  i_cmd;
  (* keep *) wire        i_quad, i_rd, i_inc, i_wpar;
  (* keep *) wire [31:0] i_sh, i_osh, i_w_hold, i_buf0, i_buf1;
  (* keep *) wire [15:0] i_a_hold, i_snap_addr, i_com_addr;
  (* keep *) wire [1:0]  i_m_hold, i_outstanding;
  (* keep *) wire        i_a_tog, i_w_tog, i_r_tog, i_host_ready;
  (* keep *) wire [2:0]  i_a_s, i_w_s, i_r_s, i_cs_s;
  (* keep *) wire        i_active, i_c_rd, i_c_inc, i_fill, i_a_pend, i_w_pend, i_need_commit, i_need_snap;

  // raw SCK-side events (counted or not): the RTL toggles for each of these
  // while out of reset (an edge in the step reset is released still sees it)
  wire raw_addr  = sck_rise && !cs_n && rst_n && rst_q && r_f == F_ADDR && r_n + bps == 6'd16;
  wire raw_word  = sck_rise && !cs_n && rst_n && rst_q && r_f == F_DATA && r_n + bps == 6'd32;
  wire raw_wword = raw_word && !r_rd;
  wire raw_rword = raw_word && r_rd;
  // CDC bookkeeping for P6
  reg [3:0] dage0, dage1;   // steps since D0 / D1 were captured (sat 15)
  reg [4:0] ad_age;         // steps since the last address bit (sat 31)
  reg       act_q;
  wire      load = sck_fall && !cs_n && ok && r_f == F_DATA && r_rd && r_n == 6'd0;  // osh takes a word
  always @($global_clock) begin
    dage0 <= capture && !n_snap[0] ? 4'd1 : (dage0 == 4'd15 ? 4'd15 : dage0 + 4'd1);
    dage1 <= capture &&  n_snap[0] ? 4'd1 : (dage1 == 4'd15 ? 4'd15 : dage1 + 4'd1);
    ad_age <= raw_addr ? 5'd1 : (ad_age == 5'd31 ? 5'd31 : ad_age + 5'd1);
    act_q <= i_active;
  end

  reg pv6;
  always @(*) begin
    pv6 = 1'b0;
`ifdef P6
    // P6 clock-domain crossings keep a margin (in steps): the clk side takes
    // a write word >= 2 clk after its last bit; the SCK side loads a read
    // word >= 1 clk after its snapshot was captured; the clk side takes an
    // address >= 2 clk after its last bit
`ifdef STICKY
`define CHK6(c) if (!(c)) pv6 = 1'b1
`else
`define CHK6(c) assert (c)
`endif
    if (op_we) `CHK6(ws_age >= 6'd8);
    if (load) `CHK6(nn_snap - nn_done != 2'd0 && (nn_done[0] ? dage1 : dage0) >= 4'd4);
    if (i_active && !act_q) `CHK6(ad_age >= 5'd8);
`endif
  end
`ifdef STICKY
  reg bad = 1'b0;
  always @($global_clock) if (pv || pv6) bad <= 1'b1;
  always @(*) assert (!bad);
`endif

  // clk rises since the last raw event of each kind (sat 15; 15 after reset)
  reg [3:0] a_ck, w_ck, r_ck;
  reg       a_ok, w_ok, rw_ok;          // that event was counted
  reg       a_rd, a_inc;                // kind of the last address
  reg [15:0] a_adr;
  reg [2:0] cs_h;                       // shadow of the CS_n synchroniser
  reg       hr;                         // shadow of host_ready
  reg       wr_inc;
  wire [3:0] na_ck = !rst_n ? 4'd15 : raw_addr  ? 4'd0 : (clk_rise && a_ck != 4'd15 ? a_ck + 4'd1 : a_ck);
  wire [3:0] nw_ck = !rst_n ? 4'd15 : raw_wword ? 4'd0 : (clk_rise && w_ck != 4'd15 ? w_ck + 4'd1 : w_ck);
  wire [3:0] nr_ck = !rst_n ? 4'd15 : raw_rword ? 4'd0 : (clk_rise && r_ck != 4'd15 ? r_ck + 4'd1 : r_ck);
  wire       na_ok = !rst_n ? 1'b0 : raw_addr ? ok : a_ok;
  wire       nw_ok = !rst_n ? 1'b0 : raw_wword ? ok : w_ok;
  wire       nrw_ok = !rst_n ? 1'b0 : raw_rword ? ok : rw_ok;
  wire       na_rd = raw_addr ? r_rd : a_rd;
  wire       na_inc = raw_addr ? r_inc : a_inc;
  wire [15:0] na_adr = raw_addr ? nin[15:0] : a_adr;
  wire [2:0] ncs_h = !rst_n ? 3'd0 : clk_rise ? {cs_h[1:0], cs_q} : cs_h;
  wire       nhr = !rst_n ? 1'b0 : (clk_rise && &cs_h) ? 1'b1 : hr;
  wire       nwr_inc = (ev_addr && !r_rd) ? r_inc : wr_inc;
  always @($global_clock) begin
    a_ck <= na_ck; w_ck <= nw_ck; r_ck <= nr_ck;
    a_ok <= na_ok; w_ok <= nw_ok; rw_ok <= nrw_ok;
    a_rd <= na_rd; a_inc <= na_inc; a_adr <= na_adr;
    cs_h <= ncs_h; hr <= nhr; wr_inc <= nwr_inc;
  end
  initial begin
    a_ck = 4'd15; w_ck = 4'd15; r_ck = 4'd15;
    a_ok = 1'b0; w_ok = 1'b0; rw_ok = 1'b0;
    cs_h = 3'd0; hr = 1'b0;
  end

  function automatic [2:0] fmap(input [2:0] f);
    case (f)
      F_CMD: fmap = 3'd0;
      F_ADDR: fmap = 3'd1;
      F_DUMMY: fmap = 3'd2;
      F_DATA: fmap = 3'd3;
      default: fmap = 3'd4;
    endcase
  endfunction
  // a toggle event with c clk rises since: where it is in the 2-flop chain
  function automatic chain_ok(input t, input [2:0] s, input [3:0] c);
    case (c)
      4'd0:    chain_ok = t != s[0] && s[0] == s[1] && s[1] == s[2];
      4'd1:    chain_ok = t == s[0] && s[0] != s[1] && s[1] == s[2];
      4'd2:    chain_ok = t == s[0] && s[0] == s[1] && s[1] != s[2];
      default: chain_ok = t == s[0] && s[0] == s[1] && s[1] == s[2];
    endcase
  endfunction

  wire        sel   = !cs_n;
  wire [31:0] Dk    = nn_done[0] ? nD1 : nD0;   // data of the word being clocked out
  wire [5:0]  bpsn  = nquad ? 6'd4 : 6'd1;
  wire        rdat  = sel && nok && nf == F_DATA && nrd;   // a counted read's data phase
  reg         r_any;                         // a data word completed in this transaction
  wire        nr_any = cs_n ? 1'b0 : (raw_word || r_any);
  always @($global_clock) r_any <= nr_any;
  initial r_any = 1'b0;
  wire [1:0]  ahead = nn_snap - nn_done;     // captured words not yet clocked out
  wire [1:0]  owed  = nn_done - nn_com;      // clocked out, not committed
  wire [1:0]  held  = nn_snap - nn_com;      // captured, not committed
  wire [3:0]  rises = nquad ? {2'd0, nn[5:2]} : (nn > 6'd15 ? 4'd15 : nn[3:0]);  // SCK rises into this word
  // clk rises since CS_n rose at the end of the live read (sat 7)
  reg  [3:0]  x_ck;
  wire [3:0]  nx_ck = !(nrd_live && nrd_end) ? 4'd0 :
                      !(rd_live && rd_end) ? 4'd0 : (clk_rise && x_ck != 4'd7 ? x_ck + 4'd1 : x_ck);
  always @($global_clock) x_ck <= nx_ck;
  initial x_ck = 4'd0;

  // ------------------------------------------------------------ inductive lemmas
  // Not part of the specification: invariants that relate the reference to
  // the RTL's internal state, so that k-induction closes (prove task only).
  // They are asserted, and so proved, like the properties.
`ifdef LEMMAS
  always @(*) if (!init) begin
    // shadows
    assert (i_cs_s == ncs_h);
    assert (i_host_ready == nhr);
    assert (nf <= F_IGN);
    if (sel && nf != F_CMD) assert (ncs_h == 3'd0);   // CS_n low for >= 7 clk by then
    // the serial parser is in step with the reference decoder
    if (sel) begin
      assert (i_phase == fmap(nf));
      assert (i_cmd == ncmd);
      assert (i_sh == nin);
      assert (i_wpar == nwp);
      case (nf)
        F_CMD:   assert (i_cnt == nn && nn <= 6'd7 && !i_quad && !i_rd && !i_inc && !nquad && !nrd && !ninc);
        F_ADDR:  assert ((nquad ? i_cnt == {2'd0, nn[5:2]} && nn[1:0] == 2'd0 : i_cnt == nn) && nn < 6'd16);
        F_DUMMY: assert (i_cnt == nn && nn <= 6'd7 && nrd);
        F_DATA:  assert ((nquad ? i_cnt == {2'd0, nn[5:2]} && nn[1:0] == 2'd0 : i_cnt == nn) && nn < 6'd32);
        default: assert (nn == 6'd0);
      endcase
      if (nf == F_ADDR || nf == F_DUMMY || nf == F_DATA)
        assert (i_quad == nquad && i_rd == nrd && i_inc == ninc);
      if (!nf[2] && nf != F_CMD && nquad) assert (quad_en || !nok);
    end else
      assert (nf == F_CMD && nn == 6'd0 && !nok);
    if (!sel || nf != F_DATA) assert (!nr_any && !nwp);
    if (rdat) assert (nwp == nn_done[0]);
    // event chains
    assert (chain_ok(i_a_tog, i_a_s, na_ck));
    assert (chain_ok(i_w_tog, i_w_s, nw_ck));
    assert (chain_ok(i_r_tog, i_r_s, nr_ck));
    // host_ready: set in every counted transaction, clear in an ignored one
    // (and still clear while an ignored transaction's event is in flight)
    if ((na_ck <= 4'd3 && !na_ok) || (nw_ck <= 4'd3 && !nw_ok) || (nr_ck <= 4'd3 && !nrw_ok)) assert (!nhr);
    if (nok) assert (nhr);
    if (sel && !nok) assert (!nhr);
    if (!nhr) assert (!i_active && !i_a_pend && !i_w_pend && !i_need_commit && !i_need_snap &&
                      i_outstanding == 2'd0 && !bus_we && !bus_snap && !bus_commit && !bus_cancel);
    // pending flags: an event is taken by the first clk rise after its chain
    assert (i_a_pend == (na_ck == 4'd3 && na_ok));
    assert (i_w_pend == (nw_ck == 4'd3 && nw_ok));
    assert (i_need_commit == (nr_ck == 4'd3 && nrw_ok));
    if (na_ok && na_ck <= 4'd3) assert (i_a_hold == na_adr && i_m_hold == {na_rd, na_inc});
    // the last address event's transaction
    if (sel && (nf == F_CMD || nf == F_ADDR || nf == F_IGN))
      assert (!i_active && na_ck >= 4'd4 && !nrd_live && !nws_v);
    if (sel && (nf == F_DUMMY || nf == F_DATA)) assert (na_ok == nok && (!nok || na_rd == nrd && na_inc == ninc));
    if (na_ok && na_ck <= 4'd3) assert (!i_active);
    if (na_ok && na_rd) assert (nrd_live || na_ck >= 4'd5);
    if (nrd_live) assert (na_ok && na_rd && nrd_inc == na_inc);
    if (i_active) assert (na_ok && na_ck >= 4'd4 && i_c_rd == na_rd && i_c_inc == na_inc);
    if (i_active && i_c_rd) assert (nrd_live);
    // writes
    if (nws_v) assert (nw_ok && nw_ck <= 4'd3 && i_w_hold == nws_d && na_ok && !na_rd && i_active &&
                       nws_a == i_snap_addr && nwa == nws_a + {15'd0, nwr_inc});
    if (nw_ok && nw_ck <= 4'd3) assert (nws_v);
    if (i_active && !i_c_rd) assert (nwr_inc == i_c_inc && (nws_v || nwa == i_snap_addr));
    if (sel && nok && !nrd && (nf == F_DATA)) assert (i_active || (na_ck <= 4'd3 && na_ok));
    // reads
    if (nrd_live && na_ck >= 4'd4) begin
      assert (i_active && i_c_rd && i_snap_addr == ne_snap && i_com_addr == ne_com);
      assert (i_outstanding == held + {1'b0, bus_snap});
      assert (i_fill == nn_snap[0]);
      assert (i_need_snap == (i_outstanding != 2'd2));
      assert (ne_snap == ne_com + ((held + {1'b0, bus_snap}) & {2{nrd_inc}}));
    end
    if (nrd_live && na_ck <= 4'd3) assert (nn_snap == 2'd0 && nn_done == 2'd0 && nn_com == 2'd0 &&
                                           ne_snap == na_adr && ne_com == na_adr);
    if (nrd_live) begin
      assert (held <= 2'd2 && owed <= 2'd1 && ahead <= 2'd2);
      assert ((owed == 2'd1) == (nr_ck <= 4'd3 && nrw_ok));
      if (!nrd_end) assert (sel && nok);
    end
    if (!nrd_live) assert (!nrd_end);
    if (nr_ck <= 4'd3 && nrw_ok) assert (nrd_live);
    if (nrd_live && nrd_end) assert (end_age <= LX);
    assert (i_buf0 == nD0 && i_buf1 == nD1);
    // the output shifter holds the rest of the word being clocked out
    if (rdat) begin
      assert (nrd_live && !nrd_end);
      if (!sck)            assert (i_osh == Dk << nn && ahead != 2'd0);
      else if (nn != 6'd0) assert (i_osh == Dk << (nn - bpsn) && ahead != 2'd0);
      // (SCK high after a word's last rise: the next fall reloads osh)
      assert ((nout << (6'd32 - nn)) == (Dk & ~(32'hffffffff >> nn)));   // the bits the host has
      if (!nr_any) assert (na_ck >= 4'd8 && held == 2'd2 && owed == 2'd0 && nn_done == 2'd0);
      else         assert (nr_ck >= rises && (nr_ck >= 4'd6 ? ahead == 2'd2 : ahead == 2'd1));
    end
    if (sel && nok && nrd && nf == F_DUMMY) assert (nrd_live && na_ck >= nn[3:0] && nn_done == 2'd0 && nn_com == 2'd0);
    if (sel && nok && !nrd && nf == F_DATA && nr_any) assert (nw_ck >= rises && na_ck >= 4'd8);
    if (sel && nok && !nrd && nf == F_DATA && !nr_any) assert (na_ck >= rises);
    if (!i_active) assert (!i_need_snap && i_outstanding == 2'd0 && !i_need_commit);
    if (i_active && !i_c_rd) assert (!i_need_snap && i_outstanding == 2'd0 && !i_need_commit);
    // ages in steps are bounded by the clk rises counted since
    if (!ws_v) assert (ws_age == 6'd0);
    if (n_done == n_com) assert (com_age == 6'd0);
    if (!(rd_live && rd_end)) assert (end_age == 6'd0);
    if (!nrd_live) assert (owed == 2'd0);
    if (nws_v && ws_v) assert (ws_age <= {nw_ck, 2'b11});
    if (owed != 2'd0 && n_done != n_com && nrd_live) assert (com_age <= {nr_ck, 2'b11});
    if (nrd_live && nrd_end) begin
      assert (cs_n && nx_ck <= 4'd3 && na_ck >= nx_ck + 4'd1);
      if (rd_live && rd_end) assert (end_age <= {nx_ck, 2'b11});
      if (nx_ck >= 4'd1) assert (ncs_h[0]);
      if (nx_ck >= 4'd2) assert (ncs_h[1]);
      if (nx_ck >= 4'd3) assert (ncs_h[2]);
      if (owed != 2'd0) assert (nr_ck >= nx_ck + 4'd1);
    end
  end
`endif

  // ------------------------------------------------------------ covers
  // bookkeeping for the covers only
  reg [1:0] c_k;            // data words completed in this transaction (sat 3)
  reg       ws_quad, ws_inc;
  reg [1:0] ws_k;           // index of the pending write word in its transaction
  reg       rd_quad, rd_cut;
  reg       rst_mid;        // a reset hit a counted transaction after its command
  reg       ign_run;        // SCK ran in an ignored transaction after reset release
  always @($global_clock) begin
    c_k <= cs_n ? 2'd0 : (ev_word && c_k != 2'd3 ? c_k + 2'd1 : c_k);
    if (ev_word && !r_rd) begin
      ws_quad <= r_quad;
      ws_inc <= r_inc;
      ws_k <= c_k;
    end
    if (ev_addr && r_rd) begin
      rd_quad <= r_quad;
      rd_cut <= 1'b0;
    end
    if (cs_rise && rd_live && !rd_end && r_f == F_DATA && r_n != 6'd0) rd_cut <= 1'b1;
    if (!rst_n && r_ok && r_f != F_CMD) rst_mid <= 1'b1;
    if (sck_rise && !cs_n && !r_ok && rst_n) ign_run <= 1'b1;
  end
  initial begin
    c_k = 2'd0;
    rst_mid = 1'b0;
    ign_run = 1'b0;
  end
  always @(*) begin
`ifdef COVER1
    cover (op_we && !ws_quad && ws_inc);                 // 1-bit WRITE, a word written
    cover (op_commit && !rd_quad);                       // 1-bit READ, a word committed
`else
    cover (op_we && ws_quad && ws_inc && ws_k == 2'd1);  // quad WRITE, second word (at addr+1)
    cover (op_we && ws_quad && !ws_inc && ws_k == 2'd1); // quad WRITE_FIXED, second word (at addr)
    cover (op_commit && rd_quad && rd_inc && n_com == 2'd1);   // quad READ, second word committed
    cover (op_commit && rd_quad && !rd_inc && n_com == 2'd1);  // quad READ_FIXED, second word
    cover (op_cancel && rd_cut && n_com == 2'd1);        // CS rose mid-word after one complete word
    cover (cs_rise && r_ok && r_f == F_DATA && !r_rd && r_n != 6'd0 && c_k != 2'd0);  // write cut mid-word
    cover (!rst_n && rst_q && rd_live && n_snap - n_com == 2'd2);  // reset with two words snapped
    cover (!rst_n && rst_q && ws_v);                     // reset with a write word in flight
    cover (op_we && rst_mid);                            // writes again after a reset mid-transaction
    cover (op_commit && rst_mid);                        // reads again after a reset mid-transaction
    cover (op_we && ign_run);                            // ... and after SCK ran in an ignored one
    cover (io_oe);                                       // quad drivers on
    cover (op_we && ws_age == LW);                       // the latency bounds are reached
    cover (op_commit && com_age == LC);
    cover (op_cancel && end_age == LX);
`endif
  end
endmodule
