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

# Early-stop level for the fast RTL<->P&R tuning loop (flow.py --until):
#   place -> stop after global/detailed placement + repair_design (realistic Fmax)
#   cts   -> stop after clock-tree synthesis
#   route -> full global route + signoff + DEF/ODB (default; GDS streamed by run_pnr.sh)
# place/cts stops skip DEF/ODB writes (no GDS) to keep the inner loop fast.
set STOP_AFTER "route"
if {[info exists ::env(STOP_AFTER)]} { set STOP_AFTER $::env(STOP_AFTER) }

proc stage_report {tag} {
  puts "##STAGE $tag"
  report_worst_slack -max
  report_tns
  report_design_area
  report_power -digits 6
  puts "##PATH $tag"
  report_checks -path_delay max -group_path_count 1 -fields {fanout cap slew} -digits 4
}

# ---------- floorplan ----------
initialize_floorplan -utilization 35 -aspect_ratio 1.0 -core_space 5 -site $::env(SITE)
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
if {$STOP_AFTER eq "place"} { puts "##DONE (stop after place)"; exit }

# ---------- clock tree synthesis ----------
clock_tree_synthesis -buf_list $::env(CLKBUF) -root_buf $::env(CLKROOT) -sink_clustering_enable
set_propagated_clock [all_clocks]
detailed_placement
estimate_parasitics -placement
stage_report CTS
if {$STOP_AFTER eq "cts"} { puts "##DONE (stop after cts)"; exit }

# ---------- routing ----------
set_routing_layers -signal met1-met5 -clock met1-met5
global_route -allow_congestion
estimate_parasitics -global_routing
stage_report GROUTE

# ---------- signoff-style reports (markers; flow.py splits into signoff/*.rpt) ----------
puts "##SIGNOFF cellusage";   catch {report_cell_usage}
puts "##SIGNOFF hold";        catch {report_checks -path_delay min -group_path_count 5 -fields {slew cap} -digits 4}
puts "##SIGNOFF timing_worstN"; catch {report_checks -path_delay max -group_path_count 10 -digits 4}
puts "##SIGNOFF clock";       catch {report_clock_skew}
puts "##SIGNOFF wirelength";  catch {report_wire_length -net_count 1}
puts "##SIGNOFF END"

if {$::env(DETAILED) == 1} {
  detailed_route -output_drc [file rootname $::env(OUTDEF)].drc.rpt -verbose 0
  estimate_parasitics -global_routing
  stage_report DROUTE
}

# ---------- outputs (DEF + OpenDB for GDS streaming) ----------
write_def $::env(OUTDEF)
write_db  $::env(OUTODB)
puts "##DONE"
