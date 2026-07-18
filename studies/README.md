# Local study playground

Generate and edit personal terminal workfiles here:

```sh
fr templates list
fr init studies/my-study.fr.md --template human-development-cdl \
  --name my-study --bbox -116.41 43.54 -116.38 43.57 \
  --years 2008 2016 2021
```

Everything in this directory except this README is ignored by Git. Shipped,
source-controlled examples remain in `examples/`; copy one here before
experimenting so local bbox, source-policy, and year edits do not change those
examples. Downloaded rasters and finalized handoffs belong under the configured
cache and `outputs/` roots, not in this directory.
