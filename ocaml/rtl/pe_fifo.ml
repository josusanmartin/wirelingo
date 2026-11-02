(* FIFO of depth 2**LOG2: port of src/pe_fifo.sv. Full and empty are judged on
   start-of-cycle state: a push while full is dropped even with a pop in the
   same cycle. The latches and clock gates stay the blackboxes of
   src/pe_cg.sv. *)
open! Base
open Hardcaml
open Signal
open Prims

module I = struct
  type 'a t =
    { clk : 'a
    ; rst_n : 'a
    ; clear : 'a
    ; push : 'a
    ; push_data : 'a [@bits 32]
    ; pop : 'a
    }
  [@@deriving hardcaml]
end

module O = struct
  type 'a t =
    { head : 'a [@bits 32]
    ; head2 : 'a [@bits 32]
    ; level : 'a [@bits 4]
    }
  [@@deriving hardcaml]
end

let create ~log2 ~no_cg _scope (i : _ I.t) =
  let depth = 1 lsl log2 in
  let dom =
    spec ~clock:i.clk ~reset:i.rst_n ~reset_edge:Edge.Falling ~clear:i.clear ()
  in
  let rd_w = wire log2 in
  let wr_w = wire log2 in
  let cnt_w = wire (log2 + 1) in
  let do_push = i.push &: ~:(bit cnt_w log2) in
  let do_pop = i.pop &: (cnt_w <>:. 0) in
  let rd = reg dom ~enable:do_pop (rd_w +:. 1) -- "rd" in
  let wr = reg dom ~enable:do_push (wr_w +:. 1) -- "wr" in
  let cnt =
    reg dom (cnt_w +: uresize do_push ~width:(log2 + 1) -: uresize do_pop ~width:(log2 + 1))
    -- "cnt"
  in
  rd_w <== rd;
  wr_w <== wr;
  cnt_w <== cnt;
  let ent =
    if no_cg
    then
      List.init depth ~f:(fun e ->
        let wen = do_push &: (wr ==:. e) in
        reg (Reg_spec.create ~clock:i.clk ()) ~enable:wen i.push_data
        -- Printf.sprintf "g_ent_%d_q" e)
    else (
      let wd_gclk = cg ~instance:"u_wd_cg" ~clk:i.clk ~en:do_push in
      let wd = reg (Reg_spec.create ~clock:wd_gclk ()) i.push_data -- "wd" in
      List.init depth ~f:(fun e ->
        let wen = do_push &: (wr ==:. e) in
        let gclk = cg ~instance:(Printf.sprintf "g_ent_%d_u_cg" e) ~clk:i.clk ~en:wen in
        latch ~instance:(Printf.sprintf "g_ent_%d_u_lat" e) ~en:gclk ~d:wd))
  in
  { O.head = mux rd ent
  ; head2 = mux (rd +:. 1) ent
  ; level = (if log2 = 3 then cnt else uresize cnt ~width:4)
  }
;;

let hierarchical ~instance ~log2 ~no_cg scope i =
  let module H = Hierarchy.In_scope (I) (O) in
  H.hierarchical
    ~config:circuit_config
    ~scope
    ~name:"pe_fifo"
    ~instance
    (create ~log2 ~no_cg)
    i
;;
