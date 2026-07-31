from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from faster_raster.public_contract_schemas import (
    credential_requirement_v2_schema,
    source_materialization_request_v2_schema,
    source_pack_plan_v2_schema,
    source_pack_v2_schema,
)
from faster_raster.source_pack import (
    compile_source_materialization_request,
    compile_source_pack_plan,
    load_source_pack,
    validate_source_materialization_request,
    validate_source_pack,
)

ROOT = Path(__file__).resolve().parent.parent
V2_EXAMPLES = ROOT / "examples" / "sauce-packs-v2"


def _base_manifest(asset_access: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": "fasterraster.source-pack/v2",
        "pack_id": "synthetic-stac-v2",
        "display_name": "Synthetic STAC v2",
        "description": "Synthetic contract fixture with no live provider identity.",
        "adapter": {
            "family": "stac_search",
            "endpoint": "https://catalog.example.test",
            "media_types": [
                "application/geo+json",
                "image/tiff; application=geotiff; profile=cloud-optimized",
            ],
            "asset_roles": ["data"],
        },
        "capabilities": {
            "planning": True,
            "preview": False,
            "materialization": False,
            "analysis": False,
            "temporal_discovery": True,
        },
        "source": {
            "semantic_type": "continuous",
            "crs": "EPSG:4326",
            "resampling": "bilinear",
            "nodata": -9999,
            "mask_policy": "explicit_nodata",
        },
        "access": {
            "authentication_scheme": "none",
            "allowed_hosts": ["catalog.example.test"],
            "redirect_hosts": [],
            "asset_hosts": ["data.example.test"],
            "asset_host_suffixes": [],
            "resolver_hosts": (
                ["signer.example.test"]
                if asset_access["mode"] == "brokered_signed_https"
                else []
            ),
            "resolver_host_suffixes": [],
        },
        "asset_access": asset_access,
        "network": {
            "max_requests": 4,
            "max_bytes": 65536,
            "max_asset_bytes": 64000000,
            "max_total_bytes": 128000000,
            "timeout_seconds": 8,
            "maximum_redirects": 0,
            "max_parallel_requests": 2,
        },
        "temporal": {
            "mode": "exact",
            "requested": "2023",
            "tolerance_days": 0,
            "template_variables": {},
        },
        "preview": {
            "template_id": None,
            "role": None,
            "theme": None,
            "target_crs": None,
        },
        "family_contract": {
            "asset_host_scope": ["data.example.test"],
            "asset_selection": {
                "item_limit": 2,
                "item_order": ["datetime", "id"],
                "required_media_types": [
                    "image/tiff; application=geotiff; profile=cloud-optimized"
                ],
                "required_roles": ["data"],
            },
            "bbox_crs": "EPSG:4326",
            "search_path": "/search",
            "temporal_representation": "interval",
        },
        "earth_engine": None,
    }


def _write_pack(
    root: Path,
    asset_access: dict[str, object],
    *,
    manifest_update: dict[str, object] | None = None,
) -> Path:
    pack = root / "synthetic.sauce"
    pack.mkdir(parents=True)
    manifest = _base_manifest(asset_access)
    if manifest_update:
        manifest.update(manifest_update)
    (pack / "sauce.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )
    (pack / "probe_fixture.json").write_text(
        json.dumps(
            {
                "identity": {
                    "provider": "SYNTHETIC",
                    "product": "Synthetic COG",
                    "collection": "synthetic-cog",
                },
                "provider_evidence": {
                    "official_documentation": [],
                    "status": "synthetic",
                },
                "available_times": ["2023"],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return pack


def _direct_access() -> dict[str, object]:
    return {
        "mode": "direct_https",
        "stable_identity": {"href_policy": "selected_stac_asset"},
    }


def test_v2_direct_https_plan_and_request_are_canonical(tmp_path: Path) -> None:
    pack = _write_pack(tmp_path, _direct_access())
    plan = compile_source_pack_plan(pack)
    assert plan["schema_version"] == "fasterraster.source-pack-plan/v2"
    assert plan["asset_access"]["mode"] == "direct_https"
    assert plan["credential_requirement"] is None
    request = compile_source_materialization_request(
        plan,
        requested_asset_roles=["data"],
        bbox=[-101.06, 39.38, -101.04, 39.4],
        bbox_crs="EPSG:4326",
    )
    assert (
        request["schema_version"]
        == "fasterraster.source-materialization-request/v2"
    )
    assert validate_source_materialization_request(plan, request) == request


def test_request_identity_changes_but_content_identity_does_not_for_inert_consent(
    tmp_path: Path,
) -> None:
    plan = compile_source_pack_plan(_write_pack(tmp_path, _direct_access()))
    base = compile_source_materialization_request(
        plan,
        requested_asset_roles=["data"],
        bbox=[-101.06, 39.38, -101.04, 39.4],
        bbox_crs="EPSG:4326",
    )
    consent = compile_source_materialization_request(
        plan,
        requested_asset_roles=["data"],
        bbox=[-101.06, 39.38, -101.04, 39.4],
        bbox_crs="EPSG:4326",
        allow_chargeable_access=True,
    )
    assert (
        base["materialization_content_sha256"]
        == consent["materialization_content_sha256"]
    )
    assert (
        base["materialization_request_sha256"]
        != consent["materialization_request_sha256"]
    )


def test_requester_pays_requires_consent_and_opaque_credential(
    tmp_path: Path,
) -> None:
    access = {
        "mode": "s3_requester_pays",
        "stable_identity": {
            "scheme": "s3",
            "bucket": "usgs-landsat",
            "region": "us-west-2",
            "key_policy": "selected_stac_asset",
        },
        "credential_scheme": "aws_sigv4",
        "billing": {
            "mode": "requester_pays",
            "explicit_study_consent_required": True,
            "explicit_runtime_permission_required": True,
        },
    }
    plan = compile_source_pack_plan(_write_pack(tmp_path, access))
    assert plan["credential_requirement"]["credential_scheme"] == "aws_sigv4"
    kwargs = {
        "requested_asset_roles": ["data"],
        "bbox": [-101.06, 39.38, -101.04, 39.4],
        "bbox_crs": "EPSG:4326",
        "credential_ref": "aws-research-account",
    }
    with pytest.raises(ValueError, match="explicit study consent"):
        compile_source_materialization_request(plan, **kwargs)
    request = compile_source_materialization_request(
        plan,
        **kwargs,
        allow_chargeable_access=True,
        max_network_requests=3,
        max_network_bytes=75_000_000,
    )
    assert request["authorization"]["allow_chargeable_access"] is True
    assert request["authorization"]["credential_ref"] == "aws-research-account"


@pytest.mark.parametrize(
    ("asset_access", "scheme"),
    [
        (
            {
                "mode": "bearer_https",
                "stable_identity": {
                    "href_policy": "unsigned_selected_stac_asset"
                },
                "credential_scheme": "oauth2_bearer",
            },
            "oauth2_bearer",
        ),
        (
            {
                "mode": "brokered_signed_https",
                "stable_identity": {
                    "href_policy": "unsigned_selected_stac_asset"
                },
                "resolver": {
                    "scheme": "ephemeral_https_signer",
                    "endpoint": "https://signer.example.test/sign",
                    "method": "GET",
                    "href_parameter": "href",
                    "response_field": "href",
                },
            },
            "ephemeral_https_signer",
        ),
    ],
)
def test_operation_scoped_credential_requirements(
    tmp_path: Path,
    asset_access: dict[str, object],
    scheme: str,
) -> None:
    plan = compile_source_pack_plan(_write_pack(tmp_path, asset_access))
    assert plan["credential_requirement"]["credential_scheme"] == scheme
    assert "credential_ref" not in plan["credential_requirement"]


def test_source_pack_hash_binds_access_mode_not_probe_fixture(
    tmp_path: Path,
) -> None:
    direct_pack = _write_pack(tmp_path / "direct", _direct_access())
    direct_hash = load_source_pack(direct_pack).source_pack_sha256
    fixture = direct_pack / "probe_fixture.json"
    evidence = json.loads(fixture.read_text(encoding="utf-8"))
    evidence["note"] = "evidence-only mutation"
    fixture.write_text(json.dumps(evidence), encoding="utf-8")
    assert load_source_pack(direct_pack).source_pack_sha256 == direct_hash
    public_s3 = {
        "mode": "s3_public",
        "stable_identity": {
            "scheme": "s3",
            "bucket": "copernicus-dem-30m",
            "region": "eu-central-1",
            "key_policy": "selected_stac_asset",
        },
    }
    public_pack = _write_pack(tmp_path / "s3", public_s3)
    assert load_source_pack(public_pack).source_pack_sha256 != direct_hash


def test_v2_schema_surfaces_are_versioned() -> None:
    assert (
        source_pack_v2_schema()["properties"]["schema_version"]["const"]
        == "fasterraster.source-pack/v2"
    )
    assert (
        source_pack_plan_v2_schema()["properties"]["schema_version"]["const"]
        == "fasterraster.source-pack-plan/v2"
    )
    assert (
        source_materialization_request_v2_schema()["properties"][
            "schema_version"
        ]["const"]
        == "fasterraster.source-materialization-request/v2"
    )
    assert (
        credential_requirement_v2_schema()["properties"]["schema_version"][
            "const"
        ]
        == "fasterraster.credential-requirement/v2"
    )


def test_earth_engine_contract_is_closed_and_requires_grid(
    tmp_path: Path,
) -> None:
    manifest = _base_manifest(_direct_access())
    manifest.update(
        {
            "pack_id": "synthetic-ee-v2",
            "adapter": {
                "family": "earth_engine_compute",
                "endpoint": None,
                "media_types": ["image/tiff"],
                "asset_roles": ["elevation"],
            },
            "access": {
                "authentication_scheme": "none",
                "allowed_hosts": [],
                "redirect_hosts": [],
                "asset_hosts": [],
                "asset_host_suffixes": [],
                "resolver_hosts": [],
                "resolver_host_suffixes": [],
            },
            "asset_access": None,
            "family_contract": {},
            "earth_engine": {
                "dataset_id": "NASA/NASADEM_HGT/001",
                "dataset_type": "image",
                "bands": [
                    {
                        "name": "elevation",
                        "semantic_type": "continuous",
                        "data_type": "int16",
                    }
                ],
                "allowed_operations": ["load_image", "select_bands"],
                "credential_scheme": "google_adc",
                "max_uncompressed_response_bytes": 48_000_000,
                "max_width": 32_000,
                "max_height": 32_000,
                "max_bands": 1_024,
            },
            "source": {
                "semantic_type": "continuous",
                "crs": "EPSG:4326",
                "resampling": "bilinear",
                "nodata": -32768,
                "mask_policy": "explicit_nodata",
            },
        }
    )
    pack = _write_pack(
        tmp_path,
        _direct_access(),
        manifest_update=manifest,
    )
    plan = compile_source_pack_plan(pack)
    with pytest.raises(ValueError, match="complete output grid"):
        compile_source_materialization_request(
            plan,
            requested_asset_roles=["elevation"],
            bbox=[-101.06, 39.38, -101.04, 39.4],
            bbox_crs="EPSG:4326",
            credential_ref="local-google-adc",
            project_ref="earth-engine-research",
            max_compute_requests=1,
        )
    request = compile_source_materialization_request(
        plan,
        requested_asset_roles=["elevation"],
        bbox=[-101.06, 39.38, -101.04, 39.4],
        bbox_crs="EPSG:4326",
        output_width=128,
        output_height=128,
        output_crs="EPSG:32614",
        output_transform=[30, 0, 321600, 0, -30, 4363200],
        credential_ref="local-google-adc",
        project_ref="earth-engine-research",
        max_compute_requests=1,
    )
    assert request["output_grid"]["width"] == 128
    assert request["authorization"]["project_ref"] == "earth-engine-research"


@pytest.mark.parametrize(
    "name",
    [
        "earth-search-copdem-https.sauce",
        "copdem-public-s3.sauce",
        "planetary-computer-signed.sauce",
        "usgs-landsat-requester-pays.sauce",
        "nasa-hls-bearer.sauce",
        "earth-engine-nasadem.sauce",
        "earth-engine-sentinel2.sauce",
        "earth-engine-worldcover.sauce",
    ],
)
def test_canonical_v2_examples_validate_and_compile(name: str) -> None:
    pack = V2_EXAMPLES / name
    validation = validate_source_pack(pack)
    assert validation["status"] == "PASS", validation["errors"]
    plan = compile_source_pack_plan(pack)
    assert plan["schema_version"] == "fasterraster.source-pack-plan/v2"
    assert plan["source_pack_sha256"] == validation["source_pack_sha256"]
    assert plan == json.loads(
        (pack / "golden_plan.json").read_text(encoding="utf-8")
    )
    request = json.loads(
        (pack / "golden_materialization_request.json").read_text(
            encoding="utf-8"
        )
    )
    assert validate_source_materialization_request(plan, request) == request
