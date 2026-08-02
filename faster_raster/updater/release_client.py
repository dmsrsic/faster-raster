from __future__ import annotations

import json
import hashlib
import platform
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from .models import ReleaseAsset, ReleaseManifest, UpdateChannel

API_URL = "https://api.github.com/repos/dmsrsic/faster-raster/releases?per_page=20"
ALLOWED_HOSTS = {
    "api.github.com",
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
}
MAX_API_BYTES = 512 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
MAX_REDIRECTS = 2
MANIFEST_NAME = "fasterraster-release-v1.json"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TAG = re.compile(r"^v\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
_REQUIRED_MANIFEST_KEYS = {
    "schema_version",
    "tag",
    "package_version",
    "channel",
    "requires_python",
    "updater_manifest_major",
    "release_notes_url",
    "assets",
}
_REQUIRED_ASSET_KEYS = {"kind", "name", "url", "sha256", "size_bytes"}
_GITHUB_ASSET_DIGEST = re.compile(r"^sha256:([0-9a-f]{64})$")


class ReleaseClientError(ValueError):
    pass


class _BoundedRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self) -> None:
        self.count = 0

    def redirect_request(self, request, response, code, msg, headers, newurl):
        if self.count >= MAX_REDIRECTS:
            raise ReleaseClientError("release metadata exceeded redirect limit")
        parsed = urllib.parse.urlparse(newurl)
        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
            raise ReleaseClientError("release metadata redirect host is not allowlisted")
        self.count += 1
        return super().redirect_request(request, response, code, msg, headers, newurl)


def _fetch(url: str, *, maximum: int, timeout: float = 5.0) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "fasterraster-update/1",
        },
    )
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise ReleaseClientError("release metadata host is not allowlisted")
    redirect_handler = _BoundedRedirect()
    try:
        opener = urllib.request.build_opener(redirect_handler)
        with opener.open(request, timeout=timeout) as response:
            data = response.read(maximum + 1)
    except ReleaseClientError:
        raise
    except (OSError, urllib.error.URLError) as exc:
        raise ReleaseClientError("release metadata request failed") from exc
    if len(data) > maximum:
        raise ReleaseClientError("release metadata exceeds the byte limit")
    return data


def _version(value: Any, *, field: str) -> Version:
    try:
        parsed = Version(str(value))
    except InvalidVersion as exc:
        raise ReleaseClientError(f"release manifest has an invalid {field}") from exc
    if parsed.is_devrelease:
        raise ReleaseClientError(f"release manifest has a development {field}")
    return parsed


def _release_assets(release: dict[str, Any]) -> dict[str, dict[str, Any]]:
    tag = release.get("tag_name")
    if not isinstance(tag, str) or not _TAG.fullmatch(tag):
        raise ReleaseClientError("GitHub release tag is invalid")
    expected_prefix = f"/dmsrsic/faster-raster/releases/download/{tag}/"
    raw_assets = release.get("assets")
    if not isinstance(raw_assets, list) or not raw_assets:
        raise ReleaseClientError("GitHub release assets are missing")
    result: dict[str, dict[str, Any]] = {}
    for item in raw_assets:
        if not isinstance(item, dict):
            raise ReleaseClientError("GitHub release contains an invalid asset")
        name = item.get("name")
        url = item.get("browser_download_url")
        size = item.get("size")
        digest = item.get("digest")
        if (
            not isinstance(name, str)
            or not name
            or name in result
            or not isinstance(url, str)
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size <= 0
        ):
            raise ReleaseClientError("GitHub release contains malformed asset metadata")
        digest_match = _GITHUB_ASSET_DIGEST.fullmatch(str(digest))
        if digest_match is None:
            raise ReleaseClientError("GitHub release asset is missing a sha256 digest")
        parsed_url = urllib.parse.urlparse(url)
        if (
            parsed_url.scheme != "https"
            or parsed_url.hostname != "github.com"
            or parsed_url.query
            or parsed_url.fragment
            or not parsed_url.path.startswith(expected_prefix)
            or parsed_url.path.rsplit("/", 1)[-1] != name
        ):
            raise ReleaseClientError("GitHub release asset URL does not match the selected release")
        result[name] = {
            "name": name,
            "browser_download_url": url,
            "size": size,
            "sha256": digest_match.group(1),
        }
    return result


def _manifest(
    payload: dict[str, Any],
    *,
    release: dict[str, Any],
    release_assets: dict[str, dict[str, Any]],
) -> ReleaseManifest:
    if set(payload) != _REQUIRED_MANIFEST_KEYS:
        raise ReleaseClientError("release manifest has unexpected or missing fields")
    if payload.get("schema_version") != "fasterraster.release-manifest/v1":
        raise ReleaseClientError("unsupported release manifest schema")
    if payload.get("updater_manifest_major") != 1:
        raise ReleaseClientError("unsupported updater manifest major")
    tag = str(payload.get("tag", ""))
    release_tag = str(release.get("tag_name", ""))
    if not _TAG.fullmatch(tag) or tag != release_tag:
        raise ReleaseClientError("release manifest tag does not match GitHub release")
    package_version = _version(payload.get("package_version"), field="package_version")
    try:
        tag_version = Version(tag[1:].replace("-beta.", "b").replace("-rc.", "rc"))
    except InvalidVersion as exc:
        raise ReleaseClientError("release manifest tag is not a valid package version") from exc
    if tag_version != package_version:
        raise ReleaseClientError("release manifest package version does not match its tag")
    try:
        requires_python = str(payload["requires_python"])
        specifier = SpecifierSet(requires_python)
    except (InvalidSpecifier, TypeError) as exc:
        raise ReleaseClientError("release manifest has an invalid Python requirement") from exc
    if not specifier.contains(Version(platform.python_version()), prereleases=True):
        raise ReleaseClientError("release manifest is incompatible with this Python")
    channel = str(payload.get("channel", ""))
    if channel not in {"stable", "beta"}:
        raise ReleaseClientError("release manifest has an invalid channel")
    if channel == "stable" and release.get("prerelease"):
        raise ReleaseClientError("stable manifest is marked prerelease by GitHub")
    release_notes_url = str(payload.get("release_notes_url", ""))
    expected_notes = f"https://github.com/dmsrsic/faster-raster/releases/tag/{tag}"
    if release_notes_url != expected_notes:
        raise ReleaseClientError("release notes URL does not match the release tag")
    assets: list[ReleaseAsset] = []
    seen_names: set[str] = set()
    raw_assets = payload.get("assets")
    if not isinstance(raw_assets, list) or not raw_assets:
        raise ReleaseClientError("release manifest assets must be a non-empty array")
    for item in raw_assets:
        if not isinstance(item, dict) or set(item) != _REQUIRED_ASSET_KEYS:
            raise ReleaseClientError("release manifest contains an invalid asset")
        kind = str(item["kind"])
        if kind not in {"wheel", "sdist", "checksums", "manifest"}:
            raise ReleaseClientError("release manifest contains an invalid asset kind")
        name = str(item["name"])
        digest = str(item["sha256"])
        url = str(item["url"])
        if not name or name in seen_names or not _SHA256.fullmatch(digest):
            raise ReleaseClientError("release manifest contains an invalid asset digest")
        raw_size = item["size_bytes"]
        if isinstance(raw_size, bool) or not isinstance(raw_size, int) or raw_size <= 0:
            raise ReleaseClientError("release manifest contains an invalid asset size")
        size_bytes = raw_size
        parsed_url = urllib.parse.urlparse(url)
        if parsed_url.scheme != "https" or parsed_url.hostname not in ALLOWED_HOSTS:
            raise ReleaseClientError("release asset host is not allowlisted")
        expected_prefix = f"/dmsrsic/faster-raster/releases/download/{tag}/"
        if parsed_url.hostname == "github.com" and not parsed_url.path.startswith(expected_prefix):
            raise ReleaseClientError("release asset URL does not match the release tag")
        if parsed_url.path.rsplit("/", 1)[-1] != name:
            raise ReleaseClientError("release asset URL does not match its name")
        github_asset = release_assets.get(name)
        if github_asset is None:
            raise ReleaseClientError("release manifest asset is not attached to the selected release")
        if (
            url != github_asset["browser_download_url"]
            or size_bytes != github_asset["size"]
            or digest != github_asset["sha256"]
        ):
            raise ReleaseClientError("release manifest asset metadata does not match GitHub")
        if kind == "wheel" and ("faster_raster-" not in name or str(package_version) not in name):
            raise ReleaseClientError("release wheel name does not match package version")
        if kind == "sdist" and ("faster_raster-" not in name or str(package_version) not in name):
            raise ReleaseClientError("release sdist name does not match package version")
        assets.append(ReleaseAsset(kind, name, url, digest, size_bytes))
        seen_names.add(name)
    if not any(asset.kind in {"wheel", "sdist"} for asset in assets):
        raise ReleaseClientError("release manifest contains no installable assets")
    if release.get("draft") or release.get("prerelease") is None:
        raise ReleaseClientError("GitHub release state is incomplete or draft")
    return ReleaseManifest(
        schema_version="fasterraster.release-manifest/v1",
        tag=tag,
        package_version=str(package_version),
        channel=channel,
        requires_python=requires_python,
        updater_manifest_major=1,
        release_notes_url=release_notes_url,
        assets=tuple(assets),
    )


def discover(channel: UpdateChannel) -> tuple[ReleaseManifest, ...]:
    try:
        payload = json.loads(_fetch(API_URL, maximum=MAX_API_BYTES).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseClientError("GitHub release response was not valid JSON") from exc
    if not isinstance(payload, list):
        raise ReleaseClientError("GitHub release response was not an array")
    candidates: list[tuple[Version, dict[str, Any]]] = []
    for release in payload:
        if not isinstance(release, dict) or release.get("draft"):
            continue
        if not isinstance(release.get("prerelease"), bool):
            continue
        if channel == UpdateChannel.STABLE and release["prerelease"]:
            continue
        tag = str(release.get("tag_name", ""))
        if not _TAG.fullmatch(tag):
            continue
        try:
            tag_version = _version(tag[1:], field="release tag")
        except ReleaseClientError:
            continue
        raw_assets = release.get("assets")
        if not isinstance(raw_assets, list) or not any(
            isinstance(asset, dict) and asset.get("name") == MANIFEST_NAME for asset in raw_assets
        ):
            continue
        candidates.append((tag_version, release))
    if not candidates:
        return tuple()
    _, release = max(candidates, key=lambda item: item[0])
    release_assets = _release_assets(release)
    manifest_asset = release_assets.get(MANIFEST_NAME)
    if manifest_asset is None:
        raise ReleaseClientError("selected release has no valid manifest asset")
    try:
        manifest_bytes = _fetch(manifest_asset["browser_download_url"], maximum=MAX_MANIFEST_BYTES)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseClientError("release manifest was not valid JSON") from exc
    if len(manifest_bytes) != manifest_asset["size"]:
        raise ReleaseClientError("release manifest size does not match GitHub")
    if hashlib.sha256(manifest_bytes).hexdigest() != manifest_asset["sha256"]:
        raise ReleaseClientError("release manifest digest does not match GitHub")
    try:
        manifest_payload = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseClientError("release manifest was not valid JSON") from exc
    if not isinstance(manifest_payload, dict):
        raise ReleaseClientError("release manifest was not an object")
    manifest = _manifest(manifest_payload, release=release, release_assets=release_assets)
    if channel == UpdateChannel.STABLE and manifest.channel != "stable":
        raise ReleaseClientError("stable channel received a non-stable manifest")
    return (manifest,)
