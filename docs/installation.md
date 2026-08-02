# Installation

## Requirements

- Python 3.12
- GDAL/Rasterio-compatible native libraries
- enough local storage for the study's explicit byte ceiling and derived products

Ubuntu is the public CI target. WSL2 is exercised during local release validation. macOS and native Windows are not yet in the beta CI matrix.

## Install the published beta.5 wheel

The reproducible public installation starts from the immutable GitHub release asset:
```sh { .release-operator }
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install https://github.com/dmsrsic/faster-raster/releases/download/v1.0.0-beta.5/faster_raster-1.0.0b5-py3-none-any.whl
fr --version
fr doctor --offline
```

The beta.5 release has wheel and source assets on GitHub. Verify the release page and retain the asset digest with your study record.

## Use the development checkout
```sh { .illustrative }
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
```sh { .release-operator }
python -m build
python3.12 -m venv /tmp/fasterraster-wheel-test
/tmp/fasterraster-wheel-test/bin/pip install dist/faster_raster-1.0.0b5-py3-none-any.whl
cd /tmp
/tmp/fasterraster-wheel-test/bin/fr --help
/tmp/fasterraster-wheel-test/bin/fr doctor --offline
```

The wheel installation must work outside the source checkout. An editable install is not a release-installation test.

## Validate the documentation

```sh
mkdocs build --strict
```

The generated site is local under `site/`. The Pages workflow deploys that directory from the public `main` branch after strict validation.
