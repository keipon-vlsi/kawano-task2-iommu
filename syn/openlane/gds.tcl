# Magic: stream DEF -> GDS with real sky130 cell geometry.
drc off
gds read $env(CELLGDS)
lef read $env(TLEF)
def read $env(OUTDEF)
load $env(TOP)
gds write $env(OUTGDS)
puts "##GDS_WRITTEN $env(OUTGDS)"
exit
