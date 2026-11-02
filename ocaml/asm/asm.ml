(* Assembler for protoemu programs (.pasm): port of tools/pe/asm.py, syntax in
   docs/asm.md. The patterns are asm.py's, run by Pyre. [Crash] stands for an
   exception asm.py does not catch (an IndexError on a directive with too few
   arguments, for example): the Python assembler has no defined message there,
   and the parity test compares the exception type. *)

exception Asm_error of string
exception Crash of string

let err fmt = Printf.ksprintf (fun s -> raise (Asm_error s)) fmt

(* .config entries: what host.core_config reads; [cfg_repr] gives Python's repr *)
type cfg =
  | Pair of Z.t * Z.t (* out_pins, set_pins *)
  | Num of Z.t (* side_pins, in_pins *)
  | Clk of int * int (* clkdiv *)
  | Yes (* fjoin *)
  | Shift of bool * bool * Z.t (* shift_out, shift_in: right, auto, threshold *)

let py_bool b = if b then "True" else "False"

let cfg_repr = function
  | Pair (a, b) -> Printf.sprintf "(%s, %s)" (Z.to_string a) (Z.to_string b)
  | Num a -> Z.to_string a
  | Clk (i, f) -> Printf.sprintf "(%d, %d)" i f
  | Yes -> "True"
  | Shift (r, a, n) -> Printf.sprintf "(%s, %s, %s)" (py_bool r) (py_bool a) (Z.to_string n)
;;

type program =
  { name : string
  ; mutable words : int list (* reversed while assembling *)
  ; mutable reloc : int list
  ; labels : (string, int) Hashtbl.t
  ; mutable origin : Z.t option
  ; mutable wrap_bottom : int
  ; mutable wrap_top : int option
  ; mutable side_count : Z.t
  ; mutable side_opt : int
  ; mutable side_pindir : int
  ; config : (string, cfg) Hashtbl.t
  ; mutable source_lines : (int * string) list
  }

let pin_aliases =
  let t = Hashtbl.create 64 in
  for i = 0 to 31 do
    Hashtbl.replace t (Printf.sprintf "p%d" i) i
  done;
  for i = 0 to 7 do
    Hashtbl.replace t (Printf.sprintf "uio%d" i) i;
    Hashtbl.replace t (Printf.sprintf "v%d" i) (24 + i)
  done;
  for i = 0 to 6 do
    Hashtbl.replace t (Printf.sprintf "uo%d" i) (8 + i)
  done;
  for i = 0 to 4 do
    Hashtbl.replace t (Printf.sprintf "ui%d" i) (16 + i)
  done;
  t
;;

let locs =
  let t = Hashtbl.create 32 in
  Array.iteri (fun i n -> Hashtbl.replace t n i) Isa.loc_names;
  Hashtbl.replace t "x" 0;
  Hashtbl.replace t "y" 1;
  t
;;

let re = Pyre.compile
let re_comment = re ";|//"
let re_symbol = re "(?<![0-9a-fA-FxXbB])[A-Za-z_][A-Za-z_0-9]*"
let re_expr_chars = re "[0-9a-fA-FxXbB_+\\-*/%()<>|&~ ]*"
let re_label = re "^([A-Za-z_][A-Za-z_0-9]*):\\s*(.*)$"
let re_delay = re "\\[([^\\]]+)\\]\\s*$"
let re_side = re "\\s+side\\s+(\\S+)\\s*$"
let re_jmp_reg = re "(!?)(r[0-3]|x|y)(--)?"
let re_timeout = re ~icase:true "\\btimeout(?:\\s+(r[0-3]|x|y))?\\b"
let re_clear = re ~icase:true "\\bclear\\b"
let re_if_pin = re ~icase:true "\\bif\\s+pin\\s+(.+?)\\s*==\\s*([01])\\s*$"

(* defs hold the text str(value) of each symbol: _eval substitutes text *)
let eval_expr expr defs lineno =
  let expr = Pyre.strip expr in
  let e =
    Pyre.sub
      re_symbol
      (fun n ->
         match Hashtbl.find_opt defs n with
         | Some v -> v
         | None ->
           (match Hashtbl.find_opt pin_aliases n with
            | Some v -> string_of_int v
            | None -> err "line %d: unknown symbol '%s'" lineno n))
      expr
  in
  if Pyre.fullmatch re_expr_chars e = None || e = ""
  then err "line %d: bad expression '%s'" lineno expr;
  let e = Pyre.replace_all e ~sub:"//" ~by:"/" in
  let e = Pyre.replace_all e ~sub:"/" ~by:"//" in
  try Pyexpr.eval e with
  | Pyexpr.Eval_error msg -> err "line %d: bad expression '%s': %s" lineno expr msg
;;

(* Int conversions for values already range-checked *)
let z_int = Z.to_int
let in_range v lo hi = Z.geq v (Z.of_int lo) && Z.lt v (Z.of_int hi)
let index_error () = raise (Crash "IndexError")

let round_half_even x =
  let f = Float.floor x in
  let d = x -. f in
  if d > 0.5
  then f +. 1.
  else if d < 0.5
  then f
  else if Float.rem f 2. = 0.
  then f
  else f +. 1.
;;

(* Python float(s) on a directive argument; None if it would raise ValueError *)
let py_float s =
  let t = String.lowercase_ascii s in
  let t' = if t <> "" && (t.[0] = '+' || t.[0] = '-') then String.sub t 1 (String.length t - 1) else t in
  let neg = t <> "" && t.[0] = '-' in
  if t' = "inf" || t' = "infinity"
  then Some (if neg then neg_infinity else infinity)
  else if t' = "nan"
  then Some nan
  else (
    let d = "[0-9](?:_?[0-9])*" in
    let pat =
      Printf.sprintf "[+\\-]?(?:%s(?:\\.(?:%s)?)?|\\.%s)(?:[eE][+\\-]?%s)?" d d d d
    in
    if Pyre.fullmatch (re pat) s = None
    then None
    else float_of_string_opt (String.concat "" (String.split_on_char '_' s)))
;;

let clkdiv_fields d =
  if Float.is_nan d then raise (Invalid_argument "cannot convert float NaN to integer");
  if Float.abs d = infinity then raise (Crash "OverflowError");
  let i = Float.trunc d in
  let f = round_half_even ((d -. i) *. 256.) in
  let i, f = if f = 256. then i +. 1., 0. else i, f in
  if not (1. <= i && i < 65536.)
  then
    raise
      (Invalid_argument
         (Printf.sprintf "clock divider %s out of range (1 .. 65535.996)" (Pyexpr.float_repr d)));
  int_of_float i, int_of_float f
;;

let assemble_block name lines defs =
  let prog =
    { name
    ; words = []
    ; reloc = []
    ; labels = Hashtbl.create 16
    ; origin = None
    ; wrap_bottom = 0
    ; wrap_top = None
    ; side_count = Z.zero
    ; side_opt = 0
    ; side_pindir = 0
    ; config = Hashtbl.create 8
    ; source_lines = []
    }
  in
  let items = ref [] in
  let pc = ref 0 in
  let ev s ln = eval_expr s defs ln in
  let nth l k =
    match List.nth_opt l k with
    | Some x -> x
    | None -> index_error ()
  in
  List.iter
    (fun (lineno, line, raw) ->
       let line = ref line in
       let continue = ref true in
       while !continue do
         match Pyre.match_ re_label !line with
         | None -> continue := false
         | Some m ->
           let l = Option.get (Pyre.group m !line 1) in
           if Hashtbl.mem prog.labels l then err "line %d: duplicate label %s" lineno l;
           Hashtbl.replace prog.labels l !pc;
           line := Option.get (Pyre.group m !line 2)
       done;
       let line = !line in
       if line <> ""
       then
         if line.[0] = '.'
         then (
           let parts = Pyre.split_ws line in
           let d = List.hd parts in
           let args = List.tl parts in
           let tail l = match l with [] -> [] | _ :: r -> r in
           match d with
           | ".define" ->
             let v = ev (String.concat " " (tail args)) lineno in
             Hashtbl.replace defs (nth args 0) (Z.to_string v)
           | ".origin" -> prog.origin <- Some (ev (String.concat " " args) lineno)
           | ".side_set" ->
             prog.side_count <- ev (nth args 0) lineno;
             prog.side_opt <- (if List.mem "opt" (tail args) then 1 else 0);
             prog.side_pindir <- (if List.mem "pindirs" (tail args) then 1 else 0);
             if Z.gt prog.side_count (Z.of_int 4)
             then err "line %d: side_set too wide" lineno
           | ".wrap_target" -> prog.wrap_bottom <- !pc
           | ".wrap" -> prog.wrap_top <- Some (!pc - 1)
           | ".word" ->
             items := (lineno, line, raw) :: !items;
             incr pc
           | ".clkdiv" ->
             let a = nth args 0 in
             (match py_float a with
              | None ->
                err "line %d: could not convert string to float: '%s'" lineno a
              | Some f ->
                (try
                   let i, fr = clkdiv_fields f in
                   Hashtbl.replace prog.config "clkdiv" (Clk (i, fr))
                 with
                 | Invalid_argument msg -> err "line %d: %s" lineno msg))
           | ".out_pins" | ".set_pins" ->
             let a = ev (nth args 0) lineno in
             let b = ev (nth args 1) lineno in
             Hashtbl.replace
               prog.config
               (String.sub d 1 (String.length d - 1))
               (Pair (a, b))
           | ".side_pins" | ".in_pins" ->
             Hashtbl.replace
               prog.config
               (String.sub d 1 (String.length d - 1))
               (Num (ev (nth args 0) lineno))
           | ".fifo" ->
             if args <> [ "join_tx" ] then err "line %d: .fifo takes join_tx" lineno;
             Hashtbl.replace prog.config "fjoin" Yes
           | ".shift_out" | ".shift_in" ->
             let auto_kw = if d = ".shift_out" then "autopull" else "autopush" in
             (match args with
              | ("left" | "right") :: rest ->
                let auto = List.mem auto_kw rest in
                let th = List.filter (fun a -> a <> auto_kw) rest in
                if List.length th > 1
                then err "line %d: %s right|left [%s] [threshold]" lineno d auto_kw;
                let n =
                  match th with
                  | [ t ] -> ev t lineno
                  | _ -> Z.of_int 32
                in
                if not (in_range n 1 33)
                then err "line %d: threshold must be 1..32" lineno;
                Hashtbl.replace
                  prog.config
                  (String.sub d 1 (String.length d - 1))
                  (Shift (List.hd args = "right", auto, n))
              | _ -> err "line %d: %s needs left or right" lineno d)
           | _ -> err "line %d: unknown directive %s" lineno d)
         else (
           items := (lineno, line, raw) :: !items;
           incr pc))
    lines;
  if prog.wrap_top = None then prog.wrap_top <- Some (!pc - 1);
  Hashtbl.iter (fun k v -> Hashtbl.replace defs k (string_of_int v)) prog.labels;
  prog, List.rev !items, defs
;;

let split_suffix line =
  let delay = ref None in
  let side = ref None in
  let line = ref line in
  let try_delay () =
    match Pyre.search re_delay !line with
    | Some m ->
      delay := Pyre.group m !line 1;
      line := Pyre.rstrip (String.sub !line 0 m.start)
    | None -> ()
  in
  try_delay ();
  (match Pyre.search re_side !line with
   | Some m ->
     side := Pyre.group m !line 1;
     line := Pyre.rstrip (String.sub !line 0 m.start)
   | None -> ());
  if !delay = None then try_delay ();
  !line, !side, !delay
;;

(* One instruction being encoded: its mnemonic, operands and helpers *)
type ln =
  { line : string
  ; defs : (string, string) Hashtbl.t
  ; lineno : int
  ; mn : string
  ; rest : string
  ; args : string list
  }

let make_ln line defs lineno =
  let parts = Pyre.split_ws (Pyre.replace_all line ~sub:"," ~by:" , ") in
  match parts with
  | [] -> raise (Crash "IndexError")
  | mn :: rest_l ->
    let rest = String.concat " " rest_l in
    { line
    ; defs
    ; lineno
    ; mn = String.lowercase_ascii mn
    ; rest
    ; args =
        (if rest = ""
         then []
         else List.map Pyre.strip (String.split_on_char ',' rest))
    }
;;

let ln_err l msg = err "line %d: %s: '%s'" l.lineno msg l.line
let value l s = eval_expr s l.defs l.lineno

let need l n usage =
  if List.length l.args <> n then ln_err l usage;
  l.args
;;

let loc l s =
  let s = String.lowercase_ascii (Pyre.strip s) in
  match Hashtbl.find_opt locs s with
  | Some v -> v
  | None -> ln_err l (Printf.sprintf "unknown operand %s" s)
;;

let count l s =
  let n = value l s in
  if not (in_range n 1 33) then ln_err l "bit count must be 1..32";
  z_int n land 31
;;

let reg l s =
  let s = String.lowercase_ascii (Pyre.strip s) in
  match s with
  | "r0" | "r1" | "r2" | "r3" | "x" | "y" -> Hashtbl.find locs s
  | _ -> ln_err l (Printf.sprintf "expected register, got %s" s)
;;

let ranged l s lo hi what =
  let v = value l s in
  if not (in_range v lo hi) then ln_err l (Printf.sprintf "%s %d..%d" what lo (hi - 1));
  z_int v
;;

let side_delay prog side delay l =
  let dbits = Z.sub (Z.of_int (8 - prog.side_opt)) prog.side_count in
  (* _side_delay shifts by dbits and side_count, so a negative count raises *)
  let sd = ref Z.zero in
  (match delay with
   | Some ds ->
     let d = value l ds in
     if Z.sign dbits < 0 then raise (Crash "ValueError");
     if not (Z.geq d Z.zero && Z.lt d (Z.shift_left Z.one (z_int dbits)))
     then
       ln_err l (Printf.sprintf "delay %s doesn't fit in %s bits" (Z.to_string d) (Z.to_string dbits));
     sd := Z.logor !sd d
   | None -> ());
  (match side with
   | Some ss ->
     if Z.equal prog.side_count Z.zero then ln_err l "side-set used without .side_set";
     let v = value l ss in
     if Z.sign prog.side_count < 0 then raise (Crash "ValueError");
     if not (Z.geq v Z.zero && Z.lt v (Z.shift_left Z.one (z_int prog.side_count)))
     then ln_err l "side value too large";
     sd := Z.logor !sd (Z.shift_left v (z_int dbits));
     if prog.side_opt = 1 then sd := Z.logor !sd (Z.of_int 128)
   | None ->
     if (not (Z.equal prog.side_count Z.zero)) && prog.side_opt = 0
     then ln_err l "side-set is mandatory (declare .side_set N opt to make it optional)");
  !sd
;;

let jmp l =
  let cond, sel, tgt =
    match l.args with
    | [ t ] -> Isa.c_always, Z.zero, t
    | [ c0; tgt ] ->
      let c_orig = Pyre.split_ws c0 in
      let c = List.map String.lowercase_ascii c_orig in
      let cs = String.concat " " c in
      let tail l = match l with [] -> [] | _ :: r -> r in
      let cond, sel =
        match Pyre.fullmatch re_jmp_reg cs with
        | Some m ->
          let bang = Pyre.group m cs 1 <> Some "" in
          let dec = Pyre.group m cs 3 <> None in
          if bang && dec then ln_err l "can't combine ! and --";
          let sel = reg l (Option.get (Pyre.group m cs 2)) in
          ( (if dec then Isa.c_dec else if bang then Isa.c_zero else Isa.c_nzero)
          , Z.of_int sel )
        | None ->
          (match c with
           | [] -> raise (Crash "IndexError")
           | ("pin" | "!pin") :: _ ->
             let v = value l (String.concat " " (tail c_orig)) in
             (if (List.hd c).[0] = '!' then Isa.c_npin else Isa.c_pin), v
           | ("f" | "!f" | "flag" | "!flag") :: _ ->
             let v = value l (String.concat " " (tail c_orig)) in
             if Z.gt v (Z.of_int 7) then ln_err l "flag index 0..7";
             (if (List.hd c).[0] = '!' then Isa.c_nf else Isa.c_f), v
           | _ ->
             (match cs with
              | "z" -> Isa.c_z, Z.zero
              | "!z" -> Isa.c_nz, Z.zero
              | "c" -> Isa.c_c, Z.zero
              | "!c" -> Isa.c_nc, Z.zero
              | "t" -> Isa.c_t, Z.zero
              | "!osre" -> Isa.c_nosre, Z.zero
              | "txne" -> Isa.c_txne, Z.zero
              | "rxf" -> Isa.c_rxf, Z.zero
              | _ -> ln_err l "unknown jmp condition"))
      in
      if not (in_range sel 0 32) then ln_err l "jmp selector out of range";
      cond, sel, tgt
    | _ -> ln_err l (Printf.sprintf "%s takes [cond,] target" l.mn)
  in
  let addr = ranged l tgt 0 1024 (l.mn ^ " target") in
  ( (if l.mn = "call" then Isa.op_call else Isa.op_jmp)
  , (cond lsl 15) lor (z_int sel lsl 10) lor addr )
;;

let wait_ l =
  let body = ref (Pyre.replace_all l.rest ~sub:"," ~by:" ") in
  let to_ = ref 0 in
  let treg = ref 3 in
  (match Pyre.search re_timeout !body with
   | Some m ->
     to_ := 1;
     (match Pyre.group m !body 1 with
      | Some r -> treg := reg l r
      | None -> treg := 3);
     let s = !body in
     body := String.sub s 0 m.start ^ String.sub s m.stop (String.length s - m.stop)
   | None -> ());
  let clr = ref 0 in
  if Pyre.search re_clear !body <> None
  then (
    clr := 1;
    body := Pyre.sub re_clear (fun _ -> "") !body);
  let qen = ref 0 in
  let qpol = ref 0 in
  let qpin = ref Z.zero in
  let stripped = Pyre.strip !body in
  (match Pyre.search re_if_pin stripped with
   | Some m ->
     qen := 1;
     qpin := value l (Option.get (Pyre.group m stripped 1));
     qpol := int_of_string (Option.get (Pyre.group m stripped 2));
     body := String.sub stripped 0 m.start;
     if not (in_range !qpin 0 32) then ln_err l "qualifier pin out of range"
   | None -> ());
  let toks = Pyre.split_ws !body in
  let kw = List.map String.lowercase_ascii toks in
  let ntoks = List.length toks in
  let rest_from k = String.concat " " (List.filteri (fun i _ -> i >= k) toks) in
  let t k = List.nth toks k in
  let w k = List.nth kw k in
  if ntoks >= 3 && w 0 = "pins" && w 1 = "!="
  then (
    if !qen <> 0 || !clr <> 0 then ln_err l "'wait pins != rN' takes no qualifier or 'clear'";
    let ref_ = reg l (t 2) in
    let m =
      if ntoks = 3
      then 0xFF
      else if ntoks >= 5 && w 3 = "mask"
      then ranged l (rest_from 4) 0 256 "mask"
      else ln_err l "wait pins != rN [mask m] [timeout rT]"
    in
    Isa.op_wait, (Isa.w_diff lsl 16) lor (ref_ lsl 14) lor (!to_ lsl 10) lor (!treg lsl 8) lor m)
  else (
    let kind, idx =
      if ntoks >= 3 && (w 0 = "1" || w 0 = "0") && w 1 = "pin"
      then (if t 0 = "1" then Isa.w_pin1 else Isa.w_pin0), value l (rest_from 2)
      else if ntoks >= 3 && (w 0 = "1" || w 0 = "0") && (w 1 = "flag" || w 1 = "irq")
      then (
        let v = value l (rest_from 2) in
        if Z.gt v (Z.of_int 7) then ln_err l "flag index 0..7";
        (if t 0 = "1" then Isa.w_flag1 else Isa.w_flag0), v)
      else if ntoks >= 2 && (w 0 = "rise" || w 0 = "fall" || w 0 = "edge")
      then
        ( (match w 0 with
           | "rise" -> Isa.w_rise
           | "fall" -> Isa.w_fall
           | _ -> Isa.w_edge)
        , value l (rest_from 1) )
      else ln_err l "bad wait"
    in
    if !clr <> 0 && kind <> Isa.w_flag1
    then ln_err l "'clear' only valid with 'wait 1 flag'";
    if not (in_range idx 0 32) then ln_err l "pin out of range";
    ( Isa.op_wait
    , (kind lsl 16)
      lor (z_int idx lsl 11)
      lor (!to_ lsl 10)
      lor (!treg lsl 8)
      lor (!clr lsl 7)
      lor (!qen lsl 6)
      lor (!qpol lsl 5)
      lor z_int !qpin ))
;;

let in_out l =
  match need l 2 (l.mn ^ " takes loc, count") with
  | [ where; n ] ->
    let wh = loc l where in
    if l.mn = "out" && not (List.mem wh Isa.out_dsts)
    then ln_err l (Printf.sprintf "out can't write %s" (List.hd l.args));
    (if l.mn = "in" then Isa.op_in else Isa.op_out), (wh lsl 15) lor count l n
  | _ -> assert false
;;

let push_pull l =
  let toks = Pyre.split_ws (String.lowercase_ascii l.rest) in
  let cond_kw = if l.mn = "push" then "iffull" else "ifempty" in
  let bad = List.exists (fun t -> t <> cond_kw && t <> "block" && t <> "noblock") toks in
  let uniq = List.length (List.sort_uniq compare toks) = List.length toks in
  if bad || (List.mem "block" toks && List.mem "noblock" toks) || not uniq
  then ln_err l (Printf.sprintf "%s [%s] [block|noblock]" l.mn cond_kw);
  let x =
    ((if List.mem cond_kw toks then 1 else 0) lsl 18)
    lor ((if List.mem "noblock" toks then 0 else 1) lsl 17)
  in
  (if l.mn = "push" then Isa.op_push else Isa.op_pull), x
;;

let mov l =
  match need l 2 "mov takes dst, src" with
  | [ dst; src ] ->
    let tail s k = String.sub s k (String.length s - k) in
    let mop, src =
      if Pyre.starts_with ~prefix:"::" src
      then Isa.m_rev, tail src 2
      else if src <> "" && (src.[0] = '!' || src.[0] = '~')
      then Isa.m_inv, tail src 1
      else Isa.m_none, src
    in
    let d = loc l dst in
    Isa.op_mov, (d lsl 15) lor (mop lsl 13) lor (loc l src lsl 9)
  | _ -> assert false
;;

let set_ l =
  match need l 2 "set pins|pindirs, value" with
  | [ dst; v ] ->
    let d = String.lowercase_ascii dst in
    if d <> "pins" && d <> "pindirs" then ln_err l "set pins|pindirs, value";
    Isa.op_set, ((if d = "pindirs" then 1 else 0) lsl 18) lor ranged l v 0 32 "set value"
  | _ -> assert false
;;

let ldi l =
  match need l 2 "ldi rN, value" with
  | [ rd; v ] ->
    let r = reg l rd in
    Isa.op_ldi, (r lsl 16) lor ranged l v 0 65536 "ldi immediate"
  | _ -> assert false
;;

let alu_index mn =
  let r = ref (-1) in
  Array.iteri (fun i n -> if n = mn then r := i) Isa.alu_names;
  !r
;;

let alu l =
  match need l 2 (l.mn ^ " rN, rM|value") with
  | [ rd; src ] ->
    let x = (alu_index l.mn lsl 16) lor (reg l rd lsl 14) in
    (match String.lowercase_ascii src with
     | "r0" | "r1" | "r2" | "r3" | "x" | "y" -> Isa.op_alu, x lor reg l src
     | _ ->
       (* src.lstrip("#") *)
       let n = String.length src in
       let i = ref 0 in
       while !i < n && src.[!i] = '#' do
         incr i
       done;
       let s = String.sub src !i (n - !i) in
       Isa.op_alu, x lor (1 lsl 13) lor ranged l s 0 8192 "alu immediate")
  | _ -> assert false
;;

let pin l =
  match need l 2 "pin p, lo|hi|toggle|hiz" with
  | [ p; action ] ->
    let a = String.lowercase_ascii action in
    let idx = ref (-1) in
    Array.iteri (fun i n -> if n = a then idx := i) Isa.pin_actions;
    if !idx < 0 then ln_err l "pin p, lo|hi|toggle|hiz";
    Isa.op_pin, (!idx lsl 17) lor ranged l p 0 32 "pin"
  | _ -> assert false
;;

let flag l =
  let toks = Pyre.split_ws l.rest in
  match toks with
  | a :: (_ :: _ as rest) when List.mem (String.lowercase_ascii a) [ "set"; "clr"; "clear" ] ->
    let k = String.lowercase_ascii a in
    Isa.op_flag, ((if k <> "set" then 1 else 0) lsl 18) lor ranged l (String.concat " " rest) 0 8 "flag index"
  | _ -> ln_err l "flag set|clr k"
;;

let encoder mn =
  match mn with
  | "nop" -> Some (fun _ -> Isa.op_mov, (Isa.null lsl 15) lor (Isa.null lsl 9))
  | "jmp" | "call" -> Some jmp
  | "wait" -> Some wait_
  | "ret" -> Some (fun _ -> Isa.op_mov, (Isa.pc lsl 15) lor (Isa.lr lsl 9))
  | "in" | "out" -> Some in_out
  | "push" | "pull" -> Some push_pull
  | "mov" -> Some mov
  | "set" -> Some set_
  | "ldi" -> Some ldi
  | "pin" -> Some pin
  | "flag" -> Some flag
  | "halt" -> Some (fun _ -> Isa.op_halt, 0)
  | "add" | "sub" | "and" | "or" | "xor" | "shl" | "shr" | "cmp" -> Some alu
  | _ -> None
;;

let encode line prog defs lineno =
  if Pyre.starts_with ~prefix:".word" line
  then (
    let v = eval_expr (String.sub line 5 (String.length line - 5)) defs lineno in
    Z.to_int (Z.logand v (Z.of_int 0xFFFFFFFF)), false)
  else (
    let line, side, delay = split_suffix line in
    let l = make_ln line defs lineno in
    let sd = side_delay prog side delay l in
    match encoder l.mn with
    | None -> ln_err l "unknown mnemonic"
    | Some enc ->
      let op, x = enc l in
      if Z.sign sd < 0 || Z.geq sd (Z.of_int 256) then raise (Crash "AssertionError");
      Isa.enc op (z_int sd) x, op = Isa.op_jmp || op = Isa.op_call)
;;

let finish_block (prog, items, defs) =
  List.iteri
    (fun idx (lineno, line, raw) ->
       let w, is_jmp = encode line prog defs lineno in
       prog.words <- w :: prog.words;
       if is_jmp then prog.reloc <- idx :: prog.reloc;
       prog.source_lines <- (idx, raw) :: prog.source_lines)
    items;
  prog.words <- List.rev prog.words;
  prog.reloc <- List.rev prog.reloc;
  prog.source_lines <- List.rev prog.source_lines;
  prog
;;

let assemble_all text ~defines =
  let blocks = ref [] in
  let cur = ref None in
  List.iteri
    (fun i raw ->
       let lineno = i + 1 in
       let head =
         match Pyre.search re_comment raw with
         | Some m -> String.sub raw 0 m.start
         | None -> raw
       in
       let line = Pyre.strip head in
       if Pyre.starts_with ~prefix:".program" line
       then (
         let p = Pyre.split_ws line in
         let name = match p with _ :: n :: _ -> n | _ -> "" in
         let b = name, ref [] in
         cur := Some b;
         blocks := b :: !blocks)
       else (
         if !cur = None && line <> ""
         then (
           let b = "", ref [] in
           cur := Some b;
           blocks := b :: !blocks);
         match !cur with
         | None -> ()
         | Some (_, l) -> l := (lineno, line, Pyre.strip raw) :: !l))
    (Pyre.splitlines text);
  List.rev_map
    (fun (name, l) -> name, List.rev !l)
    !blocks
  |> List.map (fun (name, lines) ->
    let defs = Hashtbl.create 32 in
    List.iter (fun (k, v) -> Hashtbl.replace defs k v) defines;
    finish_block (assemble_block name lines defs))
;;

let assemble text ~defines =
  match assemble_all text ~defines with
  | [ p ] -> p
  | l -> err "expected one .program, got %d" (List.length l)
;;

(* Program.relocate; Invalid_argument stands for ValueError *)
let relocate p base =
  if base < 0 || base >= 1024 then invalid_arg "relocation base must be in 0..1023";
  List.mapi
    (fun i w ->
       if List.mem i p.reloc
       then (
         let target = (w land 0x3FF) + base in
         if target >= 1024 then invalid_arg "relocated JMP exceeds address space";
         w land lnot 0x3FF lor target)
       else w)
    p.words
;;

(* The canonical dump ocaml/test/asm_parity.py compares with tools/pe/asm.py's *)
let dump b p =
  let pr fmt = Printf.bprintf b fmt in
  pr "program %s\n" p.name;
  pr "origin %s\n" (match p.origin with None -> "None" | Some z -> Z.to_string z);
  pr "wrap %d %s\n" p.wrap_bottom (string_of_int (Option.get p.wrap_top));
  pr "side %s %d %d\n" (Z.to_string p.side_count) p.side_opt p.side_pindir;
  Hashtbl.fold (fun k v acc -> (k, v) :: acc) p.config []
  |> List.sort compare
  |> List.iter (fun (k, v) -> pr "config %s %s\n" k (cfg_repr v));
  Hashtbl.fold (fun k v acc -> (k, v) :: acc) p.labels []
  |> List.sort compare
  |> List.iter (fun (k, v) -> pr "label %s %d\n" k v);
  List.iteri
    (fun i w -> pr "word %d %08x %d\n" i w (if List.mem i p.reloc then 1 else 0))
    p.words;
  List.iter (fun (i, raw) -> pr "src %d %s\n" i raw) p.source_lines
;;

(* Program.listing *)
let listing p base =
  let b = Buffer.create 4096 in
  let words = Array.of_list (relocate p base) in
  List.iteri
    (fun n (i, src) ->
       if n > 0 then Buffer.add_char b '\n';
       Printf.bprintf b "%4d: %08x  %-28s %s" (base + i) words.(i) (Isa.disasm words.(i)) src)
    p.source_lines;
  Buffer.contents b
;;
