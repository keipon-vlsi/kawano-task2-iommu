#!/usr/bin/env python3
"""Copy a liberty file dropping every top-level `cell (...) {...}` whose header name
contains 'lpflow' (the power-gating isolation/clamp family). yosys/abc otherwise mis-picks
the very slow lpflow_isobufsrc as a buffer; OpenLane dont_uses it too. argv: <src> <dst>."""
import re, sys

t = open(sys.argv[1]).read()
out = []; i = 0; n = len(t)
while i < n:
    m = re.compile(r'cell\s*\(').search(t, i)
    if not m:
        out.append(t[i:]); break
    out.append(t[i:m.start()])
    j = t.find('{', m.start()); d = 0; k = j
    while k < n:
        if t[k] == '{': d += 1
        elif t[k] == '}': d -= 1
        if d == 0: break
        k += 1
    blk = t[m.start():k + 1]; hdr = t[m.start():j]
    if 'lpflow' not in hdr:
        out.append(blk)
    i = k + 1
open(sys.argv[2], 'w').write(''.join(out))
print(f"filtered: {len(t)} -> {len(''.join(out))} bytes")
