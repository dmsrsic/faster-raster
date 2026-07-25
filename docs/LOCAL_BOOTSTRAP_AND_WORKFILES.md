# FasterRaster local bootstrap and study workfiles

## Four configuration layers

FasterRaster keeps four concerns separate:

1. The shipped source registry describes sources that may exist, their endpoints, formats, access categories, and deterministic probe strategy. Local evaluation never rewrites it.
2. The generated local capability profile records dated evidence about this machine, GDAL, local resources, credentials being present or absent, and bounded source probes. It is evidence, not a permanent availability claim.
3. User and project TOML configuration records human choices such as cache paths, reuse, byte ceilings, parallelism, source preferences, offline mode, and preview behavior. Recommendations are only applied by `fr configure --apply-recommendations`.
4. A `.fr.md` study workfile contains strictly validated YAML front matter plus ordinary Markdown notes. Only front matter controls execution; prose is never interpreted as configuration or commands.

The planner combines the shipped registry, saved capability evidence, local preferences, and study requirements. The capability profile never mutates the registry.

## Local paths

Linux and WSL defaults are:

- user configuration: `~/.config/fasterraster/config.toml`
- generated state and capability profiles: `~/.local/state/fasterraster/`
- generated cache and disposable probe artifacts: `~/.cache/fasterraster/`
- temporary fixtures: the platform temporary directory under `fasterraster/`
- optional project configuration: `.fasterraster/config.toml`

Tests and advanced users may set `FASTERRASTER_CONFIG_HOME`, `FASTERRASTER_STATE_HOME`, `FASTERRASTER_CACHE_HOME`, and `FASTERRASTER_TEMP_HOME`. `FASTERRASTER_PROFILE` selects a named capability-profile file. Generated state is not written into the Git repository by default.

## Bootstrap and bounded source probes

`fr doctor` inspects the OS, WSL status, architecture, Python, GDAL commands and drivers, TLS certificate support, writable local directories, disk space, CPU, approximate available memory, a temporary file round trip, a tiny GDAL raster round trip, and preview-opener availability. It does not contact source services.

`fr sources evaluate` is the explicit network boundary. Sources use different policies:

- `static_verified`: a bounded range request;
- `service_discovered`: a small service-capabilities or metadata request;
- `api_discovered`: a small API discovery request;
- `credential_gated`: an allowlisted credential-environment check before any authenticated probe;
- `future_unverified`: no network probe and never selectable merely because a hostname responds.

The default complete-run network ceiling is 10 MB. Each source also has a timeout, byte limit, and request limit. Redirects are not followed. A unique probe directory is always used and removed in a `finally` block after success, timeout, invalid response, credential failure, or another error. `--keep-probe-artifacts` retains a redacted JSON summary for debugging. Probe code never downloads full raster products.

Profiles are written with flush, fsync, and atomic replace. A failed write or refresh does not destroy the previous profile. Profiles contain credential state and the expected environment-variable name, never credential values, authorization headers, cookies, signed query strings, or a full environment dump.

## Source statuses and staleness

The status vocabulary is:

`available`, `available_unverified_auth`, `credential_missing`, `credential_present_unverified`, `authentication_failed`, `unreachable`, `timeout`, `rate_limited`, `service_error`, `unsupported_local_format`, `unsupported_local_driver`, `invalid_response`, `probe_not_supported`, `skipped_offline`, `disabled_by_user`, `future_unverified`, `stale`, and `unknown`.

Transient service errors remain distinct from local incompatibility and missing credentials. Default evidence lifetimes are 24 hours for local and credential observations, 7 days for static endpoints, 3 days for service discovery, 24 hours for API discovery, and 1 hour for rate-limit evidence. TOML can override those values. Stale, formerly available evidence can remain a provisional candidate; the plan says that execution must revalidate it.

## Configuration and precedence

Configuration is strict TOML. It supports named profiles; cache, state, and temporary roots; reuse; default byte ceiling; service tile size; maximum parallel tasks; preview opening; source preference, allow, and deny lists; offline mode; source-specific credential environment names and probe limits; and capability TTLs. Credential values are rejected.

Every resolved value records its value, origin layer, original key, source file, default status, recommendation status, and explicit override status in `resolved_config.json`. Precedence is deterministic, highest first:

`CLI override → workfile → project configuration → user configuration → workflow/recipe defaults → source defaults`

## Workfile front matter

The schema version is `fasterraster.work/v1`. The parser rejects duplicate YAML keys, malformed YAML, unsupported versions and workflows, unknown fields, invalid bounding boxes, invalid or reversed dates, negative limits, inconsistent source policies, inline credential fields, and executable-command fields. Validation finishes before cache discovery or any optional network refresh. Markdown after the closing `---` is preserved as prose but ignored by planning.

Source policy may be `auto`, `preferred` with `prefer` and `deny` lists, or `pinned` with logical mappings such as `natural_imagery: naip`. Auto planning does not require ordinary users to know every registry ID. The source-resolution artifact records all candidates, rejections and reasons, selected source, capability timestamp and age, credentials, local driver compatibility, fallback use, provisional status, and execution-time revalidation.

For
`workflow_id: naip_cdl_index_hybrid_classification_audit`, V1 workfiles may
include an optional validated `classification` override. It uses the V4 recipe
contract: explicit general classes/count, index requests, explicit specialist
classes/count, calibration, selection mode/search bounds, and arbitration. The
field is rejected for other workflows, so older workfiles need no new fields
and cannot silently acquire hybrid semantics.

The `ag-naip-index-hybrid-classification` template exposes point/buffer or bbox
location behavior, imagery/CDL years, byte ceiling, general class selection,
specialist strategies/labels/parents, and selection mode. Recommendation review
does not rewrite the workfile; an accepted choice applies only to that run.

## Commands

```text
fr init STUDY.fr.md [--project-config]
fr configure --show | --path | --validate
fr configure [deterministic update flags] [--apply-recommendations]
fr doctor [--offline] [--json]
fr sources [--stale] [--status STATUS] [--category CATEGORY] [--json]
fr sources evaluate [--source ID] [--offline] [--maximum-bytes N] [--keep-probe-artifacts] [--json]
fr indices list [--json]
fr indices show INDEX_ID [--json]
fr validate STUDY.fr.md [--json]
fr plan STUDY.fr.md [--out DIR] [--json]
fr explain STUDY.fr.md [--json]
fr cook STUDY.fr.md [--refresh-sources] [--json]
fr inspect latest [--json]
fr open latest [--json]
```

`validate`, `plan`, and `explain` make zero network requests by default. `--refresh-sources` is an explicit bounded refresh on plan, explain, or cook. Cook calls the existing shared agricultural execution function and preserves transactional staging, reuse, selective acquisition, validation, receipts, and checksums. The finalized handoff gains `resolved_config.json` and `source_resolution.json`.

`inspect latest` ignores hidden, staging, incomplete, failed, and temporary directories. `open latest` only opens a preview from a finalized handoff. On WSL it converts the Linux path with `wslpath -w` and uses `explorer.exe`; native Linux uses the configured opener or `xdg-open`.

## Security and future sources

Diagnostics do not upload machine inventories, scan arbitrary home files, or inspect unrelated environment variables. Source contracts allowlist the one credential environment name that may be checked. Network evidence stores a query-free endpoint and concise status, not request secrets.

Future sources such as nighttime lights can be added by registering their source metadata, access category, logical assets, credential-environment name if needed, format/driver requirement, and a bounded probe strategy. A `future_unverified` registration is visible but cannot be selected until its source contract and probe are deliberately promoted.
