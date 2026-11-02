(* wl_run: assemble a program, load it on the OCaml cycle model and print every
   pin change. Python is not involved: the whole path, source to pins, is OCaml.

     wl_run FILE [--define NAME=value]... [--clkdiv F] [--out-pins B,N] [--set-pins B,N]
            [--side-pins B] [--in-pins B] [--push WORD]... [--cycles N]

   Output: one line "cycle uo uio_out uio_oe" per change (core 0, base 0), then the
   words left in RXF. ocaml/test/run_parity.py compares it with tools/pe/modelhost.py. *)
open Wirelingo_asm
open Wirelingo_sim

let read_file f =
  let ic = open_in_bin f in
  let s = really_input_string ic (in_channel_length ic) in
  close_in ic;
  s
;;

let pair s =
  match String.split_on_char ',' s with
  | [ a; b ] -> int_of_string a, int_of_string b
  | _ -> failwith "pair"
;;

let () =
  let file = ref "" in
  let defines = ref [] in
  let o = ref Host.no_overrides in
  let pushes = ref [] in
  let cycles = ref 100_000 in
  let rec args = function
    | "--define" :: v :: r ->
      let i = String.index v '=' in
      defines
      := (String.sub v 0 i, String.sub v (i + 1) (String.length v - i - 1)) :: !defines;
      args r
    | "--clkdiv" :: v :: r ->
      o
      := { !o with
           clkdiv = Some (Host.clkdiv_for ~f_clk:(float_of_string v) ~f_tick:1.)
         };
      args r
    | "--out-pins" :: v :: r ->
      o := { !o with out_pins = Some (pair v) };
      args r
    | "--set-pins" :: v :: r ->
      o := { !o with set_pins = Some (pair v) };
      args r
    | "--side-pins" :: v :: r ->
      o := { !o with side_pins = Some (int_of_string v) };
      args r
    | "--in-pins" :: v :: r ->
      o := { !o with in_pins = Some (int_of_string v) };
      args r
    | "--push" :: v :: r ->
      pushes := int_of_string v :: !pushes;
      args r
    | "--cycles" :: v :: r ->
      cycles := int_of_string v;
      args r
    | f :: r ->
      file := f;
      args r
    | [] -> ()
  in
  args (List.tl (Array.to_list Sys.argv));
  let prog = Asm.assemble (read_file !file) ~defines:(List.rev !defines) in
  let mh = Modelhost.create () in
  Modelhost.apply
    mh
    (List.map (fun (a, d) -> Z.to_int a, Z.to_int d) (Host.load_program ~base:0 0 prog !o));
  Modelhost.start mh 1;
  List.iter (Modelhost.push mh 0) (List.rev !pushes);
  let last = ref (-1, -1, -1) in
  ignore
    (Modelhost.run
       mh
       !cycles
       ~fn:(fun _ t ->
         let now = Chip.outputs t.chip in
         if now <> !last
         then (
           let a, b, c = now in
           Printf.printf "%d %d %d %d\n" t.chip.cycle a b c;
           last := now);
         true));
  List.iter (Printf.printf "rxf %d\n") mh.chip.cores.(0).rxf
;;
