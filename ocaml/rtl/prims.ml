(* Helpers shared by the module ports: register domains, the hard-cell
   wrappers and the Verilog operators the RTL leans on. *)
open! Base
open Hardcaml
open Signal

(* Instances form cycles through ports (the fetch address reaches the SRAM and
   the read data comes back; the SRAM's output is registered, but Hardcaml
   cannot see inside a blackbox), so the loop check is off. Real combinational
   loops would show up in yosys (the equivalence run) and in simulation. *)
let circuit_config = { Circuit.Config.default with detect_combinational_loops = false }

(* names kept close to the current Base-style API *)
let of_int_trunc ~width v = of_int ~width v
let uresize x ~width = Signal.uresize x width
let sll_const = sll
let repeat_bit b n = repeat b n
let reduce_and x = reduce ~f:( &: ) (bits_lsb x)
let reduce_or x = reduce ~f:( |: ) (bits_lsb x)

(* a << n, a >> n and a >>> n with Verilog's meaning: the amount may be wider
   than log2 (width a), and an amount >= width gives 0 (or the sign) *)
let shift_left a n = log_shift sll a n
let shift_right a n = log_shift srl a n
let shift_right_arith a n = log_shift sra a n

(* function rotl32: {v, v} << s, top half *)
let rotl32 v s = (shift_left (v @: v) s).:[63, 32]

(* function rotr32: {v, v} >> s, bottom half *)
let rotr32 v s = (shift_right (v @: v) s).:[31, 0]

(* function lowmask, n in 0..32 *)
let lowmask n =
  mux2 (n >=:. 32) (ones 32) (shift_left (of_int ~width:32 1) n -:. 1)
;;

(* ---- registers. A domain is one clock and one active-low asynchronous reset:
   always @(posedge clk or negedge rst_n) *)
let spec ~clock ?(clock_edge = Edge.Rising) ~reset ~reset_edge ?clear () =
  Reg_spec.override (Reg_spec.create ?clear ~reset () ~clock) ~clock_edge ~reset_edge
;;

let domain ~clock ~rst_n = spec ~clock ~reset:rst_n ~reset_edge:Edge.Falling ()

let spec_to ?reset_to dom =
  match reset_to with
  | None -> dom
  | Some v -> Reg_spec.override dom ~reset_to:v
;;

(* a plain register, assigned once *)
let dff dom ?enable ?reset_to d = reg (spec_to ?reset_to dom) ?enable d

(* a register on a gated clock: always @(posedge gclk or negedge rst_n) *)
let gated ~clock ~rst_n ?reset_to d = dff (domain ~clock ~rst_n) ?reset_to d

let named (v : Always.Variable.t) name =
  ignore (v.value -- name : Signal.t);
  v
;;

(* a register written from an Always block *)
let state dom ?reset_to name width =
  named (Always.Variable.reg (spec_to ?reset_to dom) ~width) name
;;

(* (* async_reg = "true", keep = 1 *): the pad and clock-crossing synchronisers *)
let sync_attrs =
  [ Rtl_attribute.create ~value:(String "true") "async_reg"
  ; Rtl_attribute.create ~value:(Int 1) "keep"
  ]
;;

let async_reg s = List.fold sync_attrs ~init:s ~f:add_attribute

let async_state dom name width =
  let v = state dom name width in
  ignore (async_reg v.value : Signal.t);
  v
;;

(* ---- hard cells: the wrappers of src/pe_cg.sv and the SRAM macro stay Verilog *)
let macro ?(parameters = []) ?(keep = false) ~name ~instance ~inputs ~output:(oname, width) () =
  let attributes = if keep then [ Rtl_attribute.create "keep" ] else [] in
  let o =
    Instantiation.create
      ()
      ~parameters
      ~attributes
      ~instance
      ~name
      ~inputs
      ~outputs:[ oname, width ]
  in
  Map.find_exn o oname
;;

let cg ~instance ~clk ~en =
  macro ~name:"pe_cg" ~instance ~inputs:[ "clk", clk; "en", en ] ~output:("gclk", 1) ()
;;

let latch ~instance ~en ~d =
  macro
    ~name:"pe_latch"
    ~instance
    ~parameters:[ Parameter.create ~name:"W" ~value:(Parameter.Value.Int (width d)) ]
    ~inputs:[ "en", en; "d", d ]
    ~output:("q", width d)
    ()
;;
