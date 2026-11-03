(* Build parameters of src/project.v and src/pe_chip.sv. [tapeout] is the frozen
   chip: 2 cores, 2 banks of 256-word SRAM, 8-deep FIFOs. *)
open! Base

type imem =
  | Sram (* one IHP 256x32 macro per bank; BANK_AW = 8 *)
  | Flop of int (* PE_FLOP_IMEM, flop banks with this BANK_AW *)

type t =
  { ncores : int
  ; nbanks : int
  ; fifo_log2 : int
  ; imem : imem
  ; fifo_no_cg : bool (* PE_FIFO_NO_CG *)
  }

let tapeout = { ncores = 2; nbanks = 2; fifo_log2 = 3; imem = Sram; fifo_no_cg = false }

(* the 4-core flop build CI's second lockstep run uses *)
let ci_flop4 = { ncores = 4; nbanks = 4; fifo_log2 = 2; imem = Flop 4; fifo_no_cg = false }
let bank_aw t = match t.imem with Sram -> 8 | Flop aw -> aw
