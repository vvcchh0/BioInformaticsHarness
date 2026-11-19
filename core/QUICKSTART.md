# Quickstart

For literature synthesis or theoretical comparison, start with your question and existing study notes, then choose the relevant [method guide](docs/methods/README.md). This page provides optional tool examples; the maintenance tests at the end serve repository development.

When you select a tool, run its commands from the core directory with Python 3.11 or newer and follow that tool's input contract. The three examples use fictional inputs and the Python standard library. Each command writes to a new directory under your own `.scratch/` folder.

## Prepare a question or a review

Use the [preparation guide](docs/methods/guides/research-preparation.md) to choose a question-led or review-first route. Keep the question, sources, choices and next action in existing study records, or copy the [starter records](examples/research-support/preparation/README.md) when choosing that structured route. A review may be delivered before selecting a data or model experiment.

To plan acquisition for a selected paper:

```text
python -B scripts/acquisition/route_sources.py --doi 10.1371/journal.pone.0310814 --out .scratch/preparation/doi-plan.json
```

The plan command stays offline. Follow the [acquisition guide](docs/methods/source-acquisition-routing.md) to install optional `requests` and explicitly download selected sources.

For a request to develop a method from an earlier research problem, start with [the design choice to test](docs/methods/guides/research-guidance-cycle.md#从已有问题重新开发方法). For a reproduction, start with the authors’ inputs, implementation and numerical reference.

## Move from a review to a study

Use the [research guidance cycle](docs/methods/guides/research-guidance-cycle.md) to explain the biological problem and technical routes, compare feasible directions, and record the actual choice. The [discussion templates](examples/research-support/research-cycle/README.md) connect that choice to the study bridge and later evaluation. Continue with the data and first-plan steps when a study direction has been selected.

## Run three small tools

```text
python -B -m scripts.research_support.handoff --root . --manifest examples/research-support/handoff.json --actor demo-producer --phase producer --out .scratch/research-support/producer.json
python -B -m scripts.research_support.diagnostics --root . --plan examples/research-support/diagnostic-plan.json --matrix examples/research-support/diagnostic-matrix.tsv --out .scratch/research-support/diagnostic.json
python -B -m scripts.research_support.evidence --root . --manifest examples/research-support/evidence.json --out-dir .scratch/research-support/evidence
```

The handoff command records the producer's observation. The diagnostic example reports the `u2` and `u3` residuals, local candidate matches, and the absence of one candidate that satisfies every condition. The evidence command builds an index from the files named in its manifest.

## Compare controls across two runs

From the core directory, compare two synthetic exports. The example asks whether the score difference could be attributed to the input window.

```sh
python -B -m scripts.research_support.compare_controls examples/research-support/paired-controls/short.csv examples/research-support/paired-controls/long.csv --root . --key transcript --key-kind entity --vary input --label readout --input input_view --selection checkpoint_rule --selection candidate_budget --evaluation metric --fixed weights
```

Two transcript IDs are shared and one is unique to each export. On the shared panel the labels match, while the recorded input and selection protocol both change. The report suggests checks to consider before further computation. It does not compute a score or inspect a checkpoint. Use verified entity IDs (repeat `--key` for a composite key), or declare `--key-kind position` for row offsets. Role options name literal exported columns and can be repeated to cover other controls, such as weights, label units, pool identity, selection split or scoring policy. In the example both exports name the same weights. Use `--fixed` for conditions outside the intended contrast. For a rule-only comparison fix the candidate pool and budget separately. An entity key describes observed export coverage; reconcile exclusions and failed predictions against the intended panels. Single-factor attribution still depends on controls not visible in these exports. Missing evidence columns, blank values and the literal `unknown` are unassessed; use `--missing-value` for other markers. Align equivalent encodings using documented transformations before comparing. Shared rows need separate checks for consistent measurement and independent evaluation. Use `--vary system` to describe both systems with their observed conditions. A zero exit code means the diagnostic completed; interpret the differences, incomplete fields and scope in its JSON output.

To compare pseudonymized exports, assign an opaque ID once per observation unit and reuse the same study mapping on later runs. This example has the same collection of readout values in both files. Pairing by namespace and unit reveals which observations changed and which units occur in only one export.

```sh
python -B -m scripts.research_support.compare_controls units-before.csv units-after.csv --root examples/research-support/paired-controls --key unit_namespace --key unit_id --key-kind entity --vary members --label readout --input input_view --selection selection_rule --evaluation metric
```

See the [observation unit guide](docs/methods/guides/statistical-units-and-selection.md#跨运行配对同一观察单位) for repeat measurements and reports that lack a stable identifier.

If a comparison claims to hold out a measurement, trace both sides to the raw inputs used in each fold. In this calibration example, three units share the same targets and context across runs; one fold in the first run reuses a calibration input across different units.

```sh
python -B -m scripts.research_support.compare_controls calibration-shared.csv calibration-separate.csv --root examples/research-support/paired-controls --key scope --key fold --key unit --key-kind entity --vary sources --context readout_window --label target --label target_definition --label target_source --input input_view --selection selection_rule --evaluation metric --split-by fold --split-sources train_raw test_raw --require-disjoint-sources
```

Read `split_sources` per side and fold. Each source cell is a JSON array of study-scoped IDs. Follow derived artifacts back to the raw measurements; repeat `--split-sources` for additional roles. Where biological origin matters, include the shared parent pool or donor as a source as well. Distinct library IDs can come from the same pool. A blank or empty list leaves coverage unknown. Include fold identity in the key when supplying `--split-by`; without it each file represents one split. The report distinguishes a single changed axis from the declared requirement for disjoint sources. A completed audit can still find overlap. [Worked reasoning about shared inputs](docs/methods/guides/function-and-utility-validation.md#留出评价共享了哪些输入)

## Receive and rerun a handoff

After the producer finishes, the receiver runs the same manifest independently:

```text
python -B -m scripts.research_support.handoff --root . --manifest examples/research-support/handoff.json --actor demo-receiver --phase receiver --out .scratch/research-support/receiver.json
```

The producer and receiver outputs record separate observations. When a task needs an environment check, provide the package-relative working directory, interpreter, and a finite timeout in the handoff's `environment` section. The tools do not install packages or retry a failed run automatically.

## Add plan sealing or a first audit

- [Plan seal example](examples/research-support/plan-seal/README.md): create a READY plan in a dedicated plan root, then check and seal it using the same root. Put revisions in a new directory.
- [First audit example](examples/research-support/first-audit/README.md): list the code, configuration, and input before starting the child process. The runner uses `psutil` only when process-tree resource sampling is requested.

## Maintain or develop the core

Run these regression tests when maintaining the core or checking a relevant implementation change. Research use starts from its question and selected methods; these tests are a separate maintenance activity.

The test commands below use Python's standard library. For the test session, set `$env:PYTHONDONTWRITEBYTECODE = "1"` in PowerShell or `export PYTHONDONTWRITEBYTECODE=1` on POSIX. This also configures the Python processes started by the tests. Run each test directory explicitly:

```text
python -B -m unittest discover -s tests/research_support -p 'test_*.py' -v
python -B -m unittest discover -s tests/verification -p 'test_*.py' -v
python -B -m unittest discover -s tests -p 'test_*.py' -v
```

The last command also discovers tests for the newer plan-seal and capture tools. With optional `requests` installed, run the acquisition tests separately:

```text
python -B -m unittest discover -s tests/acquisition -p 'test_*.py' -v
```

These tests use offline responses and check plan creation from a relocated core directory.

## Try the saved-state examples

The public repository contains separate [Splice](../examples/splice/README.md) and [Yeast](../examples/yeast/README.md) examples. They install their own dependencies and read a saved state to produce predictions for the listed inputs. Their READMEs give the commands, data source, observed results, and license details.
