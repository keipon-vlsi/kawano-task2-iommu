"""Smoke tests: every component constructs, every scenario runs end-to-end
without crashing, and the basic counters are non-negative / consistent."""
from iommu_sim_pymtl import (
    SimConfig, IOTLBCfg, PWCCfg, PrefetchCfg, TraceCfg,
    SetAssocCache, LRU, FIFO, RandomRepl,
    NoPrefetch, NextLineStride, ConfidenceStride,
    MemoryModel, SingleStageCost, NestedCost,
    sequential, random_trace, multi_stream,
    run_simulation,
)


def test_replacement_policies_construct():
    for p in (LRU(), FIFO(), RandomRepl()):
        c = SetAssocCache(num_sets=4, assoc=2, policy=p)
        for k in range(20):
            c.lookup(k)
            c.insert(k)
        assert c.total == 20
        assert c.hits + c.misses == 20


def test_cache_disabled_assoc_zero_always_miss():
    c = SetAssocCache(num_sets=1, assoc=0, policy=LRU())
    for k in range(50):
        c.insert(k)
        assert not c.lookup(k)


def test_cache_infinite_assoc_none_never_evicts():
    c = SetAssocCache(num_sets=1, assoc=None, policy=LRU())
    for k in range(50):
        c.insert(k)
    for k in range(50):
        assert c.peek(k)


def test_prefetchers_predict_returns_list():
    assert NoPrefetch().predict(0, 0) == []
    out = NextLineStride(distance=16, coalesce=8).predict(0, 0)
    assert all(isinstance(x, int) for x in out)
    cs = ConfidenceStride(distance=4, threshold=2, coalesce=1)
    for v in (0, 1, 2, 3, 4):
        cs.predict(v, v)
    # After several monotonic deltas, confidence triggers a prediction.
    out2 = cs.predict(5, 5)
    assert isinstance(out2, list)


def test_trace_generators_have_expected_length():
    assert len(sequential(100)) == 100
    assert len(random_trace(100)) == 100
    assert len(multi_stream(100, streams=4)) == 100


def test_cost_models():
    pwc = SetAssocCache(num_sets=1, assoc=16, policy=LRU())
    p = SingleStageCost(coalesce=8).cost(123, pwc)
    assert p.accesses >= 1
    assert len(p.iotlb_keys) == 8
    pn = NestedCost(coalesce=8, s2_levels=3).cost(123, pwc)
    assert pn.accesses >= p.accesses


def test_memory_model_tracks_outstanding():
    m = MemoryModel(latency_cycles=40)
    m.issue(3); assert m.outstanding == 3 and m.peak_outstanding == 3
    m.issue(2); assert m.outstanding == 5 and m.peak_outstanding == 5
    m.retire(4); assert m.outstanding == 1 and m.peak_outstanding == 5


def test_full_scenario_runs():
    cfg = SimConfig(
        iotlb=IOTLBCfg(assoc=0), pwc=PWCCfg(assoc=0),
        coalesce_factor=1,
        prefetcher=PrefetchCfg(kind="none"),
        trace=TraceCfg(kind="sequential", n=500),
    )
    m, _ = run_simulation(cfg)
    assert m.completed == 500
    assert m.peak_walks >= 1
    assert m.peak_buffer >= 1
    assert m.mem_accesses > 0
