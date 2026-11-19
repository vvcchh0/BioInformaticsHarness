#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Check the links across one research cycle without judging its science.

The checker is an optional native-Harness connector.  It reuses the source
ledger/evidence map, direction-discussion record, D53 study bridge, D51 task
context and delivery checks, standard first-plan seal, and bounded-run
receipts.  A passing report means that the declared links and identities are
present for the requested stage; it is never scientific acceptance.

Cycle schema ``research-support.research-cycle.v1`` fields:

* ``cycle_id``, ``version``, ``previous_version`` (a root-relative prior
  cycle path or null), ``stage``, and ``task`` (study_id, iteration_id,
  objective).
* ``synthesis``: root-relative source-ledger, evidence-map and report refs,
  question IDs, plus ``time_scope`` (mode, as_of, refresh_before_study,
  refresh_record_ref, study_target, and, for a same-historical-window target,
  historical_scope_reason).
* ``direction``: discussion_ref, requires_human_choice, and
  selected_direction_id.
* ``study``: bridge_ref, task_context_ref, first_plan_ref (the plan root
  directory), and first_plan_seal_ref (that root's ARCHIVED.json).
* ``execution``: run_receipt_refs, evaluation_ref, evidence_refs and
  delivery_ref. ``revision`` contains decision_ref and next_cycle_ref.
  ``next_action`` contains owner and action.

The discussion record uses
``research-support.direction-discussion.v1``.  It links a prepared or held
discussion brief, technical route, candidate directions, and an optional
recorded choice. A direction may have intent ``study`` (the default),
``review_delivery``, ``continue_reading``, ``retain``, ``defer``, or ``stop``.
Study intent requires non-empty question and method IDs with mapped
question-method links. Other intents may use empty ID arrays; any supplied IDs
still must resolve. Only study intent can advance to ``data_and_plan``. A
prepared brief can be ready for discussion; it is not represented as a
discussion already held or as authorization.

The minimal revision Decision record uses
``research-support.research-cycle-decision.v1`` with cycle_id, version,
action (keep, revise, narrow, defer, or stop), reason, evidence_refs or
observed_findings_ref, and next_action.  A revise action must reference a new
cycle version whose previous_version points to the current record.  Other
actions may end or defer the route without a next cycle.

CLI paths for --record and --out, and all record references, are relative to
--root.  Traversal and symlink resolution outside root are rejected. JSON
parsing and identity use one shared byte read. Output creation is exclusive.
This first adapter supports the native D51/D53/standard-plan-seal/bounded-run
formats; an unrecognized alternative platform format needs an explicit
adapter and is reported as needs_work, not as a scientific negative.
Report ``status`` is ``ready``, ``waiting_human``, or ``needs_work``; the CLI
returns 0, 1, or 2 respectively. A waiting-human report has ``overall=pass``
for the structural check but is not ready to advance. A run may be reused
within the same task-context binding across cycle versions; this checker does
not infer that a receipt represents a fresh invocation for each cycle version.
All reports use ``scientific_acceptance=not_assessed``.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.research_support._common import (  # noqa: E402
    SupportError,
    _as_root,
    _inside,
    _resolve_inside,
    dump_new,
    fingerprint,
    load_json_with_identity,
    utc_now,
)
from scripts.research_support.plan_seal import (  # noqa: E402
    STANDARD_PROTOCOL,
    validate_ready,
)
from scripts.research_support.task_context import (  # noqa: E402
    check_bound_run_receipt,
    check_task_context,
    check_task_delivery,
)


CYCLE_SCHEMA = "research-support.research-cycle.v1"
DISCUSSION_SCHEMA = "research-support.direction-discussion.v1"
DECISION_SCHEMA = "research-support.research-cycle-decision.v1"
CHECK_SCHEMA = "research-support.research-cycle-check.v1"
SOURCE_LEDGER_SCHEMA = "harness.research-preparation-source-ledger/v1"
EVIDENCE_MAP_SCHEMA = "harness.research-preparation-evidence-map/v1"
BRIDGE_SCHEMA = "harness.research-preparation-study-bridge/v1"
TASK_CONTEXT_SCHEMA = "research-support.task-context.v1"
TASK_DELIVERY_SCHEMA = "research-support.task-delivery.v1"
TASK_DELIVERY_CHECK_SCHEMA = "research-support.task-delivery-check.v1"
BOUNDED_RUN_SCHEMA = "bounded-run-receipt.v1"
STANDARD_READY_SCHEMA = "research-support.first-plan-ready.v1"
STANDARD_ARCHIVED_SCHEMA = "research-support.first-plan-archived.v1"
STAGES = (
    "synthesis",
    "human_discussion",
    "direction_choice",
    "data_and_plan",
    "execution_evaluation",
    "evidence_revision",
)
_STAGE_RANK = {stage: index for index, stage in enumerate(STAGES)}
_ACTION_VALUES = {"keep", "revise", "narrow", "defer", "stop"}
_DIRECTION_INTENTS = {"study", "review_delivery", "continue_reading", "retain", "defer", "stop"}


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SupportError(f"{label} must be non-empty text", "fail")
    return value.strip()


def _one_of(value: Any, choices: set[str] | tuple[str, ...]) -> bool:
    """Return false for malformed non-string values instead of raising."""

    return isinstance(value, str) and value in choices


def _unique_text_ids(value: Any) -> bool:
    return (
        isinstance(value, list)
        and all(isinstance(item, str) and bool(item.strip()) for item in value)
        and len(value) == len(set(value))
    )


def _relative_path(value: Any, label: str) -> str:
    text = _text(value, label)
    raw = Path(text)
    if raw.is_absolute() or raw.drive or "\x00" in text:
        raise SupportError(f"{label} must be root-relative", "fail")
    return text


def _resolve_ref(root: Path, value: Any, label: str, *, directory: bool = False) -> Path:
    relative = _relative_path(value, label)
    try:
        resolved = _resolve_inside(root, relative, allow_directory=directory)
    except SupportError as exc:
        raise SupportError(f"invalid {label}: {exc}", exc.status) from exc
    if not _inside(root, resolved):
        raise SupportError(f"{label} escapes the supplied root", "fail")
    return resolved


def _same_path(left: Path | str, right: Path | str) -> bool:
    try:
        left_resolved = Path(left).expanduser().resolve(strict=False)
        right_resolved = Path(right).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return False
    return os.path.normcase(str(left_resolved)) == os.path.normcase(str(right_resolved))


def _finding(
    findings: list[dict[str, str]], check_id: str, passed: bool, detail: str
) -> None:
    findings.append(
        {"id": check_id, "status": "pass" if passed else "needs_work", "detail": detail}
    )


def _reference(
    root: Path,
    value: Any,
    label: str,
    findings: list[dict[str, str]],
    identities: dict[str, Any],
    *,
    json_record: bool = False,
    optional: bool = False,
    directory: bool = False,
) -> tuple[Path | None, dict[str, Any] | None, dict[str, Any] | None]:
    if value is None and optional:
        return None, None, None
    try:
        path = _resolve_ref(root, value, label, directory=directory)
        if not path.exists():
            raise SupportError(f"{label} is missing: {value}", "unknown")
        if directory:
            if not path.is_dir():
                raise SupportError(f"{label} must be a directory: {value}", "fail")
            identity: dict[str, Any] = {"path": path.relative_to(root).as_posix(), "is_dir": True}
            identities[label] = identity
            _finding(findings, label, True, "root-contained directory exists")
            return path, None, identity
        if not path.is_file():
            raise SupportError(f"{label} is not a regular file: {value}", "fail")
        if json_record:
            payload, byte_identity = load_json_with_identity(path)
        else:
            byte_identity = fingerprint(path)
            payload = None
        identity = {"path": path.relative_to(root).as_posix(), **byte_identity}
        identities[label] = identity
        _finding(findings, label, True, "root-contained file is readable")
        return path, payload, identity
    except (SupportError, OSError, RuntimeError, ValueError) as exc:
        _finding(findings, label, False, str(exc))
        return None, None, None


def _json_list(value: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise SupportError(f"{label} must be an array of objects", "fail")
    return value


def _unique_id_map(
    records: list[dict[str, Any]], key: str, label: str, findings: list[dict[str, str]]
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    duplicate = False
    malformed = False
    for index, item in enumerate(records):
        identity = item.get(key)
        if not isinstance(identity, str) or not identity.strip():
            malformed = True
            continue
        if identity in result:
            duplicate = True
        result[identity] = item
    _finding(
        findings,
        f"{label}.ids",
        not malformed and not duplicate,
        "IDs are non-empty and unique" if not malformed and not duplicate else "IDs are missing, malformed, or duplicated",
    )
    return result


def _read_synthesis(
    root: Path,
    cycle: dict[str, Any],
    findings: list[dict[str, str]],
    identities: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    synthesis = cycle.get("synthesis")
    if not isinstance(synthesis, dict):
        _finding(findings, "synthesis.shape", False, "synthesis must be an object")
        return None, None
    _ledger_path, ledger, _ledger_ident = _reference(
        root, synthesis.get("source_ledger_ref"), "synthesis.source_ledger_ref", findings, identities, json_record=True
    )
    _map_path, evidence_map, _map_ident = _reference(
        root, synthesis.get("evidence_map_ref"), "synthesis.evidence_map_ref", findings, identities, json_record=True
    )
    _report_path, _report, _report_ident = _reference(
        root, synthesis.get("report_ref"), "synthesis.report_ref", findings, identities
    )

    if ledger is not None:
        schema_ok = ledger.get("schema") == SOURCE_LEDGER_SCHEMA
        _finding(findings, "source_ledger.schema", schema_ok, "recognized preparation source-ledger schema" if schema_ok else "adapter_required: source ledger schema is not the native D53 format")
        try:
            sources = _json_list(ledger.get("sources"), "source_ledger.sources")
            source_map = _unique_id_map(sources, "source_id", "source_ledger.sources", findings)
        except SupportError as exc:
            _finding(findings, "source_ledger.sources", False, str(exc))
            source_map = {}
        included = (ledger.get("coverage_snapshot") or {}).get("included_source_ids") if isinstance(ledger.get("coverage_snapshot"), dict) else None
        if isinstance(included, list):
            included_ok = all(isinstance(item, str) and item in source_map for item in included)
            _finding(findings, "source_ledger.coverage_ids", included_ok, "coverage source IDs resolve" if included_ok else "coverage snapshot contains an unknown source ID")
        else:
            _finding(findings, "source_ledger.coverage_ids", False, "coverage_snapshot.included_source_ids must be a list")

    question_map: dict[str, dict[str, Any]] = {}
    method_map: dict[str, dict[str, Any]] = {}
    finding_map: dict[str, dict[str, Any]] = {}
    if evidence_map is not None:
        schema_ok = evidence_map.get("schema") == EVIDENCE_MAP_SCHEMA
        _finding(findings, "evidence_map.schema", schema_ok, "recognized preparation evidence-map schema" if schema_ok else "adapter_required: evidence map schema is not the native D53 format")
        prep_matches = (
            isinstance(ledger, dict)
            and isinstance(ledger.get("preparation_id"), str)
            and ledger.get("preparation_id") == evidence_map.get("preparation_id")
        )
        _finding(findings, "preparation_identity", prep_matches, "source ledger and evidence map share a preparation_id" if prep_matches else "source ledger and evidence map preparation_id differ or are unavailable")
        try:
            finding_map = _unique_id_map(_json_list(evidence_map.get("findings"), "evidence_map.findings"), "finding_id", "evidence_map.findings", findings)
            method_map = _unique_id_map(_json_list(evidence_map.get("method_candidates"), "evidence_map.method_candidates"), "method_id", "evidence_map.method_candidates", findings)
            question_map = _unique_id_map(_json_list(evidence_map.get("questions"), "evidence_map.questions"), "question_id", "evidence_map.questions", findings)
        except SupportError as exc:
            _finding(findings, "evidence_map.collections", False, str(exc))
        for method_id, method in method_map.items():
            motivated = method.get("motivated_by_finding_ids")
            valid = isinstance(motivated, list) and all(isinstance(item, str) and item in finding_map for item in motivated)
            _finding(findings, f"method.{method_id}.findings", valid, "method finding references resolve" if valid else "method refers to a missing finding")
        for question_id, question in question_map.items():
            motivated = question.get("motivated_by_finding_ids")
            methods = question.get("candidate_method_ids")
            valid = (
                isinstance(motivated, list)
                and all(isinstance(item, str) and item in finding_map for item in motivated)
                and isinstance(methods, list)
                and all(isinstance(item, str) and item in method_map for item in methods)
            )
            _finding(findings, f"question.{question_id}.links", valid, "question finding and method references resolve" if valid else "question refers to a missing finding or method")
        if isinstance(ledger, dict):
            sources = ledger.get("sources") if isinstance(ledger.get("sources"), list) else []
            source_ids = {item.get("source_id") for item in sources if isinstance(item, dict) and isinstance(item.get("source_id"), str)}
            valid_evidence = True
            for item in finding_map.values():
                evidence = item.get("evidence")
                if not isinstance(evidence, list):
                    valid_evidence = False
                    continue
                for evidence_item in evidence:
                    if not isinstance(evidence_item, dict) or not isinstance(evidence_item.get("source_id"), str) or evidence_item.get("source_id") not in source_ids:
                        valid_evidence = False
            _finding(findings, "evidence_map.source_links", valid_evidence, "finding evidence source IDs resolve to the ledger" if valid_evidence else "finding evidence refers to absent sources")

    question_ids = synthesis.get("question_ids")
    if not isinstance(question_ids, list) or any(not isinstance(item, str) or not item.strip() for item in question_ids):
        _finding(findings, "synthesis.question_ids", False, "question_ids must be an array of non-empty IDs")
    elif len(question_ids) != len(set(question_ids)):
        _finding(findings, "synthesis.question_ids", False, "question_ids must be unique")
    elif evidence_map is None or not set(question_ids).issubset(question_map):
        _finding(findings, "synthesis.question_ids", False, "one or more question IDs are absent from the evidence map")
    else:
        _finding(findings, "synthesis.question_ids", True, "question IDs resolve to the current evidence-map bytes")

    _check_time_scope(root, synthesis.get("time_scope"), _STAGE_RANK.get(cycle.get("stage"), 0), findings, identities)
    return evidence_map, question_map if evidence_map is not None else None


def _check_time_scope(
    root: Path,
    value: Any,
    stage_rank: int,
    findings: list[dict[str, str]],
    identities: dict[str, Any],
) -> None:
    if not isinstance(value, dict):
        _finding(findings, "time_scope.shape", False, "time_scope must be an object")
        return
    mode = value.get("mode")
    target = value.get("study_target")
    as_of = value.get("as_of")
    refresh_flag = value.get("refresh_before_study")
    _finding(findings, "time_scope.mode", _one_of(mode, {"historical_window", "current"}), "time-scope mode is recognized" if _one_of(mode, {"historical_window", "current"}) else "mode must be historical_window or current")
    _finding(findings, "time_scope.as_of", isinstance(as_of, str) and bool(as_of.strip()), "an as_of date/cutoff is recorded" if isinstance(as_of, str) and as_of.strip() else "as_of must record the evidence cutoff")
    _finding(findings, "time_scope.study_target", _one_of(target, {"current", "same_historical_window"}), "study target is explicit" if _one_of(target, {"current", "same_historical_window"}) else "study_target must be current or same_historical_window")
    _finding(findings, "time_scope.refresh_flag", type(refresh_flag) is bool, "refresh_before_study is an explicit boolean" if type(refresh_flag) is bool else "refresh_before_study must be a boolean")
    if target == "same_historical_window":
        valid = mode == "historical_window" and isinstance(value.get("historical_scope_reason"), str) and bool(value["historical_scope_reason"].strip())
        _finding(findings, "time_scope.historical_rebuild", valid, "same historical window is explicitly justified" if valid else "same_historical_window requires a historical window and a non-empty historical_scope_reason")
    refresh_ref = value.get("refresh_record_ref")
    at_study_gate = stage_rank >= _STAGE_RANK["data_and_plan"]
    current_refresh_required = (
        at_study_gate and mode == "historical_window" and target == "current"
    )
    needs_refresh = at_study_gate and (
        current_refresh_required or refresh_flag is True
    )
    if current_refresh_required and refresh_flag is not True:
        _finding(findings, "time_scope.refresh_before_study", False, "a current-target study from a historical window must set refresh_before_study=true")
    if needs_refresh:
        path, _payload, _identity = _reference(root, refresh_ref, "time_scope.refresh_record_ref", findings, identities, json_record=bool(isinstance(refresh_ref, str) and refresh_ref.lower().endswith(".json")))
        if path is not None and target == "same_historical_window" and not value.get("historical_scope_reason"):
            _finding(findings, "time_scope.historical_refresh_reason", False, "a refresh claim requires a reason")
    elif at_study_gate and refresh_ref is not None:
        _finding(findings, "time_scope.refresh_record_ref", False, "a refresh record is supplied while refresh_before_study is false")
    elif not at_study_gate and refresh_ref is not None:
        # Future refresh evidence may be planned before the data-and-plan gate.
        # If it already exists, check it; its absence is not yet blocking.
        try:
            candidate = _resolve_ref(root, refresh_ref, "time_scope.refresh_record_ref")
        except SupportError as exc:
            _finding(findings, "time_scope.refresh_record_ref", False, str(exc))
        else:
            if candidate.exists():
                _reference(root, refresh_ref, "time_scope.refresh_record_ref", findings, identities, json_record=candidate.suffix.lower() == ".json")
            else:
                _finding(findings, "time_scope.refresh_record_ref", True, "future refresh record is not required before data_and_plan")


def _direction_records(
    root: Path,
    cycle: dict[str, Any],
    evidence_map: dict[str, Any] | None,
    question_map: dict[str, dict[str, Any]] | None,
    findings: list[dict[str, str]],
    identities: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, dict[str, Any]], dict[str, Any] | None]:
    direction = cycle.get("direction")
    if not isinstance(direction, dict):
        _finding(findings, "direction.shape", False, "direction must be an object")
        return None, {}, None
    requires_human = direction.get("requires_human_choice")
    _finding(findings, "direction.requires_human_choice", type(requires_human) is bool, "human-choice requirement is explicit" if type(requires_human) is bool else "requires_human_choice must be boolean")
    _discussion_path, discussion, _discussion_identity = _reference(
        root, direction.get("discussion_ref"), "direction.discussion_ref", findings, identities, json_record=True
    )
    if discussion is None:
        return None, {}, None
    schema_ok = discussion.get("schema") == DISCUSSION_SCHEMA
    _finding(findings, "discussion.schema", schema_ok, "recognized direction-discussion schema" if schema_ok else "adapter_required: discussion schema is not native")
    _finding(findings, "discussion.cycle_id", discussion.get("cycle_id") == cycle.get("cycle_id"), "discussion cycle_id matches" if discussion.get("cycle_id") == cycle.get("cycle_id") else "discussion belongs to another cycle")
    discussion_version_ok = isinstance(discussion.get("version"), str) and bool(discussion["version"].strip())
    _finding(findings, "discussion.version", discussion_version_ok, "discussion has its own non-empty version label" if discussion_version_ok else "discussion.version must be non-empty")
    discussion_status = discussion.get("status")
    _finding(findings, "discussion.status", _one_of(discussion_status, {"prepared", "discussed"}), "discussion is prepared or recorded as held" if _one_of(discussion_status, {"prepared", "discussed"}) else "discussion status must be prepared or discussed")
    brief_path, _brief_obj, _brief_ident = _reference(root, discussion.get("brief_ref"), "discussion.brief_ref", findings, identities)
    technical_path, _technical_obj, _technical_ident = _reference(root, discussion.get("technical_route_ref"), "discussion.technical_route_ref", findings, identities)

    try:
        directions = _json_list(discussion.get("directions"), "discussion.directions")
        directions_by_id = _unique_id_map(directions, "direction_id", "discussion.directions", findings)
    except SupportError as exc:
        directions_by_id = {}
        _finding(findings, "discussion.directions", False, str(exc))
    evidence_question_ids: set[str] = set(question_map or {})
    evidence_method_ids: set[str] = set()
    if isinstance(evidence_map, dict) and isinstance(evidence_map.get("method_candidates"), list):
        evidence_method_ids = {
            item.get("method_id") for item in evidence_map["method_candidates"] if isinstance(item, dict) and isinstance(item.get("method_id"), str)
        }
    for direction_id, candidate in directions_by_id.items():
        intent = candidate.get("intent", "study")
        intent_ok = _one_of(intent, _DIRECTION_INTENTS)
        _finding(findings, f"direction.{direction_id}.intent", intent_ok, "direction intent is recognized" if intent_ok else "intent must be study, review_delivery, continue_reading, retain, defer, or stop")
        qids = candidate.get("question_ids")
        mids = candidate.get("method_ids")
        text_fields = ("reason", "cost_and_dependencies", "alternative", "change_condition")
        fields_ok = all(isinstance(candidate.get(field), str) and candidate[field].strip() for field in text_fields)
        qids_ok = _unique_text_ids(qids) and all(item in evidence_question_ids for item in qids)
        mids_ok = _unique_text_ids(mids) and all(item in evidence_method_ids for item in mids)
        links_ok = qids_ok and mids_ok and (intent != "study" or (bool(qids) and bool(mids)))
        _finding(findings, f"direction.{direction_id}.shape", fields_ok, "direction has reason, cost/dependencies, alternative, and change condition" if fields_ok else "direction is missing a required rationale field")
        _finding(findings, f"direction.{direction_id}.evidence_links", links_ok, "supplied question/method IDs resolve; study intent has both" if links_ok else "question/method IDs are malformed, unmapped, or missing for a study intent")
        if links_ok and question_map is not None and qids and mids:
            candidate_methods = {}
            for qid in qids:
                values = question_map[qid].get("candidate_method_ids")
                candidate_methods[qid] = set(
                    item for item in values if isinstance(item, str)
                ) if isinstance(values, list) else set()
            aligned = (
                all(bool(set(mids).intersection(candidate_methods[qid])) for qid in qids)
                and all(any(method_id in candidate_methods[qid] for qid in qids) for method_id in mids)
            )
            _finding(findings, f"direction.{direction_id}.question_method_alignment", aligned, "every selected question and method has a declared link" if aligned else "each selected question must link at least one selected method and every selected method must serve a selected question")

    recommendation = discussion.get("recommendation")
    recommendation_ok = (
        isinstance(recommendation, dict)
        and isinstance(recommendation.get("direction_id"), str)
        and recommendation.get("direction_id") in directions_by_id
        and isinstance(recommendation.get("reason"), str)
        and bool(recommendation["reason"].strip())
    )
    _finding(findings, "discussion.recommendation", recommendation_ok, "recommendation names a declared direction with a reason" if recommendation_ok else "recommendation must name a declared direction and give a reason")

    discussion_notes = discussion.get("discussion")
    decision = discussion.get("decision")
    if not isinstance(discussion_notes, dict):
        discussion_notes = {}
    if not isinstance(decision, dict):
        decision = {}
    if discussion_status == "discussed":
        held = isinstance(discussion_notes.get("held_at"), str) and bool(discussion_notes["held_at"].strip())
        points = isinstance(discussion_notes.get("points_raised"), list)
        unresolved = isinstance(discussion_notes.get("unresolved"), list)
        _finding(findings, "discussion.held_record", held and points and unresolved, "held time, raised points, and unresolved items are recorded" if held and points and unresolved else "discussed status needs held_at, points_raised, and unresolved arrays")
        if discussion_notes.get("human_input_ref") is not None:
            _reference(root, discussion_notes.get("human_input_ref"), "discussion.human_input_ref", findings, identities)
        elif requires_human is True:
            _finding(findings, "discussion.human_input_ref", False, "a required human discussion needs a human_input_ref")
    decision_status = decision.get("status")
    if not _one_of(decision_status, {"pending", "recorded"}):
        _finding(findings, "discussion.decision_status", False, "decision.status must be pending or recorded")
    else:
        _finding(findings, "discussion.decision_status", True, "decision status is recognized")
    if decision_status == "recorded":
        authority = decision.get("authority")
        selected = decision.get("selected_direction_id")
        recorded_ok = (
            _one_of(authority, {"human", "existing_authorization"})
            and isinstance(selected, str)
            and selected in directions_by_id
            and isinstance(decision.get("made_at"), str) and bool(decision["made_at"].strip())
            and isinstance(decision.get("reason"), str) and bool(decision["reason"].strip())
            and isinstance(decision.get("constraints"), list)
        )
        _finding(findings, "discussion.recorded_decision", recorded_ok, "recorded choice has authority, direction, date, reason, and constraints" if recorded_ok else "recorded decision is incomplete or selects an unknown direction")
        if authority == "existing_authorization":
            _reference(root, decision.get("source_ref"), "discussion.decision.source_ref", findings, identities)
        elif authority == "human":
            if discussion_status != "discussed":
                _finding(findings, "discussion.human_authority", False, "human authority cannot be claimed for a discussion still marked prepared")
            elif discussion_notes.get("human_input_ref") is None:
                _finding(findings, "discussion.human_authority", False, "human choice must link its human input record")
    elif decision_status == "pending":
        if decision.get("selected_direction_id") is not None:
            _finding(findings, "discussion.pending_selection", False, "pending decision must not claim a selected direction")
        else:
            _finding(findings, "discussion.pending_selection", True, "pending decision has no selected direction")

    return discussion, directions_by_id, decision


def _check_choice(
    cycle: dict[str, Any],
    discussion: dict[str, Any] | None,
    directions_by_id: dict[str, dict[str, Any]],
    decision: dict[str, Any] | None,
    stage_rank: int,
    findings: list[dict[str, str]],
) -> tuple[bool, str | None]:
    direction = cycle.get("direction") if isinstance(cycle.get("direction"), dict) else {}
    requires_human = direction.get("requires_human_choice")
    decision = decision or {}
    if stage_rank < _STAGE_RANK["direction_choice"]:
        return False, None
    if decision.get("status") == "pending":
        if requires_human is True:
            return True, "a human must record the selected direction before this cycle can advance"
        _finding(findings, "direction.pending_without_human_gate", False, "no human choice is required, but no existing authorization has recorded a selection")
        return False, None
    if decision.get("status") != "recorded":
        _finding(findings, "direction.choice", False, "direction choice is not recorded")
        return False, None
    authority = decision.get("authority")
    if requires_human is True and authority != "human":
        _finding(findings, "direction.authority", False, "existing_authorization cannot bypass a required human choice")
    elif authority == "human" and (discussion or {}).get("status") != "discussed":
        _finding(findings, "direction.authority", False, "human choice requires a discussion recorded as discussed")
    else:
        _finding(findings, "direction.authority", True, "choice authority meets the declared human-choice condition")
    selected = direction.get("selected_direction_id")
    if not isinstance(selected, str) or selected != decision.get("selected_direction_id") or selected not in directions_by_id:
        _finding(findings, "direction.selected", False, "cycle selection does not match a recorded declared direction")
        return False, None
    _finding(findings, "direction.selected", True, "selected direction matches the recorded choice")
    if discussion is not None and selected in directions_by_id:
        return False, selected
    return False, None


def _choice_links(
    selected_id: str | None,
    directions_by_id: dict[str, dict[str, Any]],
    question_map: dict[str, dict[str, Any]] | None,
    evidence_map: dict[str, Any] | None,
    findings: list[dict[str, str]],
) -> tuple[set[str], set[str]]:
    if selected_id is None or selected_id not in directions_by_id:
        return set(), set()
    direction = directions_by_id[selected_id]
    question_values = direction.get("question_ids")
    method_values = direction.get("method_ids")
    if not _unique_text_ids(question_values) or not _unique_text_ids(method_values):
        _finding(findings, "direction.selected_links", False, "selected direction IDs must be string arrays")
        return set(), set()
    question_ids = set(question_values)
    method_ids = set(method_values)
    question_map = question_map or {}
    methods = {}
    if isinstance(evidence_map, dict) and isinstance(evidence_map.get("method_candidates"), list):
        methods = {item.get("method_id"): item for item in evidence_map["method_candidates"] if isinstance(item, dict)}
    mapped = question_ids.issubset(question_map) and method_ids.issubset(methods)
    _finding(findings, "direction.evidence_map_mapping", mapped, "selected question and method IDs map to current evidence-map records" if mapped else "selected question or method ID is not mapped")
    return question_ids, method_ids


def _check_study_and_plan(
    root: Path,
    cycle: dict[str, Any],
    evidence_map: dict[str, Any] | None,
    question_map: dict[str, dict[str, Any]] | None,
    directions_by_id: dict[str, dict[str, Any]],
    selected_id: str | None,
    findings: list[dict[str, str]],
    identities: dict[str, Any],
) -> tuple[Path | None, dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    study = cycle.get("study")
    if not isinstance(study, dict):
        _finding(findings, "study.shape", False, "study must be an object")
        return None, None, None, None
    bridge_path, bridge, _bridge_identity = _reference(root, study.get("bridge_ref"), "study.bridge_ref", findings, identities, json_record=True)
    context_path, context, context_identity = _reference(root, study.get("task_context_ref"), "study.task_context_ref", findings, identities, json_record=True)
    plan_root, _plan_obj, _plan_identity = _reference(root, study.get("first_plan_ref"), "study.first_plan_ref", findings, identities, directory=True)
    seal_path, seal, _seal_identity = _reference(root, study.get("first_plan_seal_ref"), "study.first_plan_seal_ref", findings, identities, json_record=True)
    task = cycle.get("task") if isinstance(cycle.get("task"), dict) else {}
    expected_study = task.get("study_id")
    expected_iteration = task.get("iteration_id")
    if context_path is not None and context is not None:
        try:
            context_check = check_task_context(root, context_path, expected_study, expected_iteration)
        except SupportError as exc:
            context_check = {"overall": "fail", "checks": [], "error": str(exc)}
        _finding(findings, "task_context.current", context_check.get("overall") == "pass", "D51 task_context checks root, independent IDs, entry and plan identities" if context_check.get("overall") == "pass" else "task_context is stale or mismatched: " + str(context_check.get("error", "one or more D51 checks failed")))
        if context_check.get("identity", {}).get("context", {}).get("sha256") != (context_identity or {}).get("sha256"):
            _finding(findings, "task_context.stable_read", False, "task context bytes changed between parsing and D51 validation")
        else:
            _finding(findings, "task_context.stable_read", True, "parsed task context and D51 validation use the same observed bytes")
        _finding(findings, "task_context.schema", context.get("schema") == TASK_CONTEXT_SCHEMA, "native D51 task context schema" if context.get("schema") == TASK_CONTEXT_SCHEMA else "adapter_required: task context is not native D51")
        _finding(findings, "task_context.task_ids", context.get("study_id") == expected_study and context.get("iteration_id") == expected_iteration and context.get("task_id") == task.get("task_id"), "task identity matches this cycle" if context.get("study_id") == expected_study and context.get("iteration_id") == expected_iteration and context.get("task_id") == task.get("task_id") else "task_context study/iteration/task IDs differ from the cycle")
        _finding(findings, "task_context.objective", context.get("objective") == task.get("objective"), "task_context objective matches the cycle" if context.get("objective") == task.get("objective") else "task_context objective differs from the cycle")

    if bridge is not None:
        _check_bridge(root, cycle, bridge, evidence_map, question_map, selected_id, directions_by_id, plan_root, findings)
    if plan_root is not None and seal_path is not None and seal is not None:
        _check_plan_seal(root, plan_root, seal_path, seal, context, findings, identities)
    else:
        _finding(findings, "first_plan.seal", False, "first plan root and standard ARCHIVED seal are both required")
    if context is not None and plan_root is not None:
        _check_context_plan_link(root, context, plan_root, seal, findings)
    return context_path, context, context_identity, seal


def _check_bridge(
    root: Path,
    cycle: dict[str, Any],
    bridge: dict[str, Any],
    evidence_map: dict[str, Any] | None,
    question_map: dict[str, dict[str, Any]] | None,
    selected_id: str | None,
    directions_by_id: dict[str, dict[str, Any]],
    plan_root: Path | None,
    findings: list[dict[str, str]],
) -> None:
    _finding(findings, "bridge.schema", bridge.get("schema") == BRIDGE_SCHEMA, "recognized D53 study-bridge schema" if bridge.get("schema") == BRIDGE_SCHEMA else "adapter_required: bridge schema is not native D53")
    task = cycle.get("task") if isinstance(cycle.get("task"), dict) else {}
    synthesis = cycle.get("synthesis") if isinstance(cycle.get("synthesis"), dict) else {}
    study = bridge.get("study") if isinstance(bridge.get("study"), dict) else {}
    _finding(findings, "bridge.study_identity", study.get("study_id") == task.get("study_id") and study.get("iteration_id") == task.get("iteration_id") and study.get("objective") == task.get("objective"), "bridge study/iteration/objective match the cycle" if study.get("study_id") == task.get("study_id") and study.get("iteration_id") == task.get("iteration_id") and study.get("objective") == task.get("objective") else "bridge study identity or objective differs from the cycle")
    transition = bridge.get("transition") if isinstance(bridge.get("transition"), dict) else {}
    transition_ok = transition.get("decision") == "transition_to_study"
    _finding(findings, "bridge.transition", transition_ok, "bridge records explicit transition_to_study" if transition_ok else "bridge does not record an explicit study transition")
    version_ok = isinstance(bridge.get("review_version"), str) and bool(bridge["review_version"].strip())
    _finding(findings, "bridge.version", version_ok, "bridge records its own non-empty review version" if version_ok else "bridge.review_version must be non-empty")
    for ref_field, cycle_field in (("source_ledger_ref", "source_ledger_ref"), ("evidence_map_ref", "evidence_map_ref")):
        bridge_ref = transition.get(ref_field)
        cycle_ref = synthesis.get(cycle_field)
        ref_ok = isinstance(bridge_ref, dict) and isinstance(bridge_ref.get("path"), str) and isinstance(cycle_ref, str)
        if ref_ok:
            try:
                ref_ok = _same_path(_resolve_ref(root, bridge_ref["path"], f"bridge.transition.{ref_field}"), _resolve_ref(root, cycle_ref, f"synthesis.{cycle_field}"))
            except SupportError:
                ref_ok = False
            ref_ok = ref_ok and isinstance(bridge_ref.get("version"), str) and bool(bridge_ref["version"].strip())
        _finding(findings, f"bridge.{ref_field}", ref_ok, "bridge reference resolves to the cycle's current source/evidence bytes" if ref_ok else "bridge reference path/version does not match the current cycle source/evidence reference")

    selected_questions, selected_methods = _choice_links(selected_id, directions_by_id, question_map, evidence_map, findings)
    bridge_questions = transition.get("selected_question_ids")
    q_match = _unique_text_ids(bridge_questions) and set(bridge_questions) == selected_questions
    _finding(findings, "bridge.selected_questions", q_match, "bridge selected questions equal the recorded direction" if q_match else "bridge selected question IDs differ from the recorded direction")
    selected_findings = transition.get("selected_finding_ids")
    finding_ids = {
        item.get("finding_id") for item in (evidence_map or {}).get("findings", []) if isinstance(item, dict) and isinstance(item.get("finding_id"), str)
    }
    finding_match = _unique_text_ids(selected_findings) and bool(selected_findings) and set(selected_findings).issubset(finding_ids)
    if finding_match and question_map is not None:
        finding_match = True
        for qid in selected_questions:
            if qid not in question_map:
                finding_match = False
                break
            motivated = question_map[qid].get("motivated_by_finding_ids")
            if not isinstance(motivated, list) or not set(selected_findings).intersection(
                item for item in motivated if isinstance(item, str)
            ):
                finding_match = False
                break
    _finding(findings, "bridge.selected_findings", finding_match, "selected findings exist and motivate each selected question" if finding_match else "selected findings are absent, unknown, or do not motivate the selected questions")
    approach = bridge.get("selected_approach") if isinstance(bridge.get("selected_approach"), dict) else {}
    method_id = approach.get("method_id")
    selected_method_ok = isinstance(method_id, str) and method_id in selected_methods
    if selected_method_ok and isinstance(evidence_map, dict):
        method_map = {item.get("method_id"): item for item in evidence_map.get("method_candidates", []) if isinstance(item, dict) and isinstance(item.get("method_id"), str)}
        selected_method_ok = _one_of(method_map.get(method_id, {}).get("selection"), {"selected", "selected_within_scope"})
    _finding(findings, "bridge.selected_method", selected_method_ok, "selected method is the recorded direction method and is selected in the evidence map" if selected_method_ok else "bridge method differs from the chosen direction or is not selected in the evidence map")
    bridge_first_plan = bridge.get("first_plan") if isinstance(bridge.get("first_plan"), dict) else {}
    plan_path_matches = False
    if plan_root is not None and isinstance(bridge_first_plan.get("path"), str):
        try:
            plan_path_matches = _same_path(_resolve_ref(root, bridge_first_plan["path"], "bridge.first_plan.path", directory=True), plan_root)
        except SupportError:
            plan_path_matches = False
    plan_fields_match = (
        plan_path_matches
        and isinstance(bridge_first_plan.get("version"), str)
        and bool(bridge_first_plan["version"].strip())
        and bridge_first_plan.get("seal_status") == "archived"
    )
    _finding(findings, "bridge.first_plan", plan_fields_match, "bridge links the selected cycle plan and records it archived" if plan_fields_match else "bridge first_plan path/version/seal_status does not match the cycle")


def _check_plan_seal(
    root: Path,
    plan_root: Path,
    seal_path: Path,
    seal: dict[str, Any],
    context: dict[str, Any] | None,
    findings: list[dict[str, str]],
    identities: dict[str, Any],
) -> None:
    ack_path_ok = _same_path(seal_path, plan_root / "ARCHIVED.json")
    _finding(findings, "first_plan.seal_path", ack_path_ok, "ARCHIVED.json is in the declared first-plan root" if ack_path_ok else "standard seal must be the plan root's ARCHIVED.json")
    ready_path = plan_root / "READY.json"
    try:
        ready, ready_identity = load_json_with_identity(ready_path)
        ready_readable = True
    except SupportError as exc:
        ready, ready_identity, ready_readable = None, None, False
        _finding(findings, "first_plan.ready_readable", False, str(exc))
    if ready_readable and ready is not None:
        ready_ok = ready.get("schema") == STANDARD_READY_SCHEMA
        _finding(findings, "first_plan.ready_schema", ready_ok, "standard first-plan READY schema" if ready_ok else "adapter_required: READY is not standard Harness format")
        try:
            validate_ready(plan_root, ready, STANDARD_PROTOCOL)
            manifest_valid = True
        except SupportError as exc:
            manifest_valid = False
            _finding(findings, "first_plan.manifest", False, f"standard seal manifest no longer matches current plan bytes: {exc}")
        if manifest_valid:
            _finding(findings, "first_plan.manifest", True, "standard READY manifest matches the current plan-root files")
    archived_ok = seal.get("schema") == STANDARD_ARCHIVED_SCHEMA and seal.get("status") == "archived"
    _finding(findings, "first_plan.archived_schema", archived_ok, "standard first-plan archive acknowledgement is archived" if archived_ok else "plan is not sealed with the standard ARCHIVED schema")
    if ready_identity is not None:
        _finding(findings, "first_plan.ready_identity", seal.get("ready_identity") == ready_identity, "ARCHIVED binds the exact current READY bytes" if seal.get("ready_identity") == ready_identity else "ARCHIVED ready_identity differs from the current READY bytes")
        declared_files = ready.get("files") if isinstance(ready, dict) else None
        sealed_files = seal.get("sealed_files")
        _finding(findings, "first_plan.sealed_files", sealed_files == declared_files, "ARCHIVED file list matches the READY manifest" if sealed_files == declared_files else "ARCHIVED sealed_files differ from READY files")
    else:
        _finding(findings, "first_plan.sealed_files", False, "cannot compare seal to unreadable READY")
    if context is not None:
        spec = context.get("spec") if isinstance(context.get("spec"), dict) else {}
        plan_decl = (context.get("declared_identity") or {}).get("plan") if isinstance(context.get("declared_identity"), dict) else None
        plan_value = spec.get("plan")
        included = False
        if isinstance(plan_value, str) and isinstance(plan_decl, dict) and isinstance(seal.get("sealed_files"), list):
            try:
                context_plan_path = _resolve_ref(root, plan_value, "task_context.spec.plan")
                if _inside(plan_root, context_plan_path):
                    relative = context_plan_path.relative_to(plan_root).as_posix()
                    match = next((item for item in seal["sealed_files"] if isinstance(item, dict) and item.get("path") == relative), None)
                    included = isinstance(match, dict) and match.get("bytes") == plan_decl.get("bytes") and match.get("sha256") == plan_decl.get("sha256")
            except SupportError:
                included = False
        _finding(findings, "task_context.plan_in_seal", included, "D51 context's frozen plan JSON is included with the same identity in the first-plan seal" if included else "D51 context plan is missing, outside the sealed plan root, or has a different identity")


def _check_context_plan_link(
    root: Path,
    context: dict[str, Any],
    plan_root: Path,
    seal: dict[str, Any] | None,
    findings: list[dict[str, str]],
) -> None:
    spec = context.get("spec") if isinstance(context.get("spec"), dict) else {}
    plan_value = spec.get("plan")
    if not isinstance(plan_value, str):
        _finding(findings, "task_context.plan", False, "D51 task context must bind a JSON plan inside the sealed first-plan root")
        return
    try:
        path = _resolve_ref(root, plan_value, "task_context.spec.plan")
        inside = _inside(plan_root, path)
    except SupportError as exc:
        _finding(findings, "task_context.plan", False, str(exc))
        return
    _finding(findings, "task_context.plan", inside, "task context plan path is under the declared first-plan root" if inside else "task context plan path is outside the declared first-plan root")


def _check_execution(
    root: Path,
    cycle: dict[str, Any],
    context_path: Path | None,
    context: dict[str, Any] | None,
    context_identity: dict[str, Any] | None,
    findings: list[dict[str, str]],
    identities: dict[str, Any],
) -> None:
    execution = cycle.get("execution")
    if not isinstance(execution, dict):
        _finding(findings, "execution.shape", False, "execution must be an object")
        return
    receipts = execution.get("run_receipt_refs")
    if not isinstance(receipts, list) or not receipts:
        _finding(findings, "execution.run_receipt_refs", False, "execution stage requires at least one bounded-run receipt")
        receipts = []
    task = cycle.get("task") if isinstance(cycle.get("task"), dict) else {}
    context_binding = None
    if context is not None and context_path is not None and context_identity is not None:
        context_binding = {
            "study_id": context.get("study_id"),
            "iteration_id": context.get("iteration_id"),
            "task_id": context.get("task_id"),
            "context_path": str(context_path),
            "context_sha256": context_identity.get("sha256"),
        }
    for index, receipt_ref in enumerate(receipts):
        label = f"execution.run_receipt[{index}]"
        try:
            receipt_path = _resolve_ref(root, receipt_ref, label)
        except SupportError as exc:
            _finding(findings, label, False, str(exc))
            continue
        if context_binding is None:
            _finding(findings, label, False, "a current validated D51 task context is required to bind the run")
            continue
        checked = check_bound_run_receipt(receipt_path, context_binding=context_binding, expected_cwd=root)
        receipt = checked.get("receipt")
        identity = checked.get("identity")
        identities[label] = {"path": receipt_path.relative_to(root).as_posix(), **(identity or {})}
        checked_fields = {item.get("id"): item.get("status") == "pass" for item in checked.get("checks", []) if isinstance(item, dict)}
        operational_ok = (
            checked.get("overall") == "pass"
            and checked_fields.get("schema") is True
            and checked_fields.get("success") is True
            and checked_fields.get("root_pid") is True
            and checked_fields.get("cwd") is True
            and checked_fields.get("task_context") is True
            and isinstance(receipt, dict)
            and receipt.get("schema") == BOUNDED_RUN_SCHEMA
        )
        _finding(findings, label, operational_ok, "D51 confirms a successful bounded-run receipt bound to this task context, positive PID and matching cwd; freshness within the cycle version is not inferred" if operational_ok else "adapter_required or needs_work: run is not a successful real bounded run bound to this current task context")

    evaluation_path, _evaluation, _evaluation_identity = _reference(root, execution.get("evaluation_ref"), "execution.evaluation_ref", findings, identities, json_record=True)
    evidence_refs = execution.get("evidence_refs")
    if not isinstance(evidence_refs, list) or not evidence_refs:
        _finding(findings, "execution.evidence_refs", False, "execution stage requires at least one evidence reference")
        evidence_refs = []
    for index, evidence_ref in enumerate(evidence_refs):
        _reference(root, evidence_ref, f"execution.evidence_ref[{index}]", findings, identities)
    delivery_path, delivery, delivery_identity = _reference(root, execution.get("delivery_ref"), "execution.delivery_ref", findings, identities, json_record=True)
    if delivery_path is not None and delivery is not None and context_path is not None:
        try:
            fresh = check_task_delivery(root, context_path, task.get("study_id"), task.get("iteration_id"))
            fresh_ok = fresh.get("overall") == "pass" and fresh.get("delivery_ready") is True
            if delivery.get("schema") == TASK_DELIVERY_SCHEMA:
                linked = _same_path(delivery_path, fresh.get("delivery_path", ""))
                fresh_identity = fresh.get("identity", {}).get("delivery") if isinstance(fresh.get("identity"), dict) else None
                same_observed_bytes = (
                    isinstance(delivery_identity, dict)
                    and isinstance(fresh_identity, dict)
                    and delivery_identity.get("bytes") == fresh_identity.get("bytes")
                    and delivery_identity.get("sha256") == fresh_identity.get("sha256")
                )
                delivery_ok = fresh_ok and linked and same_observed_bytes
                detail = "current Task Context delivery checker confirms this bound delivery and the same parsed bytes" if delivery_ok else "raw delivery is not the current declared delivery, changed after parsing, or fails D51 artifact checks"
            elif delivery.get("schema") == TASK_DELIVERY_CHECK_SCHEMA:
                saved_identity = delivery.get("identity", {}).get("delivery") if isinstance(delivery.get("identity"), dict) else None
                fresh_identity = fresh.get("identity", {}).get("delivery") if isinstance(fresh.get("identity"), dict) else None
                delivery_ok = (
                    fresh_ok
                    and delivery.get("overall") == "pass"
                    and delivery.get("delivery_ready") is True
                    and delivery.get("study_id") == task.get("study_id")
                    and delivery.get("iteration_id") == task.get("iteration_id")
                    and delivery.get("task_id") == task.get("task_id")
                    and _same_path(delivery.get("delivery_path", ""), fresh.get("delivery_path", ""))
                    and saved_identity == fresh_identity
                    and delivery.get("identity", {}).get("context", {}).get("sha256") == context_identity.get("sha256")
                )
                detail = "saved D51 delivery check matches the current delivery/context identities" if delivery_ok else "saved delivery check is stale, not ready, or belongs to another task"
            else:
                delivery_ok = False
                detail = "adapter_required: delivery record is not a native D51 delivery or delivery-check record"
            _finding(findings, "execution.delivery", delivery_ok, detail)
        except SupportError as exc:
            _finding(findings, "execution.delivery", False, f"current D51 delivery check failed: {exc}")
    return None


def _check_revision(
    root: Path,
    record_path: Path,
    cycle: dict[str, Any],
    findings: list[dict[str, str]],
    identities: dict[str, Any],
) -> None:
    revision = cycle.get("revision")
    if not isinstance(revision, dict):
        _finding(findings, "revision.shape", False, "revision must be an object")
        return
    decision_path, decision, _decision_identity = _reference(root, revision.get("decision_ref"), "revision.decision_ref", findings, identities, json_record=True)
    action = None
    if decision is not None:
        schema_ok = decision.get("schema") == DECISION_SCHEMA
        _finding(findings, "revision.decision_schema", schema_ok, "recognized research-cycle Decision record" if schema_ok else "adapter_required: decision is not the native research-cycle serialization")
        identity_ok = decision.get("cycle_id") == cycle.get("cycle_id") and decision.get("version") == cycle.get("version")
        _finding(findings, "revision.decision_identity", identity_ok, "decision identifies this cycle version" if identity_ok else "decision belongs to another cycle/version")
        action = decision.get("action")
        action_ok = _one_of(action, _ACTION_VALUES)
        _finding(findings, "revision.action", action_ok, "decision action is recognized" if action_ok else "decision action must be keep, revise, narrow, defer, or stop")
        decision_text_ok = all(isinstance(decision.get(field), str) and bool(decision[field].strip()) for field in ("reason",))
        next_action = decision.get("next_action")
        decision_text_ok = decision_text_ok and isinstance(next_action, dict) and all(isinstance(next_action.get(key), str) and next_action[key].strip() for key in ("owner", "action"))
        _finding(findings, "revision.decision_reason_next_action", decision_text_ok, "decision records a reason and next owner/action" if decision_text_ok else "decision reason and next_action owner/action are required")
        evidence_refs = decision.get("evidence_refs")
        observed_ref = decision.get("observed_findings_ref")
        if isinstance(evidence_refs, list) and evidence_refs:
            evidence_ok = True
            for index, ref in enumerate(evidence_refs):
                path, _payload, _ident = _reference(root, ref, f"revision.decision.evidence_refs[{index}]", findings, identities)
                evidence_ok = evidence_ok and path is not None
        elif observed_ref is not None:
            path, _payload, _ident = _reference(root, observed_ref, "revision.decision.observed_findings_ref", findings, identities)
            evidence_ok = path is not None
        else:
            evidence_ok = False
        _finding(findings, "revision.decision_evidence", evidence_ok, "decision links recorded evidence/findings" if evidence_ok else "decision needs evidence_refs or observed_findings_ref")

    next_cycle_value = revision.get("next_cycle_ref")
    if action == "revise":
        next_path, next_cycle, next_identity = _reference(root, next_cycle_value, "revision.next_cycle_ref", findings, identities, json_record=True)
        if next_path is None or next_cycle is None:
            _finding(findings, "revision.next_cycle", False, "revise decision requires a new cycle record")
            return
        version_ok = next_cycle.get("schema") == CYCLE_SCHEMA and next_cycle.get("cycle_id") == cycle.get("cycle_id") and next_cycle.get("version") != cycle.get("version")
        _finding(findings, "revision.next_cycle_identity", version_ok, "next cycle keeps cycle_id and uses a different version" if version_ok else "next cycle must keep cycle_id and use a new version")
        prior_ok = False
        if next_cycle.get("previous_version") is not None:
            try:
                prior_ok = _same_path(_resolve_ref(root, next_cycle.get("previous_version"), "next_cycle.previous_version"), record_path)
            except SupportError:
                prior_ok = False
        _finding(findings, "revision.version_link", prior_ok, "new cycle previous_version points to the current record" if prior_ok else "new cycle must point previous_version to this cycle record")
        old_plan = cycle.get("study", {}).get("first_plan_ref") if isinstance(cycle.get("study"), dict) else None
        new_plan = next_cycle.get("study", {}).get("first_plan_ref") if isinstance(next_cycle.get("study"), dict) else None
        if old_plan is not None and new_plan is not None:
            try:
                no_overwrite = not _same_path(_resolve_ref(root, old_plan, "current.first_plan_ref", directory=True), _resolve_ref(root, new_plan, "next.first_plan_ref", directory=True))
            except SupportError:
                no_overwrite = True
            _finding(findings, "revision.plan_version", no_overwrite, "revised plan has a separate root" if no_overwrite else "revision points to the previous plan root; preserve the earlier plan and use a new version path")
    elif next_cycle_value is not None:
        next_path, next_cycle, _next_identity = _reference(root, next_cycle_value, "revision.next_cycle_ref", findings, identities, json_record=True)
        if next_path is not None and next_cycle is not None:
            related = next_cycle.get("schema") == CYCLE_SCHEMA and next_cycle.get("cycle_id") == cycle.get("cycle_id") and next_cycle.get("version") != cycle.get("version")
            _finding(findings, "revision.optional_next_cycle", related, "optional next cycle is a distinct version of this cycle" if related else "optional next cycle identity is invalid")


def check_research_cycle(
    root: Path | str | os.PathLike[str],
    record_path: Path | str | os.PathLike[str],
) -> dict[str, Any]:
    """Return a structural handoff report for a native research-cycle record.

    It reads each JSON reference and its identity from the same byte buffer,
    rejects references that escape the caller's root, and leaves scientific
    sufficiency to the domain owner and human reviewer.
    """

    work_root = _as_root(root)
    record_file = _resolve_ref(work_root, os.fspath(record_path), "record")
    record, record_identity = load_json_with_identity(record_file)
    if record.get("schema") != CYCLE_SCHEMA:
        raise SupportError(f"record schema must be {CYCLE_SCHEMA}", "fail")
    for field in ("cycle_id", "version"):
        _text(record.get(field), f"cycle.{field}")
    stage = record.get("stage")
    if stage not in STAGES:
        raise SupportError(f"cycle.stage must be one of {', '.join(STAGES)}", "fail")
    task = record.get("task")
    if not isinstance(task, dict):
        raise SupportError("cycle.task must be an object", "fail")
    for field in ("study_id", "iteration_id", "objective"):
        _text(task.get(field), f"cycle.task.{field}")
    next_action = record.get("next_action")
    if not isinstance(next_action, dict) or not all(isinstance(next_action.get(field), str) and next_action[field].strip() for field in ("owner", "action")):
        raise SupportError("cycle.next_action needs non-empty owner and action", "fail")

    findings: list[dict[str, str]] = []
    identities: dict[str, Any] = {
        "record": {"path": record_file.relative_to(work_root).as_posix(), **record_identity}
    }
    previous_value = record.get("previous_version")
    if previous_value is not None:
        try:
            previous_path, previous, previous_identity = _reference(work_root, previous_value, "previous_version", findings, identities, json_record=True)
            valid_previous = previous is not None and previous.get("schema") == CYCLE_SCHEMA and previous.get("cycle_id") == record.get("cycle_id") and previous.get("version") != record.get("version")
            _finding(findings, "cycle.previous_version", valid_previous, "prior cycle record has the same cycle_id and a different version" if valid_previous else "previous_version does not identify an earlier version of this cycle")
        except SupportError as exc:
            _finding(findings, "cycle.previous_version", False, str(exc))
    else:
        _finding(findings, "cycle.previous_version", True, "initial cycle has no prior version")

    stage_rank = _STAGE_RANK[stage]
    evidence_map, question_map = _read_synthesis(work_root, record, findings, identities)
    discussion = None
    directions_by_id: dict[str, dict[str, Any]] = {}
    decision = None
    selected_id: str | None = None
    waiting_human = False
    if stage_rank >= _STAGE_RANK["human_discussion"]:
        discussion, directions_by_id, decision = _direction_records(work_root, record, evidence_map, question_map, findings, identities)
        waiting_human, selected_id = _check_choice(record, discussion, directions_by_id, decision, stage_rank, findings)
        if stage == "human_discussion" and discussion is not None:
            state = discussion.get("status")
            if state == "prepared":
                _finding(findings, "discussion.ready_to_discuss", True, "prepared brief is ready for discussion; no discussion or authorization is inferred")
            elif state == "discussed":
                _finding(findings, "discussion.ready_to_discuss", True, "discussion is recorded; a separate direction choice may still be pending")

    context_path = None
    context = None
    context_identity = None
    if stage_rank >= _STAGE_RANK["data_and_plan"] and not waiting_human:
        selected_direction = directions_by_id.get(selected_id) if isinstance(selected_id, str) else None
        selected_intent = selected_direction.get("intent", "study") if isinstance(selected_direction, dict) else None
        study_intent = selected_intent == "study"
        _finding(findings, "direction.study_intent", study_intent, "data_and_plan is entered only for a study-intent direction" if study_intent else "selected direction is not study intent; finish at direction_choice or choose a study direction")
        if study_intent:
            selected_questions, _selected_methods = _choice_links(selected_id, directions_by_id, question_map, evidence_map, findings)
            for qid in selected_questions:
                question = (question_map or {}).get(qid, {})
                status_ok = question.get("status") == "selected_for_study"
                _finding(findings, f"question.{qid}.selected", status_ok, "selected question is marked selected_for_study in this evidence-map version" if status_ok else "selected question is not marked selected_for_study in the current evidence map")
            context_path, context, context_identity, _seal = _check_study_and_plan(
                work_root, record, evidence_map, question_map, directions_by_id, selected_id, findings, identities
            )

    if stage_rank >= _STAGE_RANK["execution_evaluation"] and not waiting_human:
        _check_execution(work_root, record, context_path, context, context_identity, findings, identities)

    if stage == "evidence_revision" and not waiting_human:
        _check_revision(work_root, record_file, record, findings, identities)

    if waiting_human:
        _finding(findings, "human_gate", True, "cycle is structurally waiting for a required human choice")
    first_failure = next((item for item in findings if item["status"] == "needs_work"), None)
    status = "needs_work" if first_failure else "waiting_human" if waiting_human else "ready"
    if first_failure and (waiting_human or next_action.get("owner") == "human"):
        report_next_action = {
            "owner": "researcher",
            "action": f"Resolve {first_failure['id']} ({first_failure['detail']}) and rerun the cycle check.",
        }
    elif waiting_human:
        report_next_action = {
            "owner": "human",
            "action": "record the required discussion and selected direction before advancing this cycle",
        }
    else:
        report_next_action = dict(next_action)
    overall = "fail" if status == "needs_work" else "pass"
    return {
        "schema": CHECK_SCHEMA,
        "checked_utc": utc_now(),
        "overall": overall,
        "stage": stage,
        "status": status,
        "cycle": {"cycle_id": record["cycle_id"], "version": record["version"], "task": dict(task)},
        "findings": findings,
        "next_action": report_next_action,
        "identities": identities,
        "scientific_acceptance": "not_assessed",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="check one research-cycle stage")
    check.add_argument("--record", required=True, help="root-relative cycle JSON")
    check.add_argument("--root", required=True, help="existing work root for all cycle refs")
    check.add_argument("--out", required=True, help="new root-relative JSON report path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        work_root = _as_root(args.root)
        report = check_research_cycle(work_root, args.record)
        out_path = _resolve_ref(work_root, args.out, "out")
        dump_new(out_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        if report["status"] == "ready":
            return 0
        if report["status"] == "waiting_human":
            return 1
        return 2
    except (SupportError, FileExistsError, OSError, ValueError) as exc:
        print(json.dumps({"overall": "fail", "status": "needs_work", "error": str(exc), "scientific_acceptance": "not_assessed"}, ensure_ascii=False), file=sys.stderr)
        return 2


__all__ = [
    "CHECK_SCHEMA",
    "CYCLE_SCHEMA",
    "DECISION_SCHEMA",
    "DISCUSSION_SCHEMA",
    "STAGES",
    "check_research_cycle",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
