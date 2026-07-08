from __future__ import annotations

from pathlib import Path

import pytest

from faster_raster.auth_profiles import (
    assert_no_live_authenticated_request,
    load_auth_profiles,
    redact_auth_profile,
    validate_auth_profile,
    validate_auth_profiles,
)

EXAMPLE = Path('/home/dmsrsic/raster-work/faster-raster/configs/auth_profiles.example.yaml')


def test_example_profiles_validate():
    profiles = load_auth_profiles(EXAMPLE)
    assert len(profiles) >= 5
    assert validate_auth_profiles(profiles) == []


def test_rejects_raw_secret_looking_values():
    profile = load_auth_profiles(EXAMPLE)[0]
    profile['notes'] = 'token=abcdefghi12345'
    errors = validate_auth_profile(profile)
    assert any('raw secret-like value' in error for error in errors)


def test_redacts_secret_references():
    profile = load_auth_profiles(EXAMPLE)[1]
    redacted = redact_auth_profile(profile)
    assert redacted['required_env_vars'] == ['<ENV_REF>', '<ENV_REF>']
    assert 'EARTHDATA_PASSWORD' not in str(redacted)


def test_scaffold_profiles_cannot_execute_authenticated_requests():
    profile = load_auth_profiles(EXAMPLE)[1]
    with pytest.raises(RuntimeError, match='scaffold-only'):
        assert_no_live_authenticated_request(profile)
