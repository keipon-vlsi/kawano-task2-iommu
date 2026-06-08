# Staged OpenROAD P&R with per-stage PPA (place -> CTS -> global route).
# Env: NETLIST, LIB, TLEF, CLEF, TOP, PERIOD_NS, MAX_FANOUT, CLKBUF, CLKROOT,
#      DETAILED (1 = run detailed routing, slower), OUTDEF, OUTODB.
# Prints "##STAGE <name>" markers so the driver can parse PPA per stage.
read_lef  $::env(TLEF)
read_lef  $::env(CLEF)
read_liberty $::env(LIB)
read_verilog $::env(NETLIST)
link_design $::env(TOP)
create_clock -name clk -period $::env(PERIOD_NS) [get_ports clk]

proc stage_report {tag} {
  puts "##STAGE $tag"
  report_worst_slack -max
  report_tns
  report_design_area
  report_power -digits 6
}

# ---------- floorplan ----------
initialize_floorplan -utilization 35 -aspect_ratio 1.0 -core_space 5 -site unithd
make_tracks
place_pins -hor_layers met3 -ver_layers met2
set_wire_rc -signal -layer met3
set_wire_rc -clock  -layer met5

# ---------- global + detailed placement + repair (max-fanout/slew fix) ----------
global_placement -density 0.55
estimate_parasitics -placement
set_max_fanout     $::env(MAX_FANOUT) [current_design]
set_max_transition 0.75 [current_design]
repair_design
detailed_placement
estimate_parasitics -placement
stage_report PLACE

# ---------- clock tree synthesis ----------
clock_tree_synthesis -buf_list $::env(CLKBUF) -root_buf $::env(CLKROOT) -sink_clustering_enable
set_propagated_clock [all_clocks]
detailed_placement
estimate_parasitics -placement
stage_report CTS

# ---------- routing ----------
global_route -allow_congestion
estimate_parasitics -global_routing
stage_report GROUTE
if {$::env(DETAILED) == 1} {
  detailed_route -output_drc [file rootname $::env(OUTDEF)].drc.rpt
  estimate_parasitics -global_routing
  stage_report DROUTE
}

# ---------- outputs (DEF + OpenDB for GDS streaming) ----------
write_def $::env(OUTDEF)
write_db  $::env(OUTODB)
puts "##DONE"
