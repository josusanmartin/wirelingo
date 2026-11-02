(* The small part of Python's re that tools/pe/asm.py uses, with its backtracking
   semantics (leftmost match, greedy and lazy quantifiers), so the assembler's
   patterns can be ported verbatim. ASCII only: asm.py's patterns only ever match
   ASCII in practice, because comments are removed first. *)

type node =
  | Chr of char
  | Any
  | Cls of (char -> bool)
  | Seq of node list
  | Alt of node list
  | Rep of node * int * int * bool (* node, min, max, greedy *)
  | Grp of int * node
  | Bol
  | Eol
  | Word_b
  | Not_after of (char -> bool) (* (?<![...]), one character *)

type t =
  { root : node
  ; ngroups : int
  ; icase : bool
  }

let is_word c =
  (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c = '_'
;;

let is_space c =
  c = ' ' || c = '\t' || c = '\n' || c = '\r' || c = '\011' || c = '\012' || c = '\028'
  || c = '\029' || c = '\030' || c = '\031'
;;

let is_digit c = c >= '0' && c <= '9'

let compile ?(icase = false) pat =
  let n = String.length pat in
  let i = ref 0 in
  let ngroups = ref 0 in
  let peek () = if !i < n then Some pat.[!i] else None in
  let eat c = if peek () = Some c then (incr i; true) else false in
  let escape_class c =
    match c with
    | 's' -> Some is_space
    | 'S' -> Some (fun c -> not (is_space c))
    | 'd' -> Some is_digit
    | 'w' -> Some is_word
    | _ -> None
  in
  let cls () =
    let neg = eat '^' in
    let items = ref [] in
    let first = ref true in
    while !first || peek () <> Some ']' do
      first := false;
      let c = pat.[!i] in
      incr i;
      let lo =
        if c = '\\'
        then (
          let e = pat.[!i] in
          incr i;
          match escape_class e with
          | Some f -> items := f :: !items; None
          | None -> Some e)
        else Some c
      in
      match lo with
      | None -> ()
      | Some lo ->
        if !i + 1 < n && pat.[!i] = '-' && pat.[!i + 1] <> ']'
        then (
          let hi = pat.[!i + 1] in
          i := !i + 2;
          items := (fun c -> c >= lo && c <= hi) :: !items)
        else items := (fun c -> c = lo) :: !items
    done;
    incr i;
    let fs = !items in
    let f c = List.exists (fun g -> g c) fs in
    if neg then fun c -> not (f c) else f
  in
  let rec alt () =
    let first = seq () in
    if peek () = Some '|'
    then (
      let rest = ref [ first ] in
      while eat '|' do
        rest := seq () :: !rest
      done;
      Alt (List.rev !rest))
    else first
  and seq () =
    let items = ref [] in
    let continue = ref true in
    while !continue do
      match peek () with
      | None | Some '|' | Some ')' -> continue := false
      | Some _ -> items := quantified (atom ()) :: !items
    done;
    Seq (List.rev !items)
  and quantified a =
    let lazy_of lo hi =
      let greedy = not (eat '?') in
      Rep (a, lo, hi, greedy)
    in
    if eat '*'
    then lazy_of 0 max_int
    else if eat '+'
    then lazy_of 1 max_int
    else if eat '?'
    then lazy_of 0 1
    else a
  and atom () =
    let c = pat.[!i] in
    incr i;
    match c with
    | '.' -> Any
    | '^' -> Bol
    | '$' -> Eol
    | '[' -> Cls (cls ())
    | '(' ->
      if eat '?'
      then
        if eat ':'
        then (
          let a = alt () in
          ignore (eat ')');
          a)
        else (
          (* (?<![...]) *)
          ignore (eat '<');
          ignore (eat '!');
          ignore (eat '[');
          let f = cls () in
          ignore (eat ')');
          Not_after f)
      else (
        incr ngroups;
        let g = !ngroups in
        let a = alt () in
        ignore (eat ')');
        Grp (g, a))
    | '\\' ->
      let e = pat.[!i] in
      incr i;
      if e = 'b'
      then Word_b
      else (
        match escape_class e with
        | Some f -> Cls f
        | None -> Chr e)
    | c -> Chr c
  in
  let root = alt () in
  { root; ngroups = !ngroups; icase }
;;

type m =
  { start : int
  ; stop : int
  ; groups : (int * int) option array
  }

let group m s k =
  match m.groups.(k) with
  | Some (a, b) -> Some (String.sub s a (b - a))
  | None -> None
;;

let exec re s ~at ~full =
  let n = String.length s in
  let groups = Array.make (re.ngroups + 1) None in
  let eqc a b =
    if re.icase then Char.lowercase_ascii a = Char.lowercase_ascii b else a = b
  in
  let testc f c =
    f c
    || (re.icase && (f (Char.lowercase_ascii c) || f (Char.uppercase_ascii c)))
  in
  let word_at k = k >= 0 && k < n && is_word s.[k] in
  let rec go node pos k =
    match node with
    | Chr c -> pos < n && eqc c s.[pos] && k (pos + 1)
    | Any -> pos < n && s.[pos] <> '\n' && k (pos + 1)
    | Cls f -> pos < n && testc f s.[pos] && k (pos + 1)
    | Seq l ->
      let rec seq l pos =
        match l with
        | [] -> k pos
        | x :: r -> go x pos (fun p -> seq r p)
      in
      seq l pos
    | Alt l -> List.exists (fun a -> go a pos k) l
    | Grp (g, a) ->
      let saved = groups.(g) in
      go a pos (fun p ->
        let inner = groups.(g) in
        groups.(g) <- Some (pos, p);
        k p
        ||
        (groups.(g) <- inner;
         false))
      ||
      (groups.(g) <- saved;
       false)
    | Rep (a, lo, hi, greedy) ->
      let rec rep count pos =
        let more () =
          count < hi && go a pos (fun p -> (p > pos || count < lo) && rep (count + 1) p)
        in
        if count < lo
        then more ()
        else if greedy
        then more () || k pos
        else k pos || more ()
      in
      rep 0 pos
    | Bol -> pos = 0 && k pos
    | Eol -> (pos = n || (pos = n - 1 && s.[pos] = '\n')) && k pos
    | Word_b -> word_at (pos - 1) <> word_at pos && k pos
    | Not_after f -> (pos = 0 || not (testc f s.[pos - 1])) && k pos
  in
  let stop = ref (-1) in
  if go re.root at (fun p ->
       if full && p <> n
       then false
       else (
         stop := p;
         true))
  then Some { start = at; stop = !stop; groups = Array.copy groups }
  else None
;;

let match_ re s = exec re s ~at:0 ~full:false
let fullmatch re s = exec re s ~at:0 ~full:true

let search_from re s from =
  let n = String.length s in
  let rec loop at =
    if at > n
    then None
    else (
      match exec re s ~at ~full:false with
      | Some m -> Some m
      | None -> loop (at + 1))
  in
  loop from
;;

let search re s = search_from re s 0

(* re.sub: f gets the matched text; non-overlapping, left to right *)
let sub re f s =
  let b = Buffer.create (String.length s) in
  let n = String.length s in
  let rec loop pos =
    match search_from re s pos with
    | None -> Buffer.add_substring b s pos (n - pos)
    | Some m ->
      Buffer.add_substring b s pos (m.start - pos);
      Buffer.add_string b (f (String.sub s m.start (m.stop - m.start)));
      if m.stop = m.start
      then (
        if m.stop < n then Buffer.add_char b s.[m.stop];
        if m.stop < n then loop (m.stop + 1))
      else loop m.stop
  in
  loop 0;
  Buffer.contents b
;;

(* ---- str helpers with Python's meaning *)
let strip s =
  let n = String.length s in
  let i = ref 0 in
  while !i < n && is_space s.[!i] do
    incr i
  done;
  let j = ref n in
  while !j > !i && is_space s.[!j - 1] do
    decr j
  done;
  String.sub s !i (!j - !i)
;;

let rstrip s =
  let j = ref (String.length s) in
  while !j > 0 && is_space s.[!j - 1] do
    decr j
  done;
  String.sub s 0 !j
;;

let split_ws s =
  let n = String.length s in
  let out = ref [] in
  let i = ref 0 in
  while !i < n do
    while !i < n && is_space s.[!i] do
      incr i
    done;
    if !i < n
    then (
      let st = !i in
      while !i < n && not (is_space s.[!i]) do
        incr i
      done;
      out := String.sub s st (!i - st) :: !out)
  done;
  List.rev !out
;;

let starts_with ~prefix s =
  let p = String.length prefix in
  String.length s >= p && String.sub s 0 p = prefix
;;

let replace_all s ~sub:a ~by =
  let la = String.length a in
  let b = Buffer.create (String.length s) in
  let i = ref 0 in
  let n = String.length s in
  while !i < n do
    if !i + la <= n && String.sub s !i la = a
    then (
      Buffer.add_string b by;
      i := !i + la)
    else (
      Buffer.add_char b s.[!i];
      incr i)
  done;
  Buffer.contents b
;;

(* str.splitlines, on the UTF-8 encoding of the file's text *)
let splitlines s =
  let n = String.length s in
  let out = ref [] in
  let st = ref 0 in
  let i = ref 0 in
  let cut len =
    out := String.sub s !st (!i - !st) :: !out;
    i := !i + len;
    st := !i
  in
  while !i < n do
    match s.[!i] with
    | '\r' -> if !i + 1 < n && s.[!i + 1] = '\n' then cut 2 else cut 1
    | '\n' | '\011' | '\012' | '\028' | '\029' | '\030' -> cut 1
    | '\xc2' when !i + 1 < n && s.[!i + 1] = '\x85' -> cut 2
    | '\xe2'
      when !i + 2 < n && s.[!i + 1] = '\x80' && (s.[!i + 2] = '\xa8' || s.[!i + 2] = '\xa9')
      -> cut 3
    | _ -> incr i
  done;
  if !st < n then out := String.sub s !st (n - !st) :: !out;
  List.rev !out
;;
