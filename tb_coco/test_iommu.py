"""cocotb happy-path testbench for iommu_core (Phase 1).

Drives a sequential translation trace into the DUT, models an AXI-like memory
stub that returns PTEs after MEM_LATENCY_CYCLES, pre-loads the S1 PWC for
steady state, checks every translation completes with the correct SPA, and
cross-checks the RTL hit/miss/walk counts against the Python reference sim.

Address model: a leaf PTE read for vpn returns the coalesced LINE base PPN
(= (vpn & ~(C-1)) + PA_BASE); the front-end adds the page offset-within-line, so
SPA(vpn) = (vpn + PA_BASE) << 12. The walk takes nreads chained reads; only the
last (leaf) return matters for the SPA.
"""
import os
import sys

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles

# fixed widths (mirror iommu_pkg)
VPN_W = 27
PPN_W = 28
SPA_W = 40
CTX_W = 50
OFFSET_W = 12
PA_BASE = 0x10000           # arbitrary host-physical base for the stub mapping

# config under test (must match the runner's build parameters)
COALESCE = int(os.environ.get("COALESCE_FACTOR", "8"))
N_REQS = int(os.environ.get("N_REQS", "256"))
MEM_LATENCY = int(os.environ.get("MEM_LATENCY", "40"))


def line_base(vpn):
    return vpn & ~(COALESCE - 1)


def expected_spa(vpn):
    return (vpn + PA_BASE) << OFFSET_W


async def mem_stub(dut):
    """AXI-like read slave: accept AR every cycle, return R after MEM_LATENCY.
    rdata = line-base PPN decoded from araddr (araddr low VPN_W bits = vpn)."""
    dut.arready.value = 1
    dut.rvalid.value = 0
    pending = []          # list of [cycles_left, data, tag]
    cyc = 0
    while True:
        await RisingEdge(dut.clk)
        cyc += 1
        # capture an accepted AR
        if int(dut.arvalid.value) and int(dut.arready.value):
            araddr = int(dut.araddr.value)
            vpn = araddr & ((1 << VPN_W) - 1)
            data = (line_base(vpn) + PA_BASE) & ((1 << PPN_W) - 1)
            pending.append([MEM_LATENCY, data, int(dut.arid.value)])
        # advance timers; drive at most one R per cycle
        dut.rvalid.value = 0
        fire = None
        for p in pending:
            p[0] -= 1
        ready = [p for p in pending if p[0] <= 0]
        if ready:
            fire = ready[0]
            pending.remove(fire)
            dut.rvalid.value = 1
            dut.rdata.value = fire[1]
            dut.rid.value = fire[2]


async def preload_pwc(dut):
    """Warm the S1 PWC for every ~2 MB region the trace will touch (steady state):
    pwc_key = {ctx(=0), {9'b0, vpn[26:9]}}."""
    dut.pl_valid.value = 0
    regions = sorted({vpn >> 9 for vpn in range(N_REQS)})
    for r in regions:
        key = r & ((1 << (VPN_W - 9)) - 1)        # ctx=0 in the high bits
        dut.pl_valid.value = 1
        dut.pl_sel.value = 1                       # 1 = s1 pwc
        dut.pl_key.value = key
        dut.pl_data.value = 0
        await RisingEdge(dut.clk)
    dut.pl_valid.value = 0
    await RisingEdge(dut.clk)


def reference_counts():
    """Run the Python reference sim on the same single-stage sequential workload
    and return (walks, completions). Used for the sim<->RTL cross-check."""
    sim_dir = os.path.join(os.path.dirname(__file__), "..", "iommu_sim")
    sys.path.insert(0, os.path.abspath(sim_dir))
    from config import Config
    from runner import run_sim
    cfg = Config.from_dict({
        "mode": "bare",
        "caches": {"iotlb": {"entries": 64, "assoc": 4},
                   "s1_pwc": {"l2": {"entries": 8}, "l1": {"entries": 16}},
                   "s2_pwc": {"enabled": False}, "table_gpa": {"enabled": False},
                   "data_gpa": {"enabled": False}, "ddtc": {"entries": 4},
                   "pdtc": {"enabled": False}, "msi": {"enabled": False},
                   "coalesce_factor": COALESCE},
        "walkers": {"num_walkers": None}, "buffers": {"iommu_req_buffer": None, "io_bridge_buffer": None},
        "workload": {"iova_pattern": "sequential", "n_requests": N_REQS},
    })
    sim, m = run_sim(cfg, warmup_frac=0.0)
    return m.walks_started, m.completed


@cocotb.test()
async def happy_path(dut):
    cocotb.start_soon(Clock(dut.clk, 2.5, "ns").start())   # 400 MHz
    # reset
    dut.rst_n.value = 0
    dut.req_valid.value = 0
    dut.req_vpn.value = 0
    dut.req_device_id.value = 0
    dut.req_pasid.value = 0
    dut.req_vmid.value = 0
    dut.req_is_write.value = 0
    dut.rsp_ready.value = 1
    dut.pl_valid.value = 0
    dut.arready.value = 1
    dut.rvalid.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)

    cocotb.start_soon(mem_stub(dut))
    await preload_pwc(dut)

    # collect responses
    got_spa = []

    async def collector():
        while True:
            await RisingEdge(dut.clk)
            if int(dut.rsp_valid.value) and int(dut.rsp_ready.value):
                got_spa.append(int(dut.rsp_spa.value))

    cocotb.start_soon(collector())

    # drive the sequential trace with ~wire-rate pacing (1 req every ~16 cycles)
    for vpn in range(N_REQS):
        dut.req_vpn.value = vpn
        dut.req_valid.value = 1
        await RisingEdge(dut.clk)
        while not int(dut.req_ready.value):     # honour back-pressure
            await RisingEdge(dut.clk)
        dut.req_valid.value = 0
        await ClockCycles(dut.clk, 15)          # inter-arrival ~= 16 cycles
    dut.req_valid.value = 0

    # drain
    for _ in range(N_REQS * MEM_LATENCY + 2000):
        await RisingEdge(dut.clk)
        if len(got_spa) >= N_REQS:
            break

    # ---- checks ----
    assert len(got_spa) == N_REQS, f"completed {len(got_spa)} / {N_REQS}"
    exp = sorted(expected_spa(v) for v in range(N_REQS))
    assert sorted(got_spa) == exp, "SPA set mismatch (per-page translation wrong)"

    rtl_walks = int(dut.cnt_walks.value)
    rtl_hits = int(dut.cnt_iotlb_hit.value)
    rtl_coal = int(dut.cnt_coalesced.value)
    exp_lines = (N_REQS + COALESCE - 1) // COALESCE
    ref_walks, ref_completed = reference_counts()

    dut._log.info(f"RTL: walks={rtl_walks} iotlb_hit={rtl_hits} coalesced={rtl_coal} "
                  f"completed={len(got_spa)}")
    dut._log.info(f"REF sim: walks={ref_walks} completed={ref_completed}  "
                  f"expected lines={exp_lines}")

    # sim<->RTL cross-check: one walk per coalesced line (timing-independent)
    assert rtl_walks == exp_lines, f"RTL walks {rtl_walks} != lines {exp_lines}"
    assert abs(rtl_walks - ref_walks) <= 1, f"RTL walks {rtl_walks} vs sim {ref_walks}"
    # every non-leader request hit the IOTLB or coalesced on the in-flight line
    assert rtl_hits + rtl_coal == N_REQS - rtl_walks, \
        f"hit+coal {rtl_hits + rtl_coal} != {N_REQS - rtl_walks}"
    dut._log.info("happy path OK: all SPAs correct, sim<->RTL walk count matches")
