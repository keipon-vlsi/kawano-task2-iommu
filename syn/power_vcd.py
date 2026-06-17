#!/usr/bin/env python3
"""VCD-annotated power: gate-level sim (real toggle activity) -> OpenSTA report_power.

For each config: (1) run the gate-level netlist under the SAME cocotb test, dumping a
VCD; (2) OpenSTA reads the VCD (read_vcd) for real per-net activity and reports total +
per-module power. Far more accurate than a flat switching-activity assumption.
Outputs results/power_vcd.json. Run: python3 syn/power_vcd.py
"""
import json, re, shutil, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMAGE = "hpretl/iic-osic-tools:latest"
LIB = "/foss/pdks/sky130A/libs.ref/sky130_fd_sc_hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib"
VENV = str(ROOT / ".venv/bin/python")

# cfg, top, has_pwc, has_iotlb, has_pf, tag_ctx
CFGS = [("cfg1_nocache","cfg1_top",0,0,0,1),
        ("cfg2_pwc","cfg2_top",1,0,0,1),
        ("cfg3_iotlb","cfg3_top",1,1,0,1),
        ("cfg4_prefetch","cfg4_top",1,1,1,1),
        ("cfg5_notag","cfg5_top",1,1,1,0)]
ENV = dict(CO=8, NUM_WALKERS=1, BUFFER=1, HAS_PWC=1, HAS_IOTLB=1, PREFETCH=1,
           TAG_CONTEXT=1, N_REQS=64, MEM_LATENCY=40)


def cfg_env(cfg):
    e = dict(ENV)
    if cfg == "cfg1_nocache": e.update(CO=1, NUM_WALKERS=37, BUFFER=37, HAS_PWC=0, HAS_IOTLB=0, PREFETCH=0)
    if cfg == "cfg2_pwc":     e.update(CO=1, NUM_WALKERS=5, BUFFER=5, HAS_IOTLB=0, PREFETCH=0)
    if cfg == "cfg3_iotlb":   e.update(PREFETCH=0, BUFFER=5)
    if cfg == "cfg5_notag":   e.update(TAG_CONTEXT=0)
    return e


def modules(cfg, hp, hi, hf):
    m = {"mem_master": "u_core/u_mem"}
    if hi: m["IOTLB"] = "u_core/g_iotlb.u_iotlb"
    if hp:
        for k in ("vml2", "vml1", "gl2", "gl1"):
            m[f"PWC_{k}"] = f"u_core/g_pwc.u_pwc_{k}"
    if hf: m["prefetch_ctrl"] = "u_core/g_pf.u_pf"
    return m


def gl_sim(cfg, top):
    e = cfg_env(cfg)
    code = (f"import sys; sys.path.insert(0,'{ROOT}/tb_coco')\n"
            f"from runner_gl import run_gl\n"
            f"run_gl('{cfg}','{top}','{ROOT}/syn/build_nested/{cfg}/{cfg}_hier.v',"
            f"{e!r},'{ROOT}/{cfg}/results/gl_build','{ROOT}/{cfg}/results/gl_sim')\n")
    print(f"[{cfg}] gate-level sim ...", flush=True)
    subprocess.run([VENV, "-c", code], cwd=str(ROOT),
                   capture_output=True, text=True)
    src = ROOT / cfg / "results/gl_sim/dump.vcd"
    dst = ROOT / cfg / "results/gl.vcd"
    if src.exists():
        shutil.copy(src, dst); return True
    return False


def sta_power(cfg, top, mods):
    net = f"/foss/designs/syn/build_nested/{cfg}/{cfg}_hier.v"
    vcd = f"/foss/designs/{cfg}/results/gl.vcd"
    lines = [f'read_liberty {LIB}', f'read_verilog {net}', f'link_design {top}',
             'create_clock -name clk -period 2.5 [get_ports clk]',
             f'read_vcd -scope {top} {vcd}', 'puts GROUP', 'report_power -digits 8']
    for name, inst in mods.items():
        lines += [f'puts "INST {name}"',
                  f'report_power -instances [get_cells {inst}] -digits 8']
    lines.append('exit')
    tcl = "\n".join(lines)
    bash = f"cat > /tmp/pv.tcl <<'EOT'\n{tcl}\nEOT\nsta -no_init -exit /tmp/pv.tcl"
    print(f"[{cfg}] STA VCD power ...", flush=True)
    r = subprocess.run(["docker", "run", "--rm", "-v", f"{ROOT}:/foss/designs", IMAGE,
                        "--skip", "bash", "-lc", bash], capture_output=True, text=True)
    return parse(cfg, mods, r.stdout)


def parse(cfg, mods, out):
    res = {"cfg": cfg}
    num = r"([0-9][0-9.eE+-]*)"
    # NOTE: the docker shell echoes the tcl script to stdout, so we anchor on line
    # starts (^) over the whole output -- only the real group-table rows begin with
    # "Sequential"/"Combinational"/"Total" followed by four numbers.
    for grp, key in [("Sequential", "sequential_mW"), ("Combinational", "combinational_mW")]:
        m = re.search(rf"^{grp}\s+{num}\s+{num}\s+{num}\s+{num}", out, re.M)
        if m: res[key] = float(m.group(4)) * 1000
    mt = re.search(rf"^Total\s+{num}\s+{num}\s+{num}\s+{num}", out, re.M)
    if mt:
        res["dynamic_mW"] = (float(mt.group(1)) + float(mt.group(2))) * 1000
        res["leakage_uW"] = float(mt.group(3)) * 1e6
        res["total_mW"] = float(mt.group(4)) * 1000
    permod = {}
    for chunk in out.split("INST ")[1:]:       # each instance block
        name = chunk.split()[0]
        m = re.search(rf"{num}\s+{num}\s+{num}\s+{num}\s+u_core", chunk)  # data row
        if m: permod[name] = float(m.group(4)) * 1000
    # group the 4 PWCs; control = total - sum(submodules)
    pwc = sum(v for k, v in permod.items() if k.startswith("PWC_"))
    mod = {"IOTLB": permod.get("IOTLB", 0.0), "PWC(x4)": pwc,
           "prefetch_ctrl": permod.get("prefetch_ctrl", 0.0),
           "mem_master": permod.get("mem_master", 0.0)}
    sub = mod["IOTLB"] + mod["PWC(x4)"] + mod["prefetch_ctrl"] + mod["mem_master"]
    mod["Control"] = max(0.0, res.get("total_mW", 0.0) - sub)
    res["per_module_mW"] = mod
    return res


if __name__ == "__main__":
    sel = set(sys.argv[1:])
    out = []
    if (ROOT / "results/power_vcd.json").exists():
        out = [r for r in json.loads((ROOT / "results/power_vcd.json").read_text())]
    for cfg, top, hp, hi, hf, tc in CFGS:
        if sel and cfg.split("_")[0] not in sel and cfg not in sel:
            continue
        out = [r for r in out if r["cfg"] != cfg]   # refresh this cfg
        if not gl_sim(cfg, top):
            print(f"[{cfg}] VCD missing, skipping"); continue
        out.append(sta_power(cfg, top, modules(cfg, hp, hi, hf)))
    (ROOT / "results" / "power_vcd.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))
