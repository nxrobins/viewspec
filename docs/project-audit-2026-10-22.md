# ViewSpec project audit

Date: 2026-10-22. Target: `nxrobins/viewspec` at `e2f366b` (`main`, version `0.3.0b12`). Audit branch: `claude/project-audit-uv9ohi`. This document reports findings only. It changes no code.

## 0. Summary

ViewSpec is a 74,500-line Python package with 1,205 tests, written in about three months. The engineering discipline is high for a beta: fail-closed validators with hard caps, a closed error registry enforced by tests, constant-time token comparison, loopback-only servers with strict origin checks, a fail-closed distribution gate, a security policy, browser proofs in three engines, and a determinism design that holds within one version. The quick start works and runs in 0.3 seconds.

Four findings need action now:

1. **The package cannot import with the protobuf versions it declares.** `protobuf>=5.0` is declared; the generated schema requires 6.31.1 or later. Verified by execution.
2. **All three npm lockfiles pin a vite version with a HIGH advisory**, and the CI dependency gate fails on it as of today. One lockfile ships inside the wheel. Verified by `npm audit`.
3. **Integer values render as floats in compiled output** (`100` becomes `100.0`; large ids lose precision). Verified by compiling a validated bundle.
4. **`viewspec check` accepts inline event handlers, `javascript:` links, and CSS-escaped `url(`**, although SECURITY.md says it rejects them. Verified with tampered artifacts.

The main structural risks are: no type checking despite shipping `py.typed` (850 pyright errors); six modules above 2,000 lines and three copies of the emitter semantics; a 262-name public API frozen by the compatibility policy; a test suite that needs undocumented setup; CI actions pinned by tag; no Python lockfile; a documentation site deploy that depends on PyPI state; and a `docs/` folder where internal planning notes outnumber user documentation.

### Findings by severity

| Severity | Count | Ids |
|---|---|---|
| Critical | 0 | |
| High | 4 | P-1, P-2, C-1, S-1 |
| Medium | 31 | P-3, P-4, P-5, C-2, C-3, C-4, C-5, C-6, S-2, S-3, S-4, Q-1 to Q-7, T-1, T-2, T-3, R-1 to R-4, R-10, D-2, D-3, D-4, H-1, H-2 |
| Low | 29 | P-6 to P-8, C-7 to C-9, S-5 to S-9, Q-8 to Q-12, T-4, T-5, R-5 to R-9, D-5 to D-7, H-3 to H-5 |

Section 11 gives a prioritised action plan. Section 12 lists what was verified by execution, what was verified by reading, and what was not verified.

## 1. Scope and method

This audit covers the `nxrobins/viewspec` repository at commit `e2f366b` (branch `main`, version `0.3.0b12`, 2026-10-14). It covers the Python package, the shipped Node template, the test suite, the CI workflows, the documentation, and the repository hygiene. It does not cover the hosted API at `api.viewspec.dev`, because that code is not in this repository.

Method:

- A clean install in a fresh virtual environment (Python 3.13.16, uv, Node 22.22.0).
- Lint, format check, byte-compile, pyright, mypy, and the full unit test suite. The suite ran twice: first on a fresh clone, then after the two `npm ci` dependency seeds that CI installs.
- A wheel and sdist build, followed by the repository's own `scripts/check_distribution.py`.
- `pip-audit` on the resolved Python dependencies and `npm audit` on the three lockfiles.
- Targeted static review of the compiler core, the HTTP and process-spawning surfaces, the HTML sanitizer, the path containment logic, the CI workflows, and all documentation. Three focused reviews ran in parallel; their claims were cross-checked against the code and, where cheap, reproduced.
- Read-only queries of the GitHub repository for tags, releases, open pull requests, and the last CI results on `main`.

Not run: the opt-in `e2e` tests, the Playwright browser suites under `tests/react-tailwind-host/`, and the weekly Pretext workflow. CI runs them. Where a finding comes from reading code and not from execution, the text says so.

Severity scale: **Critical** means exploitable or data-destroying. **High** means it breaks users or the release pipeline now. **Medium** means a concrete correctness, maintainability, or process risk. **Low** means hygiene. **Info** means an observation with no action required.

## 2. Measured baseline

| Measure | Value |
|---|---|
| Source | 74,501 lines in 113 modules under `src/viewspec/` |
| Tests | 39,179 lines, 99 test files, 1,205 test functions; 22 files use Hypothesis |
| CLI | 38 subcommands; 29 accept `--json` |
| Public API | 262 names in `viewspec.__all__`; 28 modules import at `import viewspec` |
| Error registry | 749 closed error codes, enforced two ways by `tests/test_error_code_registry.py` |
| `ruff check .` (default rules) | pass |
| `ruff format --check` | 169 of 240 files would change (85 of 112 in `src/`) |
| Lines over the configured 120-char limit | 1,197 in `src/`, `scripts/`, `examples/` (rule E501 is not enabled) |
| McCabe complexity | 70 functions above 15; 18 above 25; maximum 72 |
| pyright (`src/viewspec`) | 850 errors in 113 files |
| mypy (`--ignore-missing-imports`) | 781 errors in 59 files |
| pytest, fresh clone | 5 failed, 1,666 passed, 7 skipped, 8 deselected (`-m "not e2e"`), 79 s |
| pytest, after `npm ci` seeds | 2 failed, 1,675 passed, 1 skipped (the two need full git history and a Playwright Chromium) |
| Wheel / sdist | 763 KB / 707 KB; 130 files; 3.4 MB unpacked; `check_distribution.py` passes |
| `pip-audit` | no known vulnerabilities |
| `npm audit --audit-level=low` | 1 HIGH advisory (vite 8.0.16) in each of the 3 lockfiles |
| Git | 125 commits visible, 52 merges, 2026-07-07 to 2026-10-14; remote tags for 7 betas, GitHub Releases for 6 |
| Last CI on `main` | success, 2026-10-14 (run 398) |
| Repository | pack 8.8 MiB; `demos/` 11 MB on disk; 3.9 MB of launch media |
| Quick start | `viewspec prove --out .viewspec-proof` completes in 0.3 s; artifact and manifest are byte-identical across two runs |

## 3. Packaging and dependencies

**P-1 (High) The declared protobuf floor does not import.** `pyproject.toml` declares `protobuf>=5.0`. The shipped `src/viewspec/schema/viewspec_pb2.py` is generated code for protobuf 6.31.1 and calls `ValidateProtobufRuntimeVersion(PUBLIC, 6, 31, 1)`. Verified by execution: with `protobuf==5.29.6`, `import viewspec` raises `google.protobuf.runtime_version.VersionError`; with `protobuf==5.26.1` it raises `ImportError`. The import is eager (`__init__.py` → `types.py:27` → `schema/__init__.py`), so the whole package is unusable. CI never sees this because pip always resolves the newest protobuf. There is no upper bound either, and protobuf tolerates generated code only one major version older than the runtime, so an 8.x runtime will likely reject this file. Fix: declare `protobuf>=6.31.1,<8`, and add a CI leg that installs the exact floor and imports the package.

**P-2 (High) All three npm lockfiles pin a vite version with a HIGH advisory, and the CI gate fails on it.** `vite 8.0.16` is in the advisory range 8.0.0 to 8.1.5 (GHSA-9jrq-w75r-8gcw, GHSA-rq7h-c2jc-7f22, plus three `launch-editor` advisories). The `javascript_dependencies` job runs `npm audit --package-lock-only --audit-level=low` on these exact files, so the next CI run on `main` fails. One of the lockfiles ships inside the wheel (`viewspec/host_verify_template/package-lock.json`). Product exposure is limited: ViewSpec runs `vite build` and `vite preview --host 127.0.0.1` (`host_verify.py:834`), and the advisories target the dev server. Fix: bump vite to 8.1.6 or later in the three `package.json` files and lockfiles, rerun `tests/react_dependency_regressions.mjs`, and ship a patch release because the template is in the wheel.

**P-3 (Medium) The protobuf schema source is missing.** No `.proto` file exists in the repository. The generated file says `source: universal_ui.proto`, package `universal_ui.v1`, and was renamed after generation. Nobody can regenerate, diff, or produce a `.pyi` stub for the schema from this repository. Fix: commit `universal_ui.proto`, add a pinned regeneration script, and add a test that the committed generated code matches.

**P-4 (Medium) No Python lockfile or constraints file.** CI, the release gate, and the Studio launch check all resolve `protobuf`, `PyYAML`, `httpx`, `cryptography`, `pytest`, `hypothesis`, and `build` fresh on every run. A release can pass one day and fail the next with identical source. The Node side is pinned to the patch level; the Python side is not. Fix: add `uv.lock` or a `constraints-ci.txt` used through `PIP_CONSTRAINT`, and refresh it with Dependabot.

**P-5 (Medium) No Dependabot or Renovate configuration.** Every dependency is pinned exactly (hatchling, ruff, all npm packages, all action tags) and nothing proposes updates. Security floors surface only when `npm audit` breaks CI, as in P-2. Fix: add `.github/dependabot.yml` for `pip`, the three `npm` directories, and `github-actions`.

**P-6 (Low) Version ranges.** `cryptography>=50.0.1,<51` is a one-major window; cryptography ships about three majors a year, and the changelog shows the previous bound already blocked a fix once. `PyYAML`, `httpx`, and `mcp` have no upper bound; `mcp` changes its API often. Fix: widen cryptography to two majors and add an upper bound on `mcp`.

**P-7 (Low) The source-map floor is locked but not declared.** Commit `f1dcff7` raised `source-map-js` to 1.2.2 in the lockfiles only; no `package.json` has an `overrides` field. A future `npm install` can drop the floor silently. Two tests in `tests/test_react_dependency_seeds.py` mitigate this. Fix: add `"overrides": {"source-map-js": ">=1.2.2"}` to the three manifests.

**P-8 (Low) Local tool versions drift from the pins.** `ruff==0.15.22` is pinned, but nothing keeps a developer on it. Fix: add a `.pre-commit-config.yaml` or a `uv run` wrapper.

**P-9 (Info) Wheel contents are intentional and gated.** The wheel ships `host_verify_template/` (package.json, lockfile, Vite and Playwright configs, one spec file) because `verify-host` rebuilds artifacts in a bounded host app. `check_distribution.py` requires those files, caps size and member count, forbids `node_modules`, `demos`, `tests`, caches, symlinks, and traversal, and checks version parity. A build run while `node_modules/` existed on disk produced 130 files with no `node_modules` entry, so the exclusion works. A belt-and-braces `[tool.hatch.build] exclude` would still be cheap.

## 4. Correctness and determinism

**C-1 (High) Integer values render as floats and lose precision.** Every attribute, slot, and prop value passes through `google.protobuf.Struct` (`types.py:127-144`), whose numbers are doubles. `_text_from_value` (`compiler.py:157-160`) then renders non-strings with `json.dumps`. Reproduced with a bundle that `validate-intent` accepts: the value `100` renders as the visible text `100.0`, and `9007199254740993` renders as `9007199254740992.0`. The same semantic bundle therefore yields different bytes in memory and after a file round trip, and numeric ids above 2^53 are silently wrong. `tests/test_roundtrip.py` uses only string attributes, which is why this escaped. Fix: either reject non-string scalars in the intent contract, or keep the parsed JSON beside the proto and render integers as integers, rejecting magnitudes above 2^53 with a diagnostic.

**C-2 (Medium) Non-ASCII text in list or dict bindings renders as `\uXXXX` escapes.** `_text_from_value` uses `json.dumps` with the default `ensure_ascii=True`. A whole-slot binding of `["Müller", "Zoë"]` shows literal backslash escapes in the page. Fix: `ensure_ascii=False, separators=(",", ":")`; the writers already produce UTF-8.

**C-3 (Medium) `compile_auto` uploads to the hosted API on a local DESIGN.md error.** `compiler.py:2140` catches `(UnsupportedMotifError, DesignSystemError)` and calls `compile_remote`, sending the bundle and DESIGN.md. The README documents the fallback only for unsupported motifs. A typo in DESIGN.md therefore causes a network call that the user did not ask for. Fix: catch only `UnsupportedMotifError`, or gate the design fallback behind an explicit flag.

**C-4 (Medium) Output bytes depend on the unpinned protobuf runtime.** Because values round-trip through `Struct` and `json_format.MessageToDict`, number formatting is protobuf's, not ViewSpec's. A protobuf release that returns integral doubles as `int` would change artifact hashes. Fix: stop rendering text from the proto round trip (see C-1) or pin the protobuf major and golden-test the number path.

**C-5 (Medium) Seventeen canonical-JSON helpers with three canonical forms.** `ensure_ascii=True` in seven modules (for example `verification.py:39`, `local_tools_validators.py:620`), `ensure_ascii=False` in six (for example `state_ir.py:2661`, `intent_tools.py:1520`), and no `allow_nan=False` guard in `hosted_artifacts.py:22`, `hosted_receipts.py:20`, `hosted_app_bundles.py:29`. Each is self-consistent, so no hash is wrong today, but the same object has different digests in different modules, and the unguarded helpers would emit non-JSON `NaN` instead of failing. Fix: one `viewspec/_canonical.py` with `canonical_json_bytes()` and `canonical_sha256()`, pinned in `tests/test_hash_goldens.py`.

**C-6 (Medium) Cross-OS byte identity is claimed but untested.** Writers use `newline=""`, `.gitattributes` forces LF, and evidence paths are POSIX, so identity across Windows and Linux is likely. CI runs on Ubuntu only (3.11 to 3.14). `tests/test_hash_goldens.py` declines to pin artifact hashes because of CRLF concerns, which contradicts the writers. Fix: add a `windows-latest` job that compiles the conformance corpus and compares `artifact_hash` and `semantic_digest` to the Linux values; pin one golden hash per corpus case.

**C-7 (Low) Proof bundles are not portable.** Two identical `viewspec prove` runs produce identical `artifact/` trees and manifests, but `proof_report.json`, `PROOF.md`, and `support_bundle.json` differ: they embed absolute paths and wall-clock phase timings, and `support_bundle.json` hashes the report. Moving or re-running a proof changes `proof_report_hash`. Fix: write paths relative to the proof directory and keep timings out of the hashed document.

**C-8 (Low) Dead and unreachable code in the compiler.** `compiler.py:1288` (`UNKNOWN_REGION` diagnostic) cannot execute because `_validate_region_tree` raises first. `_derive_style_tokens` (`compiler.py:1905-1920`) computes `numeric_values`, `total_text_len`, and `attr_count` and never reads them. `recipes.py:781,787` defines a second `tailwind_recipe_registry_digest` that returns a different value from the consumed one in `react_tailwind_tsx/__init__.py:137`. `types.py` has unreferenced `parse_viewspec_ref`, `parse_target_ref`, `CONTAINER_PRIMITIVES`, `CONTENT_PRIMITIVES`.

**C-9 (Low) Small API inconsistencies.** `compile(bundle, design="DESIGN.md")` treats the string as DESIGN.md content, not a path (`compiler.py:1865-1878`). `load_design_system(lint=...)` ignores `lint` (`design_md.py:161,210`). Role matching is exact in `compiler.py:545` but tokenised in the workspace detector near `compiler.py:380`. Nine of 38 CLI subcommands (`compile`, `doctor`, `lift`, `init-*`, `export-agent-assets`, `mcp`) do not accept `--json` while the other 29 do.

**C-10 (Info) Determinism discipline is otherwise strong.** Sorted protobuf-map iteration with an explanatory comment, type-ranked sort keys that mirror the JavaScript reducer (`state_ir.py:2607`), JavaScript-compatible number formatting, atomic writers, no clock or UUID in any hashed artifact, and randomness confined to session tokens and disclosed in `docs/compatibility.md`.

## 5. Code quality and architecture

**Q-1 (Medium) Type annotations do not check, but the package ships `py.typed`.** pyright reports 850 errors and mypy 781 across the package, and no type checker runs in CI. The two largest causes are: the generated `viewspec_pb2.py` has no `.pyi` stub, so every proto attribute in `types.py` is unknown (65 pyright errors in that file alone); and Optional values are used without narrowing (`reportOptionalMemberAccess` 358, mypy `union-attr` 320). Worst files: `app_reports.py` 103, `local_tools_validators.py` 85, `presentation_plan.py` 68, `types.py` 65, `design_md.py` 43, `cli.py` 36. Three errors in `compiler.py` are genuine (`:481` operator on `int | str`, `:990` variable reuse with a different type). Downstream users who type-check against this package see the same noise. Fix: generate `viewspec_pb2.pyi` with `protoc --pyi_out` (needs P-3), add pyright in basic mode to CI with a baseline file, and burn the baseline down starting with the six files above.

**Q-2 (Medium) Module and function size.** Six modules exceed 2,000 lines (`presentation_plan.py` 2,708; `state_ir.py` 2,702; `intent_patch.py` 2,357; `converge_sessions.py` 2,281; `compiler.py` 2,142; `cli.py` 2,090) and `review_server.py` is 140 KB with the browser UI embedded as strings. `state_ir.py` mixes validation, a Python reducer interpreter, a JavaScript code generator, and a Node subprocess runner. `compiler.py` contains the hosted HTTP client (`:1962-2142`). 18 functions have McCabe complexity above 25; the worst are `_validate_intent_semantic_attrs` (72, `local_tools_validators.py:1866`), `_render_node` (60, `emitters/html_tailwind/__init__.py:400`), and `summarize_agent_eval_session` (40, `agent_eval.py:945`). Fix: split by concern, move the embedded UI into static asset files, move the hosted client beside `hosted_*.py`, and enforce a complexity budget with ruff `C901`.

**Q-3 (Medium) The three emitters and the artifact validator hand-copy the same logic.** The tag-selection ladder (about 30 branches) exists three times (`html_tailwind/__init__.py:510-560`, `react_tsx/__init__.py:261`, `local_tools_validators.py:700`) and the HTML copy has already drifted (an extra `detail`+`cluster` branch). The ARIA ladder (about 60 lines) exists three times and its defaults are restated in `motif_compilers.py:280` and `local_tools_validators.py`. The write-and-rollback block with the `.viewspec_write_failed.json` sentinel exists four times. `_walk`, parent maps, `_jsx_attr`, and about 80 lines of TSX scaffolding are duplicated between the two React emitters. Because `semantic_digest` is computed from the validator's copy of the ladder, any further drift silently invalidates digest checks. Fix: one `emitters/_semantics.py` consumed by all emitters and by the validator, and one `write_artifact_set()` helper.

**Q-4 (Medium) The Tailwind emitter mutates another module's globals on every call.** `_sync_recipe_module` (`emitters/react_tailwind_tsx/__init__.py:79-87`) assigns seven module attributes on `recipes.py` and wraps four of its functions, so that tests can monkeypatch the package-level names. This is thread-unsafe, makes the registry digest depend on call order, and leaves six same-named functions in two modules. Fix: pass the registry explicitly as a dataclass.

**Q-5 (Medium) Layering violations and private imports across modules.** The HTML emitter imports a constant from `viewspec.compiler`; core modules (`intent_patch.py`, `app_validation.py`, `converge_sessions.py`) import the agent and review layers; `intent_tools.py`, `app_pipeline.py`, `app_screens.py`, and `prove.py` import the MCP tool layer; `app_pipeline.py` imports 11 underscore-private names from eight sibling modules; `presentation_plan.py` imports `compiler._derive_style_tokens`; `app_bundle.py` re-exports 18 pass-through wrappers of `app_pipeline.py` and `app_tools.py`. Two circular dependencies are managed with lazy imports in `motif_plugins.py`. Fix: define module ownership for shared constants, promote the shared helpers to public names in a small internal module, and add an import-linter contract.

**Q-6 (Medium) The public API is very large for a beta.** `__all__` has 262 names (100 classes, 85 functions, 77 constants). `docs/compatibility.md` freezes every name as the contract from 1.0 onward, so internal constants such as `AGENT_PATCH_EXAMPLE_FILE` or `INTENT_PATCH_MAX_CONTEXT_REQUESTS` become permanent. `import viewspec` loads 28 modules including the hosted clients, convergence sessions, and proof tooling. Fix: tier the surface (`viewspec`, `viewspec.app`, `viewspec.converge`, `viewspec.hosted`) and lazy-load the heavy modules before 1.0.

**Q-7 (Medium) Lint and format policy do not match the configuration.** `line-length = 120` is set but E501 is not enabled, so 1,197 lines exceed it. `ruff format` is not used and not checked; 169 of 240 files would change. CI runs only the default rule set. A broader scan (B, UP, S, SIM, PL, PERF, RUF) reports mostly style noise plus a few real items: 10 `raise` inside `except` without `from`, 3 `subprocess.run` without `check`, and the duplicated `except Exception: pass` blocks below. Fix: either adopt `ruff format` with a one-time reformat commit and add `ruff format --check` to CI, or remove the unused line-length setting; enable `E,F,I,UP,B,SIM,C90` with a baseline.

**Q-8 (Low) Two incompatible error shapes.** Typed exceptions carry a `code` attribute (`IntentPatchError`, `ConvergeError`, `CompilerConstraintError`), but `compiler.py:429,464-491,728` and about 30 sites in `local_tools_validators.py` raise `RuntimeError` with the code embedded in the message. Callers must parse strings to recover the code. The registry itself is enforced two ways by tests, which is good. Fix: one `CompilerInternalError(code, message)` class.

**Q-9 (Low) No `logging` anywhere.** The package uses `print` (146 calls in `cli.py`, plus the daemons) and never imports `logging`. CLI output through `print` is fine; the review and Studio daemons have no log levels or handlers. Fix: library modules log through `logging` with a `NullHandler`; the CLI configures handlers.

**Q-10 (Low) Swallowed exceptions.** `except Exception: pass` at `app_reports.py:418`, `intent_patch.py:2313`, `studio_review_worker.py:65`, and an identical copy in each emitter (`html_tailwind:667`, `react_tailwind_tsx:578`, `react_tsx:621`). All are best-effort writes after a failure and none hide hash-relevant work, but each should at least record the secondary failure.

**Q-11 (Low) `assert` used for narrowing in request paths.** 52 asserts in library code, mostly `isinstance` narrowing. `studio_review_asgi.py:142` asserts `self.internal_auth is not None` before authentication; under `python -O` the assert vanishes and a `None` would still fail closed with `AttributeError`. Prefer explicit `if ... raise`.

**Q-12 (Low) Naming and export inconsistencies.** `sdk/__init__.py` exports four builders but not `ListBuilder`, `FormBuilder`, `DetailBuilder`, although the README documents `add_list`, `add_form`, `add_detail`. `MotifPlugin = MotifCompiler` gives one class two public names. `ensure_no_input_overwrite`, `_assert_under_proof_root`, and `_assert_safe_output` name the same concept three ways.

**Q-13 (Info) Clean signals.** No `TODO`, `FIXME`, or commented-out code. Two `type: ignore`, eight `noqa`. No mutable default arguments, no bare `except:`, no `open()` without an encoding, no `shell=True`, `eval`, `exec`, or `pickle`. YAML is parsed with a custom safe loader that rejects duplicate keys (`design_md.py:249`).

## 6. Tests

**T-1 (Medium) The default suite is not self-contained.** On a fresh clone, `pytest -m "not e2e"` fails five tests. Four in `tests/test_agent_eval_runner.py` need `npm ci` in `conformance/agent-ui-v2/react-dependencies` and `src/viewspec/host_verify_template`; one in `tests/test_bug_prevention_manifest.py:96` runs `git merge-base` and needs full history (CI uses `fetch-depth: 0`); after the seeds, one more needs a Playwright Chromium (`test_browser_scorer_records_runtime_screenshot_and_text_geometry_fault`). Fifteen other tests already skip cleanly when Node is missing. No `CONTRIBUTING.md` explains the prerequisites. Fix: skip with a reason when the seed directory, history, or browser is absent, or give these tests their own marker, and document the setup.

**T-2 (Medium) Determinism is tested as "same process, same result".** `tests/test_refinement_determinism.py` compiles the ten-case corpus twice and compares; `tests/test_hash_goldens.py` pins only five literals (the algorithm, the canonical form, two state hashes). No emitter `artifact_hash` or `semantic_digest` is golden-pinned, and `conformance/verification/corpus.json` contains no hashes. Cross-version or cross-OS drift of the kind C-1 and C-4 describe is invisible. Fix: one golden hash per corpus case per target, updated deliberately in release commits.

**T-3 (Medium) Generated front-door pages have no drift test.** `tests/test_demo_build_drift.py` regenerates and byte-compares nine demo pages, but not `demos/index.html` (204 KB from `build_landing.py`), `demos/landing-compiled/**` (nine 125 KB manifests), `demos/proof-explorer/**`, or `demos/cross-platform-dashboard/artifacts/**`. Those are only shape-checked. The landing page can drift from the compiler without a failing test. Fix: add the landing and proof-explorer builders to the drift test; label the cross-platform artifacts as hosted fixtures.

**T-4 (Low) Round-trip and property tests avoid numbers.** `tests/test_roundtrip.py` uses string attributes only, which is why C-1 escaped. Hypothesis is used in 22 files, but not for the compiler's binding and ordering invariants. Fix: property tests over scalar types (int, float, bool, null, nested) through JSON and proto round trips.

**T-5 (Low) Several scripts have no tests.** `scripts/check_studio_release.py`, `scripts/run_refinement_corrections.py`, and `scripts/run_verification_corpus.py` are not referenced by any test; `scripts/run_agent_ui_eval.py` is 3,581 lines in one file.

**T-6 (Info) Strengths.** 1,205 tests run in 79 s. `test_compiler.py` covers every motif and includes meta-tests on the compiler's own structure. `test_emitter_contract.py` pins role-class parity across the three targets, write-before-validate ordering, and tampered-manifest rejection. Socket-blocking tests back the "no network" claim for the raw-HTML and MCP paths. The error-code registry is enforced by an AST scan in both directions. Browser suites in CI cover Chromium, Firefox, and WebKit, and the release job installs the wheel in an isolated environment and launches Studio under Chromium.

## 7. CI and release process

**R-1 (Medium) Actions are pinned by tag, not by commit SHA.** `actions/checkout@v4`, `setup-python@v5`, `setup-node@v4`, `upload-artifact@v4` and `@v5`, `configure-pages@v5`, `upload-pages-artifact@v3`, `deploy-pages@v4`, and the third-party `oven-sh/setup-bun@v2`. A moved tag runs arbitrary code in the job that builds the release wheel. Fix: pin full SHAs with a version comment and let Dependabot bump them.

**R-2 (Medium) The documentation site deploy depends on PyPI state.** In `pages.yml`, `deploy` needs `published_studio`, which installs `viewspec` from PyPI and fails when the installed version differs from `pyproject.toml` (`scripts/check_studio_release.py:216-224`, no retry). Any push to `main` between a version bump and the manual upload skips the site deploy, so demo or documentation fixes cannot ship without a package release. Fix: keep the check as a visible non-blocking status, or run it only on dispatch and when `demos/public-facts.json` changes.

**R-3 (Medium) `npm audit --audit-level=low` is a hard gate on every run.** Any new low-severity advisory in any transitive dependency fails unrelated pull requests and needs registry access each run; P-2 is the live example. Fix: block on `high` and run the full audit on a schedule; keep the deterministic floors in `tests/test_react_dependency_seeds.py` as the pull-request gate.

**R-4 (Medium) The Pretext fidelity gate changes meaning silently when fonts fail to install.** `pretext-e2e.yml:127-130` installs `ttf-mscorefonts-installer` (SourceForge) and `fonts-inter` with `|| echo WARNING`; the font census is recorded but never asserted. A SourceForge outage runs the calibrated gate on DejaVu fallbacks. Fix: vendor the open fonts and assert the census, or fail when Georgia or Inter is missing.

**R-5 (Low) Brittle constants in `pretext-e2e.yml`.** The result contract hard-codes `{"tests": 7}` (`:157`), and the toolchain check asserts `npm --version == 10.9.8` (`:47`), which setup-node can change independently. Fix: assert `failures == errors == skipped == 0 and tests >= 7`; assert the npm major.minor.

**R-6 (Low) `ci.yml` and `pages.yml` have no `timeout-minutes`, `concurrency`, or caching**, while `pretext-e2e.yml` has all three. The six Node smoke tests run four times across the Python matrix. The `agents` extra is never installed, so the real `viewspec mcp` path runs only its missing-dependency branch. Two `npx --yes --package=@playwright/test@1.60.0` sites fetch from the registry at run time with a version pin but no lockfile integrity. Fix: copy the e2e job's settings; add `agents` to one matrix leg; use `npm ci --ignore-scripts` plus `node_modules/.bin/playwright`.

**R-7 (Low) Release checklist omits tagging and GitHub Releases.** The remote has tags for seven of twelve betas (`v0.3.0b3`, `b5`, `b7`, `b9` to `b12`) and Releases for six, so the practice exists but is not documented in `docs/release-checklist.md` (lines 44 to 57), and five betas were never tagged. The version is also duplicated in `pyproject.toml` and `_version.py`; `check_distribution.py` checks parity at release time but no unit test does. Fix: add the tag and Release step to the checklist; single-source the version with `[tool.hatch.version] path`.

**R-8 (Low) sdist omits `tests/`, `conformance/`, `CHANGELOG.md`, and `SECURITY.md`.** PyPI users are unaffected; downstream packagers cannot run the suite from the sdist. Fix: include `tests/` and `CHANGELOG.md`, and raise the allowlist and size caps in `check_distribution.py`.

**R-9 (Low) `LICENSE` says "nxrobins", `pyproject.toml` says "Nigel Robinson".** Use one legal name in both.

**R-10 (Medium) The host proof step installs dependencies cold under a 90 s budget, and it failed twice on a documentation-only change.** In the `react_tailwind_host` job, "React Tailwind host proof" runs the verifier with `--install`, so the verifier performs `npm ci --ignore-scripts` in a temporary copy of `host_verify_template` under its 90 s install budget (`host_verify.py:47,446-450`). The same job installs the identical seed later ("Install verification corpus host dependencies") and passes it through `VIEWSPEC_HOST_VERIFY_NODE_MODULES_DIR` only to the corpus step (`ci.yml:212-218`); when that variable is set the verifier links the seed instead of installing (`host_verify.py:436-441`). Observed on 2026-10-22: run 402 for this audit (one Markdown file changed) failed in that step on attempt 1 and on the re-run with the identical `HOST_VERIFY_TIMEOUT` while the npm registry was slow; the test matrix passed. Fix: move the seed install before the host proof step, set `VIEWSPEC_HOST_VERIFY_NODE_MODULES_DIR` for it, and enable `cache: npm` on `setup-node`.

**R-11 (Info) Strengths.** `permissions: contents: read` in all workflows; no `secrets.` reference anywhere, so fork pull requests carry only a read-only token; `persist-credentials: false` in the e2e job; the matrix matches the classifiers exactly; `--strict-markers`, `compileall`, and a whitespace-damage gate; a fail-closed distribution check; an isolated-venv wheel install plus Chromium Studio launch on 3.11 and 3.14 with retained evidence; patch-level pins for Python, Node, npm, Bun, and Playwright in the e2e job; the last run on `main` succeeded.

## 8. Documentation

**D-1 (Info) CLI and API documentation are accurate.** An AST comparison of every fenced `viewspec` invocation in `README.md`, `docs/getting-started.md`, `docs/agent-integration.md`, `integrations/claude-code/SKILL.md`, the other docs, and `demos/llms*.txt` (about 170 invocations) against `cli.py` finds every subcommand and flag. Every Python name the README imports is exported. No relative link in `README.md`, `docs/`, `SECURITY.md`, or `CHANGELOG.md` points to a missing file. Version literals agree across `pyproject.toml`, `_version.py`, `CHANGELOG.md`, `README.md`, `demos/public-facts.json`, `demos/llms.txt`, `demos/llms-full.txt`, and `demos/index.html`.

**D-2 (Medium) The README's "Core Workflow Evidence" is seven betas old and does not say so.** `README.md:182-196` cites `conformance/refinement/gate-status-v1.json` and `scorecard-v2.json`, which record `sdk_version: 0.3.0b5`, evaluated 2026-07-21. A reader assumes the evidence covers the advertised b12. Fix: state the evaluated version and date inline, or re-run the corpus per release.

**D-3 (Medium) `docs/known-limits-a11y.md:31` cites an error code that does not exist.** `A11Y_UNRESOLVABLE_BACKGROUND` is not in `error_codes.py`, which `docs/compatibility.md` describes as a closed registry. Fix: name the code that fires, and add a test that cross-checks backticked `*_ERROR` and `A11Y_*` tokens in docs against `ERROR_CODES`.

**D-4 (Medium) The `docs/` folder mixes user documentation with internal planning notes.** 17 of 29 documents have no inbound link and there is no index. `viewspec-studio-goal.md` (47 KB), `core-refinement-goal.md`, `studio-review-deployment.md`, `launch-demo-storyboard.md`, `launch-headliner-asset.md`, `mobile-recorder-handoff.md`, and `launch-browser-qa-report.md` are status, planning, or asset-production notes. `viewspec-review.md` (74 KB) still says "Status: Proposed, Date: 2026-07-14" although Review shipped in b5, and pins `0.3.0b5` in examples. `bug-prevention.md` says every regression through pull request 135 is catalogued, but the manifest stops at PR 134 and VSBUG-040 while pull requests run to #169. `app-bundle-v0.md` documents V1 to V4. Fix: add `docs/README.md` as an index; move the internal notes to `docs/internal/`; update the Review status header; either resume the bug-prevention process or mark the document historical.

**D-5 (Low) README details.** The Python snippets at `README.md:594` and `:606` use `builder` without constructing it. `README.md:569` names `HtmlTailwindEmitter` and friends as shipped classes, but they are not in `__all__`, which `docs/compatibility.md` defines as the only stable API. `README.md:624` implies the additive-only promise applies now, while `docs/compatibility.md:5-6` says it binds from 1.0. `README.md:234-240` says plugins extend the compiler "securely"; plugins are trusted in-process Python, so "securely" over-promises. The README is 41 KB and 627 lines; much of it reads as a status log for Studio rather than as a guide.

**D-6 (Low) `integrations/claude-code/SKILL.md` is a hand-maintained copy.** `viewspec init-agent --target claude-code` writes `CLAUDE.md` from `native_agents.py`; SKILL.md is a parallel copy with no front matter, checked only by string asserts. Fix: generate it from the same source or document that it is manual.

**D-7 (Low) Changelog details.** `## [0.2.0] - earlier release` has no date; `0.3.0b1` has no entry although commit `42d4c8e` rolled it; four betas (b9 to b12) were cut on one day with "Fixed"-only entries; `docs/viewspec-review.md` examples and `demos/public-facts.json:116` still show `0.3.0b5`.

## 9. Repository hygiene and platform support

**H-1 (Medium) POSIX-only behaviour is undocumented.** `fcntl` locks in `review_session.py:62,115`, `converge_sessions.py:1383,1408`, `intent_patch.py:1972,1999`; `os.setsid` and `os.killpg` in `host_verify.py` and `app_freerange.py`; `O_NOFOLLOW` in two modules. The imports are lazy, so `import viewspec` works on Windows, but Review, Converge, Patch, and host verification fail at run time there. There is no operating-system classifier and the README is silent; CI runs Ubuntu only. Fix: declare "Linux and macOS" in the README and classifiers, or add `msvcrt` fallbacks and a Windows CI leg.

**H-2 (Medium) Missing community and process files.** `.github/` contains only `workflows/`. There is no `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, issue or pull request template, `CODEOWNERS`, `dependabot.yml`, or `.pre-commit-config.yaml`. `SECURITY.md` is present and good. Fix: add `CONTRIBUTING.md` with the install command, the two `npm ci` seeds, the full-history requirement, and the e2e opt-ins; add `dependabot.yml`.

**H-3 (Low) Launch media in the source repository.** `demos/launch-assets/` holds a 2.3 MB GIF, a 1.0 MB MP4, a 0.4 MB GIF, and a 0.2 MB poster; `demos/` is 10 MB of the 18 MB tracked and the pack is 8.8 MiB. Every re-encode grows history permanently. Fix: Git LFS or host the media on the Pages site or a Release asset.

**H-4 (Low) Thirty screenshots are committed twice.** Every PNG under `conformance/refinement/evidence-v2/` is byte-identical to one under `demos/proof-explorer/screenshots/` because `build_proof_explorer.py:109` copies them. Fix: copy at build time only, or reference one path.

**H-5 (Low) `.gitignore` gaps.** Missing: `.hypothesis/`, `.mypy_cache/`, the in-tree outputs the README tells users to create (`.viewspec-proof/`, `.viewspec-app-proof/`, `.viewspec/`), `test-results/` and `playwright-report/` outside the host directory, coverage outputs, and editor or OS junk.

**H-6 (Info) Verified clean.** `demos/CNAME` is `viewspec.dev` and all README and schema links use that domain. The vendored Pretext build carries its LICENSE. `.gitattributes` forces LF on all text types. The working tree stays clean after the full test run. No secrets, keys, or credential-like strings were found in the repository.

## 10. Security

The servers, token handling, filesystem hygiene, and subprocess use are careful. The gaps are in the `viewspec check` static-content gate, one destructive tool flag, and a few denial-of-service shaped inputs.

**S-1 (High) `viewspec check` accepts inline event handlers and `javascript:` links.** `check_artifact_dir` (`local_tools_validators.py:155-162`) rejects only `<script>` blocks; `_validate_no_autofetch_surfaces` and `_AutofetchSurfaceProbe` (`:1602-1662`) look only for auto-fetch tags, `<form>`, meta refresh, and plain `url(` or `@import`; `_validate_raw_html_dom_node` (`:1812-1838`) checks only that manifest attributes exist, so extra attributes and elements are never flagged. Reproduced: tampered artifacts with `<img src=x onerror=alert(1)>`, `<a href='javascript:alert(1)'>`, `<svg onload=...>`, `<details ontoggle=...>`, and SVG or MathML `javascript:` links all pass with `"ok": true` after the manifest hash is updated. SECURITY.md says `check` rejects inline event handlers. Inside Studio the hash-only CSP blocks execution, but an artifact opened from disk has no CSP. Fix: reject any attribute name that starts with `on`; reject `javascript:`, `vbscript:`, and non-image `data:` schemes on `href`, `xlink:href`, `src`, `action`, `formaction`, and `data` after entity and obfuscation decoding; for raw-HTML artifacts require every DOM element to carry a manifest node id with an attribute set equal to the manifest's.

**S-2 (Medium) The CSS auto-fetch gate is bypassed by CSS escapes, comments, `image-set()`, `feImage`, and `ping`.** `_UNSAFE_CSS_REFERENCE_RE` (`local_tools_validators.py:1546`) and `CSS_FORBIDDEN_RE` (`app_shell.py:57`) match the literal function names. Reproduced: `style="background:\75rl(https://evil.example/x)"` passes; the reviewer also confirmed `@\69mport`, `url/**/(`, `u\72 l(`, `image-set('https://...')`, `<svg><filter><feImage href='https://...'>`, and `<a ping='https://...'>`. The plain `url(https://...)` form is rejected. Fix: strip CSS comments and decode hex escapes before matching; match the URL scheme in CSS text rather than function names; add `image-set(`, `-webkit-image-set(`, `image(`, `src(`; add `feImage` to the auto-fetch tags and `ping` to the auto-fetch attributes.

**S-3 (Medium) Quadratic regexes make `check` a CPU denial of service on crafted HTML.** The `<style>` pattern at `local_tools_validators.py:1552-1554`, the `<script>` pattern at `:156`, and the same shapes in `app_shell.py:47-49` scan from every unclosed opener. The reviewer measured about four times the cost per doubling: 8,000 unclosed `<style>` openers (56 KB) took 5 s; the 5 MB input cap permits hours. Fix: locate openers with `str.find` and test only the slice to the next closer, or use possessive quantifiers, and cap the opener count.

**S-4 (Medium) `force=True` on `prove`, `prove_app`, and `compile_app` deletes any existing directory under the working directory.** `_assert_safe_app_output` (`app_paths.py`) blocks only the working directory, the repository root, the home directory, the drive root, and their parents, then `_prepare_app_output_dir` calls `shutil.rmtree(output_dir)`. The MCP tools pass `force` through (`app_tools.py:193,273`, `mcp_server.py:192-209,274-334`). An agent, including a prompt-injected one, that calls `prove_app(out_dir="src", force=True)` wipes `src/`. With `--allow-outside-cwd` the reach extends to anything except the blocked set. Fix: refuse unless the directory is empty or contains a ViewSpec-written marker such as `proof_report.json`.

**S-5 (Low) MCP review tools fail closed under the default `--cwd .`.** `run_mcp_server` uses `Path(cwd)` and never `resolve_cwd` (`mcp_server.py:68`), so the four review tools reject every path with `PATH_OUTSIDE_CWD` under the default, which pushes users toward `--allow-outside-cwd`. Verified by reading; the other tools resolve internally. Fix: `root = resolve_cwd(cwd)`.

**S-6 (Low) DESIGN.md YAML alias expansion is unbounded.** `design_md.py:181` reads the file with no size cap and `:249` loads with a SafeLoader subclass, which still expands anchors. The reviewer loaded one million nodes from a 236-byte front matter in 0.7 s; two more nesting levels exhaust memory. Fix: cap bytes and count constructed nodes, or forbid aliases.

**S-7 (Low) One unclosed `<base>` or `<embed>` makes the raw-HTML importer drop the rest of the document.** `raw_html.py:31` omits `base`, `embed`, `param`, and `keygen` from `VOID_TAGS`, and the skipped-tag stack pops only on an exact match (`:292-297`). This fails closed (content loss), not open. Fix: add the void tags and pop back to the matching open tag.

**S-8 (Low) The internal upload authorizer trusts a plain header outside the ASGI bridge.** `authorize_internal_studio_review_upload` (`studio_review_internal.py:341-348`) accepts `X-ViewSpec-Internal-Authenticated: true` from raw headers; only `studio_review_asgi.py:227-228` strips reserved headers. A deployment that wires the HTTP adapter to another transport, as `tests/studio_review_https_server.py` does, lets any client create sessions. Fix: strip reserved headers inside the adapter or require the verified request object.

**S-9 (Low) Whole-file reads before size checks.** `local_tools.py:163,250-251,286` and `design_md.py:181` call `read_text()` before the byte caps apply, so a multi-gigabyte local file is read fully. Fix: an `lstat` gate or the bounded reader that already exists in `review_compile.py:1646`.

**S-10 (Info) `check` is consistency, not authenticity.** It compares `index.html` with the manifest's own unsigned `artifact_hash`; anyone who edits the artifact can re-hash, as the S-1 reproduction did. `prove` adds `proof_identity`. SECURITY.md should say so.

**S-11 (Info) Hosted client trust model.** The receipt public key is fetched from the same origin (`/v1/receipt-key`) unless the caller pins one, so receipts protect against third parties, not against a malicious API origin; `api_url` accepts `http://`. API keys travel only in `Authorization: Bearer` with TLS verification on; the share publisher uses `trust_env=False`, no redirects, and bounded responses. Suggest shipping a pinned key and refusing non-HTTPS URLs.

**S-12 (Info) SECURITY.md claims, checked.** "No network calls": holds; all `httpx` use is in opt-in hosted modules, with the caveat that `--install` runs `npm ci --ignore-scripts`, which downloads packages unless a seed directory is given. "`check` rejects active content": does not hold for the cases in S-1 and S-2; holds for `<script>`, `<iframe>`, `<object>`, `<embed>`, `<link>`, `<base>`, `<form>`, meta refresh, `srcdoc`, remote `src`, `srcset`, `poster`, `formaction`, `background`, `action`, SVG `<image>` and `<use>`, and plain `url(` or `@import`. The raw-HTML import sanitizer holds against about fifty payloads (entities, control characters, case, `&colon;`, `data:`, `srcdoc`, `<svg><script>`, `<math>`, `<base>`, `<template>`, CDATA and comment tricks, 300-deep nesting). "Path containment": holds; `resolve_local_path` uses `Path.resolve()` before the `relative_to` check and rejects `..`, absolute paths, symlinked files and directories, URL forms, and NUL; every tool resolves every path through it; caveats are S-4 and S-5. "Validators fail closed with caps": holds, with caveats S-3, S-6, and S-9, and DESIGN.md ignores unknown top-level keys instead of rejecting them.

**S-13 (Info) Controls verified as sound.** Both local servers refuse any bind host other than `127.0.0.1` and require a matching `Host` header. Tokens come from `secrets`; every comparison is SHA-256 plus `hmac.compare_digest`; the bootstrap URL is single-use with a 60 s lifetime; the agent capability is stored with mode 0o600 and only its digest is in `server.json`. Cookies are `HttpOnly`, `SameSite=Strict`, path-scoped; hosted cookies use `__Secure-`. Review mutations require a matching `Origin`, `Sec-Fetch-Site: same-origin`, a per-revision frame nonce, and a handshake; hosted mutations require exact `Origin`, JSON content type, and an idempotency key. No CORS headers are emitted. Static serving uses an allowlist, a canonical segment regex, symlink rejection, `O_NOFOLLOW`, and per-read size and hash checks; no directory listing exists. Responses carry `no-store`, `nosniff`, `no-referrer`, hash-only CSP, and sandboxed frames without `allow-same-origin`; hosted responses add COOP, CORP, HSTS, and `frame-ancestors`. Connection counts and socket timeouts are bounded. Every subprocess call is an argument list with a timeout; Bun must be an absolute path; `npm ci` runs with `--ignore-scripts`; process groups are killed on timeout. Code executed by Node is compiler-generated. Plugins are in-process objects with ABI and id validation. Parsing uses `json.loads`, a duplicate-key-rejecting YAML SafeLoader, and `ParseDict(ignore_unknown_fields=False)`. ZIP ingress accepts `ZIP_STORED` only with member, size, mode, and timestamp checks and no path traversal. The repository contains no secrets; the API key is never written to receipts, manifests, or logs. Temporary files use 0o700 directories, 0o600 files, `O_EXCL`, fsync, and atomic rename. The internal API hop is HMAC-SHA256 over method, path, headers, body hash, timestamp, and nonce, with a durable nonce store. All SQL is parameterised.

## 11. Recommended action plan

**Now, before the next release**

1. Declare `protobuf>=6.31.1,<8`. Add a CI leg that installs the exact floor and runs `python -c "import viewspec"` (P-1).
2. Bump vite to 8.1.6 or later in the three `package.json` files and lockfiles. Rerun `tests/react_dependency_regressions.mjs`. Ship a patch release because the template is in the wheel (P-2).
3. Close the `viewspec check` gaps: reject every attribute whose name starts with `on`; reject `javascript:`, `vbscript:`, and non-image `data:` schemes on `href`, `xlink:href`, `src`, `action`, `formaction`, and `data` after entity and obfuscation decoding; strip CSS comments and decode CSS hex escapes before matching; add `image-set(`, `feImage`, and `ping`. Add each bypass to `conformance/refinement/negative-corpus.json`. Until the fix ships, soften the SECURITY.md sentence (S-1, S-2).
4. Render integers as integers and reject magnitudes above 2^53 with a diagnostic; set `ensure_ascii=False` in `_text_from_value`. Add property tests over scalar types through JSON and proto round trips (C-1, C-2, T-4).
5. Catch only `UnsupportedMotifError` in `compile_auto` (C-3).
6. Refuse `force=True` deletion unless the directory is empty or holds a ViewSpec-written marker. Resolve the MCP root with `resolve_cwd` (S-4, S-5).
7. Bound the `check` regex scan and cap YAML node construction (S-3, S-6).

**Next release cycle**

8. Commit `universal_ui.proto`, a pinned regeneration script, and a generated `.pyi`. Add pyright in basic mode to CI with a baseline, and reduce the baseline starting with `app_reports.py`, `local_tools_validators.py`, `presentation_plan.py`, `types.py` (P-3, Q-1).
9. Add a Python lockfile or constraints file, `dependabot.yml`, and SHA-pinned actions (P-4, P-5, R-1).
10. Make the default test suite self-contained, or give the seeded tests a marker. Write `CONTRIBUTING.md` (T-1, H-2).
11. Pin one golden `artifact_hash` and `semantic_digest` per corpus case per target. Add a Windows CI leg or document "Linux and macOS only" (T-2, C-6, H-1).
12. Decouple the documentation deploy from the PyPI check. Block `npm audit` on `high` and run the full audit on a schedule. Assert the font census in the Pretext job. Install the host-verify seed before the host proof step and point the verifier at it (R-2, R-3, R-4, R-10).
13. Decide the formatter policy: either adopt `ruff format` with one reformat commit and a CI check, or remove the unused `line-length`. Enable `E,F,I,UP,B,SIM,C90` with a baseline (Q-7).

**Later**

14. Extract one `emitters/_semantics.py` used by all emitters and the validator; one `write_artifact_set()`; one `_canonical.py` for canonical JSON and SHA-256. Remove the `recipes` module mutation. Split the six largest modules and move the embedded browser UI to static files (Q-2 to Q-5, C-5).
15. Tier the public API before 1.0 and lazy-load the heavy modules (Q-6).
16. Add `docs/README.md`, move internal notes to `docs/internal/`, update the Review status header, date the evidence cited in the README, fix the nonexistent error code, and trim the README (D-2 to D-7).
17. Move launch media to LFS or a Release asset; stop committing screenshots twice; extend `.gitignore` (H-3 to H-5).
18. Add `logging`, one typed internal-error class, and relative paths in proof reports (Q-8, Q-9, C-7).

## 12. Appendix

### Environment

Python 3.13.16; uv; Node 22.22.0; npm 10.9.4; ruff 0.15.22 (pinned) for the pass/fail check and 0.16.9 for the extended scan; pyright and mypy current releases; Linux container; shallow clone (full history was not available locally; remote facts came from the GitHub API).

### Commands run

```bash
uv venv && uv pip install -e ".[dev,remote,agents]"
ruff check . ; ruff format --check . ; python -m compileall src tests examples scripts
python -m pytest -q --strict-markers -m "not e2e"          # fresh clone
npm ci --ignore-scripts   # in conformance/agent-ui-v2/react-dependencies and src/viewspec/host_verify_template
python -m pytest -q --strict-markers -m "not e2e"          # with seeds
pyright --outputjson src/viewspec ; mypy --ignore-missing-imports src/viewspec
ruff check src scripts examples --select E,W,F,B,UP,S,SIM,PL,RUF,PERF,ARG,PTH,RET,C4,DTZ,T20,ERA,TRY
ruff check src --select C901 --config "lint.mccabe.max-complexity=25"
python -m build ; python scripts/check_distribution.py dist
pip-audit ; npm audit --package-lock-only --audit-level=low   # three lockfiles
viewspec prove --out .viewspec-proof   # twice, then diff
```

### Reproductions

- **P-1.** `uv pip install -e . "protobuf<6"` then `python -c "import viewspec"` raises `VersionError: gencode 6.31.1 runtime 5.29.6`. With `protobuf==5.26.1`: `ImportError: cannot import name 'runtime_version'`.
- **C-1.** A bundle with attribute values `100` and `9007199254740993` passes `viewspec validate-intent`; `viewspec compile` writes `100.0` and `9007199254740992.0` into `index.html` (four occurrences each).
- **S-1, S-2.** Compile the starter intent; copy the output; insert `<img src=x onerror=alert(1)>`, or `<a href='javascript:alert(1)'>`, or `<p style="background:\75rl(https://evil.example/x)">` after `<body`; update `artifact_hash` in the manifest; `viewspec check <dir> --json` returns `"ok": true` with no errors for all three. The plain `url(https://...)` variant is rejected.
- **P-2.** `npm audit --package-lock-only --audit-level=low` in each seed directory reports `vite 8.0.0 - 8.1.5, Severity: high` and exits 1.
- **C-7.** Two `viewspec prove` runs: `artifact/` identical; `proof_report.json`, `PROOF.md`, and `support_bundle.json` differ by absolute paths, phase timings, and `proof_report_hash`.

### Verified by reading only

S-3 (regex timing was measured by the reviewer on synthetic input, not re-measured here), S-4, S-5, S-6 to S-9, C-4, C-5, C-6, Q-3, Q-5, the emitter duplication inventory, and the CI workflow observations.

### Not verified

Windows behaviour; the hosted API; the opt-in `e2e` tests and the Playwright suites (CI runs them, and the last run on `main` passed); live PyPI state. The local clone was shallow, so remote tags and releases were taken from the GitHub API, which shows tags for `v0.3.0b3`, `b5`, `b7`, `b9`, `b10`, `b11`, `b12` and Releases for six of them.

### Method note

Three parallel focused reviews (security surfaces; compiler core; documentation, CI, and distribution) produced candidate findings. Every High finding and the determinism claims were reproduced or re-read independently before inclusion. One candidate finding ("no tags or releases exist") was wrong because of the shallow clone and was corrected against the remote.

## 13. Resolution status

Status as of the follow-up work on 2026-10-22, all on branch `claude/project-audit-uv9ohi` (pull request #171). "Fixed" means the change is merged on that branch with tests. "Partial" names what remains. "Open" names the reason. The rows were revised after the review of the pull request: where a fix holds only within a boundary, the row states the boundary.

| Id | Status | Resolution |
|---|---|---|
| P-1 | Fixed | `protobuf>=6.31.1,<8`; CI job imports the package at the floor; tests tie the floor to the gencode version and the job's pin to the floor. |
| P-2 | Fixed | vite 8.3.5 in all three seeds; `npm audit` reports zero advisories. |
| P-3 | Fixed | `proto/universal_ui.proto`, `scripts/regenerate_schema.py`, `.pyi` stub; CI checks source against gencode. The `dev` extra pins `grpcio-tools` 1.80.0 (protoc 31.1, wheels for Python 3.11 to 3.14). |
| P-4 | Fixed | `requirements-ci.txt` used as pip constraints in every CI install; the Pretext workflow also runs when it changes. |
| P-5 | Fixed | `.github/dependabot.yml`: one grouped minor-and-patch pull request a week for pip, the three npm seeds (one entry) and Actions. Semver-major version updates and the contract-pinned packages (grpcio-tools, pyright, ruff, Pretext, Freerange, TypeScript, Playwright, mcp 2.x) are ignored; CONTRIBUTING.md has the hand-bump steps for a major update in each ecosystem and for each of those packages. These ignores keep out proposals like pull request #194 (mcp 1.30.0 to 2.3.0, which rewrote `mcp>=1.9.0,<2` as `mcp>=2.3.0,<3`). The pip entry uses `versioning-strategy: increase-if-necessary`, so it moves exact pins and leaves a range in `pyproject.toml` alone while it allows the new release; the default acts as `increase` here and would raise a floor such as `PyYAML>=6.0` to every new release. `tests/test_ci_config.py` checks the configuration. |
| P-6 | Fixed | `cryptography<53`, `mcp<2`: MCP SDK 2.x removed `mcp.server.fastmcp`, which `viewspec mcp` imports, and `doctor --agents` now checks that module, reporting any import failure as unavailable. |
| P-7 | Fixed | `overrides` for `source-map-js` in the three manifests. |
| P-8 | Fixed | `.pre-commit-config.yaml` pinned to ruff 0.15.22; `tests/test_ci_config.py` keeps its `rev` equal to the `ruff` pin, and the ruff hand-bump steps in CONTRIBUTING.md name the file. |
| P-9 | Info | No action; caps raised with R-8. |
| C-1 | Fixed | Integral doubles become ints at `SemanticNode` construction (a snapshot: caller data is not modified, nested tuples become lists) and after round trips, so integers within 2^53 compile identically in memory and after JSON or proto round trips. `validate-intent`, `viewspec compile` and the agent/MCP tools reject magnitudes above 2^53 with `INTEGER_OUT_OF_SAFE_RANGE`. The Python `compile()` API does not check magnitudes; Python callers run `validate_agent_intent_bundle` on the JSON payload first. |
| C-2 | Fixed | `ensure_ascii=False` in text rendering. Lists and dicts also render with compact separators, which changes page text and hashes for every bundle that binds one. |
| C-3 | Fixed | `compile_auto` falls back only on `UnsupportedMotifError`. |
| C-4 | Partial | Output no longer depends on protobuf number formatting and the runtime is capped below 8; values still pass through `Struct` by design. |
| C-5 | Fixed | `viewspec/_canonical.py`; the helpers pinned in `tests/test_canonical.py` (verification, repair, convergence, presentation plan, semantic digest, Pretext coverage and the hosted contracts on the ASCII form; state IR, intent tools, AppBundle validation, intent patches, converge sessions and the Review V0 contract on the UTF-8 form) are aliases or thin wrappers of its two forms, and both forms reject NaN; no digest changed. Digests that keep their own serialization, none with `allow_nan=False`: `agent_eval_value.canonical_bytes` (agent-eval manifest and checkpoint digests), `prove.proof_report_hash` and `app_reports.app_proof_report_hash` (indented report text), and the inline `json.dumps` digests in `local_verify.py` (artifact identity), `tailwind_recipe_registry_digest`, `app_numeric.numeric_operation_inventory_sha256`, `compiler_benchmarks.py` and `review_manifest.ReviewManifestIndex.semantic_identity_sha256`, whose inputs hold no non-finite numbers. `agent_eval.canonical_json` writes indented agent-eval output and rejects NaN. Converging the forms is an owner decision. |
| C-6 | Fixed | A Windows CI job compares the corpus goldens with Linux; it found and fixed backslashes in the React app manifest. |
| C-7 | Partial | On-disk proof reports record paths relative to the report's directory; the proof report hash excludes the top-level `timings_ms` and is the same for a report read back from disk from any working directory; `support_bundle.json` is at schema 2 without timings. The hash is shared across runs and directories for `html-tailwind`, `react-tsx` and `prove-app --target html-tailwind` when inputs keep their position relative to the report; an input outside the proof directory at a different depth changes it. Remaining: `react-tailwind-tsx` and `react-tailwind-app` hashes change on every run (host report timings), and `react-tailwind-app` hashes also change by directory because `app_analysis_evidence.json` records the absolute `artifact_dir`. |
| C-8 | Fixed | Unreachable branch, dead accumulators, duplicate digest and unreferenced constants removed. |
| C-9 | Open | API inconsistencies (`design` string-as-content, unused `lint`, role matching, nine subcommands without `--json`) are behaviour changes left for the owner. |
| S-1 | Fixed | Event handlers, unsafe URL schemes, unclosed `<script`, raw-HTML governance; markup that browsers and `html.parser` parse differently (comments, CDATA and other `<![` sections, processing instructions, raw-text elements inside SVG or MathML) fails with `CHECK_AMBIGUOUS_MARKUP`; nine bypasses are negative tests. |
| S-2 | Fixed | CSS escapes and comments decoded; `image-set()`, `feImage`, `ping` covered; `http:` and `https:` URLs written without `//` count as remote. |
| S-3 | Fixed | Linear tag scans with a block cap; timing tests. |
| S-4 | Fixed | `--force` replaces only empty or ViewSpec-marked directories; `prove` and `prove-app` write a `.viewspec-output` marker when they create the directory, so an interrupted proof can be replaced. |
| S-5 | Fixed | MCP server resolves its working directory. |
| S-6 | Fixed | DESIGN.md capped at 64 KiB; YAML aliases rejected. |
| S-7 | Fixed | Void tags added; skipped-tag stack pops to the matching tag. |
| S-8 | Fixed | Adapter strips reserved internal headers; the verified flag lives on the request object. |
| S-9 | Fixed | Size gates before reads. |
| S-10 | Fixed | SECURITY.md states that `viewspec check` verifies consistency with an unsigned manifest, not authorship, and points to `prove` for identity. |
| S-11 | Open | Pinned receipt key and HTTPS-only `api_url` need a hosted-side decision. |
| S-12, S-13 | Fixed | SECURITY.md describes the resolved MCP working directory, the `--force` marker rule, the DESIGN.md cap and the HTML size gate. |
| Q-1 | Partial | Stub and `pyrightconfig.json` added (849 to 770 errors); a baseline gate stops regressions; the remaining errors are not fixed. |
| Q-2 | Partial | Hosted client moved out of `compiler.py` and re-exported from it; `viewspec.compiler` keeps every public name it had on `main`. Module splits deferred by owner decision. |
| Q-3 | Partial | The three emitters and the raw-HTML importer share `viewspec.emitters._write`; tag and ARIA ladders deferred by owner decision. |
| Q-4 | Open | Recipes module mutation deferred by owner decision (regression risk). |
| Q-5 | Partial | Constant moved, public helper names, `derive_style_tokens` public; agent-layer and tool-layer imports remain, described in the merge notes. |
| Q-6 | Open | API tiering is a pre-1.0 owner decision. |
| Q-7 | Fixed | `ruff format` applied to every Python file and gated in CI; rules `I`, `UP`, `B`, `SIM` enabled with two documented ignores. The hand edits the rules required were compared by AST with the previous code; the one that changed behaviour (interrupted IntentPatch recovery read another transaction's receipt) is reverted with a regression test. |
| Q-8 | Fixed | `CompilerInternalError` and `LocalToolContractError`, which survive pickle and copy, as `ConvergeError`, `IntentPatchError` and the Tailwind emitter's `CompilerConstraintError` now do; two coded raises in `app_numeric.py` and one in the HTML emitter remain. |
| Q-9 | Fixed | Loggers with a `NullHandler`. Daemons run with `python -m` log under `viewspec.<module>` through a formatted stderr handler; `viewspec review` and Studio start them with stderr sent to `/dev/null`, so their logs are visible only when a daemon is run directly. The review server and the Studio creation room redact request targets in their DEBUG request logs. |
| Q-10 | Fixed | Secondary failures logged; `raise ... from` everywhere; when the app support bundle and `APP_PROOF.md` both fail to write, `app_proof_report.json` is rewritten to match the returned report. |
| Q-11 | Fixed | All 52 asserts in request and tool paths replaced with explicit checks. |
| Q-12 | Fixed | Builder exports added; alias kept. |
| Q-13 | Info | |
| T-1 | Fixed | Exact skip reasons; CI still runs everything; CONTRIBUTING.md describes the skips. |
| T-2 | Fixed | 32 pinned outputs; regeneration script; checked on Linux and Windows. |
| T-3 | Fixed | Landing page and proof explorer in the drift test; cross-platform dashboard pinned as a hosted fixture. |
| T-4 | Fixed | Property tests over scalar types. |
| T-5 | Fixed | Smoke tests for three scripts. |
| T-6 | Info | |
| R-1 | Fixed | Every action pinned to a commit, at the major version each workflow used before. |
| R-2 | Fixed | Approved by the owner; PyPI check no longer blocks the docs deploy; the release checklist describes the resulting order of merge, upload and deploy. |
| R-3 | Fixed | Approved by the owner; PRs block on high and critical; weekly audit at every level. |
| R-4 | Fixed | Missing reference fonts fail the Pretext gate loudly. |
| R-5 | Open | The exact test count and npm patch assertions stay; relaxing them was judged a gate change for the owner. |
| R-6 | Fixed | Caching, timeouts, concurrency that supersedes only pull request runs (every push to `main` runs to completion), the `agents` extra in every test leg with a test that starts the real stdio MCP server, lockfile-based Playwright. |
| R-7 | Fixed | Version single-sourced; tag and release step in the checklist. |
| R-8 | Partial | sdist ships tests, conformance, schema source, changelog and policy. The full suite still needs a git checkout: some tests import or read `scripts/`, `demos/`, `docs/`, `examples/`, `integrations/` and `.github/`, and the root files `requirements-ci.txt` and `.pre-commit-config.yaml`, none of which the sdist carries. |
| R-9 | Fixed | One legal name. |
| R-10 | Fixed | Seed installed before the host proof; verifier reuses it. |
| R-11 | Info | |
| D-1 | Info | |
| D-2 to D-7 | Fixed | README and docs corrected; index added; internal notes moved; tests guard codes and the skill file. |
| H-1 | Fixed | Classifiers and README state Linux and macOS. CI runs on Linux plus a Windows goldens job; macOS is declared but not exercised in CI, as README and CONTRIBUTING.md say. |
| H-2 | Partial | CONTRIBUTING.md and CODE_OF_CONDUCT.md added; issue and PR templates and CODEOWNERS not added. |
| H-3 | Open | Git LFS migration was prepared, but this environment cannot reach `lfs.github.com`; run `git lfs track "demos/launch-assets/*.{gif,mp4,png}"`, re-add the files and push from a machine with LFS access, and set `lfs: true` on the checkout steps. |
| H-4 | Open by design | Git stores identical blobs once, and the drift test pins the copies; a deploy-time copy would remove the duplicates at the cost of building during the Pages deploy. |
| H-5 | Fixed | `.gitignore` extended. |
| H-6 | Info | |
