(* wl_asm: assemble a .pasm file.

     wl_asm [-D NAME=VALUE]... [--bin FILE] [--base N] FILE

   Prints the canonical dump (every program in the file). With --bin, also writes
   each program's words, relocated to --base, as little-endian 32-bit words.
   FILE - reads stdin. An assembly error prints "error: MESSAGE" and exits 1; a
   case where tools/pe/asm.py raises an uncaught exception prints "crash: TYPE"
   and exits 2. *)
open Wirelingo_asm

let read_all ic =
  let b = Buffer.create 65536 in
  (try
     while true do
       Buffer.add_channel b ic 1
     done
   with
   | End_of_file -> ());
  Buffer.contents b
;;

let () =
  let defines = ref [] in
  let bin = ref None in
  let base = ref 0 in
  let file = ref None in
  let rec args = function
    | "-D" :: kv :: r ->
      let i = String.index kv '=' in
      defines :=
        (String.sub kv 0 i, String.sub kv (i + 1) (String.length kv - i - 1)) :: !defines;
      args r
    | "--bin" :: f :: r ->
      bin := Some f;
      args r
    | "--base" :: n :: r ->
      base := int_of_string n;
      args r
    | f :: r ->
      file := Some f;
      args r
    | [] -> ()
  in
  args (List.tl (Array.to_list Sys.argv));
  let text =
    match !file with
    | None | Some "-" ->
      set_binary_mode_in stdin true;
      read_all stdin
    | Some f ->
      let ic = open_in_bin f in
      let s = read_all ic in
      close_in ic;
      s
  in
  match Asm.assemble_all text ~defines:(List.rev !defines) with
  | exception Asm.Asm_error m ->
    print_string ("error: " ^ m ^ "\n");
    exit 1
  | exception Asm.Crash t ->
    print_string ("crash: " ^ t ^ "\n");
    exit 2
  | progs ->
    let b = Buffer.create 65536 in
    List.iter (Asm.dump b) progs;
    print_string (Buffer.contents b);
    (match !bin with
     | None -> ()
     | Some f ->
       let oc = open_out_bin f in
       List.iter
         (fun p ->
            List.iter
              (fun w ->
                 output_byte oc (w land 255);
                 output_byte oc ((w lsr 8) land 255);
                 output_byte oc ((w lsr 16) land 255);
                 output_byte oc ((w lsr 24) land 255))
              (Asm.relocate p !base))
         progs;
       close_out oc)
;;
