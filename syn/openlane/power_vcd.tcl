# OpenSTA: default-activity vs VCD-annotated power on the gate netlist.
# Both reports come from the SAME netlist/timing context so the only difference
# is the switching activity source (default statistical toggle vs real VCD).
# Env: LIB, NETLIST, TOP, PERIOD_NS, VCD (cocotb dump), VCDSCOPE (instance in the VCD).
read_liberty $::env(LIB)
read_verilog $::env(NETLIST)
link_design  $::env(TOP)
create_clock -name clk -period $::env(PERIOD_NS) [get_ports clk]
set_propagated_clock [all_clocks]

puts "##POWER_DEFAULT"
report_power -digits 6
puts "##POWER_DEFAULT_END"

# Annotate with the real workload activity captured by the cocotb/Verilator run.
# Gate-net names differ from the RTL VCD after flatten, so only matched nets (mostly
# top-level ports / retained leaves) are annotated -- the rest keep default activity.
# This is an approximation; see syn/USAGE_flow.md.
set annotated 0
if {[file exists $::env(VCD)]} {
  if {![catch {read_power_activity -vcd $::env(VCD) -scope $::env(VCDSCOPE)} err]} {
    set annotated 1
  } else {
    puts "##VCD_ANNOTATE_FAILED $err"
  }
} else {
  puts "##VCD_MISSING $::env(VCD)"
}
puts "##POWER_ANNOTATED annotated=$annotated"
report_power -digits 6
puts "##POWER_ANNOTATED_END"
