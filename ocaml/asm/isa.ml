(* Instruction encodings: port of tools/pe/isa.py (docs/isa.md section 5). *)

let op_jmp = 0
let op_wait = 1
let op_in = 2
let op_out = 3
let op_push = 4
let op_pull = 5
let op_mov = 6
let op_set = 7
let op_ldi = 8
let op_alu = 9
let op_pin = 10
let op_flag = 11
let op_halt = 12
let op_call = 13

let opnames =
  [| "jmp"; "wait"; "in"; "out"; "push"; "pull"; "mov"; "set"; "ldi"; "alu"; "pin"; "flag"
   ; "halt"; "call"
  |]
;;

(* src/dst operand codes *)
let r0 = 0
let r1 = 1
let r2 = 2
let r3 = 3
let pins = 4
let pindirs = 5
let isr = 6
let osr = 7
let null = 8
let status = 9
let flags = 10
let pc = 11
let crc = 12
let line = 13
let lr = 14

let loc_names =
  [| "r0"; "r1"; "r2"; "r3"; "pins"; "pindirs"; "isr"; "osr"; "null"; "status"; "flags"
   ; "pc"; "crc"; "line"; "lr"
  |]
;;

let out_dsts = [ r0; r1; r2; r3; pins; pindirs; isr; null; pc; crc; line ]

(* JMP conditions *)
let c_always = 0
let c_zero = 1
let c_nzero = 2
let c_dec = 3
let c_pin = 4
let c_npin = 5
let c_z = 6
let c_nz = 7
let c_c = 8
let c_nc = 9
let c_t = 10
let c_nosre = 11
let c_txne = 12
let c_rxf = 13
let c_f = 14
let c_nf = 15

(* WAIT kinds *)
let w_pin1 = 0
let w_pin0 = 1
let w_rise = 2
let w_fall = 3
let w_edge = 4
let w_flag1 = 5
let w_flag0 = 6
let w_diff = 7

(* ALU ops *)
let a_add = 0
let a_sub = 1
let a_and = 2
let a_or = 3
let a_xor = 4
let a_shl = 5
let a_shr = 6
let a_cmp = 7
let alu_names = [| "add"; "sub"; "and"; "or"; "xor"; "shl"; "shr"; "cmp" |]

(* PIN actions *)
let p_lo = 0
let p_hi = 1
let p_toggle = 2
let p_hiz = 3
let pin_actions = [| "lo"; "hi"; "toggle"; "hiz" |]

(* MOV ops *)
let m_none = 0
let m_inv = 1
let m_rev = 2
let enc op sd operand = (op lsl 27) lor (sd lsl 19) lor operand
let bits v hi lo = (v lsr lo) land ((1 lsl (hi - lo + 1)) - 1)

let loc_name l = if l < Array.length loc_names then loc_names.(l) else Printf.sprintf "loc%d" l

(* isa.disasm: human-readable form, side/delay field shown raw *)
let disasm word =
  let op = (word lsr 27) land 31 in
  let sd = (word lsr 19) land 0xFF in
  let x = word land 0x7FFFF in
  let sfx = if sd <> 0 then Printf.sprintf "  ; sd=%d" sd else "" in
  let pr = Printf.sprintf in
  if op = op_jmp || op = op_call
  then (
    let cond = bits x 18 15 in
    let sel = bits x 14 10 in
    let addr = bits x 9 0 in
    let r = sel land 3 in
    let f = sel land 7 in
    let cs =
      match cond with
      | 0 -> ""
      | 1 -> pr "!r%d" r
      | 2 -> pr "r%d" r
      | 3 -> pr "r%d--" r
      | 4 -> pr "pin %d" sel
      | 5 -> pr "!pin %d" sel
      | 6 -> "z"
      | 7 -> "!z"
      | 8 -> "c"
      | 9 -> "!c"
      | 10 -> "t"
      | 11 -> "!osre"
      | 12 -> "txne"
      | 13 -> "rxf"
      | 14 -> pr "f %d" f
      | _ -> pr "!f %d" f
    in
    pr "%s %s%d%s" opnames.(op) (if cs <> "" then cs ^ ", " else "") addr sfx)
  else if op = op_wait
  then (
    let kind = bits x 18 16 in
    let idx = bits x 15 11 in
    let to_ = bits x 10 10 in
    let treg = bits x 9 8 in
    let clr = bits x 7 7 in
    let qen = bits x 6 6 in
    let qpol = bits x 5 5 in
    let qpin = bits x 4 0 in
    let tmo = if to_ = 1 then pr " timeout r%d" treg else "" in
    if kind = w_diff
    then pr "wait pins != r%d mask 0x%02x%s%s" (bits x 15 14) (bits x 7 0) tmo sfx
    else (
      let k =
        match kind with
        | 0 -> pr "1 pin %d" idx
        | 1 -> pr "0 pin %d" idx
        | 2 -> pr "rise %d" idx
        | 3 -> pr "fall %d" idx
        | 4 -> pr "edge %d" idx
        | 5 -> pr "1 flag %d" (idx land 7)
        | _ -> pr "0 flag %d" (idx land 7)
      in
      let q = if qen = 1 then pr " if pin %d == %d" qpin qpol else "" in
      pr "wait %s%s%s%s%s" k q (if clr = 1 then " clear" else "") tmo sfx))
  else if op = op_in || op = op_out
  then (
    let c = bits x 4 0 in
    pr "%s %s, %d%s" opnames.(op) (loc_name (bits x 18 15)) (if c = 0 then 32 else c) sfx)
  else if op = op_push || op = op_pull
  then (
    let a = bits x 18 18 in
    let b = bits x 17 17 in
    let q = if a = 1 then if op = op_push then "iffull " else "ifempty " else "" in
    pr "%s %s%s%s" opnames.(op) q (if b = 1 then "block" else "noblock") sfx)
  else if op = op_mov
  then (
    let m = [| ""; "~"; "::"; "?" |] in
    pr "mov %s, %s%s%s" (loc_name (bits x 18 15)) m.(bits x 14 13) (loc_name (bits x 12 9)) sfx)
  else if op = op_set
  then pr "set %s, %d%s" (if bits x 18 18 = 1 then "pindirs" else "pins") (bits x 4 0) sfx
  else if op = op_ldi
  then pr "ldi r%d, %d%s" (bits x 17 16) (bits x 15 0) sfx
  else if op = op_alu
  then (
    let s = if bits x 13 13 = 1 then pr "%d" (bits x 12 0) else pr "r%d" (bits x 1 0) in
    pr "%s r%d, %s%s" alu_names.(bits x 18 16) (bits x 15 14) s sfx)
  else if op = op_pin
  then pr "pin %d, %s%s" (bits x 4 0) pin_actions.(bits x 18 17) sfx
  else if op = op_flag
  then pr "flag %s %d%s" (if bits x 18 18 = 1 then "clr" else "set") (bits x 2 0) sfx
  else if op = op_halt
  then pr "halt%s" sfx
  else pr ".word 0x%08x" word
;;
