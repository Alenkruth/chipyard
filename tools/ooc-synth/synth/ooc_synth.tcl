# Out-of-context synthesis of one point. All inputs come from environment variables
# set by run_point.sh (no hardcoded paths):
#   OOC_POINT_DIR  point directory (holds rtl/, outputs go here)
#   OOC_TOP        top module name
#   OOC_FILELIST   file containing one source path per line (relative to rtl/)
#   XILINX_PART    part, OOC_CLOCK_NS nominal clock period (ns)
# synth_design directives copied verbatim from the FireSim build
# (sims/firesim/deploy/results-build/<run>/.../firesim.runs/synth_1/overall_fpga_top.tcl:
#  synth_design -top overall_fpga_top -part xcu250-figd2104-2L-e -no_lc -shreg_min_size 5
#  -fsm_extraction one_hot -resource_sharing auto -retiming); only -top/-mode differ.
set pt   $::env(OOC_POINT_DIR)
set top  $::env(OOC_TOP)
set part $::env(XILINX_PART)
set ns   $::env(OOC_CLOCK_NS)
set rtl  [file join $pt rtl]

create_project -in_memory -part $part
set fh [open $::env(OOC_FILELIST) r]
while {[gets $fh line] >= 0} {
  set line [string trim $line]
  if {$line eq "" || [string match "#*" $line] || [string match "//*" $line] || [string match "-*" $line]} continue
  if {[file pathtype $line] ne "absolute"} { set line [file join $rtl $line] }
  if {![file exists $line]} { error "source missing: $line" }
  read_verilog -sv $line
}
close $fh

# Nominal clock on the port named "clock" (Chisel default), if present.
set xdc [file join $pt ooc_clock.xdc]
set xf [open $xdc w]
puts $xf "if {\[llength \[get_ports -quiet clock\]\]} { create_clock -name ooc_clk -period $ns \[get_ports clock\] }"
close $xf
read_xdc -mode out_of_context $xdc

synth_design -top $top -part $part -mode out_of_context -no_lc -shreg_min_size 5 \
  -fsm_extraction one_hot -resource_sharing auto -retiming

report_utilization -hierarchical -hierarchical_depth [expr {[info exists ::env(OOC_UTIL_DEPTH)] ? $::env(OOC_UTIL_DEPTH) : 2}] -file [file join $pt util_hier.rpt]
puts "OOC_SYNTH_DONE"
