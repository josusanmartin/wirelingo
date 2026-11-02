(* Host-side helpers: port of tools/pe/host.py and tools/pe/profile.py. Programs
   and pin assignments become (addr, data) register writes. [Value_error] stands
   for Python's ValueError. Overrides use the same units as host.core_config. *)

exception Value_error of string

let value_error s = raise (Value_error s)

type profile =
  { pname : string
  ; ncores : int
  ; nbanks : int
  ; bank_words : int
  }

let tapeout = { pname = "fixed-bank-v1-2c2b8f"; ncores = 2; nbanks = 2; bank_words = 256 }
let full = { pname = "fixed-bank-v1-4c4b8f"; ncores = 4; nbanks = 4; bank_words = 256 }

type overrides =
  { out_pins : (int * int) option
  ; set_pins : (int * int) option
  ; side_pins : int option
  ; in_pins : int option
  ; clkdiv : int option
  }

let no_overrides =
  { out_pins = None; set_pins = None; side_pins = None; in_pins = None; clkdiv = None }
;;

type config =
  { clkdiv : Z.t
  ; pinctrl0 : Z.t
  ; pinctrl1 : Z.t
  ; shiftctrl : Z.t
  }

let z = Z.of_int
let core_reg c off = 0x400 + (0x20 * c) + off

(* clkdiv_fields: Invalid_argument is ValueError there *)
let clkdiv_for ~f_clk ~f_tick =
  let i, f =
    try Asm.clkdiv_fields (f_clk /. f_tick) with
    | Invalid_argument m -> value_error m
  in
  (i lsl 8) lor f
;;

let core_config (prog : Asm.program) (o : overrides) =
  let cfg k = Hashtbl.find_opt prog.config k in
  let pair ovr k =
    match ovr with
    | Some (a, b) -> z a, z b
    | None ->
      (match cfg k with
       | Some (Asm.Pair (a, b)) -> a, b
       | _ -> Z.zero, Z.zero)
  in
  let num ovr k =
    match ovr with
    | Some a -> z a
    | None ->
      (match cfg k with
       | Some (Asm.Num a) -> a
       | _ -> Z.zero)
  in
  let shift k =
    match cfg k with
    | Some (Asm.Shift (r, a, n)) -> r, a, n
    | _ -> false, false, z 32
  in
  let ( &. ) a m = Z.logand a (z m) in
  let ( <<. ) = Z.shift_left in
  let ( ||. ) = Z.logor in
  let bit b sh = if b then z (1 lsl sh) else Z.zero in
  let ob, oc = pair o.out_pins "out_pins" in
  let sb, scnt = pair o.set_pins "set_pins" in
  let pinctrl0 =
    (ob &. 31) ||. ((oc &. 63) <<. 8) ||. ((sb &. 31) <<. 16) ||. ((scnt &. 7) <<. 24)
  in
  let sideb = num o.side_pins "side_pins" in
  let inb = num o.in_pins "in_pins" in
  let pinctrl1 =
    (sideb &. 31)
    ||. (prog.side_count <<. 8)
    ||. z (prog.side_opt lsl 12)
    ||. z (prog.side_pindir lsl 13)
    ||. ((inb &. 31) <<. 16)
  in
  let osr_right, autopull, pull_th = shift "shift_out" in
  let isr_right, autopush, push_th = shift "shift_in" in
  let shiftctrl =
    bit osr_right 0
    ||. bit isr_right 1
    ||. bit autopull 2
    ||. bit autopush 3
    ||. bit (cfg "fjoin" <> None) 4
    ||. ((pull_th &. 31) <<. 8)
    ||. ((push_th &. 31) <<. 16)
  in
  let clkdiv =
    match o.clkdiv with
    | Some c -> z c
    | None ->
      let i, f =
        match cfg "clkdiv" with
        | Some (Asm.Clk (i, f)) -> i, f
        | _ -> 1, 0
      in
      z ((i lsl 8) lor f)
  in
  { clkdiv; pinctrl0; pinctrl1; shiftctrl }
;;

let validate_image core words base wrap profile =
  if core < 0 || core >= min profile.ncores profile.nbanks
  then value_error "core has no instruction bank in selected profile";
  if words = [] then value_error "program must not be empty";
  let lo = core * 256 in
  let hi = lo + profile.bank_words in
  if not (lo <= base && base < hi && base + List.length words <= hi)
  then value_error "program exceeds core's fixed instruction bank";
  if wrap < 0 || wrap lsr 32 <> 0 then value_error "invalid wrap encoding";
  let bottom = wrap land 0xFFFF in
  let top = wrap lsr 16 in
  if not (base <= bottom && bottom <= top && top < base + List.length words)
  then value_error "wrap must lie inside the loaded image";
  List.iter
    (fun w ->
       let op = w lsr 27 in
       if (op = 0 || op = 13) && not (lo <= w land 0x3FF && w land 0x3FF < hi)
       then value_error "JMP/CALL target leaves core's fixed instruction bank")
    words
;;

let relocate prog base =
  try Asm.relocate prog base with
  | Invalid_argument m -> value_error m
;;

let wrap_of (prog : Asm.program) base =
  (base + prog.wrap_bottom) lor ((base + Option.get prog.wrap_top) lsl 16)
;;

let load_program ?(profile = tapeout) ?base core (prog : Asm.program) o =
  let base =
    match base with
    | Some b -> b
    | None ->
      (match prog.origin with
       | Some b -> Z.to_int b
       | None -> core * 256)
  in
  if prog.words = [] then value_error "program must not be empty";
  let words = relocate prog base in
  let wrap = wrap_of prog base in
  validate_image core words base wrap profile;
  let c = core_config prog o in
  let reg off = core_reg core off in
  List.mapi (fun i w -> z (base + i), z w) words
  @ [ z (reg 0), c.clkdiv
    ; z (reg 1), c.pinctrl0
    ; z (reg 2), c.pinctrl1
    ; z (reg 3), c.shiftctrl
    ; z (reg 4), z base
    ; z (reg 5), z wrap
    ]
;;

let start_writes ?(enabled_before = 0) mask = [ 0x801, enabled_before lor mask lor (mask lsl 8) ]

let pin_owner_writes ?(profile = tapeout) owners =
  let lo = ref 0 in
  let hi = ref 0 in
  List.iter
    (fun (p, c) ->
       if p < 0 || p >= 32 then value_error "pin must be in 0..31";
       if c < 0 || c >= profile.ncores
       then value_error "pin owner does not exist in selected profile";
       if p < 16
       then lo := !lo lor (c lsl (2 * p))
       else hi := !hi lor (c lsl (2 * (p - 16))))
    owners;
  [ 0x805, !lo; 0x806, !hi ]
;;

(* spi_frame: one 1-bit transaction; reads get the dummy byte and 4*nread zeros *)
let spi_frame ?(words = []) ?(nread = 0) cmd addr =
  let b = Buffer.create 16 in
  Buffer.add_char b (Char.chr (cmd land 0xFF));
  Buffer.add_char b (Char.chr ((addr lsr 8) land 0xFF));
  Buffer.add_char b (Char.chr (addr land 0xFF));
  List.iter (fun w -> Buffer.add_int32_be b (Int32.of_int w)) words;
  if cmd land 1 = 1 then Buffer.add_string b (String.make (1 + (4 * nread)) '\000');
  Buffer.contents b
;;

(* coalesce: consecutive-address writes become (addr, words) bursts *)
let coalesce writes =
  List.fold_left
    (fun acc (a, d) ->
       match acc with
       | (b, ws) :: tl when b + List.length ws = a -> (b, ws @ [ d ]) :: tl
       | _ -> (a, [ d ]) :: acc)
    []
    writes
  |> List.rev
;;
