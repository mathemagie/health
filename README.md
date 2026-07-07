# health

An interactive dashboard of my Apple Health activity — steps, distance, and active
energy — built with **D3.js**. The data is exported from the iPhone Health app and
reduced to two small, location-free aggregate files.

## 🔗 Live dashboard

**https://mathemagie.github.io/health/steps/**

- **Calendar heatmap** — every day since 2017, colored by activity, with each year's
  daily average.
- **Long-term trend** — a 30-day rolling average showing the multi-year trajectory and
  seasonal waves (gaps in the data are drawn as gaps, not bridged).
- **Patterns** — average activity by hour of day and by day of week.
- **Metric toggle** — switch the whole page between Steps / Distance / Active energy.

Light and dark themes follow your system setting.

## Data published here

Only two aggregate files are in this repo — **no location data, no raw records**.

| File | Contents |
|------|----------|
| [`data/daily_metrics.json`](data/daily_metrics.json) | One row per day: `{date, steps, distance (km), active (kcal)}` |
| [`data/patterns.json`](data/patterns.json) | Average per **hour of day** (0–23) and per **weekday** (Mon–Sun), for each metric |

`daily_metrics.json` — one object per day:

```json
{"date":"2026-07-07","steps":8216,"distance":4.963,"active":216}
```

`patterns.json`:

```json
{
  "hourly":  [{"hour":18,"steps":633,"distance":0.41,"active":19}, ...],
  "weekday": [{"weekday":5,"name":"Sat","steps":6557,"distance":4.18,"active":207}, ...]
}
```

These flat shapes drop straight into SQLite (`.mode json` / `json_each()`).

> **Not published:** GPS workout routes, raw per-sample records, and the original
> `export.zip` stay private on the source machine (excluded via `.gitignore`) because
> they reveal precise location and full health history.

## How the data is produced

`src/parser/parse_health.py` (Python 3, standard library only) reads an Apple Health
`export.zip` and writes the JSON. To regenerate:

```bash
python3 src/parser/parse_health.py export.zip -o data/
```

Get `export.zip` from the iPhone: **Health app → profile picture → Export All Health
Data**, then AirDrop it to your computer.

## Run locally

The dashboard fetches its JSON, so serve over HTTP (not `file://`):

```bash
python3 -m http.server 8000
# open http://localhost:8000/steps/
```

## Layout

```
health/
├── index.html                 # redirects to /steps/
├── steps/index.html           # the D3.js dashboard
├── data/
│   ├── daily_metrics.json      # published
│   └── patterns.json           # published
└── src/parser/parse_health.py  # Apple Health export → JSON
```
