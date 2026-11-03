(* wl_simcheck: run the OCaml cycle model from a stream of stimulus on stdin and
   compare it with the expected values in the stream, every cycle.

     C bank_words ncores nbanks fifo_depth        first line
     S ui uio we addr wdata commit snap cancel ui_mid uio_mid rd uo uio_out uio_oe
         one cycle: *_mid -1 means None; rd is the bus read at addr before the
         edge and uo/uio_out/uio_oe the pins before the edge
     = state                                      Chip.state_line after that edge
     F name=count name=count ...                  the coverage counters, sorted
     X                                            the test cleared the counters
     Q                                            stop, nothing left to compare

   ocaml/test/lock_chip.py writes this stream from tools/pe/sim.py. The first
   difference stops the run with exit status 1 and prints both sides. At the end
   it prints "OK <cycles>". *)
open Wirelingo_sim

let () =
  let chip = ref None in
  let cycles = ref 0 in
  let b = Buffer.create 4096 in
  let fail what got want =
    Printf.printf "MISMATCH cycle %d %s\n ocaml: %s\n python: %s\n" !cycles what got want;
    exit 1
  in
  let get () = Option.get !chip in
  try
    while true do
      let line = input_line stdin in
      if line <> ""
      then (
        match line.[0] with
        | 'C' ->
          Scanf.sscanf line "C %d %d %d %d" (fun bank_words ncores nbanks fifo_depth ->
            chip := Some (Chip.create ~bank_words ~ncores ~nbanks ~fifo_depth))
        | 'S' ->
          let pos = ref 2 in
          let n = String.length line in
          let next () =
            let neg = !pos < n && line.[!pos] = '-' in
            if neg then incr pos;
            let v = ref 0 in
            while !pos < n && line.[!pos] <> ' ' do
              v := (!v * 10) + Char.code line.[!pos] - 48;
              incr pos
            done;
            incr pos;
            if neg then - !v else !v
          in
          let ui = next () in
          let uio = next () in
          let we = next () in
          let addr = next () in
          let wdata = next () in
          let commit = next () in
          let snap = next () in
          let cancel = next () in
          let ui_mid = next () in
          let uio_mid = next () in
          let rd = next () in
          let uo = next () in
          let uio_out = next () in
          let uio_oe = next () in
          (fun () ->
               let c = get () in
               let rd' = Chip.read c addr in
               if rd' <> rd
               then fail (Printf.sprintf "read %#x" addr) (string_of_int rd') (string_of_int rd);
               let a, b', d = Chip.outputs c in
               if a <> uo || b' <> uio_out || d <> uio_oe
               then
                 fail
                   "outputs"
                   (Printf.sprintf "%d %d %d" a b' d)
                   (Printf.sprintf "%d %d %d" uo uio_out uio_oe);
               let o v = if v < 0 then None else Some v in
               Chip.step
                 c
                 ~ui_in:ui
                 ~uio_in:uio
                 ~we:(we = 1)
                 ~addr
                 ~wdata
                 ~commit:(commit = 1)
                 ~snap:(snap = 1)
                 ~cancel:(cancel = 1)
                 ~ui_mid:(o ui_mid)
                 ~uio_mid:(o uio_mid);
               incr cycles)
            ()
        | '=' ->
          Buffer.clear b;
          Chip.state_line b (get ());
          let got = Buffer.contents b in
          let want = String.sub line 2 (String.length line - 2) in
          if got <> want then fail "state" got want
        | 'F' ->
          let c = get () in
          let got =
            Hashtbl.fold (fun k v acc -> Printf.sprintf "%s=%d" k v :: acc) c.Chip.stats []
            |> List.sort compare
            |> String.concat " "
          in
          let want = String.sub line 2 (String.length line - 2) in
          if got <> want then fail "coverage" got want
        | 'X' -> Hashtbl.reset (get ()).Chip.stats
        | 'Q' -> raise End_of_file
        | _ -> failwith ("bad line: " ^ line))
    done
  with
  | End_of_file -> Printf.printf "OK %d\n" !cycles
;;
