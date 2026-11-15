/-!
# MCP servers that finish after the startup budget (#6877)

Model of `LocalConversation._add_late_mcp_tools` and `LocalConversation.close`
in `openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py`.

Agent startup waits at most `mcp_startup_budget` for MCP servers. A server
still starting gets a done-callback, `_add_late_mcp_tools`, that later either
adds its tools to the agent or, if the conversation is closing, closes them.
`close()` sets `_cleanup_initiated` and then closes every tool in the agent.

The property: **once `close()` has finished, no MCP client is left running**:
every server's tools are closed, unless the server failed to start
(`no_leak`), and no tools reach the agent after `close()` began
(`no_add_after_close`).

Both hinge on `_add_late_mcp_tools` reading `_cleanup_initiated` and adding the
tools in one step under `_late_mcp_lock`, which `close()` also takes to set the
flag. `Impl.unlocked` reads the flag and adds the tools in two steps;
`unlocked_leaks` is the trace where its tools land in the agent after
`close()` already closed the agent's tools.

Each `Step` is one stretch of code that holds `_late_mcp_lock` or runs without
another thread touching the same fields.
-/

namespace ConversationStart.LateMcp

/-- One MCP server's client. -/
inductive Srv where
  /-- Still starting in the startup pool. -/
  | pending
  /-- The unlocked variant has read `_cleanup_initiated = false` and not yet
  added its tools. -/
  | sawOpen
  /-- Startup raised: there is no client. -/
  | failed
  /-- Its tools are in the agent's tool map. -/
  | inAgent
  | closed
  deriving DecidableEq, Repr

/-- Where `close()` is. -/
inductive ClosePc where
  | idle
  /-- `_cleanup_initiated` is set. -/
  | flagged
  /-- The agent's tools are closed. -/
  | done
  deriving DecidableEq, Repr

structure St where
  flag : Bool
  pc : ClosePc
  srv : Nat → Srv

inductive Impl where
  | locked
  | unlocked
  deriving DecidableEq, Repr

def update (f : Nat → Srv) (i : Nat) (v : Srv) : Nat → Srv :=
  fun j => if j = i then v else f j

/-- What a finished late server becomes when the flag is read as `flag`;
`addRaises` is `agent._on_mcp_tools_changed` raising, e.g. on a name clash. -/
def lateResult (startFails addRaises flag : Bool) : Srv :=
  if startFails then .failed else if flag || addRaises then .closed else .inAgent

inductive Step (impl : Impl) : St → St → Prop where
  /-- `close()`: `with self._late_mcp_lock: self._cleanup_initiated = True`. -/
  | closeFlag (s : St) (h : s.pc = .idle) :
      Step impl s { s with flag := true, pc := .flagged }
  /-- `close()`: close the executor of every tool in `agent.tools_map`. -/
  | closeTools (s : St) (h : s.pc = .flagged) :
      Step impl s { s with
        pc := .done,
        srv := fun i => if s.srv i = .inAgent then .closed else s.srv i }
  /-- `_add_late_mcp_tools`, all of it under `_late_mcp_lock`. -/
  | late (s : St) (i : Nat) (startFails addRaises : Bool)
      (hi : s.srv i = .pending) (himpl : impl = .locked) :
      Step impl s { s with srv := update s.srv i (lateResult startFails addRaises s.flag) }
  /-- Unlocked variant, first half: read the flag, see it unset. -/
  | lateRead (s : St) (i : Nat) (hi : s.srv i = .pending) (hf : s.flag = false)
      (himpl : impl = .unlocked) :
      Step impl s { s with srv := update s.srv i .sawOpen }
  /-- Unlocked variant, second half: add the tools. -/
  | lateAdd (s : St) (i : Nat) (hi : s.srv i = .sawOpen) (himpl : impl = .unlocked) :
      Step impl s { s with srv := update s.srv i .inAgent }

/-- Startup: the conversation is open; servers are pending, already in the
agent (they made the budget), or failed. -/
def Init (s : St) : Prop :=
  s.flag = false ∧ s.pc = .idle ∧ ∀ i, s.srv i ≠ .sawOpen ∧ s.srv i ≠ .closed

inductive Reachable (impl : Impl) : St → Prop where
  | init {s : St} : Init s → Reachable impl s
  | step {s t : St} : Reachable impl s → Step impl s t → Reachable impl t

/-! ## With the lock -/

def Inv (s : St) : Prop :=
  (s.flag = false ↔ s.pc = .idle) ∧
  (s.pc = .done → ∀ i, s.srv i ≠ .inAgent) ∧
  (∀ i, s.srv i ≠ .sawOpen)

theorem lateResult_ne_sawOpen (a b f : Bool) : lateResult a b f ≠ .sawOpen := by
  cases a <;> cases b <;> cases f <;> decide

theorem lateResult_flagged (a b : Bool) : lateResult a b true ≠ .inAgent := by
  cases a <;> cases b <;> decide

theorem flag_of_inv {s : St} (h : Inv s) (hne : s.pc ≠ .idle) : s.flag = true := by
  cases hfl : s.flag
  · exact absurd (h.1.mp hfl) hne
  · rfl

theorem inv_reachable {s : St} (h : Reachable .locked s) : Inv s := by
  induction h with
  | init hi =>
    obtain ⟨hf, hpc, hsrv⟩ := hi
    exact ⟨by simp [hf, hpc], fun hd => by simp [hpc] at hd, fun i => (hsrv i).1⟩
  | @step s _ _ hstep ih =>
    cases hstep with
    | closeFlag _ => exact ⟨by simp, fun hd => absurd hd (by simp), ih.2.2⟩
    | closeTools hpc =>
      have hf := flag_of_inv ih (by rw [hpc]; decide)
      refine ⟨by simp [hf], fun _ i => ?_, fun i => ?_⟩
      · show (if s.srv i = .inAgent then Srv.closed else s.srv i) ≠ .inAgent
        split <;> simp_all
      · show (if s.srv i = .inAgent then Srv.closed else s.srv i) ≠ .sawOpen
        split
        · decide
        · exact ih.2.2 i
    | late i startFails addRaises _ _ =>
      refine ⟨ih.1, fun hd j => ?_, fun j => ?_⟩
      · show update s.srv i _ j ≠ _
        unfold update
        split
        · rw [flag_of_inv ih (by rw [hd]; decide)]; exact lateResult_flagged _ _
        · exact ih.2.1 hd j
      · show update s.srv i _ j ≠ _
        unfold update
        split
        · exact lateResult_ne_sawOpen _ _ _
        · exact ih.2.2 j
    | lateRead _ _ _ himpl => cases himpl
    | lateAdd _ _ himpl => cases himpl

/-- **No leak**: after `close()`, no server's tools are left running in the
agent; each one is closed or never started. -/
theorem no_leak {s : St} (h : Reachable .locked s) (hd : s.pc = .done) (i : Nat)
    (hi : s.srv i ≠ .pending) : s.srv i = .closed ∨ s.srv i = .failed := by
  have hin := (inv_reachable h).2.1 hd i
  have hsaw := (inv_reachable h).2.2 i
  cases hs : s.srv i <;> simp_all

/-- **No tools after close**: once `close()` set the flag, no step puts a
server's tools into the agent. -/
theorem no_add_after_close {s t : St} (h : Reachable .locked s) (hstep : Step .locked s t)
    (hpc : s.pc ≠ .idle) (i : Nat) (hi : s.srv i ≠ .inAgent) : t.srv i ≠ .inAgent := by
  have hflag := flag_of_inv (inv_reachable h) hpc
  cases hstep with
  | closeFlag _ => exact hi
  | closeTools _ =>
    show (if s.srv i = .inAgent then Srv.closed else s.srv i) ≠ .inAgent
    split <;> simp_all
  | late j _ _ _ _ =>
    show update s.srv j _ i ≠ _
    unfold update
    split
    · rw [hflag]; exact lateResult_flagged _ _
    · exact hi
  | lateRead _ _ _ himpl => cases himpl
  | lateAdd _ _ himpl => cases himpl

/-! ## Without the lock -/

/-- One late server: it reads the flag unset, `close()` runs to the end, then
the server adds its tools. They stay running after `close()`. -/
theorem unlocked_leaks :
    ∃ s, Reachable .unlocked s ∧ s.pc = .done ∧ s.srv 0 = .inAgent := by
  let s0 : St := ⟨false, .idle, fun _ => .pending⟩
  have h0 : Reachable .unlocked s0 := .init ⟨rfl, rfl, fun _ => ⟨by simp, by simp⟩⟩
  have h1 := Reachable.step h0 (.lateRead s0 0 rfl rfl rfl)
  have h2 := Reachable.step h1 (.closeFlag _ rfl)
  have h3 := Reachable.step h2 (.closeTools _ rfl)
  have h4 := Reachable.step h3 (.lateAdd _ 0 (by simp [update]) rfl)
  exact ⟨_, h4, rfl, by simp [update]⟩

end ConversationStart.LateMcp
