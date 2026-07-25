from __future__ import annotations

import ast
import hashlib
import importlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


class _LazyModule:
    def __init__(self, module_name: str) -> None:
        self.module_name = module_name
        self._module: Any | None = None

    def __getattr__(self, name: str) -> Any:
        if self._module is None:
            self._module = importlib.import_module(self.module_name)
        return getattr(self._module, name)


np = _LazyModule("numpy")
rasterio = _LazyModule("rasterio")


def raster_copy(*args: Any, **kwargs: Any) -> Any:
    return importlib.import_module("rasterio.shutil").copy(*args, **kwargs)


def Window(*args: Any, **kwargs: Any) -> Any:
    return importlib.import_module("rasterio.windows").Window(
        *args,
        **kwargs,
    )


def raster_aoi_mask(*args: Any, **kwargs: Any) -> Any:
    return importlib.import_module(
        "faster_raster.aoi_geometry"
    ).raster_aoi_mask(*args, **kwargs)


INDEX_REGISTRY_VERSION = "fasterraster.spectral-index-registry/v1"
EXPRESSION_ENGINE_VERSION = "fasterraster.index-expression/v1"
DEFAULT_EPSILON = 1e-6
INDEX_NODATA = -9999.0
SEMANTIC_BANDS = (
    "red",
    "green",
    "blue",
    "nir",
    "red_edge",
    "swir1",
    "swir2",
)
RADIOMETRIC_DATA_LEVELS = (
    "raw_digital_number",
    "radiance",
    "top_of_atmosphere_reflectance",
    "surface_reflectance",
    "unknown",
)
NAIP_BAND_ORDER = ("red", "green", "blue", "nir")
_BAND_ORDER = {name: index for index, name in enumerate(SEMANTIC_BANDS)}
_INDEX_ID = re.compile(r"^[a-z][a-z0-9_]*$")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _hash_document(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class IndexDefinition:
    index_id: str
    display_name: str
    definition_version: str
    formula: str
    operation: str
    required_bands: tuple[str, ...]
    expected_range: tuple[float, float] | None
    clipping: tuple[float, float] | None
    epsilon_policy: str
    output_dtype: str
    valid_mask_policy: str
    directionality: str
    intended_uses: tuple[str, ...]
    unsupported_interpretations: tuple[str, ...]
    reflectance_requirement: str
    raw_digital_number_caveat: str
    parameterized: bool = False

    def __post_init__(self) -> None:
        if not _INDEX_ID.fullmatch(self.index_id):
            raise ValueError(f"invalid spectral index ID: {self.index_id!r}")
        if not self.display_name.strip() or not self.definition_version.strip():
            raise ValueError("index display name and definition version are required")
        if not self.formula.strip() or not self.operation.strip():
            raise ValueError("index formula and operation are required")
        if len(self.required_bands) != len(set(self.required_bands)):
            raise ValueError(f"duplicate required band in {self.index_id}")
        unknown = sorted(set(self.required_bands) - set(SEMANTIC_BANDS))
        if unknown:
            raise ValueError(
                f"unknown semantic band(s) in {self.index_id}: {', '.join(unknown)}"
            )
        ordered = tuple(
            sorted(self.required_bands, key=lambda name: _BAND_ORDER[name])
        )
        if ordered != self.required_bands:
            raise ValueError(
                f"required bands for {self.index_id} must use semantic order"
            )
        for label, bounds in (
            ("expected range", self.expected_range),
            ("clipping", self.clipping),
        ):
            if bounds is not None and (
                not all(math.isfinite(value) for value in bounds)
                or bounds[0] >= bounds[1]
            ):
                raise ValueError(f"invalid {label} for {self.index_id}")
        if self.output_dtype != "float32":
            raise ValueError("spectral indices currently require float32 output")
        if not self.intended_uses or not self.unsupported_interpretations:
            raise ValueError(
                f"{self.index_id} must declare uses and unsupported interpretations"
            )

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "index_id": self.index_id,
            "display_name": self.display_name,
            "definition_version": self.definition_version,
            "formula": self.formula,
            "operation": self.operation,
            "required_bands": list(self.required_bands),
            "expected_range": (
                list(self.expected_range)
                if self.expected_range is not None
                else None
            ),
            "clipping": (
                list(self.clipping) if self.clipping is not None else None
            ),
            "epsilon_policy": self.epsilon_policy,
            "output_dtype": self.output_dtype,
            "valid_mask_policy": self.valid_mask_policy,
            "directionality": self.directionality,
            "intended_uses": list(self.intended_uses),
            "unsupported_interpretations": list(
                self.unsupported_interpretations
            ),
            "reflectance_requirement": self.reflectance_requirement,
            "raw_digital_number_caveat": self.raw_digital_number_caveat,
            "parameterized": self.parameterized,
        }

    @property
    def content_hash(self) -> str:
        return _hash_document(self.canonical_dict())

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.canonical_dict(),
            "canonical_serialization": _canonical_json(self.canonical_dict()),
            "content_sha256": self.content_hash,
        }


class SpectralIndexRegistry:
    def __init__(self, definitions: Iterable[IndexDefinition]) -> None:
        by_id: dict[str, IndexDefinition] = {}
        for definition in definitions:
            if definition.index_id in by_id:
                raise ValueError(
                    f"duplicate spectral index definition: {definition.index_id}"
                )
            by_id[definition.index_id] = definition
        if not by_id:
            raise ValueError("spectral index registry cannot be empty")
        self._definitions = {
            index_id: by_id[index_id] for index_id in sorted(by_id)
        }

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(self._definitions)

    def get(self, index_id: str) -> IndexDefinition:
        try:
            return self._definitions[index_id]
        except KeyError as exc:
            raise ValueError(f"unknown spectral index: {index_id}") from exc

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": INDEX_REGISTRY_VERSION,
            "indices": [
                self._definitions[index_id].as_dict()
                for index_id in self._definitions
            ],
        }
        return {**payload, "registry_sha256": _hash_document(payload)}

    @property
    def sha256(self) -> str:
        return self.as_dict()["registry_sha256"]


_MASK_POLICY = (
    "all required source bands, the source mask, and the analysis AOI must be "
    "valid; non-finite outputs are invalid"
)
_RAW_RATIO_CAVEAT = (
    "Ratios from raw digital numbers can be useful as scene-relative spectral "
    "proxies, but are not independently calibrated surface reflectance and "
    "thresholds may not transfer across dates, mosaics, sensors, or products."
)
_RAW_BAND_CAVEAT = (
    "Raw digital numbers preserve scene values after declared scaling but are "
    "not automatically atmospherically corrected surface reflectance."
)


def _definition(
    index_id: str,
    display_name: str,
    formula: str,
    operation: str,
    required_bands: tuple[str, ...],
    expected_range: tuple[float, float] | None,
    clipping: tuple[float, float] | None,
    directionality: str,
    intended_uses: tuple[str, ...],
    unsupported: tuple[str, ...],
    *,
    reflectance: str = "preferred",
    raw_caveat: str = _RAW_RATIO_CAVEAT,
    epsilon_policy: str = "add_float32_1e-6_to_denominator",
    parameterized: bool = False,
) -> IndexDefinition:
    return IndexDefinition(
        index_id=index_id,
        display_name=display_name,
        definition_version="1",
        formula=formula,
        operation=operation,
        required_bands=required_bands,
        expected_range=expected_range,
        clipping=clipping,
        epsilon_policy=epsilon_policy,
        output_dtype="float32",
        valid_mask_policy=_MASK_POLICY,
        directionality=directionality,
        intended_uses=intended_uses,
        unsupported_interpretations=unsupported,
        reflectance_requirement=reflectance,
        raw_digital_number_caveat=raw_caveat,
        parameterized=parameterized,
    )


BUILTIN_INDEX_REGISTRY = SpectralIndexRegistry(
    (
        _definition(
            "red",
            "Red band",
            "red",
            "band",
            ("red",),
            (0.0, 1.0),
            None,
            "source value; no universal target direction",
            ("general spectral classification feature",),
            ("surface reflectance without declared calibration",),
            reflectance="not_required",
            raw_caveat=_RAW_BAND_CAVEAT,
            epsilon_policy="not_applicable",
        ),
        _definition(
            "green",
            "Green band",
            "green",
            "band",
            ("green",),
            (0.0, 1.0),
            None,
            "source value; no universal target direction",
            ("general spectral classification feature",),
            ("surface reflectance without declared calibration",),
            reflectance="not_required",
            raw_caveat=_RAW_BAND_CAVEAT,
            epsilon_policy="not_applicable",
        ),
        _definition(
            "blue",
            "Blue band",
            "blue",
            "band",
            ("blue",),
            (0.0, 1.0),
            None,
            "source value; no universal target direction",
            ("general spectral classification feature",),
            ("surface reflectance without declared calibration",),
            reflectance="not_required",
            raw_caveat=_RAW_BAND_CAVEAT,
            epsilon_policy="not_applicable",
        ),
        _definition(
            "nir",
            "Near-infrared band",
            "nir",
            "band",
            ("nir",),
            (0.0, 1.0),
            None,
            "source value; no universal target direction",
            ("general spectral classification feature",),
            ("surface reflectance without declared calibration",),
            reflectance="not_required",
            raw_caveat=_RAW_BAND_CAVEAT,
            epsilon_policy="not_applicable",
        ),
        _definition(
            "ndvi",
            "Normalized Difference Vegetation Index",
            "(nir - red) / (nir + red + epsilon)",
            "ndvi",
            ("red", "nir"),
            (-1.0, 1.0),
            (-1.0, 1.0),
            "higher values commonly indicate stronger scene-relative vegetation response",
            ("scene-relative vegetation response", "classification feature"),
            (
                "crop species truth",
                "biomass or yield without calibration",
                "transferable physical threshold across scenes",
            ),
        ),
        _definition(
            "gndvi",
            "Green Normalized Difference Vegetation Index",
            "(nir - green) / (nir + green + epsilon)",
            "gndvi",
            ("green", "nir"),
            (-1.0, 1.0),
            (-1.0, 1.0),
            "higher values commonly indicate stronger scene-relative vegetation response",
            ("scene-relative vegetation response", "classification feature"),
            (
                "crop nitrogen measurement without calibration",
                "canopy water content from four-band NAIP",
            ),
        ),
        _definition(
            "vari",
            "Visible Atmospherically Resistant Index",
            "(green - red) / (green + red - blue + epsilon)",
            "vari",
            ("red", "green", "blue"),
            (-1.0, 1.0),
            (-1.0, 1.0),
            "higher values may indicate scene-relative visible greenness",
            ("visible greenness proxy", "classification feature"),
            ("atmospheric correction", "independent vegetation truth"),
        ),
        _definition(
            "excess_green",
            "Excess green",
            "2 * green - red - blue",
            "excess_green",
            ("red", "green", "blue"),
            (-2.0, 2.0),
            (-2.0, 2.0),
            "higher values indicate greater green-channel excess",
            ("scene-relative green contrast", "classification feature"),
            ("calibrated vegetation condition",),
            epsilon_policy="not_applicable",
        ),
        _definition(
            "brightness",
            "Visible brightness",
            "(red + green + blue) / 3",
            "brightness",
            ("red", "green", "blue"),
            (0.0, 1.0),
            (0.0, 1.0),
            "higher values indicate greater visible-channel brightness",
            ("scene-relative visible brightness", "classification feature"),
            ("material identity or land-use proof",),
            epsilon_policy="not_applicable",
        ),
        _definition(
            "saturation",
            "Visible saturation",
            "(max(red, green, blue) - min(red, green, blue)) / "
            "(max(red, green, blue) + epsilon)",
            "saturation",
            ("red", "green", "blue"),
            (0.0, 1.0),
            (0.0, 1.0),
            "higher values indicate greater visible-channel separation",
            ("scene-relative color saturation", "classification feature"),
            ("material identity or land-use proof",),
        ),
        _definition(
            "green_nir_water_proxy",
            "Green–NIR normalized-difference wet-surface proxy",
            "(green - nir) / (green + nir + epsilon)",
            "green_nir_water_proxy",
            ("green", "nir"),
            (-1.0, 1.0),
            (-1.0, 1.0),
            "higher values may indicate water or wet, low-NIR surfaces in the tested scene",
            ("scene-relative water or wet-surface candidate screening",),
            (
                "NDMI",
                "canopy water content",
                "authoritative water delineation",
            ),
        ),
        _definition(
            "ndmi",
            "Normalized Difference Moisture Index",
            "(nir - swir1) / (nir + swir1 + epsilon)",
            "ndmi",
            ("nir", "swir1"),
            (-1.0, 1.0),
            (-1.0, 1.0),
            "direction depends on target and calibrated interpretation",
            ("moisture-related spectral analysis with a valid SWIR1 band",),
            (
                "calculation from ordinary four-band NAIP",
                "direct canopy water measurement without calibration",
            ),
            reflectance="required_for_physical_interpretation",
        ),
        _definition(
            "nbr",
            "Normalized Burn Ratio",
            "(nir - swir2) / (nir + swir2 + epsilon)",
            "nbr",
            ("nir", "swir2"),
            (-1.0, 1.0),
            (-1.0, 1.0),
            "direction depends on baseline, target, and calibrated interpretation",
            ("burn-related spectral analysis with a valid SWIR2 band",),
            (
                "calculation from ordinary four-band NAIP",
                "fire severity truth without temporal and field evidence",
            ),
            reflectance="required_for_physical_interpretation",
        ),
        _definition(
            "normalized_difference",
            "Generic normalized difference",
            "(band_a - band_b) / (band_a + band_b + epsilon)",
            "normalized_difference",
            (),
            (-1.0, 1.0),
            (-1.0, 1.0),
            "declared by the parameterized band contract",
            ("bounded custom normalized-difference features",),
            ("a named physical phenomenon without a matching band contract",),
            parameterized=True,
        ),
        _definition(
            "target_signature_similarity",
            "Target spectral-signature similarity",
            "1 / (1 + weighted_euclidean_distance(source_scaled_bands, target))",
            "target_signature_similarity",
            (),
            (0.0, 1.0),
            (0.0, 1.0),
            "higher values indicate greater similarity to the declared target vector",
            ("narrow scene-relative target candidate screening",),
            (
                "proof of land use, mining, abandonment, contamination, ownership, or safety",
            ),
            reflectance="preferred",
            parameterized=True,
        ),
    )
)


@dataclass(frozen=True)
class BandEvidence:
    band: int
    semantic_name: str
    dtype: str
    scale: float
    offset: float
    data_level: str
    nodata: float | int | None
    mask_behavior: str

    def __post_init__(self) -> None:
        if self.band < 1:
            raise ValueError("source band positions must be positive")
        if self.semantic_name not in SEMANTIC_BANDS:
            raise ValueError(
                f"unknown source semantic band: {self.semantic_name!r}"
            )
        if not self.dtype.strip():
            raise ValueError("source band dtype evidence is required")
        if not math.isfinite(self.scale) or self.scale <= 0:
            raise ValueError("source band scale must be finite and positive")
        if not math.isfinite(self.offset):
            raise ValueError("source band offset must be finite")
        if self.data_level not in RADIOMETRIC_DATA_LEVELS:
            raise ValueError(
                f"unsupported source radiometric data level: {self.data_level!r}"
            )
        if not self.mask_behavior.strip():
            raise ValueError("source mask behavior evidence is required")

    def as_dict(self) -> dict[str, Any]:
        return {
            "band": self.band,
            "semantic_name": self.semantic_name,
            "dtype": self.dtype,
            "scale": self.scale,
            "offset": self.offset,
            "data_level": self.data_level,
            "nodata": self.nodata,
            "mask_behavior": self.mask_behavior,
        }


@dataclass(frozen=True)
class SourceBandCapabilities:
    source_asset: str
    source_id: str
    acquisition_id: str | None
    source_sha256: str | None
    bands: tuple[BandEvidence, ...]
    declared_band_order: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.source_asset.strip() or not self.source_id.strip():
            raise ValueError("source asset and source ID evidence are required")
        if not self.bands:
            raise ValueError("source band capability evidence cannot be empty")
        if self.source_sha256 is not None and not re.fullmatch(
            r"[0-9a-f]{64}",
            self.source_sha256,
        ):
            raise ValueError("source SHA-256 evidence must be lowercase hexadecimal")
        positions = [item.band for item in self.bands]
        names = [item.semantic_name for item in self.bands]
        if positions != list(range(1, len(self.bands) + 1)):
            raise ValueError("source band positions must be contiguous and one-based")
        if len(names) != len(set(names)):
            raise ValueError("source semantic band names must be unique")
        unknown = sorted(set(names) - set(SEMANTIC_BANDS))
        if unknown:
            raise ValueError(
                "unknown source semantic band(s): " + ", ".join(unknown)
            )
        if tuple(names) != self.declared_band_order:
            raise ValueError("actual source band order differs from declared order")

    @property
    def available_bands(self) -> tuple[str, ...]:
        return tuple(item.semantic_name for item in self.bands)

    @property
    def band_positions(self) -> dict[str, int]:
        return {item.semantic_name: item.band for item in self.bands}

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": "fasterraster.source-band-capabilities/v1",
            "source_asset": self.source_asset,
            "source_id": self.source_id,
            "acquisition_id": self.acquisition_id,
            "source_sha256": self.source_sha256,
            "actual_band_count": len(self.bands),
            "actual_band_order": list(self.available_bands),
            "declared_band_order": list(self.declared_band_order),
            "bands": [item.as_dict() for item in self.bands],
            "evidence_state": "complete_semantic_band_evidence",
        }
        return {**payload, "capability_sha256": _hash_document(payload)}


class IndexCapabilityError(ValueError):
    def __init__(
        self,
        *,
        requested_index: str,
        required_bands: Sequence[str],
        available_bands: Sequence[str],
        source_asset: str,
        evidence_state: str,
        another_configured_source_can_satisfy: bool = False,
    ) -> None:
        self.document = {
            "schema_version": "fasterraster.index-capability-error/v1",
            "status": "INCOMPATIBLE",
            "requested_index": requested_index,
            "required_bands": list(required_bands),
            "available_bands": list(available_bands),
            "missing_bands": [
                band for band in required_bands if band not in available_bands
            ],
            "source_asset": source_asset,
            "evidence_state": evidence_state,
            "another_configured_source_can_satisfy": (
                another_configured_source_can_satisfy
            ),
        }
        super().__init__(
            f"index {requested_index!r} is incompatible with {source_asset!r}; "
            "missing semantic band(s): "
            + ", ".join(self.document["missing_bands"])
        )

    def as_dict(self) -> dict[str, Any]:
        return dict(self.document)


def naip_source_capabilities(
    *,
    source_asset: str = "naip_multispectral",
    source_id: str = "usgs_naip_imageserver",
    acquisition_id: str | None = None,
    source_sha256: str | None = None,
    dtype: str = "uint8",
    nodata: float | int | None = None,
) -> SourceBandCapabilities:
    return SourceBandCapabilities(
        source_asset=source_asset,
        source_id=source_id,
        acquisition_id=acquisition_id,
        source_sha256=source_sha256,
        bands=tuple(
            BandEvidence(
                band=index,
                semantic_name=name,
                dtype=dtype,
                scale=1.0 / 255.0 if dtype == "uint8" else 1.0,
                offset=0.0,
                data_level="raw_digital_number",
                nodata=nodata,
                mask_behavior="shared_dataset_mask",
            )
            for index, name in enumerate(NAIP_BAND_ORDER, start=1)
        ),
        declared_band_order=NAIP_BAND_ORDER,
    )


def source_capabilities_from_raster(
    path: Path,
    *,
    source_asset: str,
    source_id: str,
    acquisition_id: str | None = None,
) -> SourceBandCapabilities:
    with rasterio.open(path) as source:
        raw_order = source.tags().get("FASTERRASTER_BAND_ORDER", "")
        aliases = {
            "near_infrared": "nir",
            "near-infrared": "nir",
            "rededge": "red_edge",
            "swir_1": "swir1",
            "swir_2": "swir2",
        }
        declared = tuple(
            aliases.get(item.strip().lower(), item.strip().lower())
            for item in raw_order.split(",")
            if item.strip()
        )
        if len(declared) != source.count:
            raise ValueError(
                "source semantic band metadata is missing or does not match "
                f"the actual band count for {source_asset}"
            )
        if (
            source_asset == "naip_multispectral"
            and declared != NAIP_BAND_ORDER
        ):
            raise ValueError(
                "NAIP semantic band order must be red, green, blue, nir; "
                f"observed {', '.join(declared)}"
            )
        data_level = source.tags().get(
            "FASTERRASTER_DATA_LEVEL",
            "raw_digital_number" if all(
                dtype == "uint8" for dtype in source.dtypes
            ) else "unknown",
        ).strip().lower()
        for index, dtype in enumerate(source.dtypes):
            declared_scale = float(source.scales[index])
            declared_offset = float(source.offsets[index])
            if (
                data_level == "raw_digital_number"
                and dtype == "uint8"
                and (
                    not math.isclose(declared_scale, 1.0)
                    or not math.isclose(declared_offset, 0.0)
                )
            ):
                raise ValueError(
                    "raw uint8 source declares contradictory scale or offset "
                    f"metadata for band {index + 1}"
                )
        bands = tuple(
            BandEvidence(
                band=index,
                semantic_name=declared[index - 1],
                dtype=source.dtypes[index - 1],
                scale=(
                    1.0 / 255.0
                    if source.dtypes[index - 1] == "uint8"
                    and data_level == "raw_digital_number"
                    else float(source.scales[index - 1])
                ),
                offset=float(source.offsets[index - 1]),
                data_level=data_level,
                nodata=source.nodatavals[index - 1],
                mask_behavior="dataset_mask_and_band_mask",
            )
            for index in range(1, source.count + 1)
        )
    return SourceBandCapabilities(
        source_asset=source_asset,
        source_id=source_id,
        acquisition_id=acquisition_id,
        source_sha256=_sha256_file(path),
        bands=bands,
        declared_band_order=declared,
    )


def validate_index_compatibility(
    index_id: str,
    capabilities: SourceBandCapabilities,
    *,
    registry: SpectralIndexRegistry = BUILTIN_INDEX_REGISTRY,
    required_bands: Sequence[str] | None = None,
    another_configured_source_can_satisfy: bool = False,
) -> dict[str, Any]:
    definition = (
        registry.get(index_id) if required_bands is None else None
    )
    required = tuple(
        required_bands
        if required_bands is not None
        else definition.required_bands
    )
    missing = [
        band for band in required if band not in capabilities.available_bands
    ]
    if missing:
        raise IndexCapabilityError(
            requested_index=index_id,
            required_bands=required,
            available_bands=capabilities.available_bands,
            source_asset=capabilities.source_asset,
            evidence_state=capabilities.as_dict()["evidence_state"],
            another_configured_source_can_satisfy=(
                another_configured_source_can_satisfy
            ),
        )
    return {
        "status": "COMPATIBLE",
        "requested_index": index_id,
        "required_bands": list(required),
        "available_bands": list(capabilities.available_bands),
        "missing_bands": [],
        "source_asset": capabilities.source_asset,
        "evidence_state": capabilities.as_dict()["evidence_state"],
        "another_configured_source_can_satisfy": (
            another_configured_source_can_satisfy
        ),
        "source_capability_sha256": capabilities.as_dict()[
            "capability_sha256"
        ],
    }


def _required_valid_mask(
    bands: Mapping[str, np.ndarray],
    required: Sequence[str],
    source_mask: np.ndarray | None,
) -> np.ndarray:
    if not required:
        raise ValueError("at least one semantic band is required")
    shapes = {np.asarray(bands[name]).shape for name in required}
    if len(shapes) != 1:
        raise ValueError("semantic band arrays must have the same shape")
    shape = next(iter(shapes))
    if len(shape) != 2:
        raise ValueError("semantic band arrays must be two-dimensional")
    valid = np.ones(shape, dtype=bool)
    for name in required:
        valid &= np.isfinite(np.asarray(bands[name]))
    if source_mask is not None:
        mask = np.asarray(source_mask)
        if mask.ndim == 3:
            mask = np.all(mask, axis=0)
        if mask.shape != shape:
            raise ValueError("source mask does not match semantic band arrays")
        valid &= mask.astype(bool)
    return valid


def evaluate_builtin_index(
    index_id: str,
    bands: Mapping[str, np.ndarray],
    *,
    source_mask: np.ndarray | None = None,
    epsilon: float = float(DEFAULT_EPSILON),
    registry: SpectralIndexRegistry = BUILTIN_INDEX_REGISTRY,
) -> tuple[np.ndarray, np.ndarray]:
    definition = registry.get(index_id)
    if definition.parameterized:
        raise ValueError(
            f"index {index_id!r} requires a parameterized operation contract"
        )
    missing = [
        name for name in definition.required_bands if name not in bands
    ]
    if missing:
        raise ValueError(
            f"index {index_id!r} is missing band array(s): {', '.join(missing)}"
        )
    values = {
        name: np.asarray(bands[name], dtype=np.float32)
        for name in definition.required_bands
    }
    valid = _required_valid_mask(values, definition.required_bands, source_mask)
    eps = np.float32(epsilon)
    red = values.get("red")
    green = values.get("green")
    blue = values.get("blue")
    nir = values.get("nir")
    swir1 = values.get("swir1")
    swir2 = values.get("swir2")
    operation = definition.operation
    if operation == "band":
        result = values[index_id]
    elif operation == "ndvi":
        result = (nir - red) / (nir + red + eps)
    elif operation == "gndvi":
        result = (nir - green) / (nir + green + eps)
    elif operation == "vari":
        result = (green - red) / (green + red - blue + eps)
    elif operation == "excess_green":
        result = np.float32(2.0) * green - red - blue
    elif operation == "brightness":
        result = (red + green + blue) / np.float32(3.0)
    elif operation == "saturation":
        maximum = np.maximum(np.maximum(red, green), blue)
        minimum = np.minimum(np.minimum(red, green), blue)
        result = (maximum - minimum) / (maximum + eps)
    elif operation == "green_nir_water_proxy":
        result = (green - nir) / (green + nir + eps)
    elif operation == "ndmi":
        result = (nir - swir1) / (nir + swir1 + eps)
    elif operation == "nbr":
        result = (nir - swir2) / (nir + swir2 + eps)
    else:
        raise ValueError(f"unsupported built-in index operation: {operation}")
    result = np.asarray(result, dtype=np.float32)
    if definition.clipping is not None:
        result = np.clip(
            result,
            np.float32(definition.clipping[0]),
            np.float32(definition.clipping[1]),
        )
    valid &= np.isfinite(result)
    result = result.copy()
    result[~valid] = np.float32(0.0)
    return result, valid


def evaluate_builtin_indices(
    bands: Mapping[str, np.ndarray],
    index_ids: Sequence[str],
    *,
    source_mask: np.ndarray | None = None,
    epsilon: float = float(DEFAULT_EPSILON),
) -> tuple[np.ndarray, np.ndarray]:
    if not index_ids:
        raise ValueError("at least one index is required")
    if len(index_ids) != len(set(index_ids)):
        raise ValueError("spectral index IDs must be unique")
    arrays: list[np.ndarray] = []
    valid: np.ndarray | None = None
    for index_id in index_ids:
        array, index_valid = evaluate_builtin_index(
            index_id,
            bands,
            source_mask=source_mask,
            epsilon=epsilon,
        )
        arrays.append(array)
        valid = index_valid if valid is None else valid & index_valid
    assert valid is not None
    stack = np.stack(arrays).astype(np.float32, copy=False)
    stack[:, ~valid] = np.float32(0.0)
    return stack, valid


@dataclass(frozen=True)
class ParsedIndexExpression:
    original_expression: str
    canonical_expression: str
    required_bands: tuple[str, ...]
    epsilon: float
    maximum_length: int
    maximum_nodes: int
    maximum_depth: int
    maximum_bands: int
    tree: ast.Expression

    @property
    def formula_hash(self) -> str:
        return _hash_document(
            {
                "engine_version": EXPRESSION_ENGINE_VERSION,
                "canonical_expression": self.canonical_expression,
                "required_bands": list(self.required_bands),
                "epsilon": self.epsilon,
            }
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": EXPRESSION_ENGINE_VERSION,
            "original_expression": self.original_expression,
            "canonical_expression": self.canonical_expression,
            "required_bands": list(self.required_bands),
            "normalization": "declared_by_expression",
            "epsilon_policy": (
                "division invalid when abs(denominator) <= epsilon; "
                "normalized_difference adds epsilon to its denominator"
            ),
            "epsilon": self.epsilon,
            "clipping": "only through explicit clip(expression, low, high)",
            "formula_sha256": self.formula_hash,
            "complexity_limits": {
                "maximum_length": self.maximum_length,
                "maximum_nodes": self.maximum_nodes,
                "maximum_depth": self.maximum_depth,
                "maximum_bands": self.maximum_bands,
            },
        }


_ALLOWED_BINARY = {
    ast.Add: "+",
    ast.Sub: "-",
    ast.Mult: "*",
    ast.Div: "/",
}
_ALLOWED_FUNCTIONS = {
    "abs": 1,
    "minimum": 2,
    "maximum": 2,
    "clip": 3,
    "normalized_difference": 2,
}


def _tree_depth(node: ast.AST) -> int:
    children = list(ast.iter_child_nodes(node))
    return 1 if not children else 1 + max(_tree_depth(child) for child in children)


def _canonical_number(value: int | float) -> str:
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError("custom index constants must be finite") from exc
    if not math.isfinite(number):
        raise ValueError("custom index constants must be finite")
    if abs(number) > 1e12:
        raise ValueError("custom index constants must have absolute value <= 1e12")
    if number == 0:
        number = 0.0
    return format(number, ".17g")


def _validate_expression_node(node: ast.AST) -> None:
    if isinstance(node, ast.Expression):
        _validate_expression_node(node.body)
        return
    if isinstance(node, ast.Name):
        if node.id.startswith("__") or node.id not in SEMANTIC_BANDS:
            raise ValueError(f"custom index name is not an allowed band: {node.id!r}")
        return
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(
            node.value, (int, float)
        ):
            raise ValueError("custom index constants must be finite numbers")
        _canonical_number(node.value)
        return
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINARY:
        _validate_expression_node(node.left)
        _validate_expression_node(node.right)
        return
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        _validate_expression_node(node.operand)
        return
    if isinstance(node, ast.Call):
        if (
            not isinstance(node.func, ast.Name)
            or node.func.id not in _ALLOWED_FUNCTIONS
        ):
            raise ValueError("custom index contains an arbitrary function call")
        if node.keywords:
            raise ValueError("custom index functions do not accept keyword arguments")
        expected = _ALLOWED_FUNCTIONS[node.func.id]
        if len(node.args) != expected:
            raise ValueError(
                f"{node.func.id} requires exactly {expected} argument(s)"
            )
        for argument in node.args:
            _validate_expression_node(argument)
        return
    raise ValueError(
        f"custom index syntax is not allowed: {type(node).__name__}"
    )


def _canonical_expression(node: ast.AST) -> str:
    if isinstance(node, ast.Expression):
        return _canonical_expression(node.body)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Constant):
        return _canonical_number(node.value)
    if isinstance(node, ast.UnaryOp):
        return f"(-{_canonical_expression(node.operand)})"
    if isinstance(node, ast.BinOp):
        operator = _ALLOWED_BINARY[type(node.op)]
        return (
            f"({_canonical_expression(node.left)} {operator} "
            f"{_canonical_expression(node.right)})"
        )
    if isinstance(node, ast.Call):
        arguments = ", ".join(_canonical_expression(item) for item in node.args)
        return f"{node.func.id}({arguments})"
    raise AssertionError(f"unexpected validated expression node: {type(node)}")


def parse_index_expression(
    expression: str,
    *,
    epsilon: float = float(DEFAULT_EPSILON),
    maximum_length: int = 512,
    maximum_nodes: int = 96,
    maximum_depth: int = 12,
    maximum_bands: int = 7,
) -> ParsedIndexExpression:
    if not isinstance(expression, str) or not expression.strip():
        raise ValueError("custom index expression must be nonempty text")
    if len(expression) > maximum_length:
        raise ValueError(
            f"custom index expression exceeds maximum length {maximum_length}"
        )
    if not expression.isascii():
        raise ValueError(
            "custom index expressions must use ASCII identifiers and syntax"
        )
    parenthesis_depth = 0
    for character in expression:
        if character == "(":
            parenthesis_depth += 1
            if parenthesis_depth > maximum_depth:
                raise ValueError(
                    "custom index expression exceeds maximum parenthesis "
                    f"depth {maximum_depth}"
                )
        elif character == ")":
            parenthesis_depth -= 1
    if re.search(r"\b0[xXbBoO][0-9A-Fa-f_]+|\d_\d", expression):
        raise ValueError(
            "custom index constants must use ordinary decimal numeric syntax"
        )
    if not math.isfinite(epsilon) or epsilon <= 0:
        raise ValueError("custom index epsilon must be finite and positive")
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        raise ValueError(f"invalid custom index expression: {exc}") from exc
    nodes = list(ast.walk(tree))
    if len(nodes) > maximum_nodes:
        raise ValueError(
            f"custom index expression exceeds maximum AST nodes {maximum_nodes}"
        )
    depth = _tree_depth(tree)
    if depth > maximum_depth:
        raise ValueError(
            f"custom index expression exceeds maximum depth {maximum_depth}"
        )
    _validate_expression_node(tree)
    referenced = {
        node.id for node in nodes if isinstance(node, ast.Name)
        and node.id in SEMANTIC_BANDS
    }
    if not referenced:
        raise ValueError("custom index expression must reference a semantic band")
    if len(referenced) > maximum_bands:
        raise ValueError(
            f"custom index expression exceeds maximum bands {maximum_bands}"
        )
    required = tuple(sorted(referenced, key=lambda name: _BAND_ORDER[name]))
    return ParsedIndexExpression(
        original_expression=expression,
        canonical_expression=_canonical_expression(tree),
        required_bands=required,
        epsilon=float(epsilon),
        maximum_length=maximum_length,
        maximum_nodes=maximum_nodes,
        maximum_depth=maximum_depth,
        maximum_bands=maximum_bands,
        tree=tree,
    )


def _safe_divide(
    numerator: np.ndarray | np.float32,
    denominator: np.ndarray | np.float32,
    epsilon: np.float32,
) -> np.ndarray:
    left, right = np.broadcast_arrays(
        np.asarray(numerator, dtype=np.float32),
        np.asarray(denominator, dtype=np.float32),
    )
    result = np.full(left.shape, np.nan, dtype=np.float32)
    safe = np.abs(right) > epsilon
    np.divide(left, right, out=result, where=safe)
    return result


def _evaluate_expression_node(
    node: ast.AST,
    bands: Mapping[str, np.ndarray],
    epsilon: np.float32,
) -> np.ndarray | np.float32:
    if isinstance(node, ast.Expression):
        return _evaluate_expression_node(node.body, bands, epsilon)
    if isinstance(node, ast.Name):
        return np.asarray(bands[node.id], dtype=np.float32)
    if isinstance(node, ast.Constant):
        return np.float32(float(node.value))
    if isinstance(node, ast.UnaryOp):
        return -_evaluate_expression_node(node.operand, bands, epsilon)
    if isinstance(node, ast.BinOp):
        left = _evaluate_expression_node(node.left, bands, epsilon)
        right = _evaluate_expression_node(node.right, bands, epsilon)
        if isinstance(node.op, ast.Add):
            return np.asarray(left + right, dtype=np.float32)
        if isinstance(node.op, ast.Sub):
            return np.asarray(left - right, dtype=np.float32)
        if isinstance(node.op, ast.Mult):
            return np.asarray(left * right, dtype=np.float32)
        return _safe_divide(left, right, epsilon)
    if isinstance(node, ast.Call):
        arguments = [
            _evaluate_expression_node(item, bands, epsilon)
            for item in node.args
        ]
        assert isinstance(node.func, ast.Name)
        if node.func.id == "abs":
            return np.abs(arguments[0]).astype(np.float32)
        if node.func.id == "minimum":
            return np.minimum(arguments[0], arguments[1]).astype(np.float32)
        if node.func.id == "maximum":
            return np.maximum(arguments[0], arguments[1]).astype(np.float32)
        if node.func.id == "clip":
            low = np.asarray(arguments[1])
            high = np.asarray(arguments[2])
            if low.ndim or high.ndim:
                raise ValueError("clip bounds must be numeric constants")
            if not float(low) < float(high):
                raise ValueError("clip lower bound must be less than upper bound")
            return np.clip(arguments[0], low, high).astype(np.float32)
        if node.func.id == "normalized_difference":
            numerator = np.asarray(arguments[0]) - np.asarray(arguments[1])
            denominator = (
                np.asarray(arguments[0]) + np.asarray(arguments[1]) + epsilon
            )
            return np.clip(
                _safe_divide(
                    numerator,
                    denominator,
                    np.float32(0.0),
                ),
                np.float32(-1.0),
                np.float32(1.0),
            ).astype(np.float32)
    raise AssertionError(f"unexpected validated expression node: {type(node)}")


def evaluate_index_expression(
    parsed: ParsedIndexExpression,
    bands: Mapping[str, np.ndarray],
    *,
    source_mask: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    missing = [name for name in parsed.required_bands if name not in bands]
    if missing:
        raise ValueError(
            "custom index is missing band array(s): " + ", ".join(missing)
        )
    required = {
        name: np.asarray(bands[name], dtype=np.float32)
        for name in parsed.required_bands
    }
    valid = _required_valid_mask(required, parsed.required_bands, source_mask)
    result = _evaluate_expression_node(
        parsed.tree,
        required,
        np.float32(parsed.epsilon),
    )
    array = np.asarray(result, dtype=np.float32)
    if array.ndim == 0:
        array = np.full(next(iter(required.values())).shape, array, dtype=np.float32)
    valid &= np.isfinite(array)
    array = array.copy()
    array[~valid] = np.float32(0.0)
    return array, valid


def normalized_difference(
    band_a: np.ndarray,
    band_b: np.ndarray,
    *,
    epsilon: float = float(DEFAULT_EPSILON),
    source_mask: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    parsed = parse_index_expression(
        "normalized_difference(red, nir)",
        epsilon=epsilon,
    )
    return evaluate_index_expression(
        parsed,
        {"red": band_a, "nir": band_b},
        source_mask=source_mask,
    )


def target_signature_similarity(
    bands: Mapping[str, np.ndarray],
    target: Mapping[str, float],
    *,
    weights: Mapping[str, float] | None = None,
    source_mask: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    if not target:
        raise ValueError("target spectral signature cannot be empty")
    required = tuple(
        sorted(target, key=lambda name: _BAND_ORDER.get(name, 999))
    )
    if any(name not in SEMANTIC_BANDS for name in required):
        raise ValueError("target signature contains an unknown semantic band")
    if any(name not in bands for name in required):
        raise ValueError("target signature source bands are incomplete")
    vector = np.asarray([target[name] for name in required], dtype=np.float32)
    if not np.all(np.isfinite(vector)):
        raise ValueError("target signature values must be finite")
    weight_values = np.asarray(
        [
            (weights or {}).get(name, 1.0)
            for name in required
        ],
        dtype=np.float32,
    )
    if not np.all(np.isfinite(weight_values)) or np.any(weight_values <= 0):
        raise ValueError("target signature weights must be finite and positive")
    arrays = {
        name: np.asarray(bands[name], dtype=np.float32) for name in required
    }
    valid = _required_valid_mask(arrays, required, source_mask)
    stack = np.stack([arrays[name] for name in required])
    difference = stack - vector[:, None, None]
    distance = np.sqrt(
        np.sum(weight_values[:, None, None] * difference * difference, axis=0)
        / np.sum(weight_values)
    ).astype(np.float32)
    similarity = (np.float32(1.0) / (np.float32(1.0) + distance)).astype(
        np.float32
    )
    valid &= np.isfinite(similarity)
    similarity[~valid] = np.float32(0.0)
    contract = {
        "index_id": "target_signature_similarity",
        "definition_version": "1",
        "semantic_band_order": list(required),
        "target_vector": [float(value) for value in vector],
        "band_weights": [float(value) for value in weight_values],
        "formula": (
            "1 / (1 + sqrt(sum(weight * (band - target)^2) / sum(weight)))"
        ),
        "formula_sha256": _hash_document(
            {
                "operation": "weighted_euclidean_similarity",
                "bands": list(required),
                "target": [float(value) for value in vector],
                "weights": [float(value) for value in weight_values],
            }
        ),
    }
    return similarity, valid, contract


def _iter_windows(width: int, height: int, size: int) -> Iterable[Window]:
    for row_off in range(0, height, size):
        for col_off in range(0, width, size):
            yield Window(
                col_off=col_off,
                row_off=row_off,
                width=min(size, width - col_off),
                height=min(size, height - row_off),
            )


class _StreamingStatistics:
    def __init__(self, total_pixels: int, maximum_quantile_samples: int) -> None:
        self.count = 0
        self.nodata_count = 0
        self.minimum = math.inf
        self.maximum = -math.inf
        self.total = 0.0
        self.total_squares = 0.0
        self.sample_stride = max(
            1,
            math.ceil(total_pixels / maximum_quantile_samples),
        )
        self.samples: list[np.ndarray] = []

    def update(
        self,
        values: np.ndarray,
        valid: np.ndarray,
        *,
        global_indices: np.ndarray,
    ) -> None:
        sample = np.asarray(values, dtype=np.float32)[valid]
        self.nodata_count += int((~valid).sum())
        if not sample.size:
            return
        sample64 = sample.astype(np.float64)
        self.count += int(sample.size)
        self.minimum = min(self.minimum, float(sample.min()))
        self.maximum = max(self.maximum, float(sample.max()))
        self.total += float(sample64.sum())
        self.total_squares += float(np.square(sample64).sum())
        chosen = valid & ((global_indices % self.sample_stride) == 0)
        if np.any(chosen):
            self.samples.append(
                np.asarray(values, dtype=np.float32)[chosen].copy()
            )

    def as_dict(self) -> dict[str, Any]:
        if not self.count:
            raise ValueError("index calculation produced no valid pixels")
        mean = self.total / self.count
        variance = max(0.0, self.total_squares / self.count - mean * mean)
        samples = (
            np.concatenate(self.samples)
            if self.samples
            else np.asarray([self.minimum, self.maximum], dtype=np.float32)
        )
        quantiles = np.quantile(
            samples.astype(np.float64),
            (0.02, 0.25, 0.5, 0.75, 0.98),
        )
        return {
            "valid_pixel_count": self.count,
            "nodata_pixel_count": self.nodata_count,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "mean": mean,
            "standard_deviation": math.sqrt(variance),
            "quantiles": {
                key: float(value)
                for key, value in zip(
                    ("p02", "p25", "p50", "p75", "p98"),
                    quantiles,
                    strict=True,
                )
            },
            "quantile_method": (
                "exact_linear"
                if self.sample_stride == 1
                else "deterministic_global_pixel_stride_linear"
            ),
            "quantile_sample_stride": self.sample_stride,
            "quantile_sample_count": int(samples.size),
        }


def calculate_index_cog(
    source_path: Path,
    destination: Path,
    *,
    index_id: str,
    capabilities: SourceBandCapabilities | None = None,
    analysis_aoi_epsg_4326: Mapping[str, Any] | None = None,
    window_size: int = 512,
    maximum_quantile_samples: int = 262_144,
    expression: str | None = None,
) -> dict[str, Any]:
    if window_size < 16 or window_size > 4096:
        raise ValueError("index window size must be between 16 and 4096")
    if maximum_quantile_samples < 1024:
        raise ValueError("maximum quantile samples must be at least 1024")
    observed_capabilities = source_capabilities_from_raster(
        source_path,
        source_asset=(
            capabilities.source_asset
            if capabilities is not None
            else "naip_multispectral"
        ),
        source_id=(
            capabilities.source_id
            if capabilities is not None
            else "usgs_naip_imageserver"
        ),
        acquisition_id=(
            capabilities.acquisition_id
            if capabilities is not None
            else None
        ),
    )
    if capabilities is None:
        capabilities = observed_capabilities
    else:
        if (
            capabilities.source_sha256 is not None
            and capabilities.source_sha256
            != observed_capabilities.source_sha256
        ):
            raise ValueError(
                "source capability evidence is stale: source SHA-256 changed"
            )
        expected_bands = [
            (
                item.band,
                item.semantic_name,
                item.dtype,
                item.scale,
                item.offset,
                item.data_level,
            )
            for item in capabilities.bands
        ]
        observed_bands = [
            (
                item.band,
                item.semantic_name,
                item.dtype,
                item.scale,
                item.offset,
                item.data_level,
            )
            for item in observed_capabilities.bands
        ]
        if (
            capabilities.declared_band_order
            != observed_capabilities.declared_band_order
            or expected_bands != observed_bands
        ):
            raise ValueError(
                "source capability evidence is incomplete, contradictory, "
                "or stale for the current raster"
            )
    parsed_expression = (
        parse_index_expression(expression) if expression is not None else None
    )
    definition = (
        BUILTIN_INDEX_REGISTRY.get(index_id)
        if parsed_expression is None
        else None
    )
    if definition is not None and definition.parameterized:
        raise ValueError("parameterized indices require a specialist contract")
    required_bands = (
        parsed_expression.required_bands
        if parsed_expression is not None
        else definition.required_bands
    )
    compatibility = validate_index_compatibility(
        index_id,
        capabilities,
        required_bands=(
            required_bands if parsed_expression is not None else None
        ),
    )
    index_contract = (
        {
            "index_id": index_id,
            "display_name": index_id.replace("_", " ").title(),
            "definition_version": "custom-expression-v1",
            "formula": parsed_expression.canonical_expression,
            "required_bands": list(parsed_expression.required_bands),
            "output_dtype": "float32",
            "expression": parsed_expression.as_dict(),
            "content_sha256": parsed_expression.formula_hash,
        }
        if parsed_expression is not None
        else definition.as_dict()
    )
    formula_hash = (
        parsed_expression.formula_hash
        if parsed_expression is not None
        else definition.content_hash
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    working = destination.parent / f".{destination.name}.working.tif"
    statistics: _StreamingStatistics
    with rasterio.open(source_path) as source:
        if source.crs is None:
            raise ValueError("index source must declare a CRS")
        if source.count != len(capabilities.bands):
            raise ValueError("index source band count changed after preflight")
        profile = source.profile.copy()
        profile.update(
            driver="GTiff",
            count=1,
            dtype="float32",
            nodata=float(INDEX_NODATA),
            tiled=True,
            blockxsize=512,
            blockysize=512,
            compress="DEFLATE",
            bigtiff="IF_SAFER",
        )
        statistics = _StreamingStatistics(
            source.width * source.height,
            maximum_quantile_samples,
        )
        positions = capabilities.band_positions
        with rasterio.open(working, "w", **profile) as sink:
            sink.update_tags(
                FASTERRASTER_INDEX_ID=index_id,
                FASTERRASTER_INDEX_VERSION=(
                    "custom-expression-v1"
                    if parsed_expression is not None
                    else definition.definition_version
                ),
                FASTERRASTER_FORMULA_SHA256=formula_hash,
                FASTERRASTER_ANALYTICAL_VALUES_MODIFIED_FOR_DISPLAY="false",
            )
            for window in _iter_windows(
                source.width,
                source.height,
                window_size,
            ):
                required_positions = [
                    positions[name] for name in required_bands
                ]
                raw = source.read(required_positions, window=window)
                raw_masks = source.read_masks(required_positions, window=window) > 0
                scaled: dict[str, np.ndarray] = {}
                for array, name, position in zip(
                    raw,
                    required_bands,
                    required_positions,
                    strict=True,
                ):
                    evidence = capabilities.bands[position - 1]
                    scaled[name] = (
                        array.astype(np.float32) * np.float32(evidence.scale)
                        + np.float32(evidence.offset)
                    )
                if parsed_expression is None:
                    values, valid = evaluate_builtin_index(
                        index_id,
                        scaled,
                        source_mask=raw_masks,
                    )
                else:
                    values, valid = evaluate_index_expression(
                        parsed_expression,
                        scaled,
                        source_mask=raw_masks,
                    )
                if analysis_aoi_epsg_4326 is not None:
                    valid &= raster_aoi_mask(
                        source,
                        analysis_aoi_epsg_4326,
                        window=window,
                    )
                output = np.full(values.shape, INDEX_NODATA, dtype=np.float32)
                output[valid] = values[valid]
                sink.write(output, 1, window=window)
                rows = (
                    np.arange(int(window.row_off), int(window.row_off + window.height))
                    [:, None]
                )
                columns = np.arange(
                    int(window.col_off),
                    int(window.col_off + window.width),
                )[None, :]
                global_indices = rows * source.width + columns
                statistics.update(
                    values,
                    valid,
                    global_indices=global_indices,
                )
    raster_copy(
        working,
        destination,
        driver="COG",
        compress="DEFLATE",
        blocksize=512,
        bigtiff="IF_SAFER",
        overview_resampling="average",
    )
    working.unlink(missing_ok=True)
    with rasterio.open(destination) as output:
        layout = output.tags(ns="IMAGE_STRUCTURE").get("LAYOUT")
        if (
            layout != "COG"
            or output.count != 1
            or output.dtypes[0] != "float32"
        ):
            raise ValueError(f"index COG validation failed: {destination}")
        grid = {
            "crs": output.crs.to_string() if output.crs else None,
            "transform": list(output.transform)[:6],
            "width": output.width,
            "height": output.height,
        }
    source_hash = observed_capabilities.source_sha256
    result = {
        "schema_version": "fasterraster.index-calculation-receipt/v1",
        "index": index_contract,
        "registry_version": INDEX_REGISTRY_VERSION,
        "registry_sha256": BUILTIN_INDEX_REGISTRY.sha256,
        "compatibility": compatibility,
        "source_bands": capabilities.as_dict(),
        "source_asset_sha256": source_hash,
        "grid": grid,
        "analysis_aoi_epsg_4326": analysis_aoi_epsg_4326,
        "analysis_aoi_mask_applied": analysis_aoi_epsg_4326 is not None,
        "statistics": statistics.as_dict(),
        "output": {
            "path": destination.name,
            "dtype": "float32",
            "nodata": float(INDEX_NODATA),
            "sha256": _sha256_file(destination),
            "cog_validation": "PASS",
        },
        "display": {
            "analytical_values_modified": False,
            "stretch": None,
        },
    }
    return {**result, "receipt_sha256": _hash_document(result)}
