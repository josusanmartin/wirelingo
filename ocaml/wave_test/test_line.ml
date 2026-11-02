(* pe_line: what the transmitter puts on a pin, and what the receiver takes off one.
   Expected pin sequences are worked out from docs/isa.md 4.7, not copied from a run. *)
open! Base
open Hardcaml
open Hardcaml_waveterm
open Wirelingo_rtl
open Bench

module Sim = Cyclesim.With_interface (Pe_line.I) (Pe_line.O)

let pin = 3

(* LINECFG fields *)
let cfg ?(en = 1) ?(code = 0) ?(k = 0) ?(ones = 0) ?(endm = 0) ?(endb = 0) ?(idle = 0) ?(rx_en = 0) () =
  en
  lor (code lsl 1)
  lor (k lsl 3)
  lor (ones lsl 6)
  lor (endm lsl 8)
  lor (endb lsl 10)
  lor (idle lsl 13)
  lor (rx_en lsl 14)
;;

let create () =
  let sim = Sim.create (Pe_line.create (Scope.create ~flatten_design:true ())) in
  let waves, sim = Waveform.create sim in
  let i = Cyclesim.inputs sim in
  let o = Cyclesim.outputs ~clock_edge:Side.Before sim in
  set i.rst_n ~width:1 1;
  waves, sim, i, o
;;

let write sim (i : _ Pe_line.I.t) (we : Bits.t ref) v =
  we := Bits.vdd;
  set i.wdata ~width:32 v;
  Cyclesim.cycle sim;
  we := Bits.gnd;
  set i.wdata ~width:32 0
;;

(* configure, queue one entry, then return what the pin carried while driven *)
let transmit ~config ~clocks_per_bit ~bits ~cycles =
  let waves, sim, i, o = create () in
  write sim i i.div_we (clocks_per_bit lsl 8);
  write sim i i.pins_we pin;
  write sim i i.cfg_we config;
  let data = List.foldi bits ~init:0 ~f:(fun n acc b -> acc lor (b lsl n)) in
  set i.push ~width:1 1;
  set i.push_data ~width:32 data;
  set i.push_n ~width:6 (List.length bits);
  set i.push_msb ~width:1 0;
  Cyclesim.cycle sim;
  set i.push ~width:1 0;
  set i.push_data ~width:32 0;
  set i.push_n ~width:6 0;
  let seen = ref [] in
  for _ = 1 to cycles do
    Cyclesim.cycle sim;
    if (int o.drv_mask lsr pin) land 1 = 1 then seen := ((int o.drv_val lsr pin) land 1) :: !seen
  done;
  (* released at the end, and nothing left queued or on *)
  [%test_result: int] (int o.drv_mask) ~expect:0;
  waves, List.rev !seen
;;

let%expect_test "line TX: NRZ, LSB first, 2 clocks per bit, then release" =
  let waves, seen =
    transmit ~config:(cfg ()) ~clocks_per_bit:2 ~bits:[ 0; 1; 1; 0 ] ~cycles:14
  in
  [%test_result: int list] seen ~expect:[ 0; 0; 1; 1; 1; 1; 0; 0 ];
  print waves [ "push#b"; "push_data#u"; "drv_mask"; "drv_val"; "room#b" ];
  [%expect {|
    ┌Signals───────────┐┌Waves─────────────────────────────────────────────────────────────────────────┐
    │push              ││            ┌───┐                                                             │
    │                  ││────────────┘   └───────────────────────────────────────────────────────      │
    │                  ││────────────┬───┬───────────────────────────────────────────────────────      │
    │push_data         ││ 0          │6  │0                                                            │
    │                  ││────────────┴───┴───────────────────────────────────────────────────────      │
    │                  ││────────────────────┬───────────────────────────────┬───────────────────      │
    │drv_mask          ││ 00000000           │00000008                       │00000000                 │
    │                  ││────────────────────┴───────────────────────────────┴───────────────────      │
    │                  ││────────────────────────────┬───────────────┬───────────────────────────      │
    │drv_val           ││ 00000000                   │00000008       │00000000                         │
    │                  ││────────────────────────────┴───────────────┴───────────────────────────      │
    │room              ││            ┌───────────────────────────────────────────────────────────      │
    │                  ││────────────┘                                                                 │
    └──────────────────┘└──────────────────────────────────────────────────────────────────────────────┘
    |}]
;;

let%expect_test "line TX: Manchester, a 1 is low then high, one END symbol high" =
  let waves, seen =
    transmit
      ~config:(cfg ~code:2 ~endm:2 ~endb:1 ())
      ~clocks_per_bit:2
      ~bits:[ 1; 0 ]
      ~cycles:18
  in
  (* each half 2 clocks; the END symbol is one bit: two halves, all high *)
  [%test_result: int list] seen ~expect:[ 0; 0; 1; 1; 1; 1; 0; 0; 1; 1; 1; 1 ];
  print waves [ "drv_mask"; "drv_val" ];
  [%expect {|
    ┌Signals───────────┐┌Waves─────────────────────────────────────────────────────────────────────────┐
    │                  ││────────────────────┬───────────────────────────────────────────────┬─────────│
    │drv_mask          ││ 00000000           │00000008                                       │00000000 │
    │                  ││────────────────────┴───────────────────────────────────────────────┴─────────│
    │                  ││────────────────────────────┬───────────────┬───────┬───────────────┬─────────│
    │drv_val           ││ 00000000                   │00000008       │000000.│00000008       │00000000 │
    │                  ││────────────────────────────┴───────────────┴───────┴───────────────┴─────────│
    └──────────────────┘└──────────────────────────────────────────────────────────────────────────────┘
    |}]
;;

let%expect_test "line TX: equal-bit stuffing, the stuff bit starts the next run" =
  let bits = [ 1; 1; 1; 1; 1; 0; 0; 0; 0; 1 ] in
  let waves, seen =
    transmit ~config:(cfg ~k:5 ()) ~clocks_per_bit:1 ~bits ~cycles:16
  in
  (* five 1s, stuff 0; that 0 and four more make five, stuff 1; then the data 1 *)
  [%test_result: int list] seen ~expect:[ 1; 1; 1; 1; 1; 0; 0; 0; 0; 0; 1; 1 ];
  print ~wave_width:0 waves [ "drv_mask"; "drv_val" ];
  [%expect {|
    ┌Signals───────────┐┌Waves─────────────────────────────────────────────────────────────────────────┐
    │                  ││──────────┬───────────────────────┬─────                                      │
    │drv_mask          ││ 00000000 │00000008               │0000.                                      │
    │                  ││──────────┴───────────────────────┴─────                                      │
    │                  ││──────────┬─────────┬─────────┬───┬─────                                      │
    │drv_val           ││ 00000000 │00000008 │00000000 │00.│0000.                                      │
    │                  ││──────────┴─────────┴─────────┴───┴─────                                      │
    └──────────────────┘└──────────────────────────────────────────────────────────────────────────────┘
    |}]
;;

let%expect_test "line RX: Manchester, 8 clocks per bit, frame end by silence" =
  let waves, sim, i, o = create () in
  let rx = 1 in
  write sim i i.div_we (8 lsl 16);
  write sim i i.pins_we (rx lsl 8);
  write sim i i.cfg_we (cfg ~code:2 ~rx_en:1 ());
  set i.in_n ~width:6 1;
  set i.in_shr ~width:1 1;
  let sent = [ 1; 0; 1; 1; 0; 0 ] in
  let levels =
    List.init 5 ~f:(fun _ -> 0)
    @ List.concat_map sent ~f:(fun b ->
        List.init 4 ~f:(fun _ -> 1 - b) @ List.init 4 ~f:(fun _ -> b))
    @ List.init 24 ~f:(fun _ -> 0)
  in
  let got = ref [] in
  let ends = ref 0 in
  let take = ref 0 in
  let clock level =
    set i.pins_in ~width:32 (level lsl rx);
    set i.rx_mid ~width:1 level;
    set i.take ~width:1 !take;
    Cyclesim.cycle sim;
    (* o shows this cycle, before the edge: in_ok was up and take consumed it *)
    let offered = int o.in_ok = 1 in
    if !take = 1 && offered
    then (
      if int o.in_k = 1 then got := int o.in_val :: !got;
      if int o.in_t = 1 then Int.incr ends;
      take := 0)
    else take := Bool.to_int offered
  in
  List.iter levels ~f:clock;
  clock 0;
  clock 0;
  [%test_result: int list] (List.rev !got) ~expect:sent;
  [%test_result: int] !ends ~expect:1;
  [%test_result: int] (int o.r_stat land 0xFFFF_0000) ~expect:0;
  print ~wave_width:0 ~display_width:120 waves [ "rx_mid#b"; "in_ok#b"; "in_val#u"; "take#b" ];
  [%expect {|
    ┌Signals───────────┐┌Waves─────────────────────────────────────────────────────────────────────────────────────────────┐
    │rx_mid            ││                        ┌───────────────┐               ┌───────┐       ┌───────────────┐       ┌─│
    │                  ││────────────────────────┘               └───────────────┘       └───────┘               └───────┘ │
    │in_ok             ││                          ┌───┐           ┌───┐           ┌───┐           ┌───┐           ┌───┐   │
    │                  ││──────────────────────────┘   └───────────┘   └───────────┘   └───────────┘   └───────────┘   └───│
    │                  ││──────────────────────────┬───┬───────────────────────────┬───┬───────────┬───┬───────────────────│
    │in_val            ││ 0                        │1  │0                          │1  │0          │1  │0                  │
    │                  ││──────────────────────────┴───┴───────────────────────────┴───┴───────────┴───┴───────────────────│
    │take              ││                            ┌─┐             ┌─┐             ┌─┐             ┌─┐             ┌─┐   │
    │                  ││────────────────────────────┘ └─────────────┘ └─────────────┘ └─────────────┘ └─────────────┘ └───│
    └──────────────────┘└──────────────────────────────────────────────────────────────────────────────────────────────────┘
    |}]
;;
