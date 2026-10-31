#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Render one study directory as a read-only, self-contained HTML view.

The view follows the research-guidance-cycle stages and draws only the links
that records state explicitly: root-relative ``*_ref`` paths and declared IDs.
A null reference stays "not recorded" and an unresolved reference is listed as
a gap; nothing is inferred from prose.  Stage status and next action come from
the existing research-cycle checker.  The view does not assess science.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import sys
from typing import Any, Iterable
from urllib.parse import quote

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.research_support._common import (  # noqa: E402
    SupportError,
    _as_root,
    load_json,
    utc_now,
)
from scripts.research_support.research_cycle import (  # noqa: E402
    CYCLE_SCHEMA,
    DECISION_SCHEMA,
    DISCUSSION_SCHEMA,
    EVIDENCE_MAP_SCHEMA,
    SOURCE_LEDGER_SCHEMA,
    STAGES,
    check_research_cycle,
)


VIEW_SCHEMA = "research-support.study-view.v1"
REVISION_SCHEMA = "research-revision-v1"
TEMPLATE_PATH = Path(__file__).with_name("study_view_template.html")

# Transport bounds for one generated page; they do not limit a study.
MAX_FILES = 2000
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_EMBED_TEXT_BYTES = 256 * 1024
TEXT_SUFFIXES = {".md", ".txt", ".tsv", ".csv"}

# Relations as records state them (dependent -> basis) and their stored
# upstream -> downstream form.
UPSTREAM_FORM = {
    "cites": "supports",
    "motivated_by": "motivates",
    "candidate_method": "candidate_for",
    "addresses": "addressed_by",
    "uses_method": "method_of",
    "selects": "chosen_by",
    "uses": "used_by",
    "previous_version": "next_version",
    "considers": "considered_by",
    "raised_by": "raises",
    "serves": "served_by",
    "answers": "answered_by",
    "supported_by": "supports_claim",
    "checks": "checked_by",
    "inspects": "inspected_by",
    "route_selected": "handled_by_selected_route",
    "route_not_selected": "handled_by_other_route",
}

# Cycle reference fields grouped by the stage that records them.
STAGE_REFS: dict[str, tuple[tuple[str, ...], ...]] = {
    "synthesis": (
        ("synthesis", "source_ledger_ref"),
        ("synthesis", "evidence_map_ref"),
        ("synthesis", "report_ref"),
        ("synthesis", "time_scope", "refresh_record_ref"),
    ),
    "human_discussion": (("direction", "discussion_ref"),),
    "direction_choice": (),
    "data_and_plan": (
        ("study", "bridge_ref"),
        ("study", "task_context_ref"),
        ("study", "first_plan_ref"),
        ("study", "first_plan_seal_ref"),
    ),
    "execution_evaluation": (
        ("execution", "run_receipt_refs"),
        ("execution", "evaluation_ref"),
        ("execution", "evidence_refs"),
        ("execution", "delivery_ref"),
    ),
    "evidence_revision": (
        ("revision", "decision_ref"),
        ("revision", "next_cycle_ref"),
    ),
}


def _get(record: Any, path: Iterable[str]) -> Any:
    value = record
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _scan(root: Path, exclude: set[Path]) -> list[Path]:
    """List regular files under root without following links or dot directories."""

    found: list[Path] = []
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(
            name for name in dirnames
            if not name.startswith(".") and not os.path.islink(os.path.join(directory, name))
        )
        for name in sorted(filenames):
            path = Path(directory) / name
            if name.startswith(".") or path.is_symlink() or path.resolve() in exclude:
                continue
            found.append(path)
            if len(found) > MAX_FILES:
                raise SupportError(f"study root has more than {MAX_FILES} files; choose a narrower root", "fail")
    return found


class _Builder:
    def __init__(self, root: Path, files: list[Path]) -> None:
        self.root = root
        self.files: dict[str, dict[str, Any]] = {}
        self.records: dict[str, dict[str, Any]] = {}
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: list[dict[str, str]] = []
        self.gaps: list[dict[str, str]] = []
        self.cycles: list[dict[str, Any]] = []
        for path in files:
            self._read(path)

    # -- file layer -------------------------------------------------------

    def _read(self, path: Path) -> None:
        rel = _relative(self.root, path)
        size = path.stat().st_size
        entry: dict[str, Any] = {"path": rel, "bytes": size, "kind": "other", "schema": None, "content": None}
        suffix = path.suffix.lower()
        if suffix == ".json" and size <= MAX_JSON_BYTES:
            try:
                record = load_json(path, MAX_JSON_BYTES)
            except SupportError as exc:
                entry["kind"] = "unreadable"
                entry["error"] = str(exc)
            else:
                entry["kind"] = "json"
                entry["schema"] = _text(record.get("schema")) or _text(record.get("schema_version"))
                entry["content"] = json.dumps(record, ensure_ascii=False, indent=2)
                self.records[rel] = record
        elif suffix in TEXT_SUFFIXES and size <= MAX_EMBED_TEXT_BYTES:
            try:
                entry["content"] = path.read_text(encoding="utf-8")
                entry["kind"] = "text"
            except (OSError, UnicodeDecodeError) as exc:
                entry["kind"] = "unreadable"
                entry["error"] = type(exc).__name__
        self.files[rel] = entry

    def _resolve(self, value: Any, base: str | None = None) -> tuple[str, str | None]:
        """Return (state, root-relative path) for a recorded reference."""

        if value is None:
            return "null", None
        if not isinstance(value, str) or not value.strip():
            return "invalid", None
        posix = PurePosixPath(value.replace("\\", "/"))
        if posix.is_absolute() or ".." in posix.parts:
            return "outside_root", None
        rel = posix.as_posix().rstrip("/")
        if rel in self.files:
            return "present", rel
        if (self.root / rel).is_dir():
            return "directory", rel
        return "missing", rel

    # -- graph layer ------------------------------------------------------

    def node(self, node_id: str, kind: str, label: str, file: str, **extra: Any) -> str:
        if node_id not in self.nodes:
            self.nodes[node_id] = {"id": node_id, "kind": kind, "label": label, "file": file, **extra}
        elif self.nodes[node_id]["kind"] == "file" and kind != "file":
            # A record first seen through a plain reference gains its own kind.
            self.nodes[node_id]["kind"] = kind
        return node_id

    def edge(self, source: str, target: str, relation: str) -> None:
        # Records state some links from the dependent side; store every edge
        # upstream -> downstream so "from" is always where a record came from.
        if relation in UPSTREAM_FORM:
            source, target, relation = target, source, UPSTREAM_FORM[relation]
        self.edges.append({"from": source, "to": target, "relation": relation})

    def gap(self, kind: str, detail: str, file: str) -> None:
        self.gaps.append({"kind": kind, "detail": detail, "file": file})

    def file_node(self, rel: str, kind: str | None = None) -> str:
        entry = self.files.get(rel)
        schema = entry.get("schema") if entry else None
        return self.node(f"file:{rel}", kind or "file", rel, rel, schema=schema)

    def link_ref(self, owner: str, owner_file: str, value: Any, relation: str) -> str | None:
        state, rel = self._resolve(value)
        if state in {"present", "directory"} and rel is not None:
            target = self.file_node(rel, "directory" if state == "directory" else None)
            # A referenced file is an input of the record that names it.
            self.edge(target, owner, relation)
            return target
        if state in {"missing", "outside_root", "invalid"}:
            self.gap("reference", f"{relation}: {value!r} is {state}", owner_file)
        return None

    def link_id(self, owner: str, owner_file: str, target: str, relation: str, label: str) -> None:
        if target in self.nodes:
            self.edge(owner, target, relation)
        else:
            self.gap("identifier", f"{label} does not resolve in this record set", owner_file)

    # -- record adapters --------------------------------------------------

    def add_ledger(self, rel: str, record: dict[str, Any]) -> None:
        self.file_node(rel, "source_ledger")
        for source in record.get("sources") or []:
            sid = _text(source.get("source_id"))
            if not sid:
                continue
            bibliography = source.get("bibliography") or {}
            self.node(
                f"source:{rel}#{sid}", "source", _text(bibliography.get("title")) or sid, rel,
                record_id=sid,
                status=_get(source, ("reading", "status")),
                summary=_text(source.get("relation_to_work")),
                facts={
                    "selection": _get(source, ("selection", "status")),
                    "acquisition": _get(source, ("acquisition", "status")),
                    "identifier": bibliography.get("doi_or_identifier"),
                    "year": bibliography.get("year"),
                },
            )

    def add_evidence_map(self, rel: str, record: dict[str, Any], ledger: str | None) -> None:
        map_node = self.file_node(rel, "evidence_map")
        decision = record.get("review_decision") or {}
        self.nodes[map_node].update(status=decision.get("status"), summary=_text(decision.get("next_action")))
        for finding in record.get("findings") or []:
            fid = _text(finding.get("finding_id"))
            if not fid:
                continue
            self.node(
                f"finding:{rel}#{fid}", "finding", _text(finding.get("claim_or_observation")) or fid, rel,
                record_id=fid,
                status=finding.get("finding_type"),
                summary=_text(finding.get("cross_source_synthesis")),
                facts={"researcher_inference": finding.get("researcher_inference"), "scope_conditions": finding.get("scope_conditions")},
            )
        for method in record.get("method_candidates") or []:
            mid = _text(method.get("method_id"))
            if mid:
                self.node(
                    f"method:{rel}#{mid}", "method", _text(method.get("intended_use")) or mid, rel,
                    record_id=mid, status=method.get("selection"), summary=_text(method.get("selection_reason")),
                    facts={"change_condition": method.get("change_condition"), "failure_modes": method.get("failure_modes")},
                )
        for question in record.get("questions") or []:
            qid = _text(question.get("question_id"))
            if qid:
                self.node(
                    f"question:{rel}#{qid}", "question", _text(question.get("question")) or qid, rel,
                    record_id=qid, status=question.get("status"), summary=_text(question.get("evidence_gap")),
                    facts={
                        "significance": question.get("biological_or_practical_significance"),
                        "novelty_search": _get(question, ("novelty_search_scope", "status")),
                    },
                )
        # Links are drawn after all nodes of this map exist.
        for finding in record.get("findings") or []:
            fid = _text(finding.get("finding_id"))
            if not fid:
                continue
            for evidence in finding.get("evidence") or []:
                sid = _text(evidence.get("source_id"))
                if sid and ledger:
                    self.link_id(f"finding:{rel}#{fid}", rel, f"source:{ledger}#{sid}", "cites", f"finding {fid} source {sid}")
        for method in record.get("method_candidates") or []:
            mid = _text(method.get("method_id"))
            for fid in method.get("motivated_by_finding_ids") or []:
                self.link_id(f"method:{rel}#{mid}", rel, f"finding:{rel}#{fid}", "motivated_by", f"method {mid} finding {fid}")
        for question in record.get("questions") or []:
            qid = _text(question.get("question_id"))
            for fid in question.get("motivated_by_finding_ids") or []:
                self.link_id(f"question:{rel}#{qid}", rel, f"finding:{rel}#{fid}", "motivated_by", f"question {qid} finding {fid}")
            for mid in question.get("candidate_method_ids") or []:
                self.link_id(f"question:{rel}#{qid}", rel, f"method:{rel}#{mid}", "candidate_method", f"question {qid} method {mid}")

    def add_discussion(self, rel: str, record: dict[str, Any], evidence_map: str | None) -> dict[str, Any]:
        """Add directions and the recorded decision; return branch states."""

        discussion = self.file_node(rel, "discussion")
        self.nodes[discussion].update(status=record.get("status"), summary=_text(_get(record, ("recommendation", "reason"))))
        for field in ("brief_ref", "technical_route_ref"):
            self.link_ref(discussion, rel, record.get(field), field)
        self.link_ref(discussion, rel, _get(record, ("discussion", "human_input_ref")), "human_input_ref")
        decision = record.get("decision") or {}
        decided = decision.get("status") not in {None, "pending"}
        selected = decision.get("selected_direction_id") if decided else None
        recommended = _get(record, ("recommendation", "direction_id"))
        branches = {}
        for direction in record.get("directions") or []:
            did = _text(direction.get("direction_id"))
            if not did:
                continue
            intent = direction.get("intent", "study")
            if not decided:
                branch = "awaiting_choice"
            elif did == selected:
                branch = "selected"
            else:
                branch = "not_selected"
            branches[did] = branch
            node = self.node(
                f"direction:{rel}#{did}", "direction", did, rel,
                record_id=did, status=branch, intent=intent, recommended=did == recommended,
                summary=_text(direction.get("reason")),
                facts={
                    "cost_and_dependencies": direction.get("cost_and_dependencies"),
                    "alternative": direction.get("alternative"),
                    "change_condition": direction.get("change_condition"),
                },
            )
            self.edge(discussion, node, "offers")
            if evidence_map:
                for qid in direction.get("question_ids") or []:
                    self.link_id(node, rel, f"question:{evidence_map}#{qid}", "addresses", f"direction {did} question {qid}")
                for mid in direction.get("method_ids") or []:
                    self.link_id(node, rel, f"method:{evidence_map}#{mid}", "uses_method", f"direction {did} method {mid}")
        if decided:
            choice = self.node(
                f"decision:{rel}", "decision", f"{decision.get('status')}: {selected or '—'}", rel,
                status=decision.get("status"), summary=_text(decision.get("reason")),
                facts={"authority": decision.get("authority"), "made_at": decision.get("made_at"), "constraints": decision.get("constraints")},
            )
            self.edge(discussion, choice, "records")
            if selected:
                self.link_id(choice, rel, f"direction:{rel}#{selected}", "selects", f"decision direction {selected}")
            self.link_ref(choice, rel, decision.get("source_ref"), "source_ref")
        return {"decided": decided, "selected": selected, "recommended": recommended, "branches": branches}

    def add_cycle_decision(self, rel: str, record: dict[str, Any]) -> None:
        node = self.file_node(rel, "cycle_decision")
        self.nodes[node].update(
            status=record.get("action"), summary=_text(record.get("reason")),
            facts={"next_action": record.get("next_action")},
        )
        for index, ref in enumerate(record.get("evidence_refs") or []):
            self.link_ref(node, rel, ref, f"evidence_refs[{index}]")
        self.link_ref(node, rel, record.get("observed_findings_ref"), "observed_findings_ref")

    def add_revision(self, rel: str, record: dict[str, Any]) -> None:
        """Map research-revision-v1: request -> decision -> task -> artifact, claim, check."""

        record_node = self.file_node(rel, "revision_record")
        prefix = f"{rel}#"
        for item in record.get("sources") or []:
            if _text(item.get("id")):
                self.node(f"rsource:{prefix}{item['id']}", "revision_source", item["id"], rel,
                          record_id=item["id"], status=item.get("source_layer"), summary=_text(item.get("locator")))
        for item in record.get("requests") or []:
            if _text(item.get("id")):
                node = self.node(f"request:{prefix}{item['id']}", "request", _text(item.get("text")) or item["id"], rel,
                                 record_id=item["id"], status="required" if item.get("required") else "optional")
                self.edge(record_node, node, "contains")
        for item in record.get("artifacts") or []:
            if _text(item.get("id")):
                self.node(f"artifact:{prefix}{item['id']}", "artifact", item.get("path") or item["id"], rel,
                          record_id=item["id"], status=item.get("kind"))
        for item in record.get("tasks") or []:
            if _text(item.get("id")):
                self.node(f"task:{prefix}{item['id']}", "task", item["id"], rel, record_id=item["id"], status=item.get("status"))
        for item in record.get("requests") or []:
            for sid in item.get("source_ids") or []:
                self.link_id(f"request:{prefix}{item.get('id')}", rel, f"rsource:{prefix}{sid}", "raised_by", f"request {item.get('id')} source {sid}")
        for item in record.get("decisions") or []:
            rid = item.get("request_id")
            node = self.node(f"rdecision:{prefix}{rid}", "revision_decision", f"{item.get('route')}: {rid}", rel,
                             record_id=rid, status=item.get("route"), summary=_text(item.get("reason")),
                             facts={"unresolved": item.get("unresolved")})
            self.link_id(f"request:{prefix}{rid}", rel, node, "decided_as", f"decision request {rid}")
            for tid in item.get("task_ids") or []:
                self.link_id(node, rel, f"task:{prefix}{tid}", "assigns", f"decision task {tid}")
        for item in record.get("tasks") or []:
            for rid in item.get("request_ids") or []:
                self.link_id(f"task:{prefix}{item.get('id')}", rel, f"request:{prefix}{rid}", "serves", f"task request {rid}")
            for aid in item.get("artifact_ids") or []:
                self.link_id(f"task:{prefix}{item.get('id')}", rel, f"artifact:{prefix}{aid}", "produces", f"task artifact {aid}")
        for group in record.get("response_groups") or []:
            gid = _text(group.get("id"))
            if not gid:
                continue
            chosen = group.get("selected_alternative")
            alternatives = group.get("alternatives") or []
            node = self.node(f"route:{prefix}{gid}", "response_group", gid, rel, record_id=gid,
                             status="selected" if chosen else "pending", summary=_text(group.get("reason")),
                             facts={"selected_alternative": chosen,
                                    "alternatives": [alt.get("id") for alt in alternatives if isinstance(alt, dict)]})
            self.edge(record_node, node, "contains")
            for alt in alternatives:
                relation = "route_selected" if alt.get("id") == chosen else "route_not_selected"
                for rid in alt.get("request_ids") or []:
                    self.link_id(node, rel, f"request:{prefix}{rid}", relation, f"route {gid} request {rid}")
        for item in record.get("claims") or []:
            cid = _text(item.get("id"))
            if not cid:
                continue
            node = self.node(f"claim:{prefix}{cid}", "claim", _text(item.get("text")) or cid, rel,
                             record_id=cid, summary=_text(item.get("limits")) if isinstance(item.get("limits"), str) else None,
                             facts={"limits": item.get("limits")})
            for rid in item.get("request_ids") or []:
                self.link_id(node, rel, f"request:{prefix}{rid}", "answers", f"claim request {rid}")
            for aid in item.get("artifact_ids") or []:
                self.link_id(node, rel, f"artifact:{prefix}{aid}", "supported_by", f"claim artifact {aid}")
        for item in record.get("acceptance_checks") or []:
            kid = _text(item.get("id"))
            if not kid:
                continue
            node = self.node(f"check:{prefix}{kid}", "acceptance_check", kid, rel, record_id=kid,
                             status=item.get("mode"), summary=_text(item.get("scope")))
            for rid in item.get("request_ids") or []:
                self.link_id(node, rel, f"request:{prefix}{rid}", "checks", f"check request {rid}")
            for aid in item.get("artifact_ids") or []:
                self.link_id(node, rel, f"artifact:{prefix}{aid}", "inspects", f"check artifact {aid}")

    # -- cycles -----------------------------------------------------------

    def add_cycles(self) -> None:
        cycle_files = [rel for rel, record in self.records.items() if record.get("schema") == CYCLE_SCHEMA]
        by_path = {rel: self.records[rel] for rel in cycle_files}
        # Order each chain from its first version through previous_version links.
        previous: dict[str, str | None] = {}
        for rel, record in by_path.items():
            state, target = self._resolve(record.get("previous_version"))
            previous[rel] = target if state == "present" and target in by_path else None
        later = {value: key for key, value in previous.items() if value}
        ordered: list[str] = []
        for start in sorted(rel for rel in by_path if previous[rel] is None):
            cursor: str | None = start
            while cursor and cursor not in ordered:
                ordered.append(cursor)
                cursor = later.get(cursor)
        ordered.extend(rel for rel in sorted(by_path) if rel not in ordered)
        for rel in ordered:
            self.cycles.append(self._cycle(rel, by_path[rel], previous[rel]))

    def _cycle(self, rel: str, record: dict[str, Any], previous: str | None) -> dict[str, Any]:
        node = self.file_node(rel, "cycle")
        stage = record.get("stage")
        self.nodes[node].update(
            label=f"{record.get('cycle_id')} · {record.get('version')}",
            status=stage, summary=_text(_get(record, ("next_action", "action"))),
        )
        if previous:
            self.edge(node, f"file:{previous}", "previous_version")
        try:
            report = check_research_cycle(self.root, rel)
            check = {
                "overall": report["overall"],
                "status": report["status"],
                "next_action": report["next_action"],
                "findings": report["findings"],
            }
        except SupportError as exc:
            check = {"overall": "fail", "status": "unknown", "next_action": None, "findings": [], "error": str(exc)}
        for finding in check["findings"]:
            if finding.get("status") == "needs_work":
                self.gap("cycle_check", f"{finding.get('id')}: {finding.get('detail')}", rel)

        evidence_map = self._present(_get(record, ("synthesis", "evidence_map_ref")))
        ledger = self._present(_get(record, ("synthesis", "source_ledger_ref")))
        discussion_rel = self._present(_get(record, ("direction", "discussion_ref")))
        stages = []
        for stage_id in STAGES:
            refs = []
            for path in STAGE_REFS[stage_id]:
                value = _get(record, path)
                values = value if isinstance(value, list) else [value]
                if isinstance(value, list) and not value:
                    values = [None]
                for item in values:
                    state, target = self._resolve(item)
                    if state in {"present", "directory"} and target:
                        self.edge(node, self.file_node(target, "directory" if state == "directory" else None), "uses")
                    elif state in {"missing", "outside_root", "invalid"}:
                        self.gap("reference", f"{'.'.join(path)}: {item!r} is {state}", rel)
                    refs.append({"field": ".".join(path), "value": item, "state": state, "path": target})
            stages.append({"stage": stage_id, "refs": refs})

        branches: dict[str, Any] = {}
        if discussion_rel and self.records.get(discussion_rel, {}).get("schema") == DISCUSSION_SCHEMA:
            branches = self.add_discussion(discussion_rel, self.records[discussion_rel], evidence_map)
        recorded_selection = _get(record, ("direction", "selected_direction_id"))
        for qid in _get(record, ("synthesis", "question_ids")) or []:
            if evidence_map:
                self.link_id(node, rel, f"question:{evidence_map}#{qid}", "considers", f"cycle question {qid}")
        return {
            "path": rel,
            "node": node,
            "cycle_id": record.get("cycle_id"),
            "version": record.get("version"),
            "previous": previous,
            "stage": stage,
            "task": record.get("task"),
            "time_scope": _get(record, ("synthesis", "time_scope")),
            "next_action": record.get("next_action"),
            "requires_human_choice": _get(record, ("direction", "requires_human_choice")),
            "selected_direction_id": recorded_selection,
            "evidence_map": evidence_map,
            "source_ledger": ledger,
            "discussion": discussion_rel,
            "branches": branches,
            "stages": stages,
            "check": check,
        }

    def _present(self, value: Any) -> str | None:
        state, rel = self._resolve(value)
        return rel if state == "present" else None

    # -- assembly ---------------------------------------------------------

    def build(self) -> None:
        # Pair each evidence map with the ledger its cycle names, when known.
        ledger_for_map: dict[str, str] = {}
        for record in self.records.values():
            if record.get("schema") == CYCLE_SCHEMA:
                emap = self._present(_get(record, ("synthesis", "evidence_map_ref")))
                ledger = self._present(_get(record, ("synthesis", "source_ledger_ref")))
                if emap and ledger:
                    ledger_for_map[emap] = ledger
        for rel, record in self.records.items():
            if record.get("schema") == SOURCE_LEDGER_SCHEMA:
                self.add_ledger(rel, record)
        for rel, record in self.records.items():
            if record.get("schema") == EVIDENCE_MAP_SCHEMA:
                self.add_evidence_map(rel, record, ledger_for_map.get(rel))
        self.add_cycles()
        for rel, record in self.records.items():
            schema = record.get("schema") or record.get("schema_version")
            if schema == DECISION_SCHEMA:
                self.add_cycle_decision(rel, record)
            elif schema == REVISION_SCHEMA:
                self.add_revision(rel, record)
        seen = set()
        unique = []
        for edge in self.edges:
            key = (edge["from"], edge["to"], edge["relation"])
            if key not in seen and edge["to"] in self.nodes and edge["from"] in self.nodes:
                seen.add(key)
                unique.append(edge)
        self.edges = unique


def build_study_view(root: Path | str | os.PathLike[str], *, exclude: Iterable[Path] = ()) -> dict[str, Any]:
    """Return the view model for one study root without writing anything."""

    work_root = _as_root(root)
    files = _scan(work_root, {Path(item).resolve() for item in exclude})
    builder = _Builder(work_root, files)
    builder.build()
    return {
        "schema": VIEW_SCHEMA,
        "generated_utc": utc_now(),
        "root_name": work_root.name,
        "stages": list(STAGES),
        "cycles": builder.cycles,
        "nodes": builder.nodes,
        "edges": builder.edges,
        "files": builder.files,
        "gaps": builder.gaps,
        "scientific_acceptance": "not_assessed",
    }


def render_html(model: dict[str, Any], *, link_base: str = "") -> str:
    """Embed the model in the static template; link_base prefixes file links."""

    payload = dict(model, link_base=link_base)
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    # Keep the JSON inert inside <script>.
    data = data.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    return template.replace("__STUDY_VIEW_DATA__", data)


def _write_new(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="existing study work root")
    parser.add_argument("--out", required=True, help="new HTML file to write")
    parser.add_argument("--model-out", help="optional new JSON file for the view model")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        out = Path(os.path.normpath(Path(args.out).expanduser().absolute()))
        model_out = Path(os.path.normpath(Path(args.model_out).expanduser().absolute())) if args.model_out else None
        for target in (out, model_out):
            if target is not None and (target.exists() or target.is_symlink()):
                raise FileExistsError(f"output already exists: {target}")
        model = build_study_view(args.root, exclude=[path for path in (out, model_out) if path])
        root = _as_root(args.root)
        link_base = quote(Path(os.path.relpath(root, out.parent)).as_posix()) + "/"
        _write_new(out, render_html(model, link_base=link_base))
        if model_out is not None:
            _write_new(model_out, json.dumps(model, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        summary = {
            "status": "written",
            "out": str(out),
            "cycles": [{"path": c["path"], "stage": c["stage"], "status": c["check"]["status"]} for c in model["cycles"]],
            "nodes": len(model["nodes"]),
            "edges": len(model["edges"]),
            "gaps": len(model["gaps"]),
            "scientific_acceptance": "not_assessed",
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except (SupportError, FileExistsError, OSError, ValueError) as exc:
        print(json.dumps({"status": "fail", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


__all__ = ["VIEW_SCHEMA", "build_study_view", "main", "render_html"]


if __name__ == "__main__":
    raise SystemExit(main())
