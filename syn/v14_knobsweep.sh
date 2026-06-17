#!/bin/bash
# v14: P&R knob sweep on the v13 hs netlist (RTL unchanged) to push ideal-clock Fmax.
# Relax slew/fanout (less over-buffering) + setup margin. Reports post-opt Fmax per knob set.
D=/foss/designs
HSB=$D/open_pdks/sky130/sky130A/libs.ref/sky130_fd_sc_hs
run() { # $1=label $2..=KEY=VAL env
  local label=$1; shift
  docker run --rm -v /space/kawano-task2-iommu:$D \
    -e TOP=cfg5_top -e SITE=unit -e ACT=0.053 \
    -e NET=$D/cfg5_notag/results_hs/netlist.v \
    -e LIB=$HSB/lib/sky130_fd_sc_hs__tt_025C_1v80.lib \
    -e TLEF=$HSB/techlef/sky130_fd_sc_hs__nom.tlef \
    -e CLEF=$HSB/lef/sky130_fd_sc_hs.lef -e DRVCELL=sky130_fd_sc_hs__buf_2 \
    -e PERIOD=2.5 -e SDC=1 -e RDES=1 -e RTIM=1 "$@" \
    hpretl/iic-osic-tools:latest --skip bash -lc \
    "openroad -no_init -exit $D/syn/fmax_opt/opt.tcl 2>&1" \
    | awk '/##POST/{p=1} p' | grep -E "worst slack max" | head -1 \
    | awk -v l="$label" '{s=$4; f=1000/(2.5-s); printf "%-28s slack %s -> Fmax %.1f MHz\n", l, s, f}'
}
run "MT0.75 FO16 slew0"  -e MAXTRANS=0.75 -e MAXFO=16 -e SLEWM=0  -e CAPM=0
run "MT1.0  FO20 slew0"  -e MAXTRANS=1.0  -e MAXFO=20 -e SLEWM=0  -e CAPM=0
run "MT0.6  FO12 setupM0.2" -e MAXTRANS=0.6 -e MAXFO=12 -e SLEWM=0 -e CAPM=0 -e SETUPM=0.2
