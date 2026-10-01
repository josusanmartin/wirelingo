# SymbiYosys proofs. Needs sby + yices (OSS CAD Suite).
#   make          # all proofs + covers
#   fifos: the FIFO pair (incl. FJOIN_TX) vs reference queues, depths 4 and 8
#   spi:   host SPI slave bus safety
#   core:  core safety invariants for any instruction stream and host traffic
SBY ?= sby
all: fifos spi core
fifos:
	$(SBY) -f fifos.sby
spi:
	$(SBY) -f spi.sby
core:
	$(SBY) -f core.sby
clean:
	rm -rf fifos_* spi_* core_* fifos spi core
.PHONY: all fifos spi core clean
