from __future__ import annotations

from faster_raster.content_magic import detect_content_magic


def test_detect_zip_header():
    assert detect_content_magic(b"PK\x03\x04payload").content_family == "zip"


def test_detect_gzip_header():
    assert detect_content_magic(b"\x1f\x8b\x08payload").magic == "gzip"


def test_detect_netcdf_classic():
    detected = detect_content_magic(b"CDF\x01payload")
    assert detected.magic == "netcdf"
    assert detected.content_family == "netcdf"


def test_detect_hdf5_netcdf4():
    detected = detect_content_magic(b"\x89HDF\r\n\x1a\npayload")
    assert detected.magic == "hdf5"
    assert detected.content_family == "hdf5"


def test_detect_tiff():
    assert detect_content_magic(b"II*\x00payload").magic == "tiff"
    assert detect_content_magic(b"MM\x00*payload").content_family == "tiff"


def test_detect_json():
    assert detect_content_magic(b"  {\"ok\": true}").content_family == "json"
    assert detect_content_magic(b"  [1, 2]").magic == "json"


def test_detect_xml_and_html():
    assert detect_content_magic(b"<?xml version='1.0'?><root/>").magic == "xml"
    assert detect_content_magic(b"  <html><body></body></html>").magic == "html"


def test_detect_grib():
    assert detect_content_magic(b"GRIBpayload").content_family == "grib"


def test_detect_unknown():
    assert detect_content_magic(b"not a known raster header").magic == "unknown"

