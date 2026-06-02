"""First-order area & power estimator for the IOMMU simulator.

This module is *non-invasive*: it reads the static configuration plus the
activity counters that the simulator already produces (after ``sim.run()``)
and emits a per-component area/power breakdown, totals, energy-per-translation,
and a frozen JSON "prediction record" to compare against sky130 logic synthesis
later.

It is a FIRST-ORDER model intended for **relative architecture comparison and
later calibration vs. synthesis** -- NOT absolute sign-off. Random/control
logic is modelled coarsely and the interconnect / clock-tree are NOT modelled.

Locked design decisions (see ESTIMATOR_ja.md):
  1. The 4 kB data payload of a transaction lives in the I/O bridge, NOT the
     IOMMU. The transaction buffer here holds *control bits only*
     (tag/ID, IOVA, device_id, process_id, type/length, status).
  2. Tech constants are sky130 SEED placeholders -- marked REFINE -- to be
     calibrated later vs. synthesis / OpenRAM / CACTI.
  3. Units: area in um^2 (totals also mm^2), power in mW, energy in pJ.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

# ============================================================================
# Tech constants -- sky130 SEED table. *** ALL VALUES ARE PLACEHOLDERS ***
# Calibrate later vs. synthesis / OpenRAM / CACTI. Search for "REFINE".
# ============================================================================
@dataclass
class TechParams:
    # --- operating point ---
    vdd_v: float = 1.8                 # REFINE: sky130 nominal core voltage
    freq_hz: float = 400e6             # REFINE: 400 MHz -> 2.5 ns cycle

    # --- per-bit / per-gate areas (um^2) ---
    sram_bit_um2: float = 1.0          # REFINE: 6T SRAM bit; from OpenRAM sky130
    cam_bit_um2: float = 4.0           # REFINE: CAM cell (fully-assoc match) ~4x SRAM
    ff_bit_um2: float = 25.0           # REFINE: DFF per bit; sky130 DFF cells are large
    gate_um2: float = 3.0              # REFINE: NAND2-equivalent average gate

    # --- per-access / per-cycle dynamic energy (pJ) ---
    sram_e_access_pj: float = 1.0      # REFINE: per array access at ref size; grows with array (see access_energy_pj)
    ff_e_clk_pj_per_bit: float = 0.02  # REFINE: clock/toggle energy per FF bit per cycle

    # --- leakage (nW) ---
    leak_nw_per_bit: float = 0.05      # REFINE: per stored bit; sky130 130nm -> dynamic expected to dominate
    leak_nw_per_gate: float = 0.1      # REFINE: per logic gate

    # --- array overhead ---
    peripheral_overhead: float = 1.4   # REFINE: SRAM decoder/sense/precharge overhead factor

    # reference array size (entries*bits) at which sram_e_access_pj is defined; used
    # for the mild size dependence of access energy. REFINE from CACTI curves.
    e_access_ref_bits: float = 4096.0

    @property
    def cycle_ns(self) -> float:
        return 1e9 / self.freq_hz

    def access_energy_pj(self, array_bits: float) -> float:
        """Per-access SRAM energy with a *mild* size dependence (sqrt of relative
        array size). REFINE against CACTI. Floor at the seed value."""
        if array_bits <= 0:
            return 0.0
        scale = math.sqrt(max(array_bits, 1.0) / self.e_access_ref_bits)
        return self.sram_e_access_pj * max(1.0, scale)


# ============================================================================
# Per-structure bit widths. Sensible defaults; override per design point.
# ============================================================================
@dataclass
class StructParams:
    # IOTLB entry: tag = IOVA VPN (Sv39: 27b) + valid + ASID(16) ; data = PPN(28) + perms/attrs
    iotlb_tag_bits: int = 27 + 1 + 16
    iotlb_data_bits: int = 28 + 8

    # PWC (page-walk cache) entry: tag = partial VPN + level + valid ; data = next-level base PPN
    pwc_tag_bits: int = 18 + 2 + 1
    pwc_data_bits: int = 28

    # DDT$ (device-directory cache): tag = device_id(16)+valid ; data = device-context base PPN + flags
    ddt_tag_bits: int = 16 + 1
    ddt_data_bits: int = 28 + 16

    # PDT$ (process-directory cache): tag = process_id(20)+valid ; data = first-stage table base PPN + flags
    pdt_tag_bits: int = 20 + 1
    pdt_data_bits: int = 28 + 16

    # MSI$ (MSI page-table cache): tag = MSI addr index + valid ; data = translated MSI PPN + flags
    msi_tag_bits: int = 20 + 1
    msi_data_bits: int = 28 + 8

    # Transaction buffer: control bits per entry (NO 4 kB data payload, per decision 1):
    #   tag/ID(16) + IOVA(39) + device_id(16) + process_id(20) + type/length(8) + status(4) ~= 103 -> round to 96 seed
    buffer_ctrl_bits: int = 96

    # Walker context state per walker: {VPN, level, current base PPN, status, tag} ~ 96-128
    walker_context_bits: int = 112
    walker_control_gates: int = 400      # per-walker FSM / address-gen random logic (coarse)

    # Control/glue logic: top-level arbitration, request routing, CSR/glue (coarse gate estimate)
    control_glue_gates: int = 2000


# ============================================================================
# Per-component calibration multipliers (default 1.0). After synthesis, fit
# these so the model matches measured area/power per component.
# ============================================================================
@dataclass
class CalibParams:
    area_mult: dict = field(default_factory=dict)   # component name -> area multiplier
    power_mult: dict = field(default_factory=dict)  # component name -> (dyn+stat) power multiplier

    def a(self, name: str) -> float:
        return self.area_mult.get(name, 1.0)

    def p(self, name: str) -> float:
        return self.power_mult.get(name, 1.0)

    @staticmethod
    def fit(predicted: dict, measured: dict, *, key: str = "area") -> "CalibParams":
        """Build calibration multipliers from synthesis results.

        ``predicted`` / ``measured`` map component name -> value (area um^2 or
        power mW). Returns a CalibParams whose multipliers map the *uncalibrated*
        prediction onto the measured value:  mult = measured / predicted.
        """
        mult = {}
        for name, meas in measured.items():
            pred = predicted.get(name, 0.0)
            mult[name] = (meas / pred) if pred else 1.0
        cp = CalibParams()
        if key == "area":
            cp.area_mult = mult
        else:
            cp.power_mult = mult
        return cp


# ============================================================================
# Static configuration consumed by the estimator (sizing knobs per design point).
# Structural only; activity comes from Metrics/components.
# ============================================================================
@dataclass
class EstimatorConfig:
    name: str = "run"
    # caches: number of entries + whether the tag store is fully associative (CAM)
    iotlb_entries: int = 256
    iotlb_fully_assoc: bool = True
    pwc_entries: int = 16
    pwc_fully_assoc: bool = True
    # directory / MSI caches (not exercised by the single-stage workload yet, but
    # sized so the area breakdown is complete -- see ESTIMATOR_ja.md). 0 disables.
    ddt_entries: int = 16
    ddt_fully_assoc: bool = True
    pdt_entries: int = 16
    pdt_fully_assoc: bool = True
    msi_entries: int = 8
    msi_fully_assoc: bool = True
    # transaction buffer depth (control-bit FF buffer; 4 kB data NOT here)
    buffer_depth: int = 16
    # parallel page-table walkers
    num_walkers: int = 4


# ============================================================================
# Result containers
# ============================================================================
@dataclass
class ComponentEstimate:
    name: str
    area_um2: float
    sram_bits: int
    cam_bits: int
    ff_bits: int
    gates: int
    access_count: float = 0.0
    dyn_mW: float = 0.0
    stat_mW: float = 0.0

    @property
    def total_mW(self) -> float:
        return self.dyn_mW + self.stat_mW


@dataclass
class EstimateResult:
    config: EstimatorConfig
    tech: TechParams
    struct: StructParams
    calib: CalibParams
    components: list                      # list[ComponentEstimate]
    sim_time_ns: float
    completed: int

    # ---- totals ----
    @property
    def area_um2(self) -> float:
        return sum(c.area_um2 for c in self.components)

    @property
    def area_mm2(self) -> float:
        return self.area_um2 / 1e6

    @property
    def dyn_mW(self) -> float:
        return sum(c.dyn_mW for c in self.components)

    @property
    def stat_mW(self) -> float:
        return sum(c.stat_mW for c in self.components)

    @property
    def total_mW(self) -> float:
        return self.dyn_mW + self.stat_mW

    @property
    def energy_per_translation_pj(self) -> float:
        if not self.completed:
            return 0.0
        sim_time_s = self.sim_time_ns * 1e-9
        total_energy_pj = self.total_mW * 1e-3 * sim_time_s * 1e12   # mW->W, *s = J, *1e12 = pJ
        return total_energy_pj / self.completed

    # ---- presentation ----
    def table(self) -> str:
        h = f"{'component':<14}{'area_um2':>12}{'sram_b':>9}{'cam_b':>8}{'ff_b':>8}{'gates':>8}{'dyn_mW':>10}{'stat_mW':>10}{'tot_mW':>10}"
        lines = [h, "-" * len(h)]
        for c in self.components:
            lines.append(
                f"{c.name:<14}{c.area_um2:>12.1f}{c.sram_bits:>9d}{c.cam_bits:>8d}"
                f"{c.ff_bits:>8d}{c.gates:>8d}{c.dyn_mW:>10.4f}{c.stat_mW:>10.4f}{c.total_mW:>10.4f}"
            )
        lines.append("-" * len(h))
        lines.append(
            f"{'TOTAL':<14}{self.area_um2:>12.1f}{'':>9}{'':>8}{'':>8}{'':>8}"
            f"{self.dyn_mW:>10.4f}{self.stat_mW:>10.4f}{self.total_mW:>10.4f}"
        )
        lines.append(
            f"  total area: {self.area_um2:,.1f} um^2 ({self.area_mm2:.6f} mm^2)   "
            f"total power: {self.total_mW:.4f} mW (dyn {self.dyn_mW:.4f} + stat {self.stat_mW:.4f})"
        )
        lines.append(
            f"  energy/translation: {self.energy_per_translation_pj:.3f} pJ   "
            f"(completed {self.completed}, sim_time {self.sim_time_ns:.1f} ns)"
        )
        return "\n".join(lines)

    # ---- frozen prediction record ----
    def _record(self) -> dict:
        cfg = asdict(self.config)
        tech = asdict(self.tech)
        struct = asdict(self.struct)
        rec = {
            "schema": "iommu-area-power-prediction/v1",
            "note": "FIRST-ORDER sky130 SEED estimate -- relative comparison + calibration, NOT sign-off.",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "config": cfg,
            "tech_params": tech,
            "struct_params": struct,
            "calib": {"area_mult": self.calib.area_mult, "power_mult": self.calib.power_mult},
            "sim_time_ns": self.sim_time_ns,
            "completed": self.completed,
            "components": [asdict(c) | {"total_mW": c.total_mW} for c in self.components],
            "totals": {
                "area_um2": self.area_um2,
                "area_mm2": self.area_mm2,
                "dyn_mW": self.dyn_mW,
                "stat_mW": self.stat_mW,
                "total_mW": self.total_mW,
                "energy_per_translation_pj": self.energy_per_translation_pj,
            },
        }
        # config hash covers the structural config + tech + struct + calib (the inputs
        # that determine the prediction), so two runs with identical setup hash equal.
        hash_src = json.dumps(
            {"config": cfg, "tech_params": tech, "struct_params": struct,
             "calib": rec["calib"]},
            sort_keys=True,
        )
        rec["config_hash"] = hashlib.sha256(hash_src.encode()).hexdigest()
        return rec

    def freeze(self, path: str) -> dict:
        """Dump the frozen JSON prediction record to ``path`` and return it."""
        rec = self._record()
        with open(path, "w") as f:
            json.dump(rec, f, indent=2, sort_keys=True)
        return rec


# ============================================================================
# Area model: config -> per-component area (um^2)
# ============================================================================
class AreaModel:
    def __init__(self, tech: TechParams, struct: StructParams):
        self.t = tech
        self.s = struct

    def cache(self, entries: int, tag_bits: int, data_bits: int, fully_assoc: bool):
        """Return (area_um2, sram_bits, cam_bits, gates) for a cache structure.

        Fully-associative tag store uses CAM cells (sram->cam for the tag bits) and
        adds comparator/match gate area; set/direct uses SRAM cells throughout.
        """
        if entries <= 0:
            return 0.0, 0, 0, 0
        tag_total = entries * tag_bits
        data_total = entries * data_bits
        if fully_assoc:
            cam_bits = tag_total                 # tag store is CAM
            sram_bits = data_total               # data store is SRAM
            cell_area = cam_bits * self.t.cam_bit_um2 + sram_bits * self.t.sram_bit_um2
            comp_gates = tag_total               # one comparator bit-cell match line per tag bit
        else:
            cam_bits = 0
            sram_bits = tag_total + data_total
            cell_area = sram_bits * self.t.sram_bit_um2
            comp_gates = entries                 # set/direct: small way-select logic
        area = cell_area * self.t.peripheral_overhead + comp_gates * self.t.gate_um2
        return area, sram_bits, cam_bits, comp_gates

    def buffer(self, depth: int, ctrl_bits: int):
        """Transaction buffer = depth * ctrl_bits FFs (control bits ONLY, no 4 kB data)."""
        ff_bits = max(0, depth) * max(0, ctrl_bits)
        area = ff_bits * self.t.ff_bit_um2
        return area, ff_bits

    def walker(self, num_walkers: int, context_bits: int, control_gates: int):
        """Walker logic = per-walker context FFs + per-walker FSM/address-gen gates."""
        ff_bits = max(0, num_walkers) * max(0, context_bits)
        gates = max(0, num_walkers) * max(0, control_gates)
        area = ff_bits * self.t.ff_bit_um2 + gates * self.t.gate_um2
        return area, ff_bits, gates

    def control_glue(self, gates: int):
        return max(0, gates) * self.t.gate_um2, max(0, gates)


# ============================================================================
# Power model: config + activity -> per-component dynamic + static power (mW)
# ============================================================================
class PowerModel:
    def __init__(self, tech: TechParams):
        self.t = tech

    @staticmethod
    def _pj_per_s_to_mW(pj_per_s: float) -> float:
        # 1 pJ/s = 1 pW = 1e-9 mW
        return pj_per_s * 1e-9

    @staticmethod
    def _nw_to_mW(nw: float) -> float:
        return nw * 1e-6

    def dynamic_mW(self, *, access_count: float, array_bits: float, sim_time_s: float,
                   ff_bits: int = 0, ff_active_frac: float = 1.0) -> float:
        """Dynamic power = SRAM access energy / time  +  FF clock/toggle power.

        access energy uses the size-dependent per-access energy; FF clock power is
        ff_bits * e_clk * freq, scaled by an activity fraction (1.0 = clocked every
        cycle, <1 for utilisation-gated structures like idle walkers)."""
        pj_per_s = 0.0
        if sim_time_s > 0 and access_count > 0:
            pj_per_s += access_count * self.t.access_energy_pj(array_bits) / sim_time_s
        if ff_bits > 0:
            pj_per_s += ff_bits * self.t.ff_e_clk_pj_per_bit * self.t.freq_hz * ff_active_frac
        return self._pj_per_s_to_mW(pj_per_s)

    def static_mW(self, *, total_bits: float, total_gates: float) -> float:
        nw = total_bits * self.t.leak_nw_per_bit + total_gates * self.t.leak_nw_per_gate
        return self._nw_to_mW(nw)


# ============================================================================
# Orchestration
# ============================================================================
def _activity(components: dict | None, name: str) -> float:
    """Cache activity = hits + misses + inserts, read from a live cache object."""
    if not components:
        return 0.0
    obj = components.get(name)
    if obj is None:
        return 0.0
    return float(getattr(obj, "hits", 0) + getattr(obj, "misses", 0) + getattr(obj, "inserts", 0))


def estimate(config: EstimatorConfig, metrics, components: dict | None = None, *,
             tech: TechParams | None = None, struct: StructParams | None = None,
             calib: CalibParams | None = None) -> EstimateResult:
    """Estimate per-component area & power.

    Args:
        config: EstimatorConfig -- static structural sizing for this design point.
        metrics: the engine Metrics object returned by ``sim.run()``.
        components: optional dict of live component objects for activity counts, e.g.
            {"iotlb": cache, "pwc": cache, "memory": mem}. Missing entries -> 0 activity.
        tech / struct / calib: optional overrides (default to the SEED tables).

    Non-invasive: reads only, never mutates the simulator.
    """
    tech = tech or TechParams()
    struct = struct or StructParams()
    calib = calib or CalibParams()
    am = AreaModel(tech, struct)
    pm = PowerModel(tech)

    sim_time_ns = (metrics.last_complete - (metrics.first_arrival or 0.0))
    if sim_time_ns <= 0:
        sim_time_ns = metrics.last_complete or 1.0
    sim_time_s = sim_time_ns * 1e-9
    completed = metrics.completed

    comps: list[ComponentEstimate] = []

    # ---- caches: (component name, entries, fully_assoc, tag_bits, data_bits) ----
    cache_specs = [
        ("IOTLB", config.iotlb_entries, config.iotlb_fully_assoc, struct.iotlb_tag_bits, struct.iotlb_data_bits),
        ("PWC",   config.pwc_entries,   config.pwc_fully_assoc,   struct.pwc_tag_bits,   struct.pwc_data_bits),
        ("DDT$",  config.ddt_entries,   config.ddt_fully_assoc,   struct.ddt_tag_bits,   struct.ddt_data_bits),
        ("PDT$",  config.pdt_entries,   config.pdt_fully_assoc,   struct.pdt_tag_bits,   struct.pdt_data_bits),
        ("MSI$",  config.msi_entries,   config.msi_fully_assoc,   struct.msi_tag_bits,   struct.msi_data_bits),
    ]
    # map component display name -> activity-source key in `components`
    activity_key = {"IOTLB": "iotlb", "PWC": "pwc"}   # DDT$/PDT$/MSI$ not yet exercised -> 0
    for name, entries, fa, tagb, datab in cache_specs:
        area, sram_bits, cam_bits, gates = am.cache(entries, tagb, datab, fa)
        acc = _activity(components, activity_key.get(name, ""))
        array_bits = entries * (tagb + datab)
        dyn = pm.dynamic_mW(access_count=acc, array_bits=array_bits, sim_time_s=sim_time_s)
        stat = pm.static_mW(total_bits=sram_bits + cam_bits, total_gates=gates)
        comps.append(ComponentEstimate(name, area * calib.a(name), sram_bits, cam_bits, 0, gates,
                                       access_count=acc,
                                       dyn_mW=dyn * calib.p(name), stat_mW=stat * calib.p(name)))

    # ---- transaction buffer (control bits only) ----
    b_area, b_ff = am.buffer(config.buffer_depth, struct.buffer_ctrl_bits)
    # written once per admitted translation; FF-clocked every cycle.
    b_dyn = pm.dynamic_mW(access_count=completed, array_bits=struct.buffer_ctrl_bits,
                          sim_time_s=sim_time_s, ff_bits=b_ff, ff_active_frac=1.0)
    b_stat = pm.static_mW(total_bits=b_ff, total_gates=0)
    comps.append(ComponentEstimate("buffer", b_area * calib.a("buffer"), 0, 0, b_ff, 0,
                                   access_count=completed,
                                   dyn_mW=b_dyn * calib.p("buffer"), stat_mW=b_stat * calib.p("buffer")))

    # ---- walker logic ----
    w_area, w_ff, w_gates = am.walker(config.num_walkers, struct.walker_context_bits,
                                      struct.walker_control_gates)
    # utilisation = busy-time / (num_walkers * sim_time); gates toggle per memory access.
    busy_frac = 0.0
    if config.num_walkers > 0 and sim_time_ns > 0:
        busy_frac = min(1.0, metrics.walker_busy_ns / (config.num_walkers * sim_time_ns))
    mem_acc = float(getattr(components.get("memory"), "accesses", 0)) if components else 0.0
    w_dyn = pm.dynamic_mW(access_count=mem_acc, array_bits=struct.walker_context_bits,
                          sim_time_s=sim_time_s, ff_bits=w_ff, ff_active_frac=busy_frac)
    w_stat = pm.static_mW(total_bits=w_ff, total_gates=w_gates)
    comps.append(ComponentEstimate("walker", w_area * calib.a("walker"), 0, 0, w_ff, w_gates,
                                   access_count=mem_acc,
                                   dyn_mW=w_dyn * calib.p("walker"), stat_mW=w_stat * calib.p("walker")))

    # ---- control / glue ----
    c_area, c_gates = am.control_glue(struct.control_glue_gates)
    # coarse: toggles roughly once per completed translation.
    c_dyn = pm.dynamic_mW(access_count=completed, array_bits=tech.e_access_ref_bits,
                          sim_time_s=sim_time_s)
    c_stat = pm.static_mW(total_bits=0, total_gates=c_gates)
    comps.append(ComponentEstimate("control", c_area * calib.a("control"), 0, 0, 0, c_gates,
                                   access_count=completed,
                                   dyn_mW=c_dyn * calib.p("control"), stat_mW=c_stat * calib.p("control")))

    return EstimateResult(config=config, tech=tech, struct=struct, calib=calib,
                          components=comps, sim_time_ns=sim_time_ns, completed=completed)
