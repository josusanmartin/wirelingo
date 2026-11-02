(* Tiny Tapeout top level: port of src/project.v. *)
open! Base
open Hardcaml
open Signal
open Prims

module I = struct
  type 'a t =
    { ui_in : 'a [@bits 8]
    ; uio_in : 'a [@bits 8]
    ; ena : 'a
    ; clk : 'a
    ; rst_n : 'a
    }
  [@@deriving hardcaml]
end

module O = struct
  type 'a t =
    { uo_out : 'a [@bits 8]
    ; uio_out : 'a [@bits 8]
    ; uio_oe : 'a [@bits 8]
    }
  [@@deriving hardcaml]
end

let name = "tt_um_josusanmartin_protoemu"

let create ~(cfg : Config.t) ~chip_module scope (i : _ I.t) =
  (* reset synchroniser: async assert, sync deassert *)
  let rst_s =
    let dom = domain ~clock:i.clk ~rst_n:i.rst_n in
    let d = wire 2 in
    let q = async_reg (dff dom d) -- "rst_s" in
    d <== (bit q 0 @: vdd);
    q
  in
  let rstn = bit rst_s 1 in
  let bus_rdata = wire 32 in
  let quad_en = wire 1 in
  let host =
    Pe_hostport.hierarchical
      ~instance:"u_host"
      scope
      { Pe_hostport.I.clk = i.clk
      ; rst_n = rstn
      ; sck = bit i.ui_in 6
      ; cs_n = bit i.ui_in 7
      ; io_in = i.ui_in.:[5, 2]
      ; quad_en
      ; bus_rdata
      }
  in
  let chip =
    Pe_chip.hierarchical
      ~instance:"u_chip"
      ~cfg
      ~name:chip_module
      scope
      { Pe_chip.I.clk = i.clk
      ; rst_n = rstn
      ; ui_in = i.ui_in.:[4, 0]
      ; uio_in = i.uio_in
      ; bus_addr = host.bus_addr
      ; bus_we = host.bus_we
      ; bus_wdata = host.bus_wdata
      ; bus_commit = host.bus_commit
      ; bus_snap = host.bus_snap
      ; bus_cancel = host.bus_cancel
      }
  in
  bus_rdata <== chip.bus_rdata;
  quad_en <== chip.quad_en;
  let io_out = host.io_out in
  { O.uo_out =
      bit io_out 3
      @: mux2 host.io_oe io_out.:[2, 0] chip.uo_out.:[6, 4]
      @: chip.uo_out.:[3, 0]
  ; uio_out = chip.uio_out
  ; uio_oe = chip.uio_oe
  }
;;
