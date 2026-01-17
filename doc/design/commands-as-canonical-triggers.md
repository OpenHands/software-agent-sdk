# Design: Commands as the Canonical Trigger Abstraction

## Context

This document analyzes the discussion from [PR #1676](https://github.com/OpenHands/software-agent-sdk/pull/1676#issuecomment-3762026425) regarding the relationship between `CommandDefinition` and `Skill` with triggers.

### The Discussion

**Current PR approach (jpshackelford):**
- Commands in `commands/` directory are converted to `Skill` objects with `KeywordTrigger`
- `CommandDefinition.to_skill()` creates skills from commands
- `Plugin.get_all_skills()` returns combined skills (native + command-derived)

**Reviewer suggestion (enyst):**
> "I think I hear you saying that commands should be skills-with-trigger. I'm trying to say that maybe skills-with-triggers should be interpreted as commands, instead."
>
> "Precisely because AgentSkills standard doesn't cover 'triggered skills' in old OpenHands sense. Instead, plugin semantics make 'triggered stuff' _commands_."

## Analysis

### Current Data Model

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           Current Flow                                   │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│   commands/             skills/                                         │
│   └── review.md         └── github/SKILL.md                            │
│         │                      │                                        │
│         ▼                      ▼                                        │
│   CommandDefinition      Skill (trigger=None or KeywordTrigger)         │
│         │                      │                                        │
│         └──── to_skill() ──────┤                                        │
│                                ▼                                        │
│                     All become Skill objects                            │
│                     with KeywordTrigger                                 │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### Key Observations

1. **AgentSkills Standard**: The AgentSkills spec (agentskills.io) defines skills as knowledge/capability definitions. It does NOT define "triggered skills" in the OpenHands legacy sense.

2. **Claude Plugin Semantics**: In Claude Code plugins, `commands/` is the standard location for user-invokable slash commands. This is the native way to express "triggered behavior."

3. **Current Skill Trigger Types**:
   - `KeywordTrigger`: Activated when keywords appear in user messages
   - `TaskTrigger`: Activated for specific task types
   - `None`: Always active (progressive disclosure or always in context)

4. **Semantic Confusion**: Currently, a "skill with a KeywordTrigger" is semantically equivalent to a "command" - both are user-invokable triggered content.

### Proposed Unified Model

Make `CommandDefinition` the canonical abstraction for **any triggered/invokable content**:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          Proposed Flow                                   │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│   commands/             skills/ (with triggers)                         │
│   └── review.md         └── github/SKILL.md (has: keyword: "github")   │
│         │                      │                                        │
│         ▼                      │                                        │
│   CommandDefinition ◄───────── Skill.to_command()                       │
│         │                                                               │
│         └──────────────────────────────────────────────────────────────►│
│                                                                         │
│   skills/ (without triggers)                                            │
│   └── kubernetes/SKILL.md (no triggers)                                 │
│         │                                                               │
│         ▼                                                               │
│   Skill (trigger=None) ─► Always-active or progressive disclosure       │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### Semantic Clarity

| Content Type | Abstraction | Behavior |
|-------------|-------------|----------|
| User-invokable (triggered) | `CommandDefinition` | Activated by keyword/slash command |
| Always-active knowledge | `Skill` (trigger=None) | In context or progressive disclosure |

## Proposed Design

### 1. Make CommandDefinition the Canonical Triggered Type

`CommandDefinition` becomes the unified representation for:
- Commands loaded from `commands/` directory
- Skills with `KeywordTrigger` (converted from legacy format)
- Skills with `TaskTrigger` (converted from legacy format)

### 2. Add Skill.to_command() Method

```python
class Skill(BaseModel):
    def to_command(self, plugin_name: str | None = None) -> CommandDefinition | None:
        """Convert a triggered skill to a CommandDefinition.
        
        If this skill has a trigger (KeywordTrigger or TaskTrigger),
        convert it to a CommandDefinition for unified handling.
        
        Args:
            plugin_name: Optional plugin name for namespacing the command.
        
        Returns:
            CommandDefinition if this skill has a trigger, None otherwise.
        """
        if self.trigger is None:
            return None
            
        # Extract trigger keywords
        if isinstance(self.trigger, KeywordTrigger):
            keywords = self.trigger.keywords
        elif isinstance(self.trigger, TaskTrigger):
            keywords = self.trigger.triggers
        else:
            return None
            
        # Use first keyword as command name (without leading /)
        command_name = keywords[0].lstrip('/')
        if plugin_name:
            command_name = f"{plugin_name}:{command_name}"
            
        return CommandDefinition(
            name=command_name,
            description=self.description or "",
            content=self.content,
            allowed_tools=self.allowed_tools or [],
            source=self.source,
            metadata=self.metadata or {},
        )
```

### 3. Plugin Provides Unified Commands List

```python
class Plugin(BaseModel):
    def get_all_commands(self) -> list[CommandDefinition]:
        """Get all commands including those converted from triggered skills.
        
        Returns commands from:
        1. The commands/ directory (native commands)
        2. Skills with triggers (converted to commands)
        
        Returns:
            Combined list of CommandDefinition objects.
        """
        all_commands = list(self.commands)
        
        # Convert triggered skills to commands
        for skill in self.skills:
            command = skill.to_command(self.name)
            if command is not None:
                all_commands.append(command)
        
        return all_commands
```

### 4. Simplify Skill to Pure Knowledge

After this change, `Skill` objects in the codebase would:
- Focus on knowledge/context that is always available
- Support progressive disclosure (agent reads on demand)
- NOT have triggers (triggers = commands)

The trigger-related fields on `Skill` could be deprecated or made internal.

## Implementation Tasks

### Phase 1: Core Model Changes
1. [ ] Add `CommandDefinition.from_skill()` class method (or `Skill.to_command()`)
2. [ ] Add `trigger_keywords: list[str]` to `CommandDefinition` for multi-keyword support
3. [ ] Update `CommandDefinition` to support `TaskTrigger` semantics (inputs)

### Phase 2: Plugin Integration  
4. [ ] Add `Plugin.get_all_commands()` method
5. [ ] Modify `Plugin.merge_into()` to work with commands
6. [ ] Create utility to render commands in prompt format

### Phase 3: Usage Migration
7. [ ] Update conversation service to use commands for triggered content
8. [ ] Update skill matching logic to use CommandDefinition
9. [ ] Add deprecation warnings on skill trigger fields

### Phase 4: Cleanup (Future)
10. [ ] Consider making `Skill.trigger` deprecated/internal
11. [ ] Update documentation to reflect command-centric model

## Benefits

1. **Semantic Clarity**: "Command" = user-invokable, "Skill" = knowledge
2. **Plugin Alignment**: Matches Claude Code plugin expectations
3. **Single Representation**: One type for all triggered behavior
4. **Cleaner Codebase**: Less confusion about when to use triggers on skills

## Backward Compatibility

- Existing skills with triggers continue to work
- `to_skill()` method can remain for rendering triggered commands as skills if needed
- The change is primarily about which abstraction is canonical, not removing functionality

## Open Questions

1. Should we support both `KeywordTrigger` and `TaskTrigger` in commands?
2. How do we handle command arguments ($ARGUMENTS) vs TaskTrigger inputs?
3. Should `CommandDefinition` have its own trigger type, or just store keywords?

## Conclusion

The proposal is to invert the current relationship: rather than converting commands to skills-with-triggers, we should treat all triggered content as commands. This aligns with:
- Claude plugin semantics
- AgentSkills standard (which doesn't define triggered skills)
- Cleaner separation of concerns (commands = invokable, skills = knowledge)
