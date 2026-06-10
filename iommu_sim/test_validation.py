"""Validation tests (design_doc §14 / build-prompt validation).

Reproduce the A-E trends and the nested/Little's-law cross-checks (orders/trends,
not exact numbers), and check estimator per-module sums and config loading.

Run:  cd iommu_sim && ../.venv/bin/python -m pytest -q
"""
import os
import math

import pytest

from config import Config
from runner import run_sim, summarize, wire_rate_met
from estimator import estimate

HERE = os.path.dirname(__file__)


def mk(mode="s1_only", cache_on=True, coalesce=8, prefetch="off", nw=None, buf=None, iob=None,
       iova="sequential", data="sequential", coal_eff=True, n=8000, data_gpa_en=False,
       inval_rate=0.0, inval_target="s1"):
    if cache_on:
        caches = {"iotlb": {"entries": 64, "assoc": 4},
                  "s1_pwc": {"l2": {"entries": 4}, "l1": {"entries": 8}},
                  "s2_pwc": {"entries": 8}, "table_gpa": {"entries": 16},
                  "data_gpa": {"enabled": data_gpa_en, "entries": 64, "assoc": 4},
                  "ddtc": {"entries": 16}, "pdtc": {"enabled": False}, "msi": {"entries": 16},
                  "coalesce_factor": coalesce}
    else:
        caches = {"iotlb": {"entries": 0},
                  "s1_pwc": {"l2": {"entries": 0}, "l1": {"entries": 0}},
                  "s2_pwc": {"entries": 0}, "table_gpa": {"entries": 0},
                  "data_gpa": {"enabled": False}, "ddtc": {"entries": 0},
                  "pdtc": {"enabled": False}, "msi": {"entries": 0}, "coalesce_factor": coalesce}
    d = {"mode": mode, "caches": caches,
         "prefetch": {"algo": prefetch, "distance": 16, "confidence": 2},
         "walkers": {"num_walkers": nw},
         "buffers": {"iommu_req_buffer": buf, "io_bridge_buffer": iob},
         "memory": {"coalescing_effective": coal_eff},
         "workload": {"n_requests": n, "iova_pattern": iova, "data_gpa": data,
                      "invalidation": {"rate": inval_rate, "target": inval_target, "granularity": "context"}}}
    return Config.from_dict(d)


def metrics(cfg, warmup=0.0):
    sim, m = run_sim(cfg, warmup_frac=warmup)
    return sim, m


# --------------------------------------------------------------------------
# A-E trends
# --------------------------------------------------------------------------
def test_A_no_cache():
    cfg = mk(cache_on=False, coalesce=1, coal_eff=False)
    sim, m = metrics(cfg)
    mem_per_pg = sim.memory.accesses / m.completed
    assert m.completed == 8000
    assert mem_per_pg == pytest.approx(3.0, abs=0.05)        # 3-level walk, no coalescing
    assert 6 <= m.peak_walks <= 10                            # Little's law ~= 8
    assert 6 <= m.peak_buffer <= 12
    assert 250 <= m.avg_lat * cfg.cycle_ns <= 360             # ~300 ns
    assert wire_rate_met(cfg, sim, m)


def test_B_pwc_coalescing():
    cfg = mk()
    sim, m = metrics(cfg)
    mem_per_pg = sim.memory.accesses / m.completed
    assert mem_per_pg < 0.2                                   # ~0.13 (1 line / 8 pages)
    assert m.peak_walks <= 3                                  # ~1 steady (coalescing)
    assert wire_rate_met(cfg, sim, m)


def test_B_far_cheaper_than_A():
    _, ma = metrics(mk(cache_on=False, coalesce=1, coal_eff=False))
    sa = ma  # noqa
    sima, _ = metrics(mk(cache_on=False, coalesce=1, coal_eff=False))
    simb, mb = metrics(mk())
    assert simb.memory.accesses < sima.memory.accesses / 10  # ~20x fewer accesses


def test_C_prefetch_collapses_latency():
    _, mb = metrics(mk())
    _, mc = metrics(mk(prefetch="next_line"))
    assert mc.avg_lat < mb.avg_lat                            # latency collapses toward hit latency
    assert mc.avg_lat * 2.5 < 20                              # only a few cycles


def test_D_random_regresses():
    cfg = mk(iova="random")
    sim, m = metrics(cfg)
    mem_per_pg = sim.memory.accesses / m.completed
    assert mem_per_pg > 1.5                                   # back toward the no-cache regime
    assert m.peak_walks >= 6


def test_E_finite_resources_stall():
    cfg = mk(cache_on=False, coalesce=1, coal_eff=False, nw=4, buf=4, iob=4)
    sim, m = metrics(cfg)
    assert m.throughput_mps(cfg.cycle_ns) < cfg.target_throughput_mps  # wire-rate cliff
    assert not wire_rate_met(cfg, sim, m)


# --------------------------------------------------------------------------
# nested ~= 2x single, Little's law
# --------------------------------------------------------------------------
def test_nested_ddtc_pdtc_only_is_15_accesses():
    # With only DDT$/PDT$ (all PTW/S2 caches disabled), after a PDT$ hit the PTW
    # is a full 2D walk starting from the root-GPA G-stage translation =
    # (3+1)(3+1)-1 = 15 memory accesses per translation.
    caches = {"iotlb": {"enabled": False}, "s1_pwc": {"l2": {"enabled": False}, "l1": {"enabled": False}},
              "s2_pwc": {"enabled": False}, "table_gpa": {"enabled": False}, "data_gpa": {"enabled": False},
              "ddtc": {"enabled": True, "entries": 1}, "pdtc": {"enabled": True, "entries": 1},
              "msi": {"enabled": False}, "coalesce_factor": 1}
    cfg = Config.from_dict({"mode": "nested", "caches": caches,
                            "workload": {"n_requests": 2000, "iova_pattern": "sequential"}})
    sim, m = metrics(cfg)
    assert sim.caches.pdtc.hits > 0                       # PDT$ hits (context cached)
    # steady-state per-translation accesses == 15 (single context: 1 cold ctx miss aside)
    assert sim.memory.accesses / m.completed == pytest.approx(15.0, abs=0.05)


def test_nested_about_2x_single():
    # the "2x" relationship needs BOTH leaf streams coalesced: guest-leaf (VM-L0)
    # and data-leaf (G-final-L0 == data_gpa). With the data-leaf cache off, the
    # per-page IOTLB correctly re-reads the data leaf and nested costs more.
    sims, ms = metrics(mk(mode="s1_only"))
    simn, mn = metrics(mk(mode="nested", data_gpa_en=True))
    single = sims.memory.accesses / ms.completed
    nested = simn.memory.accesses / mn.completed
    assert 1.5 <= nested / single <= 3.0                     # ~2x (two coalesced leaf streams)


def test_littles_law_no_cache():
    cfg = mk(cache_on=False, coalesce=1, coal_eff=False)
    sim, m = metrics(cfg)
    predicted_N = m.avg_lat / cfg.inter_arrival_cycles       # Little's law
    assert abs(m.peak_walks - predicted_N) <= 2


# --------------------------------------------------------------------------
# measure peaks (3c/3d) under infinite resources
# --------------------------------------------------------------------------
def test_measure_peaks_relationship():
    cfg = mk(mode="nested")
    cfg.walkers.num_walkers = None
    cfg.buffers.iommu_req_buffer = None
    cfg.buffers.io_bridge_buffer = None
    sim, m = run_sim(cfg, warmup_frac=0.05)
    assert m.peak_walks >= 1
    assert m.peak_buffer >= m.peak_walks                     # B >= N (design_premises §9)


# --------------------------------------------------------------------------
# invalidation sensitivity: separate data_gpa cache retains S2 across S1 inval
# --------------------------------------------------------------------------
def test_invalidation_counts():
    cfg = mk(inval_rate=0.01, inval_target="s1")
    sim, m = metrics(cfg)
    assert m.invalidations > 0


def test_data_gpa_helps_under_s1_invalidation():
    # under frequent S1 invalidation, enabling the separate data_gpa cache should
    # not increase memory accesses (it retains S2 results) -- stage separation.
    _, m_off = metrics(mk(mode="nested", inval_rate=0.05, inval_target="s1", data_gpa_en=False))
    _, m_on = metrics(mk(mode="nested", inval_rate=0.05, inval_target="s1", data_gpa_en=True))
    sim_off, _ = run_sim(mk(mode="nested", inval_rate=0.05, inval_target="s1", data_gpa_en=False))
    sim_on, _ = run_sim(mk(mode="nested", inval_rate=0.05, inval_target="s1", data_gpa_en=True))
    assert sim_on.memory.accesses <= sim_off.memory.accesses + 1


# --------------------------------------------------------------------------
# estimator: per-module sums == totals
# --------------------------------------------------------------------------
def test_estimator_per_module_sums():
    cfg = mk(mode="nested")
    sim, m = metrics(cfg)
    res = estimate(cfg, sim.caches, m, dram_accesses=sim.memory.accesses)
    assert res.area_ge == pytest.approx(sum(x.area_ge for x in res.modules))
    assert res.dyn_power == pytest.approx(sum(x.dyn for x in res.modules))
    assert res.stat_power == pytest.approx(sum(x.stat for x in res.modules))
    assert res.total_power == pytest.approx(res.dyn_power + res.stat_power)
    assert res.energy_per_translation > 0


def test_estimator_units_ge_and_normalized():
    cfg = mk(mode="nested")
    sim, m = metrics(cfg)
    res = estimate(cfg, sim.caches, m, dram_accesses=sim.memory.accesses)
    # CAM (fully-assoc) structures must carry cam_bits; SRAM structures must not.
    iotlb = next(x for x in res.modules if x.name == "iotlb")     # 4-way -> SRAM
    vm = next(x for x in res.modules if x.name == "vm_l1")        # full -> CAM
    assert iotlb.cam_bits == 0 and iotlb.sram_bits > 0
    assert vm.cam_bits > 0


# --------------------------------------------------------------------------
# config loading / normalization
# --------------------------------------------------------------------------
def test_config_load_baseline():
    # baseline.yaml is user-editable (a scratch config), so only assert robust
    # invariants: it loads and the fields have valid types/enums.
    cfg = Config.load(os.path.join(HERE, "configs", "baseline.yaml"))
    assert cfg.mode in ("bare", "s1_only", "s2_only", "nested")
    assert cfg.superpage in ("off", "2M", "1G")
    assert isinstance(cfg.caches.coalesce_factor, int) and cfg.caches.coalesce_factor >= 1


def test_config_normalization():
    # YAML 'off'/'on' coerce to bool; ensure we normalize back to enum strings,
    # null -> None, and assoc 'full' is preserved. Fixed input (not baseline.yaml).
    import yaml
    d = yaml.safe_load(
        "mode: nested\n"
        "superpage: off\n"
        "prefetch: {algo: off}\n"
        "walkers: {num_walkers: null}\n"
        "caches: {coalesce_factor: 8, s1_pwc: {l2: {entries: 4, assoc: full}}}\n"
    )
    cfg = Config.from_dict(d)
    assert cfg.superpage == "off"               # bool False -> "off"
    assert cfg.prefetch.algo == "off"
    assert cfg.walkers.num_walkers is None
    assert cfg.caches.vm_pwc.l2.assoc == "full"  # legacy s1_pwc.l2 -> vm_pwc.l2
    assert cfg.caches.coalesce_factor == 8


def test_target_throughput():
    cfg = mk()
    assert cfg.target_throughput_mps == pytest.approx(24.41, abs=0.05)
    assert cfg.inter_arrival_cycles == pytest.approx(16.384, abs=0.01)
