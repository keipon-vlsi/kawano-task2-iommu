"""Configuration = specification.

A single ``Config`` object drives the simulator and maps one-to-one onto the
future SystemVerilog ``parameter`` table (design_doc §4 / usage_manual §3). Every
field here is intended to be RTL-realizable. Configs load from YAML or JSON and
also accept plain dicts.

YAML gotcha: the bare token ``off`` parses to the Python bool ``False`` and
``on``/``yes`` parse to ``True``. We normalize those back to the intended string
enums (``superpage: off`` -> "off", ``prefetch.algo: off`` -> "off").
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict, fields, is_dataclass
from typing import Optional


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def _norm_off(v):
    """Map YAML's coerced bool back to the 'off'/'on' enum token."""
    if v is False:
        return "off"
    if v is True:
        return "on"
    return v


def _assoc(v):
    """Associativity: int N, 'full' (fully associative / CAM), or 1 (direct)."""
    if v in ("full", "fa", "cam", None):
        return "full"
    return int(v)


# --------------------------------------------------------------------------
# cache configs
# --------------------------------------------------------------------------
@dataclass
class CacheCfg:
    entries: int = 16
    assoc: object = "full"           # int N | "full" | 1(direct)
    enabled: bool = True

    @classmethod
    def from_dict(cls, d):
        if d is None:
            return cls()
        d = dict(d)
        return cls(entries=int(d.get("entries", 16)),
                   assoc=_assoc(d.get("assoc", "full")),
                   enabled=bool(d.get("enabled", True)))


@dataclass
class PWCLevelCfg:
    entries: int = 8
    assoc: object = "full"
    enabled: bool = True

    @classmethod
    def from_dict(cls, d):
        if d is None:
            return cls()
        d = dict(d)
        return cls(entries=int(d.get("entries", 8)), assoc=_assoc(d.get("assoc", "full")),
                   enabled=bool(d.get("enabled", True)))


@dataclass
class VMPWCCfg:
    """Guest (VS / VM-stage, Sv39) page-walk cache, one structure per level.
    L0 = leaf (guest leaf PTE, IOVA->data-GPA before G-stage). root = guest
    root-table GPA from the PDT context (register-like, ~always hits)."""
    l2: PWCLevelCfg = field(default_factory=lambda: PWCLevelCfg(8, "full"))
    l1: PWCLevelCfg = field(default_factory=lambda: PWCLevelCfg(16, "full"))
    l0: PWCLevelCfg = field(default_factory=lambda: PWCLevelCfg(16, "full"))
    root: PWCLevelCfg = field(default_factory=lambda: PWCLevelCfg(1, "full"))

    @classmethod
    def from_dict(cls, d):
        if d is None:
            return cls()
        d = dict(d); b = cls()
        pick = lambda k, dv: PWCLevelCfg.from_dict(d[k]) if k in d else dv
        return cls(l2=pick("l2", b.l2), l1=pick("l1", b.l1),
                   l0=pick("l0", b.l0), root=pick("root", b.root))


@dataclass
class GStageCfg:
    """One G-stage (Sv39x4) 3-level PWC hierarchy translating a single GPA stream
    (L0 = the full GPA->SPA result level)."""
    l2: PWCLevelCfg = field(default_factory=lambda: PWCLevelCfg(4, "full"))
    l1: PWCLevelCfg = field(default_factory=lambda: PWCLevelCfg(4, "full"))
    l0: PWCLevelCfg = field(default_factory=lambda: PWCLevelCfg(4, "full"))

    @classmethod
    def from_dict(cls, d, dflt=None):
        b = dflt or cls()
        if d is None:
            return b
        d = dict(d)
        pick = lambda k, dv: PWCLevelCfg.from_dict(d[k]) if k in d else dv
        return cls(l2=pick("l2", b.l2), l1=pick("l1", b.l1), l0=pick("l0", b.l0))


def _gstage(entries, enabled=True):
    lv = lambda: PWCLevelCfg(entries, "full", enabled=enabled)
    return GStageCfg(lv(), lv(), lv())


@dataclass
class GPWCCfg:
    """G-stage caches for the guest-TABLE-page GPAs (temporal, few; heavily reused),
    physically separated per VM level: G-Lx@VM-Ly. root = shared G-stage root
    register (host root table, hgatp)."""
    vm_l2: GStageCfg = field(default_factory=lambda: _gstage(2))   # translate VM-root ptr
    vm_l1: GStageCfg = field(default_factory=lambda: _gstage(4))   # translate VM-L2 PTE ppn
    vm_l0: GStageCfg = field(default_factory=lambda: _gstage(8))   # translate VM-L1 PTE ppn
    root: PWCLevelCfg = field(default_factory=lambda: PWCLevelCfg(1, "full"))

    @classmethod
    def from_dict(cls, d):
        if d is None:
            return cls()
        d = dict(d); b = cls()
        gv = lambda k, dv: GStageCfg.from_dict(d[k], dv) if k in d else dv
        root = PWCLevelCfg.from_dict(d["root"]) if "root" in d else b.root
        return cls(vm_l2=gv("vm_l2", b.vm_l2), vm_l1=gv("vm_l1", b.vm_l1),
                   vm_l0=gv("vm_l0", b.vm_l0), root=root)


@dataclass
class GFinalCfg:
    """G-stage caches for the FINAL data GPA (spatial, streaming). G-final-L0 made
    directly hittable by VPN == the IOTLB."""
    l2: PWCLevelCfg = field(default_factory=lambda: PWCLevelCfg(8, "full"))
    l1: PWCLevelCfg = field(default_factory=lambda: PWCLevelCfg(8, "full"))
    l0: PWCLevelCfg = field(default_factory=lambda: PWCLevelCfg(64, 4))

    @classmethod
    def from_dict(cls, d):
        if d is None:
            return cls()
        d = dict(d); b = cls()
        pick = lambda k, dv: PWCLevelCfg.from_dict(d[k]) if k in d else dv
        return cls(l2=pick("l2", b.l2), l1=pick("l1", b.l1), l0=pick("l0", b.l0))


def _legacy_caches(d):
    """Map the old flat cache keys (s1_pwc / s2_pwc / root_gpa / table_gpa /
    data_gpa) onto the new VM / G / G-final structure so pre-rename configs and
    tests keep loading. Explicit new-style keys (handled by the caller) win."""
    s1 = dict(d.get("s1_pwc") or {})
    l2 = PWCLevelCfg.from_dict(s1["l2"]) if "l2" in s1 else PWCLevelCfg(8, "full")
    l1 = PWCLevelCfg.from_dict(s1["l1"]) if "l1" in s1 else PWCLevelCfg(16, "full")
    # VM-L0 (guest leaf PTE) mirrors the deepest VM PWC level's enabled-state.
    l0 = PWCLevelCfg(max(16, l1.entries), "full", enabled=l1.enabled)
    vm = VMPWCCfg(l2=l2, l1=l1, l0=l0, root=PWCLevelCfg(1, "full"))

    root_gpa = CacheCfg.from_dict(d["root_gpa"]) if "root_gpa" in d else CacheCfg(1, "full", enabled=False)
    table = CacheCfg.from_dict(d["table_gpa"]) if "table_gpa" in d else CacheCfg(16, "full", enabled=False)
    g = GPWCCfg(
        vm_l2=_gstage(max(1, root_gpa.entries), root_gpa.enabled),   # root-table G translation
        vm_l1=_gstage(max(1, table.entries), table.enabled),         # guest L1-table G translation
        vm_l0=_gstage(max(1, table.entries), table.enabled),         # guest leaf-table G translation
        root=PWCLevelCfg(1, "full", enabled=root_gpa.enabled or table.enabled),
    )
    s2 = CacheCfg.from_dict(d["s2_pwc"]) if "s2_pwc" in d else CacheCfg(8, "full", enabled=False)
    data = CacheCfg.from_dict(d["data_gpa"]) if "data_gpa" in d else CacheCfg(64, 4, enabled=False)
    gf = GFinalCfg(
        l2=PWCLevelCfg(max(1, s2.entries), "full", enabled=s2.enabled),
        l1=PWCLevelCfg(max(1, s2.entries), "full", enabled=s2.enabled),
        l0=PWCLevelCfg(max(1, data.entries), data.assoc, enabled=data.enabled),
    )
    return vm, g, gf




@dataclass
class CachesCfg:
    # IOTLB: fully-resolved IOVA->SPA (== G-final-L0 made VPN-hittable).
    iotlb: CacheCfg = field(default_factory=lambda: CacheCfg(64, 4))
    # VM-stage (guest) PWC: L0(leaf)/L1/L2 + root.
    vm_pwc: VMPWCCfg = field(default_factory=VMPWCCfg)
    # G-stage PWC for guest-table-page GPAs, separated per VM level (G-Lx@VM-Ly).
    g_pwc: GPWCCfg = field(default_factory=GPWCCfg)
    # G-stage PWC for the final data GPA: G-final-L0/L1/L2.
    g_final: GFinalCfg = field(default_factory=GFinalCfg)
    ddtc: CacheCfg = field(default_factory=lambda: CacheCfg(1, "full"))
    pdtc: CacheCfg = field(default_factory=lambda: CacheCfg(1, "full", enabled=False))
    msi: CacheCfg = field(default_factory=lambda: CacheCfg(16, "full", enabled=False))
    lookup_mode: str = "hybrid"      # parallel / sequential / hybrid
    coalesce_factor: int = 8         # 64B line / 8B PTE = 8

    @classmethod
    def from_dict(cls, d):
        if d is None:
            return cls()
        d = dict(d)
        b = cls()
        vm_pwc = VMPWCCfg.from_dict(d["vm_pwc"]) if "vm_pwc" in d else None
        g_pwc = GPWCCfg.from_dict(d["g_pwc"]) if "g_pwc" in d else None
        g_final = GFinalCfg.from_dict(d["g_final"]) if "g_final" in d else None
        # legacy flat keys -> new structure (explicit new-style keys win)
        if any(k in d for k in ("s1_pwc", "s2_pwc", "root_gpa", "table_gpa", "data_gpa")):
            lvm, lg, lgf = _legacy_caches(d)
            vm_pwc = vm_pwc or lvm
            g_pwc = g_pwc or lg
            g_final = g_final or lgf
        return cls(
            iotlb=CacheCfg.from_dict(d.get("iotlb")) if "iotlb" in d else b.iotlb,
            vm_pwc=vm_pwc or b.vm_pwc,
            g_pwc=g_pwc or b.g_pwc,
            g_final=g_final or b.g_final,
            ddtc=CacheCfg.from_dict(d.get("ddtc")) if "ddtc" in d else b.ddtc,
            pdtc=CacheCfg.from_dict(d.get("pdtc")) if "pdtc" in d else b.pdtc,
            msi=CacheCfg.from_dict(d.get("msi")) if "msi" in d else b.msi,
            lookup_mode=str(d.get("lookup_mode", "hybrid")),
            coalesce_factor=int(d.get("coalesce_factor", 8)),
        )


@dataclass
class WalkersCfg:
    num_walkers: Optional[int] = None     # None = unlimited -> measure required N (3c)
    pipeline_depth: int = 2

    @classmethod
    def from_dict(cls, d):
        if d is None:
            return cls()
        d = dict(d)
        nw = d.get("num_walkers", None)
        return cls(num_walkers=None if nw is None else int(nw),
                   pipeline_depth=int(d.get("pipeline_depth", 2)))


@dataclass
class BuffersCfg:
    iommu_req_buffer: Optional[int] = None    # None = unlimited -> measure peak (3d)
    io_bridge_buffer: int = 16

    @classmethod
    def from_dict(cls, d):
        if d is None:
            return cls()
        d = dict(d)
        rb = d.get("iommu_req_buffer", None)
        ib = d.get("io_bridge_buffer", 16)
        return cls(iommu_req_buffer=None if rb is None else int(rb),
                   io_bridge_buffer=None if ib is None else int(ib))


@dataclass
class PrefetchCfg:
    algo: str = "off"                  # off/next_line/stride/rpt/dcpt/sms
    distance: int = 16
    confidence: int = 2

    @classmethod
    def from_dict(cls, d):
        if d is None:
            return cls()
        d = dict(d)
        return cls(algo=str(_norm_off(d.get("algo", "off"))),
                   distance=int(d.get("distance", 16)),
                   confidence=int(d.get("confidence", 2)))


@dataclass
class MemoryCfg:
    latency_cycles: int = 40           # 100 ns / 2.5 ns
    max_outstanding: Optional[int] = None
    bank_parallel: bool = True
    coalescing_effective: bool = True

    @classmethod
    def from_dict(cls, d):
        if d is None:
            return cls()
        d = dict(d)
        mo = d.get("max_outstanding", None)
        return cls(latency_cycles=int(d.get("latency_cycles", 40)),
                   max_outstanding=None if mo is None else int(mo),
                   bank_parallel=bool(d.get("bank_parallel", True)),
                   coalescing_effective=bool(d.get("coalescing_effective", True)))


@dataclass
class TimingCfg:
    clock_mhz: float = 400.0
    lookup_cycles: int = 2
    arbitration_cycles: int = 1
    hit_latency_cycles: int = 1

    @property
    def cycle_ns(self) -> float:
        return 1000.0 / self.clock_mhz

    @classmethod
    def from_dict(cls, d):
        if d is None:
            return cls()
        d = dict(d)
        return cls(clock_mhz=float(d.get("clock_mhz", 400.0)),
                   lookup_cycles=int(d.get("lookup_cycles", 2)),
                   arbitration_cycles=int(d.get("arbitration_cycles", 1)),
                   hit_latency_cycles=int(d.get("hit_latency_cycles", 1)))


@dataclass
class InvalidationCfg:
    rate: float = 0.0                  # events per translation (0 = none)
    target: str = "s1"                 # s1 / s2 / both
    granularity: str = "context"       # page / range / context

    @classmethod
    def from_dict(cls, d):
        if d is None:
            return cls()
        d = dict(d)
        return cls(rate=float(d.get("rate", 0.0)),
                   target=str(d.get("target", "s1")),
                   granularity=str(d.get("granularity", "context")))


@dataclass
class WorkloadCfg:
    iova_pattern: str = "sequential"   # sequential / stride / random
    stride: int = 1                    # pages, for iova_pattern == stride
    data_gpa: str = "sequential"       # sequential / random  (guest-buffer GPA contiguity)
    n_requests: int = 8000
    wire_gbs: float = 100.0
    page_bytes: int = 4096
    invalidation: InvalidationCfg = field(default_factory=InvalidationCfg)
    fault_rate: float = 0.0
    context_switch_rate: float = 0.0
    n_devices: int = 1
    n_pasids: int = 1
    span_pages: int = 1_000_000        # address span for random pattern
    seed: int = 0

    @classmethod
    def from_dict(cls, d):
        if d is None:
            return cls()
        d = dict(d)
        return cls(
            iova_pattern=str(d.get("iova_pattern", "sequential")),
            stride=int(d.get("stride", 1)),
            data_gpa=str(d.get("data_gpa", "sequential")),
            n_requests=int(d.get("n_requests", 8000)),
            wire_gbs=float(d.get("wire_gbs", 100.0)),
            page_bytes=int(d.get("page_bytes", 4096)),
            invalidation=InvalidationCfg.from_dict(d.get("invalidation")),
            fault_rate=float(d.get("fault_rate", 0.0)),
            context_switch_rate=float(d.get("context_switch_rate", 0.0)),
            n_devices=int(d.get("n_devices", 1)),
            n_pasids=int(d.get("n_pasids", 1)),
            span_pages=int(d.get("span_pages", 1_000_000)),
            seed=int(d.get("seed", 0)),
        )


@dataclass
class PACfg:
    scale_factor: Optional[float] = None    # None = normalized; else absolutize area/energy

    @classmethod
    def from_dict(cls, d):
        if d is None:
            return cls()
        d = dict(d)
        sf = d.get("scale_factor", None)
        return cls(scale_factor=None if sf is None else float(sf))


# --------------------------------------------------------------------------
# top-level config
# --------------------------------------------------------------------------
@dataclass
class Config:
    mode: str = "nested"                # bare / s1_only / s2_only / nested
    superpage: str = "off"              # off / 2M / 1G
    caches: CachesCfg = field(default_factory=CachesCfg)
    walkers: WalkersCfg = field(default_factory=WalkersCfg)
    buffers: BuffersCfg = field(default_factory=BuffersCfg)
    prefetch: PrefetchCfg = field(default_factory=PrefetchCfg)
    memory: MemoryCfg = field(default_factory=MemoryCfg)
    timing: TimingCfg = field(default_factory=TimingCfg)
    workload: WorkloadCfg = field(default_factory=WorkloadCfg)
    pa: PACfg = field(default_factory=PACfg)
    name: str = "run"

    # ---- derived timing numbers (kept handy, not stored params) ----
    @property
    def cycle_ns(self) -> float:
        return self.timing.cycle_ns

    @property
    def inter_arrival_cycles(self) -> float:
        """Wire-rate inter-arrival in cycles: page_bytes / wire / cycle_time."""
        ia_ns = self.workload.page_bytes / (self.workload.wire_gbs * 1e9) * 1e9
        return ia_ns / self.cycle_ns

    @property
    def target_throughput_mps(self) -> float:
        """Target translations per second (M/s) to sustain wire rate."""
        ia_ns = self.workload.page_bytes / (self.workload.wire_gbs * 1e9) * 1e9
        return 1e9 / ia_ns / 1e6

    # ---- construction ----
    @classmethod
    def from_dict(cls, d):
        d = dict(d or {})
        return cls(
            mode=str(d.get("mode", "nested")),
            superpage=str(_norm_off(d.get("superpage", "off"))),
            caches=CachesCfg.from_dict(d.get("caches")),
            walkers=WalkersCfg.from_dict(d.get("walkers")),
            buffers=BuffersCfg.from_dict(d.get("buffers")),
            prefetch=PrefetchCfg.from_dict(d.get("prefetch")),
            memory=MemoryCfg.from_dict(d.get("memory")),
            timing=TimingCfg.from_dict(d.get("timing")),
            workload=WorkloadCfg.from_dict(d.get("workload")),
            pa=PACfg.from_dict(d.get("pa")),
            name=str(d.get("name", "run")),
        )

    @classmethod
    def load(cls, path):
        with open(path) as f:
            text = f.read()
        if path.endswith(".json"):
            d = json.loads(text)
        else:
            import yaml
            d = yaml.safe_load(text)
        cfg = cls.from_dict(d)
        if cfg.name == "run":
            cfg.name = os.path.splitext(os.path.basename(path))[0]
        return cfg

    def to_dict(self):
        return _to_plain(self)

    def copy(self):
        return Config.from_dict(self.to_dict())


def _to_plain(obj):
    if is_dataclass(obj):
        return {f.name: _to_plain(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, dict):
        return {k: _to_plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_plain(v) for v in obj]
    return obj
