# SymbiYosys proofs. Needs sby + yices (OSS CAD Suite).
#   make          # all proofs + covers
#   fifos: the FIFO pair (incl. FJOIN_TX) vs reference queues, depths 4 and 8
#   hostport: host port bus safety (SCK and clk unconstrained)
#   core:  core safety invariants for any instruction stream and host traffic
#   line:  line unit: queue bounds, the Manchester phase never wraps (any config, pins, timing)
SBY ?= sby
all: fifos hostport core line
fifos:
	$(SBY) -f fifos.sby
hostport:
	$(SBY) -f hostport.sby
core:
	$(SBY) -f core.sby
line:
	$(SBY) -f line.sby
clean:
	rm -rf fifos_* hostport_* core_* line_prove fifos hostport core line
.PHONY: all fifos hostport core line clean
