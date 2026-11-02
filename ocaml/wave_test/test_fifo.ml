(* pe_fifo and the pe_fifos join, in the PE_FIFO_NO_CG build (see bench.ml) *)
open! Base
open Hardcaml
open Hardcaml_waveterm
open Wirelingo_rtl
open Bench

let scope () = Scope.create ~flatten_design:true ()

module F = struct
  module Sim = Cyclesim.With_interface (Pe_fifo.I) (Pe_fifo.O)

  let create () =
    let sim = Sim.create (Pe_fifo.create ~log2:2 ~no_cg:true (scope ())) in
    let waves, sim = Waveform.create sim in
    let i = Cyclesim.inputs sim in
    let o = Cyclesim.outputs ~clock_edge:Side.Before sim in
    set i.rst_n ~width:1 1;
    (* one clock with these inputs: returns (head, level) as they were before the edge *)
    let step ?(push = None) ?(pop = false) ?(clear = false) () =
      set i.push ~width:1 (Bool.to_int (Option.is_some push));
      set i.push_data ~width:32 (Option.value push ~default:0);
      set i.pop ~width:1 (Bool.to_int pop);
      set i.clear ~width:1 (Bool.to_int clear);
      Cyclesim.cycle sim;
      int o.head, int o.level
    in
    waves, step
  ;;
end

let%expect_test "fifo: full and empty are judged on start-of-cycle state" =
  let waves, step = F.create () in
  let push v = ignore (step ~push:(Some v) () : int * int) in
  let idle () = step () in
  (* fill: depth 4; the fifth push is dropped, level stops at 4 *)
  List.iter [ 0xA1; 0xA2; 0xA3; 0xA4; 0xA5 ] ~f:push;
  let head, level = idle () in
  [%test_result: int] level ~expect:4;
  [%test_result: int] head ~expect:0xA1;
  (* full, push and pop together: the pop happens, the push is still dropped *)
  ignore (step ~push:(Some 0xA6) ~pop:true () : int * int);
  let head, level = idle () in
  [%test_result: int] level ~expect:3;
  [%test_result: int] head ~expect:0xA2;
  (* drain in order; nothing of A5 or A6 was stored *)
  let drained = collect 3 (fun () -> fst (step ~pop:true ())) in
  [%test_result: int list] drained ~expect:[ 0xA2; 0xA3; 0xA4 ];
  (* a pop while empty does nothing *)
  ignore (step ~pop:true () : int * int);
  let _, level = idle () in
  [%test_result: int] level ~expect:0;
  (* empty, push and pop together: the push is kept, the pop is ignored *)
  ignore (step ~push:(Some 0xB1) ~pop:true () : int * int);
  let head, level = idle () in
  [%test_result: int] level ~expect:1;
  [%test_result: int] head ~expect:0xB1;
  (* clear wins over a push in the same cycle *)
  ignore (step ~push:(Some 0xB2) ~clear:true () : int * int);
  let _, level = idle () in
  [%test_result: int] level ~expect:0;
  print waves [ "push#b"; "pop#b"; "clear#b"; "push_data#u"; "head#u"; "level#u" ];
  [%expect {|
    ┌Signals───────────┐┌Waves─────────────────────────────────────────────────────────────────────────┐
    │push              ││────────────────────┐   ┌───┐                       ┌───┐   ┌───┐             │
    │                  ││                    └───┘   └───────────────────────┘   └───┘   └───          │
    │pop               ││                        ┌───┐   ┌───────────────┐   ┌───┐                     │
    │                  ││────────────────────────┘   └───┘               └───┘   └───────────          │
    │clear             ││                                                            ┌───┐             │
    │                  ││────────────────────────────────────────────────────────────┘   └───          │
    │                  ││────┬───┬───┬───┬───┬───┬───┬───────────────────────┬───┬───┬───┬───          │
    │push_data         ││ 161│162│163│164│165│0  │166│0                      │177│0  │178│0            │
    │                  ││────┴───┴───┴───┴───┴───┴───┴───────────────────────┴───┴───┴───┴───          │
    │                  ││────┬───────────────────────┬───────┬───┬───┬───────────┬───────────          │
    │head              ││ 0  │161                    │162    │163│164│161        │177                  │
    │                  ││────┴───────────────────────┴───────┴───┴───┴───────────┴───────────          │
    │                  ││────┬───┬───┬───┬───────────┬───────┬───┬───┬───────────┬───────┬───          │
    │level             ││ 0  │1  │2  │3  │4          │3      │2  │1  │0          │1      │0            │
    │                  ││────┴───┴───┴───┴───────────┴───────┴───┴───┴───────────┴───────┴───          │
    └──────────────────┘└──────────────────────────────────────────────────────────────────────────────┘
    |}]
;;

module J = struct
  module Sim = Cyclesim.With_interface (Pe_fifos.I) (Pe_fifos.O)

  let create () =
    let sim = Sim.create (Pe_fifos.create ~log2:2 ~no_cg:true (scope ())) in
    let waves, sim = Waveform.create sim in
    let i = Cyclesim.inputs sim in
    let o = Cyclesim.outputs ~clock_edge:Side.Before sim in
    set i.rst_n ~width:1 1;
    set i.fjoin ~width:1 1;
    let step ?(push = None) ?(pop = false) () =
      set i.host_push ~width:1 (Bool.to_int (Option.is_some push));
      set i.host_data ~width:32 (Option.value push ~default:0);
      set i.core_pop ~width:1 (Bool.to_int pop);
      Cyclesim.cycle sim
    in
    waves, step, o
  ;;
end

let%expect_test "fifos, join: TXF -> RXF -> core, 8 words, in order" =
  let waves, step, o = J.create () in
  let words = List.init 9 ~f:(fun n -> 0xC0 + n) in
  List.iter words ~f:(fun w -> step ~push:(Some w) ());
  step ();
  [%test_result: int] (int o.txf_level) ~expect:4;
  [%test_result: int] (int o.rxf_level) ~expect:4;
  [%test_result: int] (int o.rx_block) ~expect:1;
  (* the ninth word met a full queue; the core takes the rest in order *)
  let taken =
    collect 8 (fun () ->
      step ();
      let w = int o.pq_head in
      step ~pop:true ();
      w)
  in
  [%test_result: int list] taken ~expect:(List.take words 8);
  step ();
  step ();
  [%test_result: int] (int o.pq_empty) ~expect:1;
  print
    ~wave_width:0
    ~display_width:110
    waves
    [ "host_push#b"; "core_pop#b"; "txf_level#u"; "rxf_level#u"; "pq_empty#b" ];
  [%expect {|
    ┌Signals───────────┐┌Waves───────────────────────────────────────────────────────────────────────────────────┐
    │host_push         ││──────────────────┐                                                                     │
    │                  ││                  └─────────────────────────────────────                                │
    │core_pop          ││                      ┌─┐ ┌─┐ ┌─┐ ┌─┐ ┌─┐ ┌─┐ ┌─┐ ┌─┐                                   │
    │                  ││──────────────────────┘ └─┘ └─┘ └─┘ └─┘ └─┘ └─┘ └─┘ └───                                │
    │                  ││──┬─────────┬─┬─┬─────────┬───┬───┬───┬─────────────────                                │
    │txf_level         ││ 0│1        │2│3│4        │3  │2  │1  │0                                                │
    │                  ││──┴─────────┴─┴─┴─────────┴───┴───┴───┴─────────────────                                │
    │                  ││────┬─┬─┬─┬─────────────┬─┬─┬─┬─┬─┬─┬─┬─┬───┬───┬───┬───                                │
    │rxf_level         ││ 0  │1│2│3│4            │3│4│3│4│3│4│3│4│3  │2  │1  │0                                  │
    │                  ││────┴─┴─┴─┴─────────────┴─┴─┴─┴─┴─┴─┴─┴─┴───┴───┴───┴───                                │
    │pq_empty          ││────┐                                               ┌───                                │
    │                  ││    └───────────────────────────────────────────────┘                                   │
    └──────────────────┘└────────────────────────────────────────────────────────────────────────────────────────┘
    |}]
;;
