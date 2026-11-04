# Research-support examples

The three commands below use fictional inputs to show file handoff, a small diagnostic matrix, and evidence indexing. Other subdirectories describe their own inputs, including a real UCI archive audit and source-based worked examples. Run these commands from the `core/` directory and choose a new path under `.scratch/` for each output.

```text
python -B -m scripts.research_support.handoff --root . --manifest examples/research-support/handoff.json --actor demo-producer --phase producer --out .scratch/research-support/producer.json
python -B -m scripts.research_support.diagnostics --root . --plan examples/research-support/diagnostic-plan.json --matrix examples/research-support/diagnostic-matrix.tsv --out .scratch/research-support/diagnostic.json
python -B -m scripts.research_support.evidence --root . --manifest examples/research-support/evidence.json --out-dir .scratch/research-support/evidence
```

In the diagnostic example, the base residual reports `u2` and `u3`; `u3` has no compatible candidate, while `u2` has a local alternative. No candidate satisfies every condition. The base and alias vectors match in this fixture; this observation is specific to the supplied example.

The handoff example has no environment probe. For a real preflight, provide the interpreter path, a working directory under the project root, required imports, and a finite timeout. After the producer finishes, a receiver should run the receiver phase independently.

To connect a literature synthesis with direction choices and later work, use the [research-cycle records](research-cycle/README.md). Their discussion template starts with a pending choice; an actual conversation supplies the decision and constraints.

For a research case with a real producer and consumer, see the [artifact handoff guide](../../docs/methods/guides/artifact-producer-consumer.md) and [public case examples](../../../examples/).
