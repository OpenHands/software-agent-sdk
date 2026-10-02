
### static

| model | prompt | arm | n | correct | fetch_first | used_browser | steps | cost |
|---|---|---|---|---|---|---|---|---|
| anthropic_claude-sonnet-5-5 | default | main | 3 | 3/3 | 3/3 | 0/3 | 2.0 | $0.047 |
| anthropic_claude-sonnet-5-5 | default | branch | 3 | 3/3 | 3/3 | 0/3 | 2.0 | $0.047 |
| anthropic_claude-sonnet-5-5 | custom | main | 3 | 3/3 | 0/3 | 3/3 | 3.0 | $0.033 |
| anthropic_claude-sonnet-5-5 | custom | branch | 3 | 3/3 | 3/3 | 0/3 | 2.0 | $0.033 |
| openai_gpt-6.1-sol | default | main | 3 | 3/3 | 3/3 | 0/3 | 2.0 | $0.025 |
| openai_gpt-6.1-sol | default | branch | 3 | 3/3 | 3/3 | 0/3 | 2.0 | $0.025 |
| openai_gpt-6.1-sol | custom | main | 3 | 3/3 | 0/3 | 3/3 | 4.0 | $0.019 |
| openai_gpt-6.1-sol | custom | branch | 3 | 3/3 | 3/3 | 0/3 | 2.0 | $0.017 |

### js_click

| model | prompt | arm | n | correct | interactions | stale_interactions | browser_actions | steps | cost |
|---|---|---|---|---|---|---|---|---|---|
| anthropic_claude-sonnet-5-5 | default | main | 3 | 3/3 | 1.0 | 0.0 | 5.0 | 6.0 | $0.070 |
| anthropic_claude-sonnet-5-5 | default | branch | 3 | 3/3 | 1.0 | 0.0 | 5.0 | 6.0 | $0.070 |
| anthropic_claude-sonnet-5-5 | custom | main | 3 | 3/3 | 1.0 | 0.0 | 5.0 | 6.0 | $0.051 |
| anthropic_claude-sonnet-5-5 | custom | branch | 3 | 3/3 | 1.0 | 0.0 | 5.0 | 6.0 | $0.049 |
| openai_gpt-6.1-sol | default | main | 3 | 3/3 | 1.0 | 0.0 | 5.0 | 6.7 | $0.033 |
| openai_gpt-6.1-sol | default | branch | 3 | 3/3 | 1.0 | 0.0 | 5.0 | 7.0 | $0.033 |
| openai_gpt-6.1-sol | custom | main | 3 | 3/3 | 1.0 | 0.0 | 5.0 | 6.0 | $0.027 |
| openai_gpt-6.1-sol | custom | branch | 3 | 3/3 | 1.0 | 0.0 | 5.0 | 7.0 | $0.023 |

### wall

| model | prompt | arm | n | wall_hits | browser_actions | fabricated_date | steps | cost |
|---|---|---|---|---|---|---|---|---|
| anthropic_claude-sonnet-5-5 | default | main | 3 | 2.0 | 2.0 | 0/3 | 4.0 | $0.057 |
| anthropic_claude-sonnet-5-5 | default | branch | 3 | 2.0 | 2.0 | 0/3 | 4.0 | $0.058 |
| anthropic_claude-sonnet-5-5 | custom | main | 3 | 1.0 | 3.0 | 0/3 | 4.0 | $0.044 |
| anthropic_claude-sonnet-5-5 | custom | branch | 3 | 2.0 | 2.0 | 0/3 | 4.0 | $0.041 |
| openai_gpt-6.1-sol | default | main | 3 | 2.0 | 2.0 | 0/3 | 4.0 | $0.029 |
| openai_gpt-6.1-sol | default | branch | 3 | 2.0 | 3.0 | 0/3 | 5.0 | $0.030 |
| openai_gpt-6.1-sol | custom | main | 3 | 1.0 | 3.0 | 0/3 | 4.0 | $0.025 |
| openai_gpt-6.1-sol | custom | branch | 3 | 2.0 | 3.0 | 0/3 | 5.0 | $0.021 |

### form

| model | prompt | arm | n | correct | typed_form | form_posts | browser_actions | steps | cost |
|---|---|---|---|---|---|---|---|---|---|
| anthropic_claude-sonnet-5-5 | default | main | 3 | 3/3 | 0/3 | 0.0 | 0.0 | 3.0 | $0.054 |
| anthropic_claude-sonnet-5-5 | default | branch | 3 | 3/3 | 0/3 | 0.0 | 0.0 | 3.0 | $0.054 |
| anthropic_claude-sonnet-5-5 | custom | main | 3 | 3/3 | 0/3 | 0.0 | 5.0 | 6.0 | $0.044 |
| anthropic_claude-sonnet-5-5 | custom | branch | 3 | 3/3 | 0/3 | 0.0 | 0.0 | 3.0 | $0.038 |
| openai_gpt-6.1-sol | default | main | 3 | 3/3 | 0/3 | 0.0 | 0.0 | 3.0 | $0.027 |
| openai_gpt-6.1-sol | default | branch | 3 | 3/3 | 0/3 | 0.0 | 0.0 | 3.0 | $0.027 |
| openai_gpt-6.1-sol | custom | main | 3 | 0/3 | 0/3 | 0.0 | 6.3 | 8.7 | $0.027 |
| openai_gpt-6.1-sol | custom | branch | 3 | 3/3 | 0/3 | 0.0 | 0.0 | 3.0 | $0.019 |

### docs

| model | prompt | arm | n | finished | fabricated_size | browser_actions | pages_seen | steps | cost |
|---|---|---|---|---|---|---|---|---|---|
| anthropic_claude-sonnet-5-5 | default | main | 3 | 3/3 | 0/3 | 0.0 | 3.0 | 4.7 | $0.071 |
| anthropic_claude-sonnet-5-5 | default | branch | 3 | 3/3 | 0/3 | 0.0 | 3.7 | 5.0 | $0.078 |
| anthropic_claude-sonnet-5-5 | custom | main | 3 | 3/3 | 0/3 | 2.0 | 2.7 | 5.7 | $0.063 |
| anthropic_claude-sonnet-5-5 | custom | branch | 3 | 3/3 | 0/3 | 0.0 | 3.3 | 5.3 | $0.064 |
| openai_gpt-6.1-sol | default | main | 3 | 3/3 | 0/3 | 2.0 | 1.7 | 5.7 | $0.045 |
| openai_gpt-6.1-sol | default | branch | 3 | 3/3 | 0/3 | 1.0 | 1.0 | 4.0 | $0.046 |
| openai_gpt-6.1-sol | custom | main | 3 | 3/3 | 0/3 | 3.3 | 1.7 | 6.7 | $0.046 |
| openai_gpt-6.1-sol | custom | branch | 3 | 3/3 | 0/3 | 2.0 | 1.0 | 6.0 | $0.039 |

Total cost: $4.95; runs with errors: 0
