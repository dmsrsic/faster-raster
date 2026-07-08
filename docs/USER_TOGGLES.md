# User Toggles

FasterRaster v0.5.2 adds safe user-facing toggles for Kitchen Mode exploration and no-auth cook planning.

Defaults are intentionally conservative:

- `network_mode: off`
- `no_auth_only: true`
- `allow_credentialed: false`
- `promotion_policy: proposal_only`
- `forbid_runtime_registry_edit: true`
- `fail_closed_on_unknown_endpoint: true`

The toggles control planning and reporting only. They do not promote adapters, do not edit `configs/source_registry.yaml`, and do not enable credentialed requests.
