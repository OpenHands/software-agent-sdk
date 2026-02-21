import re


CMD_OUTPUT_PS1_BEGIN = "\n###PS1JSON###\n"
CMD_OUTPUT_PS1_END = "\n###PS1END###"
# Regex to match PS1 metadata blocks. Uses negative lookahead to handle corruption
# scenarios where concurrent output causes nested ###PS1JSON### markers. This ensures
# we match only the LAST ###PS1JSON### before each ###PS1END###.
CMD_OUTPUT_METADATA_PS1_REGEX = re.compile(
    rf"^{CMD_OUTPUT_PS1_BEGIN.strip()}((?:(?!{CMD_OUTPUT_PS1_BEGIN.strip()}).)*?){CMD_OUTPUT_PS1_END.strip()}",
    re.DOTALL | re.MULTILINE,
)

# Default max size for command output content
# to prevent too large observations from being saved in the stream
# This matches the default max_message_chars in LLM class
MAX_CMD_OUTPUT_SIZE: int = 30000


# Common timeout message that can be used across different timeout scenarios
TIMEOUT_MESSAGE_TEMPLATE = (
    "You may wait longer to see additional output by sending empty command '', "
    "send other commands to interact with the current process, send keys "
    '("C-c", "C-z", "C-d") '
    "to interrupt/kill the previous command before sending your new command, "
    "or use the timeout parameter in terminal for future commands."
)

# How long to wait with no new output before considering it a no-change timeout
NO_CHANGE_TIMEOUT_SECONDS = 30

# How often to poll for new output in seconds
POLL_INTERVAL = 0.5
HISTORY_LIMIT = 10_000
