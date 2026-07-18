from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping


ANNUAL_NLCD_MAPPING_ID = "annual_nlcd_development_v1"
USDA_CDL_MAPPING_ID = "usda_cdl_development_proxy_v1"
ANNUAL_NLCD_SOURCE_ID = "usgs_annual_nlcd"
USDA_CDL_SOURCE_ID = "usda_nass_cdl_imageserver"
USGS_NAIP_SOURCE_ID = "usgs_naip_imageserver"


@dataclass(frozen=True)
class DevelopmentMapping:
    mapping_id: str
    source_id: str
    source_semantic_type: str
    scientific_claim: str
    class_labels: Mapping[int, str]
    developed_ranks: Mapping[int, int]
    invalid_codes: tuple[int, ...]
    contract_version: str = "1"

    @property
    def valid_codes(self) -> tuple[int, ...]:
        return tuple(sorted(self.class_labels))

    @property
    def nodata_code(self) -> int:
        return self.invalid_codes[0]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "fasterraster.development-source-mapping/v1",
            "mapping_id": self.mapping_id,
            "contract_version": self.contract_version,
            "source_id": self.source_id,
            "source_semantic_type": self.source_semantic_type,
            "scientific_claim": self.scientific_claim,
            "valid_classes": {str(code): self.class_labels[code] for code in self.valid_codes},
            "developed_states": {
                str(code): {
                    "label": self.class_labels[code],
                    "intensity_rank": int(rank),
                }
                for code, rank in sorted(self.developed_ranks.items())
            },
            "invalid_codes": list(self.invalid_codes),
            "unknown_value_policy": "invalid_classification",
        }

    @property
    def sha256(self) -> str:
        payload = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


NLCD_CLASS_LABELS = {
    11: "open_water",
    12: "perennial_ice_snow",
    21: "developed_open_space",
    22: "developed_low_intensity",
    23: "developed_medium_intensity",
    24: "developed_high_intensity",
    31: "barren_land",
    41: "deciduous_forest",
    42: "evergreen_forest",
    43: "mixed_forest",
    52: "shrub_scrub",
    71: "grassland_herbaceous",
    81: "pasture_hay",
    82: "cultivated_crops",
    90: "woody_wetlands",
    95: "emergent_herbaceous_wetlands",
}


# Non-null class names returned by the USDA CDL ImageServer raster attribute
# table. Background 0, Clouds/No Data 81, and transparent 255 are invalid.
CDL_CLASS_LABELS = {
    1: "Corn", 2: "Cotton", 3: "Rice", 4: "Sorghum", 5: "Soybeans",
    6: "Sunflower", 10: "Peanuts", 11: "Tobacco", 12: "Sweet Corn",
    13: "Pop or Orn Corn", 14: "Mint", 21: "Barley", 22: "Durum Wheat",
    23: "Spring Wheat", 24: "Winter Wheat", 25: "Other Small Grains",
    26: "Dbl Crop WinWht/Soybeans", 27: "Rye", 28: "Oats", 29: "Millet",
    30: "Speltz", 31: "Canola", 32: "Flaxseed", 33: "Safflower",
    34: "Rape Seed", 35: "Mustard", 36: "Alfalfa",
    37: "Other Hay/Non Alfalfa", 38: "Camelina", 39: "Buckwheat",
    41: "Sugarbeets", 42: "Dry Beans", 43: "Potatoes", 44: "Other Crops",
    45: "Sugarcane", 46: "Sweet Potatoes", 47: "Misc Vegs & Fruits",
    48: "Watermelons", 49: "Onions", 50: "Cucumbers", 51: "Chick Peas",
    52: "Lentils", 53: "Peas", 54: "Tomatoes", 55: "Caneberries",
    56: "Hops", 57: "Herbs", 58: "Clover/Wildflowers",
    59: "Sod/Grass Seed", 60: "Switchgrass", 61: "Fallow/Idle Cropland",
    63: "Forest", 64: "Shrubland", 65: "Barren", 66: "Cherries",
    67: "Peaches", 68: "Apples", 69: "Grapes", 70: "Christmas Trees",
    71: "Other Tree Crops", 72: "Citrus", 74: "Pecans", 75: "Almonds",
    76: "Walnuts", 77: "Pears", 82: "Developed", 83: "Water",
    87: "Wetlands", 88: "Nonag/Undefined", 92: "Aquaculture",
    111: "Open Water", 112: "Perennial Ice/Snow",
    121: "Developed/Open Space", 122: "Developed/Low Intensity",
    123: "Developed/Medium Intensity", 124: "Developed/High Intensity",
    131: "Barren", 141: "Deciduous Forest", 142: "Evergreen Forest",
    143: "Mixed Forest", 152: "Shrubland", 176: "Grassland/Pasture",
    190: "Woody Wetlands", 195: "Herbaceous Wetlands", 204: "Pistachios",
    205: "Triticale", 206: "Carrots", 207: "Asparagus", 208: "Garlic",
    209: "Cantaloupes", 210: "Prunes", 211: "Olives", 212: "Oranges",
    213: "Honeydew Melons", 214: "Broccoli", 215: "Avocados",
    216: "Peppers", 217: "Pomegranates", 218: "Nectarines", 219: "Greens",
    220: "Plums", 221: "Strawberries", 222: "Squash", 223: "Apricots",
    224: "Vetch", 225: "Dbl Crop WinWht/Corn", 226: "Dbl Crop Oats/Corn",
    227: "Lettuce", 228: "Dbl Crop Triticale/Corn", 229: "Pumpkins",
    230: "Dbl Crop Lettuce/Durum Wht", 231: "Dbl Crop Lettuce/Cantaloupe",
    232: "Dbl Crop Lettuce/Cotton", 233: "Dbl Crop Lettuce/Barley",
    234: "Dbl Crop Durum Wht/Sorghum", 235: "Dbl Crop Barley/Sorghum",
    236: "Dbl Crop WinWht/Sorghum", 237: "Dbl Crop Barley/Corn",
    238: "Dbl Crop WinWht/Cotton", 239: "Dbl Crop Soybeans/Cotton",
    240: "Dbl Crop Soybeans/Oats", 241: "Dbl Crop Corn/Soybeans",
    242: "Blueberries", 243: "Cabbage", 244: "Cauliflower", 245: "Celery",
    246: "Radishes", 247: "Turnips", 248: "Eggplants", 249: "Gourds",
    250: "Cranberries", 254: "Dbl Crop Barley/Soybeans",
}


ANNUAL_NLCD_MAPPING = DevelopmentMapping(
    mapping_id=ANNUAL_NLCD_MAPPING_ID,
    source_id=ANNUAL_NLCD_SOURCE_ID,
    source_semantic_type="authoritative_land_cover",
    scientific_claim="Annual NLCD mapped land-cover development-state change",
    class_labels=NLCD_CLASS_LABELS,
    developed_ranks={21: 1, 22: 2, 23: 3, 24: 4},
    invalid_codes=(250,),
)

USDA_CDL_MAPPING = DevelopmentMapping(
    mapping_id=USDA_CDL_MAPPING_ID,
    source_id=USDA_CDL_SOURCE_ID,
    source_semantic_type="crop_focused_development_proxy",
    scientific_claim="USDA CDL-derived mapped development proxy change",
    class_labels=CDL_CLASS_LABELS,
    developed_ranks={121: 1, 122: 2, 123: 3, 124: 4},
    invalid_codes=(0, 81, 255),
)

MAPPINGS = {
    ANNUAL_NLCD_MAPPING_ID: ANNUAL_NLCD_MAPPING,
    USDA_CDL_MAPPING_ID: USDA_CDL_MAPPING,
}


def development_mapping(mapping_id: str) -> DevelopmentMapping:
    try:
        return MAPPINGS[mapping_id]
    except KeyError as exc:
        raise ValueError(f"unknown development mapping: {mapping_id}") from exc


def validate_source_mapping(source_id: str, mapping_id: str) -> DevelopmentMapping:
    mapping = development_mapping(mapping_id)
    if mapping.source_id != source_id:
        raise ValueError(f"mapping {mapping_id} is incompatible with source {source_id}")
    return mapping
