"""
Jarvis Protocol — File Management Tool
Read/write files with approval gates and diff-mode support.
All generated files go to the sandbox directory by default.
"""
import difflib
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import SANDBOX_DIR, PROJECT_ROOT

logger = logging.getLogger("jarvis.tools.files")

# ── Safety: allowed base directories for writes ──
ALLOWED_WRITE_DIRS = [
    SANDBOX_DIR,
    PROJECT_ROOT / "data",
]


def _is_safe_path(path: Path) -> bool:
    """Check if a path is within allowed write directories."""
    resolved = path.resolve()
    return any(
        str(resolved).startswith(str(allowed.resolve()))
        for allowed in ALLOWED_WRITE_DIRS
    )


def read_file(path: str) -> dict:
    """Read a file and return its contents."""
    try:
        p = Path(path)
        if not p.exists():
            return {"success": False, "error": f"File not found: {path}"}
        if not p.is_file():
            return {"success": False, "error": f"Not a file: {path}"}
        if p.stat().st_size > 1_000_000:  # 1MB limit
            return {"success": False, "error": "File too large (>1MB)"}

        content = p.read_text(encoding="utf-8", errors="replace")
        return {
            "success": True,
            "path": str(p.resolve()),
            "content": content,
            "size": len(content),
            "extension": p.suffix,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def write_file(path: str, content: str, force: bool = False) -> dict:
    """
    Write content to a file.
    By default, files are written to the sandbox directory.
    Writing outside sandbox requires force=True.
    """
    try:
        p = Path(path)

        # If relative path, resolve to sandbox
        if not p.is_absolute():
            p = SANDBOX_DIR / p

        # Safety check
        if not force and not _is_safe_path(p):
            return {
                "success": False,
                "error": f"Cannot write outside sandbox without approval. Path: {p}",
                "requires_approval": True,
                "proposed_path": str(p)
            }

        # Create parent directories
        p.parent.mkdir(parents=True, exist_ok=True)

        # Generate diff if file exists
        diff_text = None
        if p.exists():
            old_content = p.read_text(encoding="utf-8", errors="replace")
            diff_lines = list(difflib.unified_diff(
                old_content.splitlines(keepends=True),
                content.splitlines(keepends=True),
                fromfile=f"a/{p.name}",
                tofile=f"b/{p.name}",
            ))
            if diff_lines:
                diff_text = "".join(diff_lines)

        p.write_text(content, encoding="utf-8")

        logger.info(f"File written: {p} ({len(content)} bytes)")
        return {
            "success": True,
            "path": str(p.resolve()),
            "size": len(content),
            "diff": diff_text,
            "is_new": diff_text is None and not p.exists()
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def list_directory(path: str = None) -> dict:
    """List contents of a directory."""
    try:
        p = Path(path) if path else SANDBOX_DIR
        if not p.exists():
            return {"success": False, "error": f"Directory not found: {path}"}
        if not p.is_dir():
            return {"success": False, "error": f"Not a directory: {path}"}

        items = []
        for entry in sorted(p.iterdir()):
            items.append({
                "name": entry.name,
                "type": "directory" if entry.is_dir() else "file",
                "size": entry.stat().st_size if entry.is_file() else None,
                "modified": datetime.fromtimestamp(entry.stat().st_mtime).isoformat()
            })

        return {
            "success": True,
            "path": str(p.resolve()),
            "items": items,
            "count": len(items)
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def delete_file(path: str, force: bool = False) -> dict:
    """Delete a file (only from sandbox unless forced)."""
    try:
        p = Path(path)
        if not p.exists():
            return {"success": False, "error": f"File not found: {path}"}

        if not force and not _is_safe_path(p):
            return {
                "success": False,
                "error": "Cannot delete files outside sandbox without approval",
                "requires_approval": True
            }

        p.unlink()
        logger.info(f"File deleted: {p}")
        return {"success": True, "path": str(p)}
    except Exception as e:
        return {"success": False, "error": str(e)}
