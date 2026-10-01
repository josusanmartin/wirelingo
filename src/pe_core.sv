/*
 * protoemu core: one protocol-engine lane. Implements docs/isa.md §4–5.
 * SPDX-License-Identifier: Apache-2.0
 */
`default_nettype none

module pe_core #(
    parameter [1:0] IDX = 2'd0,
    parameter FIFO_LOG2 = 2      // FIFO depth 2**FIFO_LOG2 per direction
) (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        enable,       // CTRL.enable[IDX]
    input  wire        restart,      // CTRL.restart[IDX] written this cycle
    input  wire        fifo_clear,   // CTRL.fifo_clear[IDX] written this cycle

    // instruction fetch (registered read: instr == IMEM[pc])
    output wire [9:0]  fetch_addr,
    input  wire [31:0] instr,
    input  wire [7:0]  bank_owner,

    // pins
    input  wire [31:0] pins_in,
    input  wire [31:0] pin_out_cur,
    output wire [31:0] wr_out_en,
    output wire [31:0] wr_out_val,
    output wire [31:0] wr_oe_en,
    output wire [31:0] wr_oe_val,

    // global flags
    input  wire [7:0]  flags,
    output wire [7:0]  flag_set,
    output wire [7:0]  flag_clr,

    // host bus (this core's 32-word block)
    input  wire        bus_sel,
    input  wire [4:0]  bus_off,
    input  wire        bus_we,
    input  wire [31:0] bus_wdata,
    input  wire        bus_commit,
    input  wire        bus_snap,
    output reg  [31:0] bus_rdata
);

  // ---------------------------------------------------------------- opcodes
  localparam OP_JMP = 5'd0, OP_WAIT = 5'd1, OP_IN = 5'd2, OP_OUT = 5'd3, OP_PUSH = 5'd4;
  localparam OP_PULL = 5'd5, OP_MOV = 5'd6, OP_SET = 5'd7, OP_LDI = 5'd8, OP_ALU = 5'd9;
  localparam OP_PIN = 5'd10, OP_FLAG = 5'd11, OP_HALT = 5'd12;

  localparam L_PINS = 4'd4, L_PINDIRS = 4'd5, L_ISR = 4'd6, L_OSR = 4'd7;
  localparam L_STATUS = 4'd9, L_FLAGS = 4'd10, L_PC = 4'd11, L_CRC = 4'd12;

  localparam W_PIN1 = 3'd0, W_PIN0 = 3'd1, W_RISE = 3'd2, W_FALL = 3'd3, W_EDGE = 3'd4;
  localparam W_FLAG1 = 3'd5, W_FLAG0 = 3'd6;

  // ---------------------------------------------------------------- config
  reg [15:0] div_int;
  reg [7:0]  div_frac;
  reg [4:0]  out_base, set_base, side_base, in_base;
  reg [5:0]  out_count;
  reg [2:0]  set_count, side_count;
  reg        side_opt, side_pindir;
  reg        out_shr, in_shr, autopull, autopush, fjoin;
  reg [4:0]  pull_thresh, push_thresh;
  reg [9:0]  entry, wrap_bottom, wrap_top;
  reg [15:0] crc_poly;
  reg [3:0]  crc_w;
  reg        in_tap, out_tap;

  // ---------------------------------------------------------------- state
  reg [9:0]  pc;
  reg [15:0] r0, r1, r2, r3;
  reg [31:0] isr, osr;
  reg [5:0]  isr_cnt, osr_cnt;
  reg        z, c, t;
  reg [7:0]  dcnt;
  reg        in_wait, wprev, wevent;
  reg [15:0] cnt;
  reg [7:0]  facc;
  reg        halted, fault, stalled;
  reg [15:0] crc;

  // ---------------------------------------------------------------- FIFOs
  wire        host_push = bus_sel && bus_we && bus_off == 5'h10;
  // A FIFO read pops only if the word the host was given was a real entry: a
  // word that arrives during a read of an empty RXF stays for the next read.
  reg         snap_valid;
  wire        host_pop  = bus_sel && bus_commit && bus_off == 5'h10 && snap_valid;
  wire [31:0] txf_head, rxf_head, pq_head;
  wire [3:0]  txf_level, rxf_level;
  wire        pq_empty, rx_block;            // what PULL/autopull see; PUSH/autopush stall
  wire        rxf_empty = rxf_level == 4'd0;
  reg         core_txpop, core_rxpush;   // decoded; qualified by exec below
  reg  [31:0] core_rxdata;
  wire        exec;

  pe_fifos #(.LOG2(FIFO_LOG2)) u_fifos (
      .clk(clk), .rst_n(rst_n), .clear(fifo_clear), .fjoin(fjoin),
      .host_push(host_push), .host_data(bus_wdata), .host_pop(host_pop),
      .core_pop(exec && core_txpop), .core_push(exec && core_rxpush), .core_data(core_rxdata),
      .txf_head(txf_head), .rxf_head(rxf_head), .txf_level(txf_level), .rxf_level(rxf_level),
      .pq_head(pq_head), .pq_empty(pq_empty), .rx_block(rx_block));

  always @(posedge clk or negedge rst_n)
    if (!rst_n) snap_valid <= 1'b0;
    else if (bus_snap) snap_valid <= !rxf_empty;

  // ---------------------------------------------------------------- helpers
  function automatic [31:0] rotl32(input [31:0] v, input [4:0] s);
    reg [63:0] d;
    begin
      d = {v, v} << s;
      rotl32 = d[63:32];
    end
  endfunction

  function automatic [31:0] rotr32(input [31:0] v, input [4:0] s);
    reg [63:0] d;
    begin
      d = {v, v} >> s;
      rotr32 = d[31:0];
    end
  endfunction

  function automatic [31:0] lowmask(input [5:0] n);  // n in 0..32
    lowmask = (n >= 6'd32) ? 32'hFFFF_FFFF : ((32'd1 << n) - 32'd1);
  endfunction

  function automatic [31:0] rev32(input [31:0] v);
    integer k;
    for (k = 0; k < 32; k = k + 1) rev32[k] = v[31-k];
  endfunction

  // ---------------------------------------------------------------- decode
  wire [4:0]  op = instr[31:27];
  wire [7:0]  sd = instr[26:19];
  wire [18:0] x  = instr[18:0];

  function automatic [15:0] rsel(input [1:0] i, input [15:0] a0, input [15:0] a1,
                                 input [15:0] a2, input [15:0] a3);
    case (i)
      2'd0: rsel = a0;
      2'd1: rsel = a1;
      2'd2: rsel = a2;
      default: rsel = a3;
    endcase
  endfunction

  wire [15:0] status16 = {1'b0, fault, halted, stalled, 1'b0, t, c, z, rxf_level, txf_level};
  wire [5:0]  pull_th = (pull_thresh == 5'd0) ? 6'd32 : {1'b0, pull_thresh};
  wire [5:0]  push_th = (push_thresh == 5'd0) ? 6'd32 : {1'b0, push_thresh};

  // divider
  wire        tick = cnt == 16'd0;
  wire [8:0]  fsum = {1'b0, facc} + {1'b0, div_frac};
  wire [15:0] cnt_next = tick ? (div_int - 16'd1 + {15'd0, fsum[8]}) : (cnt - 16'd1);

  // WAIT edge tracker
  wire [2:0]  wkind = x[18:16];
  wire [4:0]  widx = x[15:11];
  wire        wpin = pins_in[widx];
  wire        qual = !x[6] || (pins_in[x[4:0]] == x[5]);
  wire        trans = (wkind == W_RISE) ? (!wprev && wpin) :
                      (wkind == W_FALL) ? (wprev && !wpin) : (wprev != wpin);
  wire        edge_done = wevent || (trans && qual);

  wire        active  = enable && !restart;
  wire        attempt = active && tick && !halted && !fault && dcnt == 8'd0;
  wire        bank_ok = bank_owner[{pc[9:8], 1'b0} +: 2] == IDX;
  assign      exec    = attempt && bank_ok;

  // side-set / delay split
  wire [2:0]  sc    = (side_count > 3'd4) ? 3'd4 : side_count;
  wire [3:0]  dbits = 4'd8 - {1'b0, sc} - {3'd0, side_opt};
  wire [7:0]  delay = sd & ((8'd1 << dbits) - 8'd1);
  wire        side_en = side_opt ? sd[7] : 1'b1;
  wire [7:0]  side_raw = sd >> dbits;
  wire [3:0]  side_v = side_raw[3:0] & ((4'd1 << sc) - 4'd1);
  wire        side_act = exec && sc != 3'd0 && side_en;
  wire [31:0] side_mask = rotl32({28'd0, (4'd1 << sc) - 4'd1}, side_base);
  wire [31:0] side_val  = rotl32({28'd0, side_v}, side_base);

  // sources
  wire [3:0]  src_code = (op == OP_MOV) ? x[12:9] : x[18:15];
  reg  [31:0] src_val;
  always @(*) begin
    case (src_code)
      4'd0, 4'd1, 4'd2, 4'd3: src_val = {16'd0, rsel(src_code[1:0], r0, r1, r2, r3)};
      L_PINS:   src_val = rotr32(pins_in, in_base);
      L_ISR:    src_val = isr;
      L_OSR:    src_val = osr;
      L_STATUS: src_val = {21'd0, status16[10:0]};
      L_FLAGS:  src_val = {24'd0, flags};
      L_PC:     src_val = {22'd0, pc};
      L_CRC:    src_val = {16'd0, crc};
      default:  src_val = 32'd0;
    endcase
  end

  // shift amounts (count field 0 means 32)
  wire [5:0]  n = (x[4:0] == 5'd0) ? 6'd32 : {1'b0, x[4:0]};

  // Shared 64-bit funnel shifter for IN and OUT (only one runs per tick):
  //   IN  right: {src_n, isr} >> n        -> low        = new ISR
  //   IN  left : {isr, 0} >> (32-n)       -> low | src_n = new ISR
  //   OUT left : {osr0, 0} >> (32-n)      -> low = new OSR, high = data
  //   OUT right: {0, osr0} >> n           -> low = new OSR, data = osr0 & mask(n)
  wire [31:0] nmask = lowmask(n);
  wire [31:0] in_bits = src_val & nmask;
  wire        out_refill = autopull && osr_cnt >= pull_th;
  wire [31:0] osr0 = out_refill ? pq_head : osr;
  wire        is_in = op == OP_IN;
  wire        shr = is_in ? in_shr : out_shr;
  wire [31:0] fs_hi = is_in ? (in_shr ? in_bits : isr) : (out_shr ? 32'd0 : osr0);
  wire [31:0] fs_lo = is_in ? (in_shr ? isr : 32'd0) : (out_shr ? osr0 : 32'd0);
  wire [5:0]  fs_amt = shr ? n : (6'd32 - n);
  wire [63:0] fs = {fs_hi, fs_lo} >> fs_amt;

  // IN
  wire [31:0] in_isr = in_shr ? fs[31:0] : (fs[31:0] | in_bits);
  wire [6:0]  in_cnt_sum = {1'b0, isr_cnt} + {1'b0, n};
  wire [5:0]  in_cnt = (in_cnt_sum > 7'd32) ? 6'd32 : in_cnt_sum[5:0];
  wire        in_autopush = autopush && in_cnt >= push_th;

  // OUT
  wire [5:0]  cnt0 = out_refill ? 6'd0 : osr_cnt;
  wire [31:0] out_data = out_shr ? (osr0 & nmask) : fs[63:32];
  wire [31:0] out_osr  = fs[31:0];
  wire [6:0]  out_cnt_sum = {1'b0, cnt0} + {1'b0, n};
  wire [5:0]  out_cnt = (out_cnt_sum > 7'd32) ? 6'd32 : out_cnt_sum[5:0];

  // MOV value
  wire [1:0]  mop = x[14:13];
  wire [31:0] mov_val = (mop == 2'd1) ? ~src_val : (mop == 2'd2) ? rev32(src_val) : src_val;

  // ALU
  wire [2:0]  aop = x[18:16];
  wire [15:0] alu_a = rsel(x[15:14], r0, r1, r2, r3);
  wire [15:0] alu_s = x[13] ? {3'd0, x[12:0]} : rsel(x[1:0], r0, r1, r2, r3);
  reg  [16:0] alu_res;
  always @(*) begin
    case (aop)
      3'd0:       alu_res = {1'b0, alu_a} + {1'b0, alu_s};
      3'd1, 3'd7: alu_res = {(alu_a < alu_s), alu_a - alu_s};
      3'd2:       alu_res = {1'b0, alu_a & alu_s};
      3'd3:       alu_res = {1'b0, alu_a | alu_s};
      3'd4:       alu_res = {1'b0, alu_a ^ alu_s};
      3'd5:       alu_res = {1'b0, alu_a << alu_s[3:0]};
      default:    alu_res = {1'b0, alu_a >> alu_s[3:0]};
    endcase
  end

  // JMP condition
  wire [3:0]  cond = x[18:15];
  wire [4:0]  jsel = x[14:10];
  wire [15:0] jr = rsel(jsel[1:0], r0, r1, r2, r3);
  reg         cond_ok;
  always @(*) begin
    case (cond)
      4'd0:  cond_ok = 1'b1;
      4'd1:  cond_ok = jr == 16'd0;
      4'd2:  cond_ok = jr != 16'd0;
      4'd3:  cond_ok = jr != 16'd0;
      4'd4:  cond_ok = pins_in[jsel];
      4'd5:  cond_ok = !pins_in[jsel];
      4'd6:  cond_ok = z;
      4'd7:  cond_ok = !z;
      4'd8:  cond_ok = c;
      4'd9:  cond_ok = !c;
      4'd10: cond_ok = t;
      4'd11: cond_ok = osr_cnt < pull_th;
      4'd12: cond_ok = !pq_empty;
      4'd13: cond_ok = rx_block;
      4'd14: cond_ok = flags[jsel[2:0]];
      default: cond_ok = !flags[jsel[2:0]];
    endcase
  end

  // WAIT
  wire        wflag = flags[widx[2:0]];
  reg         wbase;
  always @(*) begin
    case (wkind)
      W_PIN1:  wbase = wpin && qual;
      W_PIN0:  wbase = !wpin && qual;
      W_RISE, W_FALL, W_EDGE: wbase = in_wait && edge_done;
      W_FLAG1: wbase = wflag && qual;
      W_FLAG0: wbase = !wflag && qual;
      default: wbase = 1'b1;
    endcase
  end
  wire        w_to = x[10];
  wire [1:0]  w_treg = x[9:8];
  wire [15:0] w_tr = rsel(w_treg, r0, r1, r2, r3);
  wire        w_timeout = !wbase && w_to && w_tr == 16'd0;

  // CRC tap (docs/isa.md §4.5): 1-bit IN/OUT feed one bit; MOV/OUT to CRC set it
  wire [15:0] crc_mask = 16'hFFFF >> (4'd15 - crc_w);
  reg         crc_feed, crc_bit, crc_set;
  reg  [15:0] crc_set_val;
  wire        crc_fb = crc[crc_w] ^ crc_bit;
  wire [15:0] crc_fed = ((crc << 1) & crc_mask) ^ (crc_fb ? (crc_poly & crc_mask) : 16'd0);

  // ---------------------------------------------------------------- execute (combinational)
  reg         complete, jump, new_wait;
  reg  [9:0]  jump_target;
  reg         reg_we;
  reg  [1:0]  reg_sel;
  reg  [15:0] reg_val;
  reg         isr_we, osr_we;
  reg  [31:0] isr_nv, osr_nv;
  reg  [5:0]  isr_cnt_nv, osr_cnt_nv;
  reg         z_we, t_we, t_nv;
  // pin write unit (OUT/MOV/SET/PIN), one shared rotator
  reg         pw_out, pw_oe, pw_single, pw_vo, pw_ve;
  reg  [4:0]  pw_base;
  reg  [5:0]  pw_cnt;
  reg  [31:0] pw_data;
  reg  [7:0]  fset, fclr;

  wire [3:0]  dst = x[18:15];

  always @(*) begin
    complete = 1'b1;
    jump = 1'b0;
    jump_target = x[9:0];
    new_wait = 1'b0;
    reg_we = 1'b0;
    reg_sel = 2'd0;
    reg_val = 16'd0;
    isr_we = 1'b0;
    osr_we = 1'b0;
    isr_nv = 32'd0;
    osr_nv = 32'd0;
    isr_cnt_nv = 6'd0;
    osr_cnt_nv = 6'd0;
    z_we = 1'b0;
    t_we = 1'b0;
    t_nv = 1'b0;
    pw_out = 1'b0;
    pw_oe = 1'b0;
    pw_single = 1'b0;
    pw_vo = 1'b0;
    pw_ve = 1'b0;
    pw_base = out_base;
    pw_cnt = out_count;
    pw_data = (op == OP_MOV) ? mov_val : out_data;
    fset = 8'd0;
    fclr = 8'd0;
    core_txpop = 1'b0;
    core_rxpush = 1'b0;
    core_rxdata = isr;
    crc_feed = 1'b0;
    crc_bit = 1'b0;
    crc_set = 1'b0;
    crc_set_val = 16'd0;

    case (op)
      OP_JMP: begin
        jump = cond_ok;
        if (cond == 4'd3) begin
          reg_we = 1'b1;
          reg_sel = jsel[1:0];
          reg_val = jr - 16'd1;
        end
      end

      OP_WAIT: begin
        if (wbase) begin
          if (w_to) begin
            t_we = 1'b1;
            t_nv = 1'b0;
          end
          if (wkind == W_FLAG1 && x[7]) fclr = 8'd1 << widx[2:0];
        end else if (w_timeout) begin
          t_we = 1'b1;
          t_nv = 1'b1;
        end else begin
          complete = 1'b0;
          new_wait = 1'b1;
          if (w_to) begin
            reg_we = 1'b1;
            reg_sel = w_treg;
            reg_val = w_tr - 16'd1;
          end
        end
      end

      OP_IN: begin
        if (in_autopush) begin
          if (rx_block) complete = 1'b0;
          else begin
            core_rxpush = 1'b1;
            core_rxdata = in_isr;
            isr_we = 1'b1;
            isr_nv = 32'd0;
            isr_cnt_nv = 6'd0;
          end
        end else begin
          isr_we = 1'b1;
          isr_nv = in_isr;
          isr_cnt_nv = in_cnt;
        end
        if (in_tap && n == 6'd1 && !(in_autopush && rx_block)) begin
          crc_feed = 1'b1;
          crc_bit = in_bits[0];
        end
      end

      OP_OUT: begin
        if (out_refill && pq_empty) complete = 1'b0;
        else begin
          core_txpop = out_refill;
          osr_we = 1'b1;
          osr_nv = out_osr;
          osr_cnt_nv = out_cnt;
          if (out_tap && n == 6'd1) begin
            crc_feed = 1'b1;
            crc_bit = out_data[0];
          end
          case (dst)
            4'd0, 4'd1, 4'd2, 4'd3: begin
              reg_we = 1'b1;
              reg_sel = dst[1:0];
              reg_val = out_data[15:0];
            end
            L_PINS: pw_out = 1'b1;
            L_PINDIRS: pw_oe = 1'b1;
            L_ISR: begin
              isr_we = 1'b1;
              isr_nv = out_data;
              isr_cnt_nv = n;
            end
            L_PC: begin
              jump = 1'b1;
              jump_target = out_data[9:0];
            end
            L_CRC: begin
              crc_set = 1'b1;
              crc_set_val = out_data[15:0];
            end
            default: ;
          endcase
        end
      end

      OP_PUSH: begin
        if (x[18] && isr_cnt < push_th) ;
        else if (rx_block) begin
          if (x[17]) complete = 1'b0;
          else begin
            isr_we = 1'b1;
            isr_nv = 32'd0;
            isr_cnt_nv = 6'd0;
          end
        end else begin
          core_rxpush = 1'b1;
          core_rxdata = isr;
          isr_we = 1'b1;
          isr_nv = 32'd0;
          isr_cnt_nv = 6'd0;
        end
      end

      OP_PULL: begin
        if (x[18] && osr_cnt < pull_th) ;
        else if (pq_empty) begin
          if (x[17]) complete = 1'b0;
          else begin
            osr_we = 1'b1;
            osr_nv = {16'd0, r0};
            osr_cnt_nv = 6'd0;
          end
        end else begin
          core_txpop = 1'b1;
          osr_we = 1'b1;
          osr_nv = pq_head;
          osr_cnt_nv = 6'd0;
        end
      end

      OP_MOV: begin
        case (dst)
          4'd0, 4'd1, 4'd2, 4'd3: begin
            reg_we = 1'b1;
            reg_sel = dst[1:0];
            reg_val = mov_val[15:0];
          end
          L_PINS: pw_out = 1'b1;
          L_PINDIRS: pw_oe = 1'b1;
          L_ISR: begin
            isr_we = 1'b1;
            isr_nv = mov_val;
            isr_cnt_nv = 6'd0;
          end
          L_OSR: begin
            osr_we = 1'b1;
            osr_nv = mov_val;
            osr_cnt_nv = 6'd0;
          end
          L_PC: begin
            jump = 1'b1;
            jump_target = mov_val[9:0];
          end
          L_CRC: begin
            crc_set = 1'b1;
            crc_set_val = mov_val[15:0];
          end
          default: ;
        endcase
      end

      OP_SET: begin
        pw_out = !x[18];
        pw_oe = x[18];
        pw_base = set_base;
        pw_cnt = (set_count > 3'd5) ? 6'd5 : {3'd0, set_count};
        pw_data = {27'd0, x[4:0]};
      end

      OP_LDI: begin
        reg_we = 1'b1;
        reg_sel = x[17:16];
        reg_val = x[15:0];
      end

      OP_ALU: begin
        z_we = 1'b1;
        if (aop != 3'd7) begin
          reg_we = 1'b1;
          reg_sel = x[15:14];
          reg_val = alu_res[15:0];
        end
      end

      OP_PIN: begin
        pw_single = 1'b1;
        pw_base = x[4:0];
        pw_cnt = 6'd1;
        case (x[18:17])
          2'd0: begin pw_out = 1'b1; pw_oe = 1'b1; pw_vo = 1'b0; pw_ve = 1'b1; end
          2'd1: begin pw_out = 1'b1; pw_oe = 1'b1; pw_vo = 1'b1; pw_ve = 1'b1; end
          2'd2: begin pw_out = 1'b1; pw_vo = !pin_out_cur[x[4:0]]; end
          default: begin pw_oe = 1'b1; pw_ve = 1'b0; end
        endcase
      end

      OP_FLAG: begin
        if (x[18]) fclr = 8'd1 << x[2:0];
        else fset = 8'd1 << x[2:0];
      end

      OP_HALT: complete = 1'b0;

      default: ;  // reserved: NOP
    endcase
  end

  // ---------------------------------------------------------------- outputs
  wire [31:0] pw_mask = rotl32(lowmask(pw_cnt), pw_base);
  wire [31:0] pw_rot  = rotl32(pw_data, pw_base);
  wire [31:0] pw_val_out = pw_single ? {32{pw_vo}} : pw_rot;
  wire [31:0] pw_val_oe  = pw_single ? {32{pw_ve}} : pw_rot;
  wire side_to_out = side_act && !side_pindir;
  wire side_to_oe  = side_act && side_pindir;
  wire [31:0] m_out = (exec && pw_out) ? pw_mask : 32'd0;
  wire [31:0] m_oe  = (exec && pw_oe) ? pw_mask : 32'd0;
  wire [31:0] s_mask = side_act ? side_mask : 32'd0;

  assign wr_out_en  = m_out | (side_to_out ? s_mask : 32'd0);
  assign wr_out_val = side_to_out ? ((side_val & s_mask) | (pw_val_out & ~s_mask)) : pw_val_out;
  assign wr_oe_en   = m_oe | (side_to_oe ? s_mask : 32'd0);
  assign wr_oe_val  = side_to_oe ? ((side_val & s_mask) | (pw_val_oe & ~s_mask)) : pw_val_oe;

  assign flag_set = exec ? fset : 8'd0;
  assign flag_clr = exec ? fclr : 8'd0;

  wire        commit = exec && complete;
  wire        host_w = bus_sel && bus_we;
  wire        host_pc_w = host_w && bus_off == 5'h08;

  wire [9:0]  pc_seq = (pc == wrap_top) ? wrap_bottom : (pc + 10'd1);
  assign fetch_addr = restart ? entry :
                      host_pc_w ? bus_wdata[9:0] :
                      commit ? (jump ? jump_target : pc_seq) : pc;

  // ---------------------------------------------------------------- bus read
  always @(*) begin
    case (bus_off)
      5'h00: bus_rdata = {8'd0, div_int, div_frac};
      5'h01: bus_rdata = {5'd0, set_count, 3'd0, set_base, 2'd0, out_count, 3'd0, out_base};
      5'h02: bus_rdata = {11'd0, in_base, 2'd0, side_pindir, side_opt, 1'b0, side_count, 3'd0, side_base};
      5'h03: bus_rdata = {11'd0, push_thresh, 3'd0, pull_thresh, 3'd0, fjoin, autopush, autopull, in_shr, out_shr};
      5'h04: bus_rdata = {22'd0, entry};
      5'h05: bus_rdata = {6'd0, wrap_top, 6'd0, wrap_bottom};
      5'h06: bus_rdata = {10'd0, out_tap, in_tap, crc_w, crc_poly};
      5'h07: bus_rdata = {16'd0, crc};
      5'h08: bus_rdata = {22'd0, pc};
      5'h09: bus_rdata = {16'd0, status16};
      5'h0A: bus_rdata = {16'd0, r0};
      5'h0B: bus_rdata = {16'd0, r1};
      5'h0C: bus_rdata = {16'd0, r2};
      5'h0D: bus_rdata = {16'd0, r3};
      5'h0E: bus_rdata = isr;
      5'h0F: bus_rdata = osr;
      5'h10: bus_rdata = (!rxf_empty && !fjoin) ? rxf_head : 32'd0;
      default: bus_rdata = 32'd0;
    endcase
  end

  // ---------------------------------------------------------------- config registers
  // Written only by the host, so each register gets a clock gate rather than
  // a hold mux per bit.
  wire [6:0] cfg_gclk;
  genvar g;
  generate
    for (g = 0; g < 7; g = g + 1) begin : g_cfg_cg
      pe_cg u_cg (.clk(clk), .en(bus_sel && bus_we && bus_off == g), .gclk(cfg_gclk[g]));
    end
  endgenerate

  always @(posedge cfg_gclk[0] or negedge rst_n)
    if (!rst_n) {div_int, div_frac} <= {16'd1, 8'd0};
    else {div_int, div_frac} <= bus_wdata[23:0];

  always @(posedge cfg_gclk[1] or negedge rst_n)
    if (!rst_n) {out_base, out_count, set_base, set_count} <= 19'd0;
    else {out_base, out_count, set_base, set_count} <=
        {bus_wdata[4:0], bus_wdata[13:8], bus_wdata[20:16], bus_wdata[26:24]};

  always @(posedge cfg_gclk[2] or negedge rst_n)
    if (!rst_n) {side_base, side_count, side_opt, side_pindir, in_base} <= 15'd0;
    else {side_base, side_count, side_opt, side_pindir, in_base} <=
        {bus_wdata[4:0], bus_wdata[10:8], bus_wdata[12], bus_wdata[13], bus_wdata[20:16]};

  always @(posedge cfg_gclk[3] or negedge rst_n)
    if (!rst_n) {out_shr, in_shr, autopull, autopush, fjoin, pull_thresh, push_thresh} <= 15'd0;
    else {out_shr, in_shr, autopull, autopush, fjoin, pull_thresh, push_thresh} <=
        {bus_wdata[0], bus_wdata[1], bus_wdata[2], bus_wdata[3], bus_wdata[4], bus_wdata[12:8], bus_wdata[20:16]};

  always @(posedge cfg_gclk[4] or negedge rst_n)
    if (!rst_n) entry <= {IDX, 8'd0};
    else entry <= bus_wdata[9:0];

  always @(posedge cfg_gclk[5] or negedge rst_n)
    if (!rst_n) {wrap_bottom, wrap_top} <= {10'd0, 10'd1023};
    else {wrap_bottom, wrap_top} <= {bus_wdata[9:0], bus_wdata[25:16]};

  always @(posedge cfg_gclk[6] or negedge rst_n)
    if (!rst_n) {crc_poly, crc_w, in_tap, out_tap} <= 22'd0;
    else {crc_poly, crc_w, in_tap, out_tap} <= {bus_wdata[15:0], bus_wdata[19:16], bus_wdata[20], bus_wdata[21]};

  // ---------------------------------------------------------------- sequential
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      pc <= {IDX, 8'd0};
      r0 <= 16'd0;
      r1 <= 16'd0;
      r2 <= 16'd0;
      r3 <= 16'd0;
      isr <= 32'd0;
      osr <= 32'd0;
      isr_cnt <= 6'd0;
      osr_cnt <= 6'd32;
      z <= 1'b0;
      c <= 1'b0;
      t <= 1'b0;
      dcnt <= 8'd0;
      in_wait <= 1'b0;
      wprev <= 1'b0;
      wevent <= 1'b0;
      cnt <= 16'd0;
      facc <= 8'd0;
      halted <= 1'b0;
      fault <= 1'b0;
      stalled <= 1'b0;
      crc <= 16'd0;
    end else begin
      pc <= fetch_addr;

      if (restart) begin
        isr <= 32'd0;
        osr <= 32'd0;
        isr_cnt <= 6'd0;
        osr_cnt <= 6'd32;
        z <= 1'b0;
        c <= 1'b0;
        t <= 1'b0;
        dcnt <= 8'd0;
        in_wait <= 1'b0;
        wprev <= 1'b0;
        wevent <= 1'b0;
        cnt <= 16'd0;
        facc <= 8'd0;
        halted <= 1'b0;
        fault <= 1'b0;
        stalled <= 1'b0;
      end else if (active) begin
        cnt <= cnt_next;
        if (tick) facc <= fsum[7:0];

        if (in_wait) begin
          wevent <= edge_done;
          wprev <= wpin;
        end

        if (tick && !halted && !fault) begin
          if (dcnt != 8'd0) begin
            dcnt <= dcnt - 8'd1;
          end else if (!bank_ok) begin
            fault <= 1'b1;
            halted <= 1'b1;
            stalled <= 1'b0;
          end else begin
            // exec
            stalled <= !complete && op != OP_HALT;
            in_wait <= new_wait;
            if (new_wait && !in_wait) begin
              wprev <= wpin;
              wevent <= 1'b0;
            end
            if (op == OP_HALT) halted <= 1'b1;
            if (complete) dcnt <= delay;
            if (reg_we) begin
              case (reg_sel)
                2'd0: r0 <= reg_val;
                2'd1: r1 <= reg_val;
                2'd2: r2 <= reg_val;
                default: r3 <= reg_val;
              endcase
            end
            if (isr_we) begin
              isr <= isr_nv;
              isr_cnt <= isr_cnt_nv;
            end
            if (osr_we) begin
              osr <= osr_nv;
              osr_cnt <= osr_cnt_nv;
            end
            if (z_we) begin
              z <= alu_res[15:0] == 16'd0;
              c <= alu_res[16];
            end
            if (t_we) t <= t_nv;
            if (crc_set) crc <= crc_set_val & crc_mask;
            else if (crc_feed) crc <= crc_fed;
          end
        end
      end

      // host writes (win over core writes in the same cycle)
      if (host_w) begin
        case (bus_off)
          5'h08: begin dcnt <= 8'd0; in_wait <= 1'b0; end
          5'h0A: r0 <= bus_wdata[15:0];
          5'h0B: r1 <= bus_wdata[15:0];
          5'h0C: r2 <= bus_wdata[15:0];
          5'h0D: r3 <= bus_wdata[15:0];
          5'h07: crc <= bus_wdata[15:0];
          default: ;
        endcase
      end
    end
  end

`ifdef FORMAL
  // Safety properties, checked by formal/core.sby with unconstrained inputs
  // (any instruction stream, pins, flags and host traffic).
  reg f_past = 1'b0;
  always @(posedge clk) f_past <= 1'b1;

  always @(*) begin
    if (rst_n) begin
      assert (isr_cnt <= 6'd32);
      assert (osr_cnt <= 6'd32);
      assert (!fault || halted);
      // a disabled or restarting core has no side effects
      if (!active) begin
        assert (wr_out_en == 32'd0 && wr_oe_en == 32'd0);
        assert (flag_set == 8'd0 && flag_clr == 8'd0);
        assert (!(exec && core_txpop) && !(exec && core_rxpush));
      end
      // side effects only on an executing tick
      if (!exec) assert (wr_out_en == 32'd0 && wr_oe_en == 32'd0 && flag_set == 8'd0 && flag_clr == 8'd0);
      if (exec) assert (tick && !halted && dcnt == 8'd0 && bank_ok);
    end
  end

  // previous-cycle copies (explicit, so the checks also hold under multiclock)
  reg        p_rst_n, p_halted, p_fault, p_restart, p_host_pc_w, p_commit, p_enable, p_host_w;
  reg [9:0]  p_pc;
  reg [31:0] p_isr, p_osr;
  reg [7:0]  p_dcnt;
  reg [15:0] p_cnt, p_r0, p_r1, p_r2, p_r3;
  reg        p_in_wait;
  always @(posedge clk) begin
    p_rst_n <= rst_n; p_halted <= halted; p_fault <= fault; p_restart <= restart;
    p_host_pc_w <= host_pc_w; p_commit <= commit; p_enable <= enable; p_host_w <= host_w;
    p_pc <= pc; p_isr <= isr; p_osr <= osr; p_dcnt <= dcnt; p_cnt <= cnt; p_in_wait <= in_wait;
    p_r0 <= r0; p_r1 <= r1; p_r2 <= r2; p_r3 <= r3;
  end

  always @(*) begin
    if (rst_n && f_past && p_rst_n) begin
      // halted/faulted cores stay that way until restarted
      if (p_halted && !p_restart) assert (halted);
      if (p_fault && !p_restart) assert (fault);
      // the PC only moves on a completed instruction, a restart or a host write
      if (!p_restart && !p_host_pc_w && !p_commit) assert (pc == p_pc);
      // a disabled core's execution state is frozen (host writes aside)
      if (!p_enable && !p_restart && !p_host_w) begin
        assert (pc == p_pc && isr == p_isr && osr == p_osr);
        assert (dcnt == p_dcnt && cnt == p_cnt && in_wait == p_in_wait);
        assert (r0 == p_r0 && r1 == p_r1 && r2 == p_r2 && r3 == p_r3);
      end
    end
  end
`endif
endmodule
