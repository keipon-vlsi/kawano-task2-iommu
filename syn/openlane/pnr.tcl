# Raw OpenROAD place + repair PPA (the steps OpenLane automates).
# Inputs (env): NETLIST, LIB, TLEF, CLEF, TOP, PERIOD_NS, MAX_FANOUT.
# Produces real post-placement PPA after buffer insertion / sizing (max-fanout fix).
read_lef  $::env(TLEF)
read_lef  $::env(CLEF)
read_liberty $::env(LIB)
read_verilog $::env(NETLIST)
link_design $::env(TOP)
create_clock -name clk -period $::env(PERIOD_NS) [get_ports clk]

# floorplan + routing tracks + IO
initialize_floorplan -utilization 35 -aspect_ratio 1.0 -core_space 5 -site unithd
make_tracks
place_pins -hor_layers met3 -ver_layers met2

# estimated-parasitics placement flow
set_wire_rc -signal -layer met3
set_wire_rc -clock  -layer met5
global_placement -density 0.55
estimate_parasitics -placement

# >>> the performance knobs: limit fanout / slew -> buffer trees + gate sizing <<<
set_max_fanout     $::env(MAX_FANOUT) [current_design]
set_max_transition 0.75 [current_design]
repair_design
detailed_placement
estimate_parasitics -placement

puts "=== POST-PLACE + REPAIR PPA (TOP=$::env(TOP), period=$::env(PERIOD_NS) ns, max_fanout=$::env(MAX_FANOUT)) ==="
report_worst_slack -max
report_tns
puts "--- remaining max_fanout/slew/cap violations (should be ~0) ---"
report_check_types -max_fanout -max_slew -max_capacitance -violators
puts "--- critical path ---"
report_checks -path_delay max -group_path_count 1 -digits 4
puts "--- power ---"
report_power -digits 6
puts "--- area ---"
report_design_area
