/*
 * STUDY PROTOTYPE, NOT A DESIGN CHANGE (docs/8x4-study.md): the v3 study's
 * prototype (src/pe_core.sv with its candidates under `ifdef V3_*) plus the
 * 8x4 study's under `ifdef V8_*, written only to count cells with
 * syn/8x4-study/synth_chip.sh. Not simulated, not verified, not for tapeout.
 *
 * protoemu core: one protocol-engine lane. Implements docs/isa.md §4–5.
 * SPDX-License-Identifier: Apache-2.0
 */
`default_nettype none

`ifdef V8_PAR
`define V8_OP14
`endif
`ifdef V8_XCH
`define V8_OP14
`endif
`ifdef V8_DRAM
`define V8_OP14
`endif

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
    input  wire        bus_cancel,
    output reg  [31:0] bus_rdata,
    // line unit (core 0 only, docs/isa.md §4.7)
    input  wire        line_room,
    input  wire        line_ok,
    input  wire [5:0]  line_k,
    input  wire        line_t,
    input  wire [31:0] line_val,
    output wire        line_push,
    output wire [31:0] line_push_data,
    output wire [5:0]  line_push_n,
    output wire        line_push_msb,
    output wire [5:0]  line_in_n,
    output wire        line_in_shr,
    output wire        line_take
);

  // ---------------------------------------------------------------- opcodes
  localparam OP_JMP = 5'd0, OP_WAIT = 5'd1, OP_IN = 5'd2, OP_OUT = 5'd3, OP_PUSH = 5'd4;
  localparam OP_PULL = 5'd5, OP_MOV = 5'd6, OP_SET = 5'd7, OP_LDI = 5'd8, OP_ALU = 5'd9;
  localparam OP_PIN = 5'd10, OP_FLAG = 5'd11, OP_HALT = 5'd12, OP_CALL = 5'd13;

  localparam L_PINS = 4'd4, L_PINDIRS = 4'd5, L_ISR = 4'd6, L_OSR = 4'd7;
  localparam L_STATUS = 4'd9, L_FLAGS = 4'd10, L_PC = 4'd11, L_CRC = 4'd12, L_LINE = 4'd13, L_LR = 4'd14;

  localparam W_PIN1 = 3'd0, W_PIN0 = 3'd1, W_RISE = 3'd2, W_FALL = 3'd3, W_EDGE = 3'd4;
  localparam W_FLAG1 = 3'd5, W_FLAG0 = 3'd6, W_DIFF = 3'd7;

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
`ifdef V3_CRC32
  reg [31:0] crc;
  reg        crc_w32;
`ifdef V8_CRCW
  reg [15:0] crc_poly_hi;
`endif
`else
  reg [15:0] crc;
`endif
`ifdef V3_MULTI
  reg        crc_multi;
`endif
  reg [9:0]  lr;
`ifdef V3_LR2
  reg [9:0]  lr2;
`endif
`ifdef V3_TBL
  reg        tbl_pend;
  reg [2:0]  tbl_dst;        // 0-3: R0-R3, 6: ISR, 7: OSR (others: discard)
`endif                 // link register: CALL's return address (docs/isa.md §5.2)

`ifdef V8_TICK
  reg [15:0] tcnt;             // free-running tick counter, MOV source 15
`endif
`ifdef V8_XCH
  reg [15:0] s0, s1, s2, s3;   // shadow registers: XCH swaps R[n] and S[n]
`endif
`ifdef V8_DRAM
  reg [15:0] dram [0:15];      // 16x16 data RAM in flops
`endif
`ifdef V8_TXCLR
  reg        txclr_q;          // PULL CLEAR executed last cycle: drop the FIFOs
  wire       fifo_clear_any = fifo_clear || txclr_q;
`else
  wire       fifo_clear_any = fifo_clear;
`endif
  // ---------------------------------------------------------------- FIFOs
  wire        host_push = bus_sel && bus_we && bus_off == 5'h10;
  // Host FIFO reads (docs/isa.md §6.1): a snapshot reserves the next unread
  // RXF entry (head, or head+1 behind one reservation); a commit pops the
  // oldest reservation if it was a real entry; a cancel (transaction end) or a
  // FIFO clear drops them. So a word arriving during a read of an empty RXF
  // stays for the next read, and an aborted read loses nothing.
  wire        fifo_sel  = bus_sel && bus_off == 5'h10;
  reg  [1:0]  res_n;                         // reservations, 0..2
  reg  [1:0]  res_v;                         // [0] oldest: was a real entry
  wire [1:0]  res_real  = (res_n != 2'd0 && res_v[0]) + (res_n == 2'd2 && res_v[1]);
  wire        host_pop  = fifo_sel && bus_commit && res_n != 2'd0 && res_v[0];
  wire [31:0] txf_head, rxf_head, rxf_head2, pq_head;
  wire [3:0]  txf_level, rxf_level;
  wire        pq_empty, rx_block;            // what PULL/autopull see; PUSH/autopush stall
  reg         core_txpop, core_rxpush;   // decoded; qualified by exec below
  reg         line_push_d, line_take_d;  // ditto, for the line unit
  reg  [31:0] core_rxdata;
  wire        exec;

  pe_fifos #(.LOG2(FIFO_LOG2)) u_fifos (
      .clk(clk), .rst_n(rst_n), .clear(fifo_clear_any), .fjoin(fjoin),
      .host_push(host_push), .host_data(bus_wdata), .host_pop(host_pop),
      .core_pop(exec && core_txpop), .core_push(exec && core_rxpush), .core_data(core_rxdata),
      .txf_head(txf_head), .rxf_head(rxf_head), .rxf_head2(rxf_head2), .txf_level(txf_level), .rxf_level(rxf_level),
      .pq_head(pq_head), .pq_empty(pq_empty), .rx_block(rx_block));

  wire        rd_real   = !fjoin && res_real != 2'd2 && rxf_level > {2'd0, res_real};
  wire [31:0] rd_word   = res_real == 2'd0 ? rxf_head : rxf_head2;

  always @(posedge clk or negedge rst_n)
    if (!rst_n) begin
      res_n <= 2'd0;
      res_v <= 2'd0;
    end else if (bus_cancel || fifo_clear_any) begin
      res_n <= 2'd0;
    end else if (fifo_sel && bus_commit && res_n != 2'd0) begin
      res_n <= res_n - 2'd1;
      res_v <= {1'b0, res_v[1]};
    end else if (fifo_sel && bus_snap && res_n != 2'd2) begin
      res_n <= res_n + 2'd1;
      if (res_n == 2'd0) res_v[0] <= rd_real;
      else res_v[1] <= rd_real;
    end

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

  // pins from IN_BASE: the PINS source, and what WAIT kind 7 watches
  wire [31:0] pins_rot = rotr32(pins_in, in_base);

  // WAIT edge tracker
  wire [2:0]  wkind = x[18:16];
  wire [4:0]  widx = x[15:11];
  wire        wpin = pins_in[widx];
  wire        qual = !x[6] || (pins_in[x[4:0]] == x[5]);
  wire        trans = (wkind == W_RISE) ? (!wprev && wpin) :
                      (wkind == W_FALL) ? (wprev && !wpin) : (wprev != wpin);
  // kind 7 (docs/isa.md §5.2): the masked pins differ from R[x[15:14]]; it
  // shares the sticky event bit, so a difference between ticks is kept. The
  // reference field is the ALU's rd field, so it shares that selector
  // (core_route: about 1 % less wire than a selector of its own)
  wire [15:0] wref = rsel(x[15:14], r0, r1, r2, r3);
  wire        wdiff = |((pins_rot[7:0] ^ wref[7:0]) & x[7:0]);
  wire        edge_done = wevent || (wkind == W_DIFF ? wdiff : (trans && qual));

  wire        active  = enable && !restart;
  wire        attempt = active && tick && !halted && !fault && dcnt == 8'd0;
  wire        bank_ok = pc[9:8] == IDX;          // a core runs from its own bank (§3)
`ifdef V3_TBL
  assign      exec    = attempt && bank_ok && !tbl_pend;   // the table word is data, not an instruction
`else
  assign      exec    = attempt && bank_ok;
`endif

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
      L_PINS:   src_val = pins_rot;
      L_ISR:    src_val = isr;
      L_OSR:    src_val = osr;
      L_STATUS: src_val = {21'd0, status16[10:0]};
      L_FLAGS:  src_val = {24'd0, flags};
      L_PC:     src_val = {22'd0, pc};
`ifdef V3_CRC32
      L_CRC:    src_val = crc;
`else
      L_CRC:    src_val = {16'd0, crc};
`endif
      L_LR:     src_val = {22'd0, lr};
`ifdef V8_TICK
      4'd15:    src_val = {16'd0, tcnt};
`endif
      default:  src_val = 32'd0;
    endcase
  end

  // shift amounts (count field 0 means 32)
`ifdef V8_SHV
  // IN/OUT [14]: the count is R[x[6:5]][4:0] (0 = 32)
  wire [15:0] n_reg = rsel(x[6:5], r0, r1, r2, r3);
  wire [4:0]  n_raw = ((op == OP_IN || op == OP_OUT) && x[14]) ? n_reg[4:0] : x[4:0];
  wire [5:0]  n = (n_raw == 5'd0) ? 6'd32 : {1'b0, n_raw};
`else
  wire [5:0]  n = (x[4:0] == 5'd0) ? 6'd32 : {1'b0, x[4:0]};
`endif
  // IN/OUT LINE (core 0): IN shifts by the k bits the line unit gives
`ifdef V8_LINE1
  wire        at_line = (IDX == 2'd0 || IDX == 2'd1) && x[18:15] == L_LINE;   // a line unit for core 1 too
`else
  wire        at_line = IDX == 2'd0 && x[18:15] == L_LINE;
`endif
  wire        in_line = op == OP_IN && at_line;
  wire [5:0]  n_in = in_line ? line_k : n;

  // Shared 64-bit funnel shifter for IN and OUT (only one runs per tick):
  //   IN  right: {src_n, isr} >> n        -> low        = new ISR
  //   IN  left : {isr, 0} >> (32-n)       -> low | src_n = new ISR
  //   OUT left : {osr0, 0} >> (32-n)      -> low = new OSR, high = data
  //   OUT right: {0, osr0} >> n           -> low = new OSR, data = osr0 & mask(n)
  wire [31:0] nmask = lowmask(n);
  wire [31:0] in_bits = in_line ? line_val : (src_val & nmask);
  wire        out_refill = autopull && osr_cnt >= pull_th;
  wire [31:0] osr0 = out_refill ? pq_head : osr;
  wire        is_in = op == OP_IN;
  wire        shr = is_in ? in_shr : out_shr;
  wire [31:0] fs_hi = is_in ? (in_shr ? in_bits : isr) : (out_shr ? 32'd0 : osr0);
  wire [31:0] fs_lo = is_in ? (in_shr ? isr : 32'd0) : (out_shr ? osr0 : 32'd0);
  wire [5:0]  n_sh = is_in ? n_in : n;
  wire [5:0]  fs_amt = shr ? n_sh : (6'd32 - n_sh);
  wire [63:0] fs = {fs_hi, fs_lo} >> fs_amt;

  // IN
  wire [31:0] in_isr = in_shr ? fs[31:0] : (fs[31:0] | in_bits);
  wire [6:0]  in_cnt_sum = {1'b0, isr_cnt} + {1'b0, n_in};
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
`ifdef V3_CMPW
`ifdef V3_CMPW_SPLIT
  // layout B: rd at the ALU's [15:14] (shared selector), imm16 = {x[17:16], x[13:0]}
  wire        is_cmpw = op == OP_LDI && x[18];
  wire [2:0]  aop = is_cmpw ? 3'd7 : x[18:16];
  wire [15:0] alu_a = rsel(x[15:14], r0, r1, r2, r3);
  wire [15:0] alu_s = is_cmpw ? {x[17:16], x[13:0]} : x[13] ? {3'd0, x[12:0]} : rsel(x[1:0], r0, r1, r2, r3);
`else
  // layout A: LDI's layout, rd at [17:16], imm16 at [15:0]
  wire        is_cmpw = op == OP_LDI && x[18];
  wire [2:0]  aop = is_cmpw ? 3'd7 : x[18:16];
  wire [15:0] alu_a = rsel(is_cmpw ? x[17:16] : x[15:14], r0, r1, r2, r3);
  wire [15:0] alu_s = is_cmpw ? x[15:0] : x[13] ? {3'd0, x[12:0]} : rsel(x[1:0], r0, r1, r2, r3);
`endif
`else
  wire [2:0]  aop = x[18:16];
  wire [15:0] alu_a = rsel(x[15:14], r0, r1, r2, r3);
  wire [15:0] alu_s = x[13] ? {3'd0, x[12:0]} : rsel(x[1:0], r0, r1, r2, r3);
`endif
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
`ifdef V3_NT
      4'd0:  cond_ok = jsel != 5'd1 || !t;        // sel 1: `!t`
`else
      4'd0:  cond_ok = 1'b1;
`endif
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
      default: wbase = in_wait ? edge_done : wdiff;     // W_DIFF: also on the first attempt
    endcase
  end
  wire        w_to = x[10];
  wire [1:0]  w_treg = x[9:8];
  wire [15:0] w_tr = rsel(w_treg, r0, r1, r2, r3);
  wire        w_timeout = !wbase && w_to && w_tr == 16'd0;

  // CRC tap (docs/isa.md §4.5): 1-bit IN/OUT feed one bit; MOV/OUT to CRC set it
`ifdef V3_CRC32
`ifdef V8_CRCW
  // 8x4 study: any width 1..32 ({W32, W} = width - 1) and any 32-bit polynomial
  // (POLY_HI is a 16-bit host-written configuration register)
  wire [4:0]  crc_w5 = {crc_w32, crc_w};
  wire [31:0] crc_mask = 32'hFFFF_FFFF >> (5'd31 - crc_w5);
  wire [31:0] poly_full = {crc_poly_hi, crc_poly};
  reg         crc_feed, crc_bit, crc_set;
  reg  [31:0] crc_set_val;
  function automatic [31:0] crc_step(input [31:0] cv, input b);
    crc_step = ((cv << 1) & crc_mask) ^ ((cv[crc_w5] ^ b) ? (poly_full & crc_mask) : 32'd0);
  endfunction
`else
  // CRC-32: W32 widens the register, POLY's top half fixed at 0x04C1
  wire [31:0] crc_mask = crc_w32 ? 32'hFFFF_FFFF : {16'd0, 16'hFFFF >> (4'd15 - crc_w)};
  wire [31:0] poly_full = crc_w32 ? {16'h04C1, crc_poly} : {16'd0, crc_poly};
  reg         crc_feed, crc_bit, crc_set;
  reg  [31:0] crc_set_val;
  wire        crc_top = crc_w32 ? crc[31] : crc[crc_w];
  function automatic [31:0] crc_step(input [31:0] cv, input b);
    crc_step = ((cv << 1) & crc_mask) ^ (((crc_w32 ? cv[31] : cv[crc_w]) ^ b) ? (poly_full & crc_mask) : 32'd0);
  endfunction
`endif
  wire [31:0] crc_fed1 = crc_step(crc, crc_bit);
`else
  wire [15:0] crc_mask = 16'hFFFF >> (4'd15 - crc_w);
  reg         crc_feed, crc_bit, crc_set;
  reg  [15:0] crc_set_val;
  function automatic [15:0] crc_step(input [15:0] cv, input b);
    crc_step = ((cv << 1) & crc_mask) ^ ((cv[crc_w] ^ b) ? (crc_poly & crc_mask) : 16'd0);
  endfunction
  wire [15:0] crc_fed1 = crc_step(crc, crc_bit);
`endif
`ifdef V3_MULTI
  // multi-bit tap: up to 8 bits per IN/OUT, in time order (shift right: bit 0 first)
  reg         crc_mfeed;
  reg  [7:0]  crc_mbits;      // in time order: [0] first
  reg  [3:0]  crc_mn;         // 1..8
`ifdef V3_CRC32
  reg  [31:0] crc_fed;
`else
  reg  [15:0] crc_fed;
`endif
  integer     mi;
  always @(*) begin
    crc_fed = crc;
    if (crc_mfeed) begin
      for (mi = 0; mi < 8; mi = mi + 1)
        if (mi < crc_mn) crc_fed = crc_step(crc_fed, crc_mbits[mi]);
    end else crc_fed = crc_fed1;
  end
  function automatic [7:0] rev8(input [7:0] v);
    integer k;
    for (k = 0; k < 8; k = k + 1) rev8[k] = v[7-k];
  endfunction
`else
  wire        crc_mfeed = 1'b0;
`ifdef V3_CRC32
  wire [31:0] crc_fed = crc_fed1;
`else
  wire [15:0] crc_fed = crc_fed1;
`endif
`endif

  // the PC after a completed instruction that doesn't jump (also CALL's link)
  wire [9:0]  pc_seq = (pc == wrap_top) ? wrap_bottom : (pc + 10'd1);

  // ---------------------------------------------------------------- execute (combinational)
  reg         complete, jump, new_wait;
  reg         lr_we;
  reg  [9:0]  lr_nv;
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
  reg         par_we, xch_we, dram_we, core_txclr;

  wire [3:0]  dst = x[18:15];

  always @(*) begin
    complete = 1'b1;
    par_we = 1'b0;
    xch_we = 1'b0;
    dram_we = 1'b0;
    core_txclr = 1'b0;
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
    line_push_d = 1'b0;
    line_take_d = 1'b0;
    core_rxdata = isr;
    crc_feed = 1'b0;
    crc_bit = 1'b0;
    crc_set = 1'b0;
`ifdef V3_CRC32
    crc_set_val = 32'd0;
`else
    crc_set_val = 16'd0;
`endif
`ifdef V3_MULTI
    crc_mfeed = 1'b0;
    crc_mbits = 8'd0;
    crc_mn = 4'd0;
`endif
    lr_we = 1'b0;
    lr_nv = pc_seq;

    case (op)
      OP_JMP, OP_CALL: begin
        jump = cond_ok;
        lr_we = op == OP_CALL && cond_ok;           // a taken CALL links
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
        if (in_line && !line_ok) complete = 1'b0;               // no line bits yet
        else if (in_autopush) begin
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
        if (in_line && line_ok && !(in_autopush && rx_block)) begin
          line_take_d = 1'b1;
          t_we = 1'b1;
          t_nv = line_t;
        end
        if (in_tap && n == 6'd1 && n_in == 6'd1 && !(in_line && !line_ok) && !(in_autopush && rx_block)) begin
          crc_feed = 1'b1;
          crc_bit = in_bits[0];
        end
`ifdef V3_MULTI
        else if (in_tap && crc_multi && n_in <= 6'd8 && n_in != 6'd0 && !(in_line && !line_ok) && !(in_autopush && rx_block)) begin
          crc_feed = 1'b1;
          crc_mfeed = 1'b1;
          crc_mn = n_in[3:0];
          // time order: right shift, bit 0 first; left shift, bit n-1 first
          crc_mbits = in_shr ? in_bits[7:0] : (rev8(in_bits[7:0]) >> (4'd8 - n_in[3:0]));
        end
`endif
      end

      OP_OUT: begin
        if (at_line && !line_room) complete = 1'b0;             // line TX queue full
        else if (out_refill && pq_empty) complete = 1'b0;
        else begin
          core_txpop = out_refill;
          line_push_d = at_line;
          osr_we = 1'b1;
          osr_nv = out_osr;
          osr_cnt_nv = out_cnt;
          if (out_tap && n == 6'd1) begin
            crc_feed = 1'b1;
            crc_bit = out_data[0];
          end
`ifdef V3_MULTI
          else if (out_tap && crc_multi && n <= 6'd8) begin
            crc_feed = 1'b1;
            crc_mfeed = 1'b1;
            crc_mn = n[3:0];
            crc_mbits = out_shr ? out_data[7:0] : (rev8(out_data[7:0]) >> (4'd8 - n[3:0]));
          end
`endif
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
`ifdef V3_BANKREL
              jump_target = {pc[9:8], out_data[7:0]};
`else
              jump_target = out_data[9:0];
`endif
            end
            L_CRC: begin
              crc_set = 1'b1;
`ifdef V3_CRC32
              crc_set_val = out_data;
`else
              crc_set_val = out_data[15:0];
`endif
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
`ifdef V8_TXCLR
        if (x[16]) core_txclr = 1'b1;     // PULL CLEAR
        else
`endif
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
`ifdef V3_TBL
        if (mop == 2'd3) begin
          jump = 1'b1;                       // fetch the table word next cycle
          jump_target = {pc[9:8], src_val[7:0]};
        end else
`endif
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
`ifdef V3_BANKREL
            jump_target = {pc[9:8], mov_val[7:0]};
`else
            jump_target = mov_val[9:0];
`endif
          end
          L_CRC: begin
            crc_set = 1'b1;
`ifdef V3_CRC32
            crc_set_val = mov_val;
`else
            crc_set_val = mov_val[15:0];
`endif
          end
          L_LR: begin
            lr_we = 1'b1;
            lr_nv = mov_val[9:0];
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
`ifdef V3_CMPW
        if (x[18]) z_we = 1'b1;
        else begin
`endif
        reg_we = 1'b1;
        reg_sel = x[17:16];
        reg_val = x[15:0];
`ifdef V3_CMPW
        end
`endif
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
`ifdef V3_TOG
        if (x[17]) begin                    // toggle = a set or a clear of the flag as seen
          if (flags[x[2:0]]) fclr = 8'd1 << x[2:0];
          else fset = 8'd1 << x[2:0];
        end else
`endif
        if (x[18]) fclr = 8'd1 << x[2:0];
        else fset = 8'd1 << x[2:0];
      end

`ifdef V8_OP14
      5'd14: begin
        case (x[18:16])
`ifdef V8_PAR
          3'd0: par_we = 1'b1;     // par rN: C = parity of R[rN], Z = (R[rN] == 0)
`endif
`ifdef V8_XCH
          3'd1: xch_we = 1'b1;     // xch rN: swap R[rN] and S[rN]
`endif
`ifdef V8_DRAM
          3'd2: begin             // ld rd, [rs]
            reg_we = 1'b1;
            reg_sel = x[15:14];
            reg_val = dram[alu_s[3:0]];
          end
          3'd3: dram_we = 1'b1;    // st [rs], rd
`endif
          default: ;
        endcase
      end
`endif

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

  assign line_push      = exec && line_push_d;
  assign line_push_data = out_data;
  assign line_push_n    = n;
  assign line_push_msb  = !out_shr;
  assign line_in_n      = n;
  assign line_in_shr    = in_shr;
  assign line_take      = exec && line_take_d;

  assign flag_set = exec ? fset : 8'd0;
  assign flag_clr = exec ? fclr : 8'd0;

  wire        commit = exec && complete;
  wire        host_w = bus_sel && bus_we;
  wire        host_pc_w = host_w && bus_off == 5'h08;

`ifdef V3_TBL
  wire tbl_issue = commit && op == OP_MOV && mop == 2'd3;
  // the pend cycle doesn't commit, so it fetches `pc`, which the issue set to pc_seq:
  // the SRAM address mux is v2's
  assign fetch_addr = restart ? entry :
                      host_pc_w ? bus_wdata[9:0] :
                      commit ? (jump ? jump_target : pc_seq) : pc;
`else
  assign fetch_addr = restart ? entry :
                      host_pc_w ? bus_wdata[9:0] :
                      commit ? (jump ? jump_target : pc_seq) : pc;
`endif

  // ---------------------------------------------------------------- bus read
  // Configuration registers (0x00-0x06) are write-only: their read mux was a
  // third of the chip's global-routing overflow (docs/physical.md).
  always @(*) begin
    case (bus_off)
`ifdef V3_CRC32
      5'h07: bus_rdata = crc;
`else
      5'h07: bus_rdata = {16'd0, crc};
`endif
      5'h08: bus_rdata = {22'd0, pc};
      5'h09: bus_rdata = {16'd0, status16};
      5'h0A: bus_rdata = {16'd0, r0};
      5'h0B: bus_rdata = {16'd0, r1};
      5'h0C: bus_rdata = {16'd0, r2};
      5'h0D: bus_rdata = {16'd0, r3};
      5'h0E: bus_rdata = isr;
      5'h0F: bus_rdata = osr;
      5'h10: bus_rdata = rd_real ? rd_word : 32'd0;
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
`ifdef V3_MULTI
  always @(posedge cfg_gclk[6] or negedge rst_n)
    if (!rst_n) crc_multi <= 1'b0;
    else crc_multi <= bus_wdata[22];
`endif
`ifdef V3_CRC32
  always @(posedge cfg_gclk[6] or negedge rst_n)
    if (!rst_n) crc_w32 <= 1'b0;
    else crc_w32 <= bus_wdata[23];
`endif
`ifdef V8_CRCW
  // POLY_HI at core offset 0x11 (host-written, like CRCCFG)
  wire polyhi_gclk;
  pe_cg u_cg_polyhi (.clk(clk), .en(bus_sel && bus_we && bus_off == 5'h11), .gclk(polyhi_gclk));
  always @(posedge polyhi_gclk or negedge rst_n)
    if (!rst_n) crc_poly_hi <= 16'd0;
    else crc_poly_hi <= bus_wdata[15:0];
`endif

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
`ifdef V3_CRC32
      crc <= 32'd0;
`else
      crc <= 16'd0;
`endif
      lr <= 10'd0;
`ifdef V8_TICK
      tcnt <= 16'd0;
`endif
`ifdef V3_LR2
      lr2 <= 10'd0;
`endif
`ifdef V3_TBL
      tbl_pend <= 1'b0;
      tbl_dst <= 3'd0;
`endif
    end else begin
`ifdef V3_TBL
      pc <= (tbl_issue && !restart && !host_pc_w) ? pc_seq : fetch_addr;   // continue after the MOV
      tbl_pend <= exec && tbl_issue && !restart;
      if (exec && tbl_issue) tbl_dst <= (dst[3:2] == 2'b00) ? {1'b0, dst[1:0]} : (dst == L_ISR) ? 3'd6 : (dst == L_OSR) ? 3'd7 : 3'd4;
`ifdef V3_TBL_OSR
      if (tbl_pend) begin osr <= instr; osr_cnt <= 6'd0; end   // OSR-only variant
      if (1'b0) begin
`else
      if (tbl_pend) begin                              // the table word arrived: write it
`endif
        case (tbl_dst)
          3'd0: r0 <= instr[15:0];
          3'd1: r1 <= instr[15:0];
          3'd2: r2 <= instr[15:0];
          3'd3: r3 <= instr[15:0];
          3'd6: begin isr <= instr; isr_cnt <= 6'd0; end
          3'd7: begin osr <= instr; osr_cnt <= 6'd0; end
          default: ;
        endcase
      end
`else
      pc <= fetch_addr;
`endif

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
`ifdef V8_TICK
        tcnt <= 16'd0;
`endif
      end else if (active) begin
        cnt <= cnt_next;
`ifdef V8_TICK
        if (tick) tcnt <= tcnt + 16'd1;
`endif
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
`ifdef V8_PAR
            if (par_we) begin
              z <= alu_a == 16'd0;
              c <= ^alu_a;
            end
`endif
`ifdef V8_XCH
            if (xch_we) case (x[15:14])
              2'd0: begin r0 <= s0; s0 <= r0; end
              2'd1: begin r1 <= s1; s1 <= r1; end
              2'd2: begin r2 <= s2; s2 <= r2; end
              default: begin r3 <= s3; s3 <= r3; end
            endcase
`endif
`ifdef V8_DRAM
            if (dram_we) dram[alu_s[3:0]] <= alu_a;
`endif
            if (crc_set) crc <= crc_set_val & crc_mask;
            else if (crc_feed) crc <= crc_fed;
`ifdef V3_LR2
            // two-entry link stack: a taken CALL pushes, RET (mov pc, lr) pops
            if (op == OP_CALL && lr_we) begin lr <= lr_nv; lr2 <= lr; end
            else if (lr_we) lr <= lr_nv;
            else if (op == OP_MOV && dst == L_PC && x[12:9] == L_LR) lr <= lr2;
`else
            if (lr_we) lr <= lr_nv;
`endif
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
`ifdef V3_CRC32
          5'h07: crc <= bus_wdata;
`else
          5'h07: crc <= bus_wdata[15:0];
`endif
          default: ;
        endcase
      end
    end
  end

`ifdef V8_TXCLR
  always @(posedge clk or negedge rst_n)
    if (!rst_n) txclr_q <= 1'b0;
    else txclr_q <= exec && core_txclr;
`endif
endmodule
