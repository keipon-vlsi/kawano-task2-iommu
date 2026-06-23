# Floorplan + global_place + global_route for the ports area/congestion sweep.
# Env: NET TOP LIB TLEF CLEF SITE UTIL. Reports ##AREA (post-place die) and ##CONG
# (global-route congestion / overflow). Fixed utilization so die area reflects how much
# the router must spread cells to relieve congestion (the wiring cost that cell-area misses).
proc ev {k d} { return [expr {[info exists ::env($k)] ? $::env($k) : $d}] }
read_lef [ev TLEF ""] ; read_lef [ev CLEF ""]
read_liberty [ev LIB ""]
read_verilog [ev NET ""]
link_design [ev TOP ""]
initialize_floorplan -utilization [ev UTIL 45] -aspect_ratio 1.0 -core_space 5 -site [ev SITE unithd]
make_tracks
# place all I/O pins
place_pins -hor_layers met3 -ver_layers met2
set_wire_rc -signal -layer met2
global_placement -density 0.6
estimate_parasitics -placement
puts "##AREA"
report_design_area
# global route: prints total wirelength + congestion/overflow (the wiring cost that
# cell-area misses). report_wire_length gives routed length per metric.
puts "##CONG"
global_route -congestion_iterations 30 -allow_congestion -verbose
report_wire_length -net * -global_route 2>/dev/null
exit
