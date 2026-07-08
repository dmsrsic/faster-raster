# FasterRaster CLI Commands

Professional commands remain stable:

```bash
faster-raster sources list
faster-raster sources tree
faster-raster sources show prism_daily_ppt_static_zip
faster-raster stack summary
faster-raster unlocks next
faster-raster probe atlas gridmet_daily --dry-run
faster-raster help style
faster-raster explore
```

Kitchen aliases:

```bash
faster-raster pantry
faster-raster sauces
faster-raster sauce gridmet_daily
faster-raster reigns
faster-raster buckets
faster-raster goods
faster-raster bads
faster-raster recipe
faster-raster batcher
faster-raster dips gridmet_daily --dry-run
faster-raster menu lingo
```

Output modes:

```bash
faster-raster sources list --plain
faster-raster sources list --json
faster-raster sources list --lingo kitchen
```

JSON output keeps canonical field names such as `source_id`, `provider`, `credential_requirement`, and `promotion_status`.


## User toggles and cook planning

```bash
faster-raster toggles show
faster-raster toggles explain
faster-raster cook plan
faster-raster cook queue
faster-raster cook dip gridmet_daily --dry-run
faster-raster cook propose gridmet_daily
```

Kitchen aliases:

```bash
faster-raster knobs
faster-raster knobs explain
faster-raster cookplan
faster-raster queue
faster-raster cookdip gridmet_daily --dry-run
faster-raster cookproposal gridmet_daily
```

Cook commands are planning/proposal surfaces. They do not edit the runtime registry and live dips remain opt-in.

## Source scope and endpoint readiness

```bash
faster-raster source-scope --plain
faster-raster scope --plain
faster-raster cook endpoints --plain
faster-raster cook endpoints --wide --plain
faster-raster cook endpoints --ready-only --plain
faster-raster endpoints --plain
```

Use `python3 -m json.tool` for JSON validation in WSL environments.
