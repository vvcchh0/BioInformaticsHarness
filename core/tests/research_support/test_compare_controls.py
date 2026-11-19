import contextlib
import csv
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

CORE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CORE))
from scripts.research_support.compare_controls import main


class PairedControlTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.header = "id,label,view,rule,budget,metric\n"
        self.left = "a,1,short,dev,4,P\nb,2,short,dev,4,P\n"

    def run_case(self, right, *, left=None, kind="entity", vary="input", header=None, extra=()):
        (self.root / "a.csv").write_text(self.header + (self.left if left is None else left), encoding="utf-8")
        (self.root / "b.csv").write_text((self.header if header is None else header) + right, encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["a.csv", "b.csv", "--root", str(self.root), "--key", "id", "--key-kind", kind,
                         "--vary", vary, "--label", "label", "--input", "view", "--selection", "rule",
                         "--selection", "budget", "--evaluation", "metric", *extra])
        return code, json.loads((err if code else out).getvalue())

    def test_multiple_changes_and_system_purpose(self):
        right = "b,2,long,dev,8,P\nc,3,long,dev,8,P\n"
        _, report = self.run_case(right)
        self.assertEqual(report["changed"], ["members", "input", "selection"])
        self.assertEqual(report["common_count"], 1)
        self.assertEqual(report["left_only_count"], 1)
        self.assertEqual(report["right_only_count"], 1)
        self.assertEqual(report["disposition"], "other_declared_controls_differ")
        self.assertEqual(self.run_case(right, vary="system")[1]["disposition"], "describe_observed_systems")

    def test_single_change_is_conditional_and_order_independent(self):
        _, report = self.run_case("b,2,long,dev,4,P\na,1,long,dev,4,P\n")
        self.assertEqual(report["disposition"], "only_target_differs_on_declared_controls")
        self.assertEqual(report["scientific_acceptance"], "not_assessed")
        self.assertEqual(self.run_case(self.left)[1]["disposition"], "no_observed_target_contrast")

    def test_positional_blank_absent_or_mixed_evidence(self):
        right = "a,1,long,dev,4,P\nb,2,long,dev,4,P\n"
        self.assertIn("members", self.run_case(right, kind="position")[1]["unknown_or_incomplete"])
        for rows, head in [(right.replace(",4,", ",,"), self.header),
                           (right, self.header.replace("budget", "other"))]:
            report = self.run_case(rows, header=head)[1]
            self.assertEqual(report["disposition"], "insufficient_pairing_or_controls")
            self.assertIn("selection", report["unknown_or_incomplete"])
        mixed = self.run_case("a,1,long,dev,,P\nb,2,long,dev,8,P\n")[1]
        self.assertIn("selection", mixed["changed"])
        self.assertIn("selection", mixed["unknown_or_incomplete"])

    def test_label_difference_disjoint_and_empty_pairs(self):
        self.assertIn("label", self.run_case(self.left.replace("a,1", "a,9"))[1]["changed"])
        for right, left in [("c,3,long,dev,4,P\n", None), ("", "")]:
            report = self.run_case(right, left=left)[1]
            self.assertEqual(report["common_count"], 0)
            self.assertIn("input", report["unknown_or_incomplete"])

    def test_fixed_weight_and_explicit_missing_marker(self):
        report = self.run_case(self.left.replace(",4,", ",8,"), extra=("--fixed", "weights"))[1]
        self.assertIn("fixed", report["unknown_or_incomplete"])
        head = self.header.rstrip() + ",weights\n"
        (self.root / "a.csv").write_text(head + "a,1,short,dev,4,P,w1\n", encoding="utf-8")
        (self.root / "b.csv").write_text(head + "a,1,long,dev,4,P,w2\n", encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["a.csv", "b.csv", "--root", str(self.root), "--key", "id", "--key-kind", "entity",
                         "--vary", "input", "--fixed", "weights", "--input", "view"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["changed"], ["input", "fixed"])
        report = self.run_case(self.left.replace(",4,", ",NA,"), extra=("--missing-value", "NA"))[1]
        self.assertIn("selection", report["unknown_or_incomplete"])
        self.assertIn("selection", self.run_case(self.left.replace(",4,", ",unknown,"))[1]["unknown_or_incomplete"])

    def test_pseudonyms_pair_by_scope_and_unit_after_reordering(self):
        fixture = CORE / "examples/research-support/paired-controls"
        left = (fixture / "units-before.csv").read_text(encoding="utf-8")
        right = (fixture / "units-after.csv").read_text(encoding="utf-8").splitlines()
        (self.root / "left.csv").write_text(left, encoding="utf-8")
        args = ["left.csv", "right.csv", "--root", str(self.root), "--key", "unit_namespace",
                "--key", "unit_id", "--key-kind", "entity", "--vary", "members", "--label", "readout"]
        for distinct_scope, expected in [(False, (2, 1, 1)), (True, (0, 3, 3))]:
            with self.subTest(distinct_scope=distinct_scope):
                rows = [right[0], *reversed(right[1:])]
                if distinct_scope:
                    rows = [row.replace("example-study-v1", "other-study") for row in rows]
                (self.root / "right.csv").write_text("\n".join(rows), encoding="utf-8")
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(main(args), 0)
                report = json.loads(output.getvalue())
                observed = tuple(report[key] for key in ("common_count", "left_only_count", "right_only_count"))
                self.assertEqual(observed, expected)
                if not distinct_scope:
                    self.assertEqual(report["axes"]["label"]["changed_pairs"], 2)

    def compare_sources(self, update=None, *, vary="sources", required=False, grouped=True, extra_sources=()):
        fixture = CORE / "examples/research-support/paired-controls"
        for name, original in (("a", "shared"), ("b", "separate")):
            with (fixture / f"calibration-{original}.csv").open(encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
                header = list(reader.fieldnames)
            if update:
                update(name, rows, header)
            with (self.root / f"{name}.csv").open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, header)
                writer.writeheader()
                writer.writerows(rows)
        args = ["a.csv", "b.csv", "--root", str(self.root), "--vary", vary, "--key-kind", "entity",
                "--key", "scope", "--key", "fold", "--key", "unit", "--label", "target",
                "--label", "target_source", "--label", "target_definition", "--input", "input_view",
                "--evaluation", "metric", "--selection", "selection_rule", "--context", "readout_window",
                "--split-sources", "train_raw", "test_raw"]
        if extra_sources:
            args += ["--split-sources", *extra_sources]
        if grouped:
            args += ["--split-by", "fold"]
        if required:
            args += ["--require-disjoint-sources"]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(args)
        return code, json.loads((err if code else out).getvalue())

    def test_within_split_source_reuse_across_units(self):
        _, report = self.compare_sources(required=True)
        self.assertEqual(report["common_count"], 3)
        self.assertEqual(report["changed"], ["sources"])
        self.assertEqual(report["split_sources"]["left"]["shared_splits"], 1)
        self.assertEqual(report["split_sources"]["right"]["status"], "disjoint_declared")
        self.assertEqual(report["control_disposition"], "only_target_differs_on_declared_controls")
        self.assertEqual(report["disposition"], "shared_train_test_sources")
        _, ungrouped = self.compare_sources(grouped=False)
        self.assertEqual(ungrouped["split_sources"]["right"]["status"], "shared")

    def test_source_reuse_across_roles_in_one_split(self):
        def extra_role(name, rows, header):
            header.extend(["train_calibration", "test_calibration"])
            for row in rows:
                row["train_calibration"] = '["cal-1"]'
                # Fold b2's measurement packet appears as a held-out calibration input.
                row["test_calibration"] = '["packet_04"]' if row["fold"] == "b2" else '["cal-2"]'
        _, report = self.compare_sources(extra_role, extra_sources=("train_calibration", "test_calibration"))
        self.assertEqual(report["split_sources"]["right"]["status"], "shared")
        self.assertEqual(report["split_sources"]["right"]["shared_splits"], 1)

    def test_undisclosed_and_partial_sources(self):
        _, legacy = self.run_case(self.left)
        self.assertNotIn("split_sources", legacy)
        _, absent = self.run_case(self.left, extra=("--require-disjoint-sources",))
        self.assertEqual(absent["split_sources"]["status"], "unknown")
        self.assertEqual(absent["disposition"], "insufficient_split_source_evidence")

        def incomplete(name, rows, header):
            for row in rows:
                row["train_raw"] = "[]"
        _, empty = self.compare_sources(incomplete, required=True)
        self.assertEqual(empty["disposition"], "insufficient_split_source_evidence")
        self.assertIn("sources", empty["unknown_or_incomplete"])

        def mixed(name, rows, header):
            next(row for row in rows if row["fold"] == "b2")["train_raw"] = "unknown"
        _, partial = self.compare_sources(mixed, required=True)
        self.assertEqual(partial["split_sources"]["right"]["status"], "unknown")
        self.assertEqual(partial["split_sources"]["left"]["status"], "shared")
        self.assertEqual(partial["split_sources"]["left"]["incomplete_splits"], 1)

    def test_source_sets_are_order_invariant_but_label_and_context_changes_surface(self):
        def change(name, rows, header):
            for row in rows:
                row["train_raw"] = '["a", "b"]' if name == "a" else '["b","a","a"]'
                if name == "b":
                    row["target_definition"] = "drift-v2"
        _, label = self.compare_sources(change, vary="label", required=True)
        self.assertEqual(label["changed"], ["label"])
        self.assertEqual(label["disposition"], "only_target_differs_on_declared_controls")
        self.assertEqual(label["split_sources"]["status"], "disjoint_declared")

        def coupled(name, rows, header):
            change(name, rows, header)
            if name == "b":
                rows.pop()
                for row in rows:
                    row["readout_window"] = "frame-B"
        _, multiple = self.compare_sources(coupled, vary="context")
        self.assertEqual(set(multiple["changed"]), {"members", "label", "context"})
        self.assertEqual(multiple["disposition"], "other_declared_controls_differ")

    def test_padded_source_ids_or_split_keys_cannot_certify_disjointness(self):
        for padded in ['[" packet_02 "]', '[" unknown "]']:
            with self.subTest(source=padded):
                def padded_id(name, rows, header):
                    rows[0]["train_raw"] = padded
                self.assertEqual(self.compare_sources(padded_id, required=True)[0], 2)

        def padded_fold(name, rows, header):
            next(row for row in rows if row["fold"] == "b1")["fold"] = " b1 "
        self.assertEqual(self.compare_sources(padded_fold, required=True)[0], 2)

    def test_invalid_source_list_or_group_rejected(self):
        for value in ['"packet"', '[""]', '["unknown"]', '[1]', 'broken']:
            with self.subTest(value=value):
                def invalid(name, rows, header):
                    rows[0]["train_raw"] = value
                self.assertEqual(self.compare_sources(invalid)[0], 2)
        self.assertEqual(self.run_case(self.left, extra=("--split-by", "id"))[0], 2)

    def test_ambiguous_keys_and_malformed_csv_refused(self):
        for rows in [self.left + self.left, ",1,short,dev,4,P\n", "a,1\n"]:
            self.assertEqual(self.run_case(rows)[0], 2)
        self.assertEqual(self.run_case(self.left, header=self.header.replace("id", "other"))[0], 2)
