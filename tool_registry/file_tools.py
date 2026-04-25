"""Built-in file tools — read, write, edit, glob, grep.

All tools include:
* Path traversal protection (no escape from allowed roots)
* Encoding detection (UTF-8 with fallback)
* File size limits (no accidental giant reads)
* Structured ``ToolResult`` responses
"""

from __future__ import annotations

import difflib
import os
import re
import stat
from pathlib import Path
from typing import Any, Callable

from .base import BaseTool, ParamSpec, ToolResult


# ── Helpers ──────────────────────────────────────────────────────────────────


def _safe_path(path: str, allowed_roots: tuple[str, ...] | None = None) -> Path:
    """Resolve and validate a path, preventing traversal attacks."""
    resolved = Path(path).resolve()
    if allowed_roots:
        allowed = [Path(r).resolve() for r in allowed_roots]
        if not any(str(resolved).startswith(str(a)) for a in allowed):
            raise PermissionError(
                f"Path '{resolved}' is not in allowed roots: {allowed_roots}"
            )
    return resolved


def _read_file_content(path: Path, max_bytes: int = 1_000_000) -> str:
    """Read a file with encoding detection and size limit."""
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if not path.is_file():
        raise IsADirectoryError(f"Not a file: {path}")

    # Size check
    size = path.stat().st_size
    if size > max_bytes:
        return (
            f"File too large ({size:,} bytes, max {max_bytes:,}). "
            f"Showing first {max_bytes:,} bytes:\n"
            + path.read_text(encoding="utf-8", errors="replace")[:max_bytes]
            + f"\n... truncated ({size - max_bytes:,} bytes omitted)"
        )

    # Try UTF-8 first, fall back to latin-1
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            return path.read_text(encoding="latin-1")
        except Exception as exc:
            raise ValueError(f"Cannot decode file: {exc}")


def _format_file_list(files: list[Path], base: Path | None = None) -> str:
    """Format a list of files relative to a base path."""
    if not files:
        return "(no matches)"
    lines = []
    for f in files:
        display = str(f.relative_to(base)) if base else str(f)
        try:
            size = f.stat().st_size
            mtime = f.stat().st_mtime
            lines.append(f"{display}  ({size:,} bytes)")
        except OSError:
            lines.append(display)
    return "\n".join(lines)


# ── Configuration ────────────────────────────────────────────────────────────


CURRENT_DIR = Path.cwd()

FILE_TOOL_DEFAULTS = {
    "allowed_roots": (str(CURRENT_DIR),),
    "max_read_bytes": 1_000_000,
    "max_write_bytes": 500_000,
}


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Read
# ═══════════════════════════════════════════════════════════════════════════════


class ReadTool(BaseTool):
    """Read a file from the filesystem. Returns the full content."""

    name = "read"
    description = "Read the contents of a file at the given path"
    parameters = (
        ParamSpec("file_path", description="Absolute or relative path to the file"),
        ParamSpec(
            "offset",
            type="integer",
            description="Starting line number (1-based)",
            required=False,
        ),
        ParamSpec(
            "limit",
            type="integer",
            description="Maximum number of lines to return",
            required=False,
        ),
    )

    def __init__(
        self,
        allowed_roots: tuple[str, ...] | None = None,
        max_bytes: int = FILE_TOOL_DEFAULTS["max_read_bytes"],
    ) -> None:
        self._allowed = allowed_roots or FILE_TOOL_DEFAULTS["allowed_roots"]
        self._max_bytes = max_bytes

    async def execute(
        self,
        file_path: str,
        offset: int | None = None,
        limit: int | None = None,
    ) -> ToolResult:
        try:
            path = _safe_path(file_path, self._allowed)
            content = _read_file_content(path, self._max_bytes)
        except (FileNotFoundError, PermissionError, IsADirectoryError, ValueError) as exc:
            return ToolResult.err(str(exc))
        except Exception as exc:
            return ToolResult.err(f"Failed to read '{file_path}': {exc}")

        if offset is not None or limit is not None:
            lines = content.splitlines(keepends=True)
            start = (offset - 1) if offset else 0
            end = start + limit if limit else len(lines)
            selected = lines[start:end]
            content = "".join(selected)
            info = f" (lines {start + 1}–{start + len(selected)} of {len(lines)})"
        else:
            info = ""

        return ToolResult.ok(
            text=f"--- {file_path}{info}\n{content}",
            data=content,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Write
# ═══════════════════════════════════════════════════════════════════════════════


class WriteTool(BaseTool):
    """Write or overwrite a file with new content."""

    name = "write"
    description = "Write content to a file (creates or overwrites)"
    parameters = (
        ParamSpec("file_path", description="Path to the file to write"),
        ParamSpec("content", description="Content to write"),
        ParamSpec(
            "mode",
            description='Write mode: "write" (overwrite) or "append" (append to end)',
            required=False,
        ),
        ParamSpec(
            "insert_at",
            type="integer",
            description="Line number to insert content at (1-based). Cannot be used with append mode.",
            required=False,
        ),
        ParamSpec(
            "create_parents",
            type="boolean",
            description="Create parent directories if they don't exist",
            required=False,
        ),
    )

    def __init__(
        self,
        allowed_roots: tuple[str, ...] | None = None,
        max_bytes: int = FILE_TOOL_DEFAULTS["max_write_bytes"],
        content_validator: Callable[[str], str | None] | None = None,
    ) -> None:
        self._allowed = allowed_roots or FILE_TOOL_DEFAULTS["allowed_roots"]
        self._max_bytes = max_bytes
        self._content_validator = content_validator

    async def execute(
        self,
        file_path: str,
        content: str,
        mode: str = "write",
        insert_at: int | None = None,
        create_parents: bool = True,
    ) -> ToolResult:
        # Size check
        if len(content) > self._max_bytes:
            return ToolResult.err(
                f"Content too large ({len(content):,} bytes, max {self._max_bytes:,})"
            )

        # Encoding validation
        try:
            content.encode("utf-8")
        except UnicodeEncodeError as exc:
            return ToolResult.err(f"Content is not valid UTF-8: {exc}")

        # Content validation hook
        if self._content_validator is not None:
            error = self._content_validator(content)
            if error is not None:
                return ToolResult.err(error)

        # Mode validation
        if mode not in ("write", "append"):
            return ToolResult.err(f"Invalid mode '{mode}'. Use 'write' or 'append'.")
        if mode == "append" and insert_at is not None:
            return ToolResult.err("Cannot use both append mode and insert_at together.")

        try:
            path = _safe_path(file_path, self._allowed)
        except PermissionError as exc:
            return ToolResult.err(str(exc))

        # Parent directory check
        if not create_parents and not path.parent.exists():
            return ToolResult.err(f"Parent directory does not exist: {path.parent}")

        # Content assembly
        try:
            if mode == "append" and path.exists():
                existing = path.read_text(encoding="utf-8")
                content = existing + content

            elif insert_at is not None and path.exists():
                lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
                if insert_at < 1:
                    return ToolResult.err("insert_at must be >= 1")
                idx = min(insert_at - 1, len(lines))
                lines.insert(idx, content if content.endswith("\n") else content + "\n")
                content = "".join(lines)
        except Exception as exc:
            return ToolResult.err(f"Failed to read '{file_path}' for editing: {exc}")

        # Write
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        except PermissionError as exc:
            return ToolResult.err(str(exc))
        except Exception as exc:
            return ToolResult.err(f"Failed to write '{file_path}': {exc}")

        return ToolResult.ok(
            text=f"Written {len(content):,} bytes to {file_path}",
            data={"path": str(path), "bytes": len(content)},
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Edit (line-based)
# ═══════════════════════════════════════════════════════════════════════════════


class EditTool(BaseTool):
    """Edit a file by replacing text at specific locations."""

    name = "edit"
    description = "Replace text in a file (substring or regex, with optional line-range targeting)"
    parameters = (
        ParamSpec("file_path", description="Path to the file to edit"),
        ParamSpec("old_string", description="Text to find and replace (substring or regex pattern)"),
        ParamSpec("new_string", description="New text to insert"),
        ParamSpec(
            "start_line",
            type="integer",
            description="Start line for search range (1-based, inclusive)",
            required=False,
        ),
        ParamSpec(
            "end_line",
            type="integer",
            description="End line for search range (1-based, inclusive)",
            required=False,
        ),
        ParamSpec(
            "use_regex",
            type="boolean",
            description="Treat old_string as a regex pattern instead of literal substring",
            required=False,
        ),
        ParamSpec(
            "occurrence",
            type="integer",
            description="Which occurrence to replace: 0=first (default), -1=all, N=Nth (1-based)",
            required=False,
        ),
    )

    def __init__(self, allowed_roots: tuple[str, ...] | None = None) -> None:
        self._allowed = allowed_roots or FILE_TOOL_DEFAULTS["allowed_roots"]

    async def execute(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        start_line: int | None = None,
        end_line: int | None = None,
        use_regex: bool = False,
        occurrence: int = 0,
    ) -> ToolResult:
        try:
            path = _safe_path(file_path, self._allowed)
            content = _read_file_content(path, max_bytes=10_000_000)
        except (FileNotFoundError, PermissionError, ValueError) as exc:
            return ToolResult.err(str(exc))
        except Exception as exc:
            return ToolResult.err(f"Failed to read '{file_path}': {exc}")

        # Validate line-range params
        if start_line is not None and start_line < 1:
            return ToolResult.err("start_line must be >= 1")
        if end_line is not None and end_line < 1:
            return ToolResult.err("end_line must be >= 1")
        if start_line is not None and end_line is not None and end_line < start_line:
            return ToolResult.err("end_line must be >= start_line")

        # Slice to line range if specified
        lines = content.splitlines(keepends=True)
        s = (start_line - 1) if start_line is not None else 0
        e = end_line if end_line is not None else len(lines)
        if start_line is not None and s >= len(lines):
            return ToolResult.err(
                f"start_line {start_line} is beyond file length ({len(lines)} lines)"
            )

        before = "".join(lines[:s])
        region = "".join(lines[s:e])
        after = "".join(lines[e:])

        # Search and replace
        changes = 0
        try:
            if use_regex:
                pattern = re.compile(old_string)
                count = 1 if occurrence == 0 else (0 if occurrence == -1 else occurrence)
                new_region, changes_made = pattern.subn(new_string, region, count=count)
                changes = changes_made
            else:
                if occurrence == 0:
                    # First occurrence
                    idx = region.find(old_string)
                    if idx == -1:
                        changes = 0
                        new_region = region
                    else:
                        new_region = region[:idx] + new_string + region[idx + len(old_string):]
                        changes = 1
                elif occurrence == -1:
                    # All occurrences
                    new_region = region.replace(old_string, new_string)
                    changes = region.count(old_string)
                else:
                    # Nth occurrence (1-based)
                    idx = -1
                    found = 0
                    for _ in range(occurrence):
                        prev = idx
                        idx = region.find(old_string, idx + 1)
                        if idx == -1:
                            return ToolResult.err(
                                f"Occurrence {occurrence} not found "
                                f"(only {found} found in search range)"
                            )
                        found += 1
                    new_region = region[:idx] + new_string + region[idx + len(old_string):]
                    changes = 1
        except re.error as exc:
            return ToolResult.err(f"Invalid regex pattern: {exc}")

        if changes == 0:
            return ToolResult.err(
                f"String not found in '{file_path}': "
                f"{old_string[:80]}{'...' if len(old_string) > 80 else ''}"
            )

        # Reassemble
        new_content = before + new_region + after

        # Compute diff
        diff_lines = list(difflib.unified_diff(
            content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=file_path,
            tofile=file_path,
        ))
        diff_text = "".join(diff_lines[:200])  # cap at 200 lines

        # Write
        try:
            path.write_text(new_content, encoding="utf-8")
        except PermissionError as exc:
            return ToolResult.err(str(exc))
        except Exception as exc:
            return ToolResult.err(f"Failed to write '{file_path}': {exc}")

        msg = f"Edited {file_path}: replaced {changes} occurrence(s)"
        if diff_text:
            msg += f"\n{diff_text}"
        return ToolResult.ok(
            text=msg,
            data={"path": str(path), "occurrences": changes, "diff": diff_text},
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Glob (file search by pattern)
# ═══════════════════════════════════════════════════════════════════════════════


class GlobTool(BaseTool):
    """Find files matching a glob pattern."""

    name = "glob"
    description = "Search for files and directories matching a glob pattern"
    parameters = (
        ParamSpec("pattern", description="Glob pattern (e.g. '**/*.py', 'src/**/*.ts')"),
        ParamSpec(
            "path",
            description="Base directory (defaults to current dir)",
            required=False,
        ),
        ParamSpec(
            "max_results",
            type="integer",
            description="Maximum number of results",
            required=False,
        ),
    )

    def __init__(
        self,
        allowed_roots: tuple[str, ...] | None = None,
        max_results: int = 100,
    ) -> None:
        self._allowed = allowed_roots or FILE_TOOL_DEFAULTS["allowed_roots"]
        self._max_results = max_results

    async def execute(
        self,
        pattern: str,
        path: str | None = None,
        max_results: int | None = None,
    ) -> ToolResult:
        limit = min(max_results or self._max_results, 500)

        try:
            search_root = _safe_path(path or ".", self._allowed)
            if not search_root.is_dir():
                return ToolResult.err(f"Not a directory: {search_root}")

            # Use glob with recursive support
            if pattern.startswith("**"):
                matched = list(search_root.glob(pattern))
            elif "**" in pattern or "*" in pattern:
                matched = list(search_root.glob(pattern))
            else:
                # No wildcards — treat as suffix match
                matched = list(search_root.rglob(pattern))

            # Filter out non-files if pattern explicitly has an extension
            if "." in pattern and "*" not in pattern:
                matched = [f for f in matched if f.is_file()]

            matched.sort()

            if len(matched) > limit:
                matched = matched[:limit]
                note = f" (showing first {limit} of {len(matched)} results)"
            else:
                note = ""

            formatted = _format_file_list(matched, search_root)

        except PermissionError as exc:
            return ToolResult.err(str(exc))
        except Exception as exc:
            return ToolResult.err(f"Glob failed: {exc}")

        summary = f"Found {len(matched)} file(s) matching '{pattern}'{note}"
        return ToolResult.ok(
            text=f"{summary}\n{formatted}" if formatted else summary,
            data={"files": [str(f) for f in matched], "count": len(matched)},
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Grep (content search)
# ═══════════════════════════════════════════════════════════════════════════════


class GrepTool(BaseTool):
    """Search file contents for a pattern (regex or literal)."""

    name = "grep"
    description = "Search file contents using a regular expression pattern"
    parameters = (
        ParamSpec("pattern", description="Regular expression or text to find"),
        ParamSpec(
            "path",
            description="Directory or file to search (defaults to current dir)",
            required=False,
        ),
        ParamSpec(
            "glob",
            description="File glob filter (e.g. '*.py', '**/*.ts')",
            required=False,
        ),
        ParamSpec(
            "max_results",
            type="integer",
            description="Maximum matches to return",
            required=False,
        ),
        ParamSpec(
            "context",
            type="integer",
            description="Lines of context before and after each match",
            required=False,
        ),
        ParamSpec(
            "case_sensitive",
            type="boolean",
            description="Case-sensitive search (default: false)",
            required=False,
        ),
    )

    def __init__(
        self,
        allowed_roots: tuple[str, ...] | None = None,
        max_results: int = 50,
    ) -> None:
        self._allowed = allowed_roots or FILE_TOOL_DEFAULTS["allowed_roots"]
        self._max_results = max_results

    async def execute(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        max_results: int | None = None,
        context: int | None = None,
        case_sensitive: bool = False,
    ) -> ToolResult:
        limit = min(max_results or self._max_results, 200)

        try:
            search_root = _safe_path(path or ".", self._allowed)
        except PermissionError as exc:
            return ToolResult.err(str(exc))

        if not search_root.exists():
            return ToolResult.err(f"Path not found: {search_root}")
        if search_root.is_file():
            files = [search_root]
        else:
            files = list(search_root.rglob(glob or "*"))
            files = [f for f in files if f.is_file()]

        # Skip binary-like files
        text_extensions = {
            ".py", ".js", ".ts", ".jsx", ".tsx", ".md", ".txt", ".json", ".yaml",
            ".yml", ".toml", ".cfg", ".ini", ".conf", ".sh", ".bash", ".zsh",
            ".html", ".css", ".scss", ".less", ".xml", ".svg", ".sql", ".rb",
            ".java", ".kt", ".scala", ".go", ".rs", ".c", ".cpp", ".h", ".hpp",
            ".cs", ".php", ".r", ".swift", ".ex", ".exs", ".elm", ".clj", ".cljs",
            ".erl", ".hrl", ".lua", ".pl", ".pm", ".t", ".ps1", ".bat", ".cmd",
            ".env", ".gitignore", ".dockerignore", ".editorconfig",
        }
        # Only auto-filter when glob is not specified (user chose *everything)
        if not glob:
            files = [f for f in files if f.suffix.lower() in text_extensions]

        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            regex = re.compile(pattern, flags)
        except re.error as exc:
            return ToolResult.err(f"Invalid regex: {exc}")

        results: list[dict] = []
        seen_files: set[str] = set()
        total_matches = 0

        for file in files:
            if len(results) >= limit:
                break
            try:
                lines = file.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception:
                continue

            ctx_lines = context or 0
            for i, line in enumerate(lines):
                if regex.search(line):
                    total_matches += 1
                    if len(results) >= limit:
                        break
                    start = max(0, i - ctx_lines)
                    end = min(len(lines), i + ctx_lines + 1)
                    snippet = "\n".join(
                        f"{j + 1:>6}: {lines[j]}"
                        for j in range(start, end)
                    )
                    results.append({
                        "file": str(file),
                        "line": i + 1,
                        "snippet": snippet,
                    })
                    seen_files.add(str(file))
            else:
                continue

        if not results:
            return ToolResult.ok(
                text=f"No matches for '{pattern}' in {search_root}",
                data={"matches": [], "total": 0},
            )

        # Format output
        lines_out: list[str] = []
        current_file = ""
        for r in results:
            if r["file"] != current_file:
                current_file = r["file"]
                lines_out.append(f"\n{current_file}:")
            lines_out.append(r["snippet"])

        formatted = "\n".join(lines_out)
        note = f" (showing {len(results)} of {total_matches} matches)" if total_matches > len(results) else ""

        return ToolResult.ok(
            text=f"Found {total_matches} match(es) in {len(seen_files)} file(s){note}\n{formatted}",
            data={"matches": results, "total": total_matches, "files": list(seen_files)},
        )
