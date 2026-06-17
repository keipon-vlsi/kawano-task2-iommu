#!/bin/bash
D=/foss/designs
HSB=$D/open_pdks/sky130/sky130A/libs.ref/sky130_fd_sc_hs
run() {
  local lbl=$1 u=$2 sm=$3
  docker run --rm -v /space/kawano-task2-iommu:$D \
    -e TOP=cfg5_top -e SITE=unit -e ACT=0.053 \
    -e NET=$D/cfg5_notag/results_hs/netlist.v \
    -e LIB=$HSB/lib/sky130_fd_sc_hs__tt_025C_1v80.lib \
    -e TLEF=$HSB/techlef/sky130_fd_sc_hs__nom.tlef \
    -e CLEF=$HSB/lef/sky130_fd_sc_hs.lef -e DRVCELL=sky130_fd_sc_hs__buf_2 \
    -e PERIOD=2.5 -e SDC=1 -e RDES=1 -e RTIM=1 \
    -e MAXTRANS=0.75 -e MAXFO=16 -e SLEWM=0 -e CAPM=0 -e SETUPM=$sm -e UTIL=$u \
    hpretl/iic-osic-tools:latest --skip bash -lc \
    "openroad -no_init -exit $D/syn/fmax_opt/opt.tcl 2>&1" \
    | awk '/##POST/{p=1} p' | grep -E "worst slack max|Design area" | head -2 \
    | awk -v l="$lbl" 'NR==1{s=$4;f=1000/(2.5-s)} /Design area/{a=$3} END{printf "%-22s slack %s -> Fmax %.1f MHz  area %s\n",l,s,f,a}'
}
run "U65 sm0.25" 65 0.25
run "U62 sm0.2"  62 0.2
run "U65 sm0.35" 65 0.35
