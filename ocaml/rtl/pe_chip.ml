(* Chip core: port of src/pe_chip.sv. Everything except the host SPI slave.
   Register map: docs/isa.md section 6.2. *)
open! Base
open Hardcaml
open Signal
open Prims

module I = struct
  type 'a t =
    { clk : 'a
    ; rst_n : 'a
    ; ui_in : 'a [@bits 5]
    ; uio_in : 'a [@bits 8]
    ; bus_addr : 'a [@bits 16]
    ; bus_we : 'a
    ; bus_wdata : 'a [@bits 32]
    ; bus_commit : 'a
    ; bus_snap : 'a
    ; bus_cancel : 'a
    }
  [@@deriving hardcaml]
end

module O = struct
  type 'a t =
    { uo_out : 'a [@bits 7]
    ; uio_out : 'a [@bits 8]
    ; uio_oe : 'a [@bits 8]
    ; bus_rdata : 'a [@bits 32]
    ; quad_en : 'a
    }
  [@@deriving hardcaml]
end

let id_value = 0x5045_0002

let create ~(cfg : Config.t) scope (i : _ I.t) =
  let w = i.bus_wdata in
  (* ---- bus decode *)
  let addr = i.bus_addr in
  let sel_imem = addr.:[15, 10] ==:. 0 in
  let sel_core = addr.:[15, 7] ==:. 0x008 in
  let sel_glob = addr.:[15, 4] ==:. 0x080 in
  let core_idx = addr.:[6, 5] in
  let goff = addr.:[3, 0] in
  let glob_we g = i.bus_we &: sel_glob &: (goff ==:. g) in
  let ctrl_w = glob_we 1 in
  (* ---- state *)
  let dom = domain ~clock:i.clk ~rst_n:i.rst_n in
  let st name width = state dom name width in
  let enable = st "enable" 4 in
  let hostcfg = st "hostcfg" 1 in
  let flags = st "flags" 8 in
  let pin_out = st "pin_out" 32 in
  let pin_oe = st "pin_oe" 32 in
  let sync1 = st "sync1" 13 in
  let sync2 = st "sync2" 13 in
  let ctrl_pend = st "ctrl_pend" 1 in
  let ctrl_data = st "ctrl_data" 12 in
  (* ---- config registers: clock-gated *)
  let gcfg_gclk =
    Array.init 4 ~f:(fun gi ->
      cg ~instance:(Printf.sprintf "g_gcfg_cg_%d_u_cg" gi) ~clk:i.clk ~en:(glob_we (4 + gi)))
  in
  let gcfg g name d = gated ~clock:gcfg_gclk.(g) ~rst_n:i.rst_n d -- name in
  let sync_byp = gcfg 0 "sync_byp" (w.:[20, 16] @: w.:[7, 0]) in
  let pin_owner_lo = gcfg 1 "pin_owner_lo" w in
  let pin_owner_hi = gcfg 2 "pin_owner_hi" w in
  let od_mask = gcfg 3 "od_mask" w.:[7, 0] in
  let pin_owner = pin_owner_hi @: pin_owner_lo in
  let pad_in = (sync2.value &: ~:sync_byp) |: (sync1.value &: sync_byp) in
  (* pins as driven: the registers, with the line unit's transmit pins over them *)
  let line = Pe_line.O.Of_signal.wires () in
  let eff_out = (pin_out.value &: ~:(line.drv_mask)) |: (line.drv_val &: line.drv_mask) in
  let eff_oe = pin_oe.value |: line.drv_mask in
  let pins_in =
    eff_out.:[31, 24] @: zero 3 @: pad_in.:[12, 8] @: gnd @: eff_out.:[14, 8] @: pad_in.:[7, 0]
  in
  let restart = mux2 ctrl_pend.value ctrl_data.value.:[7, 4] (zero 4) in
  let fclear = mux2 ctrl_pend.value ctrl_data.value.:[11, 8] (zero 4) in
  (* ---- IMEM *)
  let fetch_wires = List.init 4 ~f:(fun _ -> wire 10) in
  let imem =
    Pe_imem.hierarchical
      ~instance:"u_imem"
      ~cfg
      scope
      { Pe_imem.I.clk = i.clk
      ; fetch_addr = concat_lsb fetch_wires
      ; we = i.bus_we &: sel_imem
      ; waddr = addr.:[9, 0]
      ; wdata = i.bus_wdata
      }
  in
  (* ---- cores *)
  let cores =
    List.init 4 ~f:(fun c ->
      if c < cfg.ncores
      then (
        let on0 s = if c = 0 then s else zero (width s) in
        Some
          (Pe_core.hierarchical
             ~instance:(Printf.sprintf "u_core_%d" c)
             ~idx:c
             ~fifo_log2:cfg.fifo_log2
             ~fifo_no_cg:cfg.fifo_no_cg
             scope
             { Pe_core.I.clk = i.clk
             ; rst_n = i.rst_n
             ; enable = bit enable.value c
             ; restart = bit restart c
             ; fifo_clear = bit fclear c
             ; instr = imem.bank_rdata.:[(32 * c) + 31, 32 * c]
             ; pins_in
             ; pin_out_cur = pin_out.value
             ; flags = flags.value
             ; bus_sel = sel_core &: (core_idx ==:. c)
             ; bus_off = addr.:[4, 0]
             ; bus_we = i.bus_we
             ; bus_wdata = i.bus_wdata
             ; bus_commit = i.bus_commit
             ; bus_snap = i.bus_snap
             ; bus_cancel = i.bus_cancel
             ; line_room = on0 line.room
             ; line_ok = on0 line.in_ok
             ; line_k = on0 line.in_k
             ; line_t = on0 line.in_t
             ; line_val = on0 line.in_val
             }))
      else None)
  in
  let of_core ~off (f : _ Pe_core.O.t -> Signal.t) =
    List.map cores ~f:(function
      | Some o -> f o
      | None -> zero off)
  in
  List.iter2_exn fetch_wires (of_core ~off:10 (fun o -> o.fetch_addr)) ~f:( <== );
  let w_out_en = of_core ~off:32 (fun o -> o.wr_out_en) in
  let w_out_val = of_core ~off:32 (fun o -> o.wr_out_val) in
  let w_oe_en = of_core ~off:32 (fun o -> o.wr_oe_en) in
  let w_oe_val = of_core ~off:32 (fun o -> o.wr_oe_val) in
  let c_fset = of_core ~off:8 (fun o -> o.flag_set) in
  let c_fclr = of_core ~off:8 (fun o -> o.flag_clr) in
  let c_rdata = of_core ~off:32 (fun o -> o.bus_rdata) in
  let core0 = Option.value_exn (List.hd_exn cores) in
  (* ---- the line unit's falling-edge RX sample *)
  let rxp = line.rx_pin in
  let rx_is_pad =
    (rxp.:[4, 3] ==:. 0) |: ((rxp.:[4, 3] ==:. 2) &: (rxp.:[2, 0] <=:. 4))
  in
  let ui8 = zero 3 @: i.ui_in in
  let rx_pad =
    mux2 (bit rxp 4) (mux rxp.:[2, 0] (bits_lsb ui8)) (mux rxp.:[2, 0] (bits_lsb i.uio_in))
  in
  let neg_spec =
    spec
      ~clock:i.clk
      ~clock_edge:Edge.Falling
      ~reset:i.rst_n
      ~reset_edge:Edge.Falling
      ()
  in
  let rx_n1 = async_reg (reg neg_spec (rx_is_pad &: rx_pad)) -- "rx_n1" in
  let rx_hq = async_reg (dff dom rx_n1) -- "rx_hq" in
  let rx_h2 = async_reg (dff dom rx_hq) -- "rx_h2" in
  let byp_ui = zero 3 @: sync_byp.:[12, 8] in
  let rx_byp =
    mux2 (bit rxp 4) (mux rxp.:[2, 0] (bits_lsb byp_ui)) (mux rxp.:[2, 0] (bits_lsb sync_byp.:[7, 0]))
  in
  let line_rx_mid =
    mux2 rx_is_pad (mux2 rx_byp rx_hq rx_h2) (mux rxp (bits_lsb pins_in))
  in
  (* ---- line unit (core 0) *)
  let line_o =
    Pe_line.hierarchical
      ~instance:"u_line"
      scope
      { Pe_line.I.clk = i.clk
      ; rst_n = i.rst_n
      ; cfg_we = glob_we 0xC
      ; div_we = glob_we 0xD
      ; pins_we = glob_we 0xE
      ; wdata = i.bus_wdata
      ; pins_in
      ; rx_mid = line_rx_mid
      ; push = core0.line_push
      ; push_data = core0.line_push_data
      ; push_n = core0.line_push_n
      ; push_msb = core0.line_push_msb
      ; in_n = core0.line_in_n
      ; in_shr = core0.line_in_shr
      ; take = core0.line_take
      }
  in
  Pe_line.O.Of_signal.assign line line_o;
  (* ---- pins *)
  let owner p = pin_owner.:[(2 * p) + 1, 2 * p] in
  let per_pin p ~en ~value ~cur =
    let pick l = mux (owner p) (List.map l ~f:(fun s -> bit s p)) in
    mux2 (pick en) (pick value) (bit cur p)
  in
  let pin_out_n =
    concat_lsb
      (List.init 32 ~f:(fun p ->
         per_pin p ~en:w_out_en ~value:w_out_val ~cur:pin_out.value))
  in
  let pin_oe_n =
    concat_lsb
      (List.init 32 ~f:(fun p -> per_pin p ~en:w_oe_en ~value:w_oe_val ~cur:pin_oe.value))
  in
  let uo_out = eff_out.:[14, 8] in
  let uio_out = eff_out.:[7, 0] &: ~:od_mask in
  let uio_oe = (eff_oe.:[7, 0] &: ~:od_mask) |: (~:(eff_out.:[7, 0]) &: od_mask) in
  (* ---- flags *)
  let host_fset = mux2 (glob_we 2) w.:[7, 0] (zero 8) in
  let host_fclr = mux2 (glob_we 3) w.:[7, 0] (zero 8) in
  let all_set = List.fold c_fset ~init:host_fset ~f:( |: ) in
  let all_clr = List.fold c_fclr ~init:host_fclr ~f:( |: ) in
  (* ---- bus read *)
  let z32 = zero 32 in
  let glob_read =
    mux
      goff
      [ of_int_trunc ~width:32 id_value
      ; zero 28 @: enable.value
      ; zero 24 @: flags.value
      ; zero 24 @: flags.value
      ; z32
      ; z32
      ; z32
      ; z32
      ; pins_in
      ; pin_out.value
      ; pin_oe.value
      ; z32
      ; z32
      ; z32
      ; z32
      ; line.r_stat
      ]
  in
  let bus_rdata = mux2 sel_core (mux core_idx c_rdata) (mux2 sel_glob glob_read z32) in
  (* ---- sequential *)
  Always.(
    compile
      [ sync1 <-- (i.ui_in @: i.uio_in)
      ; sync2 <-- sync1.value
      ; pin_out <-- mux2 (glob_we 9) w pin_out_n
      ; pin_oe <-- mux2 (glob_we 0xA) w pin_oe_n
      ; when_ (glob_we 0xB) [ hostcfg <-- bit w 0 ]
      ; flags <-- ((flags.value &: ~:all_clr) |: all_set)
      ; ctrl_pend <-- ctrl_w
      ; ctrl_data <-- (w.:[19, 16] @: w.:[11, 8] @: w.:[3, 0])
      ; when_ ctrl_pend.value [ enable <-- ctrl_data.value.:[3, 0] ]
      ]);
  { O.uo_out; uio_out; uio_oe; bus_rdata; quad_en = hostcfg.value }
;;

let hierarchical ~instance ~cfg ~name scope i =
  let module H = Hierarchy.In_scope (I) (O) in
  H.hierarchical ~config:circuit_config ~scope ~name ~instance (create ~cfg) i
;;
