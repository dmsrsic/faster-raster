# Static HTTP Range Adapter

`static_http_range` is an experimental, no-auth adapter for deterministic bounded probes against public raster, archive, and NetCDF URLs.

It does not implement production acquisition, extraction, catalog crawling, credential handling, or unbounded downloads. Network access is off by default and live probes require `--allow-network`.

## Wave 1 Sources

The Wave 1 config lives at `configs/static_http_range_wave1.yaml` and covers:

- `chirps_daily_precipitation`
- `gridmet_daily`
- `terraclimate_monthly`
- `worldclim_bioclim_normals`
- `prism_daily_ppt_static_zip` as a fixture-only historical contract

Runnable sources declare deterministic URL inputs, expected magic/content family, and a byte cap. Contract fixtures preserve bounded evidence from earlier live audits but are not probed as live Wave 1 endpoints.

## Runnable Sources vs Contract Fixtures

`runnable_sources` are currently reproducible deterministic HTTP endpoints. `range wave1 --allow-network` probes only these sources.

`contract_fixtures` are historical evidence rows that remain useful for adapter contracts and future fixture work, but they are not treated as live endpoints. Fixture-only entries:

- never call network, even when `--allow-network` is supplied;
- do not count as endpoint failures;
- appear in JSON and Markdown reports under `fixtures`;
- are excluded from Wave 1 live pass/fail decisions.

PRISM is fixture-only in v0.6.0. Historical v0.5.3 evidence showed a bounded ZIP response for `prism_daily_ppt`, but the current deterministic URL could not be reproduced across audited static, service, FTP, and NACSE candidates. Historical success does not prove current deterministic URL reproducibility.

Future PRISM work likely requires service/catalog-aware asset resolution or a versioned path strategy before live adapter execution is enabled.

## Commands

```bash
faster-raster range sources --plain
faster-raster range plan --plain
faster-raster range probe chirps_daily_precipitation --plain
faster-raster range wave1 --plain
```

Live probes are opt-in:

```bash
faster-raster range probe chirps_daily_precipitation --allow-network --max-bytes 65536 --plain
faster-raster range wave1 --allow-network --max-bytes 65536 --plain
```

## Reports

Dry-run plans write:

- `reports/static_http_range/static_http_range_wave1_plan.json`
- `reports/static_http_range/static_http_range_wave1_plan.md`

Live Wave 1 probes write:

- `reports/static_http_range/static_http_range_wave1_results.json`
- `reports/static_http_range/static_http_range_wave1_results.md`

Single-source live probes also write source-specific JSON and Markdown files.

The PRISM deep audit evidence is preserved in:

- `reports/static_http_range/prism_static_range_deep_audit.json`
- `reports/static_http_range/prism_static_range_deep_audit.md`

## v0.7 Compiler Integration

`static_http_range` participates in `faster-raster task compile` and `faster-raster task package`.

Runnable sources compile into bounded HTTP range manifest rows with:

- `acquisition_mode: bounded_http_range`
- `request_method: GET`
- `Range: bytes=0-65535`
- `checksum_policy: compute_after_fetch`
- validation steps for HTTP status, byte cap, magic bytes, content family, SHA256, and range behavior

Fixture-only PRISM compiles into a historical evidence row with `execution_status: non_executable_fixture`. It does not generate a fetch job.

Harmonization readiness is explicit. The package stops before unsupported decoding:

- CHIRPS requires decompression and raster decode.
- gridMET and TerraClimate require NetCDF variable selection.
- WorldClim requires archive member resolution.
- PRISM requires endpoint resolution before live execution.

## Safety Contract

- Sends `Range: bytes=0-(max_bytes-1)`.
- Reads at most `max_bytes`.
- Records status, content type, bytes read, SHA256, range behavior, detected magic, and content family.
- Fails closed on missing URL parameters, HTTP failures, and magic/content-family mismatches.
- Preserves fixture-only rows separately instead of silently treating them as live failures.
- Does not store credentials or mutate the runtime source registry.
