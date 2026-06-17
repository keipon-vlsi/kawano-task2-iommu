# Constraints + sizing Fmax optimization for one config (OpenROAD, sky130_fd_sc_hd).
# RTL/netlist are NOT modified -- only SDC constraints, max_fanout/transition, and the
# resizer (repair_design = fix slew/cap/fanout DRC; repair_timing -setup = size/buffer
# the timing-critical paths). Floorplan+placement give realistic parasitics; CTS/route
# are skipped for the fast knob/period sweep (ideal clock).
#   env: NET TOP LIB TLEF CLEF SITE PERIOD MAXTRANS MAXFO SDC RDES RTIM SLEWM CAPM SETUPM
proc ev {k d} { return [expr {[info exists ::env($k)] ? $::env($k) : $d}] }
read_lef [ev TLEF ""] ; read_lef [ev CLEF ""]
read_liberty [ev LIB ""]
read_verilog [ev NET ""]
link_design [ev TOP ""]

set P [ev PERIOD 2.5]
create_clock -name clk -period $P [get_ports clk]
set_power_activity -global -activity [ev ACT 0.1]

# ---- data IO ports (exclude clk) ----
set DIN  [get_ports {rst_n pl_valid pl_sel pl_data req_valid req_vpn req_device_id \
          req_pasid req_is_write rsp_ready arready rvalid rdata rid rlast}]
set DOUT [get_ports {req_ready rsp_valid rsp_vpn rsp_spa arvalid araddr arid arlen \
          rready walks_o resp_o outstanding_o}]

# ---- SDC: driving cell, loads, IO delays (only affects IO paths, not reg2reg) ----
if {[ev SDC 0]} {
  set_driving_cell -lib_cell [ev DRVCELL sky130_fd_sc_hd__buf_2] $DIN
  set_load 0.02 $DOUT
  set_input_delay  [expr {0.3*$P}] -clock clk $DIN
  set_output_delay [expr {0.3*$P}] -clock clk $DOUT
}
# ---- design-rule timing constraints (these DO drive the resizer on reg2reg) ----
set_max_transition [ev MAXTRANS 0.75] [current_design]
set_max_fanout     [ev MAXFO 16]      [current_design]

# ---- floorplan + global placement (realistic parasitics) ----
initialize_floorplan -utilization [ev UTIL 35] -aspect_ratio 1.0 -core_space 5 -site [ev SITE unithd]
make_tracks
place_pins -hor_layers met3 -ver_layers met2
set_wire_rc -signal -layer met3
set_wire_rc -clock  -layer met5
global_placement -density 0.55
estimate_parasitics -placement

puts "##PRE"
report_worst_slack -max
puts "##PREPATH"
report_checks -path_delay max -group_path_count 1 -fields {fanout slew} -digits 4

# ---- resizer: DRC repair (slew/cap/fanout -> buffer high-fanout nets) ----
if {[ev RDES 0]} {
  repair_design -slew_margin [ev SLEWM 0] -cap_margin [ev CAPM 0]
  estimate_parasitics -placement
}
# ---- resizer: setup repair (size/buffer the critical paths -> raise Fmax) ----
if {[ev RTIM 0]} {
  repair_timing -setup -setup_margin [ev SETUPM 0] -max_buffer_percent 30
  estimate_parasitics -placement
}
detailed_placement
estimate_parasitics -placement

puts "##POST"
report_worst_slack -max
report_tns
report_design_area
report_power -digits 6
puts "##PATH"
report_checks -path_delay max -group_path_count 2 -fields {fanout slew cap} -digits 4
exit
