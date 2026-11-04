# saif_fill_regs.tcl -- give renamed / replicated registers the SAIF activity of their RTL register (power pilot).
#
# Purpose : after read_saif, every flip-flop whose Q net was not matched by name (Vivado renamed it: X_reg_n_0_[3],
#           X_reg[15]_0[3], p_0_in[3], replicas *_rep__N, memory words Memory_reg[w][b]) gets the activity of the RTL
#           register named by its CELL, taken from the table that saif_fill_regs.py wrote from the same SAIF.
#           Rule (identical to rtl_name() in saif_fill_regs.py): strip a trailing "__<n>", a trailing "_rep" /
#           "_rep__<n>" and again "__<n>"; the cell must then read <base>_reg[_rep[__<n>]]<idx> with <idx> = zero or
#           more [<n>]; the RTL bit is <base><idx> (fallbacks <base>[0] <-> <base> for 1-bit buses).
#           A flip-flop is left alone when its Q net (top of the hierarchical group) carries a SAIF name itself
#           (read_saif matched it), when the rule gives no name, or when that name is not in the SAIF.
#           The values are set with set_switching_activity -signal_rate <Mtr/s> -static_probability <p> on the Q
#           net, one call per distinct value pair, so report_switching_activity shows them as user-set, not (S).
# Inputs  : proc saif_fill_regs <data.tcl from saif_fill_regs.py> <output dir>; a design must be open and read_saif done.
# Usage   : source saif_fill_regs.tcl; saif_fill_regs $fill $out     (power_saif.tcl: PWR_FILL, saif_match_diag.tcl: DIAG_FILL)
# Output  : <dir>/saif_fill.txt (one line per filled net: net TAB cell TAB SAIF name), <dir>/saif_fill_miss.txt (counts
#           of the cells left alone by reason and leaf pattern, digits -> N), lines "SAIF_FILL <key> <value>" on stdout.
proc saif_fill_rtl_name {c} {
  regsub {__[0-9]+$} $c {} n
  regsub {_rep(__[0-9]+)?$} $n {} n
  regsub {__[0-9]+$} $n {} n
  if {[regexp {^(.*)_reg(?:_rep(?:__[0-9]+)?)?((?:\[[0-9]+\])*)$} $n -> b i]} { return "$b$i" }
  return ""
}
proc saif_fill_lookup {n} {
  if {[info exists ::saif_act($n)]} { return $n }
  if {[string match {*\[0\]} $n]} {
    set b [string range $n 0 end-3]
    if {![string match {*\]} $b] && [info exists ::saif_act($b)]} { return $b }
  }
  if {![string match {*\]} $n]} { set k "${n}\[0\]"; if {[info exists ::saif_act($k)]} { return $k } }
  return ""
}
proc saif_fill_regs {datafile outdir} {
  set t [clock milliseconds]
  catch {array unset ::saif_act}
  source $datafile
  puts "SAIF_FILL table_names [array size ::saif_act] load_s [expr {([clock milliseconds] - $t) / 1000.0}]"
  array set st {cells 0 no_rule 0 not_in_saif 0 no_q 0 named 0 set 0}
  array set pat {}
  set groups [dict create]
  set lf [open [file join $outdir saif_fill.txt] w]
  foreach c [get_cells -hierarchical -quiet -filter {IS_PRIMITIVE && REF_NAME =~ FD*}] {
    incr st(cells)
    set rtl [saif_fill_rtl_name $c]
    if {$rtl eq ""} { incr st(no_rule); set p "no_rule [regsub -all {[0-9]+} [lindex [split $c /] end] N]"; if {[info exists pat($p)]} { incr pat($p) } else { set pat($p) 1 }; continue }
    set k [saif_fill_lookup $rtl]
    if {$k eq ""} { incr st(not_in_saif); set p "not_in_saif [regsub -all {[0-9]+} [lindex [split $c /] end] N]"; if {[info exists pat($p)]} { incr pat($p) } else { set pat($p) 1 }; continue }
    set q [get_nets -quiet -top_net_of_hierarchical_group -of_objects [get_pins -quiet -of_objects $c -filter {REF_PIN_NAME == Q}]]
    if {[llength $q] != 1} { incr st(no_q); continue }
    if {[info exists ::saif_act($q)]} { incr st(named); continue }
    dict lappend groups $::saif_act($k) $q
    puts $lf "$q\t$c\t$k"
    incr st(set)
  }
  close $lf
  set ng 0
  dict for {v nets} $groups { lassign $v r p; set_switching_activity -signal_rate $r -static_probability $p $nets; incr ng }
  set mf [open [file join $outdir saif_fill_miss.txt] w]
  set prs {}; foreach {p n} [array get pat] { lappend prs [list $p $n] }   ;# Tcl 8.5 (Vivado 2023.1): no lsort -stride
  foreach pr [lsort -index 1 -integer -decreasing $prs] { puts $mf "[lindex $pr 1]\t[lindex $pr 0]" }
  close $mf
  foreach k {cells named set no_rule not_in_saif no_q} { puts "SAIF_FILL $k $st($k)" }
  puts "SAIF_FILL value_groups $ng seconds [expr {([clock milliseconds] - $t) / 1000.0}]"
  array unset ::saif_act
  return $st(set)
}
