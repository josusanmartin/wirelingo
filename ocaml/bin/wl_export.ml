(* wl_export: port of tools/pe/export.py. An assembled program as a MicroPython
   snippet for tools/micropython/protoemu.py, same arguments, same output.

     wl_export FILE [--program NAME] [--core N] [--base N] [--profile tapeout|full]
       [--define NAME=value]... [--out-pins B,N] [--set-pins B,N] [--side-pins B]
       [--in-pins B] [--clkdiv F]

   Also:  wl_export --listing FILE [--base N] [--define NAME=value]...   Program.listing
          wl_export --disasm                       isa.disasm of hex words on stdin
          wl_export --writes FILE ...              the load_program writes, one per line *)
open Wirelingo_asm

let read_file f =
  let ic = open_in_bin f in
  let s = really_input_string ic (in_channel_length ic) in
  close_in ic;
  s
;;

let py_hex z =
  if Z.sign z < 0 then "-0x" ^ Z.format "%x" (Z.neg z) else "0x" ^ Z.format "%x" z
;;

(* int(s, 0) *)
let int0 s =
  let t = String.lowercase_ascii s in
  let body = if t <> "" && t.[0] = '-' then String.sub t 1 (String.length t - 1) else t in
  match int_of_string_opt body with
  | Some _ when String.length body = 1 || body.[0] <> '0' || not (Pyre.is_digit body.[1]) ->
    int_of_string t
  | _ -> failwith ("invalid literal: " ^ s)
;;

let pair s =
  match String.split_on_char ',' s with
  | [ a; b ] -> int_of_string a, int_of_string b
  | _ -> failwith "pair"
;;

let () =
  let program = ref None in
  let core = ref 0 in
  let base = ref None in
  let profile = ref Host.tapeout in
  let defines = ref [] in
  let o = ref Host.no_overrides in
  let file = ref None in
  let mode = ref `Export in
  let rec args = function
    | "--program" :: v :: r ->
      program := Some v;
      args r
    | "--core" :: v :: r ->
      core := int_of_string v;
      args r
    | "--base" :: v :: r ->
      base := Some (int_of_string v);
      args r
    | "--profile" :: v :: r ->
      profile := if v = "tapeout" then Host.tapeout else Host.full;
      args r
    | "--define" :: v :: r ->
      let i = String.index v '=' in
      defines
      := ( String.sub v 0 i
         , string_of_int (int0 (String.sub v (i + 1) (String.length v - i - 1))) )
         :: !defines;
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
    | "--clkdiv" :: v :: r ->
      (match Asm.py_float v with
       | Some f when f <> 0. ->
         let c = Host.clkdiv_for ~f_clk:f ~f_tick:1. in
         o := { !o with clkdiv = Some c }
       | _ -> ());
      args r
    | "--listing" :: r ->
      mode := `Listing;
      args r
    | "--writes" :: r ->
      mode := `Writes;
      args r
    | "--disasm" :: r ->
      mode := `Disasm;
      args r
    | f :: r ->
      file := Some f;
      args r
    | [] -> ()
  in
  args (List.tl (Array.to_list Sys.argv));
  let run () =
    if !mode = `Disasm
    then (
      try
        while true do
          print_endline (Isa.disasm (int_of_string ("0x" ^ String.trim (input_line stdin))))
        done
      with
      | End_of_file -> ())
    else (
      let text = read_file (Option.get !file) in
      let defs = List.rev !defines in
      let prog =
        match !program with
        | None -> Asm.assemble text ~defines:defs
        | Some name ->
          (match
             List.filter
               (fun (p : Asm.program) -> p.name = name)
               (Asm.assemble_all text ~defines:defs)
           with
           | [ p ] -> p
           | l ->
             Printf.eprintf "error: --program matches %d\n" (List.length l);
             exit 2)
      in
      let base_v =
        match !base, prog.origin with
        | Some b, _ -> b
        | None, Some z -> Z.to_int z
        | None, None -> !core * 256
      in
      match !mode with
      | `Listing -> print_endline (Asm.listing prog base_v)
      | `Writes ->
        List.iter
          (fun (a, d) -> Printf.printf "%s %s\n" (Z.to_string a) (Z.to_string d))
          (Host.load_program ~profile:!profile ~base:base_v !core prog !o)
      | _ ->
        ignore (Host.load_program ~profile:!profile ~base:base_v !core prog !o);
        let c = Host.core_config prog !o in
        let words = Asm.relocate prog base_v in
        let wrap = Host.wrap_of prog base_v in
        Printf.printf
          "# %s: %d words at %d, profile %s\n"
          prog.name
          (List.length words)
          base_v
          !profile.pname;
        Printf.printf
          "pe.load(core=%d, base=%d, words=[%s],\n"
          !core
          base_v
          (String.concat ", " (List.map (Printf.sprintf "0x%x") words));
        Printf.printf
          "        clkdiv=%s, pinctrl0=%s, pinctrl1=%s,\n"
          (py_hex c.clkdiv)
          (py_hex c.pinctrl0)
          (py_hex c.pinctrl1);
        Printf.printf "        shiftctrl=%s, wrap=0x%x)\n" (py_hex c.shiftctrl) wrap)
  in
  try run () with
  | Host.Value_error m | Invalid_argument m ->
    Printf.eprintf "ValueError: %s\n" m;
    exit 1
  | Asm.Asm_error m ->
    Printf.eprintf "pe.asm.AsmError: %s\n" m;
    exit 1
  | Asm.Crash t ->
    Printf.eprintf "%s\n" t;
    exit 1
;;
