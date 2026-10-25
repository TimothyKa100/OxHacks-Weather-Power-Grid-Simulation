"""GB line-corridor model: geometry, time alignment, deduplication and splits (no local data needed)."""
import numpy as np
import pandas as pd

from failure_model.gb_lines import deduplicate, line_features, route_links, storm_ids
from failure_model.gb_model import assign_split, boundaries


def weather(stamps, gust, direction=0., rain=0., temp=5., cells=2):
    """Same series in every cell the test line crosses."""
    n = len(stamps)
    full = lambda v: np.broadcast_to(np.asarray(v, dtype=float).reshape(-1, 1) if np.ndim(v) else v, (n, cells)).copy()
    return {"wind_gusts_10m": full(gust), "wind_direction_10m": full(direction),
            "precipitation": full(rain), "temperature_2m": full(temp)}


def east_west_line():
    return {"id": "L1", "route": [(-1.30, 52.0), (-1.20, 52.0)]}


def test_route_links_split_length_by_cell_and_keep_bearings():
    links = route_links({"id": "L1", "route": [(-1.40, 52.0), (-1.10, 52.0)]})
    assert set(links.weather_key) == {"52.00_-1.50", "52.00_-1.00"}
    assert abs(links.length_fraction.sum() - 1) < 1e-9
    assert all(abs(b - 90) < 1 for bearings in links.bearings for b in bearings)


def test_crosswind_is_full_gust_across_the_route_and_zero_along_it():
    links = route_links(east_west_line())
    keys = list(links.weather_key)
    stamps = pd.date_range("2024-01-01", periods=30, freq="h", tz="UTC")
    hours = stamps[25:28]
    across = line_features(links, weather(stamps, 100., direction=0.), keys, stamps, hours)
    along = line_features(links, weather(stamps, 100., direction=90.), keys, stamps, hours)
    assert np.allclose(across["crosswind_max_kmh"], 100., atol=.5)
    assert np.allclose(along["crosswind_max_kmh"], 0., atol=1.)


def test_interval_uses_following_stamp_and_history_ends_at_its_start():
    links = route_links(east_west_line())
    keys = list(links.weather_key)
    stamps = pd.date_range("2024-01-01", periods=40, freq="h", tz="UTC")
    gust = np.arange(40, dtype=float)
    rain = np.ones(40)
    f = line_features(links, weather(stamps, gust, rain=rain), keys, stamps, stamps[[30]])
    assert f["gust_max_kmh"][0] == 31  # stamp t+1 = maximum during [t, t+1)
    assert f["gust_prev_3h_max_kmh"][0] == 30  # stamps t-2..t only
    assert f["rain_1h_mm"][0] == 1 and f["rain_prev_24h_mm"][0] == 24


def test_missing_history_stays_missing():
    links = route_links(east_west_line())
    keys = list(links.weather_key)
    stamps = pd.date_range("2024-01-01", periods=40, freq="h", tz="UTC")
    rain = np.ones(40)
    rain[20] = np.nan
    f = line_features(links, weather(stamps, 50., rain=rain), keys, stamps, stamps[[30]])
    assert np.isnan(f["rain_prev_24h_mm"][0])


def test_repeated_feeder_reports_collapse_but_separate_incidents_remain():
    t = pd.to_datetime(["2024-12-07T01:10Z", "2024-12-07T03:00Z", "2024-12-07T12:00Z", "2024-12-07T01:20Z"], utc=True)
    reports = pd.DataFrame({"operator": "SEPD", "network_reference": ["F1", "F1", "F1", "F2"],
                            "event_key": ["a", "b", "c", "d"], "reported_utc": t})
    out = deduplicate(reports)
    assert sorted(out.event_key) == ["a", "c", "d"]
    assert (out.onset_hour == out.reported_utc.dt.floor("h")).all()


def test_split_is_chronological_purged_and_never_cuts_a_storm():
    hours = pd.date_range("2020-01-01", periods=1000, freq="h", tz="UTC")
    storm_start = hours[580]
    windows = pd.DataFrame({"storm_id": ["wind-x"], "start_utc": [storm_start], "end_utc": [hours[640]]})
    rows = pd.DataFrame({"timestamp_utc": hours, "storm_id": storm_ids(hours, windows)})
    bounds = boundaries(hours, windows)
    assert bounds["fit"] == storm_start  # 60% falls inside the storm, so it moves to the storm's start
    rows["split"] = assign_split(rows, bounds)
    storm = rows[rows.storm_id.eq("wind-x") & rows.split.ne("purged")]
    assert storm.split.nunique() == 1
    order = {"fit": 0, "tune": 1, "calibrate": 2, "test": 3}
    kept = rows[rows.split.ne("purged")]
    assert kept.split.map(order).is_monotonic_increasing
    for b in bounds.values():
        near = rows[(rows.timestamp_utc - b).abs() < pd.Timedelta(hours=25)]
        assert near.split.eq("purged").all()
