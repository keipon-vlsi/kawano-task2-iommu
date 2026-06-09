# PPA across stages — config `full`

| stage | area | Fmax | power |
|---|---|---|---|
| architectural (sim, normalized) | 20571.1 GE | n/a (behavioral) | 283.81 /xlate (norm) |
| post-synthesis (yosys+OpenSTA) | 480974 um^2 (cells) | 19.7 MHz | 0.050 W |
| post-place+repair (OpenROAD) | 659445 um^2 (die@38%) | 55.5 MHz | 0.514 W |
| post-CTS (OpenROAD) | 696028 um^2 (die@40%) | 55.4 MHz | 0.666 W |
| post-global-route (OpenROAD) | 696028 um^2 (die@40%) | 54.5 MHz | 0.666 W |

Notes:
- *architectural* = simulator estimate, **normalized** units (GE / norm-energy) —
  the relative reference, not directly comparable to um^2 (calibrate via fit factor).
- *post-synthesis* area = standard-cell area (no die/whitespace); Fmax/power from OpenSTA.
- *post-place..route* area = **die** area at the placement utilization; Fmax from worst
  slack at the 2.5 ns (400 MHz) target; power includes wire RC (more realistic each stage).
- Fmax improves synth→place (buffering fixes the fanout-466 net) then dips slightly with
  CTS/route parasitics; the design still needs pipelining to reach 400 MHz.
