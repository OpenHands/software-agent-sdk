## Default prompt + browser (main vs branch)
```
system message identical: True
tool names and order identical: True
tool descriptions identical: True
tool parameters identical: True
```

## Custom system_prompt + browser (main vs branch)
```
system message identical: False
tool names and order identical: True
tool descriptions identical: True
tool parameters identical: True
--- main
+++ branch
@@ -1 +1,11 @@
 You are a terse test agent.
+
+<BROWSER_TOOLS>
+You have a browser for navigating pages and interacting with web UIs.
+* Try curl/wget/fetch first. Use the browser only when simpler tools fail or the page requires JS/interaction.
+* ALWAYS call `browser_get_state` before EVERY `browser_click` or `browser_type` — indices change after each action. Flow: navigate → get_state → interact → get_state → get_content.
+* Max 10 browser actions per sub-task. If stuck, switch approach entirely.
+* If 20+ total steps without converging, stop exploring and commit to your best answer.
+* On 403/CAPTCHA/login wall: try one alternative, then abandon the browser.
+* Do NOT submit forms or create accounts unless explicitly asked.
+</BROWSER_TOOLS>
```
