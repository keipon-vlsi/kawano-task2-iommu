#!/usr/bin/env python3
"""Constraints + sizing Fmax optimization sweep for cfg5 (OpenROAD, sky130_fd_sc_hd).
RTL/netlist unchanged -- only SDC + resizer knobs. Runs opt.tcl per (knob-set, period),
captures the full STA, and tabulates Fmax = 1/(period - worst_slack) + the critical
path's fanout/slew. Writes results/fmax_opt/<run>.log + a summary. Run: python3 syn/fmax_opt.py
"""
import json, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMAGE = "hpretl/iic-osic-tools:latest"
B = "/foss/pdks/sky130A/libs.ref/sky130_fd_sc_hd"
NET = "/foss/designs/cfg5_notag/results/netlist.v"
OUT = ROOT / "results/fmax_opt"; OUT.mkdir(parents=True, exist_ok=True)

BASE_ENV = dict(NET=NET, TOP="cfg5_top", SITE="unithd",
                LIB=f"{B}/lib/sky130_fd_sc_hd__tt_025C_1v80.lib",
                TLEF=f"{B}/techlef/sky130_fd_sc_hd__nom.tlef",
                CLEF=f"{B}/lef/sky130_fd_sc_hd.lef")

# run matrix: (label, env-overrides). First block = per-knob effect @2.5ns;
# second = period sweep with the full knob set.
RUNS = [
  ("k0_postplace",  dict(PERIOD=2.5, SDC=0, RDES=0, RTIM=0, MAXTRANS=0.75, MAXFO=16)),
  ("k1_repair_drc", dict(PERIOD=2.5, SDC=0, RDES=1, RTIM=0, MAXTRANS=0.75, MAXFO=16)),
  ("k2_repair_tim", dict(PERIOD=2.5, SDC=0, RDES=1, RTIM=1, MAXTRANS=0.75, MAXFO=16)),
  ("k3_sdc_tight",  dict(PERIOD=2.5, SDC=1, RDES=1, RTIM=1, MAXTRANS=0.5,  MAXFO=12,
                         SLEWM=10, CAPM=10)),
  ("p5_0",  dict(PERIOD=5.0, SDC=1, RDES=1, RTIM=1, MAXTRANS=0.5, MAXFO=12, SLEWM=10, CAPM=10)),
  ("p4_0",  dict(PERIOD=4.0, SDC=1, RDES=1, RTIM=1, MAXTRANS=0.5, MAXFO=12, SLEWM=10, CAPM=10)),
  ("p3_5",  dict(PERIOD=3.5, SDC=1, RDES=1, RTIM=1, MAXTRANS=0.5, MAXFO=12, SLEWM=10, CAPM=10)),
  ("p3_0",  dict(PERIOD=3.0, SDC=1, RDES=1, RTIM=1, MAXTRANS=0.5, MAXFO=12, SLEWM=10, CAPM=10)),
  ("p2_5",  dict(PERIOD=2.5, SDC=1, RDES=1, RTIM=1, MAXTRANS=0.5, MAXFO=12, SLEWM=10, CAPM=10)),
]


def run(label, ov):
    env = dict(BASE_ENV); env.update({k: str(v) for k, v in ov.items()})
    eargs = []
    for k, v in env.items():
        eargs += ["-e", f"{k}={v}"]
    cmd = ["docker", "run", "--rm", "-v", f"{ROOT}:/foss/designs", *eargs, IMAGE,
           "--skip", "bash", "-lc",
           "openroad -no_init -exit /foss/designs/syn/fmax_opt/opt.tcl 2>&1"]
    print(f"[{label}] period={ov['PERIOD']} SDC={ov.get('SDC')} RDES={ov.get('RDES')} "
          f"RTIM={ov.get('RTIM')} ...", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True)
    (OUT / f"{label}.log").write_text(r.stdout)
    return parse(label, ov, r.stdout)


def parse(label, ov, out):
    P = float(ov["PERIOD"])
    def slack(after):
        m = re.search(rf"##{after}\s*\nworst slack max\s+(-?[\d.]+)", out)
        return float(m.group(1)) if m else None
    pre, post = slack("PRE"), slack("POST")
    # worst path max fanout / slew (POST ##PATH block): take the largest fanout & slew
    pathblk = out[out.find("##PATH"):] if "##PATH" in out else ""
    fos = [int(x) for x in re.findall(r"^\s*(\d+)\s+[\d.]+\s+[\d.]+", pathblk, re.M)]
    slews = [float(x) for x in re.findall(r"\d+\s+([\d.]+)\s+[\d.]+\s+[\d.]+\s+[\d.]+\s+[v^]",
                                          pathblk)]
    area = re.search(r"Design area\s+([\d.]+)\s+um\^2\s+(\d+)%", out)
    fmax = (1000.0 / (P - post)) if (post is not None and (P - post) > 0) else None
    return {"run": label, "period_ns": P, "pre_slack_ns": pre, "post_slack_ns": post,
            "fmax_mhz": round(fmax, 1) if fmax else None,
            "path_max_fanout": max(fos) if fos else None,
            "path_max_slew_ns": max(slews) if slews else None,
            "area_um2": float(area.group(1)) if area else None,
            "knobs": {k: ov.get(k) for k in ("SDC", "RDES", "RTIM", "MAXTRANS", "MAXFO")}}


if __name__ == "__main__":
    sel = sys.argv[1:]
    res = [run(l, ov) for l, ov in RUNS if not sel or l in sel]
    (ROOT / "results/fmax_opt/summary.json").write_text(json.dumps(res, indent=2))
    print(f"\n{'run':<14}{'period':>7}{'pre_sl':>8}{'post_sl':>8}{'Fmax':>7}{'pathFO':>8}{'pathSlew':>9}")
    for r in res:
        print(f"{r['run']:<14}{r['period_ns']:>7}{str(r['pre_slack_ns']):>8}"
              f"{str(r['post_slack_ns']):>8}{str(r['fmax_mhz']):>7}"
              f"{str(r['path_max_fanout']):>8}{str(r['path_max_slew_ns']):>9}")
