(* Helpers for the waveform tests. Cyclesim runs one clock, and cannot open a
   blackbox, so these tests cover the blocks that fit: pe_fifo and pe_fifos in their
   PE_FIFO_NO_CG form (enable flops, no latches or clock gates), and pe_line. pe_core
   has clock-gated config registers and pe_hostport has four clock domains, so those
   stay with the cocotb suites and the proof. *)
open! Base
open Hardcaml
open Hardcaml_waveterm

let set p ~width v = p := Bits.of_int ~width v
let int (s : Bits.t ref) = Bits.to_int !s

(* n results of f, called in order (Base's List.init does not promise an order) *)
let collect n f =
  let out = ref [] in
  for _ = 1 to n do
    out := f () :: !out
  done;
  List.rev !out
;;

(* the waveform, with these ports: name#b as a bit, name#u in decimal, else hex *)
let print ?(wave_width = 1) ?(display_width = 100) waves names =
  let display_height =
    List.fold names ~init:2 ~f:(fun h n ->
      h + if String.is_suffix n ~suffix:"#b" then 2 else 3)
  in
  let display_rules =
    List.map names ~f:(fun n ->
      let name = String.drop_suffix n 2 in
      if String.is_suffix n ~suffix:"#b"
      then Display_rule.port_name_is name ~wave_format:Wave_format.Bit
      else if String.is_suffix n ~suffix:"#u"
      then Display_rule.port_name_is name ~wave_format:Wave_format.Unsigned_int
      else Display_rule.port_name_is n ~wave_format:Wave_format.Hex)
  in
  Waveform.print ~wave_width ~display_width ~display_height ~display_rules waves
;;
