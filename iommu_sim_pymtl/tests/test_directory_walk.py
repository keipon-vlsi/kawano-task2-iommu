"""Tests for the optional DDTW / PDTW directory-table walks.

These pin `nested` explicitly so they are independent of the SimConfig default.
"""
from iommu_sim_pymtl import (
    SimConfig, IOTLBCfg, PWCCfg, PrefetchCfg, TraceCfg, DirCacheCfg,
    SetAssocCache, SingleStageCost, DirectoryWalkCost, run_simulation,
)


N = 2000


def _cfg(**kw):
    base = dict(
        label="dir",
        iotlb=IOTLBCfg(sets=1, assoc=256, policy="lru"),
        pwc=PWCCfg(sets=1, assoc=16, policy="lru"),
        coalesce_factor=8,
        prefetcher=PrefetchCfg(kind="none"),
        trace=TraceCfg(kind="sequential", n=N),
    )
    base.update(kw)
    return SimConfig(**base)


# ---- miss-cost helpers ---------------------------------------------------

def test_ddtw_miss_cost_is_3_regardless_of_nesting():
    assert _cfg(nested=False).ddtw_miss_accesses() == 3
    assert _cfg(nested=True).ddtw_miss_accesses() == 3


def test_pdtw_miss_cost_3_single_15_nested():
    assert _cfg(nested=False).pdtw_miss_accesses() == 3
    assert _cfg(nested=True).pdtw_miss_accesses() == 15
    # parameterisable depths
    assert _cfg(nested=True, pdt_levels=2, levels=2).pdtw_miss_accesses() == 8


# ---- toggles are independent --------------------------------------------

def test_toggles_off_by_default_no_directory_walks():
    m, _ = run_simulation(_cfg(nested=False))
    assert m.ddtw_walks == 0 and m.pdtw_walks == 0


def test_cost_model_adds_exact_accesses():
    """Unit test the cost model directly (no engine timing perturbation)."""
    pwc = SetAssocCache(num_sets=1, assoc=0)        # always miss -> base = 3 accesses
    cm = DirectoryWalkCost(
        SingleStageCost(coalesce=8),
        ddtw_enabled=True, pdtw_enabled=True,
        ddt_cache=SetAssocCache(num_sets=1, assoc=None),
        pdt_cache=SetAssocCache(num_sets=1, assoc=None),
        ddt_miss=3, pdt_miss=15)
    # 1st walk: cold DDT + cold PDT -> 3 (base) + 3 + 15
    assert cm.cost(0, pwc).accesses == 3 + 3 + 15
    # 2nd walk, same (device, process) -> both directory caches hit -> base only
    assert cm.cost(8, pwc).accesses == 3


def test_ddtw_only():
    base = run_simulation(_cfg(nested=False))[0]
    m, _ = run_simulation(_cfg(nested=False, ddtw_enabled=True))
    assert m.ddtw_walks == 1           # single device -> one cold DDTW
    assert m.pdtw_walks == 0           # PDTW stays off (independent toggle)
    assert m.mem_accesses > base.mem_accesses   # directory walk adds traffic


def test_pdtw_only_independent_of_ddtw():
    m_single, _ = run_simulation(_cfg(nested=False, pdtw_enabled=True))
    assert m_single.pdtw_walks == 1 and m_single.ddtw_walks == 0

    m_nested, _ = run_simulation(_cfg(nested=True, pdtw_enabled=True))
    assert m_nested.pdtw_walks == 1 and m_nested.ddtw_walks == 0
    # nested PDTW costs more per miss than single-stage -> strictly more traffic
    base_s = run_simulation(_cfg(nested=False, pdtw_enabled=True))[0]
    assert m_nested.mem_accesses > base_s.mem_accesses


def test_both_enabled():
    m, _ = run_simulation(_cfg(nested=True, ddtw_enabled=True, pdtw_enabled=True))
    assert m.ddtw_walks == 1 and m.pdtw_walks == 1


# ---- multi-context exercises the directory caches ------------------------

def test_multi_context_triggers_repeated_walks():
    # 3 devices, 2 processes, rotate context every 50 walks.
    m, _ = run_simulation(_cfg(
        nested=True, ddtw_enabled=True, pdtw_enabled=True,
        trace=TraceCfg(kind="sequential", n=N, num_devices=3,
                       num_processes=2, ctx_switch_every=50)))
    # more than one cold walk now, and the caches register hits too
    assert m.ddtw_walks >= 2
    assert m.pdtw_walks >= m.ddtw_walks   # process keys are finer-grained
    assert m.ddt_hits > 0 and m.pdt_hits > 0


def test_disabled_directory_cache_rewalks_every_time():
    # assoc=0 disables DDT$ -> the device directory is re-walked on every walk.
    m, _ = run_simulation(_cfg(
        nested=False, ddtw_enabled=True,
        ddt=DirCacheCfg(sets=1, assoc=0)))
    # one DDTW per page-table walk (== walks_started), never a hit.
    assert m.ddtw_walks == m.walks_started
    assert m.ddt_hits == 0
