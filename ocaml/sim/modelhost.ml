(* Run firmware on the cycle model with pin-level peripherals: port of
   tools/pe/modelhost.py. *)

type t =
  { chip : Chip.chip
  ; mutable ui : int (* ui_in[4:0] *)
  ; mutable uio : int (* value external devices drive on uio, where they drive *)
  ; mutable ext_oe : int (* uio bits driven externally *)
  ; mutable pull_up : int
  }

let create ?(bank_words = 256) ?(fifo_depth = 8) () =
  { chip = Chip.create ~bank_words ~ncores:4 ~nbanks:4 ~fifo_depth
  ; ui = 0
  ; uio = 0
  ; ext_oe = 0
  ; pull_up = 0
  }
;;

(* resolved uio bus: wired-AND of chip and external drivers, pull-ups *)
let bus_uio t =
  let _, out, oe = Chip.outputs t.chip in
  let v = ref 0 in
  for i = 0 to 7 do
    let c = (oe lsr i) land 1 in
    let e = (t.ext_oe lsr i) land 1 in
    let b =
      if c = 1 && e = 1
      then (out lsr i) land 1 land ((t.uio lsr i) land 1)
      else if c = 1
      then (out lsr i) land 1
      else if e = 1
      then (t.uio lsr i) land 1
      else (t.pull_up lsr i) land 1
    in
    v := !v lor (b lsl i)
  done;
  !v
;;

let step_chip ?(we = false) ?(addr = 0) ?(wdata = 0) ?(commit = false) ?(snap = false)
      ?(cancel = false) t =
  Chip.step
    t.chip
    ~ui_in:t.ui
    ~uio_in:(bus_uio t)
    ~we
    ~addr
    ~wdata
    ~commit
    ~snap
    ~cancel
    ~ui_mid:None
    ~uio_mid:None
;;

let write t addr data = step_chip t ~we:true ~addr ~wdata:data
let apply t writes = List.iter (fun (a, d) -> write t a d) writes

let snap t addr =
  let v = Chip.read t.chip addr in
  step_chip t ~addr ~snap:true;
  v
;;

let commit t addr = step_chip t ~addr ~commit:true
let cancel t = step_chip t ~cancel:true

(* a one-word read transaction; pop lets it complete, popping a FIFO word *)
let read ?(pop = false) t addr =
  let v = snap t addr in
  if pop then commit t addr;
  cancel t;
  v
;;

let start t mask = write t Chip.a_ctrl (t.chip.enable lor mask lor (mask lsl 8))
let push t core word = write t (Chip.core_base + (Chip.core_stride * core) + Chip.c_fifo) word

let pop_all t core =
  let addr = Chip.core_base + (Chip.core_stride * core) + Chip.c_fifo in
  let out = ref [] in
  while t.chip.cores.(core).rxf <> [] do
    out := read ~pop:true t addr :: !out
  done;
  List.rev !out
;;

let step ?(n = 1) t =
  for _ = 1 to n do
    step_chip t
  done
;;

(* n cycles; [fn i t] may set t.ui/uio/ext_oe before each step, false stops *)
let run ?fn t n =
  let rec go i =
    if i >= n
    then n
    else (
      let continue =
        match fn with
        | Some f -> f i t
        | None -> true
      in
      if not continue
      then i
      else (
        step_chip t;
        go (i + 1)))
  in
  go 0
;;
