#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Check human-facing documentation and refresh public inventory manifests."""
from __future__ import annotations

import argparse
import copy
from datetime import datetime
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
import subprocess
import sys
import tempfile
from urllib.parse import unquote, urlsplit


DEFAULT_ROOT = Path(__file__).resolve().parent.parent
CHECKPOINT_MANIFEST = "CHECKPOINT-MANIFEST.json"
RELEASE_MANIFEST = "core/RELEASE-MANIFEST.json"
POLICY_PATH = "machine/documentation-policy.json"
INDEX_PATH = "machine/source-design-index.jsonl"
INDEX_SCHEMA_PATH = "machine/schemas/source-design-index.schema.json"
SOURCE_SCHEMA_PROFILE = "harness-source-index-profile/v1"
REFRESH_RECORD_PATH = "machine/history/documentation-refresh.json"
BASELINE_REVISION = "764bd88"
FIRST_PARTY_TRANSFORM = "first-party-documentation-revision"
REPORT_SCHEMA = "harness.documentation-report/v1"
RECORD_SCHEMA = "harness.documentation-refresh/v1"
TICK = chr(96)
CASE_MANIFESTS = {
    "splice": "examples/splice/CASE-MANIFEST.json",
    "yeast": "examples/yeast/CASE-MANIFEST.json",
}

INLINE_LINK = re.compile(r"!?\[([^\]]*)\]\(\s*(<[^>]+>|[^\s)]+)")
REFERENCE_DEFINITION = re.compile(r"^\s{0,3}\[([^\]]+)\]:\s*(<[^>]+>|\S+)")
RAW_URL = re.compile(r"(?i)\b(?:https?|ftp|mailto|file|codex):[^\s<>]+")
FULL_HEX = re.compile(r"(?i)(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])|(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")
SHORT_COMMIT = re.compile(
    r"(?i)\b(?:commit(?:\s+(?:id|sha))?|revision\s+(?:id|sha)|rev\s+(?:id|sha)|提交(?:号|ID)?)\s*[:=#]?\s*([0-9a-f]{7,39})\b"
)
ARCHIVE_POINTER_TARGET = re.compile(r"(?i)(?:^|[/#])archive-pointer-[0-9a-f]+(?:$|[/?#])")
PRIVATE_SEGMENT = re.compile(r"(?i)(?:^|[/\\])(?:private|internal)(?:[/\\]|$)")
DRIVE_PATH = re.compile(r"^[a-zA-Z]:[\\/]")
INLINE_CODE = re.compile(r"(" + re.escape(TICK) + r"+)(.*?)\1")
UNASSESSED = {"unknown", "not_assessed", "not_run", "not assessed", "not run"}


def _finding(findings: list[dict[str, object]], file: str, line: int, rule: str, message: str) -> None:
    findings.append({"file": file, "line": line, "rule": rule, "message": message})


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _is_reparse_or_link(path: Path, info: os.stat_result | None = None) -> bool:
    try:
        info = info or path.lstat()
    except OSError:
        return False
    return path.is_symlink() or bool(int(getattr(info, "st_file_attributes", 0)) & 0x400)


def _is_hard_link(info: os.stat_result) -> bool:
    return int(getattr(info, "st_nlink", 1)) > 1


def _matches(path: str, patterns: object) -> bool:
    if not isinstance(patterns, list):
        return False
    for pattern in patterns:
        if not isinstance(pattern, str):
            continue
        if fnmatch.fnmatchcase(path, pattern):
            return True
        # pathlib/fnmatch do not treat a leading **/ as matching zero
        # directories, while the documentation policy intends all Markdown.
        if pattern.startswith("**/") and fnmatch.fnmatchcase(path, pattern[3:]):
            return True
    return False


def _is_human_markdown(rel: str, policy: dict[str, object]) -> bool:
    if not rel.casefold().endswith(".md"):
        return False
    parts = tuple(part.casefold() for part in PurePosixPath(rel).parts)
    name = PurePosixPath(rel).name.casefold()
    if any(part in {"machine", "legal", "licenses", ".git", ".scratch", "__pycache__"} for part in parts):
        return False
    if name in {"license.md", "notice.md", "copyright.md"}:
        return False
    human = policy.get("human", {})
    if not isinstance(human, dict):
        human = {}
    legal = policy.get("legal", {})
    if not isinstance(legal, dict):
        legal = {}
    if _matches(rel, human.get("exclude", [])) or _matches(rel, legal.get("preserve_verbatim", [])):
        return False
    # These files are machine fixtures even though they contain Markdown prose.
    if rel.startswith("core/examples/verification/"):
        return False
    if rel == "core/examples/research-support/plan-seal/PLAN.md":
        return False
    return _matches(rel, human.get("include", ["**/*.md"]))


def _without_link_destinations(line: str) -> str:
    line = INLINE_LINK.sub(lambda match: match.group(1), line)
    line = REFERENCE_DEFINITION.sub(lambda match: f"[{match.group(1)}]:", line)
    return RAW_URL.sub("", line)


def _without_inline_code(line: str) -> str:
    return INLINE_CODE.sub("", line)


def _markdown_lines(text: str):
    marker: str | None = None
    marker_size = 0
    opener = re.compile(r"^\s{0,3}(" + re.escape(TICK) + r"{3,}|~{3,})")
    for number, line in enumerate(text.splitlines(), start=1):
        start = opener.match(line)
        if marker is None and start:
            run = start.group(1)
            marker, marker_size = run[0], len(run)
            yield number, line, True
            continue
        if marker is not None:
            closing = re.match(r"^\s{0,3}(" + re.escape(marker) + r"{" + str(marker_size) + r",})\s*$", line)
            yield number, line, True
            if closing:
                marker = None
                marker_size = 0
            continue
        yield number, line, False


def _inline_target(raw: str) -> str:
    target = raw.strip()
    if len(target) >= 2 and target[0] == "<" and target[-1] == ">":
        target = target[1:-1]
    return unquote(target)


def _iter_markdown_links(text: str):
    for number, raw_line, in_code in _markdown_lines(text):
        if in_code:
            continue
        line = _without_inline_code(raw_line)
        definition = REFERENCE_DEFINITION.match(line)
        if definition:
            yield number, definition.group(1), _inline_target(definition.group(2))
            continue
        visible_chars = list(line)
        for match in INLINE_LINK.finditer(line):
            yield number, match.group(1), _inline_target(match.group(2))
            for index in range(match.start(), match.end()):
                visible_chars[index] = " "
        visible_without_link_targets = "".join(visible_chars)
        for match in RAW_URL.finditer(visible_without_link_targets):
            target = match.group(0).rstrip(".,;:!?)]}")
            if target:
                yield number, target, target


def _safe_public_relative(raw: object) -> tuple[str | None, str | None]:
    if not isinstance(raw, str) or not raw or "\\" in raw:
        return None, "path is not a canonical public-root-relative path"
    path = PurePosixPath(raw)
    if (
        path.is_absolute()
        or PureWindowsPath(raw).drive
        or path.as_posix() != raw
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(":" in part for part in path.parts)
    ):
        return None, "path is not a canonical public-root-relative path"
    return path.as_posix(), None


def _public_file(root: Path, raw: object) -> tuple[bool, str]:
    rel, error = _safe_public_relative(raw)
    if error or rel is None:
        return False, "index path is not relative to the public root"
    if any(part.casefold() in {".git", ".scratch", "__pycache__"} for part in PurePosixPath(rel).parts):
        return False, "path is outside the public inventoried tree"
    target = root.joinpath(*PurePosixPath(rel).parts)
    current = root
    for part in PurePosixPath(rel).parts:
        current = current / part
        try:
            info = current.lstat()
        except OSError:
            return False, "index path does not name an existing public file"
        if _is_reparse_or_link(current, info):
            return False, "index path crosses a link"
        if current != target and not stat.S_ISDIR(info.st_mode):
            return False, "index path does not name an existing public file"
    try:
        info = target.lstat()
    except OSError:
        return False, "index path does not name an existing public file"
    if not stat.S_ISREG(info.st_mode):
        return False, "index path does not name an existing public file"
    try:
        target.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError):
        return False, "index path escapes the public root"
    return True, ""


def _json_pointer_error(document: object, fragment: str) -> str | None:
    pointer = unquote(fragment)
    if pointer == "":
        return None
    if not pointer.startswith("/"):
        return "JSON fragments must use an RFC 6901 pointer."

    current = document
    for raw_token in pointer[1:].split("/"):
        if re.search(r"~(?![01])", raw_token):
            return "The JSON Pointer contains an invalid escape."
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                return "The JSON Pointer names a missing object key."
            current = current[token]
            continue
        if isinstance(current, list):
            if not re.fullmatch(r"(?:0|[1-9][0-9]*)", token):
                return "JSON array pointers must use a canonical nonnegative integer index."
            index = int(token)
            if index >= len(current):
                return "The JSON Pointer array index is out of range."
            current = current[index]
            continue
        return "The JSON Pointer traverses a value without child keys."
    return None


def _check_public_borrowing_locator(
    root: Path,
    locator: object,
    line: int,
    findings: list[dict[str, object]],
    unassessed: list[dict[str, object]],
) -> None:
    if not isinstance(locator, str) or not locator.strip():
        _finding(findings, INDEX_PATH, line, "source-index-borrowing-locator", "A public borrowing locator must be a nonempty string.")
        return
    locator = locator.strip()
    try:
        parts = urlsplit(locator)
    except ValueError:
        _finding(findings, INDEX_PATH, line, "source-index-borrowing-public-locator", "The public borrowing locator is not a valid public-root-relative path or HTTP(S) URL.")
        return
    if parts.scheme.casefold() in {"http", "https"} and parts.netloc:
        unassessed.append({"file": INDEX_PATH, "line": line, "kind": "borrowing_locator", "status": "not_online_checked"})
        return
    if parts.scheme or parts.netloc or parts.query:
        _finding(findings, INDEX_PATH, line, "source-index-borrowing-public-locator", "The public borrowing locator must be root-relative or an HTTP(S) URL.")
        return
    local_path = unquote(parts.path)
    valid, message = _public_file(root, local_path)
    if not valid:
        _finding(findings, INDEX_PATH, line, "source-index-borrowing-public-locator", f"The public borrowing locator is invalid: {message}.")
        return
    if PurePosixPath(local_path).suffix.casefold() != ".json":
        return

    target = root.joinpath(*PurePosixPath(local_path).parts)
    try:
        document = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _finding(findings, INDEX_PATH, line, "source-index-borrowing-json", "The public JSON locator does not contain readable valid JSON.")
        return
    pointer_error = _json_pointer_error(document, parts.fragment)
    if pointer_error:
        _finding(findings, INDEX_PATH, line, "source-index-borrowing-json-pointer", pointer_error)


def _check_local_link(
    root: Path,
    doc_path: Path,
    rel: str,
    line: int,
    target: str,
    findings: list[dict[str, object]],
) -> None:
    value = target.strip()
    if not value or value.startswith("#") or value.startswith("//"):
        return
    if value.casefold().startswith("file:") or DRIVE_PATH.match(value) or value.startswith(("/", chr(92) * 2)):
        _finding(findings, rel, line, "markdown-link-private-target", "Local absolute targets are not public documentation links.")
        return
    parts = urlsplit(value)
    if parts.scheme:
        if parts.scheme.casefold() in {"http", "https", "ftp", "mailto", "tel", "data"}:
            if PRIVATE_SEGMENT.search(parts.path):
                _finding(findings, rel, line, "markdown-link-private-target", "A private or internal resource is linked from human documentation.")
            return
        _finding(findings, rel, line, "markdown-link-private-target", "Unsupported absolute target in human documentation.")
        return
    local_path = parts.path
    if not local_path:
        return
    if "\\" in local_path:
        _finding(findings, rel, line, "markdown-link-noncanonical", "Local Markdown targets must use forward slashes.")
        return
    if PRIVATE_SEGMENT.search(local_path):
        _finding(findings, rel, line, "markdown-link-private-target", "A private or internal resource is linked from human documentation.")
        return
    unresolved = doc_path.parent / PurePosixPath(local_path)
    candidate = unresolved.resolve(strict=False)
    try:
        candidate.relative_to(root.resolve(strict=True))
    except (OSError, ValueError):
        _finding(findings, rel, line, "markdown-link-escapes-root", "The relative target resolves outside the public root.")
        return
    candidate_parts = PurePosixPath(candidate.relative_to(root.resolve(strict=True)).as_posix()).parts
    if any(part.casefold() in {".git", ".scratch", "__pycache__"} for part in candidate_parts):
        _finding(findings, rel, line, "markdown-link-nonpublic-target", "The relative target is inside an ignored or generated directory.")
        return
    if not candidate.exists():
        _finding(findings, rel, line, "markdown-link-missing", "The local Markdown target does not exist.")
        return
    probe = doc_path.parent
    for part in PurePosixPath(local_path).parts:
        probe = probe / part
        if probe.is_symlink():
            _finding(findings, rel, line, "markdown-link-symlink", "The local Markdown target crosses a link.")
            break


def _scan_markdown(
    root: Path,
    policy: dict[str, object],
    findings: list[dict[str, object]],
) -> tuple[int, int]:
    markdown_count = 0
    link_count = 0
    human = policy.get("human", {})
    if not isinstance(human, dict):
        human = {}
    boilerplate = human.get("forbidden_boilerplate", [])
    verification_terms = human.get("forbidden_verification_terms", [])
    boilerplate = [item for item in boilerplate if isinstance(item, str) and item] if isinstance(boilerplate, list) else []
    verification_terms = [item for item in verification_terms if isinstance(item, str) and item] if isinstance(verification_terms, list) else []
    forbidden_link_prefixes = human.get("forbidden_link_prefixes", [])
    forbidden_link_prefixes = [item.casefold() for item in forbidden_link_prefixes if isinstance(item, str) and item] if isinstance(forbidden_link_prefixes, list) else []

    try:
        paths = sorted(
            path for path in root.rglob("*.md")
            if path.is_file() and not _is_reparse_or_link(path)
            and _is_human_markdown(path.relative_to(root).as_posix(), policy)
        )
    except OSError:
        _finding(findings, ".", 1, "markdown-inventory-unreadable", "The Markdown inventory could not be read.")
        return markdown_count, link_count

    for path in paths:
        rel = path.relative_to(root).as_posix()
        markdown_count += 1
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            _finding(findings, rel, 1, "markdown-unreadable", "The Markdown file is not readable as UTF-8.")
            continue
        for number, raw_line, in_code in _markdown_lines(content):
            verification_visible = _without_link_destinations(raw_line)
            if not in_code:
                prose_visible = _without_inline_code(verification_visible)
                for phrase in boilerplate:
                    if phrase.casefold() in prose_visible.casefold():
                        _finding(findings, rel, number, "forbidden-boilerplate", "Policy-listed defensive boilerplate appears in human-facing prose.")
                        break
            for term in verification_terms:
                if term.casefold() in verification_visible.casefold():
                    _finding(findings, rel, number, "forbidden-verification-term", "A policy-listed verification term appears in human-facing prose.")
                    break
            if FULL_HEX.search(verification_visible) or SHORT_COMMIT.search(verification_visible):
                _finding(findings, rel, number, "visible-hex-identifier", "A visible commit or hexadecimal identifier appears in human-facing prose.")

        for number, _label, target in _iter_markdown_links(content):
            link_count += 1
            if ARCHIVE_POINTER_TARGET.search(target):
                _finding(findings, rel, number, "archive-pointer-hex-target", "A legacy hexadecimal archive-pointer target is used.")
            if any(target.casefold().startswith(prefix) for prefix in forbidden_link_prefixes):
                _finding(findings, rel, number, "markdown-link-private-history", "A policy-listed private archive, conversation, or local file target is linked from human documentation.")
            else:
                _check_local_link(root, path, rel, number, target, findings)
    return markdown_count, link_count


def _json_path_line(text: str, value: str) -> int:
    encoded = json.dumps(value, ensure_ascii=False)
    expected = re.compile(r"^\s*\"path\"\s*:\s*" + re.escape(encoded) + r"\s*,?\s*$")
    for number, line in enumerate(text.splitlines(), start=1):
        if expected.match(line):
            return number
    return 1


def _check_case_manifests(
    root: Path,
    findings: list[dict[str, object]],
    *,
    allow_stale_readme_identity: bool = False,
) -> int:
    """Verify each public case artifact's published path and byte identity."""
    checked = 0
    for case_id, manifest_rel in CASE_MANIFESTS.items():
        manifest_path = root / manifest_rel
        manifest_exists, manifest_message = _public_file(root, manifest_rel)
        if not manifest_exists:
            _finding(findings, manifest_rel, 1, "case-manifest-public-path", f"The study case manifest is not a real public file: {manifest_message}.")
            continue
        try:
            text = manifest_path.read_text(encoding="utf-8")
            manifest = json.loads(text)
        except FileNotFoundError:
            _finding(findings, manifest_rel, 1, "case-manifest-missing", "The public study case manifest is missing.")
            continue
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            line = int(getattr(exc, "lineno", 1) or 1)
            _finding(findings, manifest_rel, line, "case-manifest-invalid", "The public study case manifest is unreadable or invalid JSON.")
            continue
        if not isinstance(manifest, dict):
            _finding(findings, manifest_rel, 1, "case-manifest-invalid", "The public study case manifest must be a JSON object.")
            continue
        if manifest.get("schema") != "public-checkpoint.study-case.v1" or manifest.get("case_id") != case_id:
            _finding(findings, manifest_rel, 1, "case-manifest-contract", "The study case schema or case id is unsupported.")
        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, list):
            _finding(findings, manifest_rel, 1, "case-artifacts-invalid", "The study case artifacts field must be an array.")
            continue
        seen: set[str] = set()
        for item in artifacts:
            if not isinstance(item, dict):
                _finding(findings, manifest_rel, 1, "case-artifact-invalid", "Each study case artifact must be an object.")
                continue
            artifact_rel, path_error = _safe_public_relative(item.get("path"))
            line = _json_path_line(text, item.get("path")) if isinstance(item.get("path"), str) else 1
            if path_error or artifact_rel is None:
                _finding(findings, manifest_rel, line, "case-artifact-path", "An artifact path is not a canonical case-relative path.")
                continue
            folded = artifact_rel.casefold()
            if folded in seen:
                _finding(findings, manifest_rel, line, "case-artifact-duplicate", "A case artifact path is listed more than once.")
                continue
            seen.add(folded)
            actual_rel = f"examples/{case_id}/{artifact_rel}"
            exists, message = _public_file(root, actual_rel)
            if not exists:
                _finding(findings, manifest_rel, line, "case-artifact-public-path", f"Artifact target is not a real public file: {message}.")
                continue
            published = item.get("published")
            if not isinstance(published, dict):
                _finding(findings, manifest_rel, line, "case-published-invalid", "Each artifact must have a published identity object.")
                continue
            expected_target = f"{case_id}/{artifact_rel}"
            if published.get("path") != expected_target:
                _finding(findings, manifest_rel, line, "case-published-path", "The artifact published path does not match its package-relative target.")
                continue
            size = published.get("bytes")
            digest = published.get("sha256")
            if isinstance(size, bool) or not isinstance(size, int) or size < 0 or not isinstance(digest, str) or not re.fullmatch(r"(?i)[0-9a-f]{64}", digest):
                _finding(findings, manifest_rel, line, "case-published-identity", "The artifact published identity must contain a byte count and SHA-256.")
                continue
            target_path = root.joinpath(*PurePosixPath(actual_rel).parts)
            try:
                actual_size, actual_digest = _identity(target_path)
            except OSError:
                _finding(findings, manifest_rel, line, "case-artifact-unreadable", "The published artifact could not be read for identity checking.")
                continue
            if size != actual_size or digest != actual_digest:
                is_readme = artifact_rel.casefold() == "readme.md"
                if not (allow_stale_readme_identity and is_readme):
                    _finding(findings, manifest_rel, line, "case-published-identity", "The published byte identity does not match its public artifact.")
            checked += 1
        if "readme.md" not in seen:
            _finding(findings, manifest_rel, 1, "case-readme-unlisted", "The study case must publish its human README artifact.")
    return checked


def _source_schema_profile_errors(schema: object, at: str = "$") -> list[str]:
    """Accept only the schema vocabulary used by this source-index contract.

    This is a bounded profile, not a general JSON Schema implementation. Unknown
    assertions and formats fail before records are checked, rather than being
    silently ignored. read_scope objects intentionally carry opaque annotations.
    """
    if not isinstance(schema, dict):
        return [f"{at}: the source-index profile requires an object schema"]
    supported = {
        "$schema", "$comment", "title", "description", "type", "properties",
        "additionalProperties", "required", "items", "minItems", "maxItems",
        "minLength", "minProperties", "minimum", "enum", "const", "pattern",
        "format", "oneOf", "allOf", "if", "then",
    }
    errors = [f"{at}: unsupported schema keyword {key}" for key in sorted(schema.keys() - supported)]
    for key in ("$schema", "$comment", "title", "description"):
        if key in schema and not isinstance(schema[key], str):
            errors.append(f"{at}.{key}: expected text")
    if "$schema" in schema and schema["$schema"] != "https://json-schema.org/draft/2020-12/schema":
        errors.append(f"{at}: unsupported schema dialect")
    if "type" in schema and (not isinstance(schema["type"], str) or schema["type"] not in {"object", "array", "string", "null", "boolean", "integer"}):
        errors.append(f"{at}: unsupported type declaration")
    for key in ("minItems", "maxItems", "minLength", "minProperties", "minimum"):
        if key in schema and (type(schema[key]) is not int or schema[key] < 0):
            errors.append(f"{at}.{key}: this profile requires a nonnegative integer limit")
    if "additionalProperties" in schema and type(schema["additionalProperties"]) is not bool:
        errors.append(f"{at}.additionalProperties: only a boolean is supported")
    if "required" in schema and (not isinstance(schema["required"], list) or any(not isinstance(key, str) for key in schema["required"])):
        errors.append(f"{at}.required: expected an array of property names")
    if "enum" in schema and (not isinstance(schema["enum"], list) or not schema["enum"] or any(not isinstance(value, str) for value in schema["enum"])):
        errors.append(f"{at}.enum: this profile supports nonempty text enums")
    if "const" in schema and not isinstance(schema["const"], str):
        errors.append(f"{at}.const: this profile supports text constants")
    if "pattern" in schema:
        try:
            re.compile(schema["pattern"])
        except (TypeError, re.error):
            errors.append(f"{at}.pattern: expected a valid Python-compatible regular expression")
    if "format" in schema and (not isinstance(schema["format"], str) or schema["format"] not in {"uri", "date-time"}):
        errors.append(f"{at}: unsupported format declaration")
    if "properties" in schema:
        if not isinstance(schema["properties"], dict):
            errors.append(f"{at}.properties: expected an object")
        else:
            for key, child in schema["properties"].items():
                errors.extend(_source_schema_profile_errors(child, f"{at}.properties.{key}"))
    for key in ("items", "if", "then"):
        if key in schema:
            errors.extend(_source_schema_profile_errors(schema[key], f"{at}.{key}"))
    for key in ("oneOf", "allOf"):
        if key in schema:
            if not isinstance(schema[key], list) or not schema[key]:
                errors.append(f"{at}.{key}: expected a nonempty array of object schemas")
            else:
                for number, child in enumerate(schema[key]):
                    errors.extend(_source_schema_profile_errors(child, f"{at}.{key}[{number}]"))
    if "then" in schema and "if" not in schema:
        errors.append(f"{at}: then requires if in this profile")
    return errors


def _source_format_valid(value: str, kind: str) -> bool:
    if kind == "uri":
        # Absolute ASCII URI, valid percent escapes, and an HTTP(S) authority.
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9+.-]*:[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]*", value) or re.search(r"%(?![0-9A-Fa-f]{2})", value):
            return False
        try:
            parts = urlsplit(value)
            if parts.scheme.lower() in {"http", "https"}:
                return bool(parts.hostname) and (parts.port is None or 0 <= parts.port <= 65535)
            return True
        except ValueError:
            return False
    # RFC3339 date-time with a declared timezone; leap seconds are unsupported.
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})", value):
        return False
    try:
        datetime.fromisoformat(value.upper().replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _source_schema_errors(value: object, schema: dict[str, object], at: str = "$") -> list[str]:
    """Validate a record against a preflighted source-index-profile schema."""
    errors: list[str] = []
    expected = schema.get("type")
    matches = {
        "object": isinstance(value, dict), "array": isinstance(value, list),
        "string": isinstance(value, str), "null": value is None,
        "boolean": type(value) is bool,
        "integer": type(value) is int or (type(value) is float and value.is_integer()),
    }
    if expected is not None and not matches[expected]:
        return [f"{at}: expected {expected}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{at}: value is outside the declared enum")
    if "const" in schema and value != schema["const"]:
        errors.append(f"{at}: value differs from the declared constant")
    if "oneOf" in schema:
        count = sum(not _source_schema_errors(value, child, at) for child in schema["oneOf"])
        if count != 1:
            errors.append(f"{at}: must match exactly one declared branch")
    for child in schema.get("allOf", []):
        errors.extend(_source_schema_errors(value, child, at))
    if "if" in schema and not _source_schema_errors(value, schema["if"], at) and "then" in schema:
        errors.extend(_source_schema_errors(value, schema["then"], at))
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        if len(value) < schema.get("minProperties", 0):
            errors.append(f"{at}: too few properties")
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{at}.{key}: required property is missing")
        for key, item in value.items():
            if key in properties:
                errors.extend(_source_schema_errors(item, properties[key], f"{at}.{key}"))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{at}.{key}: undeclared property")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or ("maxItems" in schema and len(value) > schema["maxItems"]):
            errors.append(f"{at}: array length is outside the declared bounds")
        if "items" in schema:
            for number, item in enumerate(value):
                errors.extend(_source_schema_errors(item, schema["items"], f"{at}[{number}]"))
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{at}: text is shorter than the declared minimum")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            errors.append(f"{at}: text does not match the declared pattern")
        if "format" in schema and not _source_format_valid(value, schema["format"]):
            errors.append(f"{at}: invalid {schema['format']} for the source-index profile")
    if "minimum" in schema and type(value) in {int, float} and value < schema["minimum"]:
        errors.append(f"{at}: value is below the declared minimum")
    return errors


def _check_document_note_identities(root: Path, findings: list[dict[str, object]]) -> int:
    """Check present top-level current identities; historical fields stay opaque."""
    notes_root = root / "machine" / "document-notes"
    if not notes_root.exists():
        return 0
    if not notes_root.is_dir() or _is_reparse_or_link(notes_root):
        _finding(findings, "machine/document-notes", 1, "document-note-public-path", "Document notes must use a real public directory.")
        return 0
    checked = 0
    for directory, children, files in os.walk(notes_root, followlinks=False):
        for child in children[:]:
            candidate = Path(directory) / child
            if _is_reparse_or_link(candidate):
                _finding(findings, candidate.relative_to(root).as_posix(), 1, "document-note-public-path", "Document notes must not cross a link.")
                children.remove(child)
        for name in sorted(files):
            if not name.endswith(".json"):
                continue
            path = Path(directory) / name
            rel = path.relative_to(root).as_posix()
            exists, message = _public_file(root, rel)
            if not exists:
                _finding(findings, rel, 1, "document-note-public-path", message)
                continue
            try:
                text = path.read_text(encoding="utf-8")
                note = json.loads(text)
            except (OSError, UnicodeError, json.JSONDecodeError):
                _finding(findings, rel, 1, "document-note-invalid", "The document note must contain readable JSON.")
                continue
            if not isinstance(note, dict):
                _finding(findings, rel, 1, "document-note-invalid", "The document note must be an object.")
                continue
            if "current_identity" not in note:
                continue
            checked += 1
            locations = [(len(line) - len(line.lstrip()), number) for number, line in enumerate(text.splitlines(), 1) if re.match(r'^\s*"current_identity"\s*:', line)]
            line = min(locations)[1] if locations else 1
            current = note["current_identity"]
            if not isinstance(current, dict) or type(current.get("bytes")) is not int or current["bytes"] < 0 or not isinstance(current.get("sha256"), str) or re.fullmatch(r"[0-9a-fA-F]{64}", current["sha256"]) is None:
                _finding(findings, rel, line, "document-note-current-identity", "current_identity requires a nonnegative integer byte count and SHA-256.")
                continue
            target = note.get("document", note.get("human_path", rel.removeprefix("machine/document-notes/").removesuffix(".json")))
            if "document" in note and "human_path" in note and note["document"] != note["human_path"]:
                _finding(findings, rel, line, "document-note-target", "document and human_path identify different documents.")
                continue
            exists, message = _public_file(root, target)
            if not exists:
                _finding(findings, rel, line, "document-note-target", f"The current document is not a public file: {message}.")
                continue
            try:
                size, digest = _identity(root / target)
            except OSError:
                _finding(findings, rel, line, "document-note-current-identity", "The current document could not be read.")
                continue
            if current["bytes"] != size or current["sha256"].lower() != digest:
                _finding(findings, rel, line, "document-note-current-identity", "The top-level current_identity does not match the current document bytes.")
    return checked


def _check_index(root: Path, findings: list[dict[str, object]]) -> tuple[int, list[dict[str, object]]]:
    index_path = root / INDEX_PATH
    if not index_path.is_file():
        _finding(findings, INDEX_PATH, 1, "source-index-missing", "The required machine-readable source/design index is missing.")
        return 0, []
    try:
        lines = index_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        _finding(findings, INDEX_PATH, 1, "source-index-unreadable", "The source/design index could not be read as UTF-8.")
        return 0, []
    source_schema = None
    exists, message = _public_file(root, INDEX_SCHEMA_PATH)
    if not exists:
        _finding(findings, INDEX_SCHEMA_PATH, 1, "source-index-schema-profile", f"The source-index schema is not a public file: {message}.")
    else:
        try:
            candidate_schema = _read_json(root / INDEX_SCHEMA_PATH)
            schema_errors = _source_schema_profile_errors(candidate_schema)
            for message in schema_errors:
                _finding(findings, INDEX_SCHEMA_PATH, 1, "source-index-schema-profile", message)
            if not schema_errors:
                source_schema = candidate_schema
        except (OSError, UnicodeError, json.JSONDecodeError):
            _finding(findings, INDEX_SCHEMA_PATH, 1, "source-index-schema-profile", "The source-index schema must contain readable valid JSON.")
    required = {"id", "sources", "design", "borrowing", "implementation", "acceptance"}
    seen: set[str] = set()
    record_count = 0
    unassessed: list[dict[str, object]] = []

    def require_text(obj: dict[str, object], key: str, file: str, line: int, rule: str) -> None:
        if not isinstance(obj.get(key), str) or not str(obj.get(key)).strip():
            _finding(findings, file, line, rule, f"Required field {key} must be a nonempty string.")

    def validate_path(raw: object, file: str, line: int, field: str) -> None:
        valid, message = _public_file(root, raw)
        if not valid:
            _finding(findings, file, line, "source-index-public-path", f"{field}: {message}.")

    for number, raw in enumerate(lines, start=1):
        if not raw.strip():
            continue
        record_count += 1
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            _finding(findings, INDEX_PATH, number, "source-index-json", "The JSONL row is not valid JSON.")
            continue
        if source_schema is not None:
            for message in _source_schema_errors(value, source_schema):
                _finding(findings, INDEX_PATH, number, "source-index-schema", message)
        if not isinstance(value, dict):
            _finding(findings, INDEX_PATH, number, "source-index-record", "Each JSONL row must be an object.")
            continue
        for key in sorted(required - value.keys()):
            _finding(findings, INDEX_PATH, number, "source-index-required-field", f"Required top-level field {key} is missing.")
        require_text(value, "id", INDEX_PATH, number, "source-index-id")
        record_id = value.get("id")
        if isinstance(record_id, str) and record_id.strip():
            normalized_id = record_id.strip().casefold()
            if normalized_id in seen:
                _finding(findings, INDEX_PATH, number, "source-index-duplicate-id", "The source/design record id is duplicated.")
            seen.add(normalized_id)

        sources = value.get("sources")
        if not isinstance(sources, list):
            _finding(findings, INDEX_PATH, number, "source-index-sources", "sources must be an array.")
        else:
            for source in sources:
                if not isinstance(source, dict):
                    _finding(findings, INDEX_PATH, number, "source-index-source", "Each source must be an object.")
                    continue
                for key in ("id", "kind", "title", "url"):
                    if key not in source:
                        _finding(findings, INDEX_PATH, number, "source-index-source-field", f"Source field {key} is missing.")
                for key in ("id", "kind", "title"):
                    require_text(source, key, INDEX_PATH, number, "source-index-source-field")
                if source.get("url") is not None and not isinstance(source.get("url"), str):
                    _finding(findings, INDEX_PATH, number, "source-index-source-field", "Source url must be a string or null.")

        design = value.get("design")
        if not isinstance(design, list):
            _finding(findings, INDEX_PATH, number, "source-index-design", "design must be an array.")
        else:
            for item in design:
                if not isinstance(item, dict):
                    _finding(findings, INDEX_PATH, number, "source-index-design", "Each design entry must be an object.")
                    continue
                for key in ("id", "path", "reason", "relation"):
                    if key not in item:
                        _finding(findings, INDEX_PATH, number, "source-index-design-field", f"Design field {key} is missing.")
                for key in ("id", "reason", "relation"):
                    require_text(item, key, INDEX_PATH, number, "source-index-design-field")
                validate_path(item.get("path"), INDEX_PATH, number, "design.path")

        borrowing = value.get("borrowing")
        if not isinstance(borrowing, dict):
            _finding(findings, INDEX_PATH, number, "source-index-borrowing", "borrowing must be an object.")
        else:
            require_text(borrowing, "summary", INDEX_PATH, number, "source-index-borrowing-field")
            require_text(borrowing, "mode", INDEX_PATH, number, "source-index-borrowing-field")
            evidence = borrowing.get("evidence")
            if not isinstance(evidence, list):
                _finding(findings, INDEX_PATH, number, "source-index-borrowing-evidence", "borrowing.evidence must be an array.")
            else:
                for item in evidence:
                    if not isinstance(item, dict):
                        _finding(findings, INDEX_PATH, number, "source-index-borrowing-evidence", "Each borrowing evidence entry must be an object.")
                        continue
                    availability = item.get("availability")
                    if not isinstance(availability, str) or availability not in {"public", "private_archive"}:
                        _finding(findings, INDEX_PATH, number, "source-index-borrowing-availability", "Evidence availability must be public or private_archive.")
                    locator = item.get("locator")
                    if not isinstance(locator, str) or not locator.strip():
                        _finding(findings, INDEX_PATH, number, "source-index-borrowing-locator", "Borrowing evidence must name a nonempty locator.")
                    elif availability == "public":
                        _check_public_borrowing_locator(root, locator, number, findings, unassessed)

        implementation = value.get("implementation")
        if not isinstance(implementation, list):
            _finding(findings, INDEX_PATH, number, "source-index-implementation", "implementation must be an array.")
        else:
            for item in implementation:
                if not isinstance(item, dict):
                    _finding(findings, INDEX_PATH, number, "source-index-implementation", "Each implementation entry must be an object.")
                    continue
                if "path" not in item or "status" not in item:
                    _finding(findings, INDEX_PATH, number, "source-index-implementation-field", "Implementation entries require path and status.")
                require_text(item, "status", INDEX_PATH, number, "source-index-implementation-field")
                validate_path(item.get("path"), INDEX_PATH, number, "implementation.path")
                status = item.get("status")
                if isinstance(status, str) and status.strip().casefold() in UNASSESSED:
                    unassessed.append({"file": INDEX_PATH, "line": number, "kind": "implementation", "status": status.strip()})

        acceptance = value.get("acceptance")
        if not isinstance(acceptance, list):
            _finding(findings, INDEX_PATH, number, "source-index-acceptance", "acceptance must be an array.")
        else:
            for item in acceptance:
                if not isinstance(item, dict):
                    _finding(findings, INDEX_PATH, number, "source-index-acceptance", "Each acceptance entry must be an object.")
                    continue
                for key in ("kind", "status", "public_path", "result"):
                    if key not in item:
                        _finding(findings, INDEX_PATH, number, "source-index-acceptance-field", f"Acceptance field {key} is missing.")
                require_text(item, "kind", INDEX_PATH, number, "source-index-acceptance-field")
                require_text(item, "status", INDEX_PATH, number, "source-index-acceptance-field")
                if "result" not in item:
                    _finding(findings, INDEX_PATH, number, "source-index-acceptance-field", "Acceptance field result is missing.")
                if item.get("public_path") is not None:
                    validate_path(item.get("public_path"), INDEX_PATH, number, "acceptance.public_path")
                if "archive_locator" in item and not isinstance(item.get("archive_locator"), str):
                    _finding(findings, INDEX_PATH, number, "source-index-archive-locator", "archive_locator must be a string when present.")
                status = item.get("status")
                if isinstance(status, str) and status.strip().casefold() in UNASSESSED:
                    unassessed.append({"file": INDEX_PATH, "line": number, "kind": "acceptance", "status": status.strip()})
    return record_count, unassessed


def check_root(root: Path, *, allow_stale_readme_identity: bool = False) -> dict[str, object]:
    """Check human Markdown and the machine source/design index."""
    requested_root = root.expanduser().absolute()
    if _is_reparse_or_link(requested_root):
        return {
            "schema": REPORT_SCHEMA,
            "status": "fail",
            "root_name": requested_root.name,
            "checked_markdown": 0,
            "checked_links": 0,
            "source_index_records": 0,
            "unassessed": [],
            "findings": [{"file": ".", "line": 1, "rule": "public-root-invalid", "message": "The public root must be a real directory."}],
        }
    try:
        root = root.resolve(strict=True)
    except OSError:
        root = root.absolute()
        return {
            "schema": REPORT_SCHEMA,
            "status": "fail",
            "root_name": root.name,
            "checked_markdown": 0,
            "checked_links": 0,
            "source_index_records": 0,
            "unassessed": [],
            "findings": [{"file": ".", "line": 1, "rule": "public-root-missing", "message": "The public root is not an accessible directory."}],
        }
    findings: list[dict[str, object]] = []
    if not root.is_dir() or _is_reparse_or_link(root):
        _finding(findings, ".", 1, "public-root-invalid", "The public root must be a real directory.")
        return {
            "schema": REPORT_SCHEMA,
            "status": "fail",
            "root_name": root.name,
            "checked_markdown": 0,
            "checked_links": 0,
            "source_index_records": 0,
            "unassessed": [],
            "findings": findings,
        }
    try:
        policy = _read_json(root / POLICY_PATH)
    except (OSError, UnicodeError, json.JSONDecodeError):
        policy = {}
        _finding(findings, POLICY_PATH, 1, "documentation-policy-invalid", "The documentation policy is missing or invalid JSON.")
    if not isinstance(policy, dict):
        policy = {}
        _finding(findings, POLICY_PATH, 1, "documentation-policy-invalid", "The documentation policy must be a JSON object.")
    markdown_count, link_count = _scan_markdown(root, policy, findings)
    case_artifact_count = _check_case_manifests(
        root,
        findings,
        allow_stale_readme_identity=allow_stale_readme_identity,
    )
    record_count, unassessed = _check_index(root, findings)
    note_identity_count = _check_document_note_identities(root, findings)
    findings.sort(key=lambda item: (str(item["file"]), int(item["line"]), str(item["rule"])))
    return {
        "schema": REPORT_SCHEMA,
        "status": "pass" if not findings else "fail",
        "root_name": root.name,
        "checked_markdown": markdown_count,
        "checked_links": link_count,
        "checked_case_artifacts": case_artifact_count,
        "source_index_records": record_count,
        "source_index_schema_profile": SOURCE_SCHEMA_PROFILE,
        "checked_document_current_identities": note_identity_count,
        "scientific_acceptance": "not_assessed",
        "unassessed": unassessed,
        "findings": findings,
    }


class RefreshError(RuntimeError):
    def __init__(self, rule: str, message: str, file: str = ".", line: int = 1):
        super().__init__(message)
        self.rule = rule
        self.message = message
        self.file = file
        self.line = line


def _git(root: Path, *args: str) -> bytes:
    safe_root = root.resolve(strict=True).as_posix()
    command = ["git", "-c", f"safe.directory={safe_root}", "-C", safe_root, *args]
    try:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    except OSError as exc:
        raise RefreshError("git-unavailable", "Git could not be started for the public root.") from exc
    if result.returncode != 0:
        raise RefreshError("git-command-failed", "A read-only Git query failed in the public root.")
    return result.stdout


def _git_text(root: Path, *args: str) -> str:
    try:
        return _git(root, *args).decode("utf-8").strip()
    except UnicodeError as exc:
        raise RefreshError("git-output-invalid", "Git returned non-UTF-8 repository metadata.") from exc


def _git_is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    safe_root = root.resolve(strict=True).as_posix()
    command = [
        "git",
        "-c",
        f"safe.directory={safe_root}",
        "-C",
        safe_root,
        "merge-base",
        "--is-ancestor",
        ancestor,
        descendant,
    ]
    try:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    except OSError as exc:
        raise RefreshError("git-unavailable", "Git could not be started for the public root.") from exc
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise RefreshError("git-command-failed", "A read-only Git ancestry query failed in the public root.")


def _git_context(root: Path, frozen_tag: str) -> dict[str, str]:
    root = root.resolve(strict=True)
    if not (root / ".git").exists() or _is_reparse_or_link(root / ".git"):
        raise RefreshError("public-git-repository-missing", "The public root does not contain its own Git repository.")
    git_root = Path(_git_text(root, "rev-parse", "--show-toplevel")).resolve(strict=True)
    if os.path.normcase(str(git_root)) != os.path.normcase(str(root)):
        raise RefreshError("public-git-root-mismatch", "Git resolved a repository root different from the requested public root.")
    head = _git_text(root, "rev-parse", "--verify", "HEAD^{commit}")
    baseline = _git_text(root, "rev-parse", "--verify", f"{BASELINE_REVISION}^{{commit}}")
    if not baseline.casefold().startswith(BASELINE_REVISION.casefold()):
        raise RefreshError("baseline-mismatch", "The frozen public baseline does not resolve to the requested revision.")
    tag_target = _git_text(root, "rev-parse", "--verify", f"refs/tags/{frozen_tag}^{{commit}}")
    if tag_target.casefold() != baseline.casefold():
        raise RefreshError("frozen-tag-moved", "The frozen release tag no longer points to the public baseline.")
    if not _git_is_ancestor(root, baseline, head):
        raise RefreshError("baseline-not-ancestor", "The frozen public baseline is not an ancestor of the current HEAD.")
    return {
        "root": str(git_root),
        "head": head,
        "baseline": baseline,
        "tag": frozen_tag,
        "tag_target": tag_target,
    }


def _safe_repo_path(raw: str) -> str:
    rel, error = _safe_public_relative(raw)
    if error or rel is None:
        raise RefreshError("git-path-invalid", "Git returned a noncanonical path in the public root.")
    return rel


def _excluded_payload_path(rel: str) -> bool:
    parts = PurePosixPath(rel).parts
    return (
        ".scratch" in parts
        or "__pycache__" in parts
        or rel.casefold().endswith((".pyc", ".pyo"))
    )


def _git_file_paths(root: Path) -> set[str]:
    tracked = _git(root, "ls-files", "--cached", "-z").split(b"\0")
    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
    values = {os.fsdecode(raw) for raw in tracked + untracked if raw}
    return {
        _safe_repo_path(value)
        for value in values
        if not _excluded_payload_path(value)
    }


def _legacy_actual_paths(root: Path) -> tuple[set[str], list[dict[str, str]]]:
    """Return paths visible to the old checkpoint verifier, plus unsafe paths."""
    actual: set[str] = set()
    problems: list[dict[str, str]] = []
    root = root.resolve(strict=True)
    for current, dirs, names in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        rel_dir = current_path.relative_to(root).as_posix()
        dirs[:] = sorted(dirs)
        names.sort()
        kept_dirs: list[str] = []
        for name in dirs:
            candidate = current_path / name
            rel = candidate.relative_to(root).as_posix()
            if rel_dir == "." and name in {".git", ".scratch"}:
                continue
            if ".scratch" in PurePosixPath(rel).parts:
                continue
            try:
                info = candidate.lstat()
            except OSError:
                problems.append({"path": rel, "rule": "inventory-unreadable"})
                continue
            if _is_reparse_or_link(candidate, info):
                problems.append({"path": rel, "rule": "inventory-link"})
            else:
                kept_dirs.append(name)
        dirs[:] = kept_dirs
        for name in names:
            candidate = current_path / name
            rel = candidate.relative_to(root).as_posix()
            if ".scratch" in PurePosixPath(rel).parts or (rel_dir == "." and name == ".git"):
                continue
            try:
                info = candidate.lstat()
            except OSError:
                problems.append({"path": rel, "rule": "inventory-unreadable"})
                continue
            if _is_reparse_or_link(candidate, info):
                problems.append({"path": rel, "rule": "inventory-link"})
            elif not stat.S_ISREG(info.st_mode):
                problems.append({"path": rel, "rule": "inventory-non-regular"})
            elif _is_hard_link(info):
                problems.append({"path": rel, "rule": "inventory-hard-link"})
            else:
                actual.add(rel)
    return actual, problems


def _collect_git_files(root: Path) -> dict[str, Path]:
    candidates = _git_file_paths(root)
    actual, problems = _legacy_actual_paths(root)
    if problems:
        raise RefreshError(
            problems[0]["rule"],
            "The public tree contains a link, unreadable path, or non-regular file.",
            file=str(problems[0].get("path", ".")),
        )
    missing = sorted(candidates - actual)
    ignored = sorted(actual - candidates)
    if missing:
        raise RefreshError("git-file-missing", "A tracked or unignored public file is missing from disk.")
    if ignored:
        raise RefreshError("legacy-inventory-extra-file", "The old verifier would see an ignored, cache, or generated file outside the Git inventory.")
    folded: set[str] = set()
    for rel in actual:
        key = rel.casefold()
        if key in folded:
            raise RefreshError("inventory-case-collision", "The public tree contains colliding paths on a case-insensitive filesystem.")
        folded.add(key)
    return {rel: root.joinpath(*PurePosixPath(rel).parts) for rel in sorted(actual)}


def _identity(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(block)
            digest.update(block)
    return size, digest.hexdigest()


def _load_manifest(root: Path, rel: str, schema: str) -> dict[str, object]:
    try:
        value = _read_json(root / rel)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RefreshError(
            "manifest-invalid",
            "An existing manifest is missing or invalid JSON.",
            file=rel,
            line=int(getattr(exc, "lineno", 1) or 1),
        ) from exc
    if not isinstance(value, dict) or value.get("schema") != schema or not isinstance(value.get("files"), list):
        raise RefreshError("manifest-contract-unsupported", "An existing manifest does not match its verifier contract.", file=rel)
    seen: set[str] = set()
    for item in value["files"]:
        if not isinstance(item, dict):
            raise RefreshError("manifest-entry-invalid", "An existing manifest contains a non-object file entry.", file=rel)
        rel_path, error = _safe_public_relative(item.get("path"))
        if error or rel_path is None:
            raise RefreshError("manifest-path-invalid", "An existing manifest contains a noncanonical path.", file=rel)
        key = rel_path.casefold()
        if key in seen:
            raise RefreshError("manifest-path-duplicate", "An existing manifest contains a duplicate path.", file=rel)
        seen.add(key)
    return value


def _entry_map(manifest: dict[str, object]) -> dict[str, dict[str, object]]:
    entries = manifest.get("files", [])
    assert isinstance(entries, list)
    return {str(entry["path"]): entry for entry in entries if isinstance(entry, dict)}


def _license_for_new(path: str, policy: dict[str, object]) -> str:
    section = policy.get("new_file_licenses", {})
    if not isinstance(section, dict):
        raise RefreshError("new-file-license-policy-invalid", "new_file_licenses must be a JSON object.")
    if path.casefold().endswith(".md"):
        markdown_license = section.get("**/*.md")
        if isinstance(markdown_license, str) and markdown_license:
            return markdown_license
    matches = [
        (len(pattern.replace("*", "").replace("?", "")), pattern, value)
        for pattern, value in section.items()
        if isinstance(pattern, str) and fnmatch.fnmatchcase(path, pattern) and isinstance(value, str) and value
    ]
    if not matches:
        raise RefreshError("new-file-license-missing", "A new public file has no matching license scope in policy.")
    return max(matches)[2]


def _previous_record(old: dict[str, object], rel: str) -> dict[str, object]:
    existing = old.get("previous")
    if isinstance(existing, dict):
        return copy.deepcopy(existing)
    return {
        "revision": BASELINE_REVISION,
        "path": rel,
        "sha256": old.get("sha256"),
        "source": copy.deepcopy(old.get("source")),
        "transform": copy.deepcopy(old.get("transform")),
    }


def _make_checkpoint_entry(
    rel: str,
    path: Path | None,
    old: dict[str, object] | None,
    policy: dict[str, object],
    self_entry: bool = False,
) -> dict[str, object]:
    if self_entry:
        entry = copy.deepcopy(old) if old else {
            "path": CHECKPOINT_MANIFEST,
            "license": "Apache-2.0",
            "source": {"kind": "generated", "path": "tools/documentation.py"},
            "transform": "generated file inventory; manifest self-hash intentionally omitted",
        }
        entry.update({"path": CHECKPOINT_MANIFEST, "bytes": None, "sha256": None, "self_hashed": False})
        return entry
    assert path is not None
    size, digest = _identity(path)
    if old is not None:
        if old.get("bytes") == size and old.get("sha256") == digest:
            return copy.deepcopy(old)
        entry = copy.deepcopy(old)
        entry["previous"] = _previous_record(old, rel)
        entry["bytes"] = size
        entry["sha256"] = digest
        entry["transform"] = FIRST_PARTY_TRANSFORM
        return entry
    return {
        "path": rel,
        "bytes": size,
        "sha256": digest,
        "license": _license_for_new(rel, policy),
        "source": {"kind": FIRST_PARTY_TRANSFORM, "path": rel, "revision": policy["release"]["documentation_revision"]},
        "transform": FIRST_PARTY_TRANSFORM,
    }


def _make_release_entry(
    rel: str,
    path: Path | None,
    old: dict[str, object] | None,
    policy: dict[str, object],
    self_entry: bool = False,
) -> dict[str, object]:
    if self_entry:
        entry = copy.deepcopy(old) if old else {
            "path": "RELEASE-MANIFEST.json",
            "source": "generated:tools/documentation.py",
            "transform": "generated-manifest; self hash intentionally omitted",
            "license": "Apache-2.0",
            "role": "release-manifest",
            "transform_details": {"self_hashed": False},
        }
        entry.update({"path": "RELEASE-MANIFEST.json", "bytes": None, "sha256": None, "self_hashed": False})
        entry.setdefault("source_sha256", None)
        entry.setdefault("transform_details", {"self_hashed": False})
        return entry
    assert path is not None
    size, digest = _identity(path)
    if old is not None:
        if old.get("bytes") == size and old.get("sha256") == digest:
            return copy.deepcopy(old)
        entry = copy.deepcopy(old)
        previous = _previous_record(old, rel)
        previous["source_sha256"] = old.get("source_sha256")
        previous["transform_details"] = copy.deepcopy(old.get("transform_details"))
        entry["previous"] = previous
        entry["bytes"] = size
        entry["sha256"] = digest
        entry["transform"] = FIRST_PARTY_TRANSFORM
        entry["transform_details"] = {
            "revision": policy["release"]["documentation_revision"],
            "baseline_revision": BASELINE_REVISION,
            "previous_transform": old.get("transform"),
            "previous_details": copy.deepcopy(old.get("transform_details")),
        }
        return entry
    return {
        "path": rel,
        "bytes": size,
        "sha256": digest,
        "license": _license_for_new("core/" + rel, policy),
        "role": "documentation" if rel.casefold().endswith(".md") else "documentation-tooling",
        "source": f"{FIRST_PARTY_TRANSFORM}:{rel}",
        "source_sha256": None,
        "transform": FIRST_PARTY_TRANSFORM,
        "transform_details": {
            "revision": policy["release"]["documentation_revision"],
            "baseline_revision": BASELINE_REVISION,
        },
    }


def _refresh_changes(
    files: dict[str, Path],
    old_entries: dict[str, dict[str, object]],
    existing_record: dict[str, object] | None,
) -> list[dict[str, object]]:
    records: dict[str, dict[str, object]] = {}
    if existing_record and isinstance(existing_record.get("changes"), list):
        for item in existing_record["changes"]:
            if isinstance(item, dict) and isinstance(item.get("path"), str):
                records[item["path"]] = copy.deepcopy(item)
    for rel, path in files.items():
        if rel in {REFRESH_RECORD_PATH, CHECKPOINT_MANIFEST}:
            continue
        size, digest = _identity(path)
        old = old_entries.get(rel)
        if old is None:
            record = records.get(rel, {"path": rel, "change": "added", "baseline_revision": BASELINE_REVISION})
            record["current_bytes"] = size
            record["current_sha256"] = digest
            records[rel] = record
            continue
        changed = old.get("bytes") != size or old.get("sha256") != digest
        if changed or isinstance(old.get("previous"), dict):
            record = records.get(rel, {
                "path": rel,
                "change": "modified",
                "baseline_revision": BASELINE_REVISION,
                "baseline_locator": f"{BASELINE_REVISION}:{rel}",
                "previous_sha256": old.get("sha256"),
            })
            record["current_bytes"] = size
            record["current_sha256"] = digest
            records[rel] = record
    for rel, old in old_entries.items():
        if rel in files or rel == CHECKPOINT_MANIFEST:
            continue
        if rel not in records:
            records[rel] = {
                "path": rel,
                "change": "removed",
                "baseline_revision": BASELINE_REVISION,
                "baseline_locator": f"{BASELINE_REVISION}:{rel}",
                "previous_sha256": old.get("sha256"),
            }
        else:
            records[rel]["change"] = "removed"
    return [records[key] for key in sorted(records)]


def _refresh_case_readmes(
    root: Path,
    files: dict[str, Path],
    documentation_revision: str,
) -> list[str]:
    """Update only stale README published identities in the two case manifests."""
    updated: list[str] = []
    for case_id, manifest_rel in CASE_MANIFESTS.items():
        manifest_path = root / manifest_rel
        try:
            manifest = _read_json(manifest_path)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RefreshError(
                "case-manifest-invalid",
                "A study case manifest is missing or invalid JSON.",
                file=manifest_rel,
                line=int(getattr(exc, "lineno", 1) or 1),
            ) from exc
        if not isinstance(manifest, dict) or manifest.get("schema") != "public-checkpoint.study-case.v1" or manifest.get("case_id") != case_id:
            raise RefreshError("case-manifest-contract", "A study case manifest does not match the supported case contract.", file=manifest_rel)
        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, list):
            raise RefreshError("case-artifacts-invalid", "A study case manifest does not contain an artifact list.", file=manifest_rel)
        matches = [item for item in artifacts if isinstance(item, dict) and item.get("path") == "README.md"]
        if len(matches) != 1:
            raise RefreshError("case-readme-unlisted", "Each study case must identify README.md exactly once.", file=manifest_rel)
        artifact = matches[0]
        old_published = artifact.get("published")
        if not isinstance(old_published, dict) or old_published.get("path") != f"{case_id}/README.md":
            raise RefreshError("case-readme-publication-invalid", "A study README published identity has an unsupported path.", file=manifest_rel)
        readme_rel = f"examples/{case_id}/README.md"
        readme_path = files.get(readme_rel)
        if readme_path is None:
            raise RefreshError("case-readme-missing", "A study README is missing from the public inventory.", file=readme_rel)
        size, digest = _identity(readme_path)
        if old_published.get("bytes") == size and old_published.get("sha256") == digest:
            continue

        if "previous_published" not in artifact:
            previous = copy.deepcopy(old_published)
            previous["revision"] = BASELINE_REVISION
            artifact["previous_published"] = previous
        if "previous_transformation" not in artifact:
            artifact["previous_transformation"] = copy.deepcopy(artifact.get("transformation"))
        artifact["published"] = {
            "path": f"{case_id}/README.md",
            "bytes": size,
            "sha256": digest,
        }
        artifact["transformation"] = f"{FIRST_PARTY_TRANSFORM}:{documentation_revision}"
        _atomic_json(manifest_path, manifest)
        files[manifest_rel] = manifest_path
        updated.append(manifest_rel)
    return updated


def _json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise OSError("output path is a symlink")
    data = _json_text(value)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=path.parent, delete=False) as handle:
            temporary_name = handle.name
            handle.write(data)
        os.replace(temporary_name, path)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _resolve_system_links(path: Path) -> Path:
    """Resolve only root-controlled POSIX links such as /var -> /private/var.

    A link qualifies when root owns it and its parent, and the parent is not
    group- or world-writable. Other links stay in place for later checks.
    """
    if os.name != "posix":
        return path
    current = Path(path.anchor)
    parts = path.parts[1:]
    for index, part in enumerate(parts):
        candidate = current / part
        try:
            info = candidate.lstat()
            parent = current.lstat()
        except OSError:
            return candidate.joinpath(*parts[index + 1:])
        if (
            stat.S_ISLNK(info.st_mode)
            and info.st_uid == 0
            and parent.st_uid == 0
            and not parent.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        ):
            current = candidate.resolve(strict=True)
        else:
            current = candidate
    return current


def _assert_outside_root(root: Path, out: Path) -> Path:
    resolved_root = root.resolve(strict=True)
    resolved_out = out.expanduser().absolute()
    lexical_relative = None
    # Compare both spellings of each side, so a system link such as
    # /var -> /private/var on macOS does not hide .scratch in either direction.
    for candidate_out in (resolved_out, _resolve_system_links(resolved_out)):
        for candidate_root in (root.expanduser().absolute(), resolved_root):
            try:
                lexical_relative = candidate_out.relative_to(candidate_root)
                break
            except ValueError:
                continue
        if lexical_relative is not None:
            break
    if lexical_relative is not None:
        if lexical_relative.parts and lexical_relative.parts[0].casefold() == ".scratch":
            probe = resolved_root
            for part in lexical_relative.parts:
                probe = probe / part
                if probe.exists() and _is_reparse_or_link(probe):
                    raise RefreshError("report-scratch-link", "The ignored report directory must not cross a link.")
            return resolved_out
        raise RefreshError("report-inside-public-root", "Only ignored .scratch paths may hold checker output inside the public root.")
    try:
        resolved_out.resolve(strict=False).relative_to(resolved_root)
    except ValueError:
        return resolved_out
    raise RefreshError("report-inside-public-root", "The JSON report must be outside the inventoried public root.")


def refresh_root(root: Path) -> dict[str, object]:
    """Refresh case publication metadata and both legacy inventory manifests."""
    requested_root = root.expanduser().absolute()
    if _is_reparse_or_link(requested_root):
        raise RefreshError("public-root-invalid", "The public root must be a real directory.")
    checked = check_root(requested_root, allow_stale_readme_identity=True)
    if checked.get("status") != "pass":
        return {
            "schema": REPORT_SCHEMA,
            "status": "fail",
            "root_name": requested_root.name,
            "refresh": "skipped",
            "reason": "documentation-check-failed",
            "findings": checked.get("findings", []),
            "unassessed": checked.get("unassessed", []),
        }
    root = requested_root.resolve(strict=True)

    policy = _read_json(root / POLICY_PATH)
    assert isinstance(policy, dict)
    release = policy.get("release")
    if not isinstance(release, dict) or not isinstance(release.get("documentation_revision"), str) or not release.get("documentation_revision"):
        raise RefreshError("documentation-revision-missing", "The policy must name a documentation_revision.")
    frozen_tag = release.get("frozen_tag")
    if not isinstance(frozen_tag, str) or not frozen_tag:
        raise RefreshError("frozen-tag-missing", "The policy must name the frozen release tag.")

    git_context = _git_context(root, frozen_tag)
    files = _collect_git_files(root)
    checkpoint = _load_manifest(root, CHECKPOINT_MANIFEST, "bioinformatics-harness-public-checkpoint/v1")
    release_manifest = _load_manifest(root, RELEASE_MANIFEST, "bioinformatics-harness-release-manifest/v1")
    old_checkpoint_entries = _entry_map(checkpoint)
    old_release_entries = _entry_map(release_manifest)

    record_path = root / REFRESH_RECORD_PATH
    existing_record: dict[str, object] | None = None
    if record_path.is_file():
        try:
            value = _read_json(record_path)
            if isinstance(value, dict) and value.get("schema") == RECORD_SCHEMA:
                existing_record = value
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RefreshError("documentation-record-invalid", "An existing documentation refresh record is invalid JSON.") from exc

    case_manifests_updated = _refresh_case_readmes(root, files, release["documentation_revision"])

    core_files = {
        rel[len("core/"):]: path
        for rel, path in files.items()
        if rel.startswith("core/") and rel != RELEASE_MANIFEST
    }
    new_release_entries = [
        _make_release_entry(rel, core_files[rel], old_release_entries.get(rel), policy)
        for rel in sorted(core_files)
    ]
    new_release_entries.append(
        _make_release_entry(
            "RELEASE-MANIFEST.json",
            None,
            old_release_entries.get("RELEASE-MANIFEST.json"),
            policy,
            self_entry=True,
        )
    )
    new_release = copy.deepcopy(release_manifest)
    new_release["files"] = sorted(new_release_entries, key=lambda item: str(item["path"]))
    new_release["documentation_revision"] = release["documentation_revision"]
    new_release["working_tree"] = {
        "kind": FIRST_PARTY_TRANSFORM,
        "baseline_revision": BASELINE_REVISION,
        "baseline_commit": git_context["baseline"],
        "head": git_context["head"],
        "frozen_tag": frozen_tag,
        "frozen_tag_target": git_context["tag_target"],
    }
    _atomic_json(root / RELEASE_MANIFEST, new_release)
    files[RELEASE_MANIFEST] = root / RELEASE_MANIFEST

    changes = _refresh_changes(files, old_checkpoint_entries, existing_record)
    record = {
        "schema": RECORD_SCHEMA,
        "baseline_revision": BASELINE_REVISION,
        "baseline_commit": git_context["baseline"],
        "frozen_tag": frozen_tag,
        "frozen_tag_target": git_context["tag_target"],
        "documentation_revision": release["documentation_revision"],
        "working_tree": {
            "kind": FIRST_PARTY_TRANSFORM,
            "head": git_context["head"],
            "tag_unchanged_by_refresh": True,
        },
        "record_path": REFRESH_RECORD_PATH,
        "record_identity_in_checkpoint_manifest": True,
        "self_identity_included_in_manifests": True,
        "changes": changes,
    }
    _atomic_json(record_path, record)
    files[REFRESH_RECORD_PATH] = record_path

    new_checkpoint_entries = [
        _make_checkpoint_entry(rel, files[rel], old_checkpoint_entries.get(rel), policy)
        for rel in sorted(files)
        if rel != CHECKPOINT_MANIFEST
    ]
    new_checkpoint_entries.append(
        _make_checkpoint_entry(
            CHECKPOINT_MANIFEST,
            None,
            old_checkpoint_entries.get(CHECKPOINT_MANIFEST),
            policy,
            self_entry=True,
        )
    )
    new_checkpoint = copy.deepcopy(checkpoint)
    new_checkpoint["files"] = sorted(new_checkpoint_entries, key=lambda item: str(item["path"]))
    _atomic_json(root / CHECKPOINT_MANIFEST, new_checkpoint)

    return {
        "schema": REPORT_SCHEMA,
        "status": "pass",
        "root_name": root.name,
        "refresh": "updated",
        "documentation_revision": release["documentation_revision"],
        "baseline_revision": BASELINE_REVISION,
        "baseline_commit": git_context["baseline"],
        "head": git_context["head"],
        "frozen_tag": frozen_tag,
        "frozen_tag_target": git_context["tag_target"],
        "checkpoint_entries": len(new_checkpoint_entries),
        "release_entries": len(new_release_entries),
        "record_path": REFRESH_RECORD_PATH,
        "change_count": len(changes),
        "case_manifests_updated": case_manifests_updated,
        "unassessed": checked.get("unassessed", []),
        "findings": [],
    }


def _write_report(out: Path, report: dict[str, object]) -> None:
    _atomic_json(out, report)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "refresh"):
        sub = subparsers.add_parser(name)
        sub.add_argument("--root", type=Path, default=DEFAULT_ROOT)
        sub.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    root = args.root.expanduser().absolute()
    out = args.out.expanduser().absolute()
    safe_out = False
    report_written = False
    try:
        out = _assert_outside_root(root, args.out)
        safe_out = True
        report = check_root(root) if args.command == "check" else refresh_root(root)
        _write_report(out, report)
        report_written = True
    except RefreshError as exc:
        report = {
            "schema": REPORT_SCHEMA,
            "status": "fail",
            "root_name": root.name,
            "refresh": "skipped" if args.command == "refresh" else "not_requested",
            "findings": [{"file": ".", "line": 1, "rule": exc.rule, "message": exc.message}],
        }
        if safe_out:
            try:
                _write_report(out, report)
                report_written = True
            except OSError:
                pass
    except (OSError, ValueError, TypeError, KeyError):
        report = {
            "schema": REPORT_SCHEMA,
            "status": "fail",
            "root_name": root.name,
            "refresh": "skipped" if args.command == "refresh" else "not_requested",
            "findings": [{"file": ".", "line": 1, "rule": "tool-operation-failed", "message": "The requested operation could not be completed safely."}],
        }
        if safe_out:
            try:
                _write_report(out, report)
                report_written = True
            except OSError:
                pass
    if not report_written:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 2
    summary = {
        "status": report.get("status"),
        "findings": len(report.get("findings", [])) if isinstance(report.get("findings"), list) else 0,
        "report": out.name,
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("status") == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
