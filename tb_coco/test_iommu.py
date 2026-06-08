"""cocotb happy-path TB for the detailed (Sv39 pointer-chase) iommu_core.

A consistent 3-level Sv39 page table is built in the TB: each non-leaf PTE's PPN
points at the next-level table, each leaf PTE maps vpn -> (vpn+PA_BASE) (linear).
The stub memory returns the real 64 B line (8 PTEs) for any address after
MEM_LATENCY cycles. The per-context root PPN is pre-loaded into the DUT. The walk
engine chases the real pointers; we check every translation yields the correct
SPA and that the RTL walk count equals the number of coalesced leaf lines.
"""
import os
import sys

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles

VPN_W, PPN_W, OFFSET_W = 27, 28, 12
ROOT_PPN = 0x100
PA_BASE  = 0x10000
COALESCE = int(os.environ.get("COALESCE_FACTOR", "8"))
N_REQS   = int(os.environ.get("N_REQS", "256"))
MEM_LATENCY = int(os.environ.get("MEM_LATENCY", "40"))
CW = (COALESCE - 1).bit_length() if COALESCE > 1 else 0   # log2(COALESCE)

# ---- build a consistent Sv39 page table: byte-addr -> 64-bit PTE ----
MEM = {}
_l1 = {}; _leaf = {}; _next = [0x200]


def _set_pte(table_ppn, idx, next_ppn, leaf):
    addr = (table_ppn << 12) | (idx << 3)
    flags = 0xF if leaf else 0x1          # leaf: V|R|W|X ; non-leaf: V (pointer)
    MEM[addr] = ((next_ppn & ((1 << 44) - 1)) << 10) | flags


def _build(vpn):
    l2i, l1i, l0i = (vpn >> 18) & 0x1ff, (vpn >> 9) & 0x1ff, vpn & 0x1ff
    if l2i not in _l1:
        _l1[l2i] = _next[0]; _next[0] += 1
        _set_pte(ROOT_PPN, l2i, _l1[l2i], False)
    l1tab = _l1[l2i]
    if (l2i, l1i) not in _leaf:
        _leaf[(l2i, l1i)] = _next[0]; _next[0] += 1
        _set_pte(l1tab, l1i, _leaf[(l2i, l1i)], False)
    _set_pte(_leaf[(l2i, l1i)], l0i, vpn + PA_BASE, True)


def expected_spa(vpn):
    return (vpn + PA_BASE) << OFFSET_W


async def mem_stub(dut):
    """Return the 64 B line (8 PTEs) containing araddr after MEM_LATENCY cycles."""
    dut.arready.value = 1
    dut.rvalid.value = 0
    pending = []
    while True:
        await RisingEdge(dut.clk)
        if int(dut.arvalid.value) and int(dut.arready.value):
            a = int(dut.araddr.value) & ~0x3f          # 64 B line base
            line = 0
            for k in range(8):
                line |= MEM.get(a + k * 8, 0) << (k * 64)
            pending.append([MEM_LATENCY, line, int(dut.arid.value)])
        dut.rvalid.value = 0
        for p in pending:
            p[0] -= 1
        ready = [p for p in pending if p[0] <= 0]
        if ready:
            p = ready[0]; pending.remove(p)
            dut.rvalid.value = 1
            dut.rdata.value = p[1]
            dut.rid.value = p[2]


@cocotb.test()
async def happy_path(dut):
    for vpn in range(N_REQS):
        _build(vpn)
    cocotb.start_soon(Clock(dut.clk, 2.5, "ns").start())
    dut.rst_n.value = 0
    for s in ["req_valid", "req_vpn", "req_device_id", "req_pasid", "req_vmid",
              "req_is_write", "pl_valid", "pl_sel", "pl_key", "pl_data", "rvalid"]:
        getattr(dut, s).value = 0
    dut.rsp_ready.value = 1
    dut.arready.value = 1
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)
    cocotb.start_soon(mem_stub(dut))

    # preload the per-context root pointer (pl_sel=6)
    dut.pl_valid.value = 1; dut.pl_sel.value = 6; dut.pl_data.value = ROOT_PPN
    await RisingEdge(dut.clk)
    dut.pl_valid.value = 0
    await RisingEdge(dut.clk)

    got = []

    async def collector():
        while True:
            await RisingEdge(dut.clk)
            if int(dut.rsp_valid.value) and int(dut.rsp_ready.value):
                got.append(int(dut.rsp_spa.value))
    cocotb.start_soon(collector())

    for vpn in range(N_REQS):
        dut.req_vpn.value = vpn
        dut.req_valid.value = 1
        await RisingEdge(dut.clk)
        while not int(dut.req_ready.value):
            await RisingEdge(dut.clk)
        dut.req_valid.value = 0
        await ClockCycles(dut.clk, 15)
    dut.req_valid.value = 0

    for _ in range(N_REQS * MEM_LATENCY + 4000):
        await RisingEdge(dut.clk)
        if len(got) >= N_REQS:
            break

    assert len(got) == N_REQS, f"completed {len(got)}/{N_REQS}"
    assert sorted(got) == sorted(expected_spa(v) for v in range(N_REQS)), \
        "SPA set mismatch (pointer chase produced wrong translation)"

    walks = int(dut.cnt_walks.value)
    hits = int(dut.cnt_iotlb_hit.value)
    coal = int(dut.cnt_coalesced.value)
    lines = len({v >> CW for v in range(N_REQS)})
    dut._log.info(f"RTL: walks={walks} iotlb_hit={hits} coalesced={coal} completed={len(got)} "
                  f"| expected coalesced lines={lines}")
    assert walks == lines, f"walks {walks} != coalesced lines {lines}"
    assert hits + coal == N_REQS - walks, f"hit+coal {hits+coal} != {N_REQS - walks}"
    dut._log.info("happy path OK: real Sv39 pointer chase, all SPAs correct, walks==lines")
