# Weather adapter and next-step GDELT integration

## Forecast adapter boundary

The app currently performs **zero external weather calls**. `backend/weather.py:load_profile()` reads `data/sample_weather.json`; `scenarios()` maps its normalized storm center, strength and rain into per-line gusts and shared-severity uncertainty. This is the extension hook, not a forecast integration already completed.

The bundled profile contract is:

```json
{
  "label":"Provider / vintage / synthetic status",
  "source":"Provenance",
  "coordinate_system":"Schematic or explicit geographic CRS",
  "windows":[
    {"hour":1,"center_x":0.1,"center_y":0.35,"strength":0.78,"rain_mm_h":8}
  ]
}
```

For a meaningful live forecast adapter, replace the profile-to-field step with a provider that returns `gust[scenario, hour, branch]` in km/h and `rain[scenario, hour]` or a documented branch-specific extension in mm/h. Keep the uniform trip draws separate and shared between policies. Update energy integration if the window duration differs from one hour. Preserve source, issue time, valid time, member ID, units and whether the fields are observed, forecast or synthetic in every exported experiment.

Suggested implementation sequence:

1. Start with a physically located network. **IEEE 14-bus has no geographic footprint here**, so normalized drawing positions cannot be submitted to a weather API as latitude/longitude. Adopt an open georeferenced network or explicitly construct and label a synthetic geographic placement.
2. Sample forecast-ensemble wind gusts and precipitation along each line corridor at valid times. Validate units and time zones; specify interpolation and missing-data rules.
3. Use provider ensemble members rather than scaling every asset with the current artificial shared-severity multiplier. Retain reproducible cached fixtures for demos and outage conditions.
4. Calibrate failure probability against equipment class and observed storm failure data. Convert hazard rates to per-window probabilities if changing temporal resolution. The current logistic curve is a software demonstration only.
5. Display forecast vintage and staleness, and fail explicitly or fall back to a clearly marked cached sample when upstream data is unavailable. Do not silently rename synthetic fields as forecasts.

## GDELT: context alongside the physics

GDELT's official [DOC 2.0 API introduction](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/) describes a full-text news search API with structured output. It can supply reports about storms, outages, road access, damage or repair progress. News mention volume is not wind speed, ground-truth failure probability or proof that a named branch failed.

**First useful hackathon extension:** add a separate event-context panel beside the existing network. It displays article title, publication/first-seen timestamp, publisher, original URL, extracted place and verification status. It has no automatic effect on the physics or optimizer.

An opt-in backend adapter could request:

```text
GET https://api.gdeltproject.org/api/v2/doc/doc
query=("power outage" OR "transmission line") (storm OR wind) <region terms>
mode=artlist
format=json
maxrecords=50
```

Build query parameters using URL encoding, cap response size, set timeouts, obey provider limits and cache responses. Check the current official API contract and availability before implementation; this prototype has not tested a live query. Use a named UTC look-back window and export the actual retrieval time. Keep server-side configuration separate from the frontend.

Proposed cached article schema:

```json
{
  "event_id":"hash-of-normalized-url-and-time",
  "source_url":"https://publisher.example/article",
  "publisher":"Example publisher",
  "title":"Reported damage in example region",
  "published_at":"ISO-8601 UTC or null",
  "first_seen_at":"ISO-8601 UTC",
  "retrieved_at":"ISO-8601 UTC",
  "place_text":"Region as reported",
  "location":{"latitude":null,"longitude":null,"confidence":0},
  "asset_candidates":[],
  "verification":"unverified report",
  "simulation_effect":"none"
}
```

Then:

1. Deduplicate syndicated stories by canonical URL and similarity; retain independent source counts.
2. Distinguish article source location from the reported event location. Resolve ambiguous place names and record confidence and time uncertainty.
3. Spatially join only against actual geographic asset corridors. With the present IEEE schematic, show regional context without claiming an asset match.
4. Keep extracted article text untrusted. Escape displayed content, retain provenance links and prevent article text from becoming instructions or executable content.
5. Allow a human-confirmed outage observation to enter a **separate conditional scenario** as a forced outage, with a visible provenance badge. Preserve an unconditional weather-only comparison to show the information's effect.
6. Evaluate timeliness and precision against verified incident logs. Avoid future-news leakage when backtesting. Never fit the illustrative fragility directly to raw story counts.

Beyond the hackathon: event assimilation could update evidence about access or restoration time, and independently confirmed outages could condition the current network state. Both require calibration and uncertainty handling. GDELT is an observational context extension; the storm physics remains the core demo.
