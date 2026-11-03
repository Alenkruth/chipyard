# power_saif.tcl -- activity-based power on a post-synthesis checkpoint (activity-power pilot, plan 1.4).
# Purpose: open the OOC post-synthesis checkpoint written by ooc_synth.tcl (OOC_CHECKPOINT=1), report the
#   vectorless power of that netlist, apply a SAIF from fst2saif, report the activity-based power, and report
#   the switching activity of probe nets.  Each step prints "PWR_STEP <name> <seconds>".
# Inputs (environment, like ooc_synth.tcl; no hardcoded paths):
#   PWR_DCP          post_synth.dcp (required)
#   PWR_OUT          output directory for the reports and logs (required, created if missing)
#   PWR_SAIF         SAIF file to apply (optional; without it only the vectorless report is made)
#   PWR_STRIP        hierarchy prefix to strip from the SAIF paths (required with PWR_SAIF), the FST path of the
#                    tile with '/' separators as printed by fst2saif --list-scopes,
#                    e.g. TOP/TestDriver/testHarness/chiptop0/system/tile_prci_domain/element_reset_domain_boom_tile
#   PWR_PROBES       space-separated net names or patterns for report_switching_activity (optional)
#   PWR_FIND         space-separated patterns; every matching net name is written to PWR_OUT/probe_candidates.txt
#                    (step V0: looking up the post-synthesis names of the probe nets); optional
#   PWR_FILL         opt-in (with PWR_SAIF): table written by power/saif_fill_regs.py from the same SAIF; right after
#                    read_saif, power/saif_fill_regs.tcl gives every register whose Q net was not matched by name the
#                    SAIF activity of its RTL register (from the cell name); writes saif_fill.txt, saif_fill_miss.txt.
#                    Unset = flow unchanged.
#   OOC_MAX_THREADS  set_param general.maxThreads N (optional)
# Usage: PWR_DCP=<point>/post_synth.dcp PWR_OUT=$W/v1 PWR_SAIF=$W/s2.saif PWR_STRIP=<tile path> \
#          vivado -mode batch -nojournal -nolog -source tools/ooc-synth/synth/power_saif.tcl
# Outputs in PWR_OUT: vectorless.rpt, saif_read.log, saif.rpt, probes.rpt, probe_candidates.txt, match.txt (summary
#   rows 'Simulation Activity File', 'Design Nets Matched', 'Confidence Level' of both power reports).
# Option names of read_saif / report_switching_activity are an assumption for Vivado 2023.1 until `help` confirms
# them (pilot.sh step V0 writes the help text); a failing optional step is logged as PWR_FAIL and does not stop later steps.
set ::pwr_dir [file dirname [file normalize [info script]]]
proc pwr_env {name {default ""}} { if {[info exists ::env($name)] && $::env($name) ne ""} { return $::env($name) } else { return $default } }
proc pwr_step {name script} {
  set t [clock milliseconds]
  set rc [catch {uplevel #0 $script} msg]
  puts [format "PWR_STEP %s %.1f" $name [expr {([clock milliseconds] - $t) / 1000.0}]]
  if {$rc} { puts "PWR_FAIL $name: $msg" }
  return $rc
}
proc pwr_grep {rpt tag} {
  set out {}
  if {![file exists $rpt]} { return $out }
  set f [open $rpt r]
  while {[gets $f line] >= 0} {
    if {[string match "*Simulation Activity File*" $line] || [string match "*Design Nets Matched*" $line] || [string match "*Confidence Level*" $line]} { lappend out "$tag $line" }
  }
  close $f
  return $out
}

set dcp [pwr_env PWR_DCP]
set out [pwr_env PWR_OUT]
if {$dcp eq "" || ![file exists $dcp]} { puts "PWR_ERROR PWR_DCP missing or not found: $dcp"; exit 2 }
if {$out eq ""} { puts "PWR_ERROR PWR_OUT not set"; exit 2 }
file mkdir $out
set saif [pwr_env PWR_SAIF]
set strip [pwr_env PWR_STRIP]
if {$saif ne "" && ($strip eq "" || ![file exists $saif])} { puts "PWR_ERROR PWR_SAIF needs an existing file and PWR_STRIP"; exit 2 }
if {[pwr_env OOC_MAX_THREADS] ne ""} { set_param general.maxThreads [pwr_env OOC_MAX_THREADS] }

# 1. open the checkpoint (clock constraint and out-of-context mode come with it)
if {[pwr_step open_checkpoint {open_checkpoint $dcp}]} { exit 1 }

# 2. vectorless power of the same netlist
pwr_step report_power_vectorless {report_power -hierarchical_depth 8 -file [file join $out vectorless.rpt]}
set match [pwr_grep [file join $out vectorless.rpt] vectorless]

# 3. SAIF, then the activity-based power
if {$saif ne ""} {
  pwr_step read_saif {
    set rc [catch {read_saif -strip_path $strip -out_file [file join $out saif_unmatched.txt] $saif} msg]
    set lf [open [file join $out saif_read.log] w]; puts $lf "rc=$rc"; puts $lf $msg; close $lf
    if {$rc} { error "read_saif failed: $msg" }
  }
  if {[pwr_env PWR_FILL] ne ""} {
    pwr_step saif_fill { source [file join $::pwr_dir .. power saif_fill_regs.tcl]; saif_fill_regs [pwr_env PWR_FILL] $out }
  }
  pwr_step report_power_saif {report_power -hierarchical_depth 8 -file [file join $out saif.rpt]}
  set match [concat $match [pwr_grep [file join $out saif.rpt] saif]]
}
set mf [open [file join $out match.txt] w]; foreach l $match { puts $mf $l; puts "PWR_INFO $l" }; close $mf

# 4. probe nets
set probes [pwr_env PWR_PROBES]
if {$probes ne ""} {
  pwr_step report_switching_activity {
    set nets {}
    foreach p $probes { set n [get_nets -hierarchical -quiet -filter "NAME =~ $p"]; if {[llength $n] == 0} { puts "PWR_WARN probe not found: $p" } else { lappend nets {*}$n } }
    if {[llength $nets] == 0} { error "no probe net found" }
    if {[catch {report_switching_activity -list_objects $nets -file [file join $out probes.rpt]} msg]} {
      puts "PWR_WARN report_switching_activity -list_objects failed ($msg); trying positional objects"
      report_switching_activity $nets -file [file join $out probes.rpt]
    }
  }
}

# V0: net name lookup
set find [pwr_env PWR_FIND]
if {$find ne ""} {
  pwr_step find_nets {
    set pf [open [file join $out probe_candidates.txt] w]
    foreach p $find { puts $pf "# $p"; foreach n [get_nets -hierarchical -quiet $p] { puts $pf $n } }
    close $pf
  }
}
puts "PWR_DONE"
