/*
 * Line unit (docs/isa.md §4.7): line coding (NRZ, NRZI, Manchester), bit
 * stuffing, bit timing and clock recovery, fed and drained by core 0 through
 * OUT LINE / IN LINE. The next-state logic follows the spec's pseudo-code (and
 * tools/pe/line.py) step by step; every decision uses start-of-cycle state.
 * SPDX-License-Identifier: Apache-2.0
 */
`default_nettype none

module pe_line (
    input  wire        clk,
    input  wire        rst_n,
    // host registers (0x080C LINECFG, 0x080D LINEDIV, 0x080E LINEPINS)
    input  wire        cfg_we, div_we, pins_we,
    input  wire [31:0] wdata,
    output wire [31:0] r_stat,
    // pins
    input  wire [31:0] pins_in,
    input  wire        rx_mid,        // the RX pin half a clock before pins_in[rxp] (pe_chip)
    output wire [4:0]  rx_pin,
    output wire [31:0] drv_mask,      // pins the transmitter drives this cycle
    output wire [31:0] drv_val,
    // core 0
    output wire        room,          // OUT LINE may queue an entry
    input  wire        push,
    input  wire [31:0] push_data,
    input  wire [5:0]  push_n,
    input  wire        push_msb,      // send push_data[n-1] first
    input  wire [5:0]  in_n,
    input  wire        in_shr,
    output reg         in_ok,
    output reg  [5:0]  in_k,
    output reg         in_t,
    output wire [31:0] in_val,
    input  wire        take           // IN LINE completed: consume in_k (and the end if in_t)
);
  // ---------------------------------------------------------------- configuration
  reg [14:0] cfg;
  reg [31:0] div;
  reg [4:0]  txp, rxp;
  wire       en     = cfg[0];
  wire [1:0] code   = cfg[2:1];
  wire [2:0] k      = cfg[5:3];
  wire       ones   = cfg[6];
  wire       diff   = cfg[7];
  wire [1:0] endm   = cfg[9:8];
  wire [2:0] endb   = cfg[12:10];
  wire       idle   = cfg[13];
  wire       rx_en  = cfg[14];
  wire       nrzi   = code == 2'd1;
  wire       manch  = code == 2'd2;
  wire [3:0] spb    = manch ? 4'd2 : 4'd1;
  wire [15:0] p     = div[31:16];

  // ---------------------------------------------------------------- state
  // TX: two queued entries (data, count, order) and the head's bit position
  reg [31:0] e_data0, e_data1;
  reg [5:0]  e_n0, e_n1, e_pos;
  reg        e_msb0, e_msb1;
  reg [1:0]  e_cnt;
  reg        tx_on, eop_idle, half, bit_q, level, txn;
  reg [3:0]  ending;
  reg [15:0] tcnt;
  reg [7:0]  tfacc;
  reg [2:0]  trun;
  reg        trunbit, trunv;
  // RX
  reg [31:0] rq;              // oldest bit at [0]
  reg [5:0]  rcount;
  reg        rx_on, rlast, rprev, end_pend, ovf;
  reg [15:0] rcnt;
  // Manchester: q = time from the expected next mid-bit edge, in 1/64
  // half-cycles; mn = mid-bit edges accepted (saturates at 32)
  reg signed [20:0] q;
  reg [5:0]  mn;
  reg [3:0]  quiet;
  reg [2:0]  rrun;
  reg        rrunbit, rrunv;

  // ---------------------------------------------------------------- core 0 view
  assign room = en && e_cnt != 2'd2;
  always @(*) begin
    in_ok = 1'b0;
    in_k = 6'd0;
    in_t = 1'b0;
    if (en) begin
      if (rcount >= in_n) begin
        in_ok = 1'b1;
        in_k = in_n;
      end else if (end_pend) begin
        in_ok = 1'b1;
        in_k = rcount;
        in_t = 1'b1;
      end
    end
  end
  function automatic [31:0] lowmask(input [5:0] n);
    lowmask = (n >= 6'd32) ? 32'hFFFF_FFFF : ((32'd1 << n) - 32'd1);
  endfunction
  function automatic [31:0] rev32(input [31:0] v);
    integer i;
    for (i = 0; i < 32; i = i + 1) rev32[i] = v[31 - i];
  endfunction
  // right shift: oldest bit at [0]; left: oldest at [k-1]
  assign in_val = in_shr ? (rq & lowmask(in_k)) : (rev32(rq) >> (6'd32 - in_k));

  // ---------------------------------------------------------------- pins
  wire [4:0] txn_pin = txp + 5'd1;
  wire       driving = en && tx_on;
  assign drv_mask = !driving ? 32'd0 : ((32'd1 << txp) | (diff ? (32'd1 << txn_pin) : 32'd0));
  assign drv_val  = !driving ? 32'd0 : (({31'd0, level} << txp) | (diff ? ({31'd0, txn} << txn_pin) : 32'd0));

  // ---------------------------------------------------------------- next state
  wire [5:0] head_idx = e_msb0 ? e_n0 - 6'd1 - e_pos : e_pos;   // 0..31
  wire       head_bit = e_data0[head_idx[4:0]];
  assign rx_pin = rxp;
  wire       rx  = pins_in[rxp];
  wire       rxn = pins_in[rxp + 5'd1];
  wire       trans = rx != rprev;
  // Manchester timing (1/64 half-cycle units, P <= 4095): a bit, the windows
  // (a quarter bit, 3/8 while locking) and the frame end (half a bit late)
  wire [11:0] p12 = p[11:0];
  wire signed [20:0] m_bit  = {2'd0, p12, 7'd0};
  wire signed [20:0] m_win  = {4'd0, p12, 5'd0};
  wire signed [20:0] m_wide = {4'd0, p12, 5'd0} + {5'd0, p12, 4'd0};
  wire signed [20:0] m_end  = {3'd0, p12, 6'd0};
  // where this cycle's edge fell: before the mid-cycle sample (1.5 half-cycles
  // ago) or after it (0.5)
  wire       edge1 = rx_mid != rprev;
  wire       edge2 = rx != rx_mid;
  wire signed [20:0] m_age = edge1 ? 21'sd96 : 21'sd32;
  wire signed [20:0] m_q = q + 21'sd128;                         // one clock later
  wire signed [20:0] m_e = m_q - m_age;                          // the edge, from its expected time
  wire       m_in = (edge1 || edge2) && m_e >= -(mn < 6'd32 ? m_wide : m_win);
  // NRZ/NRZI: the sample counter's load after a transition: P/2 - 1, one
  // clock less when the falling-edge sample already saw the edge (edge1).
  // That centres the sample 1.5-2.5 clocks after a pad edge instead of 2-3:
  // at USB full speed (P = 4 at 48 MHz) an edge-to-edge interval may shrink
  // by ~31 ns instead of ~21 ns (spec: 18.5 ns; docs/usb-full-speed.md).
  // Internal pins have rx_mid == rx, so they always take the shorter count.
  wire [15:0] r_load = {1'b0, p[15:1]} - (edge1 ? 16'd2 : 16'd1);
  wire [2:0] m_g = mn < 6'd6 ? 3'd1 : mn < 6'd12 ? 3'd2 : mn < 6'd18 ? 3'd3 : mn < 6'd24 ? 3'd4 : 3'd5;
  wire signed [20:0] m_r = (m_e + (21'sd1 <<< (m_g - 3'd1))) >>> m_g;   // loop correction, rounded

  reg        n_tx_on, n_eop, n_half, n_bit, n_level, n_txn, n_trunbit, n_trunv;
  reg [3:0]  n_ending;
  reg [15:0] n_tcnt;
  reg [7:0]  n_tfacc;
  reg [2:0]  n_trun;
  reg        pop, do_next, got, b;
  reg        n_rx_on, n_rlast, n_rrunbit, n_rrunv, new_v, new_b, new_end;
  reg [15:0] n_rcnt;
  reg signed [20:0] n_q;
  reg [5:0]  n_mn;
  reg [3:0]  n_quiet;
  reg [2:0]  n_rrun;
  reg [8:0]  fs;

  task automatic deliver(input bit_in);
    begin
      if (k != 3'd0 && n_rrun == k) begin                       // a stuff bit
        if (bit_in != (ones ? 1'b0 : !n_rrunbit)) begin
          n_rx_on = 1'b0;
          new_end = 1'b1;
        end else if (ones) n_rrun = 3'd0;
        else begin
          n_rrun = 3'd1;
          n_rrunbit = bit_in;
          n_rrunv = 1'b1;
        end
      end else begin
        if (ones) n_rrun = bit_in ? ((n_rrun == 3'd7) ? 3'd7 : n_rrun + 3'd1) : 3'd0;
        else begin
          n_rrun = (n_rrunv && bit_in == n_rrunbit) ? ((n_rrun == 3'd7) ? 3'd7 : n_rrun + 3'd1) : 3'd1;
          n_rrunbit = bit_in;
          n_rrunv = 1'b1;
        end
        new_v = 1'b1;
        new_b = bit_in;
      end
    end
  endtask

  always @(*) begin
    // ---- transmitter
    n_tx_on = tx_on; n_eop = eop_idle; n_half = half; n_bit = bit_q; n_level = level; n_txn = txn;
    n_ending = ending; n_tcnt = tcnt; n_tfacc = tfacc;
    n_trun = trun; n_trunbit = trunbit; n_trunv = trunv;
    pop = 1'b0; do_next = 1'b0; got = 1'b0; b = 1'b0;
    fs = {1'b0, tfacc} + {1'b0, div[7:0]};
    if (en) begin
      if (!tx_on) begin
        if (e_cnt != 2'd0) begin
          n_tx_on = 1'b1;
          n_level = idle;
          n_txn = !idle;
          n_trun = 3'd0;
          n_trunv = 1'b0;
          n_ending = 4'd0;
          n_eop = 1'b0;
          n_tfacc = fs[7:0];
          n_tcnt = {8'd0, div[15:8]} - 16'd1 + {15'd0, fs[8]};
          do_next = 1'b1;
        end
      end else if (tcnt != 16'd0) begin
        n_tcnt = tcnt - 16'd1;
      end else begin
        n_tfacc = fs[7:0];
        n_tcnt = {8'd0, div[15:8]} - 16'd1 + {15'd0, fs[8]};
        if (ending != 4'd0) begin
          n_ending = ending - 4'd1;
          if (ending == 4'd1) begin
            if (endm == 2'd1 && !eop_idle) begin                 // USB EOP: one IDLE bit after SE0
              n_eop = 1'b1;
              n_ending = spb;
              n_level = idle;
              n_txn = !idle;
            end else n_tx_on = 1'b0;
          end
        end else if (manch && !half) begin
          n_half = 1'b1;
          n_level = bit_q;
          n_txn = !bit_q;
        end else do_next = 1'b1;
      end
      if (do_next) begin
        if (k != 3'd0 && n_trun == k) begin                      // stuff bit
          b = ones ? 1'b0 : !n_trunbit;
          got = 1'b1;
          if (ones) n_trun = 3'd0;
          else begin
            n_trun = 3'd1;
            n_trunbit = b;
            n_trunv = 1'b1;
          end
        end else if (e_cnt != 2'd0) begin
          b = head_bit;
          pop = 1'b1;
          got = 1'b1;
          if (ones) n_trun = b ? ((n_trun == 3'd7) ? 3'd7 : n_trun + 3'd1) : 3'd0;
          else begin
            n_trun = (n_trunv && b == n_trunbit) ? ((n_trun == 3'd7) ? 3'd7 : n_trun + 3'd1) : 3'd1;
            n_trunbit = b;
            n_trunv = 1'b1;
          end
        end else if (endb == 3'd0 || endm == 2'd0) begin
          n_tx_on = 1'b0;                                        // nothing left: release
        end else begin
          n_ending = endb * spb;
          if (endm == 2'd1) begin
            n_level = 1'b0;
            n_txn = 1'b0;
          end else if (endm == 2'd2) begin
            n_level = 1'b1;
            n_txn = 1'b0;
          end
        end
        if (got) begin
          n_bit = b;
          if (nrzi) begin
            if (!b) n_level = !n_level;
          end else if (manch) begin
            n_half = 1'b0;
            n_level = !b;
          end else n_level = b;
          n_txn = !n_level;
        end
      end
    end

    // ---- receiver
    n_rx_on = rx_on; n_rlast = rlast; n_rcnt = rcnt; n_q = q; n_mn = mn; n_quiet = quiet;
    n_rrun = rrun; n_rrunbit = rrunbit; n_rrunv = rrunv;
    new_v = 1'b0; new_b = 1'b0; new_end = 1'b0;
    if (en && rx_en) begin
      if (!rx_on) begin
        if (trans) begin
          n_rx_on = 1'b1;
          n_rrun = 3'd0;
          n_rrunv = 1'b0;
          n_quiet = 4'd0;
          if (manch) begin
            n_q = m_age - m_bit;                                 // the first edge is a mid-bit edge
            n_mn = 6'd0;
            deliver(rx);
          end else begin
            n_rlast = rprev;
            n_rcnt = r_load;
          end
        end
      end else if (manch) begin
        if (m_in) begin
          n_q = m_q - m_bit - m_r;
          n_mn = (mn == 6'd32) ? 6'd32 : mn + 6'd1;
          deliver(rx);
        end else if (m_q >= m_end) begin
          n_rx_on = 1'b0;
          new_end = 1'b1;
        end else n_q = m_q;
      end else begin
        if (trans) begin
          n_rcnt = r_load;
          n_quiet = 4'd0;
        end else if (rcnt == 16'd0) begin
          n_rcnt = p - 16'd1;
          if (diff && !rx && !rxn) begin                         // SE0
            n_rx_on = 1'b0;
            new_end = 1'b1;
          end else begin
            n_quiet = (quiet == 4'd15) ? 4'd15 : quiet + 4'd1;
            if (n_quiet >= ((k != 3'd0) ? {1'b0, k} + 4'd2 : 4'd8)) begin
              n_rx_on = 1'b0;
              new_end = 1'b1;
            end else begin
              n_rlast = rx;
              deliver(nrzi ? (rx == rlast) : rx);
            end
          end
        end else n_rcnt = rcnt - 16'd1;
      end
    end
  end

  // ---------------------------------------------------------------- registers
  wire [5:0]  taken = (take && in_ok) ? in_k : 6'd0;
  wire [31:0] rq_shift = rq >> taken;
  wire [5:0]  rcount1 = rcount - taken;
  wire        head_done = pop && (e_pos + 6'd1 == e_n0);
  wire [1:0]  e_cnt1 = e_cnt - {1'b0, head_done};

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      cfg <= 15'd0;
      div <= 32'd0;
      txp <= 5'd0;
      rxp <= 5'd0;
    end else begin
      if (cfg_we) cfg <= wdata[14:0];
      if (div_we) div <= wdata;
      if (pins_we) begin
        txp <= wdata[4:0];
        rxp <= wdata[12:8];
      end
    end
  end

  // the unit's state: cleared at reset and whenever LINECFG is written with EN = 0
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        e_data0 <= 32'd0; e_data1 <= 32'd0; e_n0 <= 6'd0; e_n1 <= 6'd0; e_pos <= 6'd0;
        e_msb0 <= 1'b0; e_msb1 <= 1'b0; e_cnt <= 2'd0;
        tx_on <= 1'b0; eop_idle <= 1'b0; half <= 1'b0; bit_q <= 1'b0; level <= 1'b0; txn <= 1'b0;
        ending <= 4'd0; tcnt <= 16'd0; tfacc <= 8'd0; trun <= 3'd0; trunbit <= 1'b0; trunv <= 1'b0;
        rq <= 32'd0; rcount <= 6'd0; rx_on <= 1'b0; rlast <= 1'b0; rprev <= 1'b0;
        end_pend <= 1'b0; ovf <= 1'b0; rcnt <= 16'd0; q <= 21'sd0; mn <= 6'd0; quiet <= 4'd0;
        rrun <= 3'd0; rrunbit <= 1'b0; rrunv <= 1'b0;
    end else if (cfg_we && !wdata[0]) begin
        e_data0 <= 32'd0; e_data1 <= 32'd0; e_n0 <= 6'd0; e_n1 <= 6'd0; e_pos <= 6'd0;
        e_msb0 <= 1'b0; e_msb1 <= 1'b0; e_cnt <= 2'd0;
        tx_on <= 1'b0; eop_idle <= 1'b0; half <= 1'b0; bit_q <= 1'b0; level <= 1'b0; txn <= 1'b0;
        ending <= 4'd0; tcnt <= 16'd0; tfacc <= 8'd0; trun <= 3'd0; trunbit <= 1'b0; trunv <= 1'b0;
        rq <= 32'd0; rcount <= 6'd0; rx_on <= 1'b0; rlast <= 1'b0; rprev <= 1'b0;
        end_pend <= 1'b0; ovf <= 1'b0; rcnt <= 16'd0; q <= 21'sd0; mn <= 6'd0; quiet <= 4'd0;
        rrun <= 3'd0; rrunbit <= 1'b0; rrunv <= 1'b0;
    end else if (en) begin
      // transmitter
      tx_on <= n_tx_on; eop_idle <= n_eop; half <= n_half; bit_q <= n_bit; level <= n_level; txn <= n_txn;
      ending <= n_ending; tcnt <= n_tcnt; tfacc <= n_tfacc; trun <= n_trun; trunbit <= n_trunbit; trunv <= n_trunv;
      // TX queue: the unit's pop, then core 0's push behind what is left
      if (head_done) begin
        e_data0 <= e_data1; e_n0 <= e_n1; e_msb0 <= e_msb1; e_pos <= 6'd0;
      end else if (pop) e_pos <= e_pos + 6'd1;
      if (push) begin
        if (e_cnt1 == 2'd0) begin
          e_data0 <= push_data; e_n0 <= push_n; e_msb0 <= push_msb; e_pos <= 6'd0;
        end else begin
          e_data1 <= push_data; e_n1 <= push_n; e_msb1 <= push_msb;
        end
      end
      e_cnt <= e_cnt1 + {1'b0, push};
      // receiver
      rx_on <= n_rx_on; rlast <= n_rlast; rcnt <= n_rcnt; q <= n_q; mn <= n_mn; quiet <= n_quiet;
      rrun <= n_rrun; rrunbit <= n_rrunbit; rrunv <= n_rrunv;
      rprev <= rx;
      if (new_v && rcount < 6'd32) begin
        rq <= rq_shift | ({31'd0, new_b} << rcount1);
        rcount <= rcount1 + 6'd1;
      end else begin
        rq <= rq_shift;
        rcount <= rcount1;
      end
      if (new_v && rcount >= 6'd32) ovf <= 1'b1;
      if (new_end) end_pend <= 1'b1;
      else if (take && in_ok && in_t) end_pend <= 1'b0;
    end
  end

  assign r_stat = {12'd0, ovf, end_pend, rx_on, tx_on, 2'd0, rcount, 6'd0, e_cnt};
`ifdef FORMAL
  // Safety properties, checked by formal/line.sby with every input free
  // except core 0's contract (line_props.sv): pushes only with room, takes
  // only what IN LINE may take, counts 1..32. Any LINECFG/LINEDIV/LINEPINS
  // writes, any pins, any rx_mid.
  // Manchester phase bounds for any P <= 4095, even if LINEDIV changes in a
  // frame (docs/isa.md §4.7): q >= age - 128P - 48P (an accepted early edge),
  // q < 64P + 128 (half a bit late ends the frame); both fit 21 bits.
  localparam signed [22:0] Q_LO = -23'sd720720, Q_HI = 23'sd262208;
  wire signed [22:0] w_q = $signed({{2{q[20]}}, q}) + 23'sd128;
  wire signed [22:0] w_e = w_q - $signed({{2{m_age[20]}}, m_age});
  wire signed [22:0] w_r = (w_e + (23'sd1 <<< (m_g - 3'd1))) >>> m_g;
  wire signed [22:0] w_acc = w_q - $signed({{2{m_bit[20]}}, m_bit}) - w_r;
  always @(*) begin
    if (rst_n) begin
      assert (e_cnt <= 2'd2);
      assert (rcount <= 6'd32);
      assert (mn <= 6'd32);
      assert ($signed({{2{q[20]}}, q}) >= Q_LO && $signed({{2{q[20]}}, q}) <= Q_HI);
      // the 21-bit arithmetic never wraps: same results as 23-bit
      assert ($signed({{2{m_q[20]}}, m_q}) == w_q);
      assert ($signed({{2{m_e[20]}}, m_e}) == w_e);
      if (m_in) assert ($signed({{2{m_r[20]}}, m_r}) == w_r && w_acc >= Q_LO && w_acc <= Q_HI);
      // NRZ/NRZI: for any legal RX period (P >= 4) the post-transition load
      // doesn't wrap, so the next sample comes within half a bit
      if (p >= 16'd4) assert (r_load < {1'b0, p[15:1]});
    end
  end
`endif
endmodule
