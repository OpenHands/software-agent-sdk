/-!
# Run admission of one conversation (`EventService.run` and its re-arm)

Model of `openhands-agent-server/openhands/agent_server/event_service.py`
(main @ e4160d118): `run`, `_run_and_publish`, `settle`,
`_maybe_end_run_session`, `send_message(run=True)`, `pause`, `close`.

One conversation is modelled in detail. Every other conversation on the
server is folded into `other`, the number of semaphore tokens they hold.

Each `Act` is one atomic segment of the asyncio code, i.e. what runs between
two `await`s. The segments are:

* `userRun`      POST /run: `run(wait_for_capacity=False)`. It takes a token
                 or reuses the session permit, then creates the run task.
* `sendRun`      `send_message(run=True)` while a run task exists. `run()`
                 refuses with "conversation_already_running", so the message
                 is parked (`_rerun_requested = True`). The SDK resets
                 FINISHED to IDLE when a message arrives.
* `bodyEnd`      The agent loop returns. The `finally` calls `settle()`, which
                 returns the session permit unless input is parked.
* `tailEnd`      `wait_for_pending` returns and `_run_task = None`. If input
                 is parked and the status is IDLE, the run re-arms with
                 `run(wait_for_capacity=True)`. When no token is free that
                 call blocks inside `RunSlot.acquire`, after its stop checks.
* `rearmAcquire` The blocked re-arm gets a token.
* `pause`, `close`  Explicit stops. Both bump `_explicit_interrupt_generation`.
* `otherAcquire`, `otherRelease`  Other conversations take or return tokens.

Two ghost flags record specification violations:

* `staleStart`  A run task started for a request that a later explicit stop
                (pause/interrupt/close) superseded, or on a closing service.
* `autoResume`  A run turned PAUSED into RUNNING without a user `/run`.
-/

namespace AgentServer.RunAdmission

inductive Status where
  | idle | running | finished | paused
  deriving DecidableEq, Repr

inductive Phase where
  | none | body | tail
  deriving DecidableEq, Repr

structure S where
  cap        : Nat
  free       : Nat          -- `_run_semaphore._value`
  other      : Nat          -- tokens held by other conversations
  sess       : Bool         -- `_run_session_slot` holds a token
  task       : Phase        -- `_run_task`
  status     : Status       -- `state.execution_status`
  gen        : Nat          -- `_explicit_interrupt_generation`
  rerun      : Bool         -- `_rerun_requested`
  closing    : Bool         -- `_closing`
  rearm      : Option Nat   -- re-arm blocked on capacity, with its request gen
  staleStart : Bool         -- ghost
  autoResume : Bool         -- ghost
  deriving DecidableEq, Repr

def S.init (cap : Nat) : S :=
  { cap, free := cap, other := 0, sess := false, task := .none, status := .idle,
    gen := 0, rerun := false, closing := false, rearm := none,
    staleStart := false, autoResume := false }

/-- `main` is the code on main. `fixed` re-checks the stop generation and
`_closing` after the capacity wait, before it creates the run task. -/
inductive Impl where
  | main | fixed
  deriving DecidableEq, Repr

inductive Act where
  | userRun | sendRun | bodyEnd | tailEnd | rearmAcquire
  | pause | close | otherAcquire | otherRelease
  deriving DecidableEq, Repr

/-- Create the run task for a request made at generation `req`. `byUser` is
true only for POST /run, the one request allowed to resume a paused run. The
SDK's `run()`/`arun()` move IDLE/PAUSED/ERROR/STUCK to RUNNING. -/
def startRun (s : S) (req : Nat) (byUser : Bool) : S :=
  { s with task := .body, status := .running,
           staleStart := s.staleStart || decide (req ≠ s.gen) || s.closing,
           autoResume := s.autoResume || (decide (s.status = .paused) && !byUser) }

/-- `_maybe_end_run_session`: return the session token if it is held. -/
def releaseSess (s : S) : S :=
  if s.sess then { s with sess := false, free := s.free + 1 } else s

def step (impl : Impl) (s : S) : Act → Option S
  | .userRun =>
    -- `_run_lock` is held by a blocked re-arm, so `/run` cannot enter then.
    if s.closing = true ∨ s.task ≠ .none ∨ s.rearm.isSome ∨ s.status = .running then
      none
    else if s.sess then some (startRun s s.gen true)
    else if 0 < s.free then
      some (startRun { s with free := s.free - 1, sess := true } s.gen true)
    else none                                    -- 429
  | .sendRun =>
    if s.closing = true ∨ s.task = .none then none
    else some { s with rerun := true,
                       status := if s.status = .finished then .idle else s.status }
  | .bodyEnd =>
    if s.task ≠ .body then none
    else
      let s1 := { s with task := .tail,
                         status := if s.status = .running then .finished else s.status }
      some (if s.rerun then s1 else releaseSess s1)
  | .tailEnd =>
    if s.task ≠ .tail then none
    else
      let s1 := { s with task := .none, rerun := false }
      if s.rerun = true ∧ s.status = .idle ∧ s.closing = false then
        if s.sess then some (startRun s1 s.gen false)
        else if 0 < s.free then
          some (startRun { s1 with free := s.free - 1, sess := true } s.gen false)
        else some { s1 with rearm := some s.gen }
      else some (releaseSess s1)
  | .rearmAcquire =>
    match s.rearm with
    | none => none
    | some g =>
      if s.free = 0 then none
      else
        let s1 := { s with rearm := none, free := s.free - 1, sess := true }
        match impl with
        | .main => some (startRun s1 g false)
        | .fixed =>
          if g = s.gen ∧ s.closing = false then some (startRun s1 g false)
          else some (releaseSess s1)
  | .pause =>
    if s.closing then none
    else some { s with gen := s.gen + 1, rerun := false,
                       status := if s.status = .idle ∨ s.status = .running
                                 then .paused else s.status }
  | .close =>
    -- Cancels `_run_task` (if any) but cannot see a re-arm already past
    -- `_run_task = None`.
    if s.closing then none
    else some (releaseSess { s with closing := true, gen := s.gen + 1,
                                    rerun := false, task := .none })
  | .otherAcquire =>
    if 0 < s.free then some { s with free := s.free - 1, other := s.other + 1 } else none
  | .otherRelease =>
    if 0 < s.other then some { s with free := s.free + 1, other := s.other - 1 } else none

def exec (impl : Impl) (s : S) : List Act → Option S
  | [] => some s
  | a :: as => (step impl s a).bind (fun s' => exec impl s' as)

inductive Reachable (impl : Impl) : S → Prop
  | init (cap : Nat) : Reachable impl (S.init cap)
  | step {s s' : S} (a : Act) : Reachable impl s → step impl s a = some s' → Reachable impl s'

/-! ## Counterexamples on main -/

/-- The interleaving that `tests/agent_server/test_rearm_stop_wins.py` replays
(capacity 1). A's run ends and enters its tail, which returns its token. B
takes the only token. A receives a message, which is parked. The tail
re-arms and blocks. The user pauses. B finishes. The re-arm resumes the
paused conversation. -/
def pauseTrace : List Act :=
  [.userRun, .bodyEnd, .otherAcquire, .sendRun, .tailEnd, .pause,
   .otherRelease, .rearmAcquire]

theorem main_pause_is_overridden :
    ∃ s, exec .main (S.init 1) pauseTrace = some s ∧
      s.status = .running ∧ s.staleStart = true ∧ s.autoResume = true := by
  exact ⟨_, rfl, rfl, rfl, rfl⟩

/-- Same race with `close()` (delete or idle eviction): a run task starts on
a closed service. -/
def closeTrace : List Act :=
  [.userRun, .bodyEnd, .otherAcquire, .sendRun, .tailEnd, .close,
   .otherRelease, .rearmAcquire]

theorem main_run_starts_after_close :
    ∃ s, exec .main (S.init 1) closeTrace = some s ∧
      s.closing = true ∧ s.task = .body ∧ s.staleStart = true := by
  exact ⟨_, rfl, rfl, rfl, rfl⟩

/-- On the fixed implementation, the same traces end with no run task and
the token returned. -/
theorem fixed_pause_trace :
    ∃ s, exec .fixed (S.init 1) pauseTrace = some s ∧
      s.status = .paused ∧ s.task = .none ∧ s.sess = false ∧ s.free = 1 := by
  exact ⟨_, rfl, rfl, rfl, rfl, rfl⟩

theorem fixed_close_trace :
    ∃ s, exec .fixed (S.init 1) closeTrace = some s ∧
      s.task = .none ∧ s.sess = false ∧ s.free = 1 := by
  exact ⟨_, rfl, rfl, rfl, rfl⟩

/-! ## Invariants

`Inv` holds on both implementations. It covers token conservation, the fact
that a held session token always has a run task (no leak), and the facts
about a blocked re-arm. `Safe` adds the two specification flags, and it holds
only on `fixed`. -/

def Inv (s : S) : Prop :=
  s.free + s.other + (if s.sess then 1 else 0) = s.cap ∧
  (s.sess = true → s.task ≠ .none) ∧
  (∀ g, s.rearm = some g →
     s.task = .none ∧ s.sess = false ∧ g ≤ s.gen ∧
     (g = s.gen → s.status = .idle ∧ s.closing = false)) ∧
  (s.task = .none → s.rerun = false)

def Safe (s : S) : Prop := Inv s ∧ s.staleStart = false ∧ s.autoResume = false

theorem inv_init (cap : Nat) : Inv (S.init cap) := by
  simp [Inv, S.init]

set_option maxHeartbeats 8000000 in
/-- Brute force: destructure the state, split every finite field, let `simp`
evaluate the step, then close the arithmetic with `omega`. -/
theorem inv_step (impl : Impl) {s s' : S} (a : Act)
    (h : Inv s) (hs : step impl s a = some s') : Inv s' := by
  rcases s with ⟨cap, free, other, sess, task, status, gen, rerun, closing, rearm, st, au⟩
  rcases h with ⟨hcons, hleak, hrearm, hrerun⟩
  cases a <;> cases sess <;> cases task <;> cases status <;> cases rerun <;>
    cases closing <;> cases rearm <;> (try cases impl) <;>
    simp [step, startRun, releaseSess] at hs hleak hrerun hrearm hcons <;>
    (try split at hs) <;>
    (first | (obtain ⟨_, _, _, rfl⟩ := hs) | (obtain ⟨_, _, rfl⟩ := hs) | (obtain ⟨_, rfl⟩ := hs) | (subst hs) | (cases hs) | skip) <;>
    simp_all [Inv] <;> omega

theorem reachable_inv {impl : Impl} {s : S} (h : Reachable impl s) : Inv s := by
  induction h with
  | init cap => exact inv_init cap
  | step a _ hs ih => exact inv_step _ a ih hs

/-- Token conservation (no leak, no double release) on both implementations. -/
theorem tokens_conserved {impl : Impl} {s : S} (h : Reachable impl s) :
    s.free + s.other + (if s.sess then 1 else 0) = s.cap :=
  (reachable_inv h).1

/-- A conversation with no run task and no blocked re-arm holds no token. -/
theorem idle_holds_no_token {impl : Impl} {s : S} (h : Reachable impl s)
    (ht : s.task = .none) : s.sess = false := by
  have := (reachable_inv h).2.1
  cases hs : s.sess <;> simp_all

set_option maxHeartbeats 8000000 in
theorem safe_step {s s' : S} (a : Act) (h : Safe s)
    (hs : step .fixed s a = some s') : Safe s' := by
  refine ⟨inv_step .fixed a h.1 hs, ?_⟩
  rcases s with ⟨cap, free, other, sess, task, status, gen, rerun, closing, rearm, st, au⟩
  rcases h with ⟨⟨_, _, hrearm, _⟩, hst, hau⟩
  simp only at hst hau
  subst hst hau
  cases a <;> cases sess <;> cases task <;> cases status <;> cases rerun <;>
    cases closing <;> cases rearm <;>
    simp [step, startRun, releaseSess] at hs hrearm <;>
    (try split at hs) <;>
    (first | (obtain ⟨_, _, _, rfl⟩ := hs) | (obtain ⟨_, _, rfl⟩ := hs) | (obtain ⟨_, rfl⟩ := hs) | (subst hs) | (cases hs) | skip) <;>
    simp_all

/-- **Stop wins** on the fixed implementation: no run task ever starts for a
request superseded by a pause/interrupt/close, and a paused conversation is
resumed only by a user `/run`. -/
theorem fixed_stop_wins {s : S} (h : Reachable .fixed s) :
    s.staleStart = false ∧ s.autoResume = false := by
  have : Safe s := by
    induction h with
    | init cap => exact ⟨inv_init cap, rfl, rfl⟩
    | step a _ hs ih => exact safe_step a ih hs
  exact this.2

/-- The fix drops no legitimate re-arm: a blocked re-arm with no stop since
its request starts the run as soon as a token is free. -/
theorem fixed_rearm_progress {s : S} (h : Reachable .fixed s) (g : Nat)
    (hr : s.rearm = some g) (hg : g = s.gen) (hfree : 0 < s.free) :
    ∃ s', step .fixed s .rearmAcquire = some s' ∧ s'.task = .body := by
  obtain ⟨_, _, hrearm, _⟩ := reachable_inv h
  obtain ⟨_, _, _, hcur⟩ := hrearm g hr
  obtain ⟨_, hc⟩ := hcur hg
  have hf : s.free ≠ 0 := by omega
  simp [step, hr, hf, hg, hc, startRun]

end AgentServer.RunAdmission
