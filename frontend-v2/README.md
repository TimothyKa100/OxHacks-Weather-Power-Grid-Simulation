# GridGuard UK grid map (frontend v2)

A minimal page: a map of the UK with the power grid drawn on it, and a **Simulate** button. Pressing it runs a storm on the backend and replays it on the map as one continuous animation (about 1.8 s per storm hour). The storm glides along a smooth path through the hourly storm centres, growing and fading with its strength. Each trip happens at its own moment with a brief flash. Tripped lines turn dashed, red for weather and orange for overload, and buses that lose power turn red. The MW-without-power figure counts up smoothly.

The backend's results are still hourly. `src/timeline.js` places each recorded state on a continuous clock: weather trips land 0.3 h into their hour, and each overload round follows shortly after. Cascades are instantaneous in the model; the small stagger is only there so you can see them. All colours are computed from that clock, so the map always matches the recorded state at that moment.

It sits next to the original `frontend/` and uses the same Flask API. The backend is unchanged.

## Run it

Start the backend from the repository root, as in the main README:

```bash
.venv/Scripts/python.exe -m backend.app
```

Then, in another terminal:

```bash
cd frontend-v2
npm install
npm run dev
```

Open **http://127.0.0.1:5174**. Vite proxies `/api` to Flask on port 5001. If the backend is not running, the page uses `data/sample_run.json` and says so.

## What it uses from the API

- `GET /api/network`: draws the grid on load.
- `POST /api/experiment` with only a random `seed`: every other setting uses the backend default. It replays `portfolios.none.playback.frames`, the storm hitting the grid with no reinforcement.

Each frame needs `hour`, `shed_mw`, `status[]` (one per branch: `online`, `weather` or `overload`) and `served_by_bus[]` (one per bus). `src/schema.js` checks this and shows a clear message if the format changes.

## Node positions

- If **every** bus has `lat` and `lon`, buses are drawn at those real positions. Weather windows with `lat`/`lon` place the storm the same way.
- Otherwise, the schematic `x`/`y` (0–1) is stretched over England and Wales, and the page notes that the positions are placeholders. This is the case for the current IEEE 14-bus test grid.

## UK outline

`src/uk-outline.json` is the GBR feature from Natural Earth's 1:50m Admin 0 countries (public domain), simplified and pre-projected (equirectangular, scaled by cos of the mid latitude). `src/geo.js` uses the same projection for bus positions. The page makes no map requests at runtime.
