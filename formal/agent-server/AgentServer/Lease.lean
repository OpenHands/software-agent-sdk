/-!
# Conversation lease fencing

Model of `openhands-agent-server/openhands/agent_server/conversation_lease.py`
and of `EventService._write_guard` (main @ e4160d118).

* `claim`, `renew`, `release` and `guarded_write` run under one `FileLock`,
  so each is atomic.
* Owners are `owner_instance_id`s. Each `ConversationService` gets one per
  process (a uuid4), shared by all of its `EventService`s.
* `dead` abstracts `_owner_is_dead(payload)`, a same-host PID probe.
-/

namespace AgentServer.Lease

structure Rec where
  owner : Nat
  gen   : Nat
  exp   : Nat
  deriving DecidableEq, Repr

abbrev File := Option Rec

/-- `ConversationLease.claim`: `none` means `ConversationLeaseHeldError`. -/
def claim (f : File) (o now ttl : Nat) (dead : Bool) : Option Rec :=
  match f with
  | none => some ⟨o, 1, now + ttl⟩
  | some r =>
    if r.owner ≠ o ∧ now < r.exp ∧ dead = false then none
    else some ⟨o, if r.owner = o then r.gen else r.gen + 1, now + ttl⟩

/-- `_assert_owner_locked` succeeds. -/
def holds (f : File) (o g : Nat) : Bool :=
  match f with
  | some r => r.owner == o && r.gen == g
  | none => false

inductive Op where
  | claim (o now ttl : Nat) (dead : Bool)
  | renew (o g now ttl : Nat)
  | release (o g : Nat)
  deriving DecidableEq, Repr

def Op.by : Op → Nat
  | .claim o .. => o
  | .renew o .. => o
  | .release o .. => o

/-- Apply one operation. A refused operation leaves the file unchanged. -/
def apply (f : File) : Op → File
  | .claim o now ttl dead => (claim f o now ttl dead).elim f some
  | .renew o g now ttl => if holds f o g then some ⟨o, g, now + ttl⟩ else f
  | .release o g => if holds f o g then none else f

def applyAll (f : File) (ops : List Op) : File := ops.foldl apply f

/-- **Fencing.** Suppose the file does not name owner `o`, e.g. after a
takeover. Then no handle `(o, g)` passes `guarded_write`, for any `g`, until
`o` itself claims again: renews and releases by `o` are refused, and other
owners only ever write their own id. -/
theorem fenced_until_reclaim (o : Nat) (f : File) (ops : List Op)
    (hf : ∀ r, f = some r → r.owner ≠ o)
    (hops : ∀ op ∈ ops, ∀ now ttl dead, op ≠ .claim o now ttl dead) :
    ∀ g, holds (applyAll f ops) o g = false := by
  induction ops generalizing f with
  | nil =>
    intro g
    cases f with
    | none => rfl
    | some r => simp [applyAll, holds, hf r rfl]
  | cons op ops ih =>
    intro g
    apply ih
    · intro r hr
      cases op with
      | claim o' now ttl dead =>
        have ho' : o' ≠ o := by
          intro h; subst h
          exact hops _ (List.mem_cons_self ..) now ttl dead rfl
        cases f with
        | none => simp [apply, claim] at hr; subst hr; exact ho'
        | some r0 =>
          simp only [apply, claim] at hr
          split at hr
          · simp at hr; subst hr; exact hf _ rfl
          · simp at hr; subst hr; exact ho'
      | renew o' g' now ttl =>
        simp only [apply] at hr
        split at hr
        · rename_i hh
          simp at hr; subst hr
          cases f with
          | none => simp [holds] at hh
          | some r0 =>
            simp [holds] at hh
            intro h; subst h; exact hf r0 rfl hh.1
        · exact hf r hr
      | release o' g' =>
        simp only [apply] at hr
        split at hr
        · simp at hr
        · exact hf r hr
    · intro op' hm; exact hops op' (List.mem_cons_of_mem _ hm)

/-! ## `EventService._write_guard`

On main the guard is `nullcontext()` whenever `_lease is None`, and `close()`
sets `_lease = None`. A write that is still in flight after `close()` is
therefore not fenced. That covers a sync worker thread that `close()` cannot
cancel, and, before the run-admission fix, a re-armed run on a closed service
(`test_delete_while_rearm_waits_does_not_resurrect_files`). -/

/-- The service's view: `some (o, g)` while it holds a lease, `none` after
`close()` (and for services that never took one). -/
def writeAllowedMain (lease : Option (Nat × Nat)) (f : File) : Bool :=
  match lease with
  | none => true
  | some (o, g) => holds f o g

theorem main_closed_service_write_unfenced :
    let f : File := some ⟨2, 2, 100⟩       -- owner 2 took over
    writeAllowedMain none f = true ∧          -- closed service of owner 1
    holds f 1 1 = false := by
  decide

/-- With a `closed` flag that fails the guard (the fix), every allowed write
comes from the current lease holder, or from a service that never held a
lease. -/
def writeAllowedFixed (closed : Bool) (lease : Option (Nat × Nat)) (f : File) : Bool :=
  !closed && writeAllowedMain lease f

theorem fixed_closed_service_write_refused (lease : Option (Nat × Nat)) (f : File) :
    writeAllowedFixed true lease f = false := rfl

/-- The generation restarts at 1 after a release. Fencing still holds across
owners, because `holds` compares owner ids too, but a generation alone does
not identify a lease epoch. -/
theorem generation_restarts_after_release :
    applyAll none [.claim 1 0 45 false, .release 1 1, .claim 1 50 45 false] =
      some ⟨1, 1, 95⟩ := rfl

end AgentServer.Lease
