# BioInformaticsHarness core

BioInformaticsHarness is a small research toolkit for bioinformatics model studies. It combines practical method guides with tools for recording inputs, handing work to another researcher, and checking selected research relationships. Choose the guide and tools that fit the question; a study does not need every record type.

## Start here

- For a new topic, use the [research preparation guide](docs/methods/guides/research-preparation.md). Choose a focused question or a [review-first route](docs/methods/guides/review-first-preparation.md) that develops the evidence and method landscape before deciding on a study.
- For an existing question, choose the relevant topic from the [method guide](docs/methods/README.md) and connect its conditions to the sources and actual work.
- When a tool serves the current task, read the [quickstart](QUICKSTART.md) and run its selected example from the core directory. The [acquisition guide](docs/methods/source-acquisition-routing.md) provides the entry for selected DOI, PMCID and source URLs.
- For project context, see the [public design and source index](../docs/REFERENCES-AND-DESIGN.md) and the [research history](../docs/history/EVOLUTION.zh.md).

Most core commands use Python 3.11 or newer and the standard library. The bounded runner uses the optional `psutil` package when you need process-tree CPU and memory observations. The acquisition tool uses optional `requests` for explicitly requested downloads; its default mode creates a plan with the standard library. Install dependencies in your chosen environment.

For a study that moves from a review to a new direction, follow the [research guidance cycle](docs/methods/guides/research-guidance-cycle.md): explain the evidence, discuss the choice with the researcher, connect the selected question to data and a plan, then revisit the choice after evaluation.

For a review that needs a wider methods map or a clear stopping decision, use the [coverage and closure guide](docs/methods/guides/review-coverage-and-closure.md) and its [small runnable example](examples/research-support/preparation/coverage-example/README.md). Begin with a question map, inspect remaining gaps, and choose bounded followup or a reasoned scope.

## Examples

The core includes small fictional inputs for handoff, diagnostics, evidence indexing, plan sealing, and a first audit. The public repository also includes separate [Splice](../examples/splice/README.md) and [Yeast](../examples/yeast/README.md) examples that consume saved model states. Each example lists its own data and package requirements.

## Design and contribution

For a followup study, use the [current-task guide](docs/methods/guides/task-continuity.md) to connect the active question, execution and delivery. When a missing resource affects a claim or evaluation unit, the [resource-gap guide](docs/methods/guides/resource-gap-loop.md) helps decide whether to acquire it, revise the plan or narrow the claim.

The method cards keep scientific choices tied to their conditions: study unit, split, measurement, information use, and the result that would change the conclusion. The design notes explain why those choices are part of the Harness. Start with the public index above, then follow the links to a specific design or method page.

To work on the core, run the tests described in [QUICKSTART](QUICKSTART.md), add or update the relevant method guidance, and include a small example when it clarifies a decision. See [LICENSING.md](LICENSING.md) before adding code, data, weights, or copied material.

## Evidence and license

[Evidence summary](EVIDENCE.zh.md) reports the completed Splice and Yeast studies and explains what the public examples contain. First-party code, machine record templates and fixtures use Apache-2.0; project documentation and narrative plan templates use CC BY 4.0. The [license guide](LICENSING.md) explains attribution and how to handle data, dependencies, and model states.
