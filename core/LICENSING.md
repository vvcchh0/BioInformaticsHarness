# Project license and use

BioInformaticsHarness 0.1.0-alpha.2 is a research prototype. This guide describes the material included in the current public release. Individual files and examples retain the licenses and attributions listed in their manifests.

## Project material

- **Core code, tests, machine record templates, and fictional fixtures:** Apache-2.0. See [LICENSE](LICENSE).
- **Project-authored documentation, diagrams, and narrative plan templates:** CC BY 4.0. See [LICENSES/CC-BY-4.0.txt](LICENSES/CC-BY-4.0.txt). Executable code examples in documentation may also be used under Apache-2.0.
- **Project-trained model states listed in the public case manifests:** Apache-2.0 covers the project's new parameters and packaging. Some states also contain source-data-derived features or training IDs; those fields retain their original data attribution and terms. A case manifest identifies mixed scopes where a file contains both kinds of material.

Attribute the project as “BioInformaticsHarness contributors” and retain relevant license, source, and change notices. The recommended citation is in [CITATION.cff](CITATION.cff). Project licenses do not change the terms for papers, external code, datasets, or model weights.

## Data and dependencies

The public Splice and Yeast cases use the UCI archives listed in their `CASE-MANIFEST.json` files. Their data and derived tables retain CC BY 4.0 attribution, DOI information, and transformation details. See the [public third-party inventory](THIRD_PARTY.md) and each case manifest for file-level terms.

Python, NumPy, SciPy, scikit-learn, joblib, and optional `psutil` and `requests` are installed separately by the user and retain their own licenses. Only load pickle or joblib files from a source you trust; these formats can execute code when loaded.

## Reusing material

Before redistributing a file, check its case manifest or license notice and retain the listed attribution and change information. If rights for an input or weight are unclear, leave it out of the release, link to its source, or replace it with an openly licensed alternative. The [publication handoff guide](docs/methods/guides/publication-handoff.md) gives a practical file-by-file review process.

## Retained archive notices

The retained NOTICE names `THIRD_PARTY_ARCHIVE.md` and `LICENSES/DejaVu-fonts.txt` in its earlier research-archive context. The current public payload does not ship the upstream archive code, SVG figures or font-outline material described there. Consult the [current third-party inventory](THIRD_PARTY.md) and case manifests for shipped files. NOTICE and LICENSE remain unchanged; later archive additions require their own source and rights review.
