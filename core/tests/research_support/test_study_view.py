from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest


CORE = Path(__file__).resolve().parents[2]
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from scripts.research_support.study_view import VIEW_SCHEMA, build_study_view, main, render_html  # noqa: E402


DEMO = CORE / "examples" / "research-support" / "study-view" / "demo-study"
FIXTURE = CORE / "examples" / "research-support" / "research-cycle" / "offline-fixture"


def _edges(model: dict, relation: str) -> set[tuple[str, str]]:
    return {(edge["from"], edge["to"]) for edge in model["edges"] if edge["relation"] == relation}


def _rewrite(path: Path, change) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    change(value)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class StudyViewModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "study"
        shutil.copytree(DEMO, self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_demo_cycles_follow_version_links_and_reuse_checker_status(self) -> None:
        model = build_study_view(DEMO)
        self.assertEqual(model["schema"], VIEW_SCHEMA)
        self.assertEqual(model["scientific_acceptance"], "not_assessed")
        self.assertEqual([c["version"] for c in model["cycles"]], ["cycle-v1", "cycle-v2"])
        self.assertEqual([c["check"]["status"] for c in model["cycles"]], ["ready", "ready"])
        self.assertEqual(model["cycles"][1]["previous"], "cycle-v1.json")
        self.assertIn(("file:cycle-v1.json", "file:cycle-v2.json"), _edges(model, "next_version"))
        self.assertEqual(model["gaps"], [])

    def test_edges_run_from_basis_to_derived_record(self) -> None:
        model = build_study_view(DEMO)
        self.assertIn(
            ("source:source-ledger.json#demo-source-3", "finding:evidence-map.json#demo-finding-2"),
            _edges(model, "supports"),
        )
        self.assertIn(
            ("finding:evidence-map.json#demo-finding-2", "question:evidence-map.json#demo-question-2"),
            _edges(model, "motivates"),
        )
        self.assertIn(
            ("direction:discussion-v2.json#demo-direction-batch", "decision:discussion-v2.json"),
            _edges(model, "chosen_by"),
        )
        self.assertIn(("file:brief.md", "file:discussion-v2.json"), _edges(model, "brief_ref"))
        self.assertIn(("file:evidence-map.json", "file:cycle-v2.json"), _edges(model, "used_by"))

    def test_branch_states_keep_unselected_and_pending_directions(self) -> None:
        model = build_study_view(DEMO)
        first, second = model["cycles"]
        self.assertFalse(first["branches"]["decided"])
        self.assertEqual(set(first["branches"]["branches"].values()), {"awaiting_choice"})
        self.assertEqual(second["branches"]["selected"], "demo-direction-batch")
        self.assertEqual(
            second["branches"]["branches"],
            {
                "demo-direction-batch": "selected",
                "demo-direction-labels": "not_selected",
                "demo-direction-review": "not_selected",
            },
        )
        deferred = model["nodes"]["direction:discussion-v2.json#demo-direction-labels"]
        self.assertEqual(deferred["intent"], "defer")
        self.assertEqual(deferred["facts"]["change_condition"], "取得标签版本记录后重新讨论。")

    def test_revision_chain_keeps_planned_work_and_unselected_route(self) -> None:
        model = build_study_view(DEMO)
        prefix = "revision/research-revision.json#"
        self.assertIn((f"rsource:{prefix}demo-review", f"request:{prefix}demo-req-labels"), _edges(model, "raises"))
        self.assertIn((f"rdecision:{prefix}demo-req-labels", f"task:{prefix}demo-task-labels"), _edges(model, "assigns"))
        self.assertEqual(model["nodes"][f"task:{prefix}demo-task-labels"]["status"], "planned")
        self.assertIn((f"request:{prefix}demo-req-labels", f"route:{prefix}demo-route-labels"), _edges(model, "handled_by_other_route"))
        self.assertIn((f"request:{prefix}demo-req-explain", f"claim:{prefix}demo-claim-1"), _edges(model, "answered_by"))

    def test_unresolved_links_become_gaps_and_null_references_do_not(self) -> None:
        _rewrite(self.root / "discussion-v2.json", lambda d: d.update(technical_route_ref="missing-route.md"))
        _rewrite(
            self.root / "evidence-map.json",
            lambda d: d["findings"][0]["evidence"][0].update(source_id="demo-source-unknown"),
        )
        model = build_study_view(self.root)
        details = [gap["detail"] for gap in model["gaps"]]
        self.assertTrue(any("missing-route.md" in item and "missing" in item for item in details))
        self.assertTrue(any("demo-source-unknown" in item for item in details))
        self.assertFalse(any(source.endswith("demo-source-unknown") for source, _ in _edges(model, "supports")))
        self.assertFalse(any("bridge_ref" in item for item in details), "null references are not gaps")
        refs = {r["field"]: r["state"] for s in model["cycles"][1]["stages"] for r in s["refs"]}
        self.assertEqual(refs["study.bridge_ref"], "null")

    def test_later_stage_shows_checker_findings_and_directory_references(self) -> None:
        (self.root / "plan-v1").mkdir()
        (self.root / "plan-v1" / "PLAN.md").write_text("Synthetic plan.\n", encoding="utf-8")
        _rewrite(
            self.root / "cycle-v2.json",
            lambda d: (d.update(stage="data_and_plan"), d["study"].update(first_plan_ref="plan-v1", bridge_ref="missing-bridge.json")),
        )
        model = build_study_view(self.root)
        cycle = model["cycles"][1]
        self.assertEqual(cycle["check"]["status"], "needs_work")
        refs = {r["field"]: r["state"] for s in cycle["stages"] for r in s["refs"]}
        self.assertEqual(refs["study.first_plan_ref"], "directory")
        self.assertEqual(refs["study.bridge_ref"], "missing")
        self.assertTrue(any(gap["kind"] == "cycle_check" for gap in model["gaps"]))

    def test_offline_fixture_waits_for_discussion(self) -> None:
        model = build_study_view(FIXTURE)
        self.assertEqual(len(model["cycles"]), 1)
        self.assertEqual(model["cycles"][0]["stage"], "human_discussion")
        self.assertEqual(set(model["cycles"][0]["branches"]["branches"].values()), {"awaiting_choice"})

    def test_symlinks_and_dot_directories_are_not_read(self) -> None:
        outside = Path(self.temporary.name) / "outside.json"
        outside.write_text('{"schema": "outside"}\n', encoding="utf-8")
        (self.root / ".hidden").mkdir()
        (self.root / ".hidden" / "note.md").write_text("hidden\n", encoding="utf-8")
        try:
            os.symlink(outside, self.root / "linked.json")
        except OSError as exc:
            self.skipTest(f"symlink creation is unavailable: {exc}")
        files = build_study_view(self.root)["files"]
        self.assertNotIn("linked.json", files)
        self.assertNotIn(".hidden/note.md", files)


class StudyViewHtmlTests(unittest.TestCase):
    def test_record_text_cannot_close_the_data_script(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "study"
            shutil.copytree(DEMO, root)
            hostile = "</script><script>alert(1)</script>"
            _rewrite(root / "evidence-map.json", lambda d: d["findings"][0].update(claim_or_observation=hostile))
            model = build_study_view(root)
            html = render_html(model)
        self.assertNotIn(hostile, html)
        self.assertEqual(html.count("</script>"), 2)
        data = re.search(r'<script type="application/json" id="study-view-data">(.*?)</script>', html, re.S).group(1)
        embedded = json.loads(data)
        self.assertEqual(embedded["nodes"]["finding:evidence-map.json#demo-finding-1"]["label"], hostile)

    def test_cli_writes_once_and_excludes_its_own_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "study"
            shutil.copytree(DEMO, root)
            out = root / "view.html"
            model_out = Path(temporary) / "model.json"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--root", str(root), "--out", str(out), "--model-out", str(model_out)]), 0)
            self.assertIn("研究路径视图", out.read_text(encoding="utf-8"))
            self.assertNotIn("view.html", json.loads(model_out.read_text(encoding="utf-8"))["files"])
            before = out.read_bytes()
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["--root", str(root), "--out", str(out)]), 2)
            self.assertEqual(out.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
