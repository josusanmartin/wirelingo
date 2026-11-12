# SymbiYosys proofs. Needs sby + yices (OSS CAD Suite).
#   make          # all proofs + covers
#   fifos: the FIFO pair (incl. FJOIN_TX) vs reference queues, depths 4 and 8
#   hostport: host port bus safety (SCK and clk unconstrained)
#   hostport_ref: host port vs a pin-level reference model of the protocol:
#          address/data order, read snapshot/commit/cancel, CS_n and reset
#          at any time (k-induction) + covers (docs/formal-hostport.md)
#   core:  core safety invariants for any instruction stream and host traffic (core 1)
# Not in `all`: core0, the same proof on core 0 (IDX 0, with the line-unit ports).
#   line:  line unit: queue bounds, the Manchester phase never wraps (any config, pins, timing)
# Not in `all` (slow): hostport_ref_cover1 (1-bit covers, ~300 steps) and
# hostport_mutants (planted RTL bugs the hostport_ref properties must catch).
# Not in `all` or CI: equiv, the RTL-vs-netlist equivalence check of a
# hardening run (docs/equivalence.md; heavy, run it on runner1):
#   make -C formal equiv RUN=~/protoemu-flow/protoemu-exp-fo6 [OUT=dir] [JOBS=4] [EQUIV_FLAGS=--mutants]
SBY ?= sby
JOBS ?= 2
all: fifos hostport hostport_ref core line
fifos:
	$(SBY) -f fifos.sby
hostport:
	$(SBY) -f hostport.sby
hostport_ref:
	$(SBY) -f hostport_ref.sby prove cover
hostport_ref_cover1:
	$(SBY) -f hostport_ref.sby cover1
hostport_mutants:
	python3 hostport_mutants.py -j $(JOBS)
core:
	$(SBY) -f core.sby prove
core0:
	$(SBY) -f core.sby prove0
line:
	$(SBY) -f line.sby
equiv:
	@test -n "$(RUN)" || { echo "usage: make -C formal equiv RUN=<hardening run dir> [OUT=dir]"; exit 2; }
	equiv/equiv_check.sh -j $(JOBS) $(EQUIV_FLAGS) $(RUN) $(OUT)
clean:
	rm -rf fifos_cover8 fifos_prove4 fifos_prove8 hostport_prove hostport_cover core_prove core_prove0 line_prove \
	       hostport_ref_prove hostport_ref_cover hostport_ref_cover1 hostport_ref_mutant
.PHONY: all fifos hostport hostport_ref hostport_ref_cover1 hostport_mutants core core0 line equiv clean
