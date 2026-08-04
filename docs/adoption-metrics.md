# Adoption metrics

FasterRaster reports only explicitly defined aggregate signals. They are not a user counter, installation counter, scientific-use counter, or endorsement measure.

| Metric | Represents | Does not represent |
|---|---|---|
| Repository clone count | Complete GitHub clone events in GitHub's rolling window | Fetches, pulls, installations, people, or active users |
| GitHub-reported unique cloners | GitHub's deduplicated estimate in its rolling window | Verified humans or total users |
| Release asset downloads | Requests counted for a named wheel, sdist, or checksum asset | Successful installs, unique people, or active use |
| Package-index downloads | Future package-index file downloads | Successful installs, active use, or unique people |
| Repository page views | GitHub repository content views | GitHub Pages documentation views |
| GitHub-reported unique visitors | GitHub's deduplicated repository-view estimate in its rolling window | Verified people, installations, or documentation journeys |
| Popular paths/referrers | GitHub paths and referrers | Full Pages navigation or user journeys |
| Documentation views | Unavailable without a separate analytics provider | Must not be inferred from repository views |
| Active registered FasterRaster handles | Active validated public registry records | Unique humans, installations, or endorsement |

GitHub traffic is a rolling 14-day view and covers full clones, not fetches. A daily scheduled run and an owner-triggered manual run can archive sanitized aggregates to the dedicated `metrics-archive` branch after the owner provisions both that branch and the read-only traffic API secret. If either prerequisite is absent, no snapshot is written; if a configured request or validation fails, the workflow fails without replacing an existing snapshot.

No Pages beacon, cookie, fingerprint, installation ID, updater telemetry, or raw IP is collected.
