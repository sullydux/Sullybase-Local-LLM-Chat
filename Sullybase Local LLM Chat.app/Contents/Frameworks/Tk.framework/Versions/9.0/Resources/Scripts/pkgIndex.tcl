if {![package vsatisfies [package provide Tcl] 9.0]} return
    package ifneeded tk 9.0.3 [list load [file normalize [file join $dir .. .. Tk]]]
package ifneeded Tk 9.0.3 [list package require -exact tk 9.0.3]
