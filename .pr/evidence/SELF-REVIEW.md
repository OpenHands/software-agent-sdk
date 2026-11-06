> Historical nonnegative-usage candidate evidence. Current positive-only fix and final application findings: [positive-only/README.md](positive-only/README.md).

# Self-review and remaining gates

Reviewed local production code `ec94bbdb6b01c3cc67b6e35cade97ed78aa38926` and
regression-test head `d7de2574eb16ae8fa190bdf3c86b81f64da1239e` against baseline
`232d0b05fa5cd798c9c827d1a1c1aafa541757ad`.

This is agent self-inspection, not an independent maintainer or GitHub approval.
No unresolved material defect was found in the inspected patch. The exact final
published head will still need repository CI and maintainer review.

## Concrete paths inspected

- `_compute_cost`: valid header (including zero) still returns before custom or
  static pricing. Missing/invalid headers reach usage only without an explicit
  rate pair. The code selects a single charge rather than adding alternatives.
- `_reported_cost`: nonfinite, negative and malformed values cannot poison SDK
  accumulated cost through the new reported-value path. Existing static price
  calculation and provider normalization remain in place.
- `on_response`: known zero is recorded, unavailable remains absent. Existing
  token, logging, span and callback paths use the same selected result.
- Public sync/async `LLM.completion` transport: SSE usage and actual HTTP headers
  survive assembly before metrics selection; 32 controls per built wheel.
- Real `Conversation.run`, close/reopen, persisted statistics and second run:
  baseline retains an incorrect zero; candidate retains $0.04 then reaches $0.08.
- Installed artifacts: wheel byte comparison isolates telemetry.py plus RECORD;
  both variants use the same exported hash-locked dependencies. No edited module
  is substituted into an unrelated packaged version in the final comparison.
- Public/persisted compatibility: no signature/schema/dependency changes. The
  intentional behavioral change is inclusion of confirmed zero in costs lists;
  companion docs explain it. No new UI/client setting is introduced.

## Ledger

| Finding during validation | Resolution | Evidence |
| --- | --- | --- |
| Initial whole-module swap could include unrelated main differences | Compare complete wheels differing only in telemetry.py/RECORD under one lock | build-manifest.json, eks-packaged.log |
| New persistence demo passed an unsupported factory keyword | Remove fixture-only keyword; require actual result records and finished statuses | Final demo records and portable helper |
| Wheel build output was scanned as duplicate SDK source | Remove only generated build/lib, rerun all hooks; no gate changes | pre-commit.txt |
| Zero header might mean an estimate in a customer's proxy | Preserve established authoritative-header precedence and expose this as a reviewer contract decision | Tests and docs; customer response evidence still needed |

## Required reviewer focus

1. Confirm authoritative header > explicit rates > reported usage > static
   pricing is the desired contract, especially valid zero versus unknown estimate.
2. Confirm zero-valued cost records are the right distinction from unavailable
   pricing for existing metric consumers.
3. Review the regression behavior, package hashes, failed-baseline evidence and
   simulation limits; do not read a Helm health check as proof of SDK consumption.

The fix requires a released SDK/agent-server consumed by the Enterprise runtime.
The fresh latest Helm demo in the separate provisioning chat is infrastructure
acceptance, not evidence that a released runtime already includes this patch.
