(* gen_rtl: write the Hardcaml port as Verilog.

     gen_rtl tapeout DIR    2 cores, 2 SRAM banks, 8-deep FIFOs (the frozen chip)
     gen_rtl flop4 DIR      4 cores, 16-word flop banks, 4-deep FIFOs (CI's other build)

   Writes DIR/wirelingo_hc.v (top module tt_um_josusanmartin_protoemu, every
   child module) and DIR/pe_chip_lockstep.v (see Lockstep_shim). *)
open! Base
open Hardcaml
open Wirelingo_rtl

let top ~cfg ~chip_module path =
  let module C = Circuit.With_interface (Project.I) (Project.O) in
  let scope = Scope.create ~flatten_design:false () in
  let circuit =
    C.create_exn
      ~config:Prims.circuit_config
      ~name:Project.name
      (Project.create ~cfg ~chip_module scope)
  in
  Rtl.output
    ~database:(Scope.circuit_database scope)
    ~output_mode:(To_file path)
    Verilog
    circuit
;;

let chip_only ~cfg ~chip_module path =
  let module C = Circuit.With_interface (Pe_chip.I) (Pe_chip.O) in
  let scope = Scope.create ~flatten_design:false () in
  let circuit =
    C.create_exn
      ~config:Prims.circuit_config
      ~name:chip_module
      (Pe_chip.create ~cfg scope)
  in
  Rtl.output
    ~database:(Scope.circuit_database scope)
    ~output_mode:(To_file path)
    Verilog
    circuit
;;

let () =
  match Sys.get_argv () with
  | [| _; which; dir |] ->
    let cfg =
      match which with
      | "tapeout" -> Config.tapeout
      | "flop4" -> Config.ci_flop4
      | _ -> failwith "gen_rtl tapeout|flop4 DIR"
    in
    top ~cfg ~chip_module:"pe_chip" (dir ^ "/wirelingo_hc.v");
    chip_only ~cfg ~chip_module:Lockstep_shim.impl_module (dir ^ "/pe_chip_impl.v");
    Stdio.Out_channel.write_all
      (dir ^ "/pe_chip_lockstep.v")
      ~data:(Lockstep_shim.verilog ~cfg)
  | _ -> failwith "gen_rtl tapeout|flop4 DIR"
;;
