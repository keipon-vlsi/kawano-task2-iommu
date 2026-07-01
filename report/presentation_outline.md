# TASK2 presentation outline

## Abstract
- Goal of TASK2
    - Get familiar with performance sensitivity
    - Bridge the gap b/w RTL and logic synthesis
- Acheivement
    - Meets wire rate with small cache + few walkers + prefetch
    - PPA measured on sky130

## Problem & interpretation  (short)
- Fast NIC (800GbE) + slow memory (AXI ~100ns) → translation can bottleneck.
- Reframe: wire rate = a LATENCY problem (Little's law), clock-independent.
- Scope note: start after DDTC/PDTC hit (context cache out of scope).

## Approach to meet wire rate
- L1 General (works on any workload):
    - Hide latency → parallel walk + buffer  (each request still waits)
    - Reduce accesses / translation → PWC, IOTLB
- L2 Workload-aware (conditional → state assumption + fallback):
    - Coalescing (contiguous PTEs / DRAM burst)
    - Prefetch (contiguous IOVA) → removes the wait → shrinks buffer
    - Superpage (OS large pages)
- Highlight: parallel = HIDE latency; prefetch = ELIMINATE it (buffer disappears).

## Proposed u-architeccture
- ONE parameterized design (block diagram): walkers + buffer/MSHR
  + PWC (combined VM / split G) + IOTLB + prefetch.
- Knobs = the configs we will evaluate: ±PWC, ±IOTLB, ±coalescing, ±prefetch, N, buffer, ±context tags.
- Bridge line: "We evaluate these configs in simulation (meet wire rate? how many walkers/buffers?),
  then synthesize the ones that do."

## Simulation
- Structure: cycle-approx flow, delay model (ns→cycles), scope (no physical timing).
- Results:
    - walkers vs throughput → required N (no-cache 37 → PWC 5 → +coalescing 1)
    - buffer vs throughput → required buffer
    - config summary table (N / buffer per config)
    - one takeaway per plot


## Functional-Model to RTL to Post-Synth
- Purpose: simulation proves it works *architecturally*; synthesis reveals the *physical* cost
  (area/power), timing (Fmax), and the critical path.
- Flow:
    1. one parameterized RTL → synthesize each wire-rate config (sky130)
    2. PPA comparison (area/power/Fmax); key finding (buffer dominates / tag removal / fewer walkers)
    3. pick best config → locate critical path → cut logic depth / pipeline → before-after Fmax
    4. tie back: wire rate is met even at the achieved Fmax (it is ns-bound)

## Conclusion

## Appendix
- Pipelining for Fmax / sky130 headroom
- Sensitivity (random IOVA, invalidation, multi-context)