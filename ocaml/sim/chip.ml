(* Cycle-accurate model of pe_chip (everything except the SPI slave): port of
   tools/pe/sim.py. One call to [step] is one rising clock edge. Everything reads
   start-of-cycle state; effects combine into the next state, as in the RTL.
   The coverage counters are kept too, under sim.py's names. *)

let m32 = 0xFFFFFFFF
let m16 = 0xFFFF
let ncores_max = 4
let id_value = 0x50450002
let imem_words = 1024

(* register map *)
let a_id = 0x800
let a_ctrl = 0x801
let a_fset = 0x802
let a_fclr = 0x803
let a_syncbyp = 0x804
let a_pown_lo = 0x805
let a_pown_hi = 0x806
let a_od = 0x807
let a_pins = 0x808
let a_pout = 0x809
let a_poe = 0x80A
let a_hostcfg = 0x80B
let a_linecfg = 0x80C
let a_linepins = 0x80E
let a_linestat = 0x80F
let core_base = 0x400
let core_stride = 0x20
let c_clkdiv = 0
let c_pinctrl0 = 1
let c_pinctrl1 = 2
let c_shiftctrl = 3
let c_entry = 4
let c_wrap = 5
let c_crcctrl = 6
let c_crc = 7
let c_pc = 8
let c_status = 9
let c_r0 = 0xA
let c_isr = 0xE
let c_osr = 0xF
let c_fifo = 0x10
let bits v hi lo = (v lsr lo) land ((1 lsl (hi - lo + 1)) - 1)
let mask n = if n >= 62 then -1 else (1 lsl n) - 1

let rotr32 v s =
  let s = s land 31 in
  if s = 0 then v else ((v lsr s) lor (v lsl (32 - s))) land m32
;;

let rev32 v =
  let r = ref 0 in
  for i = 0 to 31 do
    if (v lsr i) land 1 = 1 then r := !r lor (1 lsl (31 - i))
  done;
  !r
;;

type core =
  { idx : int
  ; mutable div_int : int
  ; mutable div_frac : int
  ; mutable out_base : int
  ; mutable out_count : int
  ; mutable set_base : int
  ; mutable set_count : int
  ; mutable side_base : int
  ; mutable side_count : int
  ; mutable side_opt : int
  ; mutable side_pindir : int
  ; mutable in_base : int
  ; mutable out_shr : int
  ; mutable in_shr : int
  ; mutable autopull : int
  ; mutable autopush : int
  ; mutable pull_thresh : int
  ; mutable push_thresh : int
  ; mutable fjoin : int
  ; mutable entry : int
  ; mutable wrap_bottom : int
  ; mutable wrap_top : int
  ; mutable crc_poly : int
  ; mutable crc_w : int
  ; mutable in_tap : int
  ; mutable out_tap : int
  ; mutable crc : int
  ; r : int array
  ; mutable lr : int
  ; mutable txf : int list
  ; mutable rxf : int list
  ; mutable res : bool list
  ; mutable pc : int
  ; mutable isr : int
  ; mutable isr_cnt : int
  ; mutable osr : int
  ; mutable osr_cnt : int
  ; mutable z : int
  ; mutable c : int
  ; mutable t : int
  ; mutable dcnt : int
  ; mutable in_wait : int
  ; mutable wprev : int
  ; mutable wevent : int
  ; mutable cnt : int
  ; mutable facc : int
  ; mutable halted : int
  ; mutable fault : int
  ; mutable stalled : int
  }

type chip =
  { bank_words : int
  ; ncores : int
  ; nbanks : int
  ; fifo_depth : int
  ; imem : int array
  ; cores : core array
  ; mutable enable : int
  ; mutable ctrl_pend : int option
  ; mutable flags : int
  ; pin_owner : int array
  ; mutable od_mask : int
  ; mutable pin_out : int
  ; mutable pin_oe : int
  ; mutable hostcfg : int
  ; line : Line.t
  ; mutable sync1 : int
  ; mutable sync2 : int
  ; mutable sync_bypass : int
  ; mutable rx_hq : int
  ; mutable rx_h2 : int
  ; mutable pads_prev : int
  ; mutable cycle : int
  ; stats : (string, int) Hashtbl.t
  }

(* one core's effects in one cycle; -1 means the pin is not written *)
type fx =
  { out : int array
  ; oe : int array
  ; side_out : int array
  ; side_oe : int array
  ; mutable fset : int
  ; mutable fclr : int
  ; mutable txpop : bool
  ; mutable rxpush : int option
  }

(* the inputs and effects of one core cycle (_Exec) *)
type ex =
  { chip : chip
  ; pins_in : int
  ; eflags : int
  ; x : int
  ; fx : fx
  ; pull_th : int
  ; push_th : int
  ; pullq : int list
  ; rx_full : bool
  ; mutable jump : int option
  ; mutable complete : bool
  ; mutable new_wait : int
  ; mutable kind : int
  ; mutable wpin : int
  ; mutable qual : bool
  ; mutable ev : bool
  ; mutable seen : bool
  ; mutable diff : bool
  ; mutable edge_done : bool
  }

let cover chip event =
  let v = try Hashtbl.find chip.stats event with Not_found -> 0 in
  Hashtbl.replace chip.stats event (v + 1)
;;

let restart c =
  c.pc <- c.entry;
  c.isr <- 0;
  c.isr_cnt <- 0;
  c.osr <- 0;
  c.osr_cnt <- 32;
  c.z <- 0;
  c.c <- 0;
  c.t <- 0;
  c.dcnt <- 0;
  c.in_wait <- 0;
  c.wprev <- 0;
  c.wevent <- 0;
  c.cnt <- 0;
  c.facc <- 0;
  c.halted <- 0;
  c.fault <- 0;
  c.stalled <- 0
;;

let make_core idx =
  let c =
    { idx
    ; div_int = 1
    ; div_frac = 0
    ; out_base = 0
    ; out_count = 0
    ; set_base = 0
    ; set_count = 0
    ; side_base = 0
    ; side_count = 0
    ; side_opt = 0
    ; side_pindir = 0
    ; in_base = 0
    ; out_shr = 0
    ; in_shr = 0
    ; autopull = 0
    ; autopush = 0
    ; pull_thresh = 0
    ; push_thresh = 0
    ; fjoin = 0
    ; entry = 256 * idx
    ; wrap_bottom = 0
    ; wrap_top = 1023
    ; crc_poly = 0
    ; crc_w = 0
    ; in_tap = 0
    ; out_tap = 0
    ; crc = 0
    ; r = [| 0; 0; 0; 0 |]
    ; lr = 0
    ; txf = []
    ; rxf = []
    ; res = []
    ; pc = 0
    ; isr = 0
    ; isr_cnt = 0
    ; osr = 0
    ; osr_cnt = 32
    ; z = 0
    ; c = 0
    ; t = 0
    ; dcnt = 0
    ; in_wait = 0
    ; wprev = 0
    ; wevent = 0
    ; cnt = 0
    ; facc = 0
    ; halted = 0
    ; fault = 0
    ; stalled = 0
    }
  in
  restart c;
  c
;;

let create ~bank_words ~ncores ~nbanks ~fifo_depth =
  assert (bank_words land (bank_words - 1) = 0 && bank_words <= 256);
  let stats = Hashtbl.create 64 in
  let cover_ event =
    let v = try Hashtbl.find stats event with Not_found -> 0 in
    Hashtbl.replace stats event (v + 1)
  in
  { bank_words
  ; ncores
  ; nbanks
  ; fifo_depth
  ; imem = Array.make (4 * bank_words) 0
  ; cores = Array.init ncores_max make_core
  ; enable = 0
  ; ctrl_pend = None
  ; flags = 0
  ; pin_owner = Array.make 32 0
  ; od_mask = 0
  ; pin_out = 0
  ; pin_oe = 0
  ; hostcfg = 0
  ; line = Line.create cover_
  ; sync1 = 0
  ; sync2 = 0
  ; sync_bypass = 0
  ; rx_hq = 0
  ; rx_h2 = 0
  ; pads_prev = 0
  ; cycle = 0
  ; stats
  }
;;

let status c =
  List.length c.txf
  lor (List.length c.rxf lsl 4)
  lor (c.z lsl 8)
  lor (c.c lsl 9)
  lor (c.t lsl 10)
  lor (c.stalled lsl 12)
  lor (c.halted lsl 13)
  lor (c.fault lsl 14)
;;

let read_reg c off =
  if off = c_crc
  then c.crc
  else if off = c_pc
  then c.pc
  else if off = c_status
  then status c
  else if off >= c_r0 && off < c_r0 + 4
  then c.r.(off - c_r0)
  else if off = c_isr
  then c.isr
  else if off = c_osr
  then c.osr
  else if off = c_fifo
  then (
    let n = List.length (List.filter Fun.id c.res) in
    if c.fjoin = 0 && n < 2 && List.length c.rxf > n then List.nth c.rxf n else 0)
  else 0
;;

let write_reg c off v =
  if off = c_clkdiv
  then (
    c.div_int <- (v lsr 8) land m16;
    c.div_frac <- v land 0xFF)
  else if off = c_pinctrl0
  then (
    c.out_base <- v land 31;
    c.out_count <- (v lsr 8) land 63;
    c.set_base <- (v lsr 16) land 31;
    c.set_count <- (v lsr 24) land 7)
  else if off = c_pinctrl1
  then (
    c.side_base <- v land 31;
    c.side_count <- (v lsr 8) land 7;
    c.side_opt <- (v lsr 12) land 1;
    c.side_pindir <- (v lsr 13) land 1;
    c.in_base <- (v lsr 16) land 31)
  else if off = c_shiftctrl
  then (
    c.out_shr <- v land 1;
    c.in_shr <- (v lsr 1) land 1;
    c.autopull <- (v lsr 2) land 1;
    c.autopush <- (v lsr 3) land 1;
    c.fjoin <- (v lsr 4) land 1;
    c.pull_thresh <- (v lsr 8) land 31;
    c.push_thresh <- (v lsr 16) land 31)
  else if off = c_entry
  then c.entry <- v land 1023
  else if off = c_wrap
  then (
    c.wrap_bottom <- v land 1023;
    c.wrap_top <- (v lsr 16) land 1023)
  else if off = c_crcctrl
  then (
    c.crc_poly <- v land m16;
    c.crc_w <- (v lsr 16) land 15;
    c.in_tap <- (v lsr 20) land 1;
    c.out_tap <- (v lsr 21) land 1)
  else if off = c_crc
  then c.crc <- v land m16
  else if off = c_pc
  then (
    c.pc <- v land 1023;
    c.dcnt <- 0;
    c.in_wait <- 0)
  else if off >= c_r0 && off < c_r0 + 4
  then c.r.(off - c_r0) <- v land m16
;;

let imem_index chip addr = ((addr lsr 8) * chip.bank_words) + (addr land (chip.bank_words - 1))

let fetch chip addr =
  if addr lsr 8 >= chip.nbanks then 0 else chip.imem.(imem_index chip addr)
;;

(* ---- the ops *)
let pin e p = (e.pins_in lsr (p land 31)) land 1
let is_edge_wait k = k = Isa.w_rise || k = Isa.w_fall || k = Isa.w_edge

let divider c =
  if c.cnt = 0
  then (
    let s = c.facc + c.div_frac in
    c.facc <- s land 0xFF;
    c.cnt <- (c.div_int - 1 + (s lsr 8)) land m16;
    true)
  else (
    c.cnt <- (c.cnt - 1) land m16;
    false)
;;

let track_wait c e =
  let x = e.x in
  e.kind <- bits x 18 16;
  e.wpin <- pin e (bits x 15 11);
  let qen = bits x 6 6 in
  let qpol = bits x 5 5 in
  let qpin = bits x 4 0 in
  e.qual <- qen = 0 || pin e qpin = qpol;
  let trans =
    if e.kind = Isa.w_rise
    then c.wprev = 0 && e.wpin = 1
    else if e.kind = Isa.w_fall
    then c.wprev = 1 && e.wpin = 0
    else c.wprev <> e.wpin
  in
  e.ev <- trans && e.qual;
  e.seen <- c.in_wait = 1 && c.wevent = 1;
  e.diff <- false;
  if e.kind = Isa.w_diff
  then (
    let ref_ = c.r.(bits x 15 14) in
    let d = (rotr32 e.pins_in c.in_base lxor ref_) land bits x 7 0 <> 0 in
    e.ev <- d;
    e.diff <- d);
  e.edge_done <- c.wevent = 1 || e.ev;
  if c.in_wait = 1 && qen = 1 && trans && (not e.qual) && is_edge_wait e.kind
  then cover e.chip "wait:qualifier_blocked_edge";
  if c.in_wait = 1
  then (
    c.wevent <- (if e.edge_done then 1 else 0);
    c.wprev <- e.wpin)
;;

let side_set c sd fx =
  let sc = min c.side_count 4 in
  let dbits = 8 - sc - c.side_opt in
  let side_en = if c.side_opt = 1 then (sd lsr 7) land 1 else 1 in
  if sc <> 0 && side_en = 1
  then (
    let sv = (sd lsr dbits) land mask sc in
    let tgt = if c.side_pindir = 1 then fx.side_oe else fx.side_out in
    for i = 0 to sc - 1 do
      tgt.((c.side_base + i) land 31) <- (sv lsr i) land 1
    done);
  sd land mask dbits
;;

let write_pins c e v oe =
  let tgt = if oe then e.fx.oe else e.fx.out in
  for i = 0 to min c.out_count 32 - 1 do
    tgt.((c.out_base + i) land 31) <- (v lsr i) land 1
  done
;;

let src c e code =
  if code < 4
  then c.r.(code)
  else if code = Isa.pins
  then rotr32 e.pins_in c.in_base
  else if code = Isa.isr
  then c.isr
  else if code = Isa.osr
  then c.osr
  else if code = Isa.status
  then status c land 0x7FF
  else if code = Isa.flags
  then e.eflags
  else if code = Isa.pc
  then c.pc
  else if code = Isa.crc
  then c.crc
  else if code = Isa.lr
  then c.lr
  else 0
;;

let crc_mask c = mask (c.crc_w + 1)

let crc_feed c b =
  let fb = ((c.crc lsr c.crc_w) land 1) lxor b in
  let m = crc_mask c in
  c.crc <- ((c.crc lsl 1) land m) lxor (if fb = 1 then c.crc_poly land m else 0)
;;

let write_dst c e dst v isr_count =
  if dst < 4
  then c.r.(dst) <- v land m16
  else if dst = Isa.pins
  then write_pins c e v false
  else if dst = Isa.pindirs
  then write_pins c e v true
  else if dst = Isa.isr
  then (
    c.isr <- v;
    c.isr_cnt <- isr_count)
  else if dst = Isa.osr
  then (
    c.osr <- v;
    c.osr_cnt <- 0)
  else if dst = Isa.pc
  then e.jump <- Some (v land 1023)
  else if dst = Isa.crc
  then c.crc <- v land crc_mask c
  else if dst = Isa.lr
  then c.lr <- v land 1023
;;

let op_jmp c e =
  let x = e.x in
  let cond = bits x 18 15 in
  let sel = bits x 14 10 in
  let addr = bits x 9 0 in
  let rs = sel land 3 in
  let rv = c.r.(rs) in
  let fl = (e.eflags lsr (sel land 7)) land 1 in
  let ok =
    match cond with
    | 0 -> true
    | 1 -> rv = 0
    | 2 | 3 -> rv <> 0
    | 4 -> pin e sel = 1
    | 5 -> pin e sel = 0
    | 6 -> c.z = 1
    | 7 -> c.z = 0
    | 8 -> c.c = 1
    | 9 -> c.c = 0
    | 10 -> c.t = 1
    | 11 -> c.osr_cnt < e.pull_th
    | 12 -> e.pullq <> []
    | 13 -> e.rx_full
    | 14 -> fl = 1
    | _ -> fl = 0
  in
  if cond = Isa.c_dec then c.r.(rs) <- (rv - 1) land m16;
  if ok then e.jump <- Some addr;
  ok
;;

let op_call c e =
  if op_jmp c e
  then (
    c.lr <- (if c.pc = c.wrap_top then c.wrap_bottom else (c.pc + 1) land 1023);
    cover e.chip "call:taken";
    if c.pc = c.wrap_top then cover e.chip "call:at_wrap_top")
;;

let op_wait c e =
  let x = e.x in
  let kind = e.kind in
  let idx = bits x 15 11 in
  let to_ = bits x 10 10 in
  let treg = bits x 9 8 in
  let clr = bits x 7 7 in
  let first = c.in_wait = 0 in
  let f = (e.eflags lsr (idx land 7)) land 1 in
  let done_ =
    if kind = Isa.w_pin1
    then e.wpin = 1 && e.qual
    else if kind = Isa.w_pin0
    then e.wpin = 0 && e.qual
    else if is_edge_wait kind
    then (not first) && e.edge_done
    else if kind = Isa.w_flag1
    then f = 1 && e.qual
    else if kind = Isa.w_flag0
    then f = 0 && e.qual
    else if first
    then e.diff
    else e.edge_done
  in
  if done_
  then (
    if is_edge_wait kind && not e.ev then cover e.chip "wait:edge_caught_between_ticks";
    if kind = Isa.w_diff
    then
      cover
        e.chip
        (if first
         then "wait:diff_first_attempt"
         else if e.seen
         then "wait:diff_caught_between_ticks"
         else "wait:diff_done");
    if to_ = 1 then c.t <- 0;
    if kind = Isa.w_flag1 && clr = 1
    then e.fx.fclr <- e.fx.fclr lor (1 lsl (idx land 7)))
  else if to_ = 1 && c.r.(treg) = 0
  then (
    c.t <- 1;
    cover e.chip "wait:timeout";
    if kind = Isa.w_diff then cover e.chip "wait:diff_timeout")
  else (
    if to_ = 1 then c.r.(treg) <- (c.r.(treg) - 1) land m16;
    e.complete <- false;
    e.new_wait <- 1;
    if first
    then (
      c.wprev <- e.wpin;
      c.wevent <- 0))
;;

let in_line c e n =
  match Line.peek e.chip.line n with
  | None -> e.complete <- false
  | Some (bs, t) ->
    let k = List.length bs in
    let v = ref 0 in
    List.iteri
      (fun i b ->
         v := !v lor (b lsl if c.in_shr = 1 then i else k - 1 - i))
      bs;
    let v = !v in
    let nisr =
      if k = 32
      then v
      else if k = 0
      then c.isr
      else if c.in_shr = 1
      then ((c.isr lsr k) lor (v lsl (32 - k))) land m32
      else ((c.isr lsl k) lor v) land m32
    in
    let ncnt = min 32 (c.isr_cnt + k) in
    let blocked = c.autopush = 1 && ncnt >= e.push_th && e.rx_full in
    if blocked
    then e.complete <- false
    else (
      if c.autopush = 1 && ncnt >= e.push_th
      then (
        e.fx.rxpush <- Some nisr;
        c.isr <- 0;
        c.isr_cnt <- 0)
      else (
        c.isr <- nisr;
        c.isr_cnt <- ncnt);
      Line.consume e.chip.line k t;
      c.t <- t;
      if n = 1 && k = 1 && c.in_tap = 1 then crc_feed c (List.hd bs))
;;

let op_in c e =
  let s = bits e.x 18 15 in
  let n = match bits e.x 4 0 with 0 -> 32 | n -> n in
  if s = Isa.line && c.idx = 0
  then in_line c e n
  else (
    let b = src c e s land mask n in
    let nisr =
      if n = 32
      then b
      else if c.in_shr = 1
      then ((c.isr lsr n) lor (b lsl (32 - n))) land m32
      else ((c.isr lsl n) lor b) land m32
    in
    let ncnt = min 32 (c.isr_cnt + n) in
    if c.autopush = 1 && ncnt >= e.push_th
    then
      if e.rx_full
      then e.complete <- false
      else (
        e.fx.rxpush <- Some nisr;
        cover e.chip "shift:autopush";
        c.isr <- 0;
        c.isr_cnt <- 0)
    else (
      c.isr <- nisr;
      c.isr_cnt <- ncnt);
    if e.complete && n = 1 && c.in_tap = 1 then crc_feed c (b land 1))
;;

let op_out c e =
  let dst = bits e.x 18 15 in
  let n = match bits e.x 4 0 with 0 -> 32 | n -> n in
  let line = dst = Isa.line && c.idx = 0 in
  if line && not (Line.room e.chip.line)
  then e.complete <- false
  else (
    let refill = c.autopull = 1 && c.osr_cnt >= e.pull_th in
    if refill && e.pullq = []
    then e.complete <- false
    else (
      let osr0, cnt0 =
        if refill
        then (
          e.fx.txpop <- true;
          cover e.chip "shift:autopull";
          List.hd e.pullq, 0)
        else c.osr, c.osr_cnt
      in
      let data, nosr =
        if c.out_shr = 1
        then osr0 land mask n, if n = 32 then 0 else osr0 lsr n
        else osr0 lsr (32 - n), if n = 32 then 0 else (osr0 lsl n) land m32
      in
      c.osr <- nosr;
      c.osr_cnt <- min 32 (cnt0 + n);
      if n = 1 && c.out_tap = 1 then crc_feed c (data land 1);
      if line
      then
        Line.push
          e.chip.line
          (List.init n (fun i ->
             if c.out_shr = 1 then (data lsr i) land 1 else (data lsr (n - 1 - i)) land 1))
      else if dst <> Isa.osr && dst <> Isa.lr
      then write_dst c e dst data n))
;;

let op_push c e =
  let iff = bits e.x 18 18 in
  let blk = bits e.x 17 17 in
  if not (iff = 1 && c.isr_cnt < e.push_th)
  then
    if e.rx_full
    then
      if blk = 1
      then e.complete <- false
      else (
        c.isr <- 0;
        c.isr_cnt <- 0)
    else (
      e.fx.rxpush <- Some c.isr;
      c.isr <- 0;
      c.isr_cnt <- 0)
;;

let op_pull c e =
  let ife = bits e.x 18 18 in
  let blk = bits e.x 17 17 in
  if not (ife = 1 && c.osr_cnt < e.pull_th)
  then (
    match e.pullq with
    | [] ->
      if blk = 1
      then e.complete <- false
      else (
        c.osr <- c.r.(0);
        c.osr_cnt <- 0)
    | h :: _ ->
      c.osr <- h;
      c.osr_cnt <- 0;
      e.fx.txpop <- true)
;;

let op_mov c e =
  let dst = bits e.x 18 15 in
  let mop = bits e.x 14 13 in
  let s = bits e.x 12 9 in
  let v = src c e s in
  let v =
    if mop = Isa.m_inv then lnot v land m32 else if mop = Isa.m_rev then rev32 v else v
  in
  if s = Isa.lr && dst = Isa.pc && mop = Isa.m_none
  then cover e.chip "call:ret"
  else if dst = Isa.lr
  then cover e.chip "call:mov_to_lr";
  write_dst c e dst v 0
;;

let op_set c e =
  let tgt = if bits e.x 18 18 = 1 then e.fx.oe else e.fx.out in
  let v = bits e.x 4 0 in
  for i = 0 to min c.set_count 5 - 1 do
    tgt.((c.set_base + i) land 31) <- (v lsr i) land 1
  done
;;

let op_ldi c e = c.r.(bits e.x 17 16) <- bits e.x 15 0

let op_alu c e =
  let x = e.x in
  let aop = bits x 18 16 in
  let rd = bits x 15 14 in
  let s = if bits x 13 13 = 1 then bits x 12 0 else c.r.(bits x 1 0) in
  let a = c.r.(rd) in
  let cy = ref 0 in
  let res =
    if aop = Isa.a_add
    then (
      let r = a + s in
      cy := r lsr 16;
      r)
    else if aop = Isa.a_sub || aop = Isa.a_cmp
    then (
      cy := if a < s then 1 else 0;
      a - s)
    else if aop = Isa.a_and
    then a land s
    else if aop = Isa.a_or
    then a lor s
    else if aop = Isa.a_xor
    then a lxor s
    else if aop = Isa.a_shl
    then a lsl (s land 15)
    else a lsr (s land 15)
  in
  let res = res land m16 in
  c.z <- (if res = 0 then 1 else 0);
  c.c <- !cy;
  if aop <> Isa.a_cmp then c.r.(rd) <- res
;;

let op_pin _c e =
  let act = bits e.x 18 17 in
  let p = bits e.x 4 0 in
  let fx = e.fx in
  if act = Isa.p_lo
  then (
    fx.out.(p) <- 0;
    fx.oe.(p) <- 1)
  else if act = Isa.p_hi
  then (
    fx.out.(p) <- 1;
    fx.oe.(p) <- 1)
  else if act = Isa.p_toggle
  then fx.out.(p) <- ((e.chip.pin_out lsr p) land 1) lxor 1
  else fx.oe.(p) <- 0
;;

let op_flag _c e =
  let b = 1 lsl bits e.x 2 0 in
  if bits e.x 18 18 = 1 then e.fx.fclr <- e.fx.fclr lor b else e.fx.fset <- e.fx.fset lor b
;;

let op_halt c e =
  c.halted <- 1;
  e.complete <- false
;;

let dispatch op c e =
  match op with
  | 0 -> ignore (op_jmp c e)
  | 1 -> op_wait c e
  | 2 -> op_in c e
  | 3 -> op_out c e
  | 4 -> op_push c e
  | 5 -> op_pull c e
  | 6 -> op_mov c e
  | 7 -> op_set c e
  | 8 -> op_ldi c e
  | 9 -> op_alu c e
  | 10 -> op_pin c e
  | 11 -> op_flag c e
  | 12 -> op_halt c e
  | 13 -> op_call c e
  | _ -> ()
;;

let core_step c chip pins_in flags =
  let word = fetch chip c.pc in
  let op = (word lsr 27) land 31 in
  let sd = (word lsr 19) land 0xFF in
  let x = word land 0x7FFFF in
  let fx =
    { out = Array.make 32 (-1)
    ; oe = Array.make 32 (-1)
    ; side_out = Array.make 32 (-1)
    ; side_oe = Array.make 32 (-1)
    ; fset = 0
    ; fclr = 0
    ; txpop = false
    ; rxpush = None
    }
  in
  let e =
    { chip
    ; pins_in
    ; eflags = flags
    ; x
    ; fx
    ; pull_th = (if c.pull_thresh = 0 then 32 else c.pull_thresh)
    ; push_th = (if c.push_thresh = 0 then 32 else c.push_thresh)
    ; pullq = (if c.fjoin = 1 then c.rxf else c.txf)
    ; rx_full = c.fjoin = 1 || List.length c.rxf >= chip.fifo_depth
    ; jump = None
    ; complete = true
    ; new_wait = 0
    ; kind = 0
    ; wpin = 0
    ; qual = false
    ; ev = false
    ; seen = false
    ; diff = false
    ; edge_done = false
    }
  in
  let tick = divider c in
  track_wait c e;
  if (not tick) || c.halted = 1 || c.fault = 1
  then fx
  else if c.dcnt <> 0
  then (
    c.dcnt <- c.dcnt - 1;
    fx)
  else if c.pc lsr 8 <> c.idx
  then (
    c.fault <- 1;
    c.halted <- 1;
    c.stalled <- 0;
    cover chip "core:bank_fault";
    fx)
  else (
    let delay = side_set c sd fx in
    let name = if op < Array.length Isa.opnames then Isa.opnames.(op) else "rsvd" in
    cover chip ("attempt:" ^ name);
    dispatch op c e;
    cover chip ((if e.complete then "done:" else "stall:") ^ name);
    if e.jump <> None then cover chip "jump";
    c.stalled <- (if (not e.complete) && op <> Isa.op_halt then 1 else 0);
    c.in_wait <- e.new_wait;
    if e.complete
    then (
      c.dcnt <- delay;
      match e.jump with
      | Some a -> c.pc <- a
      | None ->
        if c.pc = c.wrap_top
        then c.pc <- c.wrap_bottom
        else c.pc <- (c.pc + 1) land 1023);
    fx)
;;

(* ---- combinational views of current state *)
let eff_pins chip =
  List.fold_left
    (fun (out, oe) (p, v) ->
       (out land lnot (1 lsl p)) lor (v lsl p), oe lor (1 lsl p))
    (chip.pin_out, chip.pin_oe)
    (Line.drive chip.line)
;;

let pad_in chip =
  (chip.sync2 land lnot chip.sync_bypass) lor (chip.sync1 land chip.sync_bypass)
;;

let pins_in chip =
  let s = pad_in chip in
  let out, _ = eff_pins chip in
  (s land 0xFF)
  lor (out land 0x7F00)
  lor (((s lsr 8) land 0x1F) lsl 16)
  lor (out land 0xFF000000)
;;

let outputs chip =
  let od = chip.od_mask in
  let out, oe = eff_pins chip in
  let uio_out = out land 0xFF land lnot od in
  let uio_oe = ((oe land lnot od) lor (lnot out land od)) land 0xFF in
  (out lsr 8) land 0x7F, uio_out, uio_oe
;;

let read chip addr =
  if addr >= core_base && addr < core_base + (ncores_max * core_stride)
  then (
    let c = (addr - core_base) / core_stride in
    let off = (addr - core_base) mod core_stride in
    if c < chip.ncores then read_reg chip.cores.(c) off else 0)
  else if addr = a_id
  then id_value
  else if addr = a_ctrl
  then chip.enable
  else if addr = a_fset || addr = a_fclr
  then chip.flags
  else if addr = a_pins
  then pins_in chip
  else if addr = a_pout
  then chip.pin_out
  else if addr = a_poe
  then chip.pin_oe
  else if addr = a_linestat
  then Line.status chip.line
  else 0
;;

let pad_bit p = if p < 8 then Some p else if p >= 16 && p <= 20 then Some (p - 8) else None

let step
      chip
      ~ui_in
      ~uio_in
      ~we
      ~addr
      ~wdata
      ~commit
      ~snap
      ~cancel
      ~ui_mid
      ~uio_mid
  =
  let pins = pins_in chip in
  let byp = chip.sync_bypass in
  let line_write = ref None in
  let flags = chip.flags in
  let fjoin = Array.map (fun c -> c.fjoin) chip.cores in
  let ctrl = chip.ctrl_pend in
  let restart_m, fclear =
    match ctrl with
    | Some v -> (v lsr 8) land 15, (v lsr 16) land 15
    | None -> 0, 0
  in
  let fset = ref 0 in
  let fclr = ref 0 in
  let new_out = ref chip.pin_out in
  let new_oe = ref chip.pin_oe in
  let fifo_ops = Array.make ncores_max (false, None) in
  Array.iteri
    (fun c core ->
       if not (c >= chip.ncores || (restart_m lsr c) land 1 = 1 || (chip.enable lsr c) land 1 = 0)
       then (
         let fx = core_step core chip pins flags in
         fset := !fset lor fx.fset;
         fclr := !fclr lor fx.fclr;
         fifo_ops.(c) <- fx.txpop, fx.rxpush;
         let apply kind_is_out base side =
           let overlap = ref false in
           for p = 0 to 31 do
             if base.(p) >= 0 && side.(p) >= 0 then overlap := true
           done;
           if !overlap then cover chip "pins:side_set_over_out";
           for p = 0 to 31 do
             let v = if side.(p) >= 0 then side.(p) else base.(p) in
             if v >= 0
             then
               if chip.pin_owner.(p) <> c
               then cover chip "pins:write_to_unowned_pin"
               else if kind_is_out
               then new_out := (!new_out land lnot (1 lsl p)) lor (v lsl p)
               else new_oe := (!new_oe land lnot (1 lsl p)) lor (v lsl p)
           done
         in
         apply true fx.out fx.side_out;
         apply false fx.oe fx.side_oe))
    chip.cores;
  let host_push = Array.make ncores_max None in
  let host_pop = Array.make ncores_max false in
  let we = ref we in
  let in_cores =
    ref (addr >= core_base && addr < core_base + (ncores_max * core_stride))
  in
  if !in_cores && (addr - core_base) / core_stride >= chip.ncores
  then (
    in_cores := false;
    if addr >= core_base then we := false);
  let cidx = (addr - core_base) / core_stride in
  let off = (addr - core_base) mod core_stride in
  let res_op = ref None in
  if !in_cores && off = c_fifo
  then (
    let core = chip.cores.(cidx) in
    if commit && core.res <> []
    then (
      if List.hd core.res
      then host_pop.(cidx) <- true
      else if core.rxf <> []
      then cover chip "fifo:pop_suppressed_by_snapshot";
      res_op := Some (cidx, `Commit, false))
    else if snap && List.length core.res < 2
    then (
      let n = List.length (List.filter Fun.id core.res) in
      res_op
      := Some (cidx, `Snap, fjoin.(cidx) = 0 && n < 2 && List.length core.rxf > n)));
  if !we
  then
    if addr < imem_words
    then (
      if addr lsr 8 < chip.nbanks then chip.imem.(imem_index chip addr) <- wdata land m32)
    else if !in_cores
    then (
      if off = c_fifo
      then host_push.(cidx) <- Some (wdata land m32)
      else write_reg chip.cores.(cidx) off wdata)
    else if addr = a_ctrl
    then ()
    else if addr = a_fset
    then fset := !fset lor (wdata land 0xFF)
    else if addr = a_fclr
    then fclr := !fclr lor (wdata land 0xFF)
    else if addr = a_pown_lo
    then
      for i = 0 to 15 do
        chip.pin_owner.(i) <- (wdata lsr (2 * i)) land 3
      done
    else if addr = a_pown_hi
    then
      for i = 0 to 15 do
        chip.pin_owner.(16 + i) <- (wdata lsr (2 * i)) land 3
      done
    else if addr = a_od
    then chip.od_mask <- wdata land 0xFF
    else if addr = a_syncbyp
    then chip.sync_bypass <- (wdata land 0xFF) lor (((wdata lsr 16) land 0x1F) lsl 8)
    else if addr = a_pout
    then new_out := wdata land m32
    else if addr = a_poe
    then new_oe := wdata land m32
    else if addr = a_hostcfg
    then chip.hostcfg <- wdata land 1
    else if addr >= a_linecfg && addr <= a_linepins
    then line_write := Some (addr - a_linecfg, wdata);
  Array.iteri
    (fun c core ->
       if (fclear lsr c) land 1 = 1
       then (
         if core.txf <> [] || core.rxf <> [] then cover chip "fifo:clear_nonempty";
         core.txf <- [];
         core.rxf <- [])
       else (
         let txpop, rxpush = fifo_ops.(c) in
         let tail = function [] -> [] | _ :: t -> t in
         let tx = ref core.txf in
         let rx = ref core.rxf in
         if fjoin.(c) = 1
         then (
           if txpop then rx := tail !rx;
           if core.txf <> [] && List.length core.rxf < chip.fifo_depth
           then (
             tx := tail !tx;
             rx := !rx @ [ List.hd core.txf ];
             cover chip "fifo:join_move"))
         else (
           if txpop then tx := tail !tx;
           if host_pop.(c) && core.rxf <> [] then rx := tail !rx;
           match rxpush with
           | Some v -> rx := !rx @ [ v ]
           | None -> ());
         (match host_push.(c) with
          | Some v ->
            if List.length core.txf < chip.fifo_depth
            then (
              tx := !tx @ [ v ];
              if txpop && fjoin.(c) = 0 then cover chip "fifo:host_push_and_core_pop")
            else cover chip "fifo:host_push_to_full_dropped"
          | None -> ());
         core.txf <- !tx;
         core.rxf <- !rx))
    chip.cores;
  for c = 0 to ncores_max - 1 do
    if (restart_m lsr c) land 1 = 1
    then (
      let core = chip.cores.(c) in
      if core.in_wait = 1 || core.dcnt <> 0 then cover chip "ctrl:restart_mid_instruction";
      restart core)
  done;
  (match !res_op with
   | Some (c, `Commit, _) ->
     let core = chip.cores.(c) in
     core.res <- List.tl core.res
   | Some (c, `Snap, real) ->
     let core = chip.cores.(c) in
     core.res <- core.res @ [ real ]
   | None -> ());
  Array.iteri
    (fun c core -> if cancel || (fclear lsr c) land 1 = 1 then core.res <- [])
    chip.cores;
  (match ctrl with
   | Some v -> chip.enable <- v land 15
   | None -> ());
  let rxp = Line.rxp chip.line in
  let bit = pad_bit rxp in
  let rx_mid =
    match bit with
    | None -> (pins lsr rxp) land 1
    | Some b -> if (byp lsr b) land 1 = 1 then chip.rx_hq else chip.rx_h2
  in
  (match bit with
   | Some b
     when (byp lsr b) land 1 = 1
          && Line.en chip.line = 1
          && Line.rx_en chip.line = 1
          && chip.rx_hq <> chip.rx_h2 -> cover chip "line:rx_bypassed_pin"
   | _ -> ());
  Line.step chip.line pins rx_mid;
  Line.commit chip.line;
  (match !line_write with
   | Some (o, v) -> Line.write chip.line o v
   | None -> ());
  chip.ctrl_pend <- (if !we && addr = a_ctrl then Some (wdata land m32) else None);
  chip.flags <- ((flags land lnot !fclr) lor !fset) land 0xFF;
  chip.pin_out <- !new_out land m32;
  chip.pin_oe <- !new_oe land m32;
  if byp land (chip.sync1 lxor chip.sync2) <> 0 then cover chip "pins:sync_bypass_fresher";
  chip.sync2 <- chip.sync1;
  let pads = (uio_in land 0xFF) lor ((ui_in land 0x1F) lsl 8) in
  let mid =
    match ui_mid, uio_mid with
    | None, None -> chip.pads_prev
    | _ ->
      let u = match uio_mid with None -> uio_in | Some v -> v in
      let i = match ui_mid with None -> ui_in | Some v -> v in
      (u land 0xFF) lor ((i land 0x1F) lsl 8)
  in
  chip.rx_h2 <- chip.rx_hq;
  chip.rx_hq <- (match bit with Some b -> (mid lsr b) land 1 | None -> 0);
  chip.sync1 <- pads;
  chip.pads_prev <- pads;
  chip.cycle <- chip.cycle + 1
;;

(* The canonical state line ocaml/test/lock_chip.py compares every cycle *)
let state_line b chip =
  let int v =
    Buffer.add_string b (string_of_int v);
    Buffer.add_char b ' '
  in
  let list l =
    int (List.length l);
    List.iter int l
  in
  int chip.enable;
  int (match chip.ctrl_pend with None -> -1 | Some v -> v);
  int chip.flags;
  Array.iter int chip.pin_owner;
  int chip.od_mask;
  int chip.pin_out;
  int chip.pin_oe;
  int chip.hostcfg;
  int chip.sync1;
  int chip.sync2;
  int chip.sync_bypass;
  int chip.rx_hq;
  int chip.rx_h2;
  int chip.pads_prev;
  int chip.cycle;
  Array.iter
    (fun c ->
       List.iter
         int
         [ c.div_int; c.div_frac; c.out_base; c.out_count; c.set_base; c.set_count
         ; c.side_base; c.side_count; c.side_opt; c.side_pindir; c.in_base; c.out_shr
         ; c.in_shr; c.autopull; c.autopush; c.pull_thresh; c.push_thresh; c.fjoin; c.entry
         ; c.wrap_bottom; c.wrap_top; c.crc_poly; c.crc_w; c.in_tap; c.out_tap; c.crc
         ; c.r.(0); c.r.(1); c.r.(2); c.r.(3); c.lr; c.pc; c.isr; c.isr_cnt; c.osr
         ; c.osr_cnt; c.z; c.c; c.t; c.dcnt; c.in_wait; c.wprev; c.wevent; c.cnt; c.facc
         ; c.halted; c.fault; c.stalled
         ];
       list c.txf;
       list c.rxf;
       list (List.map (fun r -> if r then 1 else 0) c.res))
    chip.cores;
  let l = chip.line in
  List.iter
    int
    [ l.cfg; l.div; l.pins; l.tx_on; l.ending; l.eop_idle; l.half; l.bit; l.level; l.txn
    ; l.tcnt; l.tfacc; l.trun; l.trunbit; l.trunv; l.rx_on; l.rcnt; l.rlast; l.rprev
    ; l.quiet; l.q; l.n; l.rrun; l.rrunbit; l.rrunv; l.end_pend; l.ovf
    ];
  int (List.length l.entries);
  List.iter list l.entries;
  list l.rq
;;

(* The inverse of [state_line]: set every value the line holds from such a line.
   ocaml/test/lock_chip.py sends one after a test writes into the Python chip (a
   direct FIFO pop, a register set by hand), so that the comparison goes on from
   the written state. Memory and the coverage counters are not in the line and
   are left as they are. Raises [Failure] unless the line has exactly the values
   [state_line] writes. *)
let load_state chip s =
  let a =
    String.split_on_char ' ' s
    |> List.filter (fun x -> x <> "")
    |> List.map int_of_string
    |> Array.of_list
  in
  let pos = ref 0 in
  let next () =
    if !pos >= Array.length a then failwith "load_state: line too short";
    let v = a.(!pos) in
    incr pos;
    v
  in
  let list () =
    let n = next () in
    let acc = ref [] in
    for _ = 1 to n do
      acc := next () :: !acc
    done;
    List.rev !acc
  in
  chip.enable <- next ();
  chip.ctrl_pend <- (match next () with -1 -> None | v -> Some v);
  chip.flags <- next ();
  for i = 0 to Array.length chip.pin_owner - 1 do
    chip.pin_owner.(i) <- next ()
  done;
  chip.od_mask <- next ();
  chip.pin_out <- next ();
  chip.pin_oe <- next ();
  chip.hostcfg <- next ();
  chip.sync1 <- next ();
  chip.sync2 <- next ();
  chip.sync_bypass <- next ();
  chip.rx_hq <- next ();
  chip.rx_h2 <- next ();
  chip.pads_prev <- next ();
  chip.cycle <- next ();
  Array.iter
    (fun c ->
       c.div_int <- next ();
       c.div_frac <- next ();
       c.out_base <- next ();
       c.out_count <- next ();
       c.set_base <- next ();
       c.set_count <- next ();
       c.side_base <- next ();
       c.side_count <- next ();
       c.side_opt <- next ();
       c.side_pindir <- next ();
       c.in_base <- next ();
       c.out_shr <- next ();
       c.in_shr <- next ();
       c.autopull <- next ();
       c.autopush <- next ();
       c.pull_thresh <- next ();
       c.push_thresh <- next ();
       c.fjoin <- next ();
       c.entry <- next ();
       c.wrap_bottom <- next ();
       c.wrap_top <- next ();
       c.crc_poly <- next ();
       c.crc_w <- next ();
       c.in_tap <- next ();
       c.out_tap <- next ();
       c.crc <- next ();
       for i = 0 to 3 do
         c.r.(i) <- next ()
       done;
       c.lr <- next ();
       c.pc <- next ();
       c.isr <- next ();
       c.isr_cnt <- next ();
       c.osr <- next ();
       c.osr_cnt <- next ();
       c.z <- next ();
       c.c <- next ();
       c.t <- next ();
       c.dcnt <- next ();
       c.in_wait <- next ();
       c.wprev <- next ();
       c.wevent <- next ();
       c.cnt <- next ();
       c.facc <- next ();
       c.halted <- next ();
       c.fault <- next ();
       c.stalled <- next ();
       c.txf <- list ();
       c.rxf <- list ();
       c.res <- List.map (fun r -> r <> 0) (list ()))
    chip.cores;
  let l = chip.line in
  l.cfg <- next ();
  l.div <- next ();
  l.pins <- next ();
  l.tx_on <- next ();
  l.ending <- next ();
  l.eop_idle <- next ();
  l.half <- next ();
  l.bit <- next ();
  l.level <- next ();
  l.txn <- next ();
  l.tcnt <- next ();
  l.tfacc <- next ();
  l.trun <- next ();
  l.trunbit <- next ();
  l.trunv <- next ();
  l.rx_on <- next ();
  l.rcnt <- next ();
  l.rlast <- next ();
  l.rprev <- next ();
  l.quiet <- next ();
  l.q <- next ();
  l.n <- next ();
  l.rrun <- next ();
  l.rrunbit <- next ();
  l.rrunv <- next ();
  l.end_pend <- next ();
  l.ovf <- next ();
  let n = next () in
  let acc = ref [] in
  for _ = 1 to n do
    acc := list () :: !acc
  done;
  l.entries <- List.rev !acc;
  l.rq <- list ();
  if !pos <> Array.length a then failwith "load_state: line too long"
;;
