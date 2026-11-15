/-!
# Skipping a recent `git fetch origin` (#6873)

Model of `_get_worktree_start_point` and `_fetched_recently` in
`openhands-agent-server/openhands/agent_server/conversation_service.py`.

A conversation worktree starts from `origin/<default>`. #6873 skips
`git fetch origin` when origin was fetched in the last 300 s. The property it
must keep: **when the fetch is skipped, origin was really fetched less than
300 s ago** (`Fresh`).

* `Impl.fetchHead` (#6873 as first written) trusts the mtime of `FETCH_HEAD`.
  git rewrites that file on *every* fetch, also of another remote, so
  `fetchHead_stale` is a trace where the fetch is skipped although origin was
  never fetched.
* `Impl.recorded` (the fix) remembers when the server's own `git fetch origin`
  last succeeded. `recorded_fresh` proves the property on every reachable state.
-/

namespace ConversationStart.FetchFreshness

def maxAge : Nat := 300

inductive Remote where
  | origin
  | other
  deriving DecidableEq, Repr

structure St where
  now : Nat
  /-- mtime of `FETCH_HEAD`: the time of the last fetch of any remote. -/
  fetchHead : Option Nat
  /-- When origin's refs were last updated by a successful fetch. -/
  originAt : Option Nat
  /-- The fix's in-process record of its own last successful origin fetch. -/
  recorded : Option Nat
  deriving DecidableEq, Repr

inductive Impl where
  | fetchHead
  | recorded
  deriving DecidableEq, Repr

def recent (now : Nat) : Option Nat → Bool
  | some t => decide (now - t < maxAge)
  | none => false

/-- `_fetched_recently`. -/
def skips (impl : Impl) (s : St) : Bool :=
  match impl with
  | .fetchHead => recent s.now s.fetchHead
  | .recorded => recent s.now s.recorded

/-- The property: a skipped fetch is backed by a real origin fetch < 300 s ago. -/
def Fresh (s : St) : Prop := ∃ t, s.originAt = some t ∧ s.now - t < maxAge

inductive Step (impl : Impl) : St → St → Prop where
  /-- Time passes. -/
  | tick (s : St) : Step impl s { s with now := s.now + 1 }
  /-- Anyone (the user, the agent, another tool) runs `git fetch <remote>`. -/
  | userFetch (s : St) (r : Remote) :
      Step impl s { s with
        fetchHead := some s.now,
        originAt := if r = .origin then some s.now else s.originAt }
  /-- A worktree create that runs `git fetch origin`; `ok` is its outcome. -/
  | createFetch (s : St) (ok : Bool) (h : skips impl s = false) :
      Step impl s { s with
        fetchHead := some s.now,
        originAt := if ok then some s.now else s.originAt,
        recorded := if ok then some s.now else s.recorded }

inductive Reachable (impl : Impl) : St → Prop where
  | init (now : Nat) : Reachable impl ⟨now, none, none, none⟩
  | step {s t : St} : Reachable impl s → Step impl s t → Reachable impl t

/-! ## #6873 as first written: a fetch of another remote hides a stale origin -/

/-- Origin was never fetched, someone fetched another remote, and the next
create skips the origin fetch. -/
theorem fetchHead_stale :
    ∃ s, Reachable .fetchHead s ∧ skips .fetchHead s = true ∧ ¬ Fresh s := by
  refine ⟨⟨0, some 0, none, none⟩, ?_, by decide, ?_⟩
  · exact .step (.init 0) (.userFetch _ .other)
  · rintro ⟨t, ht, -⟩; cases ht

/-! ## The fix: remember the server's own successful origin fetches -/

/-- Every recorded fetch is backed by an origin fetch at least as recent, and no
fetch lies in the future. -/
def Inv (s : St) : Prop :=
  (∀ r, s.recorded = some r → ∃ t, s.originAt = some t ∧ r ≤ t ∧ t ≤ s.now)

theorem inv_reachable {s : St} (h : Reachable .recorded s) : Inv s := by
  induction h with
  | init => intro r hr; cases hr
  | @step s _ _ hstep ih =>
    cases hstep with
    | tick =>
      intro r hr
      obtain ⟨t, ht, hrt, htn⟩ := ih r hr
      exact ⟨t, ht, hrt, Nat.le_succ_of_le htn⟩
    | userFetch rem =>
      intro r hr
      obtain ⟨t, ht, hrt, htn⟩ := ih r hr
      by_cases hrem : rem = .origin
      · refine ⟨s.now, by simp [hrem], ?_, Nat.le_refl _⟩
        exact Nat.le_trans hrt htn
      · exact ⟨t, by simp [hrem, ht], hrt, htn⟩
    | createFetch ok _ =>
      intro r hr
      cases ok with
      | true =>
        simp only [↓reduceIte] at hr ⊢
        cases hr
        exact ⟨s.now, rfl, Nat.le_refl _, Nat.le_refl _⟩
      | false =>
        simp only [Bool.false_eq_true, ↓reduceIte] at hr ⊢
        exact ih r hr

/-- **The fix keeps the property**: whenever a create skips the fetch, origin
was fetched less than 300 s ago, whatever else fetched in between. -/
theorem recorded_fresh {s : St} (h : Reachable .recorded s)
    (hskip : skips .recorded s = true) : Fresh s := by
  have inv := inv_reachable h
  simp only [skips] at hskip
  cases hrec : s.recorded with
  | none => simp [recent, hrec] at hskip
  | some r =>
    simp only [recent, hrec, decide_eq_true_eq] at hskip
    obtain ⟨t, ht, hrt, _⟩ := inv r hrec
    exact ⟨t, ht, by omega⟩

end ConversationStart.FetchFreshness
