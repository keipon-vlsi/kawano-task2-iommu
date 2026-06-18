"""Shared cocotb happy-path TB for the parameterized nested IOMMU core.

Builds a self-consistent nested (VS-stage + G-stage) page table in a stub SPA
memory and drives a sequential contiguous-IOVA trace (4 KB stride). The G-stage is
an identity map (SPN == GPN) so every nested walk step is still exercised, while the
expected data SPA stays trivially checkable: SPA = (vpn + DATA_GPA_BASE) << 12.

The test is parameterized entirely by environment variables (set by each config's
run.py) so one test file covers all five configurations:
  CFG_NAME, CO (coalesce), NUM_WALKERS, BUFFER, HAS_PWC, HAS_IOTLB, PREFETCH,
  TAG_CONTEXT, N_REQS, MEM_LATENCY.
Checks: every translation yields the correct SPA, and the sustained (warmed)
throughput meets the wire rate at the config's walker/buffer counts.
"""
import os
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles

# ---- geometry (must match iommu_pkg) ----
VPN_W, GVPN_W, OFFSET_W = 27, 27, 12
IDX = 9

# ---- config from env ----
CFG        = os.environ.get("CFG_NAME", "cfg")
CO         = int(os.environ.get("CO", "8"))
N_REQS     = int(os.environ.get("N_REQS", "256"))
MEM_LAT    = int(os.environ.get("MEM_LATENCY", "40"))
HAS_PWC    = int(os.environ.get("HAS_PWC", "1"))
HAS_IOTLB  = int(os.environ.get("HAS_IOTLB", "1"))
DEV, PASID = 1, 1
MEM_TXN    = [0]   # main-memory read transactions (AR handshakes); 1 burst = 1 txn
MEM_BEATS  = [0]   # data beats returned (8 B each); coalesced leaf burst = 8 beats

# wire rate: 100 GB/s, 4 KB pages -> 40.96 ns/translation; clk 2.5 ns -> 16.384 cyc
INTER_ARRIVAL_CYC = 40.96 / 2.5

# ---- SPA layout (PPNs; identity G-stage => GPN == SPN, table lives at its PPN) ----
VML2_BASE      = 0x00100        # VS-stage root table (vs_root_spa)
GL2_BASE       = 0x00200        # G-stage root table (g_root_spa)
DATA_GPA_BASE  = 0x40000        # data GPN base (multiple of 8 -> coalescing aligns)

MEM = {}                         # byte address -> 64-bit PTE


def _split(x):
    return (x >> 18) & 0x1ff, (x >> 9) & 0x1ff, x & 0x1ff


def _set_pte(table_ppn, idx, target_ppn, leaf):
    addr = (table_ppn << OFFSET_W) | (idx << 3)
    flags = 0xF if leaf else 0x1
    MEM[addr] = ((target_ppn & ((1 << 44) - 1)) << 10) | flags


# allocators for the various table levels (distinct PPN ranges, no overlap)
_alloc = {"vml1": 0x1000, "vml0": 0x4000, "gl1": 0x8000, "gl0": 0xC000}


def _new(kind):
    p = _alloc[kind]
    _alloc[kind] += 1
    return p


_g_l1 = {}        # gvpn2 -> G-L1 table ppn
_g_l0 = {}        # (gvpn2,gvpn1) -> G-L0 table ppn
_g_done = set()


def ensure_gtrans(gpn):
    """Build G-stage tables so the DUT can translate `gpn` -> spn == gpn (identity)."""
    if gpn in _g_done:
        return
    _g_done.add(gpn)
    g2, g1, g0 = _split(gpn)
    if g2 not in _g_l1:
        _g_l1[g2] = _new("gl1")
        _set_pte(GL2_BASE, g2, _g_l1[g2], leaf=False)
    l1 = _g_l1[g2]
    if (g2, g1) not in _g_l0:
        _g_l0[(g2, g1)] = _new("gl0")
        _set_pte(l1, g1, _g_l0[(g2, g1)], leaf=False)
    _set_pte(_g_l0[(g2, g1)], g0, gpn, leaf=True)     # identity leaf


_vm_l1 = {}       # v2 -> VM-L1 table gpn(=spn)
_vm_l0 = {}       # (v2,v1) -> VM-L0 table gpn


def build(vpn):
    v2, v1, v0 = _split(vpn)
    if v2 not in _vm_l1:
        _vm_l1[v2] = _new("vml1")
        _set_pte(VML2_BASE, v2, _vm_l1[v2], leaf=False)   # VM-L2 PTE -> VM-L1 table GPA
        ensure_gtrans(_vm_l1[v2])
    l1 = _vm_l1[v2]
    if (v2, v1) not in _vm_l0:
        _vm_l0[(v2, v1)] = _new("vml0")
        _set_pte(l1, v1, _vm_l0[(v2, v1)], leaf=False)    # VM-L1 PTE -> VM-L0 table GPA
        ensure_gtrans(_vm_l0[(v2, v1)])
    l0 = _vm_l0[(v2, v1)]
    data_gpn = vpn + DATA_GPA_BASE
    _set_pte(l0, v0, data_gpn, leaf=True)                 # VM-L0 leaf -> data GPA
    ensure_gtrans(data_gpn)


def expected_spa(vpn):
    return (vpn + DATA_GPA_BASE) << OFFSET_W


async def mem_stub(dut):
    """8 B data bus. A read transaction waits MEM_LAT cycles (latency counts down
    concurrently for all in-flight reads), then streams its beats one per cycle on the
    single R channel (1 beat for a walk-step PTE, 8 beats for a coalesced 64 B leaf
    line). rlast marks the final beat; beat j of a burst = PTE at line_base + j*8."""
    dut.arready.value = 1
    dut.rvalid.value = 0
    dut.rlast.value = 0
    waiting = []     # [latency, addr, id, beats]
    ready = []       # [addr, id, beats]
    streaming = None # [addr, id, beats, sent]
    while True:
        await RisingEdge(dut.clk)
        if int(dut.arvalid.value) and int(dut.arready.value):
            beats = int(dut.arlen.value) + 1
            waiting.append([MEM_LAT, int(dut.araddr.value), int(dut.arid.value), beats])
            MEM_TXN[0] += 1; MEM_BEATS[0] += beats
        for w in waiting:
            w[0] -= 1
        still = []
        for w in waiting:
            (ready.append(w[1:]) if w[0] <= 0 else still.append(w))
        waiting = still
        dut.rvalid.value = 0
        dut.rlast.value = 0
        if streaming is None and ready:
            a, i, b = ready.pop(0)
            streaming = [a, i, b, 0]
        if streaming is not None:
            addr, id_, beats, sent = streaming
            dut.rvalid.value = 1
            dut.rdata.value = MEM.get(addr + sent * 8, 0) & ((1 << 64) - 1)
            dut.rid.value = id_
            dut.rlast.value = 1 if sent == beats - 1 else 0
            streaming[3] += 1
            if streaming[3] == beats:
                streaming = None


@cocotb.test()
async def wire_rate(dut):
    for vpn in range(N_REQS):
        build(vpn)

    cocotb.start_soon(Clock(dut.clk, 2.5, "ns").start())
    dut.rst_n.value = 0
    for s in ["pl_valid", "req_valid", "req_vpn", "req_device_id", "req_pasid",
              "req_is_write", "rvalid", "pl_sel", "pl_data"]:
        getattr(dut, s).value = 0
    dut.rsp_ready.value = 1
    dut.arready.value = 1
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)

    # preload roots: sel0 = vs_root_spa, sel1 = g_root_spa
    dut.pl_valid.value = 1
    dut.pl_sel.value = 0; dut.pl_data.value = VML2_BASE
    await RisingEdge(dut.clk)
    dut.pl_sel.value = 1; dut.pl_data.value = GL2_BASE
    await RisingEdge(dut.clk)
    dut.pl_valid.value = 0
    await RisingEdge(dut.clk)

    cocotb.start_soon(mem_stub(dut))

    cyc = [0]

    async def ticker():
        while True:
            await RisingEdge(dut.clk)
            cyc[0] += 1

    cocotb.start_soon(ticker())

    got = {}        # vpn -> (spa, cycle)

    async def collector():
        while True:
            await RisingEdge(dut.clk)
            if int(dut.rsp_valid.value) and int(dut.rsp_ready.value):
                got[int(dut.rsp_vpn.value)] = (int(dut.rsp_spa.value), cyc[0])

    cocotb.start_soon(collector())

    # drive sequential IOVA, honoring req_ready backpressure
    dut.req_device_id.value = DEV
    dut.req_pasid.value = PASID
    accept_cyc = {}
    MEM_TXN[0] = 0; MEM_BEATS[0] = 0   # count memory accesses over the request stream
    for vpn in range(N_REQS):
        dut.req_vpn.value = vpn
        dut.req_valid.value = 1
        await RisingEdge(dut.clk)
        while not int(dut.req_ready.value):
            await RisingEdge(dut.clk)
        accept_cyc[vpn] = cyc[0]
    dut.req_valid.value = 0

    # wait for all responses
    timeout = N_REQS * (MEM_LAT + 20) + 6000
    for _ in range(timeout):
        await RisingEdge(dut.clk)
        if len(got) >= N_REQS:
            break

    # ---- correctness ----
    assert len(got) == N_REQS, f"{CFG}: only {len(got)}/{N_REQS} responses"
    bad = [(v, hex(got[v][0]), hex(expected_spa(v))) for v in range(N_REQS)
           if got[v][0] != expected_spa(v)]
    assert not bad, f"{CFG}: wrong SPA for {len(bad)} vpns, e.g. {bad[:5]}"

    # ---- coalescing (walker launches) ----
    walks = int(dut.walks_o.value)
    PREFETCH = int(os.environ.get("PREFETCH", "0"))
    exp_lines = (N_REQS + CO - 1) // CO
    dut._log.info(f"{CFG}: walks={walks}, expected coalesced lines~={exp_lines} (CO={CO})")
    if CO > 1 and HAS_IOTLB:
        # prefetch adds a few startup-transient walks before it gets ahead of demand
        bound = exp_lines + (10 if PREFETCH else 2)
        assert exp_lines - 2 <= walks <= bound, \
            f"{CFG}: walks {walks} outside coalesced range ~{exp_lines} (CO={CO})"

    # ---- sustained wire rate (warmed window: 2nd half of the trace) ----
    half = N_REQS // 2
    resp_cycles = sorted(got[v][1] for v in range(N_REQS))
    span = resp_cycles[-1] - resp_cycles[half]
    n_steady = (N_REQS - 1) - half
    cyc_per = span / n_steady
    dut._log.info(f"{CFG}: steady-state {cyc_per:.2f} cyc/translation "
                  f"(wire rate = {INTER_ARRIVAL_CYC:.2f} cyc; lower is better)")
    assert cyc_per <= INTER_ARRIVAL_CYC * 1.10, \
        f"{CFG}: wire rate NOT met: {cyc_per:.2f} > {INTER_ARRIVAL_CYC:.2f} cyc/translation"

    dut._log.info(f"{CFG}: mem accesses = {MEM_TXN[0]/N_REQS:.3f} txn/translation, "
                  f"{MEM_BEATS[0]/N_REQS:.3f} beats/translation "
                  f"({MEM_TXN[0]} txn / {MEM_BEATS[0]} beats over {N_REQS} reqs)")
    dut._log.info(f"{CFG}: PASS  ({N_REQS} translations, all SPA correct, wire rate met)")
