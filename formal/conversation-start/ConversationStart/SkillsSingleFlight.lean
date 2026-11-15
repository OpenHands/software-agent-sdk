/-!
# One public-skills load at a time (#6874)

Model of `load_public_skills` in `openhands-sdk/openhands/sdk/skills/skill.py`.
The startup warm-up thread and the first conversation start can call it at the
same time. #6874 wraps the whole call (cache check, then load and cache fill)
in `_PUBLIC_SKILLS_LOAD_LOCK`.

The property: **the skills are loaded at most once per cache lifetime**: the
number of loads never exceeds the number of cache expiries plus one
(`locked_loads_le`). `Impl.unlocked` checks the cache and loads in separate
steps, as before #6874; `unlocked_double_load` is two callers that both miss
the cache and both load.
-/

namespace ConversationStart.SkillsSingleFlight

inductive Pc where
  | idle
  /-- Missed the cache; about to load (only reachable without the lock). -/
  | missed
  deriving DecidableEq, Repr

structure St where
  /-- The cache entry exists and is within its TTL. -/
  fresh : Bool
  loads : Nat
  /-- TTL expiries and `sync_public_skills` invalidations. -/
  expiries : Nat
  pc : Nat → Pc

inductive Impl where
  | locked
  | unlocked
  deriving DecidableEq, Repr

def update (f : Nat → Pc) (i : Nat) (v : Pc) : Nat → Pc :=
  fun j => if j = i then v else f j

inductive Step (impl : Impl) : St → St → Prop where
  /-- The cache entry expires or is invalidated. -/
  | expire (s : St) : Step impl s { s with fresh := false, expiries := s.expiries + 1 }
  /-- A call that finds the cache fresh returns it. -/
  | hit (s : St) (c : Nat) (h : s.pc c = .idle) (hf : s.fresh = true) : Step impl s s
  /-- With the lock: a miss loads and fills the cache in the same step. -/
  | missLoad (s : St) (c : Nat) (h : s.pc c = .idle) (hf : s.fresh = false)
      (himpl : impl = .locked) :
      Step impl s { s with fresh := true, loads := s.loads + 1 }
  /-- Without the lock: the miss ... -/
  | miss (s : St) (c : Nat) (h : s.pc c = .idle) (hf : s.fresh = false)
      (himpl : impl = .unlocked) :
      Step impl s { s with pc := update s.pc c .missed }
  /-- ... and, later, the load. -/
  | load (s : St) (c : Nat) (h : s.pc c = .missed) :
      Step impl s
        { s with fresh := true, loads := s.loads + 1, pc := update s.pc c .idle }

inductive Reachable (impl : Impl) : St → Prop where
  | init : Reachable impl ⟨false, 0, 0, fun _ => .idle⟩
  | step {s t : St} : Reachable impl s → Step impl s t → Reachable impl t

theorem locked_inv {s : St} (h : Reachable .locked s) :
    s.loads + (if s.fresh then 0 else 1) ≤ s.expiries + 1 ∧ ∀ c, s.pc c = .idle := by
  induction h with
  | init => exact ⟨by simp, fun _ => rfl⟩
  | @step s _ _ hstep ih =>
    obtain ⟨hn, hpc⟩ := ih
    cases hstep with
    | expire => refine ⟨?_, hpc⟩; simp only; split at hn <;> simp <;> omega
    | hit => exact ⟨hn, hpc⟩
    | missLoad c _ hf _ => refine ⟨?_, hpc⟩; simp [hf] at hn; simp; omega
    | miss _ _ _ himpl => cases himpl
    | load c h => exact absurd (hpc c ▸ h) (by simp)

/-- **Single flight**: with the lock, the skills are loaded at most once per
cache lifetime. -/
theorem locked_loads_le {s : St} (h : Reachable .locked s) : s.loads ≤ s.expiries + 1 := by
  have := (locked_inv h).1
  split at this <;> omega

/-- Before #6874: two callers miss the cache before either fills it, and both
load. -/
theorem unlocked_double_load :
    ∃ s, Reachable .unlocked s ∧ s.loads = 2 ∧ s.expiries = 0 := by
  let s0 : St := ⟨false, 0, 0, fun _ => .idle⟩
  have h1 := Reachable.step (Reachable.init (impl := .unlocked)) (.miss s0 0 rfl rfl rfl)
  have h2 := Reachable.step h1 (.miss _ 1 (by simp [update, s0]) rfl rfl)
  have h3 := Reachable.step h2 (.load _ 0 (by simp [update]))
  have h4 := Reachable.step h3 (.load _ 1 (by simp [update]))
  exact ⟨_, h4, rfl, rfl⟩

end ConversationStart.SkillsSingleFlight
