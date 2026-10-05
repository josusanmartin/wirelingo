# RTL simulation of the KV260 design with the bring-up script's steps:
#   make -C fpga/kv260/sim        (needs cocotb + icarus, as test/)
SIM ?= icarus
TOPLEVEL_LANG = verilog
STAGE := $(PWD)/sim_build/stage
$(shell rm -rf $(STAGE) && python3 $(PWD)/../../../flow/fpga_profile.py --output $(STAGE) --cores 2 --fifo-log2 3)
VERILOG_SOURCES = $(PWD)/tb_kv260.v $(PWD)/../kv260_top.v $(PWD)/../pe_spim.v $(STAGE)/src/project.v \
    $(wildcard $(STAGE)/src/*.sv)
COMPILE_ARGS += -g2012 -I$(STAGE)/src
TOPLEVEL = tb_kv260
COCOTB_TEST_MODULES = test_kv260
export PYTHONPATH := $(PWD):$(PWD)/../host:$(PWD)/../../../tools:$(PYTHONPATH)
include $(shell cocotb-config --makefiles)/Makefile.sim
