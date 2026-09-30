# SymbiYosys proofs. Needs sby + yices (OSS CAD Suite).
#   make          # all proofs + covers
SBY ?= sby
all: fifo spi core
fifo:
	$(SBY) -f fifo.sby
spi:
	$(SBY) -f spi.sby
core:
	$(SBY) -f core.sby
clean:
	rm -rf fifo_* spi_* core_*
.PHONY: all fifo spi core clean
