# FasterRaster CLI Demo

Kitchen Mode is FasterRaster's friendly CLI language for browsing the source atlas and planning source unlocks. It calls the source atlas the pantry, datasets sauces, bounded probes dips, stack plans recipes, and credential gates locks.

Canonical JSON remains standard. Kitchen words are display labels and command aliases only; they are not schema keys.

## Screenshot Artifacts

- `reports/cli_screenshots/01_pantry_sauces.svg`
- `reports/cli_screenshots/02_sauce_card_prism.svg`
- `reports/cli_screenshots/03_recipe_stack_summary.svg`
- `reports/cli_screenshots/04_gridmet_dip_blocked.svg`
- `reports/cli_screenshots/05_batcher_unlocks.svg`
- `reports/cli_screenshots/06_menu_lingo.svg`

## Workflow

1. Open the pantry:

```bash
faster-raster pantry
```

2. Inspect a sauce:

```bash
faster-raster sauce prism_daily_ppt_static_zip
```

3. Check goods and bads:

```bash
faster-raster goods
faster-raster bads
```

4. Run a dry dip:

```bash
faster-raster dips gridmet_daily --dry-run
```

5. Inspect the batcher recommendation:

```bash
faster-raster batcher
```

6. View recipe summary:

```bash
faster-raster recipe
```

## v0.5.3 polish

Compact pantry tables are the default. Use `--wide` or `--full` for long tables.

```bash
faster-raster pantry --plain
faster-raster pantry --wide --plain
faster-raster source-scope --plain
faster-raster cook endpoints --plain
python3 -m json.tool /tmp/cook_queue.json >/dev/null
```
