(* Host port: port of src/pe_hostport.sv (docs/isa.md section 6.1). SPI mode 0,
   source-synchronous, 1-bit or quad. Four clock domains, as in the Verilog:
     sck rising,  reset by CS_n high  (the parser)
     sck rising,  reset by rst_n      (hold registers and toggles)
     sck falling, reset by CS_n high  (output shifter)
     clk rising,  reset by rst_n      (bus side)
   The [ifdef FORMAL] block is not ported. *)
open! Base
open Hardcaml
open Signal
open Prims

module I = struct
  type 'a t =
    { clk : 'a
    ; rst_n : 'a
    ; sck : 'a
    ; cs_n : 'a
    ; io_in : 'a [@bits 4]
    ; quad_en : 'a
    ; bus_rdata : 'a [@bits 32]
    }
  [@@deriving hardcaml]
end

module O = struct
  type 'a t =
    { io_out : 'a [@bits 4]
    ; io_oe : 'a
    ; bus_addr : 'a [@bits 16]
    ; bus_we : 'a
    ; bus_wdata : 'a [@bits 32]
    ; bus_snap : 'a
    ; bus_commit : 'a
    ; bus_cancel : 'a
    }
  [@@deriving hardcaml]
end

let ph_cmd = 0
let ph_addr = 1
let ph_dummy = 2
let ph_data = 3
let ph_ignore = 4

let create _scope (i : _ I.t) =
  let k6 = of_int_trunc ~width:6 in
  let k3 = of_int_trunc ~width:3 in
  (* ---- SCK domain, reset while CS_n is high *)
  let sck_cs = spec ~clock:i.sck ~reset:i.cs_n ~reset_edge:Edge.Rising () in
  let parse name width = named (Always.Variable.reg sck_cs ~width) name in
  let phase = parse "phase" 3 in
  let cnt = parse "cnt" 6 in
  let cmd = parse "cmd" 8 in
  let quad = parse "quad" 1 in
  let rd = parse "rd" 1 in
  let inc = parse "inc" 1 in
  let sh = parse "sh" 32 in
  let wpar = parse "wpar" 1 in
  let sh_n1 = sh.value.:[30, 0] @: bit i.io_in 3 in
  let sh_n4 = sh.value.:[27, 0] @: i.io_in in
  let sh_n = mux2 quad.value sh_n4 sh_n1 in
  let cmd_n = cmd.value.:[6, 0] @: bit i.io_in 3 in
  let hi = cmd_n.:[7, 4] in
  let lo = cmd_n.:[3, 0] in
  let cmd_ok =
    ((hi ==:. 0) |: ((hi ==:. 4) &: i.quad_en))
    &: ((lo ==:. 2) |: (lo ==:. 0xA) |: (lo ==:. 3) |: (lo ==:. 0xB))
  in
  let addr_len = mux2 quad.value (k6 3) (k6 15) in
  let word_len = mux2 quad.value (k6 7) (k6 31) in
  Always.(
    compile
      [ cnt <-- cnt.value +:. 1
      ; when_
          (phase.value ==:. ph_cmd)
          [ cmd <-- cmd_n
          ; when_
              (cnt.value ==:. 7)
              [ cnt <-- k6 0
              ; phase <-- mux2 cmd_ok (k3 ph_addr) (k3 ph_ignore)
              ; quad <-- bit cmd_n 6
              ; rd <-- bit cmd_n 0
              ; inc <-- ~:(bit cmd_n 3)
              ]
          ]
      ; when_
          (phase.value ==:. ph_addr)
          [ sh <-- sh_n
          ; when_
              (cnt.value ==: addr_len)
              [ cnt <-- k6 0; phase <-- mux2 rd.value (k3 ph_dummy) (k3 ph_data) ]
          ]
      ; when_
          ((phase.value ==:. ph_dummy) &: (cnt.value ==:. 7))
          [ cnt <-- k6 0; phase <-- k3 ph_data ]
      ; when_
          (phase.value ==:. ph_data)
          [ sh <-- sh_n
          ; when_ (cnt.value ==: word_len) [ cnt <-- k6 0; wpar <-- ~:(wpar.value) ]
          ]
      ]);
  (* ---- hold registers and toggles: not reset by CS_n *)
  let sck_rst = domain ~clock:i.sck ~rst_n:i.rst_n in
  let hold name width = state sck_rst name width in
  let a_hold = hold "a_hold" 16 in
  let m_hold = hold "m_hold" 2 in
  let w_hold = hold "w_hold" 32 in
  let a_tog = hold "a_tog" 1 in
  let w_tog = hold "w_tog" 1 in
  let r_tog = hold "r_tog" 1 in
  let in_data = phase.value ==:. ph_data in
  Always.(
    compile
      [ when_
          ~:(i.cs_n)
          [ when_
              ((phase.value ==:. ph_addr) &: (cnt.value ==: addr_len))
              [ a_hold <-- sh_n.:[15, 0]
              ; m_hold <-- (rd.value @: inc.value)
              ; a_tog <-- ~:(a_tog.value)
              ]
          ; when_
              (in_data &: (cnt.value ==: word_len))
              [ if_
                  rd.value
                  [ r_tog <-- ~:(r_tog.value) ]
                  [ w_hold <-- sh_n; w_tog <-- ~:(w_tog.value) ]
              ]
          ]
      ]);
  (* ---- clk domain *)
  let dom = domain ~clock:i.clk ~rst_n:i.rst_n in
  let st name width = state dom name width in
  let buf0 = st "buf0" 32 in
  let buf1 = st "buf1" 32 in
  (* ---- output side: changes on SCK fall *)
  let sck_fall =
    spec
      ~clock:i.sck
      ~clock_edge:Edge.Falling
      ~reset:i.cs_n
      ~reset_edge:Edge.Rising
      ()
  in
  let osh_w = wire 32 in
  let sending = in_data &: rd.value in
  let osh =
    reg
      sck_fall
      ~enable:sending
      (mux2
         (cnt.value ==:. 0)
         (mux2 wpar.value buf1.value buf0.value)
         (mux2 quad.value (osh_w.:[27, 0] @: zero 4) (osh_w.:[30, 0] @: gnd)))
    -- "osh"
  in
  osh_w <== osh;
  let host_ready = st "host_ready" 1 in
  let io_out =
    mux2 sending (mux2 quad.value osh.:[31, 28] (bit osh 31 @: zero 3)) (zero 4)
  in
  let io_oe = host_ready.value &: ~:(i.cs_n) &: sending &: quad.value in
  (* ---- synchronisers *)
  let a_s = async_state dom "a_s" 3 in
  let w_s = async_state dom "w_s" 3 in
  let r_s = async_state dom "r_s" 3 in
  let cs_s = async_state dom "cs_s" 3 in
  Always.(
    compile
      [ when_ (reduce_and cs_s.value) [ host_ready <-- vdd ]
      ; a_s <-- (a_s.value.:[1, 0] @: a_tog.value)
      ; w_s <-- (w_s.value.:[1, 0] @: w_tog.value)
      ; r_s <-- (r_s.value.:[1, 0] @: r_tog.value)
      ; cs_s <-- (cs_s.value.:[1, 0] @: i.cs_n)
      ]);
  let toggled (s : Always.Variable.t) =
    host_ready.value &: (bit s.value 2 <>: bit s.value 1)
  in
  let a_ev = toggled a_s in
  let w_ev = toggled w_s in
  let r_ev = toggled r_s in
  (* ---- read state and bus operations *)
  let active = st "active" 1 in
  let c_rd = st "c_rd" 1 in
  let c_inc = st "c_inc" 1 in
  let snap_addr = st "snap_addr" 16 in
  let com_addr = st "com_addr" 16 in
  let outstanding = st "outstanding" 2 in
  let fill = st "fill" 1 in
  let a_pend = st "a_pend" 1 in
  let w_pend = st "w_pend" 1 in
  let need_commit = st "need_commit" 1 in
  let need_snap = st "need_snap" 1 in
  let bus_addr = st "bus_addr_r" 16 in
  let bus_we = st "bus_we_r" 1 in
  let bus_wdata = st "bus_wdata_r" 32 in
  let bus_snap = st "bus_snap_r" 1 in
  let bus_commit = st "bus_commit_r" 1 in
  let bus_cancel = st "bus_cancel_r" 1 in
  let inc16 = uresize c_inc.value ~width:16 in
  Always.(
    compile
      [ bus_we <-- gnd
      ; bus_snap <-- gnd
      ; bus_commit <-- gnd
      ; bus_cancel <-- gnd
      ; when_
          bus_snap.value
          [ if_ fill.value [ buf1 <-- i.bus_rdata ] [ buf0 <-- i.bus_rdata ]
          ; fill <-- ~:(fill.value)
          ]
      ; when_ a_ev [ a_pend <-- vdd ]
      ; when_ w_ev [ w_pend <-- vdd ]
      ; when_ r_ev [ need_commit <-- vdd ]
      ; if_
          (need_commit.value &: (outstanding.value ==:. 0))
          [ need_commit <-- gnd ]
        @@ elif
             need_commit.value
             [ need_commit <-- gnd
             ; bus_commit <-- vdd
             ; bus_addr <-- com_addr.value
             ; com_addr <-- com_addr.value +: inc16
             ; outstanding <-- outstanding.value -:. 1
             ; need_snap <-- vdd
             ]
        @@ elif
             w_pend.value
             [ w_pend <-- gnd
             ; bus_we <-- vdd
             ; bus_wdata <-- w_hold.value
             ; bus_addr <-- snap_addr.value
             ; snap_addr <-- snap_addr.value +: inc16
             ]
        @@ elif
             ((a_pend.value |: bit cs_s.value 2) &: active.value)
             [ active <-- gnd
             ; need_snap <-- gnd
             ; outstanding <-- zero 2
             ; when_ c_rd.value [ bus_cancel <-- vdd ]
             ]
        @@ elif
             a_pend.value
             [ a_pend <-- gnd
             ; active <-- vdd
             ; c_rd <-- bit m_hold.value 1
             ; c_inc <-- bit m_hold.value 0
             ; snap_addr <-- a_hold.value
             ; com_addr <-- a_hold.value
             ; fill <-- gnd
             ; need_snap <-- bit m_hold.value 1
             ]
        @@ elif
             (need_snap.value
              &: ~:(bus_snap.value)
              &: (outstanding.value <>:. 2)
              &: c_rd.value
              &: active.value)
             [ bus_snap <-- vdd
             ; bus_addr <-- snap_addr.value
             ; snap_addr <-- snap_addr.value +: inc16
             ; outstanding <-- outstanding.value +:. 1
             ; need_snap <-- (outstanding.value ==:. 0)
             ]
             []
      ]);
  { O.io_out
  ; io_oe
  ; bus_addr = bus_addr.value
  ; bus_we = bus_we.value
  ; bus_wdata = bus_wdata.value
  ; bus_snap = bus_snap.value
  ; bus_commit = bus_commit.value
  ; bus_cancel = bus_cancel.value
  }
;;

let hierarchical ~instance scope i =
  let module H = Hierarchy.In_scope (I) (O) in
  H.hierarchical ~config:circuit_config ~scope ~name:"pe_hostport" ~instance create i
;;
