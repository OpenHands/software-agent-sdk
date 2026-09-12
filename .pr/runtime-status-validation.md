# Runtime status validation

After a failed start, an explicit stop or server shutdown clears the recorded
runtime error. A concurrent start failure cannot recreate that error after the
stop has claimed its startup task. Retained conversation state reports a missing,
resumable runtime instead of a stale error.

Validation: 27 registry and scoped-route tests passed, including stop/shutdown
races. Ruff, Pyright, and the repository pre-commit checks passed locally.
The parent Docker-runtime branch now contains the current main baseline; CI must
use that refreshed parent when checking baseline monotonicity.
