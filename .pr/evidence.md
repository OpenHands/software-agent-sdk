# Live verification

Started the fixed agent-server and created a remote conversation with
`AlwaysConfirm` and `LLMSecurityAnalyzer`, then read each property twice.

Observed:

```text
confirmation_policy: ['AlwaysConfirm', 'AlwaysConfirm']
security_analyzer: ['LLMSecurityAnalyzer', 'LLMSecurityAnalyzer']
agent: ['Agent', 'Agent']
is_confirmation_mode_active: [True, True]
```
