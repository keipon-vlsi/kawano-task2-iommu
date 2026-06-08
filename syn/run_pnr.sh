#!/usr/bin/env bash
# Staged OpenROAD P&R (place -> CTS -> route) + magic GDS, via IIC-OSIC-TOOLS docker.
# Per-stage PPA (Fmax/area/power) -> results/<name>_pnr.json ; GDS -> results/<name>.gds.
#
# Prereq:  python3 syn/synth_osic.py <name>   (makes syn/build/<name>_netlist.v)
# Usage:   syn/run_pnr.sh [name] [period_ns] [max_fanout]
#   env:   STD_VARIANT=hd|hs|...   DETAILED=0|1   (1 = run detailed routing, slow)
set -e
NAME="${1:-full}"; PERIOD="${2:-2.5}"; MAXFO="${3:-16}"
VARIANT="${STD_VARIANT:-hd}"; DETAILED="${DETAILED:-0}"
# STOP_AFTER=place|cts|route (default route): early stop for fast tuning loops.
# WRITE_GDS=0|1 (default 1): skip the magic GDS stream when only timing is wanted.
STOP_AFTER="${STOP_AFTER:-route}"; WRITE_GDS="${WRITE_GDS:-1}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="hpretl/iic-osic-tools:latest"
# PDK_REF: image PDK (sc_hd/hvl only) by default; set /foss/designs/pdk_full after
# the open_pdks build to use hs/hdll/ms/ls.
PDK_REF="${PDK_REF:-/foss/pdks}"
B="${PDK_REF}/sky130A/libs.ref/sky130_fd_sc_${VARIANT}"
LIB="$B/lib/sky130_fd_sc_${VARIANT}__tt_025C_1v80.lib"
TLEF="$B/techlef/sky130_fd_sc_${VARIANT}__nom.tlef"
CLEF="$B/lef/sky130_fd_sc_${VARIANT}.lef"
MAGICRC="/foss/pdks/sky130A/libs.tech/magic/sky130A.magicrc"
D=/foss/designs
CELLGDS="$B/gds/sky130_fd_sc_${VARIANT}.gds"
# placement row site: hd/hdll use 'unithd', the other variants use 'unit'
case "$VARIANT" in hd|hdll) SITE=unithd;; *) SITE=unit;; esac
ENVS=(-e NETLIST=$D/syn/build/${NAME}_netlist.v -e LIB=$LIB -e TLEF=$TLEF -e CLEF=$CLEF
      -e CELLGDS=$CELLGDS -e TOP=cfg_${NAME} -e PERIOD_NS=$PERIOD -e MAX_FANOUT=$MAXFO
      -e CLKBUF=sky130_fd_sc_${VARIANT}__clkbuf_4 -e CLKROOT=sky130_fd_sc_${VARIANT}__clkbuf_16
      -e DETAILED=$DETAILED -e STOP_AFTER=$STOP_AFTER -e SITE=$SITE
      -e OUTDEF=$D/syn/build/${NAME}.def -e OUTODB=$D/syn/build/${NAME}.odb
      -e OUTGDS=$D/results/${NAME}.gds)

# Stream GDS only when routing completed AND GDS is wanted (heavy: 16 MB cell read).
if [ "$WRITE_GDS" = "1" ] && [ "$STOP_AFTER" = "route" ]; then
  MAGIC_CMD="magic -dnull -noconsole -rcfile $MAGICRC $D/syn/openlane/gds.tcl || echo '##GDS_FAILED'"
else
  MAGIC_CMD="echo '##GDS_SKIPPED (WRITE_GDS=$WRITE_GDS STOP_AFTER=$STOP_AFTER)'"
fi

docker run --rm -v "$ROOT":/foss/designs "${ENVS[@]}" "$IMAGE" --skip bash -lc \
  "openroad -no_init -exit $D/syn/openlane/pnr.tcl; $MAGIC_CMD" \
  > "$ROOT/results/${NAME}_pnr.txt" 2>&1 || true

python3 - "$NAME" "$PERIOD" "$ROOT" "$VARIANT" <<'PY'
import re, sys, json
name, period, root, variant = sys.argv[1], float(sys.argv[2]), sys.argv[3], sys.argv[4]
log = open(f"{root}/results/{name}_pnr.txt").read()
def block(tag, nxt):
    m = re.search(rf"##STAGE {tag}(.*?)(##STAGE {nxt}|##DONE|##GDS|\Z)", log, re.S)
    return m.group(1) if m else ""
def metrics(b):
    ws = re.search(r"worst slack(?:\s+max)?\s+(-?\d+\.?\d*)", b)
    s = float(ws.group(1)) if ws else None
    crit = (period - s) if s is not None else None
    ar = re.search(r"Design area\s+(\d+)\s+um\^2\s+(\d+)%", b)
    pw = re.search(r"^Total\s+([\d.eE+-]+)\s+([\d.eE+-]+)\s+([\d.eE+-]+)\s+([\d.eE+-]+)", b, re.M)
    return {"worst_slack_ns": s, "critical_path_ns": crit,
            "fmax_mhz": (1000.0/crit) if crit and crit > 0 else None,
            "die_area_um2": int(ar.group(1)) if ar else None,
            "utilization_pct": int(ar.group(2)) if ar else None,
            "power_W_total": float(pw.group(4)) if pw else None}
order = ["PLACE", "CTS", "GROUTE", "DROUTE"]
stages = {}
for i, t in enumerate(order):
    nxt = order[i+1] if i+1 < len(order) else "ZZZ"
    b = block(t, nxt)
    if b.strip():
        stages[t] = metrics(b)
gds = "##GDS_WRITTEN" in log
out = {"config": name, "variant": variant, "period_ns": period, "stages": stages,
       "gds": f"results/{name}.gds" if gds else None}
json.dump(out, open(f"{root}/results/{name}_pnr.json", "w"), indent=2)
for t, m in stages.items():
    fm = f"{m['fmax_mhz']:.1f}MHz" if m['fmax_mhz'] else "n/a"
    print(f"  {t:7} Fmax {fm:>9}  slack {m['worst_slack_ns']}  die {m['die_area_um2']}um^2  P {m['power_W_total']}W")
print(f"  GDS: {'results/%s.gds'%name if gds else 'NOT written (see results/%s_pnr.txt)'%name}")
PY

# combined RTL/synth/P&R PPA summary (keeps the per-stage JSONs intact).
# flow.py drives this itself (SKIP_PPA=1) so the shared history gets exactly one row.
if [ "${SKIP_PPA:-0}" != "1" ]; then
  python3 "$ROOT/syn/ppa_compare.py" "$NAME" >/dev/null 2>&1 && \
    echo "  combined PPA -> results/ppa_compare.md / ${NAME}_ppa.json" || true
fi
