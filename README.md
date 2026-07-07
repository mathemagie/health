# data_pas_maps

Export all your Apple Health / Fitness data to JSON, then visualize your workout
GPS routes on an interactive map. JSON output is flat so it drops straight into SQLite.

## 1. Export from your iPhone

1. Open the **Health** app.
2. Tap your **profile picture** (top-right).
3. Scroll down and tap **Export All Health Data**.
4. Share the resulting **`export.zip`** to your Mac (AirDrop) and drop it into this folder.

The zip contains `export.xml` (all records/workouts) and `workout-routes/*.gpx` (GPS traces).

## 2. Parse to JSON

No dependencies — Python 3 stdlib only.

```bash
python3 parser/parse_health.py export.zip -o data/
```

This writes into `data/`:

| File | Contents | SQLite table |
|------|----------|--------------|
| `records.jsonl` | Every HealthKit record, one JSON object per line (steps, distance, heart rate, VO2 max, sleep, …) | `records` |
| `workouts.json` | Workouts with duration, distance, calories, and a link to the route file | `workouts` |
| `activity_summaries.json` | Daily move / exercise / stand rings + goals | `activity_summaries` |
| `record_types.json` | Count of records per type (a quick inventory) | `record_types` |
| `routes/routes_index.json` | One entry per GPS route: sport, date, distance, bbox | `routes` |
| `routes/route_*.geojson` | Each GPS trace as a GeoJSON `LineString` | — (for the map) |

### Schemas

**`records.jsonl`** — one object per line:
```json
{"type":"HKQuantityTypeIdentifierStepCount","unit":"count","value":"512",
 "sourceName":"iPhone","startDate":"2024-05-01T08:00:00+02:00",
 "endDate":"2024-05-01T08:10:00+02:00","creationDate":"2024-05-01T08:10:05+02:00"}
```

**`workouts.json`**:
```json
{"id":0,"activityType":"HKWorkoutActivityTypeRunning","duration":1830.0,
 "durationUnit":"s","distance_km":5.02,"energy_kcal":312.0,
 "startDate":"2024-05-01T18:00:00+02:00","endDate":"2024-05-01T18:30:30+02:00",
 "sourceName":"Apple Watch","route_file":"route_2024-05-01.geojson"}
```

### Load into SQLite

```bash
# records (JSONL)
sqlite3 health.db
.mode json
```
```sql
CREATE TABLE records(type TEXT, unit TEXT, value TEXT, sourceName TEXT,
                     startDate TEXT, endDate TEXT, creationDate TEXT);
```
Then from a shell, stream the JSONL in (example with Python):
```bash
python3 - <<'PY'
import json, sqlite3
db = sqlite3.connect("health.db")
with open("data/records.jsonl") as f:
    rows = (tuple(json.loads(l).values()) for l in f)
    db.executemany("INSERT INTO records VALUES (?,?,?,?,?,?,?)", rows)
db.commit()
PY
```
The array files (`workouts.json`, `activity_summaries.json`, `routes_index.json`) can be
loaded the same way, or with `sqlite3`'s `json_each()` / `.import`.

## 3. Visualize the routes on a map

The map reads the generated JSON, so serve the project over HTTP (not `file://`):

```bash
python3 -m http.server 8000
```

Open **http://localhost:8000/map/**.

- Routes are drawn on OpenStreetMap, color-coded by activity type.
- Click a route for stats (date, distance, duration, calories, pace).
- Filter by activity type and date range; "Fit all routes" reframes the map.

> Uses **Leaflet + OpenStreetMap** (free, no API key). To switch to Google Maps later,
> swap the tile layer in `map/index.html` for a Google Maps JS API layer + key.
