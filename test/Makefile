# Makefile
# See https://docs.cocotb.org/en/stable/quickstart.html for more info
#
#   make                      # top-level tests (tb.v / test.py), as run by TT CI
#   make TB=chip              # lockstep RTL-vs-model tests on pe_chip (tb_chip.v)
#   make TB=chip IMEM=flop    # same, flop IMEM with 16-word banks (faster)
#   make GATES=yes            # gate-level top-level tests

# defaults
SIM ?= icarus
FST ?= -fst # Use more efficient FST format
TOPLEVEL_LANG ?= verilog
SRC_DIR = $(PWD)/../src
PROJECT_SOURCES = project.v pe_chip.sv pe_core.sv pe_line.sv pe_fifos.sv pe_fifo.sv pe_cg.sv pe_imem.sv pe_hostport.sv
TB ?= top
# IMEM=sram (default, as taped out: 256 words/bank, behavioral SRAM model)
# IMEM=flop (flop banks, PE_BANK_AW address bits per bank; faster lockstep runs)
IMEM ?= sram
ifeq ($(IMEM),flop)
PE_BANK_AW ?= 4
IMEM_ARGS = -DPE_FLOP_IMEM -DPE_BANK_AW=$(PE_BANK_AW)
else
override PE_BANK_AW = 8
IMEM_ARGS =
endif
export PE_BANK_AW
# Build parameters: unset = each testbench's own default (tb.v -> project.v, the
# tapeout configuration: 2 cores, 2 banks, 8-deep FIFOs; tb_chip.v -> 4/4/4).
BUILD_DEFS = $(if $(PE_NCORES),-DPE_NCORES=$(PE_NCORES)) $(if $(PE_NBANKS),-DPE_NBANKS=$(PE_NBANKS)) $(if $(PE_FIFO_LOG2),-DPE_FIFO_LOG2=$(PE_FIFO_LOG2))
ifeq ($(TB),chip)
export PE_NCORES ?= 4
export PE_NBANKS ?= 4
export PE_FIFO_LOG2 ?= 2
else
export PE_NCORES ?= 2
export PE_NBANKS ?= 2
export PE_FIFO_LOG2 ?= 3
endif

export PYTHONPATH := $(PWD)/../tools:$(PYTHONPATH)

ifneq ($(GATES),yes)

# RTL simulation:
SIM_BUILD				= sim_build/rtl_$(TB)_$(IMEM)$(PE_BANK_AW)_c$(PE_NCORES)b$(PE_NBANKS)f$(PE_FIFO_LOG2)
VERILOG_SOURCES += $(addprefix $(SRC_DIR)/,$(PROJECT_SOURCES))
COMPILE_ARGS    += $(IMEM_ARGS) $(BUILD_DEFS)
ifneq ($(IMEM),flop)
VERILOG_SOURCES += $(PWD)/sram_model.v
endif

else

# Gate level simulation:
SIM_BUILD				= sim_build/gl
COMPILE_ARGS    += -DGL_TEST
COMPILE_ARGS    += -DFUNCTIONAL
COMPILE_ARGS    += -DSIM
VERILOG_SOURCES += $(PDK_ROOT)/ihp-sg13cmos5l/libs.ref/sg13cmos5l_io/verilog/sg13cmos5l_io.v
VERILOG_SOURCES += $(PDK_ROOT)/ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/verilog/sg13cmos5l_udp.v
VERILOG_SOURCES += $(PDK_ROOT)/ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/verilog/sg13cmos5l_stdcell.v
# SRAM macro models (FUNCTIONAL selects the behavioral core)
VERILOG_SOURCES += $(PDK_ROOT)/ihp-sg13cmos5l/libs.ref/sg13cmos5l_sram/verilog/RM_IHPSG13_1P_core_behavioral_bm_bist.v
VERILOG_SOURCES += $(PDK_ROOT)/ihp-sg13cmos5l/libs.ref/sg13cmos5l_sram/verilog/RM_IHPSG13_1P_256x32_c2_bm_bist.v

# this gets copied in by the GDS action workflow
GATE_LEVEL_NETLIST ?= $(PWD)/gate_level_netlist.v
VERILOG_SOURCES += $(GATE_LEVEL_NETLIST)

endif

ifeq ($(SIM),icarus)
COMPILE_ARGS 		+= -g2012
endif

# Allow sharing configuration between design and testbench via `include`:
COMPILE_ARGS 		+= -I$(SRC_DIR)

ifeq ($(TB),chip)
VERILOG_SOURCES += $(PWD)/tb_chip.v
TOPLEVEL = tb_chip
COCOTB_TEST_MODULES = test_lockstep
else
VERILOG_SOURCES += $(PWD)/tb.v
TOPLEVEL = tb
COCOTB_TEST_MODULES = test
endif

# include cocotb's make rules to take care of the simulator setup
COCOTB_PLUSARGS += +DUMP_FILE=$(SIM_BUILD)/wave.fst
include $(shell cocotb-config --makefiles)/Makefile.sim
