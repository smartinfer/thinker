# Qualification implementation results — 2026-10-07

## Delivered

The existing ThinkerQL → resolver → adapter → execution-record path now enforces application qualification. No replacement inference architecture was introduced. A thin adapter reuses the native inference functions already present in Thinker.

Qualification binds contract version/content × provider × exact model × registry/adapter/configuration × implementation/runtime. The resolver admits ordinary execution only against the current matching PASS and verifies its evidence. A new failed or review-required qualification supersedes applicability of an older PASS without overwriting it.

Each decision is an immutable content-addressed JSON artifact. Its exact live-probe execution references identify content-addressed manifests, which in turn identify raw-byte payload blobs. Publication is atomic and exclusive; live decision history is append-only and linked through `previous_record`. Offline replay cannot mint or supersede a live PASS.

The first application profile lives in `gandiva-eval`, exports the unchanged GE2-A3P public contract for OpenAI and Anthropic, and invokes its own canonical parser, semantic validator and numerical/provenance requirements externally. Thinker does not import Gandiva internals or contain financial/PIT/evidence semantics. REVIEW remains explicit and does not authorize ordinary execution.

## Changed files in this task

Thinker new files:

- `thinker-core/thinker/qualification.py`
- `thinker-core/thinker/qualification_store.py`
- `thinker-core/thinker/qualification_external.py`
- `thinker-core/thinker/adapters/planned.py`
- `thinker-core/thinker/tests/test_qualification.py`
- `docs/QUALIFICATION.md`
- `docs/QUALIFICATION_INTEGRITY_2026-10-07.json`
- this report.

Existing Thinker integration points: `core.py`, `registry/resolver.py`, `adapters/base.py`, `cli.py`, and `model_turn/runtime.py`. The current Model-Turn and inference guides link to the new qualification authority. Earlier uncommitted inference-architecture changes were preserved.

Gandiva-eval additive files: `src/gandiva_eval/qualification.py`, `tests/test_qualification_profile.py`, `docs/QUALIFICATION_GANDIVA.md`. Existing Gemini additions, frozen protocol code, historical prompts, cases, gold, scores and model outputs were not changed.

Four new Thinker source modules: 811 physical lines including comments/whitespace at verification; task-level scenario tests: 401 lines. No new runtime dependencies. The application bridge and its tests are separate from Thinker.

## Verification

Function prototypes and task-level scenarios were created first. The initial six scenarios failed against the prototypes. Implementation was then completed and coverage extended to ten scenarios, including immutable PASS→FAIL history, exact probe links, configuration drift, external-validator failures/timeouts, CLI replay, and blocking the standalone default runtime bypass.

Thinker full regression suite, from `thinker-core`:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest thinker/tests -p no:cacheprovider --tb=short
```

**224 passed, 2 skipped in 7.83s.** The skipped tests are opt-in real-model tests. Provider transports were mocked; loopback service tests ran locally.

```sh
.venv/bin/python -m ruff check thinker/qualification*.py thinker/adapters/planned.py thinker/tests/test_qualification.py --no-cache --output-format concise
```

**All checks passed.** `git diff --check` passed in both repositories.

Gandiva-eval application bridge, from its repository:

```sh
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 /tmp/ge2a3p-venv/bin/python -m unittest discover -s tests -p 'test_qualification_profile.py' -v
```

**2 tests passed in 0.970s**, exercising all six frozen cases and both provider projections.

Frozen semantic protocol regression:

```sh
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 /tmp/ge2a3p-venv/bin/python -m unittest discover -s tests -p 'test_semantic_v3.py' -v
```

**78 tests passed in 2.069s.**

A broad initial Gandiva-eval test-discovery command mistakenly entered archived solver tests. It was interrupted (exit 130), and no full-suite pass is claimed. No SWI-Prolog process remained afterward. Subsequent verification used only the qualification and semantic-protocol suites. No new compiler/solver implementation or integration was added. No live provider/model requests were made.

## End-to-end external-validator replay

Using real exported profiles and the actual application subprocess, but deliberately incomplete structural fixtures:

| Profile projection | Offline slots | Application FAIL | Application REVIEW | Live admission |
|---|---:|---:|---:|---|
| OpenAI | 30 | 20 | 10 | Blocked |
| Anthropic | 30 | 20 | 10 | Blocked |

These are fixture outcomes, not model-performance results. They demonstrate preservation of semantic failure/review and lack of a live grant. Both immutable replay decisions have status FAIL; no response was repaired.

The local replay evidence is under `/tmp/thinker-qualification/gandiva-replay`:

- OpenAI record: `d8a8d01395f0909a51c02422deac7d5ed3bac4512759318a6fa1ea773fb3783f`.
- Anthropic record: `ac29a5088e1ac7ba701e5c13c1def1fe1955e513ca1936b4b91ab89c2ba902c1`.

These replay artifacts bind the code/configuration used for that check and are never transferable to a live target. Later source changes create a different qualification identity.

## Integrity

[Integrity manifest](QUALIFICATION_INTEGRITY_2026-10-07.json) verifies every pre-existing tracked/untracked source entry captured at task start: Gandiva 148/148, Spartacus 123/123, Gandiva-eval 286/286, and frozen GE2-A3P 80/80 unchanged.

The existing retrospective integrity checker also passed all archived groups: GE0-B 1,554; GE0-C 773; GE1-A files 21 and artifacts 55; GE1-B 1,377; GE2-A 393; GE2-A2 artifacts 423, implementation 16, archive 1, initial evaluation 133; A2R start 579. Groups overlap and are not a unique-file total.

## Operational boundary

No real provider/model is qualified yet. Ordinary model calls fail closed; separately authorized qualification probes are the bootstrap exception. Default standalone Model-Turn live calls are blocked so they cannot bypass the ThinkerQL gate. Explicit host-injected transports remain trusted offline test seams, not qualification authorities.

The initial profile qualifies only the six frozen evaluation requests. It does not authorize arbitrary production Gandiva investigations. Application REVIEW may be common under the frozen semantic validator; this implementation does not relax it or invent a human-adjudication policy. Provider-side drift, human review, broader production profiles and actual live qualification remain separate work. Restart deployments after source/dependency changes; source hashing is not runtime-bytecode attestation.
