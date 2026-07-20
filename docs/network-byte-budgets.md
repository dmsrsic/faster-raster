# Network and byte-budget behavior

Network intent is explicit and bounded.

- `fr doctor --offline`, template commands, `fr validate`, and `fr plan --offline` make no network requests.
- A live human-development cook requires `data.allow_network: true` in the workfile.
- Hybrid publication requires `--allow-network` unless every compatible imagery asset is reused.
- `reuse: only` is always zero-network and fails closed on a missing asset.
- Bounded probes stop at their configured prefix ceiling and do not authorize full-object materialization.
- Complete-object materialization has separate object and total ceilings plus explicit plan-hash approval.

Review `maximum_download_mb` before a live operation. Receipts distinguish network bytes from reused bytes. A source error, unexpected response, or ceiling violation prevents finalization rather than silently continuing with partial evidence.
