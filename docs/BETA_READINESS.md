# Beta Gate 1 readiness rubric

Current score: **100/100**  
Decision: **beta ready**

The source-level, packaging, and operational gate passes. Isolated managed CPython 3.12.13 built both release artifacts; a fresh Python 3.12 environment installed the wheel and passed the installed `faster-raster`, `fr`, and `fr-beta-check` command proofs outside the checkout. Offline doctor, workfile initialization, validation, planning, and explanation made zero raster-service requests. Required package data passed verification, the complete suite passed 579 tests, tracked reports were unchanged, and the exact post-change system grader remained 99.03 (`excellent`, safety 100, `release_ready`, no blockers). Verification reports and artifacts are retained under the ignored `.beta-tools` review directory.

The agricultural geography sprint adds bounded transfer evidence without changing scientific-domain credit. Meridian, Idaho preflight rejected the original 2023 request because NAIP reported no intersection, then identified 2021 as the common NAIP/CDL year. Irrigation-field-structure acquired and published three assets under the 250 MB ceiling; crop-vigor-classification reused those finalized assets with zero network bytes. Both 4K previews passed checksums, contained no staging-path provenance, were visually inspected as a mixed urban–agricultural AOI, and opened through the WSL preview opener. This demonstrates source-driven non-Kansas transfer, not global coverage.

Preservation caveat: the initial baseline grader rewrote two previously modified tracked system-grade files before their prior diffs were captured. Those lost pre-run diffs are not represented as preserved evidence.

| Item | Score | Max | Evidence | Blocker | Remaining work |
|---|---:|---:|---|:---:|---|
| Baseline freeze and preservation | 8 | 8 | Frozen manifest, grader, and test evidence | no | None |
| Deterministic release inventory | 8 | 8 | Inventory module plus final JSON/Markdown inventory | no | None |
| Narrow ignore and release boundaries | 5 | 5 | `.gitignore` and beta scope | no | None |
| Local/remote/action separation | 10 | 10 | Study plan, inspection contract, and regression test | no | None |
| Stable finalized provenance | 10 | 10 | Stable IDs, relative paths, staging guard, tests, and two finalized Meridian handoffs | no | None |
| Friendly CLI and verbose/JSON exactness | 7 | 7 | `fr` presentation and CLI tests | no | None |
| Transparent doctor heuristics | 7 | 7 | Facts, candidates, limits, version, non-application, safety note | no | None |
| Installed console declarations and wheel data | 5 | 5 | Installed commands and required package data PASS outside the checkout | no | None |
| Python 3.12 build and clean install | 10 | 10 | CPython 3.12.13 build, fresh install, smoke proof, and complete tests | no | None |
| One-command isolated beta check | 10 | 10 | Final beta report PASS | no | None |
| Tracked report non-mutation | 5 | 5 | Runtime hash check and regression test | no | None |
| Python 3.12 CI gate | 5 | 5 | GitHub Actions workflow | no | Operator push will execute it |
| Beta scope and operator docs | 5 | 5 | Scope and readiness documents | no | None |
| Complete tests and post grader | 5 | 5 | 579 tests; final beta check PASS; grader unchanged at 99.03 | no | None |

The complete machine-readable rubric, including per-item evidence, blocker flags, and remaining work, is in `release/beta_readiness.json`.
