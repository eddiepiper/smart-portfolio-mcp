"""write_atomic, copied verbatim from ai-hedge-fund hedge_fund/paths.py (MIT)."""
import os
from pathlib import Path


def write_atomic(path: Path, text: str) -> None:
    """Write *text* to *path* via a sibling temp file and rename, so a crash
    mid-write leaves the old file intact rather than a truncated one."""
    path = Path(path)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)
