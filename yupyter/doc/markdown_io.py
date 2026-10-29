"""Markdown <-> Doc conversion (milestone 3 fills this in)."""
from __future__ import annotations

from pathlib import Path

from pycrdt import Doc


def load_markdown(text: str) -> Doc:
    """Parse a notebook ``.md`` into a new Doc. Stub: not implemented yet."""
    raise NotImplementedError("markdown loading is not implemented yet")


def dump_markdown(doc: Doc, assets_dir: Path) -> str:
    """Serialise ``doc`` to markdown; large/binary outputs go in ``assets_dir``."""
    raise NotImplementedError("markdown saving is not implemented yet")
