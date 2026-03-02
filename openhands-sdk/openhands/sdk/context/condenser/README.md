# Condenser

The condenser is one of the systems used by OpenHands to manage the context window.

At regular intervals, or when requested by the agent or a user, the context window is condensed by replacing the first half of all events with a single summary event. This strategy performs well in benchmarks and strikes a balance between:
1. **Per-completion cost**: by regularly condensing, the context window stays bounded and completions use less tokens.
2. **Cache optimization**: condensation destroys the prompt cache, but doing so regularly keeps the cost of rebuilding the prompt cache low.
3. **Early context**: events are summarized, and summaries are also summarized in future condensations, so important information stays in the context.
4. **Recent context**: the back half of the context is untouched, so the agent has an easy time continuing the current task.

The primary condensation strategy is implemented in the LLM summarizing condenser. The remaining condensation infrastructure is used to facilitate rapid condenser prototyping and specialized downstream use cases.

## Event-Based Condensations and the View

The conversation is an important source of state for the agent, and at the heart is an append-only event log. Events capture almost every non-environment state change, and the agent takes events from this log that subclass `LLMConvertibleEvent` and converts them to messages that can be sent to completion endpoints.

The fact that the event log is append-only means that, even if we lose the environment the agent ran in, we have an almost perfect record of what transpired. Incredible for debugging and for enabling broader agent uses. But this poses a slight problem for the condensation system: how can we forget events from an append-only log?

Since we can only add data to an append-only structure, we mark condensations with a special `Condensation` event. These are similar to _tombstones_ in Apache systems like Cassandra and Kafka, and contain information about how to apply a condensation. The precise semantics are captured in the `Condensation.apply` method, which converts a list of `LLMConvertibleEvent` objects by forgetting marked events and inserting summaries.

Of course, now the agent cannot just grab all instances of `LLMConvertibleEvent` when communicating with the LLM. To capture "all events currently relevant to the LLM" we use the `View` class, which does the work of applying condensation events as they come in. Views also maintain some metadata that ensures condensers don't accidentally forget critical events or insert summaries where they shouldn't.

## Triggering Condensation

Condensation is triggered in two main cases:
1. A resource limit is reached (`max_tokens` or `max_size`) in the current view, or
2. An explicit condensation request is made.

The condensation requests can be made by a user (see `Conversation.condense`) or by the agent. Agents will request a condensation when they detect issues with the context window. These issues vary by model and provider -- we do our best to capture as many cases as possible in `LLM.context_window_exception`.

## Handling Failure

Condensation is not always possible. The LLM expects a certain structure to the messages (see the view properties and the API compliance tests), and sometimes the default condensation strategy will necessarily violate that structure.

When that happens, the condenser has to determine if condensation is _needed_ right now or if we're just trying to maintain our upper bound on the size of the context. In the latter situation the condenser just returns the view uncondensed. Since the resource limit condensation trigger is still satisfied, the condenser will just try again the next time the agent takes a step. These condensation triggers are "soft".

If condensation is explicitly requested, the conversation is often in a state that cannot proceed without condensation (e.g., context window exceptions). Skipping and trying on the next step is not an option: there won't _be_ a next step. These are "hard" condensation triggers, and when our balanced condensation isn't an option we forget-and-summarize the entire view in a hard context reset (see `LLMSummarizingCondenser.hard_context_reset` for an implementation).
