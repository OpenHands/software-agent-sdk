# Docstring Guidelines for OpenHands SDK

This document describes the docstring conventions used in the OpenHands SDK to ensure
consistent, high-quality API documentation that renders correctly in our docs site.

## Style

We use **Google-style docstrings**. See the 
[Google Python Style Guide](https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings)
for full details.

## Key Requirements

### 1. Use Fenced Code Blocks for Examples

**Do NOT use `>>>` REPL-style examples.** These don't render well in Mintlify MDX.

❌ **Bad:**
```python
def my_function():
    """Do something.
    
    Example:
        >>> result = my_function()
        >>> print(result)
        42
    """
```

✅ **Good:**
```python
def my_function():
    """Do something.
    
    Example:
        ```python
        result = my_function()
        print(result)  # Output: 42
        ```
    """
```

### 2. Use Fenced Code Blocks for Shell/Config Examples

Shell commands and configuration examples must use fenced code blocks with the
appropriate language tag.

❌ **Bad:**
```python
def configure():
    """Configure the system.
    
    Example configuration:
    MY_VAR=value
    # This is a comment
    OTHER_VAR=other
    """
```

✅ **Good:**
```python
def configure():
    """Configure the system.
    
    Example configuration:

    ```bash
    MY_VAR=value
    # This is a comment
    OTHER_VAR=other
    ```
    """
```

### 3. Use Standard Section Headers

Use these section headers (with a colon):

- `Args:` - Function/method arguments
- `Returns:` - Return value description
- `Raises:` - Exceptions that may be raised
- `Attributes:` - Class attributes (for class docstrings)
- `Example:` or `Examples:` - Usage examples
- `Note:` or `Notes:` - Additional information

### 4. Document Arguments Consistently

Each argument should be on its own line with the format `name: description`.

```python
def process(data: str, options: dict | None = None) -> Result:
    """Process the input data.
    
    Args:
        data: The input data to process.
        options: Optional configuration options. Defaults to None.
    
    Returns:
        A Result object containing the processed output.
    
    Raises:
        ValueError: If data is empty.
        ProcessingError: If processing fails.
    """
```

### 5. Keep First Line Concise

The first line should be a brief summary that fits on one line (under 80 chars ideally).

```python
def complex_operation():
    """Perform a complex multi-step operation.
    
    This function coordinates multiple subsystems to achieve the desired
    outcome. It handles retries, logging, and cleanup automatically.
    
    ...
    """
```

### 6. Escape MDX-Sensitive Characters

When documenting code that contains curly braces `{}`, be aware these may be
interpreted as JSX expressions. Use fenced code blocks to avoid issues:

```python
def format_template():
    """Format a template string.
    
    The template uses curly braces for variables:

    ```python
    template = "Hello, {name}!"
    result = format_template(template, name="World")
    ```
    """
```

## Class Docstrings

For classes, document the class purpose and list important attributes:

```python
class Agent:
    """AI agent that executes tasks using tools and an LLM.
    
    The Agent class orchestrates interactions between a language model,
    available tools, and the execution environment.
    
    Attributes:
        llm: The language model instance.
        tools: List of available tools.
        name: Agent identifier.
    
    Example:
        ```python
        from openhands.sdk import Agent, LLM, Tool
        
        llm = LLM(model="claude-sonnet-4-20250514", api_key="...")
        agent = Agent(llm=llm, tools=[Tool(name="TerminalTool")])
        ```
    """
```

## Validation

We plan to add automated docstring validation using ruff's pydocstyle rules.
Until then, please manually ensure your docstrings follow these guidelines,
especially for public APIs.

## Why These Guidelines?

Our API documentation is auto-generated from docstrings using Python's `inspect`
module and rendered in Mintlify (MDX format). Following these guidelines ensures:

1. **Correct rendering** - Fenced code blocks render properly in MDX
2. **Consistency** - Uniform documentation across the codebase  
3. **Automation** - Docstrings can be parsed and transformed reliably
4. **Developer experience** - Clear, useful documentation for SDK users
