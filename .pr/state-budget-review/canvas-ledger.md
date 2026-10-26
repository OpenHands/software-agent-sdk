# Verification ledger — run state-budget-review

Revision `d5aef0488bca7f7e7a55e9a10cc420bc6c46a4a9`, local mode, Agent Server 1.58.2, base http://127.0.0.1:18800.

Checks: 4 — pass 4, fail 0, blocked 0, not-run 0, xfail 0, xpass 0. Families: 1.

## Families

| Family | pass | fail | blocked | not-run | xfail | xpass |
|---|---|---|---|---|---|---|
| F27 | 4 | 0 | 0 | 0 | 0 | 0 |

## Checks

| Feature/check | Entry point | Expected → actual | Result | Note | Evidence |
|---|---|---|---|---|---|
| F27.budget-meter | desktop and phone normal cap | Canonical cap paired with this conversation actual spend → Actual cap0.05 displays; cost0.00125133 then0.001331268; desktop and phone panels fit | pass |  | `normal-desktop.png` `normal-phone.png` |
| F27.budget-meter | direct composer follow-up without reload | Canonical cap paired with this conversation actual spend → Cost0.00125133→0.001331268 and2.50→2.66%; cap0.05 unchanged | pass |  | `state-live.mp4` |
| F27.budget-overshoot | desktop and phone after bounded think turn | Canonical cap paired with this conversation actual spend → Actual cost0.00127848/cap0.0001=1278.48%;100% bar; think thenMaxBudgetReached | pass |  | `over-desktop.png` `over-phone.png` `over-events.json` |
| F27.budget-recovery | backend restart then reload and reopen Usage | Canonical cap paired with this conversation actual spend → Normal cap0.05/cost0.001331268 retained; both base_state caps unchanged, no meta budget keys | pass |  | `state-restart.mp4` `persistence-post-restart.json` |

Only artifact paths were rebased to sibling files for publication.
