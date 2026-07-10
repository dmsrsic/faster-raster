# Static HTTP Range Plan

runnable_source_count: 0
fixture_source_count: 1
attempted_source_count: 0
pass_count: 0
fail_count: 0
fixture_count: 1
network_run: False
decision: not_promoted

PRISM is preserved separately as historical bounded contract evidence and is not counted as a runnable Wave 1 adapter failure.

| Source | Status | HTTP | Bytes | Magic | Family | Quality |
| --- | --- | ---: | ---: | --- | --- | --- |

## Contract Fixtures

| Source | Status | Historical evidence | Current endpoint |
| --- | --- | --- | --- |
| `prism_daily_ppt_static_zip` | `fixture_only` | `application/zip / zip / cc89306d4d5b` | `unresolved_or_stale` |

## Dry-Run Source Plan

| Source | Expected magic | Expected family | Required params | Default params | URL/template |
| --- | --- | --- | --- | --- | --- |

## Content Families

| Source | Expected | Detected |
| --- | --- | --- |

## Magic Validation

| Source | Expected | Detected |
| --- | --- | --- |

## Strongest Candidates

- None

## Failures/Cautions

- None

## Decision

`not_promoted`

## Next Live Command

```bash
faster-raster range wave1 --allow-network --max-bytes 65536 --plain
```
