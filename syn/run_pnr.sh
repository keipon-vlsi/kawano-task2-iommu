#!/usr/bin/env bash
# Raw-OpenROAD P&R (place + repair) PPA for one config, via IIC-OSIC-TOOLS docker.
# This is the in-container path (OpenLane is not installed in this image; OpenROAD is
# what OpenLane drives anyway). It runs syn/openlane/pnr.tcl and writes results/<name>_pnr.txt.
#
# Prereq: the gate netlist syn/build/<name>_netlist.v exists
#         (produced by:  python3 syn/synth_osic.py <name>).
# Usage:  syn/run_pnr.sh [name] [period_ns] [max_fanout]
set -e
NAME="${1:-full}"
PERIOD="${2:-2.5}"
MAXFO="${3:-16}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="hpretl/iic-osic-tools:latest"
B="/foss/pdks/sky130A/libs.ref/sky130_fd_sc_hd"

docker run --rm -v "$ROOT":/foss/designs \
  -e NETLIST="/foss/designs/syn/build/${NAME}_netlist.v" \
  -e LIB="$B/lib/sky130_fd_sc_hd__tt_025C_1v80.lib" \
  -e TLEF="$B/techlef/sky130_fd_sc_hd__nom.tlef" \
  -e CLEF="$B/lef/sky130_fd_sc_hd.lef" \
  -e TOP="cfg_${NAME}" -e PERIOD_NS="$PERIOD" -e MAX_FANOUT="$MAXFO" \
  "$IMAGE" --skip bash -lc "openroad -no_init -exit /foss/designs/syn/openlane/pnr.tcl" \
  > "$ROOT/results/${NAME}_pnr.txt" 2>&1

# parse a compact PPA summary
python3 - "$NAME" "$PERIOD" "$ROOT" <<'PY'
import re, sys, json
name, period, root = sys.argv[1], float(sys.argv[2]), sys.argv[3]
log = open(f"{root}/results/{name}_pnr.txt").read()
ws = re.search(r"worst slack(?:\s+max)?\s+(-?\d+\.?\d*)", log)
slack = float(ws.group(1)) if ws else None
crit = (period - slack) if slack is not None else None
fmax = (1000.0/crit) if crit and crit > 0 else None
area = re.search(r"Design area\s+(\d+)\s+um\^2\s+(\d+)%", log)
pw = re.search(r"^Total\s+([\d.eE+-]+)\s+([\d.eE+-]+)\s+([\d.eE+-]+)\s+([\d.eE+-]+)", log, re.M)
out = {"config": name, "stage": "post-place+repair (no CTS/route)",
       "period_ns": period, "worst_slack_ns": slack,
       "critical_path_ns": crit, "fmax_mhz": fmax,
       "die_area_um2": int(area.group(1)) if area else None,
       "utilization_pct": int(area.group(2)) if area else None,
       "power_W": ({"internal": float(pw.group(1)), "switching": float(pw.group(2)),
                    "leakage": float(pw.group(3)), "total": float(pw.group(4))} if pw else {})}
json.dump(out, open(f"{root}/results/{name}_pnr.json", "w"), indent=2)
print(f"  Fmax ~{fmax:.1f} MHz (crit {crit:.2f} ns, slack {slack} ns) | "
      f"die {out['die_area_um2']} um^2 @ {out['utilization_pct']}% | power {out['power_W'].get('total')} W"
      if fmax else "  parse failed; see results/%s_pnr.txt" % name)
PY
