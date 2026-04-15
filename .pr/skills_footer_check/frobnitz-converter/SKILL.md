---
name: frobnitz-converter
description: Convert between frobnitz units (frobs, snargs, blarps) and SI meters. The conversion constants are non-standard and MUST be looked up — never guess.
---

# Frobnitz Unit Converter

Frobnitz units (`frobs`, `snargs`, `blarps`) are a fictional measurement
system. Their conversion factors are NOT common knowledge and CANNOT be
derived — you must obtain them from this skill's bundled resources.

## How to answer a conversion request

You have two options. Pick whichever fits the request:

1. **Run the converter script** (preferred for numeric answers):

   ```
   python scripts/convert.py <amount> <unit>
   ```

   It prints the value in meters. Example: `python scripts/convert.py 7 frobs`.

2. **Read the lookup table** at `references/conversion_table.md` if the
   user asks conceptual questions ("what is a blarp?", "which unit is
   biggest?") rather than a numeric conversion.

Both paths are relative to this skill's directory (see the location note
appended to this message).

## Answer format

Return the numeric answer in meters, plus the source you used (script
output or table entry). Do not invent numbers.
