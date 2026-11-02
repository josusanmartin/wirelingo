(* Line unit: port of src/pe_line.sv (docs/isa.md section 4.7).

   The Verilog's next-state block is one combinational always with blocking
   assignments, so a later statement can read what an earlier one wrote.
   [Always] has no such thing: a variable's [.value] is the final value of the
   block. So the transmitter is two blocks, stage 1 (everything before
   [if (do_next)]) and stage 2, which reads stage 1's outputs, and the task
   [deliver] is a pure function called with the values it would have read. *)
open! Base
open Hardcaml
open Signal
open Prims

module I = struct
  type 'a t =
    { clk : 'a
    ; rst_n : 'a
    ; cfg_we : 'a
    ; div_we : 'a
    ; pins_we : 'a
    ; wdata : 'a [@bits 32]
    ; pins_in : 'a [@bits 32]
    ; rx_mid : 'a
    ; push : 'a
    ; push_data : 'a [@bits 32]
    ; push_n : 'a [@bits 6]
    ; push_msb : 'a
    ; in_n : 'a [@bits 6]
    ; in_shr : 'a
    ; take : 'a
    }
  [@@deriving hardcaml]
end

module O = struct
  type 'a t =
    { r_stat : 'a [@bits 32]
    ; rx_pin : 'a [@bits 5]
    ; drv_mask : 'a [@bits 32]
    ; drv_val : 'a [@bits 32]
    ; room : 'a
    ; in_ok : 'a
    ; in_k : 'a [@bits 6]
    ; in_t : 'a
    ; in_val : 'a [@bits 32]
    }
  [@@deriving hardcaml]
end

(* task deliver: what one received bit does to the stuffing run *)
type delivered =
  { d_run : Signal.t
  ; d_runbit : Signal.t
  ; d_runv : Signal.t
  ; d_off : Signal.t (* n_rx_on = 0; new_end = 1 *)
  ; d_v : Signal.t
  ; d_b : Signal.t
  }

let saturating_inc3 r = mux2 (r ==:. 7) (of_int_trunc ~width:3 7) (r +:. 1)

let deliver ~k ~ones ~run ~runbit ~runv ~bit_in =
  let stuff = (k <>:. 0) &: (run ==: k) in
  let bad = bit_in <>: mux2 ones gnd ~:runbit in
  let one3 = of_int_trunc ~width:3 1 in
  let zero3 = zero 3 in
  let plain_run =
    mux2
      ones
      (mux2 bit_in (saturating_inc3 run) zero3)
      (mux2 (runv &: (bit_in ==: runbit)) (saturating_inc3 run) one3)
  in
  let d_run = mux2 stuff (mux2 bad run (mux2 ones zero3 one3)) plain_run in
  let d_runbit =
    mux2 stuff (mux2 (bad |: ones) runbit bit_in) (mux2 ones runbit bit_in)
  in
  let d_runv = mux2 stuff (mux2 (bad |: ones) runv vdd) (mux2 ones runv vdd) in
  { d_run; d_runbit; d_runv; d_off = stuff &: bad; d_v = ~:stuff; d_b = ~:stuff &: bit_in }
;;

let create _scope (i : _ I.t) =
  let wdata = i.wdata in
  (* ---- configuration *)
  let dom = domain ~clock:i.clk ~rst_n:i.rst_n in
  let cfg = dff dom ~enable:i.cfg_we wdata.:[14, 0] -- "cfg" in
  let div = dff dom ~enable:i.div_we wdata -- "div" in
  let txp = dff dom ~enable:i.pins_we wdata.:[4, 0] -- "txp" in
  let rxp = dff dom ~enable:i.pins_we wdata.:[12, 8] -- "rxp" in
  let en = bit cfg 0 in
  let code = cfg.:[2, 1] in
  let k = cfg.:[5, 3] in
  let ones = bit cfg 6 in
  let diff = bit cfg 7 in
  let endm = cfg.:[9, 8] in
  let endb = cfg.:[12, 10] in
  let idle = bit cfg 13 in
  let rx_en = bit cfg 14 in
  let nrzi = code ==:. 1 in
  let manch = code ==:. 2 in
  let spb = mux2 manch (of_int_trunc ~width:4 2) (of_int_trunc ~width:4 1) in
  let p = div.:[31, 16] in
  (* ---- state: cleared at reset and when LINECFG is written with EN = 0 *)
  let st =
    spec ~clock:i.clk ~reset:i.rst_n ~reset_edge:Edge.Falling
      ~clear:(i.cfg_we &: ~:(bit wdata 0)) ()
  in
  let reg name width =
    let v = Always.Variable.reg st ~width in
    ignore (v.value -- name : Signal.t);
    v
  in
  let e_data0 = reg "e_data0" 32 in
  let e_data1 = reg "e_data1" 32 in
  let e_n0 = reg "e_n0" 6 in
  let e_n1 = reg "e_n1" 6 in
  let e_pos = reg "e_pos" 6 in
  let e_msb0 = reg "e_msb0" 1 in
  let e_msb1 = reg "e_msb1" 1 in
  let e_cnt = reg "e_cnt" 2 in
  let tx_on = reg "tx_on" 1 in
  let eop_idle = reg "eop_idle" 1 in
  let half = reg "half" 1 in
  let bit_q = reg "bit_q" 1 in
  let level = reg "level" 1 in
  let txn = reg "txn" 1 in
  let ending = reg "ending" 4 in
  let tcnt = reg "tcnt" 16 in
  let tfacc = reg "tfacc" 8 in
  let trun = reg "trun" 3 in
  let trunbit = reg "trunbit" 1 in
  let trunv = reg "trunv" 1 in
  let rq = reg "rq" 32 in
  let rcount = reg "rcount" 6 in
  let rx_on = reg "rx_on" 1 in
  let rlast = reg "rlast" 1 in
  let rprev = reg "rprev" 1 in
  let end_pend = reg "end_pend" 1 in
  let ovf = reg "ovf" 1 in
  let rcnt = reg "rcnt" 16 in
  let q = reg "q" 21 in
  let mn = reg "mn" 6 in
  let quiet = reg "quiet" 4 in
  let rrun = reg "rrun" 3 in
  let rrunbit = reg "rrunbit" 1 in
  let rrunv = reg "rrunv" 1 in
  (* ---- core 0 view *)
  let room = en &: (e_cnt.value <>:. 2) in
  let have = rcount.value >=: i.in_n in
  let in_ok = en &: (have |: end_pend.value) in
  let in_k =
    mux2 en (mux2 have i.in_n (mux2 end_pend.value rcount.value (zero 6))) (zero 6)
  in
  let in_t = en &: ~:have &: end_pend.value in
  let in_val =
    mux2
      i.in_shr
      (rq.value &: lowmask in_k)
      (shift_right (reverse rq.value) (of_int_trunc ~width:6 32 -: in_k))
  in
  (* ---- pins *)
  let txn_pin = txp +:. 1 in
  let driving = en &: tx_on.value in
  let one32 = of_int_trunc ~width:32 1 in
  let drv_mask =
    mux2
      ~:driving
      (zero 32)
      (shift_left one32 txp |: mux2 diff (shift_left one32 txn_pin) (zero 32))
  in
  let drv_val =
    mux2
      ~:driving
      (zero 32)
      (shift_left (uresize level.value ~width:32) txp
       |: mux2 diff (shift_left (uresize txn.value ~width:32) txn_pin) (zero 32))
  in
  (* ---- next state: shared terms *)
  let head_idx = mux2 e_msb0.value (e_n0.value -:. 1 -: e_pos.value) e_pos.value in
  let head_bit = mux head_idx.:[4, 0] (bits_lsb e_data0.value) in
  let rx = mux rxp (bits_lsb i.pins_in) in
  let rxn = mux (rxp +:. 1) (bits_lsb i.pins_in) in
  let trans = rx <>: rprev.value in
  (* Manchester timing, 1/64 half-cycle units *)
  let p12 = p.:[11, 0] in
  let m_bit = zero 2 @: p12 @: zero 7 in
  let m_win = zero 4 @: p12 @: zero 5 in
  let m_wide = (zero 4 @: p12 @: zero 5) +: (zero 5 @: p12 @: zero 4) in
  let m_end = zero 3 @: p12 @: zero 6 in
  let edge1 = i.rx_mid <>: rprev.value in
  let edge2 = rx <>: i.rx_mid in
  let m_age = mux2 edge1 (of_int_trunc ~width:21 96) (of_int_trunc ~width:21 32) in
  let m_q = q.value +:. 128 in
  let m_e = m_q -: m_age in
  let m_in =
    (edge1 |: edge2) &: (m_e >=+ negate (mux2 (mn.value <:. 32) m_wide m_win))
  in
  let r_load =
    (gnd @: p.:[15, 1])
    -: mux2 edge1 (of_int_trunc ~width:16 2) (of_int_trunc ~width:16 1)
  in
  let m_g =
    mux2
      (mn.value <:. 6)
      (of_int_trunc ~width:3 1)
      (mux2
         (mn.value <:. 12)
         (of_int_trunc ~width:3 2)
         (mux2
            (mn.value <:. 18)
            (of_int_trunc ~width:3 3)
            (mux2 (mn.value <:. 24) (of_int_trunc ~width:3 4) (of_int_trunc ~width:3 5))))
  in
  let m_r =
    shift_right_arith
      (m_e +: shift_left (of_int_trunc ~width:21 1) (m_g -:. 1))
      m_g
  in
  (* ---- transmitter, stage 1 *)
  let fs = (gnd @: tfacc.value) +: (gnd @: div.:[7, 0]) in
  let tload = uresize div.:[15, 8] ~width:16 -:. 1 +: uresize (bit fs 8) ~width:16 in
  let wire d = Always.Variable.wire ~default:d in
  let s_tx_on = wire tx_on.value in
  let s_eop = wire eop_idle.value in
  let s_half = wire half.value in
  let s_level = wire level.value in
  let s_txn = wire txn.value in
  let s_ending = wire ending.value in
  let s_tcnt = wire tcnt.value in
  let s_tfacc = wire tfacc.value in
  let s_trun = wire trun.value in
  let s_trunv = wire trunv.value in
  let do_next = wire gnd in
  Always.(
    compile
      [ when_
          en
          [ if_
              ~:(tx_on.value)
              [ when_
                  (e_cnt.value <>:. 0)
                  [ s_tx_on <-- vdd
                  ; s_level <-- idle
                  ; s_txn <-- ~:idle
                  ; s_trun <-- zero 3
                  ; s_trunv <-- gnd
                  ; s_ending <-- zero 4
                  ; s_eop <-- gnd
                  ; s_tfacc <-- fs.:[7, 0]
                  ; s_tcnt <-- tload
                  ; do_next <-- vdd
                  ]
              ]
            @@ elif
                 (tcnt.value <>:. 0)
                 [ s_tcnt <-- tcnt.value -:. 1 ]
                 [ s_tfacc <-- fs.:[7, 0]
                 ; s_tcnt <-- tload
                 ; if_
                     (ending.value <>:. 0)
                     [ s_ending <-- ending.value -:. 1
                     ; when_
                         (ending.value ==:. 1)
                         [ if_
                             ((endm ==:. 1) &: ~:(eop_idle.value))
                             [ s_eop <-- vdd
                             ; s_ending <-- spb
                             ; s_level <-- idle
                             ; s_txn <-- ~:idle
                             ]
                             [ s_tx_on <-- gnd ]
                         ]
                     ]
                   @@ elif
                        (manch &: ~:(half.value))
                        [ s_half <-- vdd
                        ; s_level <-- bit_q.value
                        ; s_txn <-- ~:(bit_q.value)
                        ]
                        [ do_next <-- vdd ]
                 ]
          ]
      ]);
  (* ---- transmitter, stage 2: [if (do_next)] *)
  let n_tx_on = wire s_tx_on.value in
  let n_half = wire s_half.value in
  let n_bit = wire bit_q.value in
  let n_level = wire s_level.value in
  let n_txn = wire s_txn.value in
  let n_ending = wire s_ending.value in
  let n_trun = wire s_trun.value in
  let n_trunbit = wire trunbit.value in
  let n_trunv = wire s_trunv.value in
  let pop = wire gnd in
  let got = wire gnd in
  let b = wire gnd in
  let trun1 = s_trun.value in
  let trun_sat = saturating_inc3 trun1 in
  let one3 = of_int_trunc ~width:3 1 in
  let got_level =
    mux2 nrzi (mux2 b.value s_level.value ~:(s_level.value)) (mux2 manch ~:(b.value) b.value)
  in
  Always.(
    compile
      [ when_
          do_next.value
          [ if_
              ((k <>:. 0) &: (trun1 ==: k))
              [ b <-- mux2 ones gnd ~:(trunbit.value)
              ; got <-- vdd
              ; if_
                  ones
                  [ n_trun <-- zero 3 ]
                  [ n_trun <-- one3
                  ; n_trunbit <-- ~:(trunbit.value)
                  ; n_trunv <-- vdd
                  ]
              ]
            @@ elif
                 (e_cnt.value <>:. 0)
                 [ b <-- head_bit
                 ; pop <-- vdd
                 ; got <-- vdd
                 ; if_
                     ones
                     [ n_trun <-- mux2 head_bit trun_sat (zero 3) ]
                     [ n_trun
                       <-- mux2
                             (s_trunv.value &: (head_bit ==: trunbit.value))
                             trun_sat
                             one3
                     ; n_trunbit <-- head_bit
                     ; n_trunv <-- vdd
                     ]
                 ]
            @@ elif
                 ((endb ==:. 0) |: (endm ==:. 0))
                 [ n_tx_on <-- gnd ]
                 [ n_ending <-- (uresize endb ~width:4 *: spb).:[3, 0]
                 ; if_
                     (endm ==:. 1)
                     [ n_level <-- gnd; n_txn <-- gnd ]
                     [ when_ (endm ==:. 2) [ n_level <-- vdd; n_txn <-- gnd ] ]
                 ]
          ; when_
              got.value
              [ n_bit <-- b.value
              ; when_ (~:nrzi &: manch) [ n_half <-- gnd ]
              ; n_level <-- got_level
              ; n_txn <-- ~:got_level
              ]
          ]
      ]);
  (* ---- receiver *)
  let n_rx_on = wire rx_on.value in
  let n_rlast = wire rlast.value in
  let n_rcnt = wire rcnt.value in
  let n_q = wire q.value in
  let n_mn = wire mn.value in
  let n_quiet = wire quiet.value in
  let n_rrun = wire rrun.value in
  let n_rrunbit = wire rrunbit.value in
  let n_rrunv = wire rrunv.value in
  let new_v = wire gnd in
  let new_b = wire gnd in
  let new_end = wire gnd in
  let apply (d : delivered) =
    Always.(
      proc
        [ n_rrun <-- d.d_run
        ; n_rrunbit <-- d.d_runbit
        ; n_rrunv <-- d.d_runv
        ; when_ d.d_off [ n_rx_on <-- gnd; new_end <-- vdd ]
        ; new_v <-- d.d_v
        ; new_b <-- d.d_b
        ])
  in
  let deliver ~run ~runv ~bit_in =
    deliver ~k ~ones ~run ~runbit:rrunbit.value ~runv ~bit_in
  in
  let d_first = deliver ~run:(zero 3) ~runv:gnd ~bit_in:rx in
  let d_manch = deliver ~run:rrun.value ~runv:rrunv.value ~bit_in:rx in
  let d_nrz =
    deliver
      ~run:rrun.value
      ~runv:rrunv.value
      ~bit_in:(mux2 nrzi (rx ==: rlast.value) rx)
  in
  let quiet1 = mux2 (quiet.value ==:. 15) (of_int_trunc ~width:4 15) (quiet.value +:. 1) in
  let quiet_limit =
    mux2 (k <>:. 0) ((gnd @: k) +:. 2) (of_int_trunc ~width:4 8)
  in
  let m_acc_q = m_q -: m_bit -: m_r in
  Always.(
    compile
      [ when_
          (en &: rx_en)
          [ if_
              ~:(rx_on.value)
              [ when_
                  trans
                  [ n_rx_on <-- vdd
                  ; n_rrun <-- zero 3
                  ; n_rrunv <-- gnd
                  ; n_quiet <-- zero 4
                  ; if_
                      manch
                      [ n_q <-- m_age -: m_bit; n_mn <-- zero 6; apply d_first ]
                      [ n_rlast <-- rprev.value; n_rcnt <-- r_load ]
                  ]
              ]
            @@ elif
                 manch
                 [ if_
                     m_in
                     [ n_q <-- m_acc_q
                     ; n_mn
                       <-- mux2 (mn.value ==:. 32) (of_int_trunc ~width:6 32) (mn.value +:. 1)
                     ; apply d_manch
                     ]
                   @@ elif
                        (m_q >=+ m_end)
                        [ n_rx_on <-- gnd; new_end <-- vdd ]
                        [ n_q <-- m_q ]
                 ]
                 [ if_
                     trans
                     [ n_rcnt <-- r_load; n_quiet <-- zero 4 ]
                   @@ elif
                        (rcnt.value ==:. 0)
                        [ n_rcnt <-- p -:. 1
                        ; if_
                            (diff &: ~:rx &: ~:rxn)
                            [ n_rx_on <-- gnd; new_end <-- vdd ]
                            [ n_quiet <-- quiet1
                            ; if_
                                (quiet1 >=: quiet_limit)
                                [ n_rx_on <-- gnd; new_end <-- vdd ]
                                [ n_rlast <-- rx; apply d_nrz ]
                            ]
                        ]
                        [ n_rcnt <-- rcnt.value -:. 1 ]
                 ]
          ]
      ]);
  (* ---- registers *)
  let take_ok = i.take &: in_ok in
  let taken = mux2 take_ok in_k (zero 6) in
  let rq_shift = shift_right rq.value taken in
  let rcount1 = rcount.value -: taken in
  let head_done = pop.value &: (e_pos.value +:. 1 ==: e_n0.value) in
  let e_cnt1 = e_cnt.value -: uresize head_done ~width:2 in
  Always.(
    compile
      [ when_
          en
          [ tx_on <-- n_tx_on.value
          ; eop_idle <-- s_eop.value (* only stage 1 writes n_eop *)
          ; half <-- n_half.value
          ; bit_q <-- n_bit.value
          ; level <-- n_level.value
          ; txn <-- n_txn.value
          ; ending <-- n_ending.value
          ; tcnt <-- s_tcnt.value
          ; tfacc <-- s_tfacc.value
          ; trun <-- n_trun.value
          ; trunbit <-- n_trunbit.value
          ; trunv <-- n_trunv.value
          ; (* TX queue: the unit's pop, then core 0's push behind what is left *)
            if_
              head_done
              [ e_data0 <-- e_data1.value
              ; e_n0 <-- e_n1.value
              ; e_msb0 <-- e_msb1.value
              ; e_pos <-- zero 6
              ]
              [ when_ pop.value [ e_pos <-- e_pos.value +:. 1 ] ]
          ; when_
              i.push
              [ if_
                  (e_cnt1 ==:. 0)
                  [ e_data0 <-- i.push_data
                  ; e_n0 <-- i.push_n
                  ; e_msb0 <-- i.push_msb
                  ; e_pos <-- zero 6
                  ]
                  [ e_data1 <-- i.push_data
                  ; e_n1 <-- i.push_n
                  ; e_msb1 <-- i.push_msb
                  ]
              ]
          ; e_cnt <-- e_cnt1 +: uresize i.push ~width:2
          ; (* receiver *)
            rx_on <-- n_rx_on.value
          ; rlast <-- n_rlast.value
          ; rcnt <-- n_rcnt.value
          ; q <-- n_q.value
          ; mn <-- n_mn.value
          ; quiet <-- n_quiet.value
          ; rrun <-- n_rrun.value
          ; rrunbit <-- n_rrunbit.value
          ; rrunv <-- n_rrunv.value
          ; rprev <-- rx
          ; if_
              (new_v.value &: (rcount.value <:. 32))
              [ rq
                <-- (rq_shift |: shift_left (uresize new_b.value ~width:32) rcount1)
              ; rcount <-- rcount1 +:. 1
              ]
              [ rq <-- rq_shift; rcount <-- rcount1 ]
          ; when_ (new_v.value &: (rcount.value >=:. 32)) [ ovf <-- vdd ]
          ; if_
              new_end.value
              [ end_pend <-- vdd ]
              [ when_ (i.take &: in_ok &: in_t) [ end_pend <-- gnd ] ]
          ]
      ]);
  let r_stat =
    zero 12
    @: ovf.value
    @: end_pend.value
    @: rx_on.value
    @: tx_on.value
    @: zero 2
    @: rcount.value
    @: zero 6
    @: e_cnt.value
  in
  { O.r_stat; rx_pin = rxp; drv_mask; drv_val; room; in_ok; in_k; in_t; in_val }
;;

let hierarchical ~instance scope i =
  let module H = Hierarchy.In_scope (I) (O) in
  H.hierarchical ~config:circuit_config ~scope ~name:"pe_line" ~instance create i
;;
