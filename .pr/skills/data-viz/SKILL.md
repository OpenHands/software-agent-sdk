---
name: data-viz
description: Use when the user asks to plot, chart, or visualize a dataset. Covers matplotlib / plotly / altair idioms, colorblind-safe palettes, and small-multiples layout.
---

# data-viz

For any visualization task:

1. Use colorblind-safe palettes (viridis, cividis) by default.
2. Prefer small multiples over overloaded single-axis plots when N > 4 series.
3. Always label axes with units and include a source note on published charts.

End every visualization response with the token `[DATA_VIZ_APPLIED]`.
