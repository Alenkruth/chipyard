#!/usr/bin/env bash
# pilot.sh -- run list of the activity-based power pilot (plan claude-artifacts/ooc-synthesis/plans/power-pilot-plan.md, section 3).
#
# Purpose : one documented, parameterised script for the steps P0 B1 B2 P1 P2 P3 S0 C0 S1 S2 C1 V0 V1 of the pilot.
#           Long steps are launched fully detached (setsid nohup ... < /dev/null &); the script returns at once.  Each launched
#           step leaves $W/logs/<step>.{pid,start,rc,end,log,cmd.sh}; a runner uses `wait` (kill -0 loop, never pgrep -f) from
#           a run_in_background command and reports the milestones (start, end with rc and numbers, failure) to its parent.
# Usage   : pilot.sh <step>            launch (or run, for P0/window/status/wait) one step
#           CAL_ID=W2 pilot.sh <step>  same, for calibration config W2 (see cal_cfgs.env); W=/data/akrish/scratch/power-cal/W2
#           pilot.sh check             DRYRUN of every step (all checks, nothing launched)
#           pilot.sh window            after S0/C0/S1: compute R, A, N, MAXC for S2/C1, write $W/window.env
#           pilot.sh status            state of every step (pid alive, rc, start/end)
#           pilot.sh wait <step>       block until the step ends (kill -0 on its pid), print END line with rc, exit with rc
#           pilot.sh list              steps with one-line descriptions
#           DRYRUN=1 pilot.sh <step>   run every check of that step and print the exact script it would launch; launch nothing,
#                                      write nothing under $W.  Checks that need an earlier step's output print PENDING, not FAIL.
# Inputs (environment, defaults in brackets):
#   CAL_ID  W4|W2|W1|MIN4|HELD2: calibration config; fills CONFIG PT_PARAMS W ELF TRACE_ARGS S1_MAXC from cal_cfgs.env (explicit env wins)
#   PT_PARAMS [$K,width=4]  TRACE_ARGS [empty; calibration: --trace-max-array 256]  S1_MAXC [4000000; calibration 16000000]
#   W [/data/akrish/scratch/power-pilot]  CY [/home/jht9sy/work/chipyard]  CONFIG [BaselineBoomPowerDMIConfig]
#   K [Baseline full-size knob list, plan header]   TILE_SCOPE [TOP/TestDriver/testHarness/chiptop0/system/tile_prci_domain/element_reset_domain_boom_tile]
#   SLOT_GRANTED=1   required for every step that builds, simulates or synthesizes: the controller grants each slot (plan 3)
#   GATE_CMD         optional command prefix for simulator launches (the shared gate.lock wrapper of the controller); the script also
#                    refuses a 3rd simulator (2-sim cap counted from the process table) and a 3rd build (Generator/firtool/Vivado)
#   B1_J [20]        make -j of the B1 sim build (the controller grants it; e.g. 10 beside bitstream builds)
#   B1_VT [20]       VERILATOR_THREADS of the B1 sim build (compiled into the simulator; a sim books VT + 2 threads incl. the FST writer;
#                    e.g. 16 when the controller grants <= 20 threads)
#   R A N            window: R testbench cycles per core cycle, A first window core cycle, N cycles (100000); `window` computes them
#   VIVADO_BIN XILINX_PART OUT_PERIOD_NS [10] CLOCK_NS [1.0, testbench clock period] C0_FROM [20000] C0_CYCLES [1000]
#   PROBES           space-separated probe net names for V1 (found in V0: $W/v0/probe_candidates.txt)
#   FIND_PATTERNS    V0 net-name search patterns ["*csr* *reset*"]
#   RCSR             reconfig-CSR file; S0/S1/S2 append ` +reconfig_csrs=$RCSR` after the ELF and log RCSR_FILE/RCSR_SHA1/RCSR_LOADED.
#                    With CAL_ID it comes from the line RCSR_<id>=<file> of the CAL_FILE. CAL_FILE=<file> selects another id table.
#   PT_PARAMS=fir    (3rd column of a CAL_FILE row) defense power point: POINT=$W/ooc/points/Tile/fir/$CONFIG, RTL made by step P1F
#                    (firtool + extract_rtl.py on the sim build's own FIRRTL); a FIR point without RCSR fails the check.
# Outputs : everything under $W (sw/ sw/b2/ ooc/ logs/ v0/ v1/ v1_nou/ s0.fst s2.fst s2.saif s2_nou.saif s2_profile.csv window.env ...).
#           Nothing is ever deleted by this script.
# Binding rules kept here: firesim_setup before set -u; plusargs after the ELF; fixed seed on S1 and S2; never `make verilog`;
#           the build command is the memory's debug build with a different CONFIG plus USE_FST=1 and EXTRA_SIM_SOURCES (plan B1,
#           the owner approved it in plan decision 1).

# ---- environment first (firesim_setup does not survive `set -u`)
shopt -s expand_aliases
[ -f "$HOME/.bash_aliases" ] && . "$HOME/.bash_aliases" >/dev/null 2>&1
FS_SETUP=missing
if declare -F firesim_setup >/dev/null 2>&1; then
  if firesim_setup >/dev/null 2>&1; then FS_SETUP=ok; else FS_SETUP=failed; fi
fi
set -u

# ---- calibration selector: CAL_ID=<W4|W2|W1|MIN4|HELD2> takes CONFIG, PT_PARAMS, W, ELF, TRACE_ARGS, S1_MAXC from cal_cfgs.env
#      (only for variables not already set in the environment).  CAL_ID unset: nothing below changes the pilot's behaviour.
if [ -n "${CAL_ID:-}" ]; then
  CAL_FILE="${CAL_FILE:-$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/cal_cfgs.env}"
  [ -f "$CAL_FILE" ] || { echo "pilot.sh: CAL_FILE missing: $CAL_FILE" >&2; exit 2; }
  _cal_row=$(awk -v id="$CAL_ID" '$1==id && NF==3 {print $2, $3}' "$CAL_FILE")
  [ -n "$_cal_row" ] || { echo "pilot.sh: CAL_ID '$CAL_ID' not in $CAL_FILE (ids: $(awk '!/^#/ && NF==3 {printf "%s ", $1}' "$CAL_FILE"))" >&2; exit 2; }
  _cal_var() { sed -n "s/^$1=//p" "$CAL_FILE" | head -1; }
  CONFIG="${CONFIG:-${_cal_row%% *}}"; PT_PARAMS="${PT_PARAMS:-${_cal_row##* }}"
  W="${W:-$(_cal_var CAL_W_BASE)/$CAL_ID}"; ELF="${ELF:-$(_cal_var CAL_ELF)}"
  TRACE_ARGS="${TRACE_ARGS:-$(_cal_var CAL_TRACE_ARGS)}"; S1_MAXC="${S1_MAXC:-$(_cal_var CAL_S1_MAXC)}"
  RCSR="${RCSR:-$(_cal_var "RCSR_$CAL_ID")}"   # defense power: reconfig-CSR file of this id (line RCSR_<id>=<file> in the CAL_FILE)
fi
W="${W:-/data/akrish/scratch/power-pilot}"
CY="${CY:-/home/jht9sy/work/chipyard}"
CONFIG="${CONFIG:-BaselineBoomPowerDMIConfig}"
K="${K:-rob=256,fb=64,ftq=32,ras=32,iprf=192,fprf=96,iq_int=32,iq_mem=32,iq_fp=32,ldq=40,stq=40,btb_sets=128,btb_ways=2,tage=7,ic_sets=64,ic_ways=8,dc_sets=128,dc_ways=8}"
PT_PARAMS="${PT_PARAMS:-$K,width=4}"
TRACE_ARGS="${TRACE_ARGS:-}"; S1_MAXC="${S1_MAXC:-4000000}"   # workload is dhrystone only: no RUN_TAG
SIM="${SIM:-$CY/sims/verilator/simulator-chipyard.harness-$CONFIG-debug}"
GENSRC="${GENSRC:-$CY/sims/verilator/generated-src/chipyard.harness.TestHarness.$CONFIG}"
LONG="chipyard.harness.TestHarness.$CONFIG"
# PT_PARAMS=fir (defense power rows): the tile RTL is cut from the sim build's own FIRRTL by P1F, not elaborated by elaborate.sh
if [ "$PT_PARAMS" = fir ]; then POINT="${POINT:-$W/ooc/points/Tile/fir/$CONFIG}"; else POINT="${POINT:-$W/ooc/points/Tile/base/$PT_PARAMS}"; fi
ELF="${ELF:-$W/sw/dhrystone1500.riscv}"
TILE_SCOPE="${TILE_SCOPE:-TOP/TestDriver/testHarness/chiptop0/system/tile_prci_domain/element_reset_domain_boom_tile}"
OUT_PERIOD_NS="${OUT_PERIOD_NS:-10}"; CLOCK_NS="${CLOCK_NS:-1.0}"; N="${N:-100000}"
C0_FROM="${C0_FROM:-20000}"; C0_CYCLES="${C0_CYCLES:-1000}"
VIVADO_BIN="${VIVADO_BIN:-/tools/Xilinx/Vivado/2023.1/bin/vivado}"; XILINX_PART="${XILINX_PART:-xcu250-figd2104-2L-e}"
FIRTOOL_BIN="${FIRTOOL_BIN:-$CY/.conda-env/riscv-tools/bin/firtool}"
RT="${RT:-$CY/toolchains/riscv-tools/riscv-tests}"
SLOT_GRANTED="${SLOT_GRANTED:-0}"; GATE_CMD="${GATE_CMD:-}"; DRYRUN="${DRYRUN:-0}"
FIND_PATTERNS="${FIND_PATTERNS:-*csr* *reset*}"; PROBES="${PROBES:-}"
POWER="$CY/tools/ooc-synth/power"; TOOLS="$CY/tools/ooc-synth"
LOGS="$W/logs"; BIN="$W/bin"; F2S="$BIN/fst2saif"
SEED_ARGS="+verilator+seed+1 +verilator+rand+reset+2"
RCSR="${RCSR:-}"; RCSR_ARG="${RCSR:+ +reconfig_csrs=$RCSR}"   # DTM delivers these CSR writes before the program starts (plusarg after the ELF)
FST_V="$CY/.conda-env/share/verilator/include/gtkwave"
OOC_MAX_BUILDS="${OOC_MAX_BUILDS:-2}"; MAX_SIMS="${MAX_SIMS:-2}"
B1_VT="${B1_VT:-20}"   # VERILATOR_THREADS of B1 (default 20 = unchanged)
B1_J="${B1_J:-20}"   # make -j of B1 (the controller may grant less beside other builds, e.g. B1_J=10)
R="${R:-}"; A="${A:-}"
[ -f "$W/window.env" ] && . "$W/window.env"          # R A N MAXC written by `window`

NOK=0; NPEND=0; NFAIL=0
say()  { echo "[$(date '+%F %T')] $*"; }
ok()   { NOK=$((NOK+1)); echo "  ok      $*"; }
pend() { NPEND=$((NPEND+1)); echo "  PENDING $*"; }
bad()  { NFAIL=$((NFAIL+1)); echo "  FAIL    $*"; }
die()  { echo "pilot.sh: $*" >&2; exit 2; }
chk_file() { if [ -e "$1" ]; then ok "$2: $1"; else bad "$2 missing: $1"; fi; }
chk_exec() { if [ -x "$1" ] || command -v "$1" >/dev/null 2>&1; then ok "$2: $1"; else bad "$2 not found: $1"; fi; }
chk_later() { if [ -e "$1" ]; then ok "$2: $1"; elif [ "$DRYRUN" = 1 ]; then pend "$2 (made by an earlier step): $1"; else bad "$2 missing: $1"; fi; }
chk_env()  { if [ -n "${!1:-}" ]; then ok "$1=${!1}"; else bad "$1 not set"; fi; }

n_builds() {   # real processes only (bash -c wrappers are not counted); a counted process with a counted ancestor
  # (e.g. a FireSim bitstream Vivado and the synth/impl Vivado it spawns via runme.sh) is the same build, counted once
  local all p q n=0; all=" $( { pgrep -f '^\S*java .*chipyard\.Generator'; pgrep -f '^\S*firtool '; pgrep -f '^\S*unwrapped/lnx64\.o/vivado '; } | tr '\n' ' ')"
  for p in $all; do q=$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ')
    while [ -n "$q" ] && [ "$q" -gt 1 ]; do case "$all" in *" $q "*) continue 2;; esac; q=$(ps -o ppid= -p "$q" 2>/dev/null | tr -d ' '); done
    n=$((n+1)); done; echo $n; }
n_sims()   { pgrep -f '^\S*(simulator-chipyard|VFireSim)' | wc -l; }   # same count as the shared gate (simref/run.sh, gate_exec.sh); wrappers such as timeout/stdbuf are not counted
chk_common() {
  if [ "$FS_SETUP" = ok ]; then ok "firesim_setup"; else bad "firesim_setup $FS_SETUP"; fi
  chk_env RISCV
  if [ -d "$W" ]; then ok "W exists: $W"; elif [ "$DRYRUN" = 1 ]; then pend "W will be created: $W"; else mkdir -p "$W" "$LOGS" "$BIN" || bad "cannot create $W"; fi
}
chk_slot() {  # $1 = build|sim|vivado
  if [ "$SLOT_GRANTED" = 1 ]; then ok "SLOT_GRANTED=1"
  elif [ "$DRYRUN" = 1 ]; then pend "SLOT_GRANTED not set (a real launch refuses until the controller grants the slot)"
  else bad "SLOT_GRANTED=1 required (controller grants the slot)"; fi
  case "$1" in
    build|vivado)
      local b; b=$(n_builds)
      if [ "$b" -lt "$OOC_MAX_BUILDS" ]; then ok "builds running now: $b (cap $OOC_MAX_BUILDS)"
      elif [ "$DRYRUN" = 1 ]; then pend "$b builds running now (cap $OOC_MAX_BUILDS): a launch would wait"
      else bad "$b builds running (cap $OOC_MAX_BUILDS): wait and retry"; fi ;;
    sim)
      local s; s=$(n_sims)
      if [ "$s" -lt "$MAX_SIMS" ]; then ok "simulators running now: $s (cap $MAX_SIMS)"
      elif [ "$DRYRUN" = 1 ]; then pend "$s simulators running now (cap $MAX_SIMS): a launch would wait"
      else bad "$s simulators running (cap $MAX_SIMS): wait and retry"; fi
      if [ -n "$GATE_CMD" ]; then ok "GATE_CMD=$GATE_CMD"; else echo "  note    GATE_CMD empty: the controller's gate.lock wrapper is not applied by this script"; fi ;;
  esac
}
# launch <step> <script-text>: the generated script runs detached; DRYRUN prints it and launches nothing
launch() {
  local step="$1" text="$2"
  if ! bash -n <<<"$text"; then bad "generated script for $step has a syntax error"; return 1; fi
  if [ "$DRYRUN" = 1 ]; then echo "  ---- $step would launch (detached, log $LOGS/$step.log):"; sed 's/^/  | /' <<<"$text"; return 0; fi
  [ "$NFAIL" -eq 0 ] || { echo "pilot.sh: $NFAIL check(s) failed, $step not launched" >&2; return 3; }
  mkdir -p "$LOGS" "$BIN"
  if [ -e "$LOGS/$step.pid" ] && kill -0 "$(cat "$LOGS/$step.pid")" 2>/dev/null; then echo "pilot.sh: $step already running (pid $(cat "$LOGS/$step.pid"))" >&2; return 3; fi
  rm -f "$LOGS/$step.rc" "$LOGS/$step.end"
  printf '%s\n' "$text" > "$LOGS/$step.cmd.sh"
  date +%s > "$LOGS/$step.start"
  setsid nohup bash -c "bash '$LOGS/$step.cmd.sh'; echo \$? > '$LOGS/$step.rc'; date +%s > '$LOGS/$step.end'" < /dev/null > "$LOGS/$step.log" 2>&1 &
  echo $! > "$LOGS/$step.pid"
  say "STEP $step STARTED pid=$(cat "$LOGS/$step.pid") log=$LOGS/$step.log"
}
hdr() { printf 'set -u\ncd %q\n' "$CY"; }
# reconfig-CSR file (defense power): a FIR point (PT_PARAMS=fir) must name one, else the run would silently be undefended
chk_rcsr() {
  if [ -n "$RCSR" ]; then chk_file "$RCSR" "reconfig_csrs file"
  elif [ "$PT_PARAMS" = fir ]; then bad "RCSR not set for a FIR point (line RCSR_${CAL_ID:-<id>}=<file> in the CAL_FILE, or env RCSR)"
  else echo "  note    RCSR empty: no +reconfig_csrs plusarg"; fi
}
# lines appended to a sim step's script: which file, its sha1, and the DTM's 'loaded N writes' proof (0 = the file was not loaded)
rcsr_log() { [ -n "$RCSR" ] || return 0
  printf 'echo RCSR_FILE=%q RCSR_SHA1=$(sha1sum %q | cut -c1-40)\necho RCSR_LOADED=$(grep -c "reconfig_csrs: loaded" %q)\n' "$RCSR" "$RCSR" "$LOGS/$1.log"; }
build_f2s_cmd() { printf 'mkdir -p %q\ngcc -O2 -DFST_CONFIG_INCLUDE=%q -o %q %q %q/fstapi.c %q/lz4.c %q/fastlz.c -I%q -lz -lpthread -lm || exit 1\n' "$BIN" '"fst_config.h"' "$F2S" "$POWER/fst2saif.c" "$FST_V" "$FST_V" "$FST_V" "$FST_V"; }
chk_f2s_src() {
  chk_file "$POWER/fst2saif.c" "converter source"; chk_file "$FST_V/fstapi.c" "FST library"; chk_exec gcc "gcc"
  if gcc -fsyntax-only -DFST_CONFIG_INCLUDE='"fst_config.h"' -I"$FST_V" "$POWER/fst2saif.c" 2>/dev/null; then ok "fst2saif.c compiles (syntax check)"; else bad "fst2saif.c does not compile"; fi
}
need_window() {
  if [ -n "$A" ] && [ -n "$R" ]; then ok "window A=$A R=$R N=$N"
  elif [ "$DRYRUN" = 1 ]; then pend "A and R unknown (run S0, C0, S1, then: pilot.sh window)"
  else bad "A/R unknown: run 'pilot.sh window' (needs C0 and S1)"; fi
}

# ------------------------------------------------------------------ steps
step_P0() {
  say "P0 preflight"
  chk_common
  local t="$LOGS/P0.txt"
  if [ "$DRYRUN" = 1 ]; then t=/dev/stdout; else mkdir -p "$LOGS"; fi
  { echo "date: $(date '+%F %T %Z')"
    for g in generators/boom generators/rocket-chip; do
      echo "$g HEAD: $(git -C "$CY/$g" rev-parse HEAD 2>&1)"
      echo "$g dirty files: $(git -C "$CY/$g" status --porcelain 2>&1 | wc -l)  diff sha1: $(git -C "$CY/$g" diff 2>&1 | sha1sum | cut -c1-40)"
    done
    echo "chipyard HEAD: $(git -C "$CY" rev-parse HEAD)  diff sha1 (tracked): $(git -C "$CY" diff 2>&1 | sha1sum | cut -c1-40)"
    echo "builds running: $(n_builds)  simulators running: $(n_sims)"
    echo "controller must confirm: tree stable (no mid-edit in generators/boom) and a build window"; } > "$t"
  if [ "$DRYRUN" != 1 ]; then ok "written $t"; fi
}
step_B1() {
  say "B1 sim build ($CONFIG, FST, trace-underscore, tile .vlt)"
  chk_common; chk_slot build
  chk_file "$POWER/tile_trace.vlt" "tile trace config"
  if grep -q "class $CONFIG " "$CY/generators/chipyard/src/main/scala/config/BoomConfigs.scala" 2>/dev/null; then ok "E1 applied: class $CONFIG"
  elif [ "$DRYRUN" = 1 ]; then pend "E1 not applied yet: class $CONFIG missing in BoomConfigs.scala"
  else bad "E1 not applied: class $CONFIG missing in BoomConfigs.scala"; fi
  chk_exec numactl "numactl"; chk_exec make "make"; chk_exec "$FIRTOOL_BIN" "firtool"
  if command -v verilator >/dev/null 2>&1; then ok "verilator: $(verilator --version 2>&1 | head -1)"; else bad "verilator not on PATH"; fi
  local fr; fr=$(df -Pk "$CY/sims/verilator" | awk 'NR==2{print int($4/1048576)}')
  if [ "${fr:-0}" -ge 10 ]; then ok "free space sims/verilator: ${fr} GB"; else bad "free space sims/verilator ${fr} GB < 10"; fi
  launch B1 "$(hdr)
export PATH=$(dirname "$FIRTOOL_BIN"):\$PATH
cd $CY/sims/verilator
make -j$B1_J debug CONFIG=$CONFIG VERILATOR_THREADS=$B1_VT NUMACTL=1 USE_FST=1 EXTRA_SIM_SOURCES=\"--trace-underscore $POWER/tile_trace.vlt${TRACE_ARGS:+ $TRACE_ARGS}\"
rc=\$?
ls -l $SIM
sha1sum $SIM
exit \$rc"
}
step_B2() {
  say "B2 dhrystone-1500 copy"
  chk_common
  local src="$RT/benchmarks/dhrystone/dhrystone.h" inst="$RT/build/benchmarks/dhrystone.riscv"
  chk_file "$src" "riscv-tests dhrystone.h"; chk_file "$inst" "installed dhrystone.riscv"; chk_file "$RT/benchmarks/common/test.ld" "linker script"; chk_file "$RT/env" "riscv-tests env"
  if grep -qP 'NUMBER_OF_RUNS\t\t500 ' "$src" 2>/dev/null; then ok "dhrystone.h has the unconditional run-count line (NUMBER_OF_RUNS 500)"; else bad "dhrystone.h NUMBER_OF_RUNS line not as expected"; fi
  chk_exec riscv64-unknown-elf-gcc "cross compiler"; chk_exec riscv64-unknown-elf-objdump "objdump"; chk_exec riscv64-unknown-elf-readelf "readelf"
  local OPTS='-DPREALLOCATE=1 -mcmodel=medany -static -std=gnu99 -O2 -ffast-math -fno-common -fno-builtin-printf -fno-tree-loop-distribute-patterns -Wno-implicit-int -Wno-implicit-function-declaration -march=rv64gcv -mabi=lp64d'
  if grep -q -- '-march=rv$(XLEN)gcv' "$RT/benchmarks/Makefile" 2>/dev/null; then ok "benchmarks/Makefile RISCV_GCC_OPTS still uses -march=rv\$(XLEN)gcv"; else bad "benchmarks/Makefile flags changed: update B2"; fi
  if [ -e "$ELF" ]; then
    ok "$ELF already exists (sha1 $(sha1sum "$ELF" | cut -c1-8)): B2 not launched, nothing is overwritten (rebuild by moving it away yourself)"
    return 0
  fi
  launch B2 "$(hdr)
S=$W/sw/b2; mkdir -p \$S/benchmarks \$S/dump
cp -r $RT/benchmarks/common $RT/benchmarks/dhrystone \$S/benchmarks/
cp -r $RT/env \$S/
sed -i 's/NUMBER_OF_RUNS\t\t500/NUMBER_OF_RUNS\t\t1500/' \$S/benchmarks/dhrystone/dhrystone.h
grep -n NUMBER_OF_RUNS \$S/benchmarks/dhrystone/dhrystone.h | head -2
P=riscv64-unknown-elf
\$P-gcc -I\$S/env -I\$S/benchmarks/common -I\$S/benchmarks/dhrystone $OPTS -o $ELF \$S/benchmarks/dhrystone/*.c \$S/benchmarks/common/*.c \$S/benchmarks/common/*.S -static -nostdlib -nostartfiles -lm -lgcc -T \$S/benchmarks/common/test.ld || exit 1
\$P-objdump -d $ELF > \$S/dump/copy.dis; \$P-objdump -d $inst > \$S/dump/installed.dis
\$P-readelf -A $ELF | grep Tag_RISCV_arch > \$S/dump/copy.arch; \$P-readelf -A $inst | grep Tag_RISCV_arch > \$S/dump/installed.arch
cmp \$S/dump/copy.arch \$S/dump/installed.arch && echo ARCH_SAME || { echo ARCH_DIFFERENT; exit 1; }
# only the run-count immediate may differ (500 = 0x1f4, 1500 = 0x5dc, both fit an addi immediate: same size, same addresses)
diff \$S/dump/installed.dis \$S/dump/copy.dis | grep '^[<>]' | grep -v -E '1f4|5dc|\\b500\\b|\\b1500\\b' > \$S/dump/unexpected.txt
NU=\$(wc -l < \$S/dump/unexpected.txt); echo B2_UNEXPECTED_DIFF_LINES=\$NU; echo B2_DIFF_LINES=\$(diff \$S/dump/installed.dis \$S/dump/copy.dis | grep -c '^[<>]')
[ \$NU -eq 0 ] || exit 1
sha1sum $ELF"
}
step_P1() {
  say "P1 fresh OOC elaboration (Tile base, width 4)"
  chk_common; chk_slot build
  if [ "$PT_PARAMS" = fir ]; then   # FIR points get their RTL from P1F; elaborate.sh has no such point
    if [ "$DRYRUN" = 1 ]; then echo "  note    PT_PARAMS=fir: P1 does not apply to this point (P1F makes its RTL); a real P1 refuses"
    else bad "PT_PARAMS=fir: P1 (elaborate.sh) does not apply; run P1F"; fi
  fi
  chk_file "$TOOLS/elaborate.sh" "elaborate.sh"; chk_exec "$FIRTOOL_BIN" "firtool"
  chk_later "$SIM" "B1 simulator (P1 must run after B1: same jar)"
  if [ -z "${OOC_ALLOW_STALE_JAR:-}" ]; then ok "stale-jar gate ON (OOC_ALLOW_STALE_JAR unset)"; else bad "OOC_ALLOW_STALE_JAR is set; the gate must stay on"; fi
  launch P1 "$(hdr)
export CHIPYARD_ROOT=$CY OOC_WORK=$W/ooc FIRTOOL_BIN=$FIRTOOL_BIN OOC_STATIC_FIX=off
mkdir -p $W/ooc
$TOOLS/elaborate.sh Tile base \"$PT_PARAMS\"
rc=\$?
echo P1_RTL_SHA1=\$(python3 -I -c \"import json;print(json.load(open('$POINT/rtl/elab.json'))['rtl_sha1'])\" 2>&1)   # Nov 1 point: 4be340abb551...
exit \$rc"
}
step_P1F() {
  say "P1F tile RTL from the sim build's own FIRRTL (firtool + extract_rtl.py; defense power points, no elaborate.sh)"
  chk_common; chk_slot build
  chk_exec "$FIRTOOL_BIN" "firtool"; chk_exec taskset "taskset"; chk_file "$TOOLS/scala/extract_rtl.py" "extract_rtl.py"
  local fir="$GENSRC/$LONG.fir" anno="$GENSRC/$LONG.appended.anno.json" low="$GENSRC/.mfc_lowering_options"
  local def_low=emittedLineLength=2048,noAlwaysComb,disallowLocalVariables,verifLabels,disallowPortDeclSharing,locationInfoStyle=wrapInAtSquareBracket
  chk_later "$fir" "B1 FIRRTL"; chk_later "$anno" "B1 appended annotations"
  if [ -e "$POINT/rtl" ]; then   # any rtl/ (an earlier P1F, or an elaborate.sh point of a calibration root) is never overwritten
    if [ "$DRYRUN" = 1 ]; then echo "  note    $POINT/rtl exists: a real P1F would refuse (nothing is overwritten)"
    else bad "$POINT/rtl exists: nothing is overwritten; move the point away yourself"; fi
  fi
  [ "$PT_PARAMS" = fir ] || echo "  note    PT_PARAMS=$PT_PARAMS (not 'fir'): POINT=$POINT"
  launch P1F "$(hdr)
P=$POINT; mkdir -p \$P/rtl
LOW=\$(cat $low 2>/dev/null || echo $def_low)   # the sim's own lowering options (common.mk MFC_LOWERING_OPTIONS)
echo P1F_LOWERING=\$LOW
# the sim's own firtool call (common.mk SFC_MFC_TARGETS) minus --repl-seq-mem and --split-verilog: Vivado infers the memories as in the bitstream
taskset -c 0-3 $FIRTOOL_BIN --format=fir --warn-on-unprocessed-annotations --disable-annotation-classless --disable-annotation-unknown --lowering-options=\$LOW --annotation-file=$anno -o \$P/ooc.sv $fir > \$P/firtool.log 2>&1 || { echo P1F_FIRTOOL_FAIL; exit 1; }
python3 -I $TOOLS/scala/extract_rtl.py find \$P/ooc.sv element_reset_domain_boom_tile > \$P/find.txt 2>&1 || { echo P1F_FIND_FAIL; exit 1; }
python3 -I $TOOLS/scala/extract_rtl.py emit \$P/ooc.sv element_reset_domain_boom_tile \$P/rtl > \$P/emit.log 2>&1 || { echo P1F_EMIT_FAIL; exit 1; }
python3 -I - \$P/rtl $fir $anno $FIRTOOL_BIN \$P/ooc.sv <<'PY'
import hashlib, json, os, subprocess, sys
rtl, fir, anno, ft, sv = sys.argv[1:6]
sha = lambda f: hashlib.sha1(open(f, 'rb').read()).hexdigest()
d = {'kind': 'fir-extract (P1F)', 'fir': fir, 'fir_sha1': sha(fir), 'anno': anno, 'anno_sha1': sha(anno), 'ooc_sv_sha1': sha(sv),
     'firtool': ft, 'firtool_sha1': sha(ft), 'firtool_version': subprocess.run([ft, '--version'], capture_output=True, text=True).stdout.split('\n')[0],
     'rtl_files': {f: sha(os.path.join(rtl, f)) for f in sorted(os.listdir(rtl)) if f != 'fir_elab.json'}}
json.dump(d, open(os.path.join(rtl, 'fir_elab.json'), 'w'), indent=1, sort_keys=True)
print('P1F_RTL_FILES=%d P1F_FIR_SHA1=%s' % (len(d['rtl_files']), d['fir_sha1'][:12]))
PY
echo P1F_TOP=\$(tr -d ' \\t\\r\\n' < \$P/rtl/top.txt)
exit 0"
}
step_P2() {
  say "P2 synthesis + post-synthesis checkpoint (vectorless power, OOC_CHECKPOINT=1)"
  chk_common; chk_slot vivado
  chk_exec "$VIVADO_BIN" "vivado"; chk_file "$TOOLS/run_point.sh" "run_point.sh"
  chk_later "$POINT/rtl/filelist.f" "P1 rtl"
  if grep -q "OOC_CHECKPOINT" "$TOOLS/synth/ooc_synth.tcl" 2>/dev/null; then ok "E2 applied (OOC_CHECKPOINT in ooc_synth.tcl)"
  elif [ "$DRYRUN" = 1 ]; then pend "E2 not applied yet"
  else bad "E2 not applied: ooc_synth.tcl has no OOC_CHECKPOINT"; fi
  launch P2 "$(hdr)
export VIVADO_BIN=$VIVADO_BIN XILINX_PART=$XILINX_PART OOC_CLOCK_NS=10 OOC_UTIL_DEPTH=8 OOC_POWER=1 OOC_CHECKPOINT=1 OOC_MAX_THREADS=8
$TOOLS/run_point.sh \"$POINT\"
rc=\$?
ls -l \"$POINT/post_synth.dcp\" \"$POINT/result.json\"
grep -m1 'Total On-Chip Power' \"$POINT/power_hier.rpt\"; grep -m1 'Dynamic' \"$POINT/power_hier.rpt\"
grep 'OOC_CHECKPOINT_S' \"$POINT/synth.log\"
head -40 \"$POINT/result.json\"
exit \$rc"
}
step_P3() {
  say "P3 tile equivalence (OOC rtl vs sim gen-collateral)"
  chk_common; chk_file "$POWER/tile_equiv.py" "tile_equiv.py"
  if python3 -I "$POWER/tile_equiv.py" --help >/dev/null 2>&1; then ok "tile_equiv.py --help runs"; else bad "tile_equiv.py does not start"; fi
  chk_later "$POINT/rtl/BoomTile.sv" "P1 rtl"; chk_later "$GENSRC/gen-collateral" "B1 generated-src"
  launch P3 "$(hdr)
python3 -I $POWER/tile_equiv.py --ooc \"$POINT/rtl\" --sim $GENSRC"
}
step_S0() {
  say "S0 smoke trace (60000 testbench cycles)"
  chk_common; chk_slot sim; chk_f2s_src; chk_rcsr
  chk_later "$SIM" "B1 simulator"; chk_later "$ELF" "B2 dhrystone1500.riscv"
  launch S0 "$(hdr)
mkdir -p $W
$GATE_CMD /usr/bin/time -v -o $LOGS/S0.time $SIM $ELF +verbose +max-cycles=60000 $SEED_ARGS$RCSR_ARG +vcdfile=$W/s0.fst
echo S0_SIM_RC=\$?
$(rcsr_log S0)
ls -l $W/s0.fst; grep -E 'Elapsed|Maximum resident' $LOGS/S0.time
$(build_f2s_cmd)
$F2S --fst $W/s0.fst --list-scopes > $W/s0.scopes.txt; echo S0_SCOPES=\$(wc -l < $W/s0.scopes.txt)
awk -F'\\t' -v t=\"$TILE_SCOPE\" '\$1==t{f=1} END{print \"S0_TILE_SCOPE_PRESENT=\" (f?1:0)}' $W/s0.scopes.txt
awk -F'\\t' -v t=\"$TILE_SCOPE\" '\$1==t || index(\$1,t\"/\")==1{n++;next} {o++} END{print \"S0_SCOPES_IN_TILE=\" n+0 \" S0_SCOPES_OUTSIDE_TILE=\" o+0}' $W/s0.scopes.txt"
}
step_C0() {
  say "C0 converter check on the smoke dump ($C0_CYCLES cycles from cycle $C0_FROM)"
  chk_common; chk_f2s_src; chk_file "$POWER/fst2saif_ref.py" "reference"; chk_exec fst2vcd "fst2vcd"
  chk_later "$W/s0.fst" "S0 dump"
  launch C0 "$(hdr)
$(build_f2s_cmd)
$F2S --fst $W/s0.fst --scope $TILE_SCOPE --clock clock --from-cycle $C0_FROM --cycles $C0_CYCLES --out-period-ns $OUT_PERIOD_NS --out $W/c0.saif --profile $W/c0_profile.csv --bucket 100 > $W/c0.out || exit 1
cat $W/c0.out
python3 -I $POWER/fst2saif_ref.py --fst $W/s0.fst --scope $TILE_SCOPE --clock clock --from-cycle $C0_FROM --cycles $C0_CYCLES --out-period-ns $OUT_PERIOD_NS --out $W/c0.ref.saif --profile $W/c0_profile.ref.csv --bucket 100 > $W/c0.ref.out || exit 1
if cmp $W/c0.saif $W/c0.ref.saif && cmp $W/c0_profile.csv $W/c0_profile.ref.csv; then echo C0_RESULT=IDENTICAL; else echo C0_RESULT=DIFFERENT; exit 1; fi
echo C0_CLOCK_TC=\$(grep -m1 '^ *(clock ' $W/c0.saif | sed 's/.*(TC \\([0-9]*\\)).*/\\1/') expected=$((2*C0_CYCLES))"
}
step_S1() {
  say "S1 untraced reference run (PASSED line, T_pass, mcycle)"
  chk_common; chk_slot sim; chk_rcsr
  chk_later "$SIM" "B1 simulator"; chk_later "$ELF" "B2 dhrystone1500.riscv"
  launch S1 "$(hdr)
$GATE_CMD /usr/bin/time -v -o $LOGS/S1.time $SIM $ELF +verbose +max-cycles=$S1_MAXC $SEED_ARGS$RCSR_ARG
echo S1_SIM_RC=\$?
$(rcsr_log S1)
grep -E 'Elapsed|Maximum resident' $LOGS/S1.time"
}
step_window() {
  say "window: R, A, N, MAXC"
  chk_later "$W/c0.out" "C0 output (clock period)"; chk_later "$LOGS/S1.log" "S1 log"
  if ! { [ -s "$W/c0.out" ] && [ -s "$LOGS/S1.log" ]; }; then
    if [ "$DRYRUN" = 1 ]; then return 0; else die "C0 and S1 outputs needed"; fi
  fi
  local pns tp m r a
  pns=$(sed -n 's/^period_ns=//p' "$W/c0.out"); tp=$(grep -aoE 'PASSED \*\*\* Completed after +[0-9]+ simulation cycles' "$LOGS/S1.log" | head -1 | grep -oE '[0-9]+'); m=$(grep -oE 'mcycle *= *[0-9]+' "$LOGS/S1.log" | head -1 | grep -oE '[0-9]+$')
  if [ -z "$pns" ] || [ -z "$tp" ] || [ -z "$m" ]; then die "could not read period_ns ($pns), T_pass ($tp) or mcycle ($m)"; fi
  r=$(python3 -I -c "import sys;p=float('$pns')/float('$CLOCK_NS');r=round(p);sys.exit(1) if abs(p-r)>0.01 or r<1 else print(r)") || die "tile clock period $pns ns is not an integer multiple of CLOCK_NS=$CLOCK_NS"
  a=$(( tp / r - m + 50000 )); if [ "$a" -le 0 ]; then die "A=$a is not positive (T_pass=$tp R=$r mcycle=$m)"; fi
  if [ "$DRYRUN" = 1 ]; then echo "  would write window.env: R=$r A=$a N=$N MAXC=$(( r*(a+N)+20000 ))"; return 0; fi
  printf 'R=%s\nA=%s\nN=%s\nMAXC=%s\n' "$r" "$a" "$N" "$(( r*(a+N)+20000 ))" > "$W/window.env"
  echo "T_pass=$tp mcycle=$m period_ns=$pns -> R=$r A=$a N=$N MAXC=$(( r*(a+N)+20000 )) (written $W/window.env)"
}
step_S2() {
  say "S2 traced run (FST of boot + window)"
  chk_common; chk_slot sim; need_window; chk_rcsr
  chk_later "$SIM" "B1 simulator"; chk_later "$ELF" "B2 dhrystone1500.riscv"
  local maxc="${MAXC:-MAXC_FROM_WINDOW}"
  launch S2 "$(hdr)
$GATE_CMD /usr/bin/time -v -o $LOGS/S2.time $SIM $ELF +verbose +max-cycles=$maxc $SEED_ARGS$RCSR_ARG +vcdfile=$W/s2.fst
echo S2_SIM_RC=\$?   # a +max-cycles stop is the expected ending (timeout line, closed FST)
$(rcsr_log S2)
ls -l $W/s2.fst; grep -E 'Elapsed|Maximum resident' $LOGS/S2.time
tail -3 $LOGS/S2.log"
}
step_C1() {
  say "C1 conversion (with and without underscore names)"
  chk_common; chk_f2s_src; need_window
  chk_later "$W/s2.fst" "S2 dump"
  launch C1 "$(hdr)
$(build_f2s_cmd)
/usr/bin/time -v -o $LOGS/C1.time $F2S --fst $W/s2.fst --scope $TILE_SCOPE --clock clock --from-cycle $A --cycles $N --out-period-ns $OUT_PERIOD_NS --out $W/s2.saif --profile $W/s2_profile.csv > $W/s2.conv.out; echo C1_RC=\$?
cat $W/s2.conv.out; grep -E 'Elapsed|Maximum resident' $LOGS/C1.time
/usr/bin/time -v -o $LOGS/C1nou.time $F2S --fst $W/s2.fst --scope $TILE_SCOPE --clock clock --from-cycle $A --cycles $N --out-period-ns $OUT_PERIOD_NS --skip-underscore --out $W/s2_nou.saif > $W/s2_nou.conv.out; echo C1_NOU_RC=\$?
cat $W/s2_nou.conv.out; grep -E 'Elapsed|Maximum resident' $LOGS/C1nou.time
ls -l $W/s2.saif $W/s2_nou.saif
python3 -I - $W/s2_profile.csv $A $N <<'PY'
import csv,sys
rows=list(csv.DictReader(open(sys.argv[1]))); a=int(sys.argv[2]); n=int(sys.argv[3])
w=[int(r['toggles']) for r in rows if a<=int(r['first_cycle'])<a+n]; m=sum(w)/len(w)
print('PROFILE window buckets=%d mean=%.0f min=%d max=%d max_dev=%.1f%%'%(len(w),m,min(w),max(w),100*max(abs(x-m) for x in w)/m))
PY"
}
vivado_power_cmd() { # <saif> <outdir> <probes> <find>
  printf 'mkdir -p %q\ncd %q || exit 1   # Vivado writes .Xil/ into its cwd: keep it out of the repo\nPWR_DCP=%q PWR_OUT=%q PWR_SAIF=%q PWR_STRIP=%q PWR_PROBES=%q PWR_FIND=%q OOC_MAX_THREADS=8 %q -mode batch -nojournal -nolog -source %q > %q 2>&1\necho VIVADO_RC=$?\ngrep -E "PWR_STEP|PWR_INFO|PWR_FAIL|PWR_WARN|PWR_ERROR|PWR_DONE" %q\n' \
    "$2" "$2" "$POINT/post_synth.dcp" "$2" "$1" "$TILE_SCOPE" "$3" "$4" "$VIVADO_BIN" "$TOOLS/synth/power_saif.tcl" "$2/vivado.log" "$2/vivado.log"
}
chk_vivado_power() { chk_slot vivado; chk_exec "$VIVADO_BIN" "vivado"; chk_file "$TOOLS/synth/power_saif.tcl" "power_saif.tcl"; chk_later "$POINT/post_synth.dcp" "P2 checkpoint"; }
step_V0() {
  say "V0 naming check on the smoke SAIF (help text, match rate, probe-net names)"
  chk_common; chk_f2s_src; chk_vivado_power; chk_later "$W/s0.fst" "S0 dump"
  launch V0 "$(hdr)
$(build_f2s_cmd)
mkdir -p $W/v0
cat > $W/v0/help.tcl <<'TCL'
foreach c {read_saif report_switching_activity report_power} { puts \"=== help \$c\"; catch {help \$c} m; puts \$m }
TCL
$VIVADO_BIN -mode batch -nojournal -nolog -source $W/v0/help.tcl > $W/v0/help.txt 2>&1; echo V0_HELP_RC=\$?
$F2S --fst $W/s0.fst --scope $TILE_SCOPE --clock clock --from-cycle $C0_FROM --cycles $C0_CYCLES --out-period-ns $OUT_PERIOD_NS --out $W/s0.saif > $W/s0.conv.out || exit 1
$(vivado_power_cmd "$W/s0.saif" "$W/v0" "$PROBES" "$FIND_PATTERNS")
cat $W/v0/match.txt
wc -l $W/v0/probe_candidates.txt"
}
step_V1() {
  say "V1 activity power (SAIF with and without underscore names)"
  chk_common; chk_vivado_power; chk_later "$W/s2.saif" "C1 SAIF"; chk_later "$W/s2_nou.saif" "C1 SAIF without underscore"
  if [ -n "$PROBES" ]; then ok "PROBES set: $PROBES"; else echo "  note    PROBES empty: step 4 (probe nets) is skipped; set it from $W/v0/probe_candidates.txt"; fi
  launch V1 "$(hdr)
$(vivado_power_cmd "$W/s2.saif" "$W/v1" "$PROBES" "")
$(vivado_power_cmd "$W/s2_nou.saif" "$W/v1_nou" "$PROBES" "")
echo V1_VECTORLESS=\$(grep -m1 'Dynamic (W)' $W/v1/vectorless.rpt)
echo V1_ACTIVITY=\$(grep -m1 'Dynamic (W)' $W/v1/saif.rpt)
echo V1_ACTIVITY_NOU=\$(grep -m1 'Dynamic (W)' $W/v1_nou/saif.rpt)"
}
step_status() {
  local s pid rc t0 t1 alive
  for s in B1 B2 P1 P1F P2 P3 S0 C0 S1 S2 C1 V0 V1; do
    if [ ! -e "$LOGS/$s.start" ]; then printf '%-3s not started\n' "$s"; continue; fi
    rc="-"; alive=no
    pid=$(cat "$LOGS/$s.pid" 2>/dev/null); if kill -0 "$pid" 2>/dev/null; then alive=yes; fi
    if [ -e "$LOGS/$s.rc" ]; then rc=$(cat "$LOGS/$s.rc"); fi
    t0=$(cat "$LOGS/$s.start"); t1=$(cat "$LOGS/$s.end" 2>/dev/null || date +%s)
    printf '%-3s pid=%s alive=%s rc=%s elapsed=%ss log=%s\n' "$s" "$pid" "$alive" "$rc" "$((t1-t0))" "$LOGS/$s.log"
  done
}
step_wait() {
  local s="${1:-}"
  if [ -z "$s" ] || [ ! -e "$LOGS/$s.pid" ]; then die "wait <step>: no pid file for '$s'"; fi
  local pid; pid=$(cat "$LOGS/$s.pid")
  while kill -0 "$pid" 2>/dev/null; do sleep 20; done
  sleep 2
  local rc="?"; if [ -e "$LOGS/$s.rc" ]; then rc=$(cat "$LOGS/$s.rc"); fi
  say "STEP $s END rc=$rc elapsed=$(( $(cat "$LOGS/$s.end" 2>/dev/null || date +%s) - $(cat "$LOGS/$s.start") ))s log=$LOGS/$s.log"
  grep -E '^[A-Z0-9_]+=|^(variables|bits|total_tc|PASSED|\*\*\* )' "$LOGS/$s.log" | tail -25
  if [ "$rc" = 0 ]; then exit 0; else exit 1; fi
}
step_list() {
cat <<'EOF'
P0 preflight record (foreground)          B1 sim build (FST, tile trace)        B2 dhrystone-1500 copy
P1 fresh OOC elaboration                  P2 synthesis + checkpoint (Vivado)    P3 tile equivalence
P1F tile RTL from the sim's own FIRRTL (defense power points, PT_PARAMS=fir; replaces P1)
S0 smoke trace                            C0 converter check vs reference       S1 untraced reference run
window (R, A, N, MAXC)                    S2 traced run                         C1 conversion (2 SAIFs)
V0 naming check (smoke SAIF)              V1 activity power (2 SAIFs)           status | wait <step> | check
Order: P0 -> B1 -> (P1 -> P2) in parallel with (B2, S0, C0, S1, window, S2, C1); V0 after P2 and S0; V1 after P2 and C1.
EOF
}

cmd="${1:-}"; shift || true
rc=0
case "$cmd" in
  P0|B1|B2|P1|P1F|P2|P3|S0|C0|S1|S2|C1|V0|V1|window) "step_$cmd"; rc=$? ;;
  status) step_status; exit 0 ;;
  wait) step_wait "${1:-}" ;;
  list) step_list; exit 0 ;;
  check)
    DRYRUN=1
    for s in P0 B1 B2 P1 P1F P2 P3 S0 C0 S1 window S2 C1 V0 V1; do echo; "step_$s"; done
    echo; say "DRYRUN SUMMARY ok=$NOK pending=$NPEND FAIL=$NFAIL (nothing launched)"
    if [ "$NFAIL" -eq 0 ]; then exit 0; else exit 1; fi ;;
  *) sed -n 2,29p "$0"; exit 2 ;;
esac
if [ "$DRYRUN" = 1 ]; then
  say "DRYRUN SUMMARY ok=$NOK pending=$NPEND FAIL=$NFAIL (nothing launched)"
  if [ "$NFAIL" -eq 0 ]; then exit 0; else exit 1; fi
fi
exit "$rc"
