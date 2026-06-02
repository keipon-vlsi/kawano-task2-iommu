"""Validation tests: assert the architectural TRENDS the prompt requires.

These do not require exact-number agreement with the reference event-driven
sim — only that orders of magnitude and direction match.
"""
import pytest

from iommu_sim_pymtl import (
    SimConfig, IOTLBCfg, PWCCfg, PrefetchCfg, TraceCfg, run_simulation,
)


N = 4000  # smaller than the demo to keep the test suite fast.


def _run(cfg):
    m, _ = run_simulation(cfg)
    n = max(1, m.completed)
    span_cycles = max(1, m.last_complete_cycle - (m.first_arrival_cycle or 0))
    span_ns = span_cycles * cfg.ns_per_cycle()
    tput = n / span_ns * 1e9 / 1e6
    tgt = cfg.target_throughput_per_s() / 1e6
    return m, tput, tgt


# ---------- A : no-cache baseline ----------

def test_A_no_cache_required_walkers_around_8():
    cfg = SimConfig(
        iotlb=IOTLBCfg(assoc=0), pwc=PWCCfg(assoc=0),
        coalesce_factor=1,
        prefetcher=PrefetchCfg(kind="none"),
        trace=TraceCfg(kind="sequential", n=N),
    )
    m, tput, tgt = _run(cfg)
    assert m.completed == N
    # ~3 mem accesses per page for a 3-level walk with no PWC.
    assert 2.9 <= m.mem_accesses / N <= 3.1
    # required walkers should be ~8 (300ns walk / 40ns inter-arrival).
    assert 6 <= m.peak_walks <= 10
    # wire rate sustained with infinite resources.
    assert tput >= 0.99 * tgt


# ---------- B : PWC + coalescing ----------

def test_B_pwc_coalescing_collapses_memory_traffic():
    cfg = SimConfig(
        iotlb=IOTLBCfg(assoc=256), pwc=PWCCfg(assoc=16),
        coalesce_factor=8,
        prefetcher=PrefetchCfg(kind="none"),
        trace=TraceCfg(kind="sequential", n=N),
    )
    m, tput, tgt = _run(cfg)
    # Should be ~0.13 mem/page (>15x drop vs no-cache).
    assert m.mem_accesses / N < 0.2
    # With caching the steady-state walker requirement collapses to ~1.
    assert m.peak_walks <= 2
    # Cold-start buffer transient remains ~8.
    assert m.peak_buffer >= 4
    assert tput >= 0.99 * tgt


# ---------- C : B + prefetch ----------

def test_C_prefetch_collapses_observed_latency():
    cfg = SimConfig(
        iotlb=IOTLBCfg(assoc=256), pwc=PWCCfg(assoc=16),
        coalesce_factor=8,
        prefetcher=PrefetchCfg(kind="nextline", distance=16, coalesce=8),
        trace=TraceCfg(kind="sequential", n=N),
    )
    m, tput, tgt = _run(cfg)
    avg_lat_ns = m.avg_lat_cycles * cfg.ns_per_cycle()
    # With prefetch warming the IOTLB, observed latency should approach hit lat.
    assert avg_lat_ns < 10.0
    assert tput >= 0.99 * tgt


# ---------- D : random IOVA ----------

def test_D_random_iova_regresses_to_no_cache_regime():
    cfg = SimConfig(
        iotlb=IOTLBCfg(assoc=256), pwc=PWCCfg(assoc=16),
        coalesce_factor=8,
        prefetcher=PrefetchCfg(kind="none"),
        trace=TraceCfg(kind="random", n=N, span_pages=1_000_000, seed=0),
    )
    m, _, _ = _run(cfg)
    # Random destroys cache locality => mem/page jumps back toward ~2-3.
    assert m.mem_accesses / N >= 1.5
    # Required walkers swing back toward the no-cache regime.
    assert m.peak_walks >= 5


# ---------- E : under-provisioned, no cache ----------

def test_E_finite_under_provisioning_fails_wire_rate():
    cfg = SimConfig(
        iotlb=IOTLBCfg(assoc=0), pwc=PWCCfg(assoc=0),
        coalesce_factor=1,
        prefetcher=PrefetchCfg(kind="none"),
        trace=TraceCfg(kind="sequential", n=N),
        num_walkers=4,
        buffer_size=4,
    )
    m, tput, tgt = _run(cfg)
    # Half the walker count => roughly half the throughput, well under wire.
    assert tput < 0.7 * tgt
    # Definitively flagged as not sustained.
    assert m.peak_walks == 4
    assert m.peak_buffer == 4


# ---------- swappability check ----------

@pytest.mark.parametrize("policy", ["lru", "fifo", "random"])
def test_replacement_policy_is_swappable(policy):
    cfg = SimConfig(
        iotlb=IOTLBCfg(assoc=64, policy=policy),
        pwc=PWCCfg(assoc=8, policy=policy),
        coalesce_factor=8,
        prefetcher=PrefetchCfg(kind="none"),
        trace=TraceCfg(kind="sequential", n=N),
    )
    m, tput, tgt = _run(cfg)
    assert m.completed == N
    assert tput >= 0.99 * tgt   # all policies should sustain on sequential


@pytest.mark.parametrize("kind", ["none", "nextline", "stride"])
def test_prefetcher_is_swappable(kind):
    cfg = SimConfig(
        iotlb=IOTLBCfg(assoc=256), pwc=PWCCfg(assoc=16),
        coalesce_factor=8,
        prefetcher=PrefetchCfg(kind=kind, distance=16, threshold=2, coalesce=8),
        trace=TraceCfg(kind="sequential", n=N),
    )
    m, tput, tgt = _run(cfg)
    assert m.completed == N
    assert tput >= 0.99 * tgt


def test_confidence_stride_disables_on_random():
    """ConfidenceStride should NOT generate excess traffic on random IOVA."""
    cfg_random_pf = SimConfig(
        iotlb=IOTLBCfg(assoc=256), pwc=PWCCfg(assoc=16),
        coalesce_factor=8,
        prefetcher=PrefetchCfg(kind="stride", distance=16, threshold=4,
                               coalesce=8),
        trace=TraceCfg(kind="random", n=N, span_pages=1_000_000, seed=0),
    )
    cfg_random_no = SimConfig(
        iotlb=IOTLBCfg(assoc=256), pwc=PWCCfg(assoc=16),
        coalesce_factor=8,
        prefetcher=PrefetchCfg(kind="none"),
        trace=TraceCfg(kind="random", n=N, span_pages=1_000_000, seed=0),
    )
    m_pf, _, _ = _run(cfg_random_pf)
    m_no, _, _ = _run(cfg_random_no)
    # Confidence stride should add at most modest extra traffic on random.
    assert m_pf.mem_accesses <= 1.5 * m_no.mem_accesses


def test_nested_translation_increases_memory_traffic():
    common = dict(
        iotlb=IOTLBCfg(assoc=256), pwc=PWCCfg(assoc=16),
        coalesce_factor=8,
        prefetcher=PrefetchCfg(kind="none"),
        trace=TraceCfg(kind="sequential", n=N),
    )
    m_s, _, _ = _run(SimConfig(nested=False, **common))
    m_n, _, _ = _run(SimConfig(nested=True, nested_s2_residual=1, **common))
    assert m_n.mem_accesses > m_s.mem_accesses
