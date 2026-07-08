# Authentication Design

FasterRaster v0.4 includes authentication scaffolding only. It does not perform real login, does not write secrets to disk, and does not enable credentialed downloads.

## Rules

- Secrets are referenced only by environment variable name.
- Raw token/password-looking values are rejected in profile files.
- Reports must redact secret-bearing fields.
- Credentialed profiles default to disabled.
- Placeholder auth types may support metadata-probe design later, but not live downloads.

## Profile Types

- `none`
- `bearer_token_env`
- `basic_env`
- `netrc`
- `earthdata_login_placeholder`
- `copernicus_oauth_placeholder`
- `usgs_m2m_placeholder`
- `aws_requester_pays_placeholder`

## Non-Goals

No OAuth flow, Earthdata login, S3 signing, cookie session, M2M login, or credentialed raster fetch is implemented in this milestone.
