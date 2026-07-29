"""Example adapter: AVI three-image folders to the generic manifest protocol.

This is intentionally an example at the platform boundary. The generic
multi-image pipeline does not depend on these file names or metadata fields.

Usage:
    avi_manifest_adapter.py <sample-folder-or-root> <manifest.jsonl>
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any


IMAGE_FILES = ("defect.jpg", "diff.jpg", "gt.jpg")
IMAGE_LABELS = ("AOI检出图", "差分图", "GT参考图")
TRAINING_PROMPT = (
    "请对比AOI检出图、差分图和GT参考图，分析疑似区域，并给出判断依据和最终标签。"
)


def _sample_directories(root: Path) -> Iterator[Path]:
    if all((root / name).is_file() for name in (*IMAGE_FILES, "meta.json")):
        yield root
        return
    for meta_path in sorted(root.rglob("meta.json")):
        sample = meta_path.parent
        if all((sample / name).is_file() for name in IMAGE_FILES):
            yield sample


def _relative_path(path: Path, output: Path) -> str:
    return os.path.relpath(path.resolve(), output.parent.resolve())


def convert(root_path: str, output_path: str) -> int:
    root = Path(root_path).resolve()
    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output.open("w", encoding="utf-8") as handle:
        for sample in _sample_directories(root):
            meta: dict[str, Any] = json.loads(
                (sample / "meta.json").read_text(encoding="utf-8")
            )
            references = {key: meta[key] for key in ("label", "note") if key in meta}
            metadata = {
                key: value for key, value in meta.items() if key not in references
            }
            record = {
                "sample_id": str(meta.get("id") or sample.relative_to(root)),
                "image_paths": [
                    _relative_path(sample / name, output) for name in IMAGE_FILES
                ],
                "image_labels": list(IMAGE_LABELS),
                "prompt": TRAINING_PROMPT,
                "reference_annotations": references,
                "metadata": metadata,
            }
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1
    return count


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(
            "usage: avi_manifest_adapter.py <sample-folder-or-root> <manifest.jsonl>"
        )
    print(convert(sys.argv[1], sys.argv[2]))
