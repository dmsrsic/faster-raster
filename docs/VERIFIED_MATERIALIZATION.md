# Verified Materialization

FasterRaster v0.9.0 converts validated source contracts into complete, content-addressed, cryptographically receipted local source artifacts under explicit storage and network budgets.

A bounded probe is evidence only. It validates reachability, byte-range behavior, content magic, content family, and a retained prefix checksum. It never silently authorizes a complete-object download.

Live materialization requires `--allow-network`, `--allow-materialization`, and `--approve-plan-sha256 FULL_64_CHARACTER_SHA256`. Changed source selection, budgets, package inputs, manifest inputs, DAG inputs, or probe receipt inputs change the plan hash and block execution.

The v0.9 boundary stops at verified source artifacts. It does not perform raster reprojection, resampling, mosaicking, NetCDF variable extraction, GeoTIFF pixel decoding, ZIP extraction, gzip output extraction, harmonization, Sentinel imagery download, or PRISM live materialization.

## Probe provenance hardening

Materialization plans bind to an exact v0.8 probe receipt. Mocked deterministic fixtures and blocked-policy receipts cannot authorize complete-object transfer. Planning prefers `latest_live_verified_run.json`, records rejected latest-run reasons, and accepts explicit `--probe-run-id` or `--probe-receipt-sha256` selectors. Content-Range evidence is parsed before object size is derived; inconsistent ranges keep the selected source ineligible.
