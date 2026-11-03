(* The Python expressions asm.py hands to eval(): after symbol substitution the
   text is limited to digits, a-f, x, b, _, + - * / % ( ) < > | & ~ and spaces.
   Integers are arbitrary precision, as in Python; a float literal (1e3) gives a
   float, and bools are ints. [Eval_error] carries a description; the wording of
   Python's parser is not reproduced (docs/hardcaml-port.md). *)

exception Eval_error of string

type v =
  | I of Z.t
  | F of float
  | Tup (* () *)

let fail s = raise (Eval_error s)

type tok =
  | Num of v
  | Op of string
  | Lp
  | Rp
  | End

let is_digit c = c >= '0' && c <= '9'
let is_hex c = is_digit c || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F')
let is_ident c = Pyre.is_word c

(* digits with single underscores between them, from i; returns the end *)
let digits s i ok =
  let n = String.length s in
  let rec go i =
    if i < n && ok s.[i]
    then go (i + 1)
    else if i + 1 < n && s.[i] = '_' && ok s.[i + 1]
    then go (i + 1)
    else i
  in
  if i < n && ok s.[i] then go i else fail "invalid syntax"
;;

let no_us s = String.concat "" (String.split_on_char '_' s)

let number s i =
  let n = String.length s in
  let radix = if i + 1 < n && s.[i] = '0' then Char.lowercase_ascii s.[i + 1] else ' ' in
  let finish j v =
    if j < n && (is_ident s.[j]) then fail "invalid decimal literal";
    Num v, j
  in
  if radix = 'x' || radix = 'b'
  then (
    let ok = if radix = 'x' then is_hex else fun c -> c = '0' || c = '1' in
    let st = i + 2 in
    let st' = if st < n && s.[st] = '_' then st + 1 else st in
    let j = digits s st' ok in
    if j < n && is_digit s.[j] then fail "invalid digit in literal";
    let t = no_us (String.sub s st' (j - st')) in
    finish j (I (Z.of_string_base (if radix = 'x' then 16 else 2) t)))
  else (
    let j = digits s i is_digit in
    let text = no_us (String.sub s i (j - i)) in
    let exp_at =
      if j < n && (s.[j] = 'e' || s.[j] = 'E')
      then (
        let k = j + 1 in
        let k = if k < n && (s.[k] = '+' || s.[k] = '-') then k + 1 else k in
        if k < n && is_digit s.[k] then Some k else None)
      else None
    in
    match exp_at with
    | Some k ->
      let e = digits s k is_digit in
      let lit = text ^ no_us (String.sub s j (e - j)) in
      finish e (F (float_of_string lit))
    | None ->
      if String.length text > 1
         && text.[0] = '0'
         && String.exists (fun c -> c <> '0') text
      then fail "leading zeros in decimal integer literals are not permitted";
      finish j (I (Z.of_string text)))
;;

let tokenize s =
  let n = String.length s in
  let rec go i acc =
    if i >= n
    then List.rev (End :: acc)
    else (
      let c = s.[i] in
      let two = if i + 1 < n then String.sub s i 2 else "" in
      if c = ' '
      then go (i + 1) acc
      else if is_digit c
      then (
        let t, j = number s i in
        go j (t :: acc))
      else if c = '('
      then go (i + 1) (Lp :: acc)
      else if c = ')'
      then go (i + 1) (Rp :: acc)
      else if two = "**" || two = "//" || two = "<<" || two = ">>"
      then go (i + 2) (Op two :: acc)
      else if two = "<>"
      then fail "invalid syntax"
      else if String.contains "*/%+-<>|&~" c
      then go (i + 1) (Op (String.make 1 c) :: acc)
      else fail "invalid syntax")
  in
  go 0 []
;;

let as_float = function
  | I z -> Z.to_float z
  | F f -> f
  | Tup -> fail "unsupported operand type"
;;

let need_int = function
  | I z -> z
  | _ -> fail "unsupported operand type"
;;

let of_bool b = I (if b then Z.one else Z.zero)
let is_float = function F _ -> true | _ -> false

let arith op a b =
  match a, b with
  | Tup, _ | _, Tup -> fail "unsupported operand type"
  | I x, I y ->
    (match op with
     | "+" -> I (Z.add x y)
     | "-" -> I (Z.sub x y)
     | "*" -> I (Z.mul x y)
     | "//" ->
       if Z.equal y Z.zero then fail "integer division or modulo by zero";
       I (Z.fdiv x y)
     | "%" ->
       if Z.equal y Z.zero then fail "integer modulo by zero";
       I (Z.sub x (Z.mul y (Z.fdiv x y)))
     | _ -> assert false)
  | _ ->
    let x = as_float a in
    let y = as_float b in
    (match op with
     | "+" -> F (x +. y)
     | "-" -> F (x -. y)
     | "*" -> F (x *. y)
     | "//" ->
       if y = 0. then fail "float floor division by zero";
       F (Float.floor (x /. y))
     | "%" ->
       if y = 0. then fail "float modulo by zero";
       let r = Float.rem x y in
       F (if r <> 0. && (r < 0.) <> (y < 0.) then r +. y else r)
     | _ -> assert false)
;;

let power a b =
  match a, b with
  | Tup, _ | _, Tup -> fail "unsupported operand type"
  | I x, I y when Z.sign y >= 0 ->
    if Z.numbits y > 24 then fail "exponent too large";
    I (Z.pow x (Z.to_int y))
  | _ ->
    let x = as_float a in
    let y = as_float b in
    if x = 0. && y < 0. then fail "zero to a negative power";
    F (x ** y)
;;

let shift op a b =
  let x = need_int a in
  let y = need_int b in
  if Z.sign y < 0 then fail "negative shift count";
  if Z.numbits y > 26 then fail "shift count too large";
  let k = Z.to_int y in
  I (if op = "<<" then Z.shift_left x k else Z.shift_right x k)
;;

let compare_v a b =
  match a, b with
  | Tup, _ | _, Tup -> fail "unsupported comparison"
  | I x, I y -> Z.compare x y
  | _ -> compare (as_float a) (as_float b)
;;

let eval text =
  let toks = ref (tokenize text) in
  let peek () = List.hd !toks in
  let advance () = toks := List.tl !toks in
  let is_op s = peek () = Op s in
  let rec comparison () =
    (* a < b < c means (a < b) and (b < c) *)
    let first = bor () in
    if is_op "<" || is_op ">"
    then (
      let rec chain left =
        if is_op "<" || is_op ">"
        then (
          let lt = is_op "<" in
          advance ();
          let right = bor () in
          let c = compare_v left right in
          let ok = if lt then c < 0 else c > 0 in
          if ok then chain right else (skip_chain (); of_bool false))
        else of_bool true
      and skip_chain () =
        if is_op "<" || is_op ">"
        then (
          advance ();
          ignore (bor ());
          skip_chain ())
      in
      chain first)
    else first
  and bor () =
    let v = ref (band ()) in
    while is_op "|" do
      advance ();
      let r = band () in
      v := I (Z.logor (need_int !v) (need_int r))
    done;
    !v
  and band () =
    let v = ref (shifts ()) in
    while is_op "&" do
      advance ();
      let r = shifts () in
      v := I (Z.logand (need_int !v) (need_int r))
    done;
    !v
  and shifts () =
    let v = ref (sum ()) in
    while is_op "<<" || is_op ">>" do
      let op = if is_op "<<" then "<<" else ">>" in
      advance ();
      let r = sum () in
      v := shift op !v r
    done;
    !v
  and sum () =
    let v = ref (term ()) in
    while is_op "+" || is_op "-" do
      let op = if is_op "+" then "+" else "-" in
      advance ();
      let r = term () in
      v := arith op !v r
    done;
    !v
  and term () =
    let v = ref (unary ()) in
    while is_op "*" || is_op "//" || is_op "%" do
      let op = if is_op "*" then "*" else if is_op "//" then "//" else "%" in
      advance ();
      let r = unary () in
      v := arith op !v r
    done;
    !v
  and unary () =
    if is_op "-"
    then (
      advance ();
      match unary () with
      | I z -> I (Z.neg z)
      | F f -> F (-.f)
      | Tup -> fail "bad operand type")
    else if is_op "+"
    then (
      advance ();
      match unary () with
      | Tup -> fail "bad operand type"
      | v -> v)
    else if is_op "~"
    then (
      advance ();
      match unary () with
      | I z -> I (Z.lognot z)
      | _ -> fail "bad operand type")
    else pow ()
  and pow () =
    let base = atom () in
    if is_op "**"
    then (
      advance ();
      let e = unary () in
      power base e)
    else base
  and atom () =
    match peek () with
    | Num v ->
      advance ();
      v
    | Lp ->
      advance ();
      if peek () = Rp
      then (
        advance ();
        Tup)
      else (
        let v = comparison () in
        if peek () <> Rp then fail "'(' was never closed";
        advance ();
        v)
    | _ -> fail "invalid syntax"
  in
  let v = comparison () in
  if peek () <> End then fail "invalid syntax";
  match v with
  | I z -> z
  | F f ->
    if Float.is_nan f || Float.is_integer f = false && Float.abs f = infinity
    then fail "cannot convert float to integer";
    if Float.abs f = infinity then fail "cannot convert float infinity to integer";
    Z.of_float f
  | Tup -> fail "int() argument must be a string, a bytes-like object or a real number"
;;

ignore is_float

(* repr(float) *)
let float_repr x =
  if Float.is_nan x
  then "nan"
  else if x = infinity
  then "inf"
  else if x = neg_infinity
  then "-inf"
  else if x = 0.
  then if 1. /. x < 0. then "-0.0" else "0.0"
  else (
    let rec shortest p =
      let s = Printf.sprintf "%.*e" (p - 1) x in
      if p >= 17 || float_of_string s = x then s else shortest (p + 1)
    in
    let s = shortest 1 in
    let neg = s.[0] = '-' in
    let s = if neg then String.sub s 1 (String.length s - 1) else s in
    let e_at = String.index s 'e' in
    let mant = String.sub s 0 e_at in
    let exp = int_of_string (String.sub s (e_at + 1) (String.length s - e_at - 1)) in
    let digits = Pyre.replace_all mant ~sub:"." ~by:"" in
    let nd = String.length digits in
    let body =
      if exp >= -4 && exp < 16
      then
        if exp >= nd - 1
        then digits ^ String.make (exp - nd + 1) '0' ^ ".0"
        else if exp >= 0
        then String.sub digits 0 (exp + 1) ^ "." ^ String.sub digits (exp + 1) (nd - exp - 1)
        else "0." ^ String.make (-exp - 1) '0' ^ digits
      else (
        let m =
          if nd = 1 then digits else String.sub digits 0 1 ^ "." ^ String.sub digits 1 (nd - 1)
        in
        Printf.sprintf "%se%s%02d" m (if exp < 0 then "-" else "+") (abs exp))
    in
    (if neg then "-" else "") ^ body)
;;
