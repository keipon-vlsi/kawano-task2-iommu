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

    @classmethod
    def from_dict(cls, d):
        if d is None:
            return cls()
        d = dict(d)
        return cls(entries=int(d.get("entries", 8)), assoc=_assoc(d.get("assoc", "full")))


@dataclass
class S1PWCCfg:
    l2: PWCLevelCfg = field(default_factory=lambda: PWCLevelCfg(4, "full"))
    l1: PWCLevelCfg = field(default_factory=lambda: PWCLevelCfg(8, "full"))

    @classmethod
    def from_dict(cls, d):
        if d is None:
            return cls()
        d = dict(d)
        return cls(l2=PWCLevelCfg.from_dict(d.get("l2")),
                   l1=PWCLevelCfg.from_dict(d.get("l1")))


@dataclass
class CachesCfg:
    iotlb: CacheCfg = field(default_factory=lambda: CacheCfg(64, 4))
    s1_pwc: S1PWCCfg = field(default_factory=S1PWCCfg)
    s2_pwc: CacheCfg = field(default_factory=lambda: CacheCfg(8, "full"))
    table_gpa: CacheCfg = field(default_factory=lambda: CacheCfg(16, "full"))
    data_gpa: CacheCfg = field(default_factory=lambda: CacheCfg(64, 4, enabled=False))
    ddtc: CacheCfg = field(default_factory=lambda: CacheCfg(16, "full"))
    pdtc: CacheCfg = field(default_factory=lambda: CacheCfg(16, "full", enabled=False))
    msi: CacheCfg = field(default_factory=lambda: CacheCfg(16, "full"))
    lookup_mode: str = "hybrid"      # parallel / sequential / hybrid
    walk_trigger: str = "demand"     # demand / predictive
    coalesce_factor: int = 8         # 64B line / 8B PTE = 8

    @classmethod
    def from_dict(cls, d):
        if d is None:
            return cls()
        d = dict(d)
        return cls(
            iotlb=CacheCfg.from_dict(d.get("iotlb")) if "iotlb" in d else cls().iotlb,
            s1_pwc=S1PWCCfg.from_dict(d.get("s1_pwc")) if "s1_pwc" in d else cls().s1_pwc,
            s2_pwc=CacheCfg.from_dict(d.get("s2_pwc")) if "s2_pwc" in d else cls().s2_pwc,
            table_gpa=CacheCfg.from_dict(d.get("table_gpa")) if "table_gpa" in d else cls().table_gpa,
            data_gpa=CacheCfg.from_dict(d.get("data_gpa")) if "data_gpa" in d else cls().data_gpa,
            ddtc=CacheCfg.from_dict(d.get("ddtc")) if "ddtc" in d else cls().ddtc,
            pdtc=CacheCfg.from_dict(d.get("pdtc")) if "pdtc" in d else cls().pdtc,
            msi=CacheCfg.from_dict(d.get("msi")) if "msi" in d else cls().msi,
            lookup_mode=str(d.get("lookup_mode", "hybrid")),
            walk_trigger=str(d.get("walk_trigger", "demand")),
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
