"""Procedural level of detail for grid-archive assets (synthetic index; no archive needed)."""
import numpy as np

from backend.assets import select
from backend.app import app


def index(n=20000, seed=3):
    rng = np.random.default_rng(seed)
    score = np.sort(rng.gamma(2., 1., n))[::-1].astype(np.float32)  # sorted by importance, like the file
    return {'x': rng.random(n).astype(np.float32), 'y': rng.random(n).astype(np.float32), 'score': score}


def test_budget_caps_what_is_drawn_and_small_views_show_everything():
    data = index()
    chosen, total = select(data, 0, 0, 1, 1, budget=1000)
    assert total == 20000 and len(chosen) == 1000
    small, total_small = select(data, .5, .5, .52, .52, budget=1000)
    assert len(small) == total_small < 1000


def test_zooming_in_reveals_less_important_assets_without_preset_levels():
    data = index()
    wide, _ = select(data, 0, 0, 1, 1, budget=500)
    close, _ = select(data, .4, .4, .6, .6, budget=500)
    assert data['score'][close].min() < data['score'][wide].min()
    # The most important assets in view are always kept.
    inside = np.flatnonzero((data['x'] >= .4) & (data['x'] <= .6) & (data['y'] >= .4) & (data['y'] <= .6))
    assert set(inside[:50]) <= set(close)


def test_selection_spreads_across_the_view():
    data = index()
    # Pile the most important assets into one corner: the rest of the view must still get some.
    data['x'][:3000] *= .1; data['y'][:3000] *= .1
    chosen, _ = select(data, 0, 0, 1, 1, budget=600)
    corner = ((data['x'][chosen] < .1) & (data['y'][chosen] < .1)).mean()
    assert corner < .5


def test_assets_endpoint_validates_the_view():
    client = app.test_client()
    assert client.get('/api/assets?x0=0&y0=0&x1=1').status_code == 400
    assert client.get('/api/assets?x0=1&y0=0&x1=0&y1=1').status_code == 400
    assert client.get('/api/assets?x0=0&y0=0&x1=1&y1=1&limit=0').status_code == 400
    ok = client.get('/api/assets?x0=0&y0=0&x1=1&y1=1&limit=50').get_json()
    assert ok['shown'] <= 50 and ok['shown'] == len(ok['assets'])
