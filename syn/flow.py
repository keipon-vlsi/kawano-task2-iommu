#!/usr/bin/env python3
"""One-command RTL -> synthesis -> P&R -> PPA compare -> GDS for one (config, library).

Thin orchestrator over the existing scripts (no reimplementation):
    syn/synth_osic.py  (yosys + OpenSTA)  -> synth area/Fmax/power
    syn/run_pnr.sh     (OpenROAD staged P&R + magic GDS) -> per-stage PPA + GDS
    syn/ppa_compare.py (cross-stage PPA table + shared history)

flow.py runs those in order, collects every artifact into a per-run directory
results/<cfg>_<lib>/, and adds:
  (1) P&R area/power *breakdown* (cell-type + internal/switching/leakage by
      Sequential/Combinational/Clock/Macro group) into pnr.json,
  (2) default vs VCD-annotated power (power_default.json / power_annotated.json),
  (3) signoff reports (drc/hold/timing_worstN/clock/wirelength/congestion),
  (4) an aggregate report.md(+.html), provenance.json and layout.png.

Usage:  python3 syn/flow.py --config full --lib hd --period 2.5
Idempotent; a stage failure is logged and does not delete prior artifacts.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SYN = ROOT / "syn"
RESULTS = ROOT / "results"
BUILD = SYN / "build"
TB = ROOT / "tb_coco"
IMAGE = "hpretl/iic-osic-tools:latest"
DROOT = "/foss/designs"                       # repo mount point inside the container
VENV_PY = ROOT / ".venv" / "bin" / "python3"  # cocotb/Verilator venv

sys.path.insert(0, str(SYN))
from synth import CONFIGS, RTL_FILES, clog2   # noqa: E402  (params + provenance)

# open_pdks build carries every sc library variant (hd/hs/hdll/ms/ls/...); the image
# PDK only ships hd/hvl. Default to the full build so any --lib works.
DEFAULT_PDK_REF = "/foss/designs/open_pdks/sky130"


# --------------------------------------------------------------------------- utils
def sh(cmd, env=None, log=None, cwd=None):
    """Run a command, tee combined output to `log`, return (rc, text)."""
    e = dict(os.environ)
    if env:
        e.update({k: str(v) for k, v in env.items()})
    r = subprocess.run(cmd, capture_output=True, text=True, env=e,
                       cwd=str(cwd) if cwd else None)
    out = r.stdout + ("\n===STDERR===\n" + r.stderr if r.stderr else "")
    if log:
        Path(log).write_text(out)
    return r.returncode, out


def docker_run(envs, bash_cmd):
    """Run a bash command inside the IIC-OSIC-TOOLS container with the repo mounted."""
    ev = []
    for k, v in envs.items():
        ev += ["-e", f"{k}={v}"]
    cmd = ["docker", "run", "--rm", "-v", f"{ROOT}:/foss/designs", *ev,
           IMAGE, "--skip", "bash", "-lc", bash_cmd]
    return subprocess.run(cmd, capture_output=True, text=True)


def copy(src, dst):
    src, dst = Path(src), Path(dst)
    if src.exists():
        shutil.copy2(src, dst)
        return True
    return False


def parse_power_table(text):
    """Parse a `report_power` block into {group: {internal,switching,leakage,total}}.
    Groups: Sequential / Combinational / Clock / Macro / Pad / Total (whichever appear).
    Returns {} if no Total row is found."""
    groups = {}
    for ln in text.splitlines():
        m = re.match(r"\s*(Sequential|Combinational|Clock|Macro|Pad|Total)\s+"
                     r"([0-9.eE+-]+)\s+([0-9.eE+-]+)\s+([0-9.eE+-]+)\s+([0-9.eE+-]+)", ln)
        if m:
            groups[m.group(1).lower()] = {
                "internal_W": float(m.group(2)), "switching_W": float(m.group(3)),
                "leakage_W": float(m.group(4)), "total_W": float(m.group(5))}
    if "total" not in groups:
        return {}
    tot = groups["total"]
    return {"by_category": {k: tot[k] for k in ("internal_W", "switching_W",
                                                "leakage_W", "total_W")},
            "by_group": {k: v for k, v in groups.items() if k != "total"},
            "total_W": tot["total_W"]}


# --------------------------------------------------------------------------- stages
def stage_synth(cfg, lib, period, pdk_ref, corner, rundir):
    """yosys + OpenSTA via syn/synth_osic.py; collect synth.* into the run dir."""
    env = {"STD_VARIANT": lib, "STD_CORNER": corner, "PDK_REF": pdk_ref,
           "PERIOD_NS": period}
    rc, out = sh([sys.executable, str(SYN / "synth_osic.py"), cfg], env=env,
                 log=rundir / "synth_stdout.txt")
    ok = copy(RESULTS / f"{cfg}.json", rundir / "synth.json")
    copy(RESULTS / f"{cfg}_sta.txt", rundir / "synth_sta.txt")
    copy(RESULTS / f"{cfg}_area.txt", rundir / "synth_area.txt")
    copy(RESULTS / f"{cfg}_area_flat.txt", rundir / "synth_area_flat.txt")
    syn = json.loads((rundir / "synth.json").read_text()) if ok else {}
    fmax = syn.get("fmax_mhz")
    return (ok and fmax is not None), syn, out


def stage_pnr(cfg, lib, period, pdk_ref, maxfo, detailed, rundir):
    """OpenROAD staged P&R + magic GDS via syn/run_pnr.sh; collect pnr.* + GDS/DEF/ODB."""
    env = {"STD_VARIANT": lib, "PDK_REF": pdk_ref, "DETAILED": "1" if detailed else "0",
           "SKIP_PPA": "1"}                       # flow.py owns the shared history row
    rc, out = sh(["bash", str(SYN / "run_pnr.sh"), cfg, period, str(maxfo)], env=env,
                 log=rundir / "pnr_stdout.txt")
    copy(RESULTS / f"{cfg}_pnr.txt", rundir / "pnr.txt")
    pnr = {}
    if (RESULTS / f"{cfg}_pnr.json").exists():
        pnr = json.loads((RESULTS / f"{cfg}_pnr.json").read_text())
    copy(RESULTS / f"{cfg}.gds", rundir / f"{cfg}_{lib}.gds")
    copy(BUILD / f"{cfg}.def", rundir / f"{cfg}_{lib}.def")
    copy(BUILD / f"{cfg}.odb", rundir / f"{cfg}_{lib}.odb")
    gds_ok = (rundir / f"{cfg}_{lib}.gds").exists()
    return gds_ok, pnr, out


def enrich_pnr(rundir, pnr):
    """Addition 1: add per-stage area/power *breakdown* parsed from the raw P&R log."""
    raw = (rundir / "pnr.txt").read_text() if (rundir / "pnr.txt").exists() else ""

    def block(tag, nxt):
        m = re.search(rf"##STAGE {tag}(.*?)(##STAGE {nxt}|##SIGNOFF|##DONE|\Z)", raw, re.S)
        return m.group(1) if m else ""

    order = ["PLACE", "CTS", "GROUTE", "DROUTE"]
    for i, t in enumerate(order):
        nxt = order[i + 1] if i + 1 < len(order) else "ZZZ"
        b = block(t, nxt)
        if b.strip() and t in pnr.get("stages", {}):
            pw = parse_power_table(b)
            if pw:
                pnr["stages"][t]["power_breakdown"] = pw
    # cell-type / hierarchical area usage (from the ##SIGNOFF cellusage marker)
    cu = re.search(r"##SIGNOFF cellusage(.*?)##SIGNOFF", raw, re.S)
    if cu:
        pnr["cell_usage_raw"] = cu.group(1).strip()
    (rundir / "pnr.json").write_text(json.dumps(pnr, indent=2))
    return pnr


def split_signoff(cfg, rundir, detailed):
    """Addition 3: split the raw P&R log's ##SIGNOFF markers into signoff/*.rpt."""
    sd = rundir / "signoff"
    sd.mkdir(exist_ok=True)
    raw = (rundir / "pnr.txt").read_text() if (rundir / "pnr.txt").exists() else ""
    marks = {"cellusage": "cellusage.rpt", "hold": "hold.rpt",
             "timing_worstN": "timing_worstN.rpt", "clock": "clock.rpt",
             "wirelength": "wirelength.rpt"}
    for tag, fn in marks.items():
        m = re.search(rf"##SIGNOFF {tag}\b(.*?)(##SIGNOFF |##DONE|\Z)", raw, re.S)
        (sd / fn).write_text(m.group(1).strip() + "\n" if m else "(not produced)\n")
    # congestion: pull global_route's congestion lines out of the GROUTE section
    cong = [ln for ln in raw.splitlines()
            if re.search(r"congestion|overflow|Routing congestion|utilization", ln, re.I)]
    (sd / "congestion.rpt").write_text(
        "\n".join(cong) + "\n" if cong else "(no congestion warnings reported)\n")
    # DRC: detailed_route writes <cfg>.drc.rpt next to the DEF (DETAILED=1 only)
    drc_src = BUILD / f"{cfg}.drc.rpt"
    if detailed and drc_src.exists():
        copy(drc_src, sd / "drc.rpt")
    else:
        (sd / "drc.rpt").write_text(
            "(detailed_route not run: pass --detailed for DRC signoff)\n"
            if not detailed else "(detailed_route produced no DRC report)\n")
    return sd


def gen_vcd(cfg, rundir):
    """Addition 2 input: run the cocotb happy-path with Verilator --trace -> VCD."""
    if not VENV_PY.exists():
        return None
    params = CONFIGS[cfg]
    runner = TB / "_vcd_run.py"
    runner.write_text(f"""\
import os
from pathlib import Path
from cocotb_tools.runner import get_runner
RTL = Path(r"{ROOT}") / "rtl"
SOURCES = [RTL / f for f in {RTL_FILES!r}]
PARAMS = {params!r}
os.environ["COALESCE_FACTOR"] = str(PARAMS.get("COALESCE_FACTOR", 8))
os.environ.setdefault("N_REQS", "128")
os.environ.setdefault("MEM_LATENCY", "40")
r = get_runner("verilator")
r.build(sources=[str(s) for s in SOURCES], hdl_toplevel="iommu_core",
        parameters=PARAMS, waves=True, always=True,
        build_args=["--trace", "--timing", "-Wno-WIDTHEXPAND", "-Wno-WIDTHTRUNC",
                    "-Wno-UNUSEDPARAM", "-Wno-UNUSEDSIGNAL", "-Wno-DECLFILENAME"])
r.test(hdl_toplevel="iommu_core", test_module="test_iommu", waves=True)
""")
    rc, out = sh([str(VENV_PY), str(runner)], cwd=TB, log=rundir / "vcd_build.txt")
    sb = TB / "sim_build"
    dump = next((p for p in [sb / "dump.vcd", sb / "iommu_core.vcd"] if p.exists()), None)
    if dump is None:
        dump = next(sb.glob("*.vcd"), None)
    if dump and dump.exists():
        dst = sb / f"{cfg}.vcd"
        if dump.resolve() != dst.resolve():
            shutil.copy2(dump, dst)
        return dst
    return None


def stage_power(cfg, lib, period, pdk_ref, corner, vcd, rundir):
    """Addition 2: default vs VCD-annotated power from one OpenSTA session."""
    netlist = f"{DROOT}/syn/build/{cfg}_netlist.v"
    lib_path = (f"{pdk_ref}/sky130A/libs.ref/sky130_fd_sc_{lib}/lib/"
                f"sky130_fd_sc_{lib}__{corner}.lib")
    vcd_in = f"{DROOT}/tb_coco/sim_build/{cfg}.vcd" if vcd else "/nonexistent.vcd"
    envs = {"LIB": lib_path, "NETLIST": netlist, "TOP": f"cfg_{cfg}",
            "PERIOD_NS": period, "VCD": vcd_in, "VCDSCOPE": "iommu_core"}
    r = docker_run(envs, f"sta -no_init -exit {DROOT}/syn/openlane/power_vcd.tcl")
    log = r.stdout + r.stderr
    (rundir / "power_vcd.txt").write_text(log)

    def grab(a, b):
        m = re.search(rf"{a}(.*?){b}", log, re.S)
        return m.group(1) if m else ""

    default = parse_power_table(grab("##POWER_DEFAULT", "##POWER_DEFAULT_END"))
    annotated = parse_power_table(grab("##POWER_ANNOTATED", "##POWER_ANNOTATED_END"))
    annotated_ok = bool(re.search(r"##POWER_ANNOTATED annotated=1", log))
    (rundir / "power_default.json").write_text(json.dumps(
        {"config": cfg, "library": lib, "activity": "default (statistical toggle)",
         "netlist": "post-synthesis (gate-level, no parasitics)",
         "power": default}, indent=2))
    (rundir / "power_annotated.json").write_text(json.dumps(
        {"config": cfg, "library": lib,
         "activity": "VCD-annotated (cocotb workload)" if annotated_ok
                     else "VCD annotation unavailable -> equals default",
         "vcd": (vcd.name if vcd else None), "vcd_annotated": annotated_ok,
         "netlist": "post-synthesis (gate-level, no parasitics)",
         "note": "RTL VCD net names differ from the flattened gate net; only matched "
                 "nets (mostly top ports) are annotated -- approximate.",
         "power": annotated or default}, indent=2))
    return default, annotated, annotated_ok


def provenance(cfg, lib, period, corner, pdk_ref, rundir):
    """Addition 4: git hash, tool versions, library/corner, params (no wall-clock date)."""
    git = sh(["git", "-C", str(ROOT), "rev-parse", "HEAD"])[1].strip().splitlines()[-1]
    vers = docker_run({}, "echo '##YOSYS'; yosys -V 2>&1 | head -1; "
                          "echo '##OPENROAD'; openroad -version 2>&1 | head -1; "
                          "echo '##OPENSTA'; sta -version 2>&1 | head -1; "
                          "echo '##MAGIC'; magic --version 2>&1 | head -1; "
                          "echo '##KLAYOUT'; klayout -v 2>&1 | head -1")
    vt = vers.stdout + vers.stderr

    def v(tag):
        m = re.search(rf"##{tag}\s*\n(.*)", vt)
        return m.group(1).strip() if m else "n/a"
    prov = {"config": cfg, "params": CONFIGS[cfg], "library": f"sky130_fd_sc_{lib}",
            "corner": corner, "period_ns": float(period),
            "clock_target_mhz": round(1000.0 / float(period), 2),
            "pdk_ref": pdk_ref, "git_commit": git,
            "tools": {"yosys": v("YOSYS"), "openroad": v("OPENROAD"),
                      "opensta": v("OPENSTA"), "magic": v("MAGIC"), "klayout": v("KLAYOUT")},
            "image": IMAGE}
    (rundir / "provenance.json").write_text(json.dumps(prov, indent=2))
    return prov


def gen_layout_png(cfg, lib, pdk_ref, rundir):
    """Addition 4: render the routed GDS to layout.png with KLayout (headless)."""
    gds = f"{DROOT}/results/{cfg}_{lib}/{cfg}_{lib}.gds"
    png = f"{DROOT}/results/{cfg}_{lib}/layout.png"
    if not (rundir / f"{cfg}_{lib}.gds").exists():
        return False
    # KLayout's LayoutView needs a Qt platform even in batch (-z): use offscreen.
    r = docker_run({"QT_QPA_PLATFORM": "offscreen"},
                   f"klayout -z -rd in_gds={gds} -rd out_png={png} "
                   f"-rm {DROOT}/syn/openlane/render_layout.py 2>&1 || true")
    (rundir / "layout_render.txt").write_text(r.stdout + r.stderr)
    return (rundir / "layout.png").exists()


def collect_ppa_stages(cfg, lib, rundir):
    """Reuse ppa_compare.py for the cross-stage table + the shared history row."""
    rc, out = sh([sys.executable, str(SYN / "ppa_compare.py"), cfg],
                 log=rundir / "ppa_compare_stdout.txt")
    copy(RESULTS / f"{cfg}_ppa.md", rundir / "ppa_stages.md")
    copy(RESULTS / f"{cfg}_ppa.json", rundir / "ppa_stages.json")
    return (rundir / "ppa_stages.json").exists()


def write_report(cfg, lib, period, syn, pnr, prov, pdefault, pannot, pannot_ok,
                 png_ok, rundir, stage_ok):
    """Addition 4: aggregate report.md + report.html (single page, embeds layout.png)."""
    stages = pnr.get("stages", {})
    last = next((k for k in ["DROUTE", "GROUTE", "CTS", "PLACE"] if k in stages), None)
    L = []
    L.append(f"# IOMMU flow report — `{cfg}` on `sky130_fd_sc_{lib}`\n")
    L.append(f"- clock target: **{prov['clock_target_mhz']} MHz** ({period} ns)  ·  "
             f"corner `{prov['corner']}`  ·  git `{prov['git_commit'][:10]}`")
    L.append(f"- tools: yosys `{prov['tools']['yosys']}` · openroad "
             f"`{prov['tools']['openroad']}` · magic `{prov['tools']['magic']}` · "
             f"klayout `{prov['tools']['klayout']}`\n")

    L.append("## Stage pass/fail")
    L.append("| stage | status |\n|---|---|")
    for k, ok in stage_ok.items():
        L.append(f"| {k} | {'✅ pass' if ok else '❌ fail'} |")

    L.append("\n## PPA across stages")
    L.append("| stage | area | Fmax | power |\n|---|---|---|---|")
    if syn:
        p = syn.get("power_W", {})
        L.append(f"| post-synthesis | {syn.get('area_um2_total', 0):.0f} um² (cells) | "
                 f"{(syn.get('fmax_mhz') or 0):.1f} MHz | {p.get('total_W', 0):.3f} W |")
    lab = {"PLACE": "post-place+repair", "CTS": "post-CTS",
           "GROUTE": "post-global-route", "DROUTE": "post-detailed-route"}
    for k in ["PLACE", "CTS", "GROUTE", "DROUTE"]:
        if k in stages:
            st = stages[k]
            L.append(f"| {lab[k]} | {st.get('die_area_um2', '?')} um² "
                     f"(@{st.get('utilization_pct', '?')}%) | "
                     f"{(st.get('fmax_mhz') or 0):.1f} MHz | "
                     f"{(st.get('power_W_total') or 0):.3f} W |")

    if last and stages[last].get("power_breakdown"):
        pb = stages[last]["power_breakdown"]
        L.append(f"\n## P&R power breakdown (post-{last.lower()})")
        L.append("| group | internal | switching | leakage | total (W) |\n"
                 "|---|---|---|---|---|")
        for g, vv in pb.get("by_group", {}).items():
            L.append(f"| {g} | {vv['internal_W']:.3e} | {vv['switching_W']:.3e} | "
                     f"{vv['leakage_W']:.3e} | {vv['total_W']:.3e} |")
        c = pb["by_category"]
        L.append(f"| **total** | {c['internal_W']:.3e} | {c['switching_W']:.3e} | "
                 f"{c['leakage_W']:.3e} | **{c['total_W']:.3e}** |")

    L.append("\n## Power: default vs VCD-annotated (gate-level)")
    L.append("| activity | internal | switching | leakage | total (W) |\n"
             "|---|---|---|---|---|")
    for name, pw, ok in [("default", pdefault, True),
                         ("VCD-annotated" if pannot_ok else "VCD-annotated (=default)",
                          pannot, pannot_ok)]:
        if pw:
            c = pw["by_category"]
            L.append(f"| {name} | {c['internal_W']:.3e} | {c['switching_W']:.3e} | "
                     f"{c['leakage_W']:.3e} | {c['total_W']:.3e} |")
    if not pannot_ok:
        L.append("\n> VCD annotation unavailable (RTL/gate net-name mismatch after "
                 "flatten); annotated power falls back to default. See USAGE_flow.md.")

    L.append("\n## Signoff (signoff/*.rpt)")
    L.append("- `drc.rpt` · `hold.rpt` · `timing_worstN.rpt` · `clock.rpt` · "
             "`wirelength.rpt` · `congestion.rpt`")

    if png_ok:
        L.append("\n## Layout\n")
        L.append("![layout](layout.png)")

    md = "\n".join(L) + "\n"
    (rundir / "report.md").write_text(md)
    html = ("<!doctype html><meta charset='utf-8'>"
            f"<title>{cfg}/{lib} flow report</title>"
            "<style>body{font-family:sans-serif;max-width:1000px;margin:2em auto;"
            "padding:0 1em}pre{background:#f4f4f4;padding:1em;overflow:auto}"
            "img{max-width:100%}</style>"
            f"<pre>{md.replace('<', '&lt;')}</pre>"
            + (f"<img src='layout.png'>" if png_ok else ""))
    (rundir / "report.html").write_text(html)


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="RTL->synth->P&R->compare->GDS, one command.")
    ap.add_argument("--config", default="full", choices=sorted(CONFIGS))
    ap.add_argument("--lib", default="hd",
                    help="sky130 std-cell variant: hd|hs|hdll|ms|ls (STD_VARIANT)")
    ap.add_argument("--period", default="2.5", help="clock period ns (default 2.5 = 400 MHz)")
    ap.add_argument("--corner", default=os.environ.get("STD_CORNER", "tt_025C_1v80"))
    ap.add_argument("--pdk-ref", default=os.environ.get("PDK_REF", DEFAULT_PDK_REF))
    ap.add_argument("--maxfo", type=int, default=16, help="max fanout for repair_design")
    ap.add_argument("--detailed", action="store_true",
                    help="run detailed_route (slow) for DRC signoff")
    ap.add_argument("--no-vcd", action="store_true", help="skip VCD-annotated power")
    args = ap.parse_args()

    cfg, lib, period = args.config, args.lib, args.period
    rundir = RESULTS / f"{cfg}_{lib}"
    rundir.mkdir(parents=True, exist_ok=True)
    print(f"=== flow: config={cfg} lib=sky130_fd_sc_{lib} period={period}ns "
          f"-> {rundir.relative_to(ROOT)} ===")
    stage_ok = {}

    # 1) synthesis
    print("[1/7] synthesis (yosys + OpenSTA) ...")
    ok, syn, _ = stage_synth(cfg, lib, period, args.pdk_ref, args.corner, rundir)
    stage_ok["synth"] = ok
    print(f"      {'ok' if ok else 'FAILED'}  "
          f"Fmax={(syn.get('fmax_mhz') or 0):.1f}MHz area={syn.get('area_um2_total')}um^2")

    # 2) P&R + GDS (needs the synth netlist)
    pnr, pdefault, pannot, pannot_ok, png_ok = {}, {}, {}, False, False
    if ok:
        print("[2/7] P&R (OpenROAD staged) + GDS (magic) ...")
        g_ok, pnr, _ = stage_pnr(cfg, lib, period, args.pdk_ref, args.maxfo,
                                 args.detailed, rundir)
        stage_ok["pnr"] = bool(pnr.get("stages"))
        stage_ok["gds"] = g_ok
        pnr = enrich_pnr(rundir, pnr)             # addition 1
        split_signoff(cfg, rundir, args.detailed) # addition 3
        last = next((k for k in ["DROUTE", "GROUTE", "CTS", "PLACE"]
                     if k in pnr.get("stages", {})), None)
        print(f"      P&R {'ok' if stage_ok['pnr'] else 'FAILED'} (last={last})  "
              f"GDS {'ok' if g_ok else 'FAILED'}")
    else:
        print("[2/7] P&R skipped (synthesis failed)")
        stage_ok["pnr"] = stage_ok["gds"] = False

    # 3) VCD-annotated power (addition 2)
    vcd = None
    if ok and not args.no_vcd:
        print("[3/7] VCD (cocotb/Verilator --trace) ...")
        vcd = gen_vcd(cfg, rundir)
        print(f"      VCD {'ok: ' + vcd.name if vcd else 'unavailable (annotated=default)'}")
    if ok:
        print("[4/7] power: default vs VCD-annotated (OpenSTA) ...")
        pdefault, pannot, pannot_ok = stage_power(cfg, lib, period, args.pdk_ref,
                                                  args.corner, vcd, rundir)
        stage_ok["power"] = bool(pdefault)

    # 4) provenance + cross-stage PPA + layout + report (addition 4)
    print("[5/7] provenance + cross-stage PPA table ...")
    prov = provenance(cfg, lib, period, args.corner, args.pdk_ref, rundir)
    stage_ok["ppa_stages"] = collect_ppa_stages(cfg, lib, rundir)
    print("[6/7] layout.png (klayout) ...")
    png_ok = gen_layout_png(cfg, lib, args.pdk_ref, rundir) if stage_ok.get("gds") else False
    stage_ok["layout"] = png_ok
    print("[7/7] aggregate report.md/html ...")
    write_report(cfg, lib, period, syn, pnr, prov, pdefault, pannot, pannot_ok,
                 png_ok, rundir, stage_ok)

    # summary
    print("\n=== summary ===")
    for k, v in stage_ok.items():
        print(f"  {k:12} {'PASS' if v else 'FAIL'}")
    files = sorted(p.relative_to(rundir).as_posix() for p in rundir.rglob("*") if p.is_file())
    print(f"  artifacts in {rundir.relative_to(ROOT)}/: {len(files)} files")
    all_pass = all(stage_ok.get(k) for k in ("synth", "pnr", "gds"))
    print(f"  RESULT: {'PASS' if all_pass else 'PARTIAL'} "
          f"(report: {(rundir / 'report.md').relative_to(ROOT)})")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
