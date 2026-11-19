from __future__ import annotations

import contextlib
import copy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


CORE = Path(__file__).resolve().parents[2]
PUBLIC_ROOT = CORE.parent
FIXTURE = CORE / "examples" / "research-support" / "research-cycle" / "offline-fixture"
SCRATCH = PUBLIC_ROOT / ".scratch" / "research-cycle-tests"
SCRATCH.mkdir(parents=True, exist_ok=True)
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from scripts.research_support._common import dump_new, fingerprint, load_json, load_json_with_identity  # noqa: E402
from scripts.research_support.plan_seal import STANDARD_PROTOCOL, build_ready, seal_plan  # noqa: E402
from scripts.research_support.research_cycle import CHECK_SCHEMA, check_research_cycle  # noqa: E402
from scripts.research_support.run_bounded import main as run_bounded_main  # noqa: E402
from scripts.research_support.task_context import SPEC_SCHEMA, init_task_context  # noqa: E402


STUDY_ID = "synthetic-study-001"
ITERATION_ID = "synthetic-iteration-001"
TASK_ID = "synthetic-task-001"
OBJECTIVE = "Exercise continuity structure without science."
DIRECTION_ID = "synthetic-direction-001"
QUESTION_ID = "synthetic-question-001"
METHOD_ID = "synthetic-method-001"
FINDING_ID = "synthetic-finding-001"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _copy_fixture(root: Path) -> dict:
    shutil.copytree(FIXTURE, root, dirs_exist_ok=True)
    return load_json(root / "cycle.json")


def _make_study_package(root: Path, *, seal: bool, stage: str = "execution_evaluation") -> dict:
    """Build a wholly synthetic native D53/D51 package; no science is run here."""

    cycle = _copy_fixture(root)
    ledger = load_json(root / "source-ledger.json")
    evidence_map = load_json(root / "evidence-map.json")
    evidence_map["method_candidates"][0]["selection"] = "selected"
    evidence_map["questions"][0]["status"] = "selected_for_study"
    _write_json(root / "source-ledger.json", ledger)
    _write_json(root / "evidence-map.json", evidence_map)

    human_input = root / "human-input.md"
    human_input.write_text("Synthetic decision placeholder; no human discussion occurred.\n", encoding="utf-8")
    discussion = load_json(root / "discussion.json")
    discussion["status"] = "discussed"
    discussion["discussion"] = {
        "held_at": "synthetic timestamp",
        "human_input_ref": "human-input.md",
        "points_raised": ["synthetic placeholder"],
        "unresolved": [],
    }
    discussion["decision"] = {
        "status": "recorded",
        "authority": "human",
        "selected_direction_id": DIRECTION_ID,
        "source_ref": None,
        "made_at": "synthetic timestamp",
        "reason": "Synthetic test record only.",
        "constraints": [],
    }
    _write_json(root / "discussion.json", discussion)

    plan_root = root / "plan-v1"
    plan_root.mkdir(parents=True, exist_ok=True)
    _write_json(plan_root / "plan.json", {"schema": "synthetic-plan.v1", "note": "no scientific plan"})
    if seal:
        ready = build_ready(plan_root, "synthetic-plan-001", STANDARD_PROTOCOL)
        dump_new(plan_root / "READY.json", ready)
        seal_plan(plan_root, root / "sealed-plan-v1", "synthetic-plan-001", STANDARD_PROTOCOL)

    inputs = root / "inputs"
    inputs.mkdir(exist_ok=True)
    _write_json(inputs / "entry.json", {"fixture": "synthetic", "purpose": "identity test"})
    spec = {
        "schema": SPEC_SCHEMA,
        "study_id": STUDY_ID,
        "iteration_id": ITERATION_ID,
        "task_id": TASK_ID,
        "objective": OBJECTIVE,
        "phase": "synthetic-integration",
        "next_gate": "check synthetic bounded-run binding",
        "entry": "inputs/entry.json",
        "plan": "plan-v1/plan.json",
        "output_root": "output",
        "completion": {
            "delivery": "output/TASK-DELIVERY.json",
            "requirements": [
                {
                    "id": "synthetic-run",
                    "role": "synthetic operational run receipt",
                    "path": "output/run.receipt.json",
                    "evidence_kind": "bounded-run",
                }
            ],
        },
    }
    _write_json(root / "task-spec.json", spec)
    init_task_context(root, "task-spec.json", "task-context.json")

    bridge = {
        "schema": "harness.research-preparation-study-bridge/v1",
        "preparation_id": "synthetic-preparation-001",
        "review_version": "review-v1",
        "transition": {
            "decision": "transition_to_study",
            "decided_on": "synthetic date",
            "reason": "Synthetic fixture only.",
            "alternatives_considered": [],
            "source_ledger_ref": {"path": "source-ledger.json", "version": "ledger-v1"},
            "evidence_map_ref": {"path": "evidence-map.json", "version": "map-v1"},
            "selected_question_ids": [QUESTION_ID],
            "selected_finding_ids": [FINDING_ID],
        },
        "study": {
            "study_id": STUDY_ID,
            "iteration_id": ITERATION_ID,
            "objective": OBJECTIVE,
            "research_question": "Synthetic question only.",
            "owner": "fixture",
            "work_root": str(root.resolve()),
        },
        "selected_approach": {
            "method_id": METHOD_ID,
            "reason": "Synthetic fixture only.",
            "alternatives": [],
            "change_condition": "Replace before any real use.",
        },
        "data_requirements": [],
        "evaluation_design": {},
        "resource_gap": {"status": "not_applicable_fixture", "reference": "", "next_decision": ""},
        "first_plan": {
            "path": "plan-v1",
            "version": "plan-v1",
            "seal_status": "archived" if seal else "not_archived",
            "acceptance_gates": [],
        },
        "first_audit": {},
        "first_artifact_seam": {},
        "budget_and_acceptance": {},
    }
    _write_json(root / "bridge.json", bridge)

    cycle.update(
        {
            "version": "cycle-v2",
            "stage": stage,
            "task": {
                "study_id": STUDY_ID,
                "iteration_id": ITERATION_ID,
                "task_id": TASK_ID,
                "objective": OBJECTIVE,
            },
            "synthesis": {
                "source_ledger_ref": "source-ledger.json",
                "evidence_map_ref": "evidence-map.json",
                "report_ref": "brief.md",
                "question_ids": [QUESTION_ID],
                "time_scope": {
                    "mode": "historical_window",
                    "as_of": "synthetic historical cutoff",
                    "refresh_before_study": False,
                    "refresh_record_ref": None,
                    "study_target": "same_historical_window",
                    "historical_scope_reason": "Reconstruct only this synthetic historical window.",
                },
            },
            "direction": {
                "discussion_ref": "discussion.json",
                "requires_human_choice": True,
                "selected_direction_id": DIRECTION_ID,
            },
            "study": {
                "bridge_ref": "bridge.json",
                "task_context_ref": "task-context.json",
                "first_plan_ref": "plan-v1",
                "first_plan_seal_ref": "plan-v1/ARCHIVED.json",
            },
            "execution": {
                "run_receipt_refs": [],
                "evaluation_ref": "evaluation.json",
                "evidence_refs": ["evidence.txt"],
                "delivery_ref": "output/TASK-DELIVERY.json",
            },
            "revision": {"decision_ref": None, "next_cycle_ref": None},
        }
    )
    _write_json(root / "evaluation.json", {"fixture": "synthetic", "assessment": "not scientific"})
    (root / "evidence.txt").write_text("Synthetic evidence pointer; no scientific evidence.\n", encoding="utf-8")
    _write_json(root / "cycle.json", cycle)
    return cycle


def _run_synthetic_child(root: Path) -> Path:
    context_path = root / "task-context.json"
    output_prefix = root / "output" / "run"
    argv = [
        "--cwd", str(root),
        "--out-prefix", str(output_prefix),
        "--task-root", str(root),
        "--task-context", str(context_path),
        "--study-id", STUDY_ID,
        "--iteration-id", ITERATION_ID,
        "--timeout-seconds", "10",
        "--memory-limit-mib", "128",
        "--threads", "1",
        "--sample-interval-seconds", "0.05",
        "--", sys.executable, "-B", "-c", "print('synthetic continuity child')",
    ]
    output = io.StringIO()
    error = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
        return_code = run_bounded_main(argv)
    if return_code != 0:
        raise AssertionError(f"synthetic bounded child returned {return_code}: {error.getvalue()} {output.getvalue()}")
    receipt_path = root / "output" / "run.receipt.json"
    receipt = load_json(receipt_path)
    if receipt.get("result", {}).get("kind") != "success" or not isinstance(receipt.get("result", {}).get("root_pid"), int):
        raise AssertionError("bounded runner did not produce a successful real-root-PID receipt")
    context, context_identity = load_json_with_identity(context_path)
    del context
    artifact_identity = fingerprint(receipt_path)
    _write_json(
        root / "output" / "TASK-DELIVERY.json",
        {
            "schema": "research-support.task-delivery.v1",
            "study_id": STUDY_ID,
            "iteration_id": ITERATION_ID,
            "task_id": TASK_ID,
            "context_sha256": context_identity["sha256"],
            "artifacts": [
                {
                    "requirement_id": "synthetic-run",
                    "path": "output/run.receipt.json",
                    **artifact_identity,
                }
            ],
        },
    )
    return receipt_path


class ResearchCycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="cycle-", dir=SCRATCH)
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def _cli(self, root: Path, *, out: str = "check.json", record: str = "cycle.json") -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        temp = SCRATCH / "temp"
        temp.mkdir(parents=True, exist_ok=True)
        env.update(
            {
                "TEMP": str(temp),
                "TMP": str(temp),
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        script = CORE / "scripts" / "research_support" / "research_cycle.py"
        return subprocess.run(
            [sys.executable, "-B", str(script), "check", "--root", str(root), "--record", record, "--out", out],
            cwd=CORE,
            env=env,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )

    def test_prepared_brief_is_ready_to_discuss_but_not_a_choice(self) -> None:
        cycle = _copy_fixture(self.root)
        report = check_research_cycle(self.root, "cycle.json")
        self.assertEqual(report["schema"], CHECK_SCHEMA)
        self.assertEqual(report["status"], "ready")
        self.assertEqual(cycle["direction"]["selected_direction_id"], None)
        self.assertEqual(load_json(self.root / "discussion.json")["decision"]["status"], "pending")
        self.assertTrue(any(item["id"] == "discussion.ready_to_discuss" and item["status"] == "pass" for item in report["findings"]))
        self.assertEqual(report["scientific_acceptance"], "not_assessed")

    def test_pending_human_choice_is_waiting_and_cli_returns_one(self) -> None:
        cycle = _copy_fixture(self.root)
        cycle["stage"] = "direction_choice"
        _write_json(self.root / "cycle.json", cycle)
        report = self._cli(self.root)
        self.assertEqual(report.returncode, 1, report.stderr)
        check = load_json(self.root / "check.json")
        self.assertEqual(check["status"], "waiting_human")
        self.assertEqual(check["overall"], "pass")
        self.assertEqual(check["next_action"]["owner"], "human")

    def test_missing_report_takes_priority_over_pending_human_choice(self) -> None:
        cycle = _copy_fixture(self.root)
        cycle["stage"] = "direction_choice"
        cycle["synthesis"]["report_ref"] = "revised-brief.md"
        _write_json(self.root / "cycle.json", cycle)
        result = self._cli(self.root)
        self.assertEqual(result.returncode, 2, result.stderr)
        check = load_json(self.root / "check.json")
        self.assertEqual(check["status"], "needs_work")
        self.assertEqual(check["overall"], "fail")
        self.assertIn("human_gate", [item["id"] for item in check["findings"] if item["status"] == "pass"])
        self.assertEqual(check["next_action"]["owner"], "researcher")
        self.assertIn("synthesis.report_ref", check["next_action"]["action"])
        self.assertIn("revised-brief.md", check["next_action"]["action"])
        self.assertEqual(load_json(self.root / "cycle.json")["next_action"], cycle["next_action"])

        shutil.copyfile(self.root / "brief.md", self.root / "revised-brief.md")
        repaired = check_research_cycle(self.root, "cycle.json")
        self.assertEqual(repaired["status"], "waiting_human")
        self.assertEqual(repaired["next_action"]["owner"], "human")

    def test_unreadable_discussion_before_human_choice_routes_to_researcher(self) -> None:
        cycle = _copy_fixture(self.root)
        cycle["stage"] = "direction_choice"
        cycle["direction"]["discussion_ref"] = "missing-discussion.json"
        cycle["next_action"]["owner"] = "human"
        _write_json(self.root / "cycle.json", cycle)
        check = check_research_cycle(self.root, "cycle.json")
        self.assertEqual(check["status"], "needs_work")
        self.assertEqual(check["next_action"]["owner"], "researcher")
        self.assertIn("discussion_ref", check["next_action"]["action"])

    def test_required_human_choice_cannot_be_bypassed_by_existing_authorization(self) -> None:
        cycle = _copy_fixture(self.root)
        (self.root / "authorization.md").write_text("Synthetic authority pointer.\n", encoding="utf-8")
        cycle["stage"] = "direction_choice"
        cycle["direction"]["selected_direction_id"] = DIRECTION_ID
        discussion = load_json(self.root / "discussion.json")
        discussion["decision"] = {
            "status": "recorded",
            "authority": "existing_authorization",
            "selected_direction_id": DIRECTION_ID,
            "source_ref": "authorization.md",
            "made_at": "synthetic time",
            "reason": "Synthetic test only.",
            "constraints": [],
        }
        _write_json(self.root / "discussion.json", discussion)
        _write_json(self.root / "cycle.json", cycle)
        report = check_research_cycle(self.root, "cycle.json")
        self.assertEqual(report["status"], "needs_work")
        self.assertTrue(any(item["id"] == "direction.authority" and item["status"] == "needs_work" for item in report["findings"]))

    def test_existing_authorization_is_accepted_when_human_choice_is_not_required(self) -> None:
        cycle = _copy_fixture(self.root)
        (self.root / "authorization.md").write_text("Synthetic authority pointer.\n", encoding="utf-8")
        cycle["stage"] = "direction_choice"
        cycle["direction"]["requires_human_choice"] = False
        cycle["direction"]["selected_direction_id"] = DIRECTION_ID
        discussion = load_json(self.root / "discussion.json")
        discussion["decision"] = {
            "status": "recorded",
            "authority": "existing_authorization",
            "selected_direction_id": DIRECTION_ID,
            "source_ref": "authorization.md",
            "made_at": "synthetic time",
            "reason": "Synthetic pre-existing authorization route.",
            "constraints": [],
        }
        _write_json(self.root / "discussion.json", discussion)
        _write_json(self.root / "cycle.json", cycle)
        report = check_research_cycle(self.root, "cycle.json")
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["next_action"], cycle["next_action"])

    def test_historical_current_refresh_is_deferred_until_data_and_plan(self) -> None:
        cycle = _copy_fixture(self.root)
        cycle["synthesis"]["time_scope"].update(
            {
                "mode": "historical_window",
                "study_target": "current",
                "refresh_before_study": True,
                "refresh_record_ref": None,
                "historical_scope_reason": None,
            }
        )
        _write_json(self.root / "cycle.json", cycle)
        early = check_research_cycle(self.root, "cycle.json")
        self.assertEqual(early["status"], "ready")
        cycle["stage"] = "data_and_plan"
        _write_json(self.root / "cycle.json", cycle)
        gated = check_research_cycle(self.root, "cycle.json")
        self.assertEqual(gated["status"], "needs_work")
        self.assertTrue(any(item["id"] == "time_scope.refresh_record_ref" and item["status"] == "needs_work" for item in gated["findings"]))
        self.assertIn("time_scope.refresh_record_ref", gated["next_action"]["action"])

    def test_selected_question_method_must_map_to_evidence_map(self) -> None:
        _copy_fixture(self.root)
        discussion = load_json(self.root / "discussion.json")
        discussion["directions"][0]["method_ids"] = ["synthetic-method-absent"]
        _write_json(self.root / "discussion.json", discussion)
        report = check_research_cycle(self.root, "cycle.json")
        self.assertEqual(report["status"], "needs_work")
        self.assertTrue(any(item["id"] == f"direction.{DIRECTION_ID}.evidence_links" and item["status"] == "needs_work" for item in report["findings"]))

    def test_review_delivery_intent_can_close_at_choice_but_not_enter_study(self) -> None:
        cycle = _copy_fixture(self.root)
        (self.root / "human-input.md").write_text("Synthetic placeholder.\n", encoding="utf-8")
        discussion = load_json(self.root / "discussion.json")
        discussion["status"] = "discussed"
        discussion["discussion"] = {
            "held_at": "synthetic time",
            "human_input_ref": "human-input.md",
            "points_raised": ["fixture"],
            "unresolved": [],
        }
        discussion["directions"][0].update({"intent": "review_delivery", "question_ids": [], "method_ids": []})
        discussion["decision"] = {
            "status": "recorded",
            "authority": "human",
            "selected_direction_id": DIRECTION_ID,
            "source_ref": None,
            "made_at": "synthetic time",
            "reason": "End with review delivery; no study authorized.",
            "constraints": [],
        }
        cycle["stage"] = "direction_choice"
        cycle["direction"]["selected_direction_id"] = DIRECTION_ID
        _write_json(self.root / "discussion.json", discussion)
        _write_json(self.root / "cycle.json", cycle)
        self.assertEqual(check_research_cycle(self.root, "cycle.json")["status"], "ready")
        cycle["stage"] = "data_and_plan"
        _write_json(self.root / "cycle.json", cycle)
        report = check_research_cycle(self.root, "cycle.json")
        self.assertEqual(report["status"], "needs_work")
        self.assertTrue(any(item["id"] == "direction.study_intent" and item["status"] == "needs_work" for item in report["findings"]))

    def test_unsealed_first_plan_does_not_pass_data_and_plan(self) -> None:
        _make_study_package(self.root, seal=False, stage="data_and_plan")
        report = check_research_cycle(self.root, "cycle.json")
        self.assertEqual(report["status"], "needs_work")
        self.assertTrue(any(item["id"] in {"study.first_plan_seal_ref", "first_plan.seal"} and item["status"] == "needs_work" for item in report["findings"]))

    def test_planned_receipt_is_not_an_execution(self) -> None:
        cycle = _make_study_package(self.root, seal=True)
        context_path = self.root / "task-context.json"
        _context, context_identity = load_json_with_identity(context_path)
        fake_receipt = {
            "schema": "bounded-run-receipt.v1",
            "result": {"kind": "planned", "root_exit_code": None, "root_pid": 12345},
            "command": {"cwd": str(self.root.resolve())},
            "task_context": {
                "study_id": STUDY_ID,
                "iteration_id": ITERATION_ID,
                "task_id": TASK_ID,
                "context_path": str(context_path.resolve()),
                "context_sha256": context_identity["sha256"],
            },
        }
        _write_json(self.root / "output" / "planned.receipt.json", fake_receipt)
        cycle["execution"]["run_receipt_refs"] = ["output/planned.receipt.json"]
        _write_json(self.root / "cycle.json", cycle)
        report = check_research_cycle(self.root, "cycle.json")
        self.assertEqual(report["status"], "needs_work")
        run_finding = next(item for item in report["findings"] if item["id"] == "execution.run_receipt[0]")
        self.assertEqual(run_finding["status"], "needs_work")
        self.assertIn("not a successful real bounded run", run_finding["detail"])

    def test_plan_question_and_method_many_to_many_mapping_is_allowed(self) -> None:
        cycle = _copy_fixture(self.root)
        evidence_map = load_json(self.root / "evidence-map.json")
        evidence_map["questions"].append(
            {
                **evidence_map["questions"][0],
                "question_id": "synthetic-question-002",
                "candidate_method_ids": ["synthetic-method-002"],
            }
        )
        evidence_map["method_candidates"].append(
            {
                **evidence_map["method_candidates"][0],
                "method_id": "synthetic-method-002",
            }
        )
        evidence_map["questions"][0]["candidate_method_ids"] = [METHOD_ID]
        _write_json(self.root / "evidence-map.json", evidence_map)
        cycle["synthesis"]["question_ids"] = [QUESTION_ID, "synthetic-question-002"]
        discussion = load_json(self.root / "discussion.json")
        discussion["directions"][0]["question_ids"] = [QUESTION_ID, "synthetic-question-002"]
        discussion["directions"][0]["method_ids"] = [METHOD_ID, "synthetic-method-002"]
        _write_json(self.root / "discussion.json", discussion)
        _write_json(self.root / "cycle.json", cycle)
        report = check_research_cycle(self.root, "cycle.json")
        self.assertEqual(report["status"], "ready")
        self.assertTrue(any(item["id"] == f"direction.{DIRECTION_ID}.question_method_alignment" and item["status"] == "pass" for item in report["findings"]))

    def test_out_path_escape_is_rejected_without_writing(self) -> None:
        _copy_fixture(self.root)
        escaped = self.root.parent / "escaped-report.json"
        self.assertFalse(escaped.exists())
        result = self._cli(self.root, out="../escaped-report.json")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(escaped.exists())

    def test_actual_run_is_bound_and_reusable_for_same_cycle_revision(self) -> None:
        cycle = _make_study_package(self.root, seal=True)
        receipt_path = _run_synthetic_child(self.root)
        receipt = load_json(receipt_path)
        self.assertEqual(receipt["result"]["kind"], "success")
        self.assertGreater(receipt["result"]["root_pid"], 0)
        cycle["execution"]["run_receipt_refs"] = ["output/run.receipt.json"]
        _write_json(self.root / "cycle.json", cycle)
        initial = check_research_cycle(self.root, "cycle.json")
        self.assertEqual(initial["status"], "ready", [f for f in initial["findings"] if f["status"] != "pass"])
        run_finding = next(item for item in initial["findings"] if item["id"] == "execution.run_receipt[0]")
        self.assertIn("freshness within the cycle version is not inferred", run_finding["detail"])

        prior = copy.deepcopy(cycle)
        prior["version"] = "cycle-v1"
        prior["stage"] = "execution_evaluation"
        _write_json(self.root / "cycle-v1.json", prior)
        decision = {
            "schema": "research-support.research-cycle-decision.v1",
            "cycle_id": cycle["cycle_id"],
            "version": "cycle-v2",
            "action": "keep",
            "reason": "Continue evaluating the same synthetic result.",
            "evidence_refs": ["evidence.txt"],
            "next_action": {"owner": "human", "action": "review the synthetic evaluation"},
        }
        _write_json(self.root / "decision-v2.json", decision)
        cycle["stage"] = "evidence_revision"
        cycle["previous_version"] = "cycle-v1.json"
        cycle["revision"] = {"decision_ref": "decision-v2.json", "next_cycle_ref": None}
        _write_json(self.root / "cycle.json", cycle)
        revision = check_research_cycle(self.root, "cycle.json")
        self.assertEqual(revision["status"], "ready", [f for f in revision["findings"] if f["status"] != "pass"])

        wrong_root = self.root.parent / f"{self.root.name}-different-work-root"
        self.addCleanup(shutil.rmtree, wrong_root, True)
        shutil.copytree(self.root, wrong_root)
        wrong_root_report = check_research_cycle(wrong_root, "cycle.json")
        self.assertEqual(wrong_root_report["status"], "needs_work")
        self.assertTrue(any(item["id"] == "task_context.current" and item["status"] == "needs_work" for item in wrong_root_report["findings"]))

        second_spec = load_json(self.root / "task-spec.json")
        second_spec["iteration_id"] = "synthetic-iteration-002"
        second_spec["output_root"] = "output-iteration-002"
        second_spec["completion"]["delivery"] = "output-iteration-002/TASK-DELIVERY.json"
        second_spec["completion"]["requirements"][0]["path"] = "output-iteration-002/run.receipt.json"
        _write_json(self.root / "task-spec-iteration-002.json", second_spec)
        init_task_context(self.root, "task-spec-iteration-002.json", "task-context-iteration-002.json")
        bridge = load_json(self.root / "bridge.json")
        bridge["study"]["iteration_id"] = "synthetic-iteration-002"
        _write_json(self.root / "bridge.json", bridge)
        new_iteration = copy.deepcopy(cycle)
        new_iteration["stage"] = "execution_evaluation"
        new_iteration["version"] = "cycle-iteration-002"
        new_iteration["previous_version"] = None
        new_iteration["task"]["iteration_id"] = "synthetic-iteration-002"
        new_iteration["study"]["task_context_ref"] = "task-context-iteration-002.json"
        _write_json(self.root / "cycle-iteration-002.json", new_iteration)
        stale_run = check_research_cycle(self.root, "cycle-iteration-002.json")
        self.assertEqual(stale_run["status"], "needs_work")
        run_check = next(item for item in stale_run["findings"] if item["id"] == "execution.run_receipt[0]")
        self.assertEqual(run_check["status"], "needs_work")


if __name__ == "__main__":
    unittest.main()
