from faster_raster.adapters.arcgis_imageserver import ArcgisImageServerAdapter
from faster_raster.adapters.generic_https_template import GenericHttpsTemplateAdapter

__all__ = ["ArcgisImageServerAdapter", "GenericHttpsTemplateAdapter", "ThreddsNcssAdapter"]

from faster_raster.adapters.thredds_ncss import ThreddsNcssAdapter
