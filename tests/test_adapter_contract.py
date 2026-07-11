from faster_raster.adapter_contract import PlannedRequest


def test_planned_request_redacts_authorization_header():
    row = PlannedRequest(
        request_id="r1",
        task_id="t1",
        source_id="s1",
        adapter="static_http_range",
        acquisition_mode="bounded_http_range",
        source_classification="runnable",
        execution_status="planned_executable",
        deterministic_url="https://example.test/a.zip",
        request_method="GET",
        request_headers_redacted={"Range": "bytes=0-65535", "Authorization": "Bearer secret"},
        temporal_key="20230101",
        spatial_key="EPSG:4326:0,0,1,1",
        expected_content_family="zip",
        expected_magic="zip",
        expected_format="zip",
        max_bytes=65536,
        bounded_request=True,
        credential_required=False,
        auth_profile=None,
        fixture_only=False,
        network_required=True,
        checksum_policy="compute_after_fetch",
        validation_steps=["validate_magic"],
        harmonization_readiness="requires_archive_member_resolution",
    ).to_row()

    assert row["request_headers_redacted"] == {"Range": "bytes=0-65535"}
    assert row["url_sha256"]
