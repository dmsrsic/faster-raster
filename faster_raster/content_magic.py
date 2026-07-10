from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ContentMagic:
    magic: str
    content_family: str


def detect_content_magic(data: bytes, content_type: str | None = None) -> ContentMagic:
    sample = data[:512]
    stripped = sample.lstrip()
    lowered_type = (content_type or "").lower()

    if sample.startswith(b"PK"):
        return ContentMagic("zip", "zip")
    if sample.startswith(b"\x1f\x8b"):
        return ContentMagic("gzip", "gzip")
    if sample.startswith(b"CDF"):
        return ContentMagic("netcdf", "netcdf")
    if sample.startswith(b"\x89HDF"):
        return ContentMagic("hdf5", "hdf5")
    if sample.startswith((b"II*\x00", b"MM\x00*")):
        return ContentMagic("tiff", "tiff")
    if stripped.startswith((b"{", b"[")):
        return ContentMagic("json", "json")
    if stripped.startswith(b"<"):
        if b"<html" in stripped[:128].lower() or "html" in lowered_type:
            return ContentMagic("html", "html")
        return ContentMagic("xml", "xml")
    if sample.startswith(b"GRIB"):
        return ContentMagic("grib", "grib")
    if "application/octet-stream" in lowered_type:
        return ContentMagic("octet_stream", "octet_stream")
    return ContentMagic("unknown", "unknown")

