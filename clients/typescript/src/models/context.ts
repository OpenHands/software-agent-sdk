/** Opt-in native notes and history condensation; requires server support. */
export interface NotesRetrievalCondenserConfig {
  kind: 'NotesRetrievalCondenser';
  max_size?: number;
  max_tokens?: number | null;
  keep_first?: number;
  keep_recent?: number;
  reminder_fraction?: number;
}

/** Settings payload for the same opt-in condenser. */
export interface NotesRetrievalCondenserSettings extends Omit<
  NotesRetrievalCondenserConfig,
  'kind'
> {
  condenser_kind: 'notes_retrieval';
  enabled?: boolean;
}
