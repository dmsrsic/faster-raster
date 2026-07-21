# CI and release validation

The offline beta gate uses Python 3.12 and performs no live raster requests. It:

1. checks out the repository and sets up Python;
2. installs development and documentation dependencies;
3. builds wheel and source distributions;
4. installs the wheel into a fresh environment;
5. checks installed CLI entry points outside the checkout;
6. compiles Python modules and imports documented public workflow modules;
7. runs the offline doctor, template generation, validation, schema determinism, quick smoke, complete tests, and beta check;
8. builds this site in strict mode;
9. runs `git diff --check` and confirms tracked files did not change.

A separate Pages workflow builds the same MkDocs site strictly, uploads only the generated `site/` directory as the official Pages artifact, and deploys through the `github-pages` environment. Automatic and manual deployment jobs are gated to the future `main` branch; this release-preparation branch cannot deploy.

Live integration with USDA, USGS, ArcGIS, STAC, THREDDS, PRISM, or another provider remains a bounded manual concern. CI contains no raster credentials and never authorizes live acquisition.
