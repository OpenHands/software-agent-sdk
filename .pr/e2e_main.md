
## default system prompt + browser
LLM requests: 3; browser tools sent: True
page content reached the LLM via the real browser: True
<BROWSER_TOOLS> in system message: True
| rule | in system message | count in tool schemas |
|---|---|---|
| Try curl/wget/fetch first | True | 0 |
| Max 10 browser actions per sub-task | True | 0 |
| On 403/CAPTCHA/login wall | True | 0 |
| ALWAYS call browser_get_state before EVERY browser_click | False | 0 |
| Do NOT submit forms or create accounts | True | 0 |

browser_navigate description as sent:
```
Navigate to a URL in the browser.

This tool allows you to navigate to any web page. You can optionally open the URL in a new tab.

Parameters:
- url: The URL to navigate to (required)
- new_tab: Whether to open in a new tab (optional, default: False)

Examples:
- Navigate to Google: url="https://www.google.com"
- Open GitHub in new tab: url="https://github.com", new_tab=True
```

## custom system_prompt + browser
LLM requests: 3; browser tools sent: True
page content reached the LLM via the real browser: True
<BROWSER_TOOLS> in system message: False
| rule | in system message | count in tool schemas |
|---|---|---|
| Try curl/wget/fetch first | False | 0 |
| Max 10 browser actions per sub-task | False | 0 |
| On 403/CAPTCHA/login wall | False | 0 |
| ALWAYS call browser_get_state before EVERY browser_click | False | 0 |
| Do NOT submit forms or create accounts | False | 0 |

browser_navigate description as sent:
```
Navigate to a URL in the browser.

This tool allows you to navigate to any web page. You can optionally open the URL in a new tab.

Parameters:
- url: The URL to navigate to (required)
- new_tab: Whether to open in a new tab (optional, default: False)

Examples:
- Navigate to Google: url="https://www.google.com"
- Open GitHub in new tab: url="https://github.com", new_tab=True
```

## default system prompt, no browser
LLM requests: 1; browser tools sent: False
page content reached the LLM via the real browser: False
<BROWSER_TOOLS> in system message: False
| rule | in system message | count in tool schemas |
|---|---|---|
| Try curl/wget/fetch first | False | 0 |
| Max 10 browser actions per sub-task | False | 0 |
| On 403/CAPTCHA/login wall | False | 0 |
| ALWAYS call browser_get_state before EVERY browser_click | False | 0 |
| Do NOT submit forms or create accounts | False | 0 |
