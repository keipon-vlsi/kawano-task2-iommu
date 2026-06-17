#!/usr/bin/env python3
"""Apply the corrected constraints+sizing flow (syn/fmax_opt/opt.tcl: SDC + repair_design
+ repair_timing) to ALL 5 configs at the 2.5 ns target, and tabulate post-optimization
Fmax / area / power. RTL & synthesized netlists are unchanged. Run: python3 syn/fmax_opt_all.py
Writes results/fmax_opt/postopt.json.
"""
import json, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMAGE = "hpretl/iic-osic-tools:latest"
B = "/foss/pdks/sky130A/libs.ref/sky130_fd_sc_hd"
OUT = ROOT / "results/fmax_opt"; OUT.mkdir(parents=True, exist_ok=True)

# cfg, top, calibrated switching activity (from the VCD calibration)
CFGS = [("cfg1_nocache", "cfg1_top", 0.18), ("cfg2_pwc", "cfg2_top", 0.16),
        ("cfg3_iotlb", "cfg3_top", 0.165), ("cfg4_prefetch", "cfg4_top", 0.045),
        ("cfg5_notag", "cfg5_top", 0.053)]
# full corrected knob set (SDC + tight slew/fanout + both resizer passes)
KNOBS = dict(PERIOD=2.5, SDC=1, RDES=1, RTIM=1, MAXTRANS=0.5, MAXFO=12, SLEWM=10, CAPM=10)


def run(cfg, top, act):
    env = dict(TOP=top, SITE="unithd", ACT=act,
               NET=f"/foss/designs/{cfg}/results/netlist.v",
               LIB=f"{B}/lib/sky130_fd_sc_hd__tt_025C_1v80.lib",
               TLEF=f"{B}/techlef/sky130_fd_sc_hd__nom.tlef",
               CLEF=f"{B}/lef/sky130_fd_sc_hd.lef", **KNOBS)
    eargs = []
    for k, v in env.items():
        eargs += ["-e", f"{k}={v}"]
    print(f"[{cfg}] constrained P&R + resize ...", flush=True)
    r = subprocess.run(["docker", "run", "--rm", "-v", f"{ROOT}:/foss/designs", *eargs,
                        IMAGE, "--skip", "bash", "-lc",
                        "openroad -no_init -exit /foss/designs/syn/fmax_opt/opt.tcl 2>&1"],
                       capture_output=True, text=True)
    (OUT / f"postopt_{cfg}.log").write_text(r.stdout)
    return parse(cfg, r.stdout)


def parse(cfg, out):
    post = out[out.find("##POST"):]
    P = 2.5
    ms = re.search(r"##POST\s*\nworst slack max\s+(-?[\d.]+)", out)
    sl = float(ms.group(1)) if ms else None
    fmax = (1000.0 / (P - sl)) if (sl is not None and (P - sl) > 0) else None
    ar = re.search(r"Design area\s+([\d.]+)\s+um\^2\s+(\d+)%", post)
    n = r"([0-9][0-9.eE+-]*)"
    seq = re.search(rf"^Sequential\s+{n}\s+{n}\s+{n}\s+{n}", post, re.M)
    comb = re.search(rf"^Combinational\s+{n}\s+{n}\s+{n}\s+{n}", post, re.M)
    tot = re.search(rf"^Total\s+{n}\s+{n}\s+{n}\s+{n}", post, re.M)
    return {"cfg": cfg, "post_slack_ns": sl, "fmax_mhz": round(fmax, 1) if fmax else None,
            "area_um2": float(ar.group(1)) if ar else None,
            "util_pct": int(ar.group(2)) if ar else None,
            "seq_mW": float(seq.group(4)) * 1000 if seq else None,
            "comb_mW": float(comb.group(4)) * 1000 if comb else None,
            "power_mW": float(tot.group(4)) * 1000 if tot else None}


if __name__ == "__main__":
    sel = sys.argv[1:]
    res = [run(c, t, a) for c, t, a in CFGS if not sel or c in sel or c.split("_")[0] in sel]
    if sel:  # merge with existing
        old = json.loads((OUT / "postopt.json").read_text()) if (OUT / "postopt.json").exists() else []
        keep = [r for r in old if r["cfg"] not in {x["cfg"] for x in res}]
        res = keep + res
    res.sort(key=lambda r: r["cfg"])
    (OUT / "postopt.json").write_text(json.dumps(res, indent=2))
    print(f"\n{'cfg':<15}{'Fmax':>7}{'area_um2':>11}{'power_mW':>9}{'seq':>8}{'comb':>7}")
    for r in res:
        print(f"{r['cfg']:<15}{str(r['fmax_mhz']):>7}{str(round(r['area_um2']) if r['area_um2'] else None):>11}"
              f"{str(round(r['power_mW'],1) if r['power_mW'] else None):>9}"
              f"{str(round(r['seq_mW'],1) if r['seq_mW'] else None):>8}"
              f"{str(round(r['comb_mW'],1) if r['comb_mW'] else None):>7}")
