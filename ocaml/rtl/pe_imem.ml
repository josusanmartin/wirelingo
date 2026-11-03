(* Banked instruction memory with registered reads: port of src/pe_imem.sv.
   The SRAM macro stays an external instantiation with the same tie-offs. *)
open! Base
open Hardcaml
open Signal
open Prims

module I = struct
  type 'a t =
    { clk : 'a
    ; fetch_addr : 'a [@bits 40]
    ; we : 'a
    ; waddr : 'a [@bits 10]
    ; wdata : 'a [@bits 32]
    }
  [@@deriving hardcaml]
end

module O = struct
  type 'a t = { bank_rdata : 'a [@bits 128] } [@@deriving hardcaml]
end

(* pe_imem_bank, the macro variant *)
let sram_bank ~clk ~raddr ~we ~waddr ~wdata =
  macro
    ~name:"RM_IHPSG13_1P_256x32_c2_bm_bist"
    ~instance:"sram"
    ~keep:true
    ~inputs:
      [ "A_CLK", clk
      ; "A_MEN", vdd
      ; "A_WEN", we
      ; "A_REN", ~:we
      ; "A_ADDR", mux2 we waddr raddr
      ; "A_DIN", wdata
      ; "A_DLY", vdd
      ; "A_BM", ones 32
      ; "A_BIST_CLK", gnd
      ; "A_BIST_EN", gnd
      ; "A_BIST_MEN", gnd
      ; "A_BIST_WEN", gnd
      ; "A_BIST_REN", gnd
      ; "A_BIST_ADDR", zero 8
      ; "A_BIST_DIN", zero 32
      ; "A_BIST_BM", zero 32
      ]
    ~output:("A_DOUT", 32)
    ()
;;

(* pe_imem_bank, PE_FLOP_IMEM: synchronous write, registered read *)
let flop_bank ~aw ~clk ~raddr ~we ~waddr ~wdata =
  let write_port =
    { Write_port.write_clock = clk
    ; write_address = waddr
    ; write_enable = we
    ; write_data = wdata
    }
  in
  let mem =
    multiport_memory
      (1 lsl aw)
      ~write_ports:[| write_port |]
      ~read_addresses:[| raddr |]
  in
  reg (Reg_spec.create ~clock:clk ()) mem.(0) -- "rdata_q"
;;

module Bank_io (P : sig
    val aw : int
  end) =
struct
  module I = struct
    type 'a t =
      { clk : 'a
      ; raddr : 'a [@bits P.aw]
      ; we : 'a
      ; waddr : 'a [@bits P.aw]
      ; wdata : 'a [@bits 32]
      }
    [@@deriving hardcaml]
  end

  module O = struct
    type 'a t = { rdata : 'a [@bits 32] } [@@deriving hardcaml]
  end
end

let bank ~instance ~(imem : Config.imem) ~aw scope ~clk ~raddr ~we ~waddr ~wdata =
  let module B =
    Bank_io (struct
      let aw = aw
    end)
  in
  let module H = Hierarchy.In_scope (B.I) (B.O) in
  let create _scope (i : _ B.I.t) =
    let rdata =
      match imem with
      | Config.Sram ->
        sram_bank ~clk:i.clk ~raddr:i.raddr ~we:i.we ~waddr:i.waddr ~wdata:i.wdata
      | Config.Flop _ ->
        flop_bank ~aw ~clk:i.clk ~raddr:i.raddr ~we:i.we ~waddr:i.waddr ~wdata:i.wdata
    in
    { B.O.rdata }
  in
  let o =
    H.hierarchical
      ~config:circuit_config
      ~scope
      ~name:"pe_imem_bank"
      ~instance
      create
      { B.I.clk; raddr; we; waddr; wdata }
  in
  o.rdata
;;

let create ~(cfg : Config.t) scope (i : _ I.t) =
  let aw = Config.bank_aw cfg in
  let banks =
    List.init 4 ~f:(fun b ->
      if b < cfg.nbanks
      then
        bank
          ~instance:(Printf.sprintf "g_bank_%d_u_bank" b)
          ~imem:cfg.imem
          ~aw
          scope
          ~clk:i.clk
          ~raddr:i.fetch_addr.:[(10 * b) + aw - 1, 10 * b]
          ~we:(i.we &: (i.waddr.:[9, 8] ==:. b))
          ~waddr:i.waddr.:[aw - 1, 0]
          ~wdata:i.wdata
      else zero 32)
  in
  { O.bank_rdata = concat_lsb banks }
;;

let hierarchical ~instance ~cfg scope i =
  let module H = Hierarchy.In_scope (I) (O) in
  H.hierarchical ~config:circuit_config ~scope ~name:"pe_imem" ~instance (create ~cfg) i
;;
