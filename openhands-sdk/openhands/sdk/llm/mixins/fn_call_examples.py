"""In-context learning examples for non-native function calling.

This module contains the tool example snippets and the logic to assemble them
into a single in-context learning prompt.  It is intentionally separated from
the conversion logic in ``fn_call_converter`` so that the large data literals
don't clutter the algorithmic code.
"""

import sys
from typing import Final

from litellm import ChatCompletionToolParam


# Tool name constants used to map tool definitions to example keys
TERMINAL_TOOL_NAME: Final[str] = "terminal"
STR_REPLACE_EDITOR_TOOL_NAME: Final[str] = "file_editor"
BROWSER_TOOL_NAME: Final[str] = "browser"
FINISH_TOOL_NAME: Final[str] = "finish"
LLM_BASED_EDIT_TOOL_NAME: Final[str] = "edit_file"
TASK_TRACKER_TOOL_NAME: Final[str] = "task_tracker"


def _refine_prompt(prompt: str) -> str:
    if sys.platform == "win32":
        return prompt.replace("bash", "powershell")
    return prompt


# NOTE: we need to make sure these examples are always in-sync with the tool
# interface designed in openhands/agenthub/agent/function_calling.py

# Example snippets for each tool
TOOL_EXAMPLES = {
    "bash": {
        "check_dir": """
ASSISTANT: Sure! Let me first check the current directory:
<function=terminal>
<parameter=command>
pwd && ls
</parameter>
</function>

USER: EXECUTION RESULT of [terminal]:
/workspace
openhands@runtime:~/workspace$
""",  # noqa: E501
        "run_server": """
ASSISTANT:
Let me run the Python file for you:
<function=terminal>
<parameter=command>
python3 app.py > server.log 2>&1 &
</parameter>
</function>

USER: EXECUTION RESULT of [terminal]:
[1] 121
[1]+  Exit 1                  python3 app.py > server.log 2>&1

ASSISTANT:
Looks like the server was running with PID 121 then crashed. Let me check the server log:
<function=terminal>
<parameter=command>
cat server.log
</parameter>
</function>

USER: EXECUTION RESULT of [terminal]:
Traceback (most recent call last):
  File "/workspace/app.py", line 2, in <module>
    from flask import Flask
ModuleNotFoundError: No module named 'flask'

ASSISTANT:
Looks like the server crashed because the `flask` module is not installed. Let me install the `flask` module for you:
<function=terminal>
<parameter=command>
pip3 install flask
</parameter>
</function>

USER: EXECUTION RESULT of [terminal]:
Defaulting to user installation because normal site-packages is not writeable
Collecting flask
  Using cached flask-3.0.3-py3-none-any.whl (101 kB)
Collecting blinker>=1.6.2
  Using cached blinker-1.7.0-py3-none-any.whl (13 kB)
Collecting Werkzeug>=3.0.0
  Using cached werkzeug-3.0.2-py3-none-any.whl (226 kB)
Collecting click>=8.1.3
  Using cached click-8.1.7-py3-none-any.whl (97 kB)
Collecting itsdangerous>=2.1.2
  Using cached itsdangerous-2.2.0-py3-none-any.whl (16 kB)
Requirement already satisfied: Jinja2>=3.1.2 in /home/openhands/.local/lib/python3.10/site-packages (from flask) (3.1.3)
Requirement already satisfied: MarkupSafe>=2.0 in /home/openhands/.local/lib/python3.10/site-packages (from Jinja2>=3.1.2->flask) (2.1.5)
Installing collected packages: Werkzeug, itsdangerous, click, blinker, flask
Successfully installed Werkzeug-3.0.2 blinker-1.7.0 click-8.1.7 flask-3.0.3 itsdangerous-2.2.0

ASSISTANT:
The `flask` module is installed successfully. Let me run the Python file again:
<function=terminal>
<parameter=command>
python3 app.py > server.log 2>&1 &
</parameter>
</function>

USER: EXECUTION RESULT of [terminal]:
[1] 124

ASSISTANT:
Let me check the server log again:
<function=terminal>
<parameter=command>
cat server.log
</parameter>
</function>

USER: EXECUTION RESULT of [terminal]:
* Serving Flask app 'app'
 * Debug mode: off
WARNING: This is a development server. Do not use it in a production deployment. Use a production WSGI server instead.
 * Running on http://127.0.0.1:5000
Press CTRL+C to quit

ASSISTANT:
The server is running on port 5000 with PID 124. You can access the list of numbers by visiting http://127.0.0.1:5000. If you have any further questions, feel free to ask!
""",  # noqa: E501
        "kill_server": """
USER: Now kill the server, make it display the numbers in a table format.

ASSISTANT:
Sure! Let me stop the server first:
<function=terminal>
<parameter=command>
kill 124
</parameter>
</function>

USER: EXECUTION RESULT of [terminal]:
[1]+  Terminated              python3 app.py > server.log 2>&1
""",  # noqa: E501
        "run_server_again": """
ASSISTANT:
Running the updated file:
<function=terminal>
<parameter=command>
python3 app.py > server.log 2>&1 &
</parameter>
</function>

USER: EXECUTION RESULT of [terminal]:
[1] 126

ASSISTANT:
The server is running on port 5000 with PID 126. You can access the list of numbers in a table format by visiting http://127.0.0.1:5000.
""",  # noqa: E501
    },
    "file_editor": {
        "create_file": """
ASSISTANT:
There is no `app.py` file in the current directory. Let me create a Python file `app.py`:
<function=str_replace_editor>
<parameter=command>create</parameter>
<parameter=path>/workspace/app.py</parameter>
<parameter=file_text>
from flask import Flask
app = Flask(__name__)

@app.route('/')
def index():
    numbers = list(range(1, 11))
    return str(numbers)

if __name__ == '__main__':
    app.run(port=5000)
</parameter>
</function>

USER: EXECUTION RESULT of [str_replace_editor]:
File created successfully at: /workspace/app.py
""",  # noqa: E501
        "edit_file": """
ASSISTANT:
Now let me display the numbers in a table format:
<function=str_replace_editor>
<parameter=command>str_replace</parameter>
<parameter=path>/workspace/app.py</parameter>
<parameter=old_str>return str(numbers)</parameter>
<parameter=new_str>return '<table>' + ''.join([f'<tr><td>{i}</td></tr>' for i in numbers]) + '</table>'</parameter>
</function>

USER: EXECUTION RESULT of [str_replace_editor]:
The file /workspace/app.py has been edited. Here's the result of running `cat -n` on a snippet of /workspace/app.py:
     3
     4  @app.route('/')
     5  def index():
     6      numbers = list(range(1, 11))
     7      return '<table>' + ''.join([f'<tr><td>{i}</td></tr>' for i in numbers]) + '</table>'
     8
     9  if __name__ == '__main__':
    10      app.run(port=5000)
Review the changes and make sure they are as expected. Edit the file again if necessary.

""",  # noqa: E501
    },
    "browser": {
        "view_page": """
ASSISTANT:
Let me check how the page looks in the browser:
<function=browser>
<parameter=code>
goto('http://127.0.0.1:5000')
noop(1000)  # Wait for page to load
</parameter>
</function>

USER: EXECUTION RESULT of [browser]:
[Browser shows the numbers in a table format]
"""  # noqa: E501
    },
    "edit_file": {
        "create_file": """
ASSISTANT: There is no `app.py` file in the current directory. Let me create a Python file `app.py`:
<function=edit_file>
<parameter=path>/workspace/app.py</parameter>
<parameter=start>1</parameter>
<parameter=end>-1</parameter>
<parameter=content>
from flask import Flask
app = Flask(__name__)

@app.route('/')
def index():
    numbers = list(range(1, 11))
    return str(numbers)

if __name__ == '__main__':
    app.run(port=5000)
</parameter>
</function>

USER: EXECUTION RESULT of [edit_file]:
File created successfully at: /workspace/app.py
""",  # noqa: E501
        "edit_file": """
ASSISTANT:
Now let me display the numbers in a table format:
<function=edit_file>
<parameter=path>/workspace/app.py</parameter>
<parameter=start>6</parameter>
<parameter=end>9</parameter>
<parameter=content>
    numbers = list(range(1, 11))
    return '<table>' + ''.join([f'<tr><td>{i}</td></tr>' for i in numbers]) + '</table>'
    # ... existing code ...
if __name__ == '__main__':
</parameter>
</function>

USER: EXECUTION RESULT of [edit_file]:
The file /workspace/app.py has been edited. Here's the result of running `cat -n` on a snippet of /workspace/app.py:
     3
     4  @app.route('/')
     5  def index():
     6      numbers = list(range(1, 11))
     7      return '<table>' + ''.join([f'<tr><td>{i}</td></tr>' for i in numbers]) + '</table>'
     8
     9  if __name__ == '__main__':
    10      app.run(port=5000)
Review the changes and make sure they are as expected. Edit the file again if necessary.
""",  # noqa: E501
    },
    "finish": {
        "example": """
ASSISTANT:
The server is running on port 5000 with PID 126. You can access the list of numbers in a table format by visiting http://127.0.0.1:5000. Let me know if you have any further requests!
<function=finish>
<parameter=message>The task has been completed. The web server is running and displaying numbers 1-10 in a table format at http://127.0.0.1:5000.</parameter>
</function>
"""  # noqa: E501
    },
    "task_tracker": {
        "view": """
ASSISTANT:
Let me check the current task list first:
<function=task_tracker>
<parameter=command>view</parameter>
</function>
""",
        "plan": """
I'll create or update the full plan based on your requirements and current progress:
<function=task_tracker>
<parameter=command>plan</parameter>
<parameter=task_list>
[
  {
    "title": "Initialize repo",
    "status": "done",
    "notes": "Repository created and README added."
  },
  {
    "title": "Implement nested param parsing",
    "status": "in_progress",
    "notes": "Add recursive parsing for array-typed parameters."
  }
]
</parameter>
</function>
""",
    },
}


def get_example_for_tools(tools: list[ChatCompletionToolParam]) -> str:
    """Generate an in-context learning example based on available tools."""
    available_tools = set()
    for tool in tools:
        if tool["type"] == "function":
            name = tool["function"]["name"]
            if name == TERMINAL_TOOL_NAME:
                available_tools.add("terminal")
            elif name == STR_REPLACE_EDITOR_TOOL_NAME:
                available_tools.add("file_editor")
            elif name == BROWSER_TOOL_NAME:
                available_tools.add("browser")
            elif name == FINISH_TOOL_NAME:
                available_tools.add("finish")
            elif name == LLM_BASED_EDIT_TOOL_NAME:
                available_tools.add("edit_file")
            elif name == TASK_TRACKER_TOOL_NAME:
                available_tools.add("task_tracker")

    if not available_tools:
        return ""

    example = """Here's a running example of how to perform a task with the provided tools.

--------------------- START OF EXAMPLE ---------------------

USER: Create a list of numbers from 1 to 10, and display them in a web page at port 5000.

"""  # noqa: E501

    # Build example based on available tools
    if "terminal" in available_tools:
        example += TOOL_EXAMPLES["bash"]["check_dir"]

    if "file_editor" in available_tools:
        example += TOOL_EXAMPLES["file_editor"]["create_file"]
    elif "edit_file" in available_tools:
        example += TOOL_EXAMPLES["edit_file"]["create_file"]

    if "terminal" in available_tools:
        example += TOOL_EXAMPLES["bash"]["run_server"]

    if "browser" in available_tools:
        example += TOOL_EXAMPLES["browser"]["view_page"]

    if "terminal" in available_tools:
        example += TOOL_EXAMPLES["bash"]["kill_server"]

    if "file_editor" in available_tools:
        example += TOOL_EXAMPLES["file_editor"]["edit_file"]
    elif "edit_file" in available_tools:
        example += TOOL_EXAMPLES["edit_file"]["edit_file"]

    if "terminal" in available_tools:
        example += TOOL_EXAMPLES["bash"]["run_server_again"]

    if "finish" in available_tools:
        example += TOOL_EXAMPLES["finish"]["example"]

    if "task_tracker" in available_tools:
        example += TOOL_EXAMPLES["task_tracker"]["view"]
        example += TOOL_EXAMPLES["task_tracker"]["plan"]

    example += """
--------------------- END OF EXAMPLE ---------------------

Do NOT assume the environment is the same as in the example above.

--------------------- NEW TASK DESCRIPTION ---------------------
"""  # noqa: E501
    example = example.lstrip()

    return _refine_prompt(example)
