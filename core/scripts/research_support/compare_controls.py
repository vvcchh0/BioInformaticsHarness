"""Compare declared controls across two saved CSV exports, without rerunning a model."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.research_support._common import DEFAULT_FINGERPRINT_BYTES, SupportError, _read_bounded, resolve_path
else:
    from ._common import DEFAULT_FINGERPRINT_BYTES, SupportError, _read_bounded, resolve_path


AXES = ("label", "input", "selection", "evaluation", "fixed", "context")
EXAMPLES = 5


def read_panel(root, filename, key, columns, missing_values):
    data = _read_bounded(resolve_path(root, filename), DEFAULT_FINGERPRINT_BYTES)
    rows = csv.reader(io.StringIO(data.decode("utf-8-sig"), newline=""), strict=True)
    header = next(rows, [])
    if not header or len(set(header)) != len(header) or any(not h.strip() for h in header):
        raise ValueError(f"{filename}: expected distinct, nonempty CSV headers")
    if not set(key).issubset(header):
        raise ValueError(f"{filename}: missing pairing key")
    positions = {name: index for index, name in enumerate(header)}
    keys = [positions[name] for name in key]
    selected = {name: positions[name] for name in columns if name in positions}
    panel = {}
    for row in rows:
        if len(row) != len(header):
            raise ValueError(f"{filename}: wrong field count at CSV line {rows.line_num}")
        identity = tuple(row[i] for i in keys)
        if any(not part.strip() or part in missing_values for part in identity) or identity in panel:
            raise ValueError(f"{filename}: empty or duplicate pairing key at CSV line {rows.line_num}")
        panel[identity] = {name: row[index] for name, index in selected.items()}
    return panel, {"file": filename, "sha256": hashlib.sha256(data).hexdigest(), "rows": len(panel)}


def compare_axis(common, left, right, fields, missing_values):
    changed = incomplete = 0
    examples = []
    by_column = {name: {"changed_pairs": 0, "incomplete_pairs": 0} for name in fields}
    for key in common:
        mismatch = False
        missing = not fields
        for name in fields:
            a, b = left[key].get(name, ""), right[key].get(name, "")
            if not a.strip() or not b.strip() or a in missing_values or b in missing_values:
                missing = True
                by_column[name]["incomplete_pairs"] += 1
            elif a != b:
                mismatch = True
                by_column[name]["changed_pairs"] += 1
        incomplete += missing
        if mismatch:
            changed += 1
            if len(examples) < EXAMPLES:
                examples.append(key)
    status = "different" if changed else "unknown" if incomplete or not common else "same"
    return {"status": status, "columns": by_column, "changed_pairs": changed,
            "incomplete_pairs": incomplete, "changed_examples": examples}


def split_source_inventory(panel, key, split_by, source_pairs, missing_values):
    """Compare declared leaf IDs within each split, pooling all rows and roles.

    Canonicalize each source field in place for the subsequent paired-axis
    comparison. Unreported lists stay unknown, including explicit empty lists.
    """
    positions = [key.index(column) for column in split_by]
    splits = {}
    for identity, row in panel.items():
        group = tuple(identity[index] for index in positions)
        if any(value != value.strip() for value in group):
            raise ValueError("split-by key values must not have surrounding whitespace")
        train, test, missing = splits.setdefault(group, (set(), set(), set()))
        for train_col, test_col in source_pairs:
            for column, collector in ((train_col, train), (test_col, test)):
                raw = row.get(column, "")
                if not raw.strip() or raw in missing_values:
                    missing.add(column)
                    continue
                values = json.loads(raw)
                if not isinstance(values, list) or any(
                    not isinstance(v, str) or not v or v != v.strip() or v in missing_values
                    for v in values
                ):
                    raise ValueError(f"{column}: expected a JSON array of nonempty, unpadded raw source IDs")
                if not values:
                    missing.add(column)
                    row[column] = ""
                    continue
                unique = set(values)
                collector.update(unique)
                row[column] = json.dumps(sorted(unique), ensure_ascii=True)

    shared = disjoint = incomplete = 0
    for train, test, missing in splits.values():
        incomplete += bool(missing)
        if train & test:
            shared += 1
        elif not missing:
            disjoint += 1
    if shared:
        status = "shared"
    elif splits and disjoint == len(splits):
        status = "disjoint_declared"
    else:
        status = "unknown"
    return {"status": status, "split_count": len(splits), "shared_splits": shared,
            "disjoint_declared_splits": disjoint, "incomplete_splits": incomplete}


def compare(root, left_file, right_file, key, key_kind, vary, fields, missing_values,
            source_pairs=(), split_by=(), require_disjoint_sources=False):
    fields = {axis: fields.get(axis, []) for axis in AXES}
    if split_by and (not source_pairs or not set(split_by).issubset(key)):
        raise ValueError("split-by must name pairing-key columns and use split-sources")
    if len(set(split_by)) != len(split_by):
        raise ValueError("duplicate split-by column")
    if source_pairs or vary == "sources":
        fields["sources"] = [name for pair in source_pairs for name in pair]
    names = [name for columns in fields.values() for name in columns]
    if len(set(key + names)) != len(key + names):
        raise ValueError("pairing keys and evidence columns must have distinct roles")
    left, left_id = read_panel(root, left_file, key, names, missing_values)
    right, right_id = read_panel(root, right_file, key, names, missing_values)
    source_report = None
    if source_pairs or require_disjoint_sources or vary == "sources":
        side_states = {
            side: split_source_inventory(panel, key, split_by, source_pairs, missing_values)
            if source_pairs else {"status": "unknown"}
            for side, panel in (("left", left), ("right", right))
        }
        statuses = {side["status"] for side in side_states.values()}
        restriction = ("shared" if "shared" in statuses else
                       "unknown" if "unknown" in statuses else "disjoint_declared")
        source_report = {**side_states, "status": restriction, "split_by": list(split_by),
                         "columns": [list(pair) for pair in source_pairs],
                         "disjoint_required": require_disjoint_sources,
                         "scope": "declared raw source IDs within each split, across all rows and roles"}
    common = sorted(left.keys() & right.keys())
    left_only = sorted(left.keys() - right.keys())
    right_only = sorted(right.keys() - left.keys())
    membership = "unknown"
    if key_kind == "entity" and (left or right):
        membership = "different" if left_only or right_only else "same"
    axes = {"members": {"status": membership, "scope": "observed CSV keys; intended cohort and failed rows are not verified"}}
    axes.update({axis: compare_axis(common, left, right, fields[axis], missing_values)
                 for axis in fields if axis not in ("fixed", "context") or fields[axis] or axis == vary})
    changed = [axis for axis, info in axes.items() if info["status"] == "different"]
    unknown = [axis for axis, info in axes.items()
               if info["status"] == "unknown" or info.get("incomplete_pairs", 0)]
    if vary == "system":
        disposition = "describe_observed_systems"
    elif any(axis != vary for axis in changed):
        disposition = "other_declared_controls_differ"
    elif unknown or not common:
        disposition = "insufficient_pairing_or_controls"
    elif vary not in changed:
        disposition = "no_observed_target_contrast"
    elif vary == "members":
        disposition = "rescore_on_common_panel_before_interpreting_membership"
    else:
        disposition = "only_target_differs_on_declared_controls"
    control_disposition = disposition
    if require_disjoint_sources:
        if source_report["status"] == "shared":
            disposition = "shared_train_test_sources"
        elif source_report["status"] == "unknown":
            disposition = "insufficient_split_source_evidence"
    next_checks = []
    if source_report and source_report["status"] != "disjoint_declared":
        next_checks.append("trace_training_and_test_inputs_to_original_measurements")
    if not common or key_kind == "position":
        next_checks.append("establish_shared_entity_keys_and_measurement_units")
    if axes["label"]["status"] != "same":
        next_checks.append("reconcile_label_source_units_and_transforms")
    if left_only or right_only:
        next_checks.append("account_for_export_coverage_exclusions_and_failures")
        if common and key_kind == "entity" and axes["label"]["status"] == "same":
            next_checks.append("rescore_existing_predictions_on_shared_entities_if_measurement_units_align")
    if vary != "system":
        if any(axis in unknown for axis in ("input", "selection", "evaluation")):
            next_checks.append("recover_missing_observed_input_and_protocol_records")
        if "fixed" in unknown or "fixed" in changed:
            next_checks.append("verify_and_match_declared_fixed_conditions")
        if "selection" in changed or "evaluation" in changed:
            next_checks.append("align_candidate_pool_dev_selection_and_held_out_scoring_if_available")
        if vary == "input" and disposition != "only_target_differs_on_declared_controls":
            next_checks.append("consider_paired_input_inference_with_fixed_weights_after_alignment")
    report = {
        "next_checks": next_checks,
        "schema_version": "paired-control-diagnostic-v1",
        "evidence_scope": "literal supplied CSV fields; field comparisons use shared keys only",
        "scientific_acceptance": "not_assessed", "vary": vary, "key": key, "key_kind": key_kind,
        "left": left_id, "right": right_id, "common_count": len(common),
        "left_only_count": len(left_only), "right_only_count": len(right_only),
        "left_only_examples": left_only[:EXAMPLES], "right_only_examples": right_only[:EXAMPLES],
        "axes": axes, "changed": changed, "unknown_or_incomplete": unknown, "disposition": disposition,
    }
    if source_report:
        report["split_sources"] = source_report
        report["control_disposition"] = control_disposition
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("left")
    parser.add_argument("right")
    parser.add_argument("--root", type=Path, required=True, help="root containing both exports")
    parser.add_argument("--key", action="append", required=True, help="pairing column; repeat for composite key")
    parser.add_argument("--key-kind", choices=("entity", "position"), required=True)
    parser.add_argument("--vary", choices=("system", "members", "label", "input", "selection", "evaluation", "context", "sources"), required=True)
    parser.add_argument("--missing-value", action="append", default=["unknown"], metavar="TEXT",
                        help="additional literal missing marker; blank fields and unknown are always missing")
    parser.add_argument("--split-sources", action="append", default=[], nargs=2,
                        metavar=("TRAIN_COLUMN", "TEST_COLUMN"),
                        help="paired CSV columns of JSON arrays of scoped raw source IDs; repeat for other roles")
    parser.add_argument("--split-by", action="append", default=[], metavar="KEY_COLUMN",
                        help="fold identifier from the pairing key; by default each file describes one split")
    parser.add_argument("--require-disjoint-sources", action="store_true",
                        help="qualify the disposition when train and test share sources or disclosure is incomplete")
    for axis in AXES:
        parser.add_argument("--" + axis, action="append", default=[], metavar="COLUMN",
                            help="observed field; repeat to compare several fields for this role")
    args = parser.parse_args(argv)
    try:
        result = compare(args.root, args.left, args.right, args.key, args.key_kind, args.vary,
                         {axis: getattr(args, axis) for axis in AXES}, set(args.missing_value),
                         args.split_sources, args.split_by, args.require_disjoint_sources)
    except (SupportError, ValueError, csv.Error, OSError) as exc:
        print(json.dumps({"error": str(exc), "scientific_acceptance": "not_assessed"}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
