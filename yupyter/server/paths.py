"""Safe mapping from URL/relative paths to files under the server root."""
from __future__ import annotations

from pathlib import Path


class PathError(Exception):
    def __init__(self, status: int, msg: str) -> None:
        super().__init__(msg)
        self.status, self.msg = status, msg


NOTEBOOK_SUFFIXES = (".md",)


def hidden_name(name: str) -> bool:
    """Dotfiles and the server's own artifacts (assets dirs, tmp/old swaps)."""
    return name.startswith(".") or name.endswith((".assets", ".tmp", ".old"))


def resolve_rel(root: Path, rel: str | None, *, notebook: bool = False) -> Path:
    """Resolve ``rel`` (posix, relative to ``root``) to an absolute path inside ``root``.

    ``root`` must already be resolved. Raises PathError(403) for absolute paths,
    ``..`` and symlink escapes, PathError(404) for hidden names.
    """
    rel = rel or ""
    if "\x00" in rel or "\\" in rel:
        raise PathError(400, "bad path")
    if rel.startswith("/") or Path(rel).is_absolute():
        raise PathError(403, "path outside root")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if ".." in parts:
        raise PathError(403, "path outside root")
    if any(hidden_name(p) for p in parts):
        raise PathError(404, "not found")
    p = (root.joinpath(*parts)).resolve()
    if not p.is_relative_to(root):
        raise PathError(403, "path outside root")
    if notebook:
        if p == root or p.suffix.lower() not in NOTEBOOK_SUFFIXES:
            raise PathError(400, "not a notebook (.md) path")
        if p.is_dir():
            raise PathError(400, "is a directory")
    return p


def rel_of(root: Path, p: Path) -> str:
    return "." if p == root else p.relative_to(root).as_posix()


def clean_filename(name: str) -> str:
    """Validate a bare file name for a new/renamed notebook; appends ``.md``."""
    name = (name or "").strip()
    if not name or "/" in name or "\\" in name or "\x00" in name or len(name) > 200:
        raise PathError(400, "invalid name")
    if name.startswith(".") or name in (".", ".."):
        raise PathError(400, "invalid name")
    if not name.lower().endswith(NOTEBOOK_SUFFIXES):
        name += ".md"
    if hidden_name(name):
        raise PathError(400, "invalid name")
    return name
