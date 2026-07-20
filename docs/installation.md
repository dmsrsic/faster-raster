# Installation

## Requirements

- Python 3.12
- GDAL/Rasterio-compatible native libraries
- enough local storage for the study's explicit byte ceiling and derived products

Ubuntu is the public CI target. WSL2 is exercised during local release validation. macOS and native Windows are not yet in the beta CI matrix.

## Install from a checkout

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install .
fr --version
fr doctor --offline
```

For development and documentation:

```sh
python -m pip install -e '.[dev,docs]'
```

## Install a built wheel

```sh
python -m build
python3.12 -m venv /tmp/fasterraster-wheel-test
/tmp/fasterraster-wheel-test/bin/pip install dist/faster_raster-1.0.0b1-py3-none-any.whl
cd /tmp
/tmp/fasterraster-wheel-test/bin/fr --help
/tmp/fasterraster-wheel-test/bin/fr doctor --offline
```

The wheel installation must work outside the source checkout. An editable install is not a release-installation test.

## Validate the documentation

```sh
mkdocs build --strict
```

The generated site is local under `site/`; no deployment is configured.
