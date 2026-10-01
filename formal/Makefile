# SymbiYosys proofs. Needs sby + yices (OSS CAD Suite).
#   make          # all proofs + covers
#   fifos: the FIFO pair (incl. FJOIN_TX) vs reference queues, depths 4 and 8
#   hostport: host port bus safety (SCK and clk unconstrained)
#   core:  core safety invariants for any instruction stream and host traffic
SBY ?= sby
all: fifos hostport core
fifos:
	$(SBY) -f fifos.sby
hostport:
	$(SBY) -f hostport.sby
core:
	$(SBY) -f core.sby
clean:
	rm -rf fifos_* hostport_* core_* fifos hostport core
.PHONY: all fifos hostport core clean
