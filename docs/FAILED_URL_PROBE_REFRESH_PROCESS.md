# Failed URL Probe Refresh Process

This note defines the conservative process for turning failed live URL probes into runtime FasterRaster changes.

## Current Failed Probes

| Probe | HTTP | Current interpretation |
| --- | ---: | --- |
| `nlcd_aws_tile` | 403 | The candidate Annual NLCD tile object path or access method needs official re-verification. |
| `nlcd_aws_mosaic` | 403 | The candidate Annual NLCD mosaic object path or access method needs official re-verification. |
| `daymet_ncss_tiny_query_experimental` | 401 | The experimental Daymet NCSS endpoint/path/query/access policy is not ready for runtime support. |

## Reviewed Gemini Clarification

The follow-up Gemini-style clarification has been cleaned and recorded here:

`research/failed_url_probe_fixes.reviewed.yaml`

The key review decision is conservative:

- NLCD should **not** be promoted directly to the runtime `generic_https_template` registry yet. Official USGS documentation confirms AWS S3 access and a nested `s3://usgs-landcover/annual-nlcd/c1/v0/...` structure, but describes the AWS path as requester-pays access. The anonymous HTTPS probes still return `403`, including with a simple `x-amz-request-payer: requester` header. A future S3/requester-pays-aware adapter or confirmed public HTTPS object URL is needed.
- Daymet should **not** be implemented as `generic_https_template`. It needs a future NCSS/THREDDS query adapter with small-query validation and possibly Earthdata credential awareness.

Runtime files were not changed from this clarification.

## Do Not Do

- Do not update `configs/source_registry.yaml` from unverified AI output.
- Do not update golden fixtures from failed live probes.
- Do not add downloads or default live probes.
- Do not treat HTTP 403/401 as proof that the dataset is unavailable. It may mean the URL path, host, endpoint version, request headers, or access policy is wrong.

## Refresh Workflow

1. Use [gemini_failed_url_probe_clarification_prompt.md](../prompts/gemini_failed_url_probe_clarification_prompt.md) with the latest failed probe report.
2. Require official documentation links for every proposed replacement URL pattern.
3. Save candidate corrections to a review file under `research/`, not directly to runtime config.
4. Run a bounded live structure probe with:

   ```bash
   python scripts/live_url_structure_probe.py \
     --allow-network \
     --max-bytes 65536 \
     --chunk-size 16384 \
     --timeout-seconds 20 \
     --out-json reports/live_url_structure_probe_refresh.json \
     --out-md reports/live_url_structure_probe_refresh.md
   ```

5. Only promote a source to runtime registry if:
   - the URL structure is supported by official docs,
   - placeholders are clearly known,
   - a bounded probe succeeds or the source is intentionally docs-only,
   - schema/golden tests are added,
   - no existing manifest/harmonization hashes drift unless an intentional contract version bump is made.

## NLCD-Specific Questions

- Is `https://usgs-landcover.s3.us-west-2.amazonaws.com` still the correct official host?
- Are `annual-nlcd/c1/v0/...` paths current?
- Did Collection 1.2 or later alter object paths?
- Are tile and mosaic filenames different for `FctImp`, `LndCov`, and other products?
- Does public access require another host, signed links, or a catalog lookup?
- Are range requests supported?

## Daymet-Specific Questions

- What is the current official THREDDS/NCSS path for daily Daymet?
- Which product version should be used?
- What is the exact minimal valid query syntax for one variable, one day, and a tiny bbox?
- Does the endpoint require authentication, a different dataset id, or a different output accept value?
- Should FasterRaster implement this as `future_ncss` / `thredds_ncss_template` rather than `generic_https_template`?
