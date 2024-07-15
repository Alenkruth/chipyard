###############################################################################
# Makefile for boom-attacks
###########################
# Makes baremetal executables to run on BOOM
###############################################################################

# Folders
SRC:=src
INC:=inc
LNK:=link
OBJ:=obj
BIN:=bin
DMP:=dump
DEP:=dep

# Commands and flags
CC:=riscv64-unknown-elf-gcc
OBJDUMP:=riscv64-unknown-elf-objdump -S -w -g -z -h -t -M numeric,no-aliases
CFLAGS=-mcmodel=medany -march=rv64g -l -std=gnu99 -O0 -g -fno-common -fno-builtin-printf -Wall -I$(INC) -Wno-unused-function -Wno-unused-variable
LDFLAGS:=-static -nostdlib -nostartfiles -lgcc
DEPFLAGS=-MT $@ -MMD -MP -MF $(DEP)/$*.d
RM=rm -rf

# Programs to compile
PROGRAMS=fizz_buzz_count_0_0 fizz_buzz_count_1_0 fizz_buzz_count_2_0 fizz_buzz_count_3_0 \
fizz_buzz_count_4_0 fizz_buzz_print_0_0 fizz_buzz_print_1_0 fizz_buzz_print_2_0 \
fizz_buzz_print_3_0 fizz_buzz_print_4_0 full_hazards_0_0 full_hazards_1_0 \
full_hazards_2_0 full_hazards_3_0 parallel_hazards_0_0 parallel_hazards_1_0 \
parallel_hazards_2_0 parallel_hazards_3_0 
BINS=$(addprefix $(BIN)/,$(addsuffix .riscv,$(PROGRAMS)))
DUMPS=$(addprefix $(DMP)/,$(addsuffix .dump,$(PROGRAMS)))

# Build object files
$(OBJ)/$(PROGRAMS).o: $(SRC)/$(PROGRAMS).S
	@mkdir -p $(OBJ)
	$(CC) $(CFLAGS) -D__ASSEMBLY__=1 -c $< -o $@

$(OBJ)/$(PROGRAMS).o: $(SRC)/$(PROGRAMS).c
	@mkdir -p $(OBJ)
	@mkdir -p $(DEP)
	$(CC) $(CFLAGS) $(DEPFLAGS) -c $< -o $@

# Build executable
$(BIN)/$(PROGRAMS).riscv: $(OBJ)/$(PROGRAMS).o $(OBJ)/crt.o $(OBJ)/syscalls.o $(OBJ)/stack.o $(LNK)/link.ld
	@mkdir -p $(BIN)
	$(CC) -T $(LNK)/link.ld $(LDFLAGS) $< $(OBJ)/crt.o $(OBJ)/stack.o $(OBJ)/syscalls.o -o $@

# Build dump
$(DMP)/$(PROGRAMS).dump: $(BIN)/$(PROGRAMS).riscv
	@mkdir -p $(DMP)
	$(OBJDUMP) -D $< > $@


.PHONY: all
all: $(BINS) $(DUMPS)

dumps: $(DUMPS)

# .PHONY: default
# default: $(addprefix $(BIN)/,$(addsuffix .riscv, $(PROGRAMS)))

# Keep the temporary .o files
.PRECIOUS: $(OBJ)/%.o

# Remove all generated files
clean:
	rm -rf $(BIN) $(OBJ) $(DMP) $(DEP)

# Include dependencies
-include $(addprefix $(DEP)/,$(addsuffix .d,$(PROGRAMS)))