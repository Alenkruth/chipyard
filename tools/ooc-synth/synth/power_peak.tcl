# power_peak.tcl -- forced-activity ("peak") power on a post-synthesis checkpoint (defense scaling OOC plan, section 3.2, option PF-<T>).
# Purpose: open the OOC post-synthesis checkpoint written by ooc_synth.tcl (OOC_CHECKPOINT=1), force a high switching activity on
#   the sequential/memory sources, and run report_power.  Dynamic power of this report + "Device Static" = the peak figure of the plan.
#   Upper bound only: real code toggles far less; idle defense hardware is forced active too.  The area flow is not touched.
# Activity set (T = PEAK_T, percent of the clock; Vivado toggle_rate is 0-100 for data pins):
#   all primary-input defaults : toggle T, static probability 0.5     (set_switching_activity -default_toggle_rate/-default_static_probability)
#   resets                     : -deassert_resets (static probability 0 on set/reset/clear/preset nets)
#   register, lut_ram, dsp, bram_wr_enable outputs : toggle T, static probability 0.5   (set_switching_activity -type <t> -all)
#   bram_enable                : static probability 1.0 (enable always on), toggle 0 (the command requires -toggle_rate with -type; SP 1.0 is legal only at toggle 0)
#   LUT / carry / mux outputs are not set: report_power propagates the activity from these sources.
# Form of the -type command: Vivado 2023.1 help (set_switching_activity) lists -type {io_output io_bidir_enable register lut_ram lut dsp
#   bram_enable bram_wr_enable ...} with -all and no <objects>; each type is applied in its own call.  If a call fails (PWR_WARN), the
#   same activity is applied per net (register, lut_ram, dsp only): the output nets of FD*, RAM*/SRL*, DSP* primitives, so the run
#   still produces a report; the PWR_INFO line says which form was used.  The sample reports show what was applied.
# Inputs (environment; no hardcoded paths):
#   PEAK_DCP         post_synth.dcp (required)
#   PEAK_OUT         output directory (required, created if missing)
#   PEAK_T           toggle rate in percent [50]
#   OOC_MAX_THREADS  set_param general.maxThreads N (optional)
# Usage: PEAK_DCP=<point>/post_synth.dcp PEAK_OUT=<dir> PEAK_T=50 OOC_MAX_THREADS=8 \
#          vivado -mode batch -nojournal -nolog -source tools/ooc-synth/synth/power_peak.tcl      (run with cwd = a work dir, not the repo)
# Outputs in PEAK_OUT: power_peak<T>.rpt (report_power -hierarchical_depth 8), peak_applied.txt (what each set_switching_activity call did),
#   act_<class>.rpt (report_switching_activity of the first 40 output nets of FD*/RAMB*/DSP*/LUT-RAM cells, taken right after the set calls,
#   before report_power, so user-set values show as (U) and not propagated), act_defaults.rpt (default toggle/static probability), peak_summary.txt (Device Static, Dynamic,
#   Total, Confidence rows).  Lines printed: PWR_STEP <name> <seconds>, PWR_INFO, PWR_WARN, PWR_FAIL <step>: <msg>, PWR_DONE.
set ::pk_log {}
proc pk_env {name {default ""}} { if {[info exists ::env($name)] && $::env($name) ne ""} { return $::env($name) } else { return $default } }
proc pwr_step {name script} {
  set t [clock milliseconds]
  set rc [catch {uplevel #0 $script} msg]
  puts [format "PWR_STEP %s %.1f" $name [expr {([clock milliseconds] - $t) / 1000.0}]]
  if {$rc} { puts "PWR_FAIL $name: $msg" }
  return $rc
}
proc pk_note {line} { lappend ::pk_log $line; puts "PWR_INFO $line" }
# one set_switching_activity call; returns 0 on success, logs the exact command either way
proc pk_set {args} {
  set rc [catch {eval set_switching_activity $args} msg]
  pk_note "set_switching_activity $args rc=$rc [string map {\n { }} $msg]"
  if {$rc} { puts "PWR_WARN set_switching_activity $args failed: $msg" }
  return $rc
}
# output nets of the primitive cells whose REF_NAME matches the glob
proc pk_nets {pat} {
  set cells [get_cells -hierarchical -quiet -filter "IS_PRIMITIVE && REF_NAME =~ $pat"]
  if {[llength $cells] == 0} { return {} }
  set pins [get_pins -quiet -of_objects $cells -filter {DIRECTION == OUT}]
  return [lsort -unique [get_nets -quiet -top_net_of_hierarchical_group -of_objects $pins]]
}
proc pk_sample {out cls pats} {
  set nets {}
  foreach p $pats { lappend nets {*}[pk_nets $p] }
  set nets [lrange [lsort -unique $nets] 0 39]
  set f [file join $out act_$cls.rpt]
  if {[llength $nets] == 0} { set fh [open $f w]; puts $fh "no nets of class $cls in this netlist"; close $fh; pk_note "sample $cls: 0 nets"; return }
  if {[catch {report_switching_activity -file $f $nets} msg]} { puts "PWR_WARN report_switching_activity $cls: $msg" }
  pk_note "sample $cls: [llength $nets] nets -> $f"
}

set dcp [pk_env PEAK_DCP]
set out [pk_env PEAK_OUT]
set T   [pk_env PEAK_T 50]
if {$dcp eq "" || ![file exists $dcp]} { puts "PWR_ERROR PEAK_DCP missing or not found: $dcp"; exit 2 }
if {$out eq ""} { puts "PWR_ERROR PEAK_OUT not set"; exit 2 }
if {![string is double -strict $T] || $T < 0 || $T > 100} { puts "PWR_ERROR PEAK_T must be 0..100, got $T"; exit 2 }
file mkdir $out
if {[pk_env OOC_MAX_THREADS] ne ""} { set_param general.maxThreads [pk_env OOC_MAX_THREADS] }

# 1. open the checkpoint (clock constraint and out-of-context mode come with it)
if {[pwr_step open_checkpoint {open_checkpoint $dcp}]} { exit 1 }

# 2. forced activity
pwr_step set_defaults {
  pk_set -default_toggle_rate $T -default_static_probability 0.5
  pk_set -deassert_resets
}
set via {}
pwr_step set_types {
  # BRAM enable: held high (SP 1.0) and never toggling (toggle 0). Vivado 12-4370 requires T/200 <= SP <= 1 - T/200 when a toggle
  # rate is given, so 'toggle T with SP 1.0' is illegal for any T > 0 (PK0 on U 2026-11-09 15:53: bram_enable FAILED at T=50).
  foreach {ty tr sp} [list register $T 0.5 lut_ram $T 0.5 dsp $T 0.5 bram_wr_enable $T 0.5 bram_enable 0 1.0] {
    if {[pk_set -type $ty -all -toggle_rate $tr -static_probability $sp] == 0} { lappend via "$ty:type" } else { lappend via "$ty:FAILED" }
  }
}
# fallback: per-net form for a register/lut_ram/dsp type whose -type call failed (BRAM enable pins have no net-output fallback: they stay FAILED)
pwr_step set_fallback {
  foreach {ty pats} {register {FD*} lut_ram {RAM* SRL*} dsp {DSP*}} {
    if {[lsearch $via "$ty:FAILED"] < 0} { continue }
    set nets {}
    foreach p $pats { lappend nets {*}[pk_nets $p] }
    if {[llength $nets] == 0} { pk_note "fallback $ty: no nets"; continue }
    pk_set -toggle_rate $T -static_probability 0.5 $nets
    pk_note "fallback $ty: per-net form on [llength $nets] output nets of $pats"
  }
  foreach ty {bram_wr_enable bram_enable} { if {[lsearch $via "$ty:FAILED"] >= 0} { puts "PWR_WARN $ty not applied (no fallback): the report has default activity on BRAM enables" } }
}
pk_note "forms: $via  T=$T"
set fh [open [file join $out peak_applied.txt] w]; foreach l $::pk_log { puts $fh $l }; close $fh

# 3. what was applied (before report_power: user-set values; no propagation yet)
pwr_step report_applied {
  set fh [open [file join $out act_defaults.rpt] w]
  catch {puts $fh [report_switching_activity -default_toggle_rate -default_static_probability -return_string]} m
  close $fh
  pk_sample $out FD {FD*}; pk_sample $out RAMB {RAMB*}; pk_sample $out DSP {DSP*}; pk_sample $out LUTRAM {RAM* SRL*}
}

# 4. peak power
pwr_step report_power_peak {report_power -hierarchical_depth 8 -file [file join $out power_peak$T.rpt]}
set sf [open [file join $out peak_summary.txt] w]
if {[file exists [file join $out power_peak$T.rpt]]} {
  set f [open [file join $out power_peak$T.rpt] r]
  while {[gets $f line] >= 0} {
    if {[string match "*Device Static*" $line] || [string match "*Dynamic (W)*" $line] || [string match "*Total On-Chip*" $line] || [string match "*Confidence*" $line] || [string match "*Internal nodes*" $line] || [string match "*Design Nets Matched*" $line]} { puts $sf $line; puts "PWR_INFO $line" }
  }
  close $f
}
close $sf
puts "PWR_DONE"
