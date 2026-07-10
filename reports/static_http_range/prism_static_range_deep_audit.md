# PRISM Static Range Deep Audit

PRISM daily precipitation is preserved as a historical contract fixture for v0.6.0. It is not counted as a runnable Wave 1 `static_http_range` source.

## Audit Scope

- Audited dates: `20230101`, `20230315`, `20230715`, `20231001`, `20231215`, `20240115`, `20240601`
- Audited candidate count: 70
- Working static ZIP candidates found: 0
- Observed HTTP statuses: 200 and 404

## Audited Endpoint Families

- PRISM static `data.prism.oregonstate.edu` daily stable/provisional/early ZIP paths
- PRISM static `ftp.prism.oregonstate.edu` daily stable/provisional/early ZIP paths
- PRISM `fetchData.php` stable/provisional/early service variants
- NACSE public PRISM service path variants

## Historical Evidence

- Source: `prism_daily_ppt`
- Endpoint: `prism_daily_zip`
- HTTP status: 206
- Bytes read: 65536
- Content type: `application/zip`
- Detected magic: `zip`
- SHA256 short: `cc89306d4d5b`
- Observed at UTC: `2026-07-08T21:29:55Z`

## Decision

`prism_daily_ppt_static_zip` is fixture-only for v0.6.0.

Historical bounded ZIP evidence exists, but current deterministic URL reproduction failed across the audited static and service candidates. Future PRISM work should resolve the current catalog/service asset strategy or a versioned path strategy before live adapter execution is enabled.

No runtime promotion was performed.
