(* A core's FIFO pair and the FJOIN_TX join: port of src/pe_fifos.sv. *)
open! Base
open Hardcaml
open Signal
open Prims

module I = struct
  type 'a t =
    { clk : 'a
    ; rst_n : 'a
    ; clear : 'a
    ; fjoin : 'a
    ; host_push : 'a
    ; host_data : 'a [@bits 32]
    ; host_pop : 'a
    ; core_pop : 'a
    ; core_push : 'a
    ; core_data : 'a [@bits 32]
    }
  [@@deriving hardcaml]
end

module O = struct
  type 'a t =
    { txf_head : 'a [@bits 32]
    ; rxf_head : 'a [@bits 32]
    ; rxf_head2 : 'a [@bits 32]
    ; txf_level : 'a [@bits 4]
    ; rxf_level : 'a [@bits 4]
    ; pq_head : 'a [@bits 32]
    ; pq_empty : 'a
    ; rx_block : 'a
    }
  [@@deriving hardcaml]
end

let create ~log2 ~no_cg scope (i : _ I.t) =
  let txf_level = wire 4 in
  let rxf_level = wire 4 in
  let txf_head = wire 32 in
  let txf_empty = txf_level ==:. 0 in
  let rxf_empty = rxf_level ==:. 0 in
  let rxf_full = bit rxf_level log2 in
  let xfer = i.fjoin &: ~:txf_empty &: ~:rxf_full in
  let txf =
    Pe_fifo.hierarchical
      ~instance:"txf"
      ~log2
      ~no_cg
      scope
      { Pe_fifo.I.clk = i.clk
      ; rst_n = i.rst_n
      ; clear = i.clear
      ; push = i.host_push
      ; push_data = i.host_data
      ; pop = mux2 i.fjoin xfer i.core_pop
      }
  in
  let rxf =
    Pe_fifo.hierarchical
      ~instance:"rxf"
      ~log2
      ~no_cg
      scope
      { Pe_fifo.I.clk = i.clk
      ; rst_n = i.rst_n
      ; clear = i.clear
      ; push = mux2 i.fjoin xfer i.core_push
      ; push_data = mux2 i.fjoin txf_head i.core_data
      ; pop = mux2 i.fjoin i.core_pop i.host_pop
      }
  in
  txf_level <== txf.level;
  rxf_level <== rxf.level;
  txf_head <== txf.head;
  { O.txf_head
  ; rxf_head = rxf.head
  ; rxf_head2 = rxf.head2
  ; txf_level
  ; rxf_level
  ; pq_head = mux2 i.fjoin rxf.head txf_head
  ; pq_empty = mux2 i.fjoin rxf_empty txf_empty
  ; rx_block = i.fjoin |: rxf_full
  }
;;

let hierarchical ~instance ~log2 ~no_cg scope i =
  let module H = Hierarchy.In_scope (I) (O) in
  H.hierarchical
    ~config:circuit_config
    ~scope
    ~name:"pe_fifos"
    ~instance
    (create ~log2 ~no_cg)
    i
;;
