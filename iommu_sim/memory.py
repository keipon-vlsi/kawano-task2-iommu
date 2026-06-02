"""Memory model (swappable component). Currently fixed latency plus outstanding tracking.
Future: replace with bank / row-buffer / bandwidth model."""
class MemoryModel:
    def __init__(self, latency_ns=100.0, max_outstanding=None):
        self.latency = latency_ns   # 100ns / access
        self.max_outstanding = max_outstanding
        self.outstanding = 0; self.peak_outstanding = 0
        self.accesses = 0
    def issue(self, n=1):
        self.accesses += n
        self.outstanding += n
        self.peak_outstanding = max(self.peak_outstanding, self.outstanding)
    def retire(self, n=1):
        self.outstanding -= n