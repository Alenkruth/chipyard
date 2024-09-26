#################################
# RISCV Toolchain
#################################

PREFIX = riscv64-unknown-elf-

GCC = $(PREFIX)gcc
CXX = $(PREFIX)g++
CP = $(PREFIX)objcopy
OBJDUMP = $(PREFIX)objdump
DG = $(PREFIX)gdb
SIZE = $(PREFIX)size


#################################
# Flags
#################################

# SoC Settings
ARCH = rv64imafdc
ABI = lp64d
ARCHFLAGS = -march=$(ARCH) -mabi=$(ABI)

## folder for SizeTestsFB
STFB_FOLDER = SizeTestsFB/src

## changed flags to match AK makefile - AS
CFLAGS = -mcmodel=medany -march=rv64g -l -std=gnu99 -O0 -g -fno-common
CFLAGS += -fno-builtin-printf  -Wall -Wno-unused-function -Wno-unused-variable
## CFLAGS  = -std=gnu99 -O2 -fno-common -fno-builtin-printf -Wall
## CFLAGS += $(ARCHFLAGS)
LDFLAGS = -static

include libgloss.mk

PROGRAMS = pwm blkdev accum charcount nic-loopback big-blkdev pingd \
           streaming-passthrough streaming-fir nvdla spiflashread spiflashwrite fft gcd \
           hello mt-hello hello-world FetchBufferCSR

## programs within stfb_folder
STFB_PROGRAMS = fizz_buzz_count_0_0 fizz_buzz_count_1_0 fizz_buzz_count_2_0 fizz_buzz_count_3_0 \
				fizz_buzz_print_0_0 fizz_buzz_print_1_0 fizz_buzz_print_2_0 fizz_buzz_print_3_0 \
				full_hazards_0_0 full_hazards_1_0 full_hazards_2_0 full_hazards_3_0 \
				parallel_hazards_0_0 parallel_hazards_1_0 parallel_hazards_2_0 parallel_hazards_3_0 \
				b_blks_0 b_blks_1 b_blks_2 b_blks_3 \
				simple_loop_6l_0 simple_loop_6l_1 simple_loop_6l_2 simple_loop_6l_3 \
				simple_loop_10l_0 simple_loop_10l_1 simple_loop_10l_2 simple_loop_10l_3 \
				simple_loop_20l_0 simple_loop_20l_1 simple_loop_20l_2 simple_loop_20l_3
## append stbfb programs to programs list
PROGRAMS += $(addprefix $(STFB_FOLDER)/, $(STFB_PROGRAMS))

.DEFAULT_GOAL := default


#################################
# Build
#################################

spiflash.img: spiflash.py
	python3 $<

%.o: %.S
	$(GCC) $(CFLAGS) -D__ASSEMBLY__=1 -c $< -o $@

%.o: %.c mmio.h spiflash.h
	$(GCC) $(CFLAGS) -c $< -o $@

%.riscv: %.o $(libgloss)
	$(GCC) $(LDFLAGS) $< -o $@

%.dump: %.riscv
	$(OBJDUMP) -D $< > $@


#################################
# Recipes
#################################

.PHONY: clean
clean:
	rm -f *.riscv *.o *.dump
	rm -f $(STFB_FOLDER)/*.riscv $(STFB_FOLDER)/*.o $(STFB_FOLDER)/*.dump
	$(if $(libgloss),rm -rf $(libgloss_builddir)/)

.PHONY: default
default: $(addsuffix .riscv, $(PROGRAMS)) spiflash.img

.PHONY: dumps
dumps: $(addsuffix .dump, $(PROGRAMS))
