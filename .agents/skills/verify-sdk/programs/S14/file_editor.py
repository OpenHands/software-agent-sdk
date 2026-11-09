"""Drive the file editor through a scripted agent over a small project.

The program lays out files a user's repository might hold (a nested tree, a
hidden file, an image, an ASCII PDF, a binary blob, an oversized log, a
Latin-1 file and a file holding a secret), then a scripted agent views and
edits them with `file_editor`. Reports each named step and the files after.
"""

import base64

from S14.toolkit import converse, tool_results, workspace
from sdk_verify import emit

from openhands.sdk import Tool
from openhands.tools.file_editor import FileEditorTool


ws = workspace()
(ws / "src" / "pkg" / "deep").mkdir(parents=True, exist_ok=True)
(ws / "src" / "pkg" / "deep" / "third_level.py").write_text("x = 3\n")
(ws / "src" / "pkg" / "mod.py").write_text("y = 2\n")
(ws / ".hidden").write_text("dot\n")
(ws / "pixel.png").write_bytes(
    base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg=="
    )
)
(ws / "doc.pdf").write_bytes(
    b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n"
    b"BT (Quarterly Report) Tj ET\n%%EOF\n"
)
(ws / "blob.bin").write_bytes(bytes(range(256)) * 8)
(ws / "huge.log").write_bytes(b"a" * (11 * 1024 * 1024))
(ws / "latin.txt").write_bytes("café = 1\nname = 'old'\n".encode("latin-1"))
(ws / "config.env").write_text("API_TOKEN=tok-9f8e7d6c5b\n")

steps = [
    ("view_dir", {"command": "view", "path": "{ws}"}),
    (
        "create",
        {
            "command": "create",
            "path": "{ws}/src/app.py",
            "file_text": "def greet():\n    return 'hi'\n\n"
            "def bye():\n    return 'hi'\n",
        },
    ),
    (
        "create_again",
        {"command": "create", "path": "{ws}/src/app.py", "file_text": "x"},
    ),
    ("view_file", {"command": "view", "path": "{ws}/src/app.py"}),
    (
        "view_range",
        {"command": "view", "path": "{ws}/src/app.py", "view_range": [4, 5]},
    ),
    (
        "replace",
        {
            "command": "str_replace",
            "path": "{ws}/src/app.py",
            "old_str": "def bye():\n    return 'hi'",
            "new_str": "def bye():\n    return 'bye'",
        },
    ),
    (
        "replace_ambiguous",
        {
            "command": "str_replace",
            "path": "{ws}/src/app.py",
            "old_str": "return",
            "new_str": "yield",
        },
    ),
    (
        "replace_missing",
        {
            "command": "str_replace",
            "path": "{ws}/src/app.py",
            "old_str": "no such text",
            "new_str": "x",
        },
    ),
    (
        "insert",
        {
            "command": "insert",
            "path": "{ws}/src/app.py",
            "insert_line": 0,
            "new_str": "import os",
        },
    ),
    ("undo", {"command": "undo_edit", "path": "{ws}/src/app.py"}),
    ("relative", {"command": "view", "path": "src/app.py"}),
    ("missing", {"command": "view", "path": "{ws}/nope.txt"}),
    ("image", {"command": "view", "path": "{ws}/pixel.png"}),
    ("pdf", {"command": "view", "path": "{ws}/doc.pdf"}),
    ("binary", {"command": "view", "path": "{ws}/blob.bin"}),
    ("huge", {"command": "view", "path": "{ws}/huge.log"}),
    (
        "latin",
        {
            "command": "str_replace",
            "path": "{ws}/latin.txt",
            "old_str": "'old'",
            "new_str": "'new'",
        },
    ),
    ("secret", {"command": "view", "path": "{ws}/config.env"}),
]
script = [{"tool": "file_editor", "args": args} for _, args in steps] + [
    {"finish": "done"}
]
conversation = converse(
    [Tool(name=FileEditorTool.name)], script, secrets={"API_TOKEN": "tok-9f8e7d6c5b"}
)
rows = [r for r in tool_results(conversation, clip=20000) if r["tool"] == "file_editor"]
named = dict(zip([n for n, _ in steps], rows))
conversation.close()

latin = (ws / "latin.txt").read_bytes()
listing = named["view_dir"]["text"]
facts = {
    "dir_lists_second_level": "/src/pkg/" in listing,
    "dir_lists_third_level": "/src/pkg/mod.py" in listing,
    "dir_lists_hidden": "/.hidden" in listing,
    "dir_notes_hidden": "1 hidden files/directories" in listing,
}
for row in named.values():  # keep the result small: images are base64
    row["text"] = row["text"][:600]
emit(
    {
        "ok": True,
        "steps": named,
        "app_py": (ws / "src" / "app.py").read_text(),
        **facts,
        "latin_still_latin1": latin.decode("latin-1") == "café = 1\nname = 'new'\n",
        "secret_leaked": "tok-9f8e7d6c5b" in str(rows),
    }
)
