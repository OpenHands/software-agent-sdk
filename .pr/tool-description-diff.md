# Tool description comparison

Differences detected in the following descriptions:

### tools.apply_patch.description

```diff
--- main+++ pr@@ -1 +1,2 @@-Apply unified text patches to files in the workspace. Input must start with '*** Begin Patch' and end with '*** End Patch'.+Apply unified text patches to files in the workspace.
+Input must start with '*** Begin Patch' and end with '*** End Patch'.
```

### tools.browser_use.browser_click

```diff
--- main+++ pr@@ -1,10 +1,10 @@ Click an element on the page by its index.
 
-Use this tool to click on interactive elements like buttons, links, or form controls. 
+Use this tool to click on interactive elements like buttons, links, or form controls.
 The index comes from the browser_get_state tool output.
 
 Parameters:
 - index: The index of the element to click (from browser_get_state)
 - new_tab: Whether to open any resulting navigation in a new tab (optional)
 
-Important: Only use indices that appear in your current browser_get_state output.
+Important: Only use indices that appear in your current browser_get_state output.
```

### tools.browser_use.browser_close_tab

```diff
--- main+++ pr@@ -3,4 +3,4 @@ Use this tool to close tabs you no longer need. Get the tab_id from browser_list_tabs.
 
 Parameters:
-- tab_id: 4 Character Tab ID of the tab to close
+- tab_id: 4 Character Tab ID of the tab to close
```

### tools.browser_use.browser_get_content

```diff
--- main+++ pr@@ -1,3 +1,3 @@ Extract the main content of the current page in clean markdown format. It has been filtered to remove noise and advertising content.
 
-If the content was truncated and you need more information, use start_from_char parameter to continue from where truncation occurred.
+If the content was truncated and you need more information, use start_from_char parameter to continue from where truncation occurred.
```

### tools.browser_use.browser_get_state

```diff
--- main+++ pr@@ -1,7 +1,7 @@ Get the current state of the page including all interactive elements.
 
-This tool returns the current page content with numbered interactive elements that you can 
+This tool returns the current page content with numbered interactive elements that you can
 click or type into. Use this frequently to understand what's available on the page.
 
 Parameters:
-- include_screenshot: Whether to include a screenshot (optional, default: False)
+- include_screenshot: Whether to include a screenshot (optional, default: False)
```

### tools.browser_use.browser_get_storage

```diff
--- main+++ pr@@ -2,4 +2,4 @@ local storage, and session storage.
 
 This tool extracts all cookies and storage data from the current browser session.
-Useful for debugging, session management, or extracting authentication tokens.
+Useful for debugging, session management, or extracting authentication tokens.
```

### tools.browser_use.browser_go_back

```diff
--- main+++ pr@@ -1,4 +1,4 @@ Go back to the previous page in browser history.
 
-Use this tool to navigate back to the previously visited page, similar to clicking the 
-browser's back button.
+Use this tool to navigate back to the previously visited page, similar to clicking the
+browser's back button.
```

### tools.browser_use.browser_list_tabs

```diff
--- main+++ pr@@ -1,4 +1,4 @@ List all open browser tabs.
 
 This tool shows all currently open tabs with their IDs, titles, and URLs. Use the tab IDs
-with browser_switch_tab or browser_close_tab.
+with browser_switch_tab or browser_close_tab.
```

### tools.browser_use.browser_navigate

```diff
--- main+++ pr@@ -8,4 +8,4 @@ 
 Examples:
 - Navigate to Google: url="https://www.google.com"
-- Open GitHub in new tab: url="https://github.com", new_tab=True
+- Open GitHub in new tab: url="https://github.com", new_tab=True
```

### tools.browser_use.browser_scroll

```diff
--- main+++ pr@@ -4,4 +4,4 @@ to see more content.
 
 Parameters:
-- direction: Direction to scroll - "up" or "down" (optional, default: "down")
+- direction: Direction to scroll - "up" or "down" (optional, default: "down")
```

### tools.browser_use.browser_set_storage

```diff
--- main+++ pr@@ -7,4 +7,4 @@ Parameters:
 - storage_state: A dictionary containing 'cookies' and 'origins'.
   - cookies: List of cookie objects
-  - origins: List of origin objects containing 'localStorage' and 'sessionStorage'
+  - origins: List of origin objects containing 'localStorage' and 'sessionStorage'
```

### tools.browser_use.browser_start_recording

```diff
--- main+++ pr@@ -11,4 +11,4 @@ Call browser_stop_recording to stop recording and save any remaining events.
 
 Note: Recording persists across page navigations - the recording will automatically
-restart on new pages.
+restart on new pages.
```

### tools.browser_use.browser_stop_recording

```diff
--- main+++ pr@@ -7,4 +7,4 @@ rrweb event arrays. These files can be replayed using rrweb-player to visualize
 the recorded session.
 
-Returns a summary message with the total event count, file count, and save directory.
+Returns a summary message with the total event count, file count, and save directory.
```

### tools.browser_use.browser_switch_tab

```diff
--- main+++ pr@@ -3,4 +3,4 @@ Use this tool to switch between open tabs. Get the tab_id from browser_list_tabs.
 
 Parameters:
-- tab_id: 4 Character Tab ID of the tab to switch to
+- tab_id: 4 Character Tab ID of the tab to switch to
```

### tools.browser_use.browser_type

```diff
--- main+++ pr@@ -7,4 +7,4 @@ - index: The index of the input element (from browser_get_state)
 - text: The text to type
 
-Important: Only use indices that appear in your current browser_get_state output.
+Important: Only use indices that appear in your current browser_get_state output.
```

### tools.file_editor.vision_disabled

```diff
--- main+++ pr@@ -1,6 +1,7 @@ Custom editing tool for viewing, creating and editing files in plain-text format
 * State is persistent across command calls and discussions with the user
 * If `path` is a text file, `view` displays the result of applying `cat -n`. If `path` is a directory, `view` lists non-hidden files and directories up to 2 levels deep
+
 * The `create` command cannot be used if the specified `path` already exists as a file
 * If a `command` generates a long output, it will be truncated and marked with `<response clipped>`
 * The `undo_edit` command will revert the last edit made to the file at `path`
@@ -29,6 +30,5 @@ 
 Remember: when making multiple file edits in a row to the same file, you should prefer to send all edits in a single message with multiple calls to this tool, rather than multiple messages with a single call each.
 
-
 Your current working directory is: /workspace/project/software-agent-sdk/.pr/description_workspace_shared
 When exploring project structure, start with this directory instead of the root filesystem.
```

### tools.file_editor.vision_enabled

```diff
--- main+++ pr@@ -1,7 +1,9 @@ Custom editing tool for viewing, creating and editing files in plain-text format
 * State is persistent across command calls and discussions with the user
+* If `path` is a text file, `view` displays the result of applying `cat -n`. If `path` is a directory, `view` lists non-hidden files and directories up to 2 levels deep
+
 * If `path` is an image file (.png, .jpg, .jpeg, .gif, .webp, .bmp), `view` displays the image content
-* If `path` is a text file, `view` displays the result of applying `cat -n`. If `path` is a directory, `view` lists non-hidden files and directories up to 2 levels deep
+
 * The `create` command cannot be used if the specified `path` already exists as a file
 * If a `command` generates a long output, it will be truncated and marked with `<response clipped>`
 * The `undo_edit` command will revert the last edit made to the file at `path`
@@ -30,6 +32,5 @@ 
 Remember: when making multiple file edits in a row to the same file, you should prefer to send all edits in a single message with multiple calls to this tool, rather than multiple messages with a single call each.
 
-
 Your current working directory is: /workspace/project/software-agent-sdk/.pr/description_workspace_shared
 When exploring project structure, start with this directory instead of the root filesystem.
```

### tools.gemini.edit

```diff
--- main+++ pr@@ -24,6 +24,5 @@ - Create file: edit(file_path="new.py", old_string="", new_string="print('hello')")
 - Multiple replacements: edit(file_path="test.py", old_string="foo", new_string="bar", expected_replacements=3)
 
-
 Your current working directory is: /workspace/project/software-agent-sdk/.pr/description_workspace_shared
 File paths can be absolute or relative to this directory.
```

### tools.gemini.list_directory

```diff
--- main+++ pr@@ -16,6 +16,5 @@ - List specific directory: list_directory(dir_path="/path/to/dir")
 - List recursively: list_directory(dir_path="/path/to/dir", recursive=True)
 
-
 Your current working directory is: /workspace/project/software-agent-sdk/.pr/description_workspace_shared
 Relative paths will be resolved from this directory.
```

### tools.gemini.read_file

```diff
--- main+++ pr@@ -10,6 +10,5 @@ - Read entire file: read_file(file_path="/path/to/file.py")
 - Read with pagination: read_file(file_path="/path/to/file.py", offset=100, limit=50)
 
-
 Your current working directory is: /workspace/project/software-agent-sdk/.pr/description_workspace_shared
 File paths can be absolute or relative to this directory.
```

### tools.gemini.write_file

```diff
--- main+++ pr@@ -15,6 +15,5 @@ - Create new file: write_file(file_path="/path/to/new.py", content="print('hello')")
 - Overwrite file: write_file(file_path="/path/to/existing.py", content="new content")
 
-
 Your current working directory is: /workspace/project/software-agent-sdk/.pr/description_workspace_shared
 File paths can be absolute or relative to this directory.
```

### tools.glob.description

```diff
--- main+++ pr@@ -10,6 +10,5 @@ - Find Python test files: "**/test_*.py"
 - Find configuration files: "**/*.{json,yaml,yml,toml}"
 
-
 Your current working directory is: /workspace/project/software-agent-sdk/.pr/description_workspace_shared
 When searching for files, patterns are relative to this directory.
```

### tools.grep.description

```diff
--- main+++ pr@@ -1,11 +1,10 @@ Fast content search tool.
 * Searches file contents using regular expressions
-* Supports full regex syntax (eg. "log.*Error", "function\s+\w+", etc.)
+* Supports full regex syntax (eg. "log.*Error", "function\\s+\\w+", etc.)
 * Filter files by pattern with the include parameter (eg. "*.js", "*.{ts,tsx}")
 * Returns matching file paths sorted by modification time.
 * Only the first 100 results are returned. Consider narrowing your search with stricter regex patterns or provide path parameter if you need more results.
 * Use this tool when you need to find files containing specific patterns.
 
-
 Your current working directory is: /workspace/project/software-agent-sdk/.pr/description_workspace_shared
 When searching for content, searches are performed in this directory.
```

### tools.planning_file_editor.vision_disabled

```diff
--- main+++ pr@@ -1,6 +1,7 @@ Custom editing tool for viewing, creating and editing files in plain-text format
 * State is persistent across command calls and discussions with the user
 * If `path` is a text file, `view` displays the result of applying `cat -n`. If `path` is a directory, `view` lists non-hidden files and directories up to 2 levels deep
+
 * The `create` command cannot be used if the specified `path` already exists as a file
 * If a `command` generates a long output, it will be truncated and marked with `<response clipped>`
 * The `undo_edit` command will revert the last edit made to the file at `path`
@@ -29,7 +30,6 @@ 
 Remember: when making multiple file edits in a row to the same file, you should prefer to send all edits in a single message with multiple calls to this tool, rather than multiple messages with a single call each.
 
-
 IMPORTANT RESTRICTION FOR PLANNING AGENT:
 * You can VIEW any file in the workspace using the 'view' command
 * You can ONLY EDIT the PLAN.md file (all other edit operations will be rejected)
@@ -37,7 +37,6 @@ * All editing commands (create, str_replace, insert, undo_edit) are restricted to PLAN.md only
 * The PLAN.md file already contains the required section structure - you just need to fill in the content
 
-
 Your current working directory: /workspace/project/software-agent-sdk/.pr/description_workspace_shared
 Your PLAN.md location: /workspace/project/software-agent-sdk/.pr/description_workspace_shared/.agents_tmp/PLAN.md
 This plan file will be accessible to other agents in the workflow.
```

### tools.planning_file_editor.vision_enabled

```diff
--- main+++ pr@@ -1,6 +1,9 @@ Custom editing tool for viewing, creating and editing files in plain-text format
 * State is persistent across command calls and discussions with the user
 * If `path` is a text file, `view` displays the result of applying `cat -n`. If `path` is a directory, `view` lists non-hidden files and directories up to 2 levels deep
+
+* If `path` is an image file (.png, .jpg, .jpeg, .gif, .webp, .bmp), `view` displays the image content
+
 * The `create` command cannot be used if the specified `path` already exists as a file
 * If a `command` generates a long output, it will be truncated and marked with `<response clipped>`
 * The `undo_edit` command will revert the last edit made to the file at `path`
@@ -29,7 +32,6 @@ 
 Remember: when making multiple file edits in a row to the same file, you should prefer to send all edits in a single message with multiple calls to this tool, rather than multiple messages with a single call each.
 
-
 IMPORTANT RESTRICTION FOR PLANNING AGENT:
 * You can VIEW any file in the workspace using the 'view' command
 * You can ONLY EDIT the PLAN.md file (all other edit operations will be rejected)
@@ -37,7 +39,6 @@ * All editing commands (create, str_replace, insert, undo_edit) are restricted to PLAN.md only
 * The PLAN.md file already contains the required section structure - you just need to fill in the content
 
-
 Your current working directory: /workspace/project/software-agent-sdk/.pr/description_workspace_shared
 Your PLAN.md location: /workspace/project/software-agent-sdk/.pr/description_workspace_shared/.agents_tmp/PLAN.md
 This plan file will be accessible to other agents in the workflow.
```

### tools.terminal.description

```diff
--- main+++ pr@@ -25,4 +25,4 @@ 
 ### Terminal Reset
 * Terminal reset: If the terminal becomes unresponsive, you can set the "reset" parameter to `true` to create a new terminal session. This will terminate the current session and start fresh.
-* Warning: Resetting the terminal will lose all previously set environment variables, working directory changes, and any running processes. Use this only when the terminal stops responding to commands.
+* Warning: Resetting the terminal will lose all previously set environment variables, working directory changes, and any running processes. Use this only when the terminal stops responding to commands.
```
