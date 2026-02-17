"""
Jarvis Protocol — Script Generation Tool
Generates scripts (Python, Bash, JS, etc.) and saves them to the sandbox.
Can optionally execute Python scripts in a controlled environment.
"""
import asyncio
import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import SANDBOX_DIR

logger = logging.getLogger("jarvis.tools.scripts")


def generate_script(
    filename: str,
    content: str,
    language: str = "python",
    description: str = ""
) -> dict:
    """Save a generated script to the sandbox directory."""
    try:
        # Ensure proper extension
        ext_map = {
            "python": ".py",
            "javascript": ".js",
            "bash": ".sh",
            "powershell": ".ps1",
            "batch": ".bat",
            "html": ".html",
            "css": ".css",
            "json": ".json",
            "yaml": ".yml",
            "typescript": ".ts",
        }

        if not Path(filename).suffix:
            filename += ext_map.get(language.lower(), ".txt")

        scripts_dir = SANDBOX_DIR / "scripts"
        scripts_dir.mkdir(exist_ok=True)

        filepath = scripts_dir / filename
        filepath.write_text(content, encoding="utf-8")

        logger.info(f"Script generated: {filepath}")
        return {
            "success": True,
            "path": str(filepath.resolve()),
            "filename": filename,
            "language": language,
            "size": len(content),
            "description": description
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


async def execute_python_script(
    filepath: str,
    timeout: int = 30,
    args: list[str] = None
) -> dict:
    """
    Execute a Python script in the sandbox.
    Only scripts in the sandbox directory can be executed.
    """
    try:
        p = Path(filepath)

        # Safety: only execute from sandbox
        if not str(p.resolve()).startswith(str(SANDBOX_DIR.resolve())):
            return {
                "success": False,
                "error": "Can only execute scripts from the sandbox directory",
                "requires_approval": True
            }

        if not p.exists():
            return {"success": False, "error": f"Script not found: {filepath}"}

        cmd = [sys.executable, str(p)]
        if args:
            cmd.extend(args)

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(SANDBOX_DIR)
            )
        )

        return {
            "success": result.returncode == 0,
            "stdout": result.stdout[:5000] if result.stdout else "",
            "stderr": result.stderr[:2000] if result.stderr else "",
            "return_code": result.returncode,
            "script": str(p)
        }

    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"Script timed out after {timeout}s"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def list_scripts() -> list[dict]:
    """List all scripts in the sandbox."""
    scripts_dir = SANDBOX_DIR / "scripts"
    if not scripts_dir.exists():
        return []

    scripts = []
    for f in sorted(scripts_dir.iterdir()):
        if f.is_file():
            scripts.append({
                "name": f.name,
                "path": str(f.resolve()),
                "size": f.stat().st_size,
                "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                "language": _detect_language(f.suffix)
            })
    return scripts


def _detect_language(ext: str) -> str:
    """Detect language from file extension."""
    lang_map = {
        ".py": "python", ".js": "javascript", ".ts": "typescript",
        ".sh": "bash", ".ps1": "powershell", ".bat": "batch",
        ".html": "html", ".css": "css", ".json": "json", ".yml": "yaml",
    }
    return lang_map.get(ext.lower(), "text")
