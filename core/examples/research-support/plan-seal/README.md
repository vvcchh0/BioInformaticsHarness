<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# First-plan seal

From the public repository's `core/` directory, or the root of a standalone
extracted core release, start Python 3.11 or newer:

    python -B -X utf8

Paste this standard-library example into the Python prompt. It copies the
adjacent plan into a fresh directory under .scratch, validates the manifest,
seals the copy, and writes a receipt beside the scratch run. It leaves this
example file unchanged.

~~~python
from pathlib import Path
import json
import shutil
import uuid

from scripts.research_support._common import dump_new
from scripts.research_support.plan_seal import build_ready, seal_plan, validate_ready

repo_root = Path.cwd().resolve()
source_plan = (repo_root / "examples" / "research-support" / "plan-seal" / "PLAN.md").resolve(strict=True)
scratch_base = (repo_root / ".scratch").resolve()
scratch_base.mkdir(parents=True, exist_ok=True)
scratch_base.relative_to(repo_root)
run_root = (scratch_base / f"plan-seal-example-{uuid.uuid4().hex}").resolve()
run_root.relative_to(scratch_base)
plan_root = (run_root / "plan").resolve()
archive_root = (run_root / "archive" / "first-plan").resolve()
plan_root.relative_to(run_root)
archive_root.relative_to(run_root)

plan_root.mkdir(parents=True, exist_ok=False)
archive_root.parent.mkdir(parents=True, exist_ok=True)
shutil.copyfile(source_plan, plan_root / "PLAN.md")

ready = build_ready(plan_root, "example", protocol="standard")
dump_new(plan_root / "READY.json", ready)
validated_files = validate_ready(plan_root, ready, "standard")
sealed = seal_plan(plan_root, archive_root, "example", "standard")
ack = sealed["acknowledgement"]

receipt = {
    "schema": "research-support.plan-seal-example-receipt.v1",
    "status": sealed["status"],
    "run_root": run_root.relative_to(repo_root).as_posix(),
    "plan_root": plan_root.relative_to(repo_root).as_posix(),
    "archive_root": archive_root.relative_to(repo_root).as_posix(),
    "case_id": "example",
    "source_plan": {
        "path": source_plan.relative_to(repo_root).as_posix(),
        "bytes": source_plan.stat().st_size,
    },
    "ready_schema": ready["schema"],
    "validated_file_count": len(validated_files),
    "ack_schema": ack["schema"],
    "ack_status": ack["status"],
    "archived_file_count": len(sealed["files"]),
    "scientific_acceptance": "not_assessed",
}
receipt_path = run_root / "example-receipt.json"
receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(receipt, ensure_ascii=False, indent=2))
~~~

The tool writes READY.json and later ARCHIVED.json inside the plan root.
Manifest paths are sorted POSIX paths relative to the explicit plan root.
READY.json and ARCHIVED.json are excluded from the file list. The archive
contains READY.json and every file named by that list.

Choose the protocol explicitly. The manifest paths are interpreted relative to the
plan root, so run sealing from the same root used to create READY.json.

The acknowledgement lists the archived files and their sizes. Sealing records the selected plan files; review the study method separately. For a changed plan, create a new plan directory and seal it separately.

The package accepts up to 1,024 files, 64 MiB per file, and 256 MiB total. For
larger research datasets, store the data separately and seal a manifest of the
selected inputs and outputs.

The adjacent PLAN.md is a small illustrative input. The code above operates on
a copy so the source example remains unchanged.
