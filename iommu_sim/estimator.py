"""Process-independent area & power estimator (design_doc §10, design_premises §13).

Absolute area/power are process-dependent, but the *relative* cost of primitives
is nearly process-independent, so we report normalized units and absolutize with
one optional ``scale_factor``.

  * Area  -> gate-equivalents (GE), 1 GE = one 2-input NAND.
        cell = SRAMbit*0.2 + CAMbit*0.6 + FFbit*6   (periphery x1.3 on the array)
        module_GE = cell*periphery + logic_gates
  * Power -> normalized energy units, 1 unit = one NAND switch.
        dynamic = Σ(activity x access_energy) + FF-clock(FFbits x clk_e x cycles)
        static  = (bits x leak_bit + gates x leak_gate) x cycles
        DRAM access energy is reported SEPARATELY (memory subsystem).
  * energy_per_translation = (dyn + stat) / completed.

Per-module breakdown for area and power, plus totals. All weights live in
``PAWeights`` (one place, tunable). A frozen JSON record (config hash + normalized
PPA) is emitted for later estimate-vs-synthesis calibration (design_doc §12).

The 4 kB DMA payload lives in the I/O bridge, NOT the IOMMU, so the request
buffer here is control bits only (design_premises §13).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict


# ============================================================================
# Tunable weights (one place). Area in GE; energy in normalized NAND-switch units.
# ============================================================================
@dataclass
class PAWeights:
    # --- area (gate-equivalents per primitive) ---
    sram_bit_ge: float = 0.2
    cam_bit_ge: float = 0.6
    ff_bit_ge: float = 6.0
    periphery: float = 1.3          # array decoder/sense overhead

    # --- dynamic energy (normalized units) ---
    e_sram_bit: float = 0.02        # per SRAM bit touched per access
    e_cam_bit: float = 0.05         # per CAM tag bit per match (all entries compared)
    e_ff_clk_bit: float = 0.01      # per FF bit per active cycle (clock/toggle)

    # --- static / leakage (normalized units per bit/gate per cycle) ---
    leak_bit: float = 1.0e-5
    leak_gate: float = 2.0e-5

    # --- DRAM (reported separately) ---
    e_dram_access: float = 20.0     # per 64 B memory access (normalized)


# Per-structure tag/data bit widths (RTL-realizable; context-tagged). ctx ~= 32 b.
CTX_BITS = 32
STRUCT_BITS = {
    # module        (tag_bits,            data_bits)
    "iotlb":      (27 + CTX_BITS + 1,     28 + 8),
    "s1_pwc":     (18 + 2 + CTX_BITS + 1, 28),
    "s2_pwc":     (29 + CTX_BITS + 1,     28),
    "table_gpa":  (29 + CTX_BITS + 1,     28),
    "data_gpa":   (29 + CTX_BITS + 1,     28),
    "ddtc":       (16 + 1,                28 + 16),
    "pdtc":       (20 + 1,                28 + 16),
    "msi":        (20 + 1,                28 + 8),
}

# FF-based / logic structures
BUFFER_CTRL_BITS = 96          # tag/ID + IOVA + device_id + pasid + type/len + status
WALKER_CTX_BITS = 112          # per-walker state {vpn, level, base, status, tag}
WALKER_GATES = 400             # per-walker FSM / address-gen logic
ARBITER_GATES = 800            # request arbitration / most-complete-hit encoder
CONTROL_GATES = 2000           # CSR / glue / top-level control


@dataclass
class ModuleEstimate:
    name: str
    area_ge: float
    sram_bits: int
    cam_bits: int
    ff_bits: int
    gates: int
    accesses: float = 0.0
    dyn: float = 0.0               # normalized power (energy/cycle)
    stat: float = 0.0

    @property
    def total_power(self):
        return self.dyn + self.stat


@dataclass
class PAResult:
    modules: list
    weights: PAWeights
    sim_cycles: float
    completed: int
    dram_accesses: int
    dram_energy: float
    scale_factor: float = None
    config_dict: dict = field(default_factory=dict)

    # ---- totals ----
    @property
    def area_ge(self):
        return sum(m.area_ge for m in self.modules)

    @property
    def dyn_power(self):
        return sum(m.dyn for m in self.modules)

    @property
    def stat_power(self):
        return sum(m.stat for m in self.modules)

    @property
    def total_power(self):
        return self.dyn_power + self.stat_power

    @property
    def total_energy(self):
        return self.total_power * self.sim_cycles

    @property
    def energy_per_translation(self):
        if not self.completed:
            return 0.0
        return self.total_energy / self.completed

    # ---- presentation ----
    def table(self):
        unit_a = "GE" if self.scale_factor is None else "um^2"
        h = (f"{'module':<12}{'area_'+unit_a:>12}{'sram_b':>9}{'cam_b':>8}{'ff_b':>8}"
             f"{'gates':>7}{'access':>10}{'dyn':>11}{'stat':>11}")
        lines = [h, "-" * len(h)]
        sf = self.scale_factor or 1.0
        for m in self.modules:
            lines.append(
                f"{m.name:<12}{m.area_ge*sf:>12.1f}{m.sram_bits:>9d}{m.cam_bits:>8d}"
                f"{m.ff_bits:>8d}{m.gates:>7d}{m.accesses:>10.0f}{m.dyn:>11.4f}{m.stat:>11.5f}"
            )
        lines.append("-" * len(h))
        lines.append(
            f"{'TOTAL':<12}{self.area_ge*sf:>12.1f}{'':>9}{'':>8}{'':>8}{'':>7}{'':>10}"
            f"{self.dyn_power:>11.4f}{self.stat_power:>11.5f}"
        )
        lines.append(
            f"  area: {self.area_ge:,.1f} GE" + (f" -> {self.area_ge*sf:,.1f} um^2 (scale {sf})" if self.scale_factor else "")
        )
        lines.append(
            f"  power(norm): dyn {self.dyn_power:.4f} + stat {self.stat_power:.5f} = {self.total_power:.4f} units/cycle"
        )
        lines.append(
            f"  energy/translation: {self.energy_per_translation:.3f} norm-units"
            + (f" -> {self.energy_per_translation*sf:.3f} pJ (scale {sf})" if self.scale_factor else "")
        )
        lines.append(
            f"  DRAM (separate): {self.dram_accesses} accesses, {self.dram_energy:,.0f} norm-units "
            f"({self.dram_energy/self.completed:.2f}/translation)" if self.completed else ""
        )
        return "\n".join(l for l in lines if l)

    # ---- frozen prediction record ----
    def record(self):
        rec = {
            "schema": "iommu-ppa-prediction/v2-normalized",
            "note": "Process-independent GE/normalized-energy estimate; relative comparison + calibration, NOT sign-off.",
            "config": self.config_dict,
            "weights": asdict(self.weights),
            "sim_cycles": self.sim_cycles,
            "completed": self.completed,
            "modules": [asdict(m) | {"total_power": m.total_power} for m in self.modules],
            "totals": {
                "area_ge": self.area_ge,
                "dyn_power": self.dyn_power,
                "stat_power": self.stat_power,
                "total_power": self.total_power,
                "energy_per_translation": self.energy_per_translation,
                "dram_accesses": self.dram_accesses,
                "dram_energy": self.dram_energy,
            },
            "scale_factor": self.scale_factor,
        }
        hash_src = json.dumps({"config": self.config_dict, "weights": asdict(self.weights)},
                              sort_keys=True)
        rec["config_hash"] = hashlib.sha256(hash_src.encode()).hexdigest()
        return rec

    def freeze(self, path):
        rec = self.record()
        with open(path, "w") as f:
            json.dump(rec, f, indent=2, sort_keys=True)
        return rec


def _cache_area_energy(w, entries, tag_bits, data_bits, is_cam, accesses, sim_cycles):
    """Return (area_ge, sram_bits, cam_bits, gates, dyn, stat) for one cache."""
    if entries <= 0:
        return 0.0, 0, 0, 0, 0.0, 0.0
    tag_total = entries * tag_bits
    data_total = entries * data_bits
    if is_cam:
        cam_bits = tag_total
        sram_bits = data_total
        gates = tag_total                       # one match-line cell per tag bit
        access_e = entries * tag_bits * w.e_cam_bit + data_bits * w.e_sram_bit
    else:
        cam_bits = 0
        sram_bits = tag_total + data_total
        gates = entries                         # way-select logic
        access_e = (tag_bits + data_bits) * w.e_sram_bit
    cell = sram_bits * w.sram_bit_ge + cam_bits * w.cam_bit_ge
    area = cell * w.periphery + gates
    dyn = accesses * access_e / sim_cycles if sim_cycles > 0 else 0.0
    stat = (sram_bits + cam_bits) * w.leak_bit + gates * w.leak_gate
    return area, sram_bits, cam_bits, gates, dyn, stat


def _ff_area_energy(w, ff_bits, gates, write_accesses, bits_per_write, sim_cycles, clocked_frac=1.0):
    area = ff_bits * w.ff_bit_ge + gates
    dyn_clk = ff_bits * w.e_ff_clk_bit * clocked_frac
    dyn_wr = (write_accesses * bits_per_write * w.e_sram_bit / sim_cycles) if sim_cycles > 0 else 0.0
    dyn = dyn_clk + dyn_wr
    stat = ff_bits * w.leak_bit + gates * w.leak_gate
    return area, dyn, stat


def estimate(cfg, caches, metrics, *, weights=None, dram_accesses=0):
    """Per-module normalized area/power for one design point.

    cfg           : Config (sizes the structures).
    caches        : CacheSet (entries, CAM flags, activity counters).
    metrics       : Metrics (completed, peaks, walker busy, sim_cycles).
    dram_accesses : total memory accesses (for the separate DRAM energy line).
    """
    w = weights or PAWeights()
    sim_cycles = metrics.sim_cycles or 1.0
    completed = metrics.completed
    modules = []

    def cache_activity(c):
        return float(c.hits + c.misses + c.inserts)

    # combined S1 PWC activity (two physical levels share the module name)
    s1_entries = caches.s1_l2.entries + caches.s1_l1.entries
    s1_cam = caches.s1_l2.is_cam or caches.s1_l1.is_cam
    s1_acc = cache_activity(caches.s1_l2) + cache_activity(caches.s1_l1)

    cache_inputs = [
        ("iotlb", caches.iotlb.entries, caches.iotlb.is_cam, cache_activity(caches.iotlb)),
        ("s1_pwc", s1_entries, s1_cam, s1_acc),
        ("s2_pwc", caches.s2_pwc.entries, caches.s2_pwc.is_cam, cache_activity(caches.s2_pwc)),
        ("table_gpa", caches.table_gpa.entries, caches.table_gpa.is_cam, cache_activity(caches.table_gpa)),
        ("data_gpa", caches.data_gpa.entries, caches.data_gpa.is_cam, cache_activity(caches.data_gpa)),
        ("ddtc", caches.ddtc.entries, caches.ddtc.is_cam, cache_activity(caches.ddtc)),
        ("pdtc", caches.pdtc.entries, caches.pdtc.is_cam, cache_activity(caches.pdtc)),
        ("msi", caches.msi.entries, caches.msi.is_cam, cache_activity(caches.msi)),
    ]
    for name, entries, is_cam, acc in cache_inputs:
        tagb, datab = STRUCT_BITS[name]
        area, sb, cb, g, dyn, stat = _cache_area_energy(w, entries, tagb, datab, is_cam, acc, sim_cycles)
        modules.append(ModuleEstimate(name, area, sb, cb, 0, g, accesses=acc, dyn=dyn, stat=stat))

    # ---- walkers (provisioned = explicit num_walkers else measured peak) ----
    n_walk = cfg.walkers.num_walkers if cfg.walkers.num_walkers is not None else max(1, metrics.peak_walks)
    busy_frac = 0.0
    if n_walk > 0 and sim_cycles > 0:
        busy_frac = min(1.0, metrics.walker_busy_cycles / (n_walk * sim_cycles))
    w_ff = n_walk * WALKER_CTX_BITS
    w_gates = n_walk * WALKER_GATES
    area, dyn, stat = _ff_area_energy(w, w_ff, w_gates, metrics.walks_started, WALKER_CTX_BITS,
                                      sim_cycles, clocked_frac=busy_frac)
    modules.append(ModuleEstimate("walkers", area, 0, 0, w_ff, w_gates, accesses=metrics.walks_started, dyn=dyn, stat=stat))

    # ---- transaction buffer (control bits only; 4 kB data is in the I/O bridge) ----
    depth = cfg.buffers.iommu_req_buffer if cfg.buffers.iommu_req_buffer is not None else max(1, metrics.peak_buffer)
    b_ff = depth * BUFFER_CTRL_BITS
    area, dyn, stat = _ff_area_energy(w, b_ff, 0, completed, BUFFER_CTRL_BITS, sim_cycles, clocked_frac=1.0)
    modules.append(ModuleEstimate("buffer", area, 0, 0, b_ff, 0, accesses=completed, dyn=dyn, stat=stat))

    # ---- arbiter ----
    a_dyn = (completed * ARBITER_GATES * 0.001 / sim_cycles) if sim_cycles > 0 else 0.0
    modules.append(ModuleEstimate("arbiter", ARBITER_GATES, 0, 0, 0, ARBITER_GATES,
                                  accesses=completed, dyn=a_dyn, stat=ARBITER_GATES * w.leak_gate))

    # ---- control / glue ----
    c_dyn = (completed * CONTROL_GATES * 0.0005 / sim_cycles) if sim_cycles > 0 else 0.0
    modules.append(ModuleEstimate("control", CONTROL_GATES, 0, 0, 0, CONTROL_GATES,
                                  accesses=completed, dyn=c_dyn, stat=CONTROL_GATES * w.leak_gate))

    # ---- DRAM energy (separate) ----
    dram_acc = int(dram_accesses)
    dram_energy = dram_acc * w.e_dram_access

    res = PAResult(modules=modules, weights=w, sim_cycles=sim_cycles, completed=completed,
                   dram_accesses=dram_acc, dram_energy=dram_energy,
                   scale_factor=cfg.pa.scale_factor, config_dict=cfg.to_dict())
    return res
