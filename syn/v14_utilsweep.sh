#!/bin/bash
D=/foss/designs
HSB=$D/open_pdks/sky130/sky130A/libs.ref/sky130_fd_sc_hs
run() {
  local label=$1; shift
  docker run --rm -v /space/kawano-task2-iommu:$D \
    -e TOP=cfg5_top -e SITE=unit -e ACT=0.053 \
    -e NET=$D/cfg5_notag/results_hs/netlist.v \
    -e LIB=$HSB/lib/sky130_fd_sc_hs__tt_025C_1v80.lib \
    -e TLEF=$HSB/techlef/sky130_fd_sc_hs__nom.tlef \
    -e CLEF=$HSB/lef/sky130_fd_sc_hs.lef -e DRVCELL=sky130_fd_sc_hs__buf_2 \
    -e PERIOD=2.5 -e SDC=1 -e RDES=1 -e RTIM=1 \
    -e MAXTRANS=0.75 -e MAXFO=16 -e SLEWM=0 -e CAPM=0 -e SETUPM=0.15 "$@" \
    hpretl/iic-osic-tools:latest --skip bash -lc \
    "openroad -no_init -exit $D/syn/fmax_opt/opt.tcl 2>&1" \
    | awk '/##POST/{p=1} p' | grep -E "worst slack max" | head -1 \
    | awk -v l="$label" '{s=$4; f=1000/(2.5-s); printf "%-16s slack %s -> Fmax %.1f MHz\n", l, s, f}'
}
run "UTIL45" -e UTIL=45
run "UTIL55" -e UTIL=55
run "UTIL65" -e UTIL=65
