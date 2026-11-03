(* One protocol-engine lane: port of src/pe_core.sv (docs/isa.md sections 4-5).
   The [ifdef FORMAL] safety block is not ported: formal/core.sby proves it on
   src/, and the port is proved equal to src/ (docs/hardcaml-port.md). *)
open! Base
open Hardcaml
open Signal
open Prims

module I = struct
  type 'a t =
    { clk : 'a
    ; rst_n : 'a
    ; enable : 'a
    ; restart : 'a
    ; fifo_clear : 'a
    ; instr : 'a [@bits 32]
    ; pins_in : 'a [@bits 32]
    ; pin_out_cur : 'a [@bits 32]
    ; flags : 'a [@bits 8]
    ; bus_sel : 'a
    ; bus_off : 'a [@bits 5]
    ; bus_we : 'a
    ; bus_wdata : 'a [@bits 32]
    ; bus_commit : 'a
    ; bus_snap : 'a
    ; bus_cancel : 'a
    ; line_room : 'a
    ; line_ok : 'a
    ; line_k : 'a [@bits 6]
    ; line_t : 'a
    ; line_val : 'a [@bits 32]
    }
  [@@deriving hardcaml]
end

module O = struct
  type 'a t =
    { fetch_addr : 'a [@bits 10]
    ; wr_out_en : 'a [@bits 32]
    ; wr_out_val : 'a [@bits 32]
    ; wr_oe_en : 'a [@bits 32]
    ; wr_oe_val : 'a [@bits 32]
    ; flag_set : 'a [@bits 8]
    ; flag_clr : 'a [@bits 8]
    ; bus_rdata : 'a [@bits 32]
    ; line_push : 'a
    ; line_push_data : 'a [@bits 32]
    ; line_push_n : 'a [@bits 6]
    ; line_push_msb : 'a
    ; line_in_n : 'a [@bits 6]
    ; line_in_shr : 'a
    ; line_take : 'a
    }
  [@@deriving hardcaml]
end

(* opcodes *)
let op_jmp = 0
let op_wait = 1
let op_in = 2
let op_out = 3
let op_push = 4
let op_pull = 5
let op_mov = 6
let op_set = 7
let op_ldi = 8
let op_alu = 9
let op_pin = 10
let op_flag = 11
let op_halt = 12
let op_call = 13

(* operand codes *)
let l_pins = 4
let l_pindirs = 5
let l_isr = 6
let l_osr = 7
let l_status = 9
let l_flags = 10
let l_pc = 11
let l_crc = 12
let l_line = 13
let l_lr = 14

(* WAIT kinds *)
let w_rise = 2
let w_fall = 3
let w_flag1 = 5
let w_diff = 7

let create ~idx ~fifo_log2 ~fifo_no_cg scope (i : _ I.t) =
  let w = i.bus_wdata in
  let k16 = of_int_trunc ~width:16 in
  let k10 = of_int_trunc ~width:10 in
  let k6 = of_int_trunc ~width:6 in
  (* ---- config registers: host-only, each behind a clock gate *)
  let host_cfg_en g = i.bus_sel &: i.bus_we &: (i.bus_off ==:. g) in
  let cfg_gclk =
    Array.init 7 ~f:(fun g ->
      cg ~instance:(Printf.sprintf "g_cfg_cg_%d_u_cg" g) ~clk:i.clk ~en:(host_cfg_en g))
  in
  let cfg g ?reset_to name d =
    gated ~clock:cfg_gclk.(g) ~rst_n:i.rst_n ?reset_to d -- name
  in
  let div_int = cfg 0 ~reset_to:(k16 1) "div_int" w.:[23, 8] in
  let div_frac = cfg 0 "div_frac" w.:[7, 0] in
  let out_base = cfg 1 "out_base" w.:[4, 0] in
  let out_count = cfg 1 "out_count" w.:[13, 8] in
  let set_base = cfg 1 "set_base" w.:[20, 16] in
  let set_count = cfg 1 "set_count" w.:[26, 24] in
  let side_base = cfg 2 "side_base" w.:[4, 0] in
  let side_count = cfg 2 "side_count" w.:[10, 8] in
  let side_opt = cfg 2 "side_opt" (bit w 12) in
  let side_pindir = cfg 2 "side_pindir" (bit w 13) in
  let in_base = cfg 2 "in_base" w.:[20, 16] in
  let out_shr = cfg 3 "out_shr" (bit w 0) in
  let in_shr = cfg 3 "in_shr" (bit w 1) in
  let autopull = cfg 3 "autopull" (bit w 2) in
  let autopush = cfg 3 "autopush" (bit w 3) in
  let fjoin = cfg 3 "fjoin" (bit w 4) in
  let pull_thresh = cfg 3 "pull_thresh" w.:[12, 8] in
  let push_thresh = cfg 3 "push_thresh" w.:[20, 16] in
  let idx_addr = k10 (idx lsl 8) in
  let entry = cfg 4 ~reset_to:idx_addr "entry" w.:[9, 0] in
  let wrap_bottom = cfg 5 "wrap_bottom" w.:[9, 0] in
  let wrap_top = cfg 5 ~reset_to:(k10 1023) "wrap_top" w.:[25, 16] in
  let crc_poly = cfg 6 "crc_poly" w.:[15, 0] in
  let crc_w = cfg 6 "crc_w" w.:[19, 16] in
  let in_tap = cfg 6 "in_tap" (bit w 20) in
  let out_tap = cfg 6 "out_tap" (bit w 21) in
  (* ---- state *)
  let dom = domain ~clock:i.clk ~rst_n:i.rst_n in
  let reg ?reset_to name width = state dom ?reset_to name width in
  let pc = reg ~reset_to:idx_addr "pc" 10 in
  let r0 = reg "r0" 16 in
  let r1 = reg "r1" 16 in
  let r2 = reg "r2" 16 in
  let r3 = reg "r3" 16 in
  let isr = reg "isr" 32 in
  let osr = reg "osr" 32 in
  let isr_cnt = reg "isr_cnt" 6 in
  let osr_cnt = reg ~reset_to:(k6 32) "osr_cnt" 6 in
  let z = reg "z" 1 in
  let c = reg "c" 1 in
  let t = reg "t" 1 in
  let dcnt = reg "dcnt" 8 in
  let in_wait = reg "in_wait" 1 in
  let wprev = reg "wprev" 1 in
  let wevent = reg "wevent" 1 in
  let cnt = reg "cnt" 16 in
  let facc = reg "facc" 8 in
  let halted = reg "halted" 1 in
  let fault = reg "fault" 1 in
  let stalled = reg "stalled" 1 in
  let crc = reg "crc" 16 in
  let lr = reg "lr" 10 in
  let res_n = reg "res_n" 2 in
  let res_v = reg "res_v" 2 in
  (* ---- execute outputs (assigned in the block below, used by the FIFOs) *)
  let wire d = Always.Variable.wire ~default:d in
  let core_txpop = wire gnd in
  let core_rxpush = wire gnd in
  let line_push_d = wire gnd in
  let line_take_d = wire gnd in
  let core_rxdata = wire isr.value in
  (* ---- enable chain *)
  let instr = i.instr in
  let op = instr.:[31, 27] in
  let sd = instr.:[26, 19] in
  let x = instr.:[18, 0] in
  let tick = cnt.value ==:. 0 in
  let active = i.enable &: ~:(i.restart) in
  let attempt = active &: tick &: ~:(halted.value) &: ~:(fault.value) &: (dcnt.value ==:. 0) in
  let bank_ok = pc.value.:[9, 8] ==:. idx in
  let exec = attempt &: bank_ok in
  (* ---- FIFOs *)
  let host_push = i.bus_sel &: i.bus_we &: (i.bus_off ==:. 0x10) in
  let fifo_sel = i.bus_sel &: (i.bus_off ==:. 0x10) in
  let res_some = res_n.value <>:. 0 in
  let res_two = res_n.value ==:. 2 in
  let res_real =
    uresize (res_some &: bit res_v.value 0) ~width:2
    +: uresize (res_two &: bit res_v.value 1) ~width:2
  in
  let host_pop = fifo_sel &: i.bus_commit &: res_some &: bit res_v.value 0 in
  let f =
    Pe_fifos.hierarchical
      ~instance:"u_fifos"
      ~log2:fifo_log2
      ~no_cg:fifo_no_cg
      scope
      { Pe_fifos.I.clk = i.clk
      ; rst_n = i.rst_n
      ; clear = i.fifo_clear
      ; fjoin
      ; host_push
      ; host_data = i.bus_wdata
      ; host_pop
      ; core_pop = exec &: core_txpop.value
      ; core_push = exec &: core_rxpush.value
      ; core_data = core_rxdata.value
      }
  in
  let rxf_head = f.rxf_head in
  let rxf_head2 = f.rxf_head2 in
  let txf_level = f.txf_level in
  let rxf_level = f.rxf_level in
  let pq_head = f.pq_head in
  let pq_empty = f.pq_empty in
  let rx_block = f.rx_block in
  let rd_real =
    ~:fjoin &: (res_real <>:. 2) &: (rxf_level >: (zero 2 @: res_real))
  in
  let rd_word = mux2 (res_real ==:. 0) rxf_head rxf_head2 in
  (* ---- decode *)
  let rsel sel = mux sel [ r0.value; r1.value; r2.value; r3.value ] in
  let status16 =
    gnd
    @: fault.value
    @: halted.value
    @: stalled.value
    @: gnd
    @: t.value
    @: c.value
    @: z.value
    @: rxf_level
    @: txf_level
  in
  let thresh th = mux2 (th ==:. 0) (k6 32) (gnd @: th) in
  let pull_th = thresh pull_thresh in
  let push_th = thresh push_thresh in
  (* divider *)
  let fsum = (gnd @: facc.value) +: (gnd @: div_frac) in
  let cnt_next =
    mux2 tick (div_int -:. 1 +: uresize (bit fsum 8) ~width:16) (cnt.value -:. 1)
  in
  let pins_rot = rotr32 i.pins_in in_base in
  (* WAIT edge tracker *)
  let wkind = x.:[18, 16] in
  let widx = x.:[15, 11] in
  let pin s = mux s (bits_lsb i.pins_in) in
  let wpin = pin widx in
  let qual = ~:(bit x 6) |: (pin x.:[4, 0] ==: bit x 5) in
  let trans =
    mux2
      (wkind ==:. w_rise)
      (~:(wprev.value) &: wpin)
      (mux2 (wkind ==:. w_fall) (wprev.value &: ~:wpin) (wprev.value <>: wpin))
  in
  let wref = rsel x.:[15, 14] in
  let wdiff = reduce_or ((pins_rot.:[7, 0] ^: wref.:[7, 0]) &: x.:[7, 0]) in
  let edge_done =
    wevent.value |: mux2 (wkind ==:. w_diff) wdiff (trans &: qual)
  in
  (* side-set / delay split *)
  let sc = mux2 (side_count >:. 4) (of_int_trunc ~width:3 4) side_count in
  let dbits =
    of_int_trunc ~width:4 8 -: (gnd @: sc) -: uresize side_opt ~width:4
  in
  let delay = sd &: (shift_left (of_int_trunc ~width:8 1) dbits -:. 1) in
  let side_en = mux2 side_opt (bit sd 7) vdd in
  let side_raw = shift_right sd dbits in
  let sc_mask4 = shift_left (of_int_trunc ~width:4 1) sc -:. 1 in
  let side_v = side_raw.:[3, 0] &: sc_mask4 in
  let side_act = exec &: (sc <>:. 0) &: side_en in
  let side_mask = rotl32 (zero 28 @: sc_mask4) side_base in
  let side_val = rotl32 (zero 28 @: side_v) side_base in
  (* sources *)
  let src_code = mux2 (op ==:. op_mov) x.:[12, 9] x.:[18, 15] in
  let src_val =
    let reg_src n = zero 16 @: n in
    mux
      src_code
      [ reg_src r0.value
      ; reg_src r1.value
      ; reg_src r2.value
      ; reg_src r3.value
      ; pins_rot (* L_PINS *)
      ; zero 32 (* 5: PINDIRS reads 0 *)
      ; isr.value
      ; osr.value
      ; zero 32 (* 8: NULL *)
      ; zero 21 @: status16.:[10, 0]
      ; zero 24 @: i.flags
      ; zero 22 @: pc.value
      ; zero 16 @: crc.value
      ; zero 32 (* 13: LINE *)
      ; zero 22 @: lr.value
      ; zero 32
      ]
  in
  (* shift amounts (count field 0 means 32) *)
  let n = mux2 (x.:[4, 0] ==:. 0) (k6 32) (gnd @: x.:[4, 0]) in
  let at_line = if idx = 0 then x.:[18, 15] ==:. l_line else gnd in
  let is_in = op ==:. op_in in
  let in_line = is_in &: at_line in
  let n_in = mux2 in_line i.line_k n in
  (* shared 64-bit funnel shifter for IN and OUT *)
  let nmask = lowmask n in
  let in_bits = mux2 in_line i.line_val (src_val &: nmask) in
  let out_refill = autopull &: (osr_cnt.value >=: pull_th) in
  let osr0 = mux2 out_refill pq_head osr.value in
  let shr = mux2 is_in in_shr out_shr in
  let fs_hi =
    mux2 is_in (mux2 in_shr in_bits isr.value) (mux2 out_shr (zero 32) osr0)
  in
  let fs_lo =
    mux2 is_in (mux2 in_shr isr.value (zero 32)) (mux2 out_shr osr0 (zero 32))
  in
  let n_sh = mux2 is_in n_in n in
  let fs_amt = mux2 shr n_sh (k6 32 -: n_sh) in
  let fs = shift_right (fs_hi @: fs_lo) fs_amt in
  (* IN *)
  let in_isr = mux2 in_shr fs.:[31, 0] (fs.:[31, 0] |: in_bits) in
  let sat_cnt a b =
    let sum = (gnd @: a) +: (gnd @: b) in
    mux2 (sum >:. 32) (k6 32) sum.:[5, 0]
  in
  let in_cnt = sat_cnt isr_cnt.value n_in in
  let in_autopush = autopush &: (in_cnt >=: push_th) in
  (* OUT *)
  let cnt0 = mux2 out_refill (zero 6) osr_cnt.value in
  let out_data = mux2 out_shr (osr0 &: nmask) fs.:[63, 32] in
  let out_osr = fs.:[31, 0] in
  let out_cnt = sat_cnt cnt0 n in
  (* MOV value *)
  let mop = x.:[14, 13] in
  let mov_val = mux2 (mop ==:. 1) ~:src_val (mux2 (mop ==:. 2) (reverse src_val) src_val) in
  (* ALU *)
  let aop = x.:[18, 16] in
  let alu_a = rsel x.:[15, 14] in
  let alu_s = mux2 (bit x 13) (zero 3 @: x.:[12, 0]) (rsel x.:[1, 0]) in
  let alu_sub = (alu_a <: alu_s) @: (alu_a -: alu_s) in
  let alu_res =
    mux
      aop
      [ (gnd @: alu_a) +: (gnd @: alu_s)
      ; alu_sub
      ; gnd @: (alu_a &: alu_s)
      ; gnd @: (alu_a |: alu_s)
      ; gnd @: (alu_a ^: alu_s)
      ; gnd @: shift_left alu_a alu_s.:[3, 0]
      ; gnd @: shift_right alu_a alu_s.:[3, 0]
      ; alu_sub
      ]
  in
  (* JMP condition *)
  let cond = x.:[18, 15] in
  let jsel = x.:[14, 10] in
  let jr = rsel jsel.:[1, 0] in
  let jflag = mux jsel.:[2, 0] (bits_lsb i.flags) in
  let cond_ok =
    mux
      cond
      [ vdd
      ; jr ==:. 0
      ; jr <>:. 0
      ; jr <>:. 0
      ; pin jsel
      ; ~:(pin jsel)
      ; z.value
      ; ~:(z.value)
      ; c.value
      ; ~:(c.value)
      ; t.value
      ; osr_cnt.value <: pull_th
      ; ~:pq_empty
      ; rx_block
      ; jflag
      ; ~:jflag
      ]
  in
  (* WAIT *)
  let wflag = mux widx.:[2, 0] (bits_lsb i.flags) in
  let wedge = in_wait.value &: edge_done in
  let wbase =
    mux
      wkind
      [ wpin &: qual
      ; ~:wpin &: qual
      ; wedge
      ; wedge
      ; wedge
      ; wflag &: qual
      ; ~:wflag &: qual
      ; mux2 in_wait.value edge_done wdiff
      ]
  in
  let w_to = bit x 10 in
  let w_treg = x.:[9, 8] in
  let w_tr = rsel w_treg in
  let w_timeout = ~:wbase &: w_to &: (w_tr ==:. 0) in
  (* CRC tap *)
  let crc_mask = shift_right (ones 16) (of_int_trunc ~width:4 15 -: crc_w) in
  let crc_bit = wire gnd in
  let crc_fb = mux crc_w (bits_lsb crc.value) ^: crc_bit.value in
  let crc_fed =
    (sll_const crc.value 1 &: crc_mask) ^: mux2 crc_fb (crc_poly &: crc_mask) (zero 16)
  in
  let pc_seq = mux2 (pc.value ==: wrap_top) wrap_bottom (pc.value +:. 1) in
  (* ---- execute (combinational) *)
  let complete = wire vdd in
  let jump = wire gnd in
  let new_wait = wire gnd in
  let lr_we = wire gnd in
  let lr_nv = wire pc_seq in
  let jump_target = wire x.:[9, 0] in
  let reg_we = wire gnd in
  let reg_sel = wire (zero 2) in
  let reg_val = wire (zero 16) in
  let isr_we = wire gnd in
  let osr_we = wire gnd in
  let isr_nv = wire (zero 32) in
  let osr_nv = wire (zero 32) in
  let isr_cnt_nv = wire (zero 6) in
  let osr_cnt_nv = wire (zero 6) in
  let z_we = wire gnd in
  let t_we = wire gnd in
  let t_nv = wire gnd in
  let pw_out = wire gnd in
  let pw_oe = wire gnd in
  let pw_single = wire gnd in
  let pw_vo = wire gnd in
  let pw_ve = wire gnd in
  let pw_base = wire out_base in
  let pw_cnt = wire out_count in
  let pw_data = wire (mux2 (op ==:. op_mov) mov_val out_data) in
  let fset = wire (zero 8) in
  let fclr = wire (zero 8) in
  let crc_feed = wire gnd in
  let crc_set = wire gnd in
  let crc_set_val = wire (zero 16) in
  let dst = x.:[18, 15] in
  let one8 = of_int_trunc ~width:8 1 in
  let in_blocked = in_autopush &: rx_block in
  let dst_is_reg = dst <:. 4 in
  let exec_block =
    Always.(
      let reg_write s v = proc [ reg_we <-- vdd; reg_sel <-- s; reg_val <-- v ] in
      let set_isr v c = proc [ isr_we <-- vdd; isr_nv <-- v; isr_cnt_nv <-- c ] in
      let set_osr v c = proc [ osr_we <-- vdd; osr_nv <-- v; osr_cnt_nv <-- c ] in
      let clear_isr = set_isr (zero 32) (zero 6) in
      let when_op o body = when_ (op ==:. o) body in
      let when_dst d body = when_ (dst ==:. d) body in
      [ when_
          ((op ==:. op_jmp) |: (op ==:. op_call))
          [ jump <-- cond_ok
          ; lr_we <-- ((op ==:. op_call) &: cond_ok)
          ; when_ (cond ==:. 3) [ reg_write jsel.:[1, 0] (jr -:. 1) ]
          ]
      ; when_op
          op_wait
          [ if_
              wbase
              [ when_ w_to [ t_we <-- vdd; t_nv <-- gnd ]
              ; when_
                  ((wkind ==:. w_flag1) &: bit x 7)
                  [ fclr <-- shift_left one8 widx.:[2, 0] ]
              ]
            @@ elif
                 w_timeout
                 [ t_we <-- vdd; t_nv <-- vdd ]
                 [ complete <-- gnd
                 ; new_wait <-- vdd
                 ; when_ w_to [ reg_write w_treg (w_tr -:. 1) ]
                 ]
          ]
      ; when_op
          op_in
          [ if_
              (in_line &: ~:(i.line_ok))
              [ complete <-- gnd ]
            @@ elif
                 in_autopush
                 [ if_
                     rx_block
                     [ complete <-- gnd ]
                     [ core_rxpush <-- vdd; core_rxdata <-- in_isr; clear_isr ]
                 ]
                 [ set_isr in_isr in_cnt ]
          ; when_
              (in_line &: i.line_ok &: ~:in_blocked)
              [ line_take_d <-- vdd; t_we <-- vdd; t_nv <-- i.line_t ]
          ; when_
              (in_tap
               &: (n ==:. 1)
               &: (n_in ==:. 1)
               &: ~:(in_line &: ~:(i.line_ok))
               &: ~:in_blocked)
              [ crc_feed <-- vdd; crc_bit <-- bit in_bits 0 ]
          ]
      ; when_op
          op_out
          [ if_
              (at_line &: ~:(i.line_room))
              [ complete <-- gnd ]
            @@ elif
                 (out_refill &: pq_empty)
                 [ complete <-- gnd ]
                 [ core_txpop <-- out_refill
                 ; line_push_d <-- at_line
                 ; set_osr out_osr out_cnt
                 ; when_
                     (out_tap &: (n ==:. 1))
                     [ crc_feed <-- vdd; crc_bit <-- bit out_data 0 ]
                 ; when_ dst_is_reg [ reg_write dst.:[1, 0] out_data.:[15, 0] ]
                 ; when_dst l_pins [ pw_out <-- vdd ]
                 ; when_dst l_pindirs [ pw_oe <-- vdd ]
                 ; when_dst l_isr [ set_isr out_data n ]
                 ; when_dst
                     l_pc
                     [ jump <-- vdd; jump_target <-- out_data.:[9, 0] ]
                 ; when_dst
                     l_crc
                     [ crc_set <-- vdd; crc_set_val <-- out_data.:[15, 0] ]
                 ]
          ]
      ; when_op
          op_push
          [ when_
              ~:(bit x 18 &: (isr_cnt.value <: push_th))
              [ if_
                  rx_block
                  [ if_ (bit x 17) [ complete <-- gnd ] [ clear_isr ] ]
                  [ core_rxpush <-- vdd; core_rxdata <-- isr.value; clear_isr ]
              ]
          ]
      ; when_op
          op_pull
          [ when_
              ~:(bit x 18 &: (osr_cnt.value <: pull_th))
              [ if_
                  pq_empty
                  [ if_
                      (bit x 17)
                      [ complete <-- gnd ]
                      [ set_osr (zero 16 @: r0.value) (zero 6) ]
                  ]
                  [ core_txpop <-- vdd; set_osr pq_head (zero 6) ]
              ]
          ]
      ; when_op
          op_mov
          [ when_ dst_is_reg [ reg_write dst.:[1, 0] mov_val.:[15, 0] ]
          ; when_dst l_pins [ pw_out <-- vdd ]
          ; when_dst l_pindirs [ pw_oe <-- vdd ]
          ; when_dst l_isr [ set_isr mov_val (zero 6) ]
          ; when_dst l_osr [ set_osr mov_val (zero 6) ]
          ; when_dst l_pc [ jump <-- vdd; jump_target <-- mov_val.:[9, 0] ]
          ; when_dst l_crc [ crc_set <-- vdd; crc_set_val <-- mov_val.:[15, 0] ]
          ; when_dst l_lr [ lr_we <-- vdd; lr_nv <-- mov_val.:[9, 0] ]
          ]
      ; when_op
          op_set
          [ pw_out <-- ~:(bit x 18)
          ; pw_oe <-- bit x 18
          ; pw_base <-- set_base
          ; pw_cnt <-- mux2 (set_count >:. 5) (k6 5) (zero 3 @: set_count)
          ; pw_data <-- (zero 27 @: x.:[4, 0])
          ]
      ; when_op op_ldi [ reg_write x.:[17, 16] x.:[15, 0] ]
      ; when_op
          op_alu
          [ z_we <-- vdd
          ; when_ (aop <>:. 7) [ reg_write x.:[15, 14] alu_res.:[15, 0] ]
          ]
      ; when_op
          op_pin
          [ pw_single <-- vdd
          ; pw_base <-- x.:[4, 0]
          ; pw_cnt <-- k6 1
          ; when_
              (x.:[18, 17] ==:. 0)
              [ pw_out <-- vdd; pw_oe <-- vdd; pw_vo <-- gnd; pw_ve <-- vdd ]
          ; when_
              (x.:[18, 17] ==:. 1)
              [ pw_out <-- vdd; pw_oe <-- vdd; pw_vo <-- vdd; pw_ve <-- vdd ]
          ; when_
              (x.:[18, 17] ==:. 2)
              [ pw_out <-- vdd
              ; pw_vo <-- ~:(mux x.:[4, 0] (bits_lsb i.pin_out_cur))
              ]
          ; when_ (x.:[18, 17] ==:. 3) [ pw_oe <-- vdd; pw_ve <-- gnd ]
          ]
      ; when_op
          op_flag
          [ if_
              (bit x 18)
              [ fclr <-- shift_left one8 x.:[2, 0] ]
              [ fset <-- shift_left one8 x.:[2, 0] ]
          ]
      ; when_op op_halt [ complete <-- gnd ]
      ])
  in
  Always.compile exec_block;
  (* ---- outputs *)
  let pw_mask = rotl32 (lowmask pw_cnt.value) pw_base.value in
  let pw_rot = rotl32 pw_data.value pw_base.value in
  let pw_val_out = mux2 pw_single.value (repeat_bit pw_vo.value 32) pw_rot in
  let pw_val_oe = mux2 pw_single.value (repeat_bit pw_ve.value 32) pw_rot in
  let side_to_out = side_act &: ~:side_pindir in
  let side_to_oe = side_act &: side_pindir in
  let m_out = mux2 (exec &: pw_out.value) pw_mask (zero 32) in
  let m_oe = mux2 (exec &: pw_oe.value) pw_mask (zero 32) in
  let s_mask = mux2 side_act side_mask (zero 32) in
  let side_over sel value =
    mux2 sel ((side_val &: s_mask) |: (value &: ~:s_mask)) value
  in
  let wr_out_en = m_out |: mux2 side_to_out s_mask (zero 32) in
  let wr_out_val = side_over side_to_out pw_val_out in
  let wr_oe_en = m_oe |: mux2 side_to_oe s_mask (zero 32) in
  let wr_oe_val = side_over side_to_oe pw_val_oe in
  let commit = exec &: complete.value in
  let host_w = i.bus_sel &: i.bus_we in
  let host_pc_w = host_w &: (i.bus_off ==:. 0x08) in
  let fetch_addr =
    mux2
      i.restart
      entry
      (mux2
         host_pc_w
         i.bus_wdata.:[9, 0]
         (mux2 commit (mux2 jump.value jump_target.value pc_seq) pc.value))
  in
  (* ---- bus read: registers 0x00-0x06 are write-only *)
  let bus_rdata =
    let z32 = zero 32 in
    mux
      i.bus_off
      (List.init 7 ~f:(fun _ -> z32)
       @ [ zero 16 @: crc.value
         ; zero 22 @: pc.value
         ; zero 16 @: status16
         ; zero 16 @: r0.value
         ; zero 16 @: r1.value
         ; zero 16 @: r2.value
         ; zero 16 @: r3.value
         ; isr.value
         ; osr.value
         ; mux2 rd_real rd_word z32
         ]
       @ List.init 15 ~f:(fun _ -> z32))
  in
  (* ---- sequential *)
  let full_nv = reg_val.value in
  let clear_state =
    Always.(
      proc
        [ isr <-- zero 32
        ; osr <-- zero 32
        ; isr_cnt <-- zero 6
        ; osr_cnt <-- k6 32
        ; z <-- gnd
        ; c <-- gnd
        ; t <-- gnd
        ; dcnt <-- zero 8
        ; in_wait <-- gnd
        ; wprev <-- gnd
        ; wevent <-- gnd
        ; cnt <-- zero 16
        ; facc <-- zero 8
        ; halted <-- gnd
        ; fault <-- gnd
        ; stalled <-- gnd
        ])
  in
  Always.(
    compile
      [ (* host FIFO reservations *)
        if_
          (i.bus_cancel |: i.fifo_clear)
          [ res_n <-- zero 2 ]
        @@ elif
             (fifo_sel &: i.bus_commit &: res_some)
             [ res_n <-- res_n.value -:. 1; res_v <-- (gnd @: bit res_v.value 1) ]
        @@ elif
             (fifo_sel &: i.bus_snap &: ~:res_two)
             [ res_n <-- res_n.value +:. 1
             ; if_
                 (res_n.value ==:. 0)
                 [ res_v <-- (bit res_v.value 1 @: rd_real) ]
                 [ res_v <-- (rd_real @: bit res_v.value 0) ]
             ]
             []
      ; pc <-- fetch_addr
      ; if_
          i.restart
          [ clear_state ]
        @@ elif
             active
             [ cnt <-- cnt_next
             ; when_ tick [ facc <-- fsum.:[7, 0] ]
             ; when_
                 in_wait.value
                 [ wevent <-- edge_done; wprev <-- wpin ]
             ; when_
                 (tick &: ~:(halted.value) &: ~:(fault.value))
                 [ if_
                     (dcnt.value <>:. 0)
                     [ dcnt <-- dcnt.value -:. 1 ]
                   @@ elif
                        ~:bank_ok
                        [ fault <-- vdd; halted <-- vdd; stalled <-- gnd ]
                        [ stalled <-- (~:(complete.value) &: (op <>:. op_halt))
                        ; in_wait <-- new_wait.value
                        ; when_
                            (new_wait.value &: ~:(in_wait.value))
                            [ wprev <-- wpin; wevent <-- gnd ]
                        ; when_ (op ==:. op_halt) [ halted <-- vdd ]
                        ; when_ complete.value [ dcnt <-- delay ]
                        ; when_
                            reg_we.value
                            [ when_ (reg_sel.value ==:. 0) [ r0 <-- full_nv ]
                            ; when_ (reg_sel.value ==:. 1) [ r1 <-- full_nv ]
                            ; when_ (reg_sel.value ==:. 2) [ r2 <-- full_nv ]
                            ; when_ (reg_sel.value ==:. 3) [ r3 <-- full_nv ]
                            ]
                        ; when_
                            isr_we.value
                            [ isr <-- isr_nv.value; isr_cnt <-- isr_cnt_nv.value ]
                        ; when_
                            osr_we.value
                            [ osr <-- osr_nv.value; osr_cnt <-- osr_cnt_nv.value ]
                        ; when_
                            z_we.value
                            [ z <-- (alu_res.:[15, 0] ==:. 0); c <-- bit alu_res 16 ]
                        ; when_ t_we.value [ t <-- t_nv.value ]
                        ; if_
                            crc_set.value
                            [ crc <-- (crc_set_val.value &: crc_mask) ]
                            [ when_ crc_feed.value [ crc <-- crc_fed ] ]
                        ; when_ lr_we.value [ lr <-- lr_nv.value ]
                        ]
                 ]
             ]
             []
      ; (* host writes win over core writes in the same cycle *)
        when_
          host_w
          [ when_
              (i.bus_off ==:. 0x08)
              [ dcnt <-- zero 8; in_wait <-- gnd ]
          ; when_ (i.bus_off ==:. 0x0A) [ r0 <-- w.:[15, 0] ]
          ; when_ (i.bus_off ==:. 0x0B) [ r1 <-- w.:[15, 0] ]
          ; when_ (i.bus_off ==:. 0x0C) [ r2 <-- w.:[15, 0] ]
          ; when_ (i.bus_off ==:. 0x0D) [ r3 <-- w.:[15, 0] ]
          ; when_ (i.bus_off ==:. 0x07) [ crc <-- w.:[15, 0] ]
          ]
      ]);
  { O.fetch_addr
  ; wr_out_en
  ; wr_out_val
  ; wr_oe_en
  ; wr_oe_val
  ; flag_set = mux2 exec fset.value (zero 8)
  ; flag_clr = mux2 exec fclr.value (zero 8)
  ; bus_rdata
  ; line_push = exec &: line_push_d.value
  ; line_push_data = out_data
  ; line_push_n = n
  ; line_push_msb = ~:out_shr
  ; line_in_n = n
  ; line_in_shr = in_shr
  ; line_take = exec &: line_take_d.value
  }
;;

let hierarchical ~instance ~idx ~fifo_log2 ~fifo_no_cg scope i =
  let module H = Hierarchy.In_scope (I) (O) in
  H.hierarchical
    ~config:circuit_config
    ~scope
    ~name:"pe_core"
    ~instance
    (create ~idx ~fifo_log2 ~fifo_no_cg)
    i
;;
