import pytest

from faster_raster import task_compiler


def test_prism_time_dates_render_exact_temporal_key_and_url():
    task = {
        "task_id": "prism_date_regression",
        "name": "PRISM date regression",
        "aoi": {
            "bbox": [-88.55, 39.75, -87.75, 40.45],
            "bbox_crs": "EPSG:4326",
        },
        "target_grid": {"crs": "EPSG:5070", "resolution_m": 4000},
        "time": {"years": [2019], "dates": ["2019-06-09"]},
        "themes": ["precipitation"],
        "sources": ["prism_daily_ppt_static_zip"],
    }

    rows = task_compiler.plan_task_requests(task)

    assert len(rows) == 1
    assert rows[0]["temporal_key"] == "20190609"
    assert rows[0]["deterministic_url"] == (
        "https://data.prism.oregonstate.edu/time_series/us/an/4km/ppt/"
        "daily/2019/prism_ppt_us_25m_20190609.zip"
    )


def test_task_date_rejects_year_mismatch():
    with pytest.raises(ValueError, match="task year/date mismatch"):
        task_compiler._date_parts(
            {
                "time": {
                    "years": [2019],
                    "dates": ["2023-06-09"],
                }
            }
        )


def test_task_date_uses_configured_year_for_january_fallback():
    assert task_compiler._date_parts({"time": {"years": [2021]}}) == {
        "date": "2021-01-01",
        "year": 2021,
        "month": "01",
        "day": "01",
        "yyyymmdd": "20210101",
    }
