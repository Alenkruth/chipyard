# saif_match_diag.tcl -- which synthesized nets can a SAIF from the RTL simulation reach? (power pilot V2 diagnosis)
#
# Purpose : answer, for one post-synthesis checkpoint and one SAIF,
#           Q1 how many SAIF names Vivado matched (read_saif -out_file lists the unmatched SAIF objects), and
#           Q2 per driver class (FD*, RAMB*, DSP*, LUT*, CARRY*, MUXF*, SRL*, other, ports) how many design nets exist,
#              written as name lists so an offline script can match them against the SAIF names.
#           Also dumps `help` of the switching-activity commands and a sample report_switching_activity, so the
#           per-net activity source can be read without guessing option names.
# Inputs  : env DIAG_DCP (checkpoint), DIAG_SAIF, DIAG_STRIP (read_saif -strip_path), DIAG_OUT (output dir, also cwd),
#           optional OOC_MAX_THREADS (default 8), optional DIAG_FILL (opt-in: a table from saif_fill_regs.py; right
#           after read_saif, saif_fill_regs.tcl gives renamed/replicated registers their RTL register's SAIF activity
#           and writes saif_fill.txt / saif_fill_miss.txt; unset = flow unchanged).
# Usage   : cd $DIAG_OUT && DIAG_DCP=... DIAG_SAIF=... DIAG_STRIP=... DIAG_OUT=... vivado -mode batch -nojournal -nolog -source saif_match_diag.tcl
# Output  : $DIAG_OUT/{read_saif.log,saif_unmatched.txt,nets_<class>.txt,nets_all.txt,counts.txt,help_*.txt,sample_activity.rpt}
#           and lines "DIAG_STEP <name> <seconds>" / "DIAG_COUNT <class> <n>" on stdout.
proc envv {n {d ""}} { if {[info exists ::env($n)]} { return $::env($n) } ; return $d }
set ::diag_dir [file dirname [file normalize [info script]]]
set out [envv DIAG_OUT]; set dcp [envv DIAG_DCP]; set saif [envv DIAG_SAIF]; set strip [envv DIAG_STRIP]
if {$out eq "" || $dcp eq "" || $saif eq "" || $strip eq ""} { puts "DIAG_ERROR need DIAG_OUT DIAG_DCP DIAG_SAIF DIAG_STRIP"; exit 2 }
file mkdir $out
set_param general.maxThreads [envv OOC_MAX_THREADS 8]
proc step {name body} { set t [clock milliseconds]; uplevel 1 $body; puts "DIAG_STEP $name [expr {([clock milliseconds]-$t)/1000.0}]" }
proc wlist {f l} { set fh [open $f w]; foreach x [lsort -unique $l] { puts $fh $x }; close $fh }

step open_checkpoint { open_checkpoint $dcp }
foreach c {report_switching_activity get_switching_activity set_switching_activity read_saif} {
  set fh [open [file join $out help_$c.txt] w]; catch {puts $fh [help $c]} m; puts $fh $m; close $fh
}
step read_saif { set rc [catch {read_saif -strip_path $strip -out_file [file join $out saif_unmatched.txt] $saif} msg]
                 set fh [open [file join $out read_saif.log] w]; puts $fh "rc=$rc"; puts $fh $msg; close $fh }
if {[envv DIAG_FILL] ne ""} {
  step saif_fill { source [file join $::diag_dir saif_fill_regs.tcl]; saif_fill_regs [envv DIAG_FILL] $out }
}

set counts {}
step classify {
  set all [get_nets -hierarchical -top_net_of_hierarchical_group -quiet]
  wlist [file join $out nets_all.txt] $all; lappend counts "all_top_nets [llength $all]"
  set seen {}
  foreach {cls pat} {FD FD* RAMB RAMB* DSP DSP* LUT LUT* CARRY CARRY* MUXF MUXF* SRL SRL*} {
    set cells [get_cells -hierarchical -quiet -filter "IS_PRIMITIVE && REF_NAME =~ $pat"]
    set pins [get_pins -quiet -of_objects $cells -filter {DIRECTION == OUT}]
    set nets [get_nets -quiet -top_net_of_hierarchical_group -of_objects $pins]
    wlist [file join $out nets_$cls.txt] $nets
    lappend counts "$cls cells=[llength $cells] out_nets=[llength [lsort -unique $nets]]"
  }
  set pn [get_nets -quiet -top_net_of_hierarchical_group -of_objects [get_ports]]
  wlist [file join $out nets_PORT.txt] $pn; lappend counts "PORT nets=[llength [lsort -unique $pn]]"
}
set fh [open [file join $out counts.txt] w]; foreach c $counts { puts $fh $c; puts "DIAG_COUNT $c" }; close $fh
step sample_activity {
  set ff [lrange [lsort [get_nets -quiet -top_net_of_hierarchical_group -of_objects [get_pins -quiet -of_objects [get_cells -hierarchical -quiet -filter {IS_PRIMITIVE && REF_NAME =~ FD*}] -filter {DIRECTION == OUT}]]] 0 39]
  catch {report_switching_activity -file [file join $out sample_activity.rpt] $ff} m; puts "DIAG_INFO sample_activity: $m"
}
# Optional DIAG_POWER=1: report_power first (activity propagation happens there), so the DIAG_ACTIVITY reports below show
# the activity sources report_power used; plus DIAG_PROBES (space-separated full-name patterns, matched with
# -filter {NAME =~ ...}) into probes.rpt.
if {[envv DIAG_POWER 0] eq "1"} {
  step report_power { report_power -hierarchical_depth 3 -file [file join $out power_saif.rpt] }
  set pn {}
  foreach p [envv DIAG_PROBES ""] { set n [get_nets -hierarchical -quiet -filter "NAME =~ $p"]; puts "DIAG_INFO probe $p: [llength $n] nets"; lappend pn {*}$n }
  if {[llength $pn]} { report_switching_activity -file [file join $out probes.rpt] [lsort -unique $pn] }
}
# Optional DIAG_ACTIVITY=1: report_switching_activity for every net of each class into act_<class>.rpt; each line ends
# in a source tag per value, (S) = from the SAIF (simulated), (D) = default, others = propagated/user. Count offline.
if {[envv DIAG_ACTIVITY 0] eq "1"} {
  foreach {cls pat} {FD FD* RAMB RAMB* DSP DSP* LUT LUT* CARRY CARRY* MUXF MUXF*} {
    step act_$cls {
      set nets [lsort -unique [get_nets -quiet -top_net_of_hierarchical_group -of_objects [get_pins -quiet -of_objects [get_cells -hierarchical -quiet -filter "IS_PRIMITIVE && REF_NAME =~ $pat"] -filter {DIRECTION == OUT}]]]
      catch {report_switching_activity -file [file join $out act_$cls.rpt] $nets} m; if {$m ne ""} { puts "DIAG_INFO act_$cls: $m" }
    }
  }
  step act_PORT { catch {report_switching_activity -file [file join $out act_PORT.rpt] [get_ports]} m }
}
puts "DIAG_DONE"
