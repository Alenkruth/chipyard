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

# Opt-in OOC_MAX_THREADS=N: set_param general.maxThreads N (unset = Vivado default) (2026-11-01, thread budget)
if {[info exists ::env(OOC_MAX_THREADS)] && $::env(OOC_MAX_THREADS) ne ""} { set_param general.maxThreads $::env(OOC_MAX_THREADS) }
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

# Nominal clock on the port named "clock" (Chisel default; every OOC top has it).
# 2026-10-31: the earlier XDC wrapped create_clock in a Tcl 'if', which the XDC reader rejects (CRITICAL WARNING
# Designutils 20-1307), so NO run before 2026-10-31 23:00 had a clock (no retiming, no timing-driven optimization).
# OOC_LEGACY_NOCLOCK=1 writes that old XDC again, to reproduce those area numbers exactly.
set xdc [file join $pt ooc_clock.xdc]
set xf [open $xdc w]
if {[info exists ::env(OOC_LEGACY_NOCLOCK)] && $::env(OOC_LEGACY_NOCLOCK) eq "1"} {
  puts $xf "if {\[llength \[get_ports -quiet clock\]\]} { create_clock -name ooc_clk -period $ns \[get_ports clock\] }"
} else {
  puts $xf "create_clock -name ooc_clk -period $ns \[get_ports clock\]"
}
close $xf
read_xdc -mode out_of_context $xdc

# Opt-in diagnostics (2026-10-31, gaps 1/3 + power). With none of these set the commands below are exactly the
# area flow used for every earlier number.
#   OOC_FLATTEN=none|full|rebuilt  pass -flatten_hierarchy (unset = Vivado default 'rebuilt', the area flow)
#   OOC_PRIMS=1                    also write util_flat.rpt (flat report with the primitive/cell-type table)
#   OOC_POWER=1                    also write power_hier.rpt (vectorless report_power, hierarchical, after synthesis)
set flat {}
if {[info exists ::env(OOC_FLATTEN)] && $::env(OOC_FLATTEN) ne ""} { set flat [list -flatten_hierarchy $::env(OOC_FLATTEN)] }
synth_design -top $top -part $part -mode out_of_context -no_lc -shreg_min_size 5 \
  -fsm_extraction one_hot -resource_sharing auto -retiming {*}$flat

report_utilization -hierarchical -hierarchical_depth [expr {[info exists ::env(OOC_UTIL_DEPTH)] ? $::env(OOC_UTIL_DEPTH) : 2}] -file [file join $pt util_hier.rpt]
if {[info exists ::env(OOC_PRIMS)] && $::env(OOC_PRIMS) eq "1"} { report_utilization -file [file join $pt util_flat.rpt] }
if {[info exists ::env(OOC_POWER)] && $::env(OOC_POWER) eq "1"} {
  report_power -hierarchical_depth [expr {[info exists ::env(OOC_UTIL_DEPTH)] ? $::env(OOC_UTIL_DEPTH) : 2}] -file [file join $pt power_hier.rpt]
}
# Opt-in OOC_CHECKPOINT=1 (2026-11-02, activity-power pilot): write the post-synthesis checkpoint post_synth.dcp
if {[info exists ::env(OOC_CHECKPOINT)] && $::env(OOC_CHECKPOINT) eq "1"} {
  set t_ck [clock seconds]
  write_checkpoint -force [file join $pt post_synth.dcp]
  puts "OOC_CHECKPOINT_S [expr {[clock seconds] - $t_ck}]"
}
# Opt-in (2026-11-01, defenses timing/congestion estimate):
#   OOC_DESIGN_ANALYSIS=1  post-synthesis logic-level distribution, Rent complexity and timing summary
#   OOC_PLACE=1            OOC opt_design + place_design, then congestion, timing and utilization after placement
#                          (whole xcu250 device, no pblock/shell: indicative only, not the bitstream's placement)
if {[info exists ::env(OOC_DESIGN_ANALYSIS)] && $::env(OOC_DESIGN_ANALYSIS) eq "1"} {
  report_design_analysis -logic_level_distribution -complexity -file [file join $pt design_analysis_synth.rpt]
  report_timing_summary -max_paths 20 -file [file join $pt timing_synth.rpt]
}
if {[info exists ::env(OOC_PLACE)] && $::env(OOC_PLACE) eq "1"} {
  opt_design
  place_design
  report_design_analysis -congestion -complexity -logic_level_distribution -file [file join $pt design_analysis_placed.rpt]
  report_timing_summary -max_paths 20 -file [file join $pt timing_placed.rpt]
  report_utilization -file [file join $pt util_placed.rpt]
}
puts "OOC_SYNTH_DONE"
