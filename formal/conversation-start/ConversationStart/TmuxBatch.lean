/-!
# Batched tmux environment setup (#6871)

Model of `run_tmux_commands` in
`openhands-tools/openhands/tools/terminal/terminal/tmux_terminal.py`, and of
how tmux splits one invocation's arguments into commands
(`cmd_parse_from_arguments` in tmux's `cmd-parse.c`): an argument that ends in
`;` ends the current command, and tmux keeps the text before the `;`.

Before #6871 every command ran as its own tmux invocation. The theorems say the
batched invocations make tmux run exactly the commands it ran before, in the
same order and with the same arguments (`exec_plan`); that each invocation
fits the size limit unless it is a single command that alone exceeds it
(`plan_fits`); and that batching never needs more invocations than commands
(`plan_length_le`).
-/

namespace ConversationStart.TmuxBatch

/-- What the model needs to know about an argument. -/
structure Args (α : Type) where
  /-- The argument ends in `;`, so tmux ends the current command after it. -/
  endsSemi : α → Bool
  /-- What tmux keeps of an argument that ends in `;` (nothing for `;`). -/
  strip : α → List α
  /-- `len(arg)` in Python. -/
  len : α → Nat
  /-- The `";"` argument the batch puts between commands. -/
  semi : α
  semi_ends : endsSemi semi = true
  semi_strip : strip semi = []
  semi_len : len semi = 1

variable {α : Type}

/-- tmux: split one invocation's arguments into commands. -/
def parseFrom (A : Args α) : List α → List α → List (List α)
  | cur, [] => if cur.isEmpty then [] else [cur]
  | cur, a :: rest =>
    if A.endsSemi a then (cur ++ A.strip a) :: parseFrom A [] rest
    else parseFrom A (cur ++ [a]) rest

def parse (A : Args α) (args : List α) : List (List α) := parseFrom A [] args

/-- A command none of whose arguments ends in `;`. -/
def Plain (A : Args α) (c : List α) : Prop := ∀ a ∈ c, A.endsSemi a = false

/-- Commands joined with `;` arguments, as the batch sends them. -/
def join (A : Args α) : List (List α) → List α
  | [] => []
  | [c] => c
  | c :: c' :: cs => c ++ A.semi :: join A (c' :: cs)

/-- `sum(len(a) + 1 for a in command)`. -/
def cost (A : Args α) : List α → Nat
  | [] => 0
  | a :: c => A.len a + 1 + cost A c

/-- The cost of a batch as the Python code counts it: every command followed
by its `;` (which costs `len(";") + 1 = 2`). -/
def batchCost (A : Args α) : List (List α) → Nat
  | [] => 0
  | c :: b => cost A c + 2 + batchCost A b

/-- `run(batch[:-1])`: nothing for an empty batch. -/
def flush (A : Args α) (b : List (List α)) : List (List α) :=
  if b.isEmpty then [] else [join A b]

/-- `run_tmux_commands`: the invocations it makes, given the commands already
in the current batch `b`. -/
def plan (A : Args α) (limit : Nat) : List (List α) → List (List α) → List (List α)
  | b, [] => flush A b
  | b, c :: cs =>
    if c.any A.endsSemi then flush A b ++ c :: plan A limit [] cs
    else if !b.isEmpty && decide (batchCost A b + cost A c > limit) then
      flush A b ++ plan A limit [c] cs
    else plan A limit (b ++ [c]) cs

/-! ## Parsing -/

theorem parseFrom_plain_append (A : Args α) (c rest cur : List α) (h : Plain A c) :
    parseFrom A cur (c ++ rest) = parseFrom A (cur ++ c) rest := by
  induction c generalizing cur with
  | nil => simp
  | cons a c ih =>
    have ha : A.endsSemi a = false := h a (List.mem_cons_self ..)
    have hc : Plain A c := fun x hx => h x (List.mem_cons_of_mem _ hx)
    simp only [List.cons_append, parseFrom, ha]
    rw [ih (cur ++ [a]) hc]
    simp

theorem parse_plain (A : Args α) (c : List α) (h : Plain A c) (hne : c ≠ []) :
    parse A c = [c] := by
  have := parseFrom_plain_append A c [] [] h
  simp only [List.append_nil, List.nil_append] at this
  simp only [parse, this, parseFrom]
  cases c with
  | nil => exact absurd rfl hne
  | cons _ _ => rfl

theorem parse_join (A : Args α) :
    ∀ (cs : List (List α)), (∀ c ∈ cs, Plain A c ∧ c ≠ []) →
      parse A (join A cs) = cs
  | [], _ => rfl
  | [c], h => by
    simp only [join]
    exact parse_plain A c (h c (List.mem_singleton_self c)).1
      (h c (List.mem_singleton_self c)).2
  | c :: c' :: cs, h => by
    have hc := h c (List.mem_cons_self ..)
    have ih := parse_join A (c' :: cs) (fun x hx => h x (List.mem_cons_of_mem _ hx))
    simp only [join, parse]
    rw [parseFrom_plain_append A c _ [] hc.1]
    simp only [List.nil_append, parseFrom, A.semi_ends, A.semi_strip, List.append_nil,
      ite_true]
    simp only [parse] at ih
    rw [ih]

/-! ## Batched execution equals one invocation per command -/

/-- Commands a batch may hold: plain and non-empty. -/
def Good (A : Args α) (b : List (List α)) : Prop := ∀ c ∈ b, Plain A c ∧ c ≠ []

theorem flush_exec (A : Args α) (b : List (List α)) (hb : Good A b) :
    (flush A b).flatMap (parse A) = b := by
  unfold flush
  cases b with
  | nil => rfl
  | cons c b =>
    simp only [List.isEmpty_cons, Bool.false_eq_true, ite_false, List.flatMap_cons,
      List.flatMap_nil, List.append_nil]
    exact parse_join A (c :: b) hb

theorem any_false_plain (A : Args α) (c : List α) (h : c.any A.endsSemi = false) :
    Plain A c := by
  intro a ha
  simp only [List.any_eq_false] at h
  simpa using h a ha

theorem exec_plan (A : Args α) (limit : Nat) :
    ∀ (b cmds : List (List α)), Good A b → (∀ c ∈ cmds, c ≠ []) →
      (plan A limit b cmds).flatMap (parse A) = b ++ cmds.flatMap (parse A)
  | b, [], hb, _ => by simp [plan, flush_exec A b hb]
  | b, c :: cs, hb, hcmds => by
    have hc : c ≠ [] := hcmds c (List.mem_cons_self ..)
    have hcs : ∀ x ∈ cs, x ≠ [] := fun x hx => hcmds x (List.mem_cons_of_mem _ hx)
    have hnil : Good A ([] : List (List α)) := fun _ h => absurd h (List.not_mem_nil)
    unfold plan
    by_cases hsemi : c.any A.endsSemi = true
    · simp only [hsemi, ite_true, List.flatMap_append, List.flatMap_cons,
        flush_exec A b hb, exec_plan A limit [] cs hnil hcs, List.nil_append]
    · have hplain : Plain A c := any_false_plain A c (by simpa using hsemi)
      have hgc : Good A [c] := by
        intro x hx; simp at hx; subst hx; exact ⟨hplain, hc⟩
      have hgbc : Good A (b ++ [c]) := by
        intro x hx
        simp only [List.mem_append, List.mem_singleton] at hx
        rcases hx with hx | hx
        · exact hb x hx
        · subst hx; exact ⟨hplain, hc⟩
      simp only [hsemi, Bool.false_eq_true, ite_false]
      split
      · simp only [List.flatMap_append, flush_exec A b hb,
          exec_plan A limit [c] cs hgc hcs, List.flatMap_cons,
          parse_plain A c hplain hc, List.singleton_append]
      · simp only [exec_plan A limit (b ++ [c]) cs hgbc hcs, List.flatMap_cons,
          parse_plain A c hplain hc, List.append_assoc, List.singleton_append]

/-- **Batching is invisible to tmux.** The batched invocations run exactly the
commands that one invocation per command ran, in the same order. -/
theorem exec_equiv (A : Args α) (limit : Nat) (cmds : List (List α))
    (hcmds : ∀ c ∈ cmds, c ≠ []) :
    (plan A limit [] cmds).flatMap (parse A) = cmds.flatMap (parse A) := by
  have h := exec_plan A limit [] cmds (fun _ h => absurd h List.not_mem_nil) hcmds
  simpa using h

/-! ## Size and count -/

theorem cost_append (A : Args α) (x y : List α) :
    cost A (x ++ y) = cost A x + cost A y := by
  induction x with
  | nil => simp [cost]
  | cons a x ih => simp only [List.cons_append, cost, ih]; omega

theorem cost_join (A : Args α) :
    ∀ (b : List (List α)), b ≠ [] → cost A (join A b) + 2 = batchCost A b
  | [], h => absurd rfl h
  | [c], _ => by simp [join, batchCost]
  | c :: c' :: cs, _ => by
    have ih := cost_join A (c' :: cs) (by simp)
    simp only [join, batchCost, cost_append, cost, A.semi_len] at *
    omega

theorem batchCost_append (A : Args α) (b : List (List α)) (c : List α) :
    batchCost A (b ++ [c]) = batchCost A b + cost A c + 2 := by
  induction b with
  | nil => simp [batchCost]
  | cons x b ih => simp only [List.cons_append, batchCost, ih]; omega

/-- What the current batch can be: empty, one command from the input, or a
batch whose Python-counted cost is at most `limit + 2`. -/
def Fits (A : Args α) (limit : Nat) (S : List (List α)) (b : List (List α)) : Prop :=
  b = [] ∨ (∃ c ∈ S, b = [c]) ∨ batchCost A b ≤ limit + 2

theorem flush_fits (A : Args α) (limit : Nat) (S b : List (List α))
    (hb : Fits A limit S b) : ∀ inv ∈ flush A b, cost A inv ≤ limit ∨ inv ∈ S := by
  intro inv hinv
  unfold flush at hinv
  cases b with
  | nil => simp at hinv
  | cons c b =>
    simp only [List.isEmpty_cons, Bool.false_eq_true, ite_false,
      List.mem_singleton] at hinv
    subst hinv
    rcases hb with h | ⟨x, hx, h⟩ | h
    · exact absurd h (by simp)
    · right; simp only [List.cons.injEq] at h; obtain ⟨rfl, rfl⟩ := h; simpa [join]
    · left; have := cost_join A (c :: b) (by simp); omega

/-- **Every invocation fits**, except a single command that alone exceeds the
limit (which one invocation per command could not split either). -/
theorem plan_fits (A : Args α) (limit : Nat) (S : List (List α)) :
    ∀ (b cmds : List (List α)), Fits A limit S b → (∀ c ∈ cmds, c ∈ S) →
      ∀ inv ∈ plan A limit b cmds, cost A inv ≤ limit ∨ inv ∈ S
  | b, [], hb, _ => by simpa [plan] using flush_fits A limit S b hb
  | b, c :: cs, hb, hS => by
    have hcS : c ∈ S := hS c (List.mem_cons_self ..)
    have hcsS : ∀ x ∈ cs, x ∈ S := fun x hx => hS x (List.mem_cons_of_mem _ hx)
    intro inv hinv
    unfold plan at hinv
    split at hinv
    · simp only [List.mem_append, List.mem_cons] at hinv
      rcases hinv with h | rfl | h
      · exact flush_fits A limit S b hb inv h
      · exact Or.inr hcS
      · exact plan_fits A limit S [] cs (Or.inl rfl) hcsS inv h
    · split at hinv
      · simp only [List.mem_append] at hinv
        rcases hinv with h | h
        · exact flush_fits A limit S b hb inv h
        · exact plan_fits A limit S [c] cs (Or.inr (Or.inl ⟨c, hcS, rfl⟩)) hcsS inv h
      · rename_i hover
        have hfits : Fits A limit S (b ++ [c]) := by
          cases b with
          | nil => exact Or.inr (Or.inl ⟨c, hcS, rfl⟩)
          | cons x b =>
            right; right
            rw [batchCost_append]
            simp only [List.isEmpty_cons, Bool.not_false, Bool.true_and,
              decide_eq_true_eq, Nat.not_lt] at hover
            omega
        exact plan_fits A limit S (b ++ [c]) cs hfits hcsS inv hinv

theorem flush_length (A : Args α) (b : List (List α)) :
    (flush A b).length = if b.isEmpty then 0 else 1 := by
  unfold flush; split <;> simp

/-- **Never more invocations than before**: batching uses at most one tmux
invocation per command. -/
theorem plan_length (A : Args α) (limit : Nat) :
    ∀ (b cmds : List (List α)),
      (plan A limit b cmds).length ≤ (if b.isEmpty then 0 else 1) + cmds.length
  | b, [] => by simp [plan, flush_length]
  | b, c :: cs => by
    unfold plan
    have h0 := plan_length A limit [] cs
    have h1 := plan_length A limit [c] cs
    have h2 := plan_length A limit (b ++ [c]) cs
    simp only [List.isEmpty_nil, ite_true, List.isEmpty_cons, Bool.false_eq_true,
      ite_false] at h0 h1
    have hbc : (b ++ [c]).isEmpty = false := by simp
    rw [hbc] at h2
    simp only [Bool.false_eq_true, ite_false] at h2
    split
    · simp only [List.length_append, List.length_cons, flush_length]; split <;> omega
    · split
      · simp only [List.length_append, flush_length]; split <;> simp_all <;> omega
      · split <;> simp_all <;> omega

theorem plan_length_le (A : Args α) (limit : Nat) (cmds : List (List α)) :
    (plan A limit [] cmds).length ≤ cmds.length := by
  simpa using plan_length A limit [] cmds

end ConversationStart.TmuxBatch
