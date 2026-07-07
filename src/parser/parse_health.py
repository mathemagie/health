#!/usr/bin/env python3
"""Apple Health export -> JSON/JSONL + GeoJSON routes.

Reads an Apple Health `export.zip` (from Health app -> Export All Health Data)
and writes flat JSON files, each of which maps 1:1 to a SQLite table.

Usage:
    python3 parser/parse_health.py export.zip -o data/

Stdlib only. Streams export.xml so multi-hundred-MB files parse in constant memory.
"""

import argparse
import json
import math
import os
import sys
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime

GPX_NS = {"gpx": "http://www.topografix.com/GPX/1/1"}


def parse_apple_date(value):
    """Normalize Apple's 'YYYY-MM-DD HH:MM:SS +ZZZZ' to ISO 8601. Returns None if empty."""
    if not value:
        return None
    try:
        # Python's %z accepts '+0200'; Apple uses a space before the offset.
        dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S %z")
        return dt.isoformat()
    except ValueError:
        return value  # keep raw if the format is unexpected


def find_export_xml(zf):
    """Return the name of export.xml inside the zip (handles nested folders)."""
    for name in zf.namelist():
        if name.endswith("export.xml") and not name.endswith("export_cda.xml"):
            return name
    raise FileNotFoundError("export.xml not found inside the zip")


def gpx_members(zf):
    """Map basename -> zip member name for every .gpx under workout-routes/."""
    out = {}
    for name in zf.namelist():
        if name.lower().endswith(".gpx"):
            out[os.path.basename(name)] = name
    return out


def haversine_km(a, b):
    """Great-circle distance in km between two [lon, lat] points."""
    lon1, lat1 = math.radians(a[0]), math.radians(a[1])
    lon2, lat2 = math.radians(b[0]), math.radians(b[1])
    dlon, dlat = lon2 - lon1, lat2 - lat1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(h))


def path_length_km(coords):
    """Total length of a GPS polyline in km."""
    return sum(haversine_km(coords[i - 1], coords[i]) for i in range(1, len(coords)))


def parse_gpx(fileobj):
    """Parse a GPX track into (coordinates, times, elevations).

    coordinates are [lon, lat] pairs (GeoJSON order).
    """
    coords, times, eles = [], [], []
    for _, elem in ET.iterparse(fileobj):
        tag = elem.tag.split("}")[-1]
        if tag == "trkpt":
            lat = elem.get("lat")
            lon = elem.get("lon")
            if lat is None or lon is None:
                elem.clear()
                continue
            coords.append([float(lon), float(lat)])
            t = elem.find("gpx:time", GPX_NS)
            times.append(t.text if t is not None else None)
            e = elem.find("gpx:ele", GPX_NS)
            eles.append(float(e.text) if (e is not None and e.text) else None)
            elem.clear()
    return coords, times, eles


def main():
    ap = argparse.ArgumentParser(description="Convert an Apple Health export.zip to JSON.")
    ap.add_argument("zip", help="Path to export.zip from the Health app")
    ap.add_argument("-o", "--out", default="data", help="Output directory (default: data)")
    args = ap.parse_args()

    if not os.path.isfile(args.zip):
        sys.exit(f"error: file not found: {args.zip}")

    out = args.out
    routes_dir = os.path.join(out, "routes")
    os.makedirs(routes_dir, exist_ok=True)

    zf = zipfile.ZipFile(args.zip)
    export_name = find_export_xml(zf)
    gpx_map = gpx_members(zf)

    records_path = os.path.join(out, "records.jsonl")
    type_counts = Counter()
    n_records = 0
    workouts = []
    summaries = []
    # remember the route FilePath declared inside each workout so we can link it
    pending_route_for_workout = {}

    print(f"Reading {export_name} ...")

    with open(records_path, "w") as rec_f:
        # Stream the XML. We track Workout context so nested <WorkoutRoute> /
        # <MetadataEntry> can be attached to the enclosing workout.
        context = ET.iterparse(zf.open(export_name), events=("start", "end"))
        current_workout = None

        for event, elem in context:
            tag = elem.tag

            if event == "start":
                if tag == "Workout":
                    current_workout = {
                        "id": len(workouts),
                        "activityType": elem.get("workoutActivityType"),
                        "duration": _f(elem.get("duration")),
                        "durationUnit": elem.get("durationUnit"),
                        "distance_km": None,
                        "energy_kcal": None,
                        "startDate": parse_apple_date(elem.get("startDate")),
                        "endDate": parse_apple_date(elem.get("endDate")),
                        "sourceName": elem.get("sourceName"),
                        "route_file": None,
                    }
                    # totalDistance / totalEnergyBurned live as attributes on older
                    # exports and as child <WorkoutStatistics> on newer ones.
                    td = elem.get("totalDistance")
                    if td is not None:
                        current_workout["distance_km"] = _f(td)
                    te = elem.get("totalEnergyBurned")
                    if te is not None:
                        current_workout["energy_kcal"] = _f(te)
                continue

            # event == "end"
            if tag == "Record":
                n_records += 1
                rtype = elem.get("type")
                type_counts[rtype] += 1
                rec = {
                    "type": rtype,
                    "unit": elem.get("unit"),
                    "value": elem.get("value"),
                    "sourceName": elem.get("sourceName"),
                    "startDate": parse_apple_date(elem.get("startDate")),
                    "endDate": parse_apple_date(elem.get("endDate")),
                    "creationDate": parse_apple_date(elem.get("creationDate")),
                }
                rec_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                elem.clear()

            elif tag == "WorkoutStatistics" and current_workout is not None:
                st = elem.get("type") or ""
                if st.endswith("DistanceWalkingRunning") or st.endswith("DistanceCycling"):
                    current_workout["distance_km"] = _f(elem.get("sum"))
                elif st.endswith("ActiveEnergyBurned"):
                    current_workout["energy_kcal"] = _f(elem.get("sum"))
                elem.clear()

            elif tag == "FileReference" and current_workout is not None:
                # <WorkoutRoute><FileReference path="/workout-routes/route_x.gpx"/>
                path = elem.get("path")
                if path:
                    pending_route_for_workout[current_workout["id"]] = os.path.basename(path)
                elem.clear()

            elif tag == "Workout":
                workouts.append(current_workout)
                current_workout = None
                elem.clear()

            elif tag == "ActivitySummary":
                summaries.append({
                    "date": elem.get("dateComponents"),
                    "activeEnergyBurned": _f(elem.get("activeEnergyBurned")),
                    "activeEnergyBurnedGoal": _f(elem.get("activeEnergyBurnedGoal")),
                    "activeEnergyBurnedUnit": elem.get("activeEnergyBurnedUnit"),
                    "exerciseMinutes": _f(elem.get("appleExerciseTime")),
                    "exerciseMinutesGoal": _f(elem.get("appleExerciseTimeGoal")),
                    "standHours": _f(elem.get("appleStandHours")),
                    "standHoursGoal": _f(elem.get("appleStandHoursGoal")),
                })
                elem.clear()

    # ---- Convert GPX routes to GeoJSON and build the index ----
    print(f"Found {len(gpx_map)} GPX route file(s). Converting ...")
    routes_index = []
    # Reverse-lookup: gpx basename -> workout id (for stats in the route index)
    route_to_workout = {v: k for k, v in pending_route_for_workout.items()}

    for basename, member in sorted(gpx_map.items()):
        try:
            with zf.open(member) as fh:
                coords, times, eles = parse_gpx(fh)
        except ET.ParseError:
            continue
        if not coords:
            continue

        route_id = os.path.splitext(basename)[0]
        wid = route_to_workout.get(basename)
        wk = workouts[wid] if wid is not None and wid < len(workouts) else None

        lons = [c[0] for c in coords]
        lats = [c[1] for c in coords]
        bbox = [min(lons), min(lats), max(lons), max(lats)]
        # Distance computed from the GPS trace itself. Apple's recorded
        # workout distance is often near-zero or missing for these, so the
        # GPS-derived length is the authoritative distance for the map.
        gps_distance_km = round(path_length_km(coords), 3)

        props = {
            "id": route_id,
            "activityType": wk["activityType"] if wk else None,
            "startDate": (wk["startDate"] if wk else times[0]) if (wk or times) else None,
            "distance_km": wk["distance_km"] if wk else None,
            "gps_distance_km": gps_distance_km,
            "energy_kcal": wk["energy_kcal"] if wk else None,
            "duration": wk["duration"] if wk else None,
            "times": times,
            "elevations": eles,
        }

        feature = {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": props,
        }
        with open(os.path.join(routes_dir, f"{route_id}.geojson"), "w") as gf:
            json.dump(feature, gf, ensure_ascii=False)

        if wk is not None:
            wk["route_file"] = f"{route_id}.geojson"

        routes_index.append({
            "id": route_id,
            "file": f"{route_id}.geojson",
            "activityType": props["activityType"],
            "startDate": props["startDate"],
            "distance_km": props["distance_km"],
            "gps_distance_km": gps_distance_km,
            "energy_kcal": props["energy_kcal"],
            "duration": props["duration"],
            "n_points": len(coords),
            "bbox": bbox,
        })

    # ---- Write the flat JSON outputs ----
    _dump(os.path.join(out, "workouts.json"), workouts)
    _dump(os.path.join(out, "activity_summaries.json"), summaries)
    _dump(os.path.join(routes_dir, "routes_index.json"), routes_index)
    _dump(os.path.join(out, "record_types.json"),
          [{"type": t, "count": c} for t, c in type_counts.most_common()])

    # ---- Summary ----
    print("\n=== Summary ===")
    print(f"Records:            {n_records:>10,}  -> {records_path}")
    print(f"Record types:       {len(type_counts):>10,}  -> record_types.json")
    print(f"Workouts:           {len(workouts):>10,}  -> workouts.json")
    print(f"Activity summaries: {len(summaries):>10,}  -> activity_summaries.json")
    print(f"GPS routes:         {len(routes_index):>10,}  -> routes/*.geojson")
    if type_counts:
        print("\nTop record types:")
        for t, c in type_counts.most_common(10):
            short = t.replace("HKQuantityTypeIdentifier", "").replace("HKCategoryTypeIdentifier", "")
            print(f"  {c:>9,}  {short}")
    print(f"\nDone. Output in: {os.path.abspath(out)}")


def _f(v):
    """Best-effort float() that returns None on failure/empty."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _dump(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
