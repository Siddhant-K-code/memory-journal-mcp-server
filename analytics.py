"""Bounded, read-only photo metadata analysis. No image or GPS reads."""

import math
from collections import Counter
from datetime import timedelta
from itertools import combinations

MAX_ASSETS = 5000
SCORE_FIELDS = (
    "overall",
    "curation",
    "sharply_focused_subject",
    "pleasant_composition",
    "pleasant_lighting",
    "noise",
    "low_light",
)
MODE_FIELDS = ("selfie", "live_photo", "portrait", "panorama", "hdr", "burst")


def finite(value, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or (positive and value <= 0):
        return None
    return value


def city_name(photo):
    """Never substitute a full place name for a missing city field."""
    place = getattr(photo, "place", None)
    address = getattr(place, "address", None)
    return getattr(address, "city", None) or None


def metadata(photo, *, zone=None, location_mode="none", person_names=(), include_labels=False):
    taken = photo.date.astimezone(zone) if zone else photo.date
    exif = getattr(photo, "exif_info", None)
    score = getattr(photo, "score", None)
    width = finite(getattr(photo, "width", None), positive=True)
    height = finite(getattr(photo, "height", None), positive=True)
    result = {
        "uuid": photo.uuid,
        "filename": photo.original_filename or photo.filename or photo.uuid,
        "date": taken.isoformat(),
        "day": taken.date().isoformat(),
        "hour": taken.hour,
        "media_type": "video" if photo.ismovie else "photo",
        "width": width,
        "height": height,
        "orientation": (
            "vertical" if height > width else "horizontal" if width > height else "square"
        )
        if width and height
        else "unknown",
        "modes": [name for name in MODE_FIELDS if getattr(photo, name, False)],
        "edited": bool(getattr(photo, "hasadjustments", False)),
        "favorite": bool(getattr(photo, "favorite", False)),
        "original_available": bool(getattr(photo, "path", None)),
        "motion_available": bool(getattr(photo, "path_live_photo", None)),
        "original_bytes": finite(getattr(photo, "original_filesize", None)),
        "camera": getattr(exif, "camera_model", None),
        "lens": getattr(exif, "lens_model", None),
        "iso": finite(getattr(exif, "iso", None), positive=True),
        "aperture": finite(getattr(exif, "aperture", None), positive=True),
        "exposure_seconds": finite(getattr(exif, "shutter_speed", None), positive=True),
        "focal_length_mm": finite(getattr(exif, "focal_length", None), positive=True),
        "duration_seconds": finite(getattr(exif, "duration", None), positive=True)
        if photo.ismovie
        else None,
        "fps": finite(getattr(exif, "fps", None), positive=True) if photo.ismovie else None,
        # Zero may mean uncomputed; report it as unknown, never a worst-image score.
        "scores": {name: finite(getattr(score, name, None)) or None for name in SCORE_FIELDS},
    }
    if location_mode == "city":
        result["city"] = city_name(photo)
    if person_names:
        names = set(getattr(photo, "persons", None) or [])
        result["persons"] = [name for name in person_names if name in names]
    if include_labels:
        result["labels"] = sorted(set(getattr(photo, "labels", None) or []))[:100]
    return result


def histogram(values):
    return dict(sorted(Counter(values).items(), key=lambda pair: (-pair[1], str(pair[0]))))


def capture_groups(records, gap_seconds):
    """Adjacent capture gaps, not scene similarity or time spent at a place."""
    from datetime import datetime

    groups = []
    for row in sorted(
        records, key=lambda r: (datetime.fromisoformat(r["date"]).timestamp(), r["uuid"])
    ):
        now = datetime.fromisoformat(row["date"])
        if not groups or (now - groups[-1]["_last"]).total_seconds() > gap_seconds:
            groups.append({"start": row["date"], "end": row["date"], "uuids": [], "_last": now})
        group = groups[-1]
        group["end"] = row["date"]
        group["uuids"].append(row["uuid"])
        group["_last"] = now
    return [{k: v for k, v in group.items() if k != "_last"} for group in groups]


def crop_retained(width, height, target_ratio):
    """Area retained by a centered fill crop; does not assess the subject."""
    if not width or not height:
        return None
    source = width / height
    return round(min(source / target_ratio, target_ratio / source), 4)


def analyze(
    records, *, start_date, end_date, person_names=(), retake_seconds=5, session_minutes=30
):
    if len(records) > MAX_ASSETS:
        raise ValueError(f"Analysis exceeds {MAX_ASSETS} assets. Narrow the dates or filters.")
    photos = [r for r in records if r["media_type"] == "photo"]
    videos = [r for r in records if r["media_type"] == "video"]
    day = start_date
    daily = []
    while day <= end_date:
        rows = [r for r in records if r["day"] == day.isoformat()]
        daily.append(
            {
                "day": day.isoformat(),
                "photos": sum(r["media_type"] == "photo" for r in rows),
                "videos": sum(r["media_type"] == "video" for r in rows),
            }
        )
        day += timedelta(days=1)
    family = {}
    for name in person_names:
        own = [r for r in photos if name in r.get("persons", [])]
        family[name] = {
            "total": len(own),
            "by_day": histogram(r["day"] for r in own),
            "by_city": histogram(r.get("city") for r in own if r.get("city")),
        }
    cooccurrence = [
        {
            "persons": list(pair),
            "photos": sum(all(n in r.get("persons", []) for n in pair) for r in photos),
        }
        for pair in combinations(person_names, 2)
    ]
    coverage = {
        key: sum(r.get(key) is not None for r in photos)
        for key in ("camera", "lens", "iso", "width", "height", "original_bytes")
    }
    rapid = [g for g in capture_groups(photos, retake_seconds) if len(g["uuids"]) > 1]
    sessions = capture_groups(photos, session_minutes * 60)
    durations = [r["duration_seconds"] for r in videos if r["duration_seconds"] is not None]
    return {
        "scope": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "photos": len(photos),
            "videos": len(videos),
            "assets": len(records),
        },
        "daily": daily,
        "hourly": [
            {"hour": hour, "photos": sum(r["hour"] == hour for r in photos)} for hour in range(24)
        ],
        "day_hour": [
            {
                "day": d["day"],
                "hour": hour,
                "photos": sum(r["day"] == d["day"] and r["hour"] == hour for r in photos),
            }
            for d in daily
            for hour in range(24)
        ],
        "cities": histogram(r["city"] for r in photos if r.get("city")),
        "family": family,
        "family_pairs": cooccurrence,
        "all_requested_people": sum(
            all(n in r.get("persons", []) for n in person_names) for r in photos
        )
        if person_names
        else None,
        "camera": histogram(r["camera"] for r in photos if r["camera"]),
        "lens": histogram(r["lens"] for r in photos if r["lens"]),
        "modes": {mode: sum(mode in r["modes"] for r in photos) for mode in MODE_FIELDS},
        "orientations": histogram(r["orientation"] for r in photos),
        "themes": dict(
            list(histogram(label for r in photos for label in r.get("labels", [])).items())[:30]
        ),
        "coverage": coverage,
        "score_coverage": {
            name: sum(r["scores"].get(name) is not None for r in photos) for name in SCORE_FIELDS
        },
        "edited": sum(r["edited"] for r in photos),
        "favorites": sum(r["favorite"] for r in photos),
        "originals_available": sum(r["original_available"] for r in records),
        "motion_available": sum(r["motion_available"] for r in photos),
        "photo_original_bytes": sum(r["original_bytes"] or 0 for r in photos),
        "video_original_bytes": sum(r["original_bytes"] or 0 for r in videos),
        "video_duration_seconds": sum(durations),
        "video_duration_coverage": len(durations),
        "retakes": {
            "gap_seconds": retake_seconds,
            "stacks": len(rapid),
            "photos_in_stacks": sum(len(g["uuids"]) for g in rapid),
            "adjacent_pairs": sum(len(g["uuids"]) - 1 for g in rapid),
        },
        "sessions": {"gap_minutes": session_minutes, "count": len(sessions)},
        "crops": {
            name: {
                "mean_area_retained": round(sum(values) / len(values), 4) if values else None,
                "lose_over_quarter": sum(value < 0.75 for value in values),
                "coverage": len(values),
            }
            for name, ratio in (("4:5", 0.8), ("1:1", 1), ("9:16", 9 / 16))
            if (
                values := [
                    value
                    for r in photos
                    if (value := crop_retained(r["width"], r["height"], ratio)) is not None
                ]
            )
        },
        "notes": [
            "People and scene labels are existing Photos analysis and may be incomplete or wrong.",
            "Rapid capture groups are candidates, not confirmed visual duplicates.",
            "Capture gaps do not measure activity duration, time at a place, or travel distance.",
            "Aesthetic scores are undocumented hints; zero/unavailable values are unknown.",
            "Original byte counts exclude Live Photo motion companions, edits, and caches.",
            "EXIF values are Photos' cached import metadata, not a fresh original-file read.",
        ],
    }
