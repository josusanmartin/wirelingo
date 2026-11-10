# RTL simulation of the KV260 design with the bring-up script's steps:
#   make -C fpga/kv260/sim                 (needs cocotb + icarus, as test/)
#   make -C fpga/kv260/sim KV260_CORES=2   (the 2-core fallback; default 3, as build.sh)
SIM ?= icarus
KV260_CORES ?= 3
export KV260_CORES
TOPLEVEL_LANG = verilog
SIM_BUILD ?= sim_build/c$(KV260_CORES)
STAGE := $(PWD)/sim_build/stage-c$(KV260_CORES)
$(shell rm -rf $(STAGE) && python3 $(PWD)/../../../flow/fpga_profile.py --output $(STAGE) --cores $(KV260_CORES) --fifo-log2 3)
# a placeholder identity (build.sh writes the real one)
$(shell printf '`define BUILD_RTL_REV 160%sh0\n`define BUILD_HEAD_REV 160%sh0\n`define BUILD_DIRTY 1%sb1\n`define BUILD_TIME 32%sd0\n`define BUILD_DIGEST 32%sh0\n' "'" "'" "'" "'" "'" > $(STAGE)/build_id.vh)
VERILOG_SOURCES = $(PWD)/tb_kv260.v $(PWD)/../kv260_top.v $(PWD)/../pe_spim.v $(PWD)/../pe_lab.v $(STAGE)/src/project.v \
    $(wildcard $(STAGE)/src/*.sv)
COMPILE_ARGS += -g2012 -I$(STAGE)/src -I$(STAGE)
TOPLEVEL = tb_kv260
COCOTB_TEST_MODULES = test_kv260
export PYTHONPATH := $(PWD):$(PWD)/../host:$(PWD)/../../../tools:$(PYTHONPATH)
include $(shell cocotb-config --makefiles)/Makefile.sim
