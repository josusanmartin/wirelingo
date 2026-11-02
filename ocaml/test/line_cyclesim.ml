(* The Hardcaml pe_line under Cyclesim against the OCaml cycle model's Line, every
   cycle, on random traffic: no Verilog, no Python. pe_line is the one module with
   a single clock and no blackboxes, so Hardcaml's own simulator can run it as it
   stands. The stimulus keeps core 0's contract (line_props.sv): pushes only with
   room, takes only what IN LINE may take, counts 1..32; P <= 4095.

     dune exec test/line_cyclesim.exe [cycles] [seed] *)
open Hardcaml
open Wirelingo_rtl
open Wirelingo_sim
module Sim = Cyclesim.With_interface (Pe_line.I) (Pe_line.O)

let () =
  let cycles = if Array.length Sys.argv > 1 then int_of_string Sys.argv.(1) else 2_000_000 in
  let seed = if Array.length Sys.argv > 2 then int_of_string Sys.argv.(2) else 1 in
  let rng = Random.State.make [| seed |] in
  let r n = Random.State.int rng n in
  let chance p = Random.State.float rng 1.0 < p in
  let rand32 () = (r (1 lsl 16) lsl 16) lor r (1 lsl 16) in
  let sim = Sim.create (Pe_line.create (Scope.create ~flatten_design:true ())) in
  let i = Cyclesim.inputs sim in
  let o = Cyclesim.outputs ~clock_edge:Side.Before sim in
  let set p ~width v = p := Bits.of_int ~width v in
  let model = Line.create (fun _ -> ()) in
  let live = [| 0; 1; 2; 3; 16; 17; 24; 25; 26 |] in
  let pins = ref 0 in
  let toggle = ref 0.1 in
  let events = Hashtbl.create 8 in
  let count k = Hashtbl.replace events k (1 + try Hashtbl.find events k with Not_found -> 0) in
  set i.rst_n ~width:1 1;
  set i.clk ~width:1 0;
  for cycle = 0 to cycles - 1 do
    (* a random configuration now and then, sane most of the time *)
    let cfg_we = ref 0 and div_we = ref 0 and pins_we = ref 0 and wdata = ref 0 in
    let k = r 4000 in
    if k = 0 || cycle = 0
    then (
      cfg_we := 1;
      let c = (r (1 lsl 15) land lnot 6) lor (r 3 lsl 1) in
      wdata := if chance 0.8 then c lor 1 lor (1 lsl 14) else c;
      toggle := [| 0.02; 0.1; 0.4 |].(r 3))
    else if k = 1 || cycle = 1
    then (
      div_we := 1;
      let rx_p = if chance 0.9 then 4 + r 13 else 1 + r 4095 in
      let tx = if chance 0.9 then 2 + r 5 else r 256 in
      wdata := (rx_p lsl 16) lor (tx lsl 8) lor r 256)
    else if k = 2 || cycle = 2
    then (
      pins_we := 1;
      let t = live.(r 9) in
      let x = if chance 0.5 then t else live.(r 9) in
      wdata := t lor (x lsl 8) lor (r 8 lsl 5) lor (r 8 lsl 13))
    else wdata := rand32 ();
    (* pins: what the unit drives loops back on internal pins, random elsewhere *)
    let prev = !pins in
    for b = 0 to 31 do
      if chance !toggle then pins := !pins lxor (1 lsl b)
    done;
    let out =
      List.fold_left
        (fun acc (p, v) -> (acc land lnot (1 lsl p)) lor (v lsl p))
        !pins
        (Line.drive model)
    in
    let pins_in = out land 0xFFFFFFFF in
    let rxp = Line.rxp model in
    let rx_now = (pins_in lsr rxp) land 1 in
    let rx_mid = if chance 0.5 then rx_now else (prev lsr rxp) land 1 in
    (* core 0 *)
    let push = Line.room model && chance 0.3 in
    let push_n = 1 + r 32 in
    let push_data = rand32 () land ((1 lsl push_n) - 1) in
    let push_msb = chance 0.5 in
    let in_n = 1 + r 32 in
    let in_shr = chance 0.5 in
    let peeked = Line.peek model in_n in
    let take = peeked <> None && chance 0.5 in
    set i.cfg_we ~width:1 !cfg_we;
    set i.div_we ~width:1 !div_we;
    set i.pins_we ~width:1 !pins_we;
    set i.wdata ~width:32 !wdata;
    set i.pins_in ~width:32 pins_in;
    set i.rx_mid ~width:1 rx_mid;
    set i.push ~width:1 (Bool.to_int push);
    set i.push_data ~width:32 push_data;
    set i.push_n ~width:6 push_n;
    set i.push_msb ~width:1 (Bool.to_int push_msb);
    set i.in_n ~width:6 in_n;
    set i.in_shr ~width:1 (Bool.to_int in_shr);
    set i.take ~width:1 (Bool.to_int take);
    Cyclesim.cycle sim;
    let v (s : Bits.t ref) = Bits.to_int !s in
    let check what got want =
      if got <> want
      then (
        Printf.printf "MISMATCH cycle %d %s: hardcaml %#x model %#x\n" cycle what got want;
        exit 1)
    in
    check "r_stat" (v o.r_stat) (Line.status model);
    check "rx_pin" (v o.rx_pin) rxp;
    let mask, value =
      List.fold_left
        (fun (m, x) (p, b) -> m lor (1 lsl p), x lor (b lsl p))
        (0, 0)
        (Line.drive model)
    in
    check "drv_mask" (v o.drv_mask) mask;
    check "drv_val" (v o.drv_val) value;
    check "room" (v o.room) (Bool.to_int (Line.room model));
    (match peeked with
     | None ->
       check "in_ok" (v o.in_ok) 0;
       check "in_k" (v o.in_k) 0
     | Some (bs, t) ->
       let k = List.length bs in
       check "in_ok" (v o.in_ok) 1;
       check "in_k" (v o.in_k) k;
       check "in_t" (v o.in_t) t;
       let value = ref 0 in
       List.iteri
         (fun n b -> value := !value lor (b lsl if in_shr then n else k - 1 - n))
         bs;
       check "in_val" (v o.in_val) !value;
       if t = 1 then count "frame_ends_seen";
       if take then count "takes";
       if take then Line.consume model k t);
    if push
    then (
      count "pushes";
      Line.push
        model
        (List.init push_n (fun n ->
           (push_data lsr if push_msb then push_n - 1 - n else n) land 1)));
    Line.step model pins_in rx_mid;
    Line.commit model;
    if !cfg_we = 1 then Line.write model 0 !wdata;
    if !div_we = 1 then Line.write model 1 !wdata;
    if !pins_we = 1 then Line.write model 2 !wdata
  done;
  Printf.printf "line_cyclesim: %d cycles agree (seed %d)" cycles seed;
  Hashtbl.iter (fun k c -> Printf.printf ", %s=%d" k c) events;
  print_newline ()
;;
