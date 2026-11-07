/-!
# Event delivery over `/sockets/session`

Model of `openhands-agent-server/openhands/agent_server/session_socket.py`
(main @ e4160d118) and of how events reach it.

**Producer side.** The run thread does three things for each event:
1. Under the state lock, it appends the event to the log (`persist`). The
   event's seq is its index.
2. It schedules the fan-out on the event loop through `AsyncCallbackWrapper`
   (`schedule`).
3. The loop later runs the fan-out (`publish`).

Scheduling and publishing are FIFO, so the producer is captured by three
counters: `saved ≥ sched ≥ pub`. The log holds `[0, saved)`, the scheduled
fan-outs are `[pub, sched)`, and the persisted-but-unscheduled events are
`[sched, saved)`.

**Client side.** One client reconnects with a cursor before every event,
i.e. a full replay. It goes through four steps:
* `subscribe`  registers a buffering subscriber. Its state snapshot takes the
               state lock, so it cannot land between `persist` and `schedule`.
* `mark`       reads `len(events)` without the lock and replays `[0, mark)`
               from disk.
* `goLive`     flushes the buffer, dropping seqs below the mark, and switches
               to pass-through.
* After that, `publish` sends straight to the wire. On `main` it sends with
               no check; `fixed` drops seqs below the mark there too.

`out` is what reached the client, in order.
-/

namespace AgentServer.EventDelivery

inductive Phase where
  | off | buffering | marked (m : Nat) | live (m : Nat)
  deriving DecidableEq, Repr

structure S where
  saved : Nat
  sched : Nat
  pub   : Nat
  phase : Phase
  buf   : List Nat
  out   : List Nat
  deriving DecidableEq, Repr

def S.init : S := { saved := 0, sched := 0, pub := 0, phase := .off, buf := [], out := [] }

inductive Impl where
  | main | fixed
  deriving DecidableEq, Repr

inductive Act where
  | persist | schedule | publish | subscribe | mark | goLive
  deriving DecidableEq, Repr

def step (impl : Impl) (s : S) : Act → Option S
  | .persist => some { s with saved := s.saved + 1 }
  | .schedule => if s.sched < s.saved then some { s with sched := s.sched + 1 } else none
  | .publish =>
    if s.pub < s.sched then
      let e := s.pub
      let s1 := { s with pub := s.pub + 1 }
      match s.phase with
      | .off => some s1
      | .buffering | .marked _ => some { s1 with buf := s.buf ++ [e] }
      | .live m =>
        match impl with
        | .main => some { s1 with out := s.out ++ [e] }
        | .fixed => some { s1 with out := if m ≤ e then s.out ++ [e] else s.out }
    else none
  | .subscribe =>
    if s.phase = .off ∧ s.sched = s.saved then some { s with phase := .buffering } else none
  | .mark =>
    if s.phase = .buffering then
      some { s with phase := .marked s.saved, out := List.range s.saved }
    else none
  | .goLive =>
    match s.phase with
    | .marked m =>
      some { s with phase := .live m, out := s.out ++ s.buf.filter (m ≤ ·), buf := [] }
    | _ => none

def exec (impl : Impl) (s : S) : List Act → Option S
  | [] => some s
  | a :: as => (step impl s a).bind (fun s' => exec impl s' as)

inductive Reachable (impl : Impl) : S → Prop
  | init : Reachable impl S.init
  | step {s s' : S} (a : Act) : Reachable impl s → step impl s a = some s' → Reachable impl s'

/-! ## Counterexample on main

This is what `test_event_persisted_before_mark_but_published_after_go_live`
reproduces. The client subscribes. The run thread persists event 0, and the
mark (read without the lock) already covers it. Replay sends 0, the client
goes live, and then the late fan-out sends 0 again. -/

def dupTrace : List Act := [.subscribe, .persist, .mark, .goLive, .schedule, .publish]

theorem main_duplicates :
    exec .main S.init dupTrace = some
      { saved := 1, sched := 1, pub := 1, phase := .live 1, buf := [], out := [0, 0] } := rfl

theorem fixed_dupTrace :
    exec .fixed S.init dupTrace = some
      { saved := 1, sched := 1, pub := 1, phase := .live 1, buf := [], out := [0] } := rfl

/-! ## Exactly once, in order, on `fixed` -/

theorem range'_concat' (s n : Nat) : List.range' s (n + 1) = List.range' s n ++ [s + n] := by
  induction n generalizing s with
  | zero => simp [List.range']
  | succ n ih =>
    rw [List.range'_succ, ih (s + 1), List.range'_succ]
    simp [Nat.add_assoc, Nat.add_comm 1 n]

theorem filter_range' (m b n : Nat) (hb : b ≤ m) :
    (List.range' b n).filter (m ≤ ·) = List.range' m (b + n - m) := by
  induction n with
  | zero => simp <;> omega
  | succ n ih =>
    rw [range'_concat', List.filter_append, ih]
    by_cases h : m ≤ b + n
    · have : b + (n + 1) - m = (b + n - m) + 1 := by omega
      rw [this, range'_concat']
      have : m + (b + n - m) = b + n := by omega
      simp [h, this]
    · have h1 : b + (n + 1) - m = 0 := by omega
      have h2 : b + n - m = 0 := by omega
      simp [h1, h2] <;> omega

theorem range_append_range' (m k : Nat) :
    List.range m ++ List.range' m k = List.range (m + k) := by
  rw [List.range_eq_range', List.range_eq_range']
  have := List.range'_append (s := 0) (m := m) (n := k) (step := 1)
  simpa using this

def Inv (s : S) : Prop :=
  s.pub ≤ s.sched ∧ s.sched ≤ s.saved ∧
  match s.phase with
  | .off => s.out = [] ∧ s.buf = []
  | .buffering => s.out = [] ∧ ∃ b, b ≤ s.pub ∧ s.buf = List.range' b (s.pub - b)
  | .marked m => m ≤ s.saved ∧ s.out = List.range m ∧
      ∃ b, b ≤ s.pub ∧ b ≤ m ∧ s.buf = List.range' b (s.pub - b)
  | .live m => m ≤ s.saved ∧ s.buf = [] ∧ s.out = List.range (max m s.pub)

theorem inv_step {s s' : S} (a : Act) (h : Inv s) (hs : step .fixed s a = some s') :
    Inv s' := by
  rcases s with ⟨saved, sched, pub, phase, buf, out⟩
  simp only [Inv] at h
  obtain ⟨h1, h2, hp⟩ := h
  cases a
  · -- persist
    simp only [step, Option.some.injEq] at hs; subst hs
    simp only [Inv]
    refine ⟨h1, by omega, ?_⟩
    cases phase <;> simp only at hp ⊢
    · exact hp
    · exact hp
    · exact ⟨by omega, hp.2⟩
    · exact ⟨by omega, hp.2⟩
  · -- schedule
    simp only [step] at hs
    split at hs
    · simp only [Option.some.injEq] at hs; subst hs
      simp only [Inv]
      refine ⟨by omega, by omega, ?_⟩
      cases phase <;> simp only at hp ⊢ <;> exact hp
    · simp at hs
  · -- publish
    simp only [step] at hs
    split at hs
    · rename_i hlt
      cases phase with
      | off =>
        simp only [Option.some.injEq] at hs; subst hs; simp only at hp; simp only [Inv]
        exact ⟨by omega, by omega, hp⟩
      | buffering =>
        simp only [Option.some.injEq] at hs; subst hs; simp only at hp; simp only [Inv]
        obtain ⟨hout, b, hb, hbuf⟩ := hp
        refine ⟨by omega, by omega, hout, b, by omega, ?_⟩
        rw [hbuf, show pub + 1 - b = (pub - b) + 1 by omega, range'_concat',
          show b + (pub - b) = pub by omega]
      | marked m =>
        simp only [Option.some.injEq] at hs; subst hs; simp only at hp; simp only [Inv]
        obtain ⟨hm, hout, b, hb, hbm, hbuf⟩ := hp
        refine ⟨by omega, by omega, hm, hout, b, by omega, hbm, ?_⟩
        rw [hbuf, show pub + 1 - b = (pub - b) + 1 by omega, range'_concat',
          show b + (pub - b) = pub by omega]
      | live m =>
        simp only [Option.some.injEq] at hs; subst hs; simp only at hp; simp only [Inv]
        obtain ⟨hm, hbuf, hout⟩ := hp
        refine ⟨by omega, by omega, hm, hbuf, ?_⟩
        by_cases hmp : m ≤ pub
        · rw [if_pos hmp, hout, show max m pub = pub by omega,
            show max m (pub + 1) = pub + 1 by omega, List.range_succ]
        · rw [if_neg hmp, hout, show max m pub = m by omega,
            show max m (pub + 1) = m by omega]
    · simp at hs
  · -- subscribe
    simp only [step] at hs
    split at hs
    · rename_i hc
      obtain ⟨hoff, _⟩ := hc
      subst hoff
      simp only [Option.some.injEq] at hs; subst hs; simp only at hp; simp only [Inv]
      exact ⟨h1, h2, hp.1, pub, Nat.le_refl _, by simp [hp.2]⟩
    · simp at hs
  · -- mark
    simp only [step] at hs
    split at hs
    · rename_i hc
      subst hc
      simp only [Option.some.injEq] at hs; subst hs; simp only at hp; simp only [Inv]
      obtain ⟨_, b, hb, hbuf⟩ := hp
      exact ⟨h1, h2, Nat.le_refl _, trivial, b, hb, by omega, hbuf⟩
    · simp at hs
  · -- goLive
    simp only [step] at hs
    cases phase with
    | marked m =>
      simp only [Option.some.injEq] at hs; subst hs; simp only at hp; simp only [Inv]
      obtain ⟨hm, hout, b, hb, hbm, hbuf⟩ := hp
      refine ⟨h1, h2, hm, trivial, ?_⟩
      rw [hout, hbuf, filter_range' m b _ hbm, range_append_range']
      congr 1; omega
    | _ => simp at hs

theorem reachable_inv {s : S} (h : Reachable .fixed s) : Inv s := by
  induction h with
  | init => simp [Inv, S.init]
  | step a _ hs ih => exact inv_step a ih hs

/-- Safety on `fixed`: what reached the client is strictly increasing, so it
has no duplicates and no reordering. -/
theorem fixed_no_dup_in_order {s : S} (h : Reachable .fixed s) :
    s.out.Pairwise (· < ·) := by
  obtain ⟨_, _, hp⟩ := reachable_inv h
  cases hph : s.phase <;> rw [hph] at hp
  · rw [hp.1]; simp
  · rw [hp.1]; simp
  · rw [hp.2.1]; exact List.pairwise_lt_range
  · rw [hp.2.2]; exact List.pairwise_lt_range

/-- Completeness on `fixed`: once live and every persisted event has been
fanned out, the client holds exactly the log, `[0, saved)`. -/
theorem fixed_complete {s : S} (h : Reachable .fixed s) (m : Nat)
    (hl : s.phase = .live m) (hq : s.pub = s.saved) : s.out = List.range s.saved := by
  obtain ⟨_, _, hp⟩ := reachable_inv h
  rw [hl] at hp
  obtain ⟨hm, _, hout⟩ := hp
  rw [hout, hq, Nat.max_eq_right hm]

end AgentServer.EventDelivery
