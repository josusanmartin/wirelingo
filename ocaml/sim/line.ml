(* The line unit in the cycle model: port of tools/pe/line.py (docs/isa.md 4.7).
   Every decision uses start-of-cycle state; Chip.step calls core 0 first (which
   may push or pop through [push]/[consume]), then [step], then [commit]. *)

let m16 = 0xFFFF
let nrzi = 1
let manch = 2

let bit_units p = 128 * (p land 0xFFF)

type t =
  { cover : string -> unit
  ; mutable cfg : int
  ; mutable div : int
  ; mutable pins : int
  ; (* TX *)
    mutable entries : int list list (* queued OUT LINE entries: bits, send order *)
  ; mutable tx_on : int
  ; mutable ending : int
  ; mutable eop_idle : int
  ; mutable half : int
  ; mutable bit : int
  ; mutable level : int
  ; mutable txn : int
  ; mutable tcnt : int
  ; mutable tfacc : int
  ; mutable trun : int
  ; mutable trunbit : int
  ; mutable trunv : int
  ; (* RX *)
    mutable rq : int list (* received bits, oldest first *)
  ; mutable rx_on : int
  ; mutable rcnt : int
  ; mutable rlast : int
  ; mutable rprev : int
  ; mutable quiet : int
  ; mutable q : int
  ; mutable n : int
  ; mutable rrun : int
  ; mutable rrunbit : int
  ; mutable rrunv : int
  ; mutable end_pend : int
  ; mutable ovf : int
  ; (* this cycle's core-0 traffic and the unit's *)
    mutable c_push : int list option
  ; mutable c_take : int
  ; mutable c_clear_end : bool
  ; mutable new_rx : int option
  ; mutable new_end : bool
  }

let create cover =
  { cover
  ; cfg = 0
  ; div = 0
  ; pins = 0
  ; entries = []
  ; tx_on = 0
  ; ending = 0
  ; eop_idle = 0
  ; half = 0
  ; bit = 0
  ; level = 0
  ; txn = 0
  ; tcnt = 0
  ; tfacc = 0
  ; trun = 0
  ; trunbit = 0
  ; trunv = 0
  ; rq = []
  ; rx_on = 0
  ; rcnt = 0
  ; rlast = 0
  ; rprev = 0
  ; quiet = 0
  ; q = 0
  ; n = 0
  ; rrun = 0
  ; rrunbit = 0
  ; rrunv = 0
  ; end_pend = 0
  ; ovf = 0
  ; c_push = None
  ; c_take = 0
  ; c_clear_end = false
  ; new_rx = None
  ; new_end = false
  }
;;

let reset l =
  l.entries <- [];
  l.tx_on <- 0;
  l.ending <- 0;
  l.eop_idle <- 0;
  l.half <- 0;
  l.bit <- 0;
  l.level <- 0;
  l.txn <- 0;
  l.tcnt <- 0;
  l.tfacc <- 0;
  l.trun <- 0;
  l.trunbit <- 0;
  l.trunv <- 0;
  l.rq <- [];
  l.rx_on <- 0;
  l.rcnt <- 0;
  l.rlast <- 0;
  l.rprev <- 0;
  l.quiet <- 0;
  l.q <- 0;
  l.n <- 0;
  l.rrun <- 0;
  l.rrunbit <- 0;
  l.rrunv <- 0;
  l.end_pend <- 0;
  l.ovf <- 0;
  l.c_push <- None;
  l.c_take <- 0;
  l.c_clear_end <- false
;;

let f l lo width = (l.cfg lsr lo) land ((1 lsl width) - 1)
let en l = f l 0 1
let code l = f l 1 2
let stuff_k l = f l 3 3
let stuff_ones l = f l 6 1
let diff l = f l 7 1
let end_ l = f l 8 2
let end_bits l = f l 10 3
let idle l = f l 13 1
let rx_en l = f l 14 1
let txp l = l.pins land 31
let rxp l = (l.pins lsr 8) land 31

let write l off v =
  match off with
  | 0 ->
    l.cfg <- v land 0x7FFF;
    if en l = 0 then reset l
  | 1 -> l.div <- v land 0xFFFFFFFF
  | 2 -> l.pins <- v land 0x1F1F
  | _ -> ()
;;

let status l =
  List.length l.entries
  lor (List.length l.rq lsl 8)
  lor (l.tx_on lsl 16)
  lor (l.rx_on lsl 17)
  lor (l.end_pend lsl 18)
  lor (l.ovf lsl 19)
;;

(* the pins the transmitter drives this cycle: (pin, value) *)
let drive l =
  if not (en l = 1 && l.tx_on = 1)
  then []
  else if diff l = 1
  then [ txp l, l.level; (txp l + 1) land 31, l.txn ]
  else [ txp l, l.level ]
;;

let room l = en l = 1 && List.length l.entries < 2
let push l bits = l.c_push <- Some bits

(* IN LINE n from the start-of-cycle queue: (bits, T), or None to stall *)
let peek l n =
  if en l = 0
  then None
  else if List.length l.rq >= n
  then Some (List.filteri (fun i _ -> i < n) l.rq, 0)
  else if l.end_pend = 1
  then Some (l.rq, 1)
  else None
;;

let consume l k t =
  l.cover (if t = 1 then "line:in_frame_end" else "line:in_bits");
  l.c_take <- k;
  l.c_clear_end <- t = 1
;;

let reload l =
  let s = l.tfacc + (l.div land 0xFF) in
  l.tfacc <- s land 0xFF;
  l.tcnt <- (((l.div lsr 8) land 0xFF) - 1 + (s lsr 8)) land m16
;;

let spb l = if code l = manch then 2 else 1

let pop_bit l =
  match l.entries with
  | (b :: rest) :: tl ->
    l.entries <- (if rest = [] then tl else rest :: tl);
    b
  | _ -> assert false
;;

let next_bit l =
  let k = stuff_k l in
  let finish b =
    l.bit <- b;
    let c = code l in
    if c = nrzi
    then (if b = 0 then l.level <- l.level lxor 1)
    else if c = manch
    then (
      l.half <- 0;
      l.level <- 1 - b)
    else l.level <- b;
    l.txn <- 1 - l.level
  in
  if k <> 0 && l.trun = k
  then (
    l.cover "line:tx_stuff";
    let b = if stuff_ones l = 1 then 0 else 1 - l.trunbit in
    if stuff_ones l = 1
    then l.trun <- 0
    else (
      l.trun <- 1;
      l.trunbit <- b;
      l.trunv <- 1);
    finish b)
  else if l.entries <> []
  then (
    l.cover "line:tx_bit";
    let b = pop_bit l in
    if stuff_ones l = 1
    then l.trun <- (if b = 1 then min (l.trun + 1) 7 else 0)
    else (
      l.trun <- (if l.trunv = 1 && b = l.trunbit then min (l.trun + 1) 7 else 1);
      l.trunbit <- b;
      l.trunv <- 1);
    finish b)
  else (
    l.cover "line:tx_frame_end";
    if end_bits l = 0 || end_ l = 0
    then l.tx_on <- 0
    else (
      l.ending <- end_bits l * spb l;
      if end_ l = 1
      then (
        l.level <- 0;
        l.txn <- 0)
      else if end_ l = 2
      then (
        l.level <- 1;
        l.txn <- 0)))
;;

let tx_step l =
  if l.tx_on = 0
  then (
    if l.entries <> []
    then (
      l.tx_on <- 1;
      l.level <- idle l;
      l.txn <- 1 - idle l;
      l.trun <- 0;
      l.trunv <- 0;
      l.ending <- 0;
      l.eop_idle <- 0;
      reload l;
      next_bit l))
  else if l.tcnt <> 0
  then l.tcnt <- (l.tcnt - 1) land m16
  else (
    reload l;
    if l.ending <> 0
    then (
      l.ending <- l.ending - 1;
      if l.ending = 0
      then
        if end_ l = 1 && l.eop_idle = 0
        then (
          l.eop_idle <- 1;
          l.ending <- spb l;
          l.level <- idle l;
          l.txn <- 1 - idle l)
        else l.tx_on <- 0)
    else if code l = manch && l.half = 0
    then (
      l.cover "line:tx_manchester_half";
      l.half <- 1;
      l.level <- l.bit;
      l.txn <- 1 - l.level)
    else next_bit l)
;;

let end_frame l =
  l.cover "line:rx_frame_end";
  l.rx_on <- 0;
  l.new_end <- true
;;

let deliver l b =
  let k = stuff_k l in
  if k <> 0 && l.rrun = k
  then (
    l.cover "line:rx_stuff";
    let expect = if stuff_ones l = 1 then 0 else 1 - l.rrunbit in
    if b <> expect
    then end_frame l
    else if stuff_ones l = 1
    then l.rrun <- 0
    else (
      l.rrun <- 1;
      l.rrunbit <- b;
      l.rrunv <- 1))
  else (
    if stuff_ones l = 1
    then l.rrun <- (if b = 1 then min (l.rrun + 1) 7 else 0)
    else (
      l.rrun <- (if l.rrunv = 1 && b = l.rrunbit then min (l.rrun + 1) 7 else 1);
      l.rrunbit <- b;
      l.rrunv <- 1);
    l.new_rx <- Some b)
;;

let sample l rx rxn =
  if diff l = 1 && rx = 0 && rxn = 0
  then end_frame l
  else (
    l.quiet <- min (l.quiet + 1) 15;
    let k = stuff_k l in
    if l.quiet >= (if k <> 0 then k + 2 else 8)
    then end_frame l
    else (
      let b = if code l = nrzi then (if rx = l.rlast then 1 else 0) else rx in
      l.rlast <- rx;
      deliver l b))
;;

let rx_step l pins_in rx_mid =
  let rx = (pins_in lsr rxp l) land 1 in
  let rxn = (pins_in lsr ((rxp l + 1) land 31)) land 1 in
  let h = rx_mid in
  let trans = rx <> l.rprev in
  let p = (l.div lsr 16) land m16 in
  let age = if h <> l.rprev then Some 96 else if rx <> h then Some 32 else None in
  let early = if h <> l.rprev then 1 else 0 in
  if l.rx_on = 0
  then (
    if trans
    then (
      l.rx_on <- 1;
      l.rrun <- 0;
      l.rrunv <- 0;
      l.quiet <- 0;
      if code l = manch
      then (
        l.q <- Option.get age - bit_units p;
        l.n <- 0;
        deliver l rx)
      else (
        l.rlast <- l.rprev;
        l.rcnt <- ((p lsr 1) - 1 - early) land m16)))
  else if code l = manch
  then (
    let p12 = p land 0xFFF in
    let q = l.q + 128 in
    let e = Option.map (fun a -> q - a) age in
    let wide = l.n < 32 in
    match e with
    | Some e when e >= -(if wide then 48 * p12 else 32 * p12) ->
      let g = min 5 (1 + (l.n / 6)) in
      l.q <- q - bit_units p - ((e + (1 lsl (g - 1))) asr g);
      l.n <- min (l.n + 1) 32;
      l.cover
        ("line:rx_edge_" ^ (if age = Some 96 then "first" else "second") ^ "_half");
      if not wide then l.cover "line:rx_locked";
      deliver l rx
    | _ ->
      if q >= 64 * p12
      then end_frame l
      else (
        if e <> None then l.cover "line:rx_edge_outside_window";
        l.q <- q))
  else if trans
  then (
    l.rcnt <- ((p lsr 1) - 1 - early) land m16;
    l.quiet <- 0)
  else if l.rcnt = 0
  then (
    l.rcnt <- (p - 1) land m16;
    sample l rx rxn)
  else l.rcnt <- (l.rcnt - 1) land m16;
  l.rprev <- rx
;;

let step l pins_in rx_mid =
  if en l = 0
  then (
    l.c_push <- None;
    l.c_take <- 0;
    l.c_clear_end <- false)
  else (
    l.new_rx <- None;
    l.new_end <- false;
    tx_step l;
    if rx_en l = 1
    then rx_step l pins_in rx_mid
    else l.rprev <- (pins_in lsr rxp l) land 1)
;;

let commit l =
  if en l = 1
  then (
    let start_len = List.length l.rq in
    if l.c_take <> 0 then l.rq <- List.filteri (fun i _ -> i >= l.c_take) l.rq;
    if l.c_clear_end then l.end_pend <- 0;
    (match l.new_rx with
     | Some b ->
       if start_len < 32
       then (
         l.rq <- l.rq @ [ b ];
         l.cover "line:rx_bit")
       else (
         l.ovf <- 1;
         l.cover "line:rx_overflow")
     | None -> ());
    if l.new_end then l.end_pend <- 1;
    (match l.c_push with
     | Some bits ->
       l.entries <- l.entries @ [ bits ];
       l.cover "line:out_entry"
     | None -> ());
    l.c_push <- None;
    l.c_take <- 0;
    l.c_clear_end <- false)
;;
