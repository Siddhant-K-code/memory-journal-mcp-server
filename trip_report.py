"""Generate a private, self-contained trip explorer from a bounded Photos selection."""

import argparse
import base64
import hashlib
import io
import json
import re
from pathlib import Path

from PIL import Image, ImageOps

import analytics
import server


def thumbnail(path, size=96, quality=28):
    try:
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image).convert("RGB")
            image.thumbnail((size, size))
            buffer = io.BytesIO()
            image.save(buffer, "JPEG", quality=quality, optimize=True)
        return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    except (OSError, Image.DecompressionBombError):
        return None


def difference_hash(path):
    """Small preview comparison hint; neither identity nor duplicate verification."""
    with Image.open(path) as image:
        image = ImageOps.exif_transpose(image).convert("L").resize((9, 8))
        pixels = [image.getpixel((x, y)) for y in range(8) for x in range(9)]
    value = 0
    for y in range(8):
        for x in range(8):
            value = (value << 1) | (pixels[y * 9 + x] > pixels[y * 9 + x + 1])
    return f"{value:016x}"


def build_data(db, options, *, shortlist=()):
    rows = server.trip_records(db, options)
    start, end = server.validate_trip(options)
    projection = server.disclosure(options)
    aggregate = analytics.analyze(
        rows, start_date=start, end_date=end, person_names=projection["person_names"]
    )
    photos = [r for r in rows if r["media_type"] == "photo"]
    thumbs, larger = {}, {}
    # UUID resolution only; caller-controlled arbitrary image paths are never accepted by MCP.
    by_id = {p.uuid: p for p in db.photos() if server.is_visible(p)}
    for row in photos:
        for path in by_id[row["uuid"]].path_derivatives:
            value = thumbnail(path)
            if value:
                thumbs[row["uuid"]] = value
                row["preview_hash"] = difference_hash(path)
                if row["uuid"] in shortlist:
                    larger[row["uuid"]] = thumbnail(path, 300, 45)
                break
    allowed = {r["uuid"] for r in photos}
    return {
        "aggregate": aggregate,
        "records": rows,
        "thumbnails": thumbs,
        "carousel_images": larger,
        "rapid": [g for g in analytics.capture_groups(photos, 5) if len(g["uuids"]) > 1],
        "sessions": analytics.capture_groups(photos, 1800),
        "shortlist": [u for u in shortlist if u in allowed],
        "scope": {"timezone": options.get("timezone", "capture local")},
        "map": None,
    }


def pack(data):
    """Dictionary keys once; round computed scores without changing original records."""
    result = dict(data)
    keys = list(dict.fromkeys(key for row in data["records"] for key in row))
    result["record_keys"] = keys
    result["records"] = [[row.get(key) for key in keys] for row in data["records"]]
    return result


def render(data, destination, *, standalone=False):
    template = Path(__file__).with_name("trip_explorer.html").read_text()
    encoded = json.dumps(pack(data), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    # Prevent metadata from terminating a script or becoming HTML.
    encoded = encoded.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    if not re.search(r"/\*TRIP_DATA\*/\s*null", template):
        raise ValueError("Report template is missing its data marker.")
    html = re.sub(r"/\*TRIP_DATA\*/\s*null", lambda _: encoded, template, count=1)
    report_id = hashlib.sha256(
        "\n".join(sorted(r["uuid"] for r in data["records"])).encode()
    ).hexdigest()[:16]
    html = html.replace('id="trip-explorer"', f'id="trip-explorer" data-report-id="{report_id}"', 1)
    if not standalone and len(html.encode()) > 1_000_000:
        raise ValueError("Report exceeds 1 MB. Narrow the selection or reduce preview sizes.")
    if standalone:
        html = (
            Path(__file__)
            .with_name("report_shell.html")
            .read_text()
            .replace("<!--TRIP_REPORT-->", html)
        )
    Path(destination).write_text(html)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--timezone")
    parser.add_argument("--location", action="append", default=[])
    parser.add_argument("--person-name", action="append", default=[])
    parser.add_argument("--location-mode", choices=["none", "city"], default="none")
    parser.add_argument("--include-labels", action="store_true")
    parser.add_argument("--selection-json", help="JSON list of UUIDs for an exact selection")
    parser.add_argument("--shortlist-json", help="JSON list of shortlisted UUIDs")
    parser.add_argument("--output", required=True)
    parser.add_argument("--data-json", help="Optional private report data for the local editor")
    parser.add_argument("--media-index", help="Optional PRIVATE UUID-to-file index; never share it")
    parser.add_argument("--preview-size", type=int, choices=[96, 192, 384], default=96)
    parser.add_argument(
        "--standalone",
        action="store_true",
        help="Full local browser report; permits larger previews",
    )
    args = parser.parse_args()
    options = {
        "start_date": args.start_date,
        "end_date": args.end_date,
        "locations": args.location,
        "person_names": args.person_name,
        "location_mode": args.location_mode,
        "include_labels": args.include_labels,
    }
    if args.timezone:
        options["timezone"] = args.timezone
    if args.selection_json:
        options["uuids"] = json.loads(Path(args.selection_json).read_text())
    import jsonschema

    jsonschema.validate(options, server.TOOLS_BY_NAME["get-trip-photo-metadata"].inputSchema)
    server.validate_trip(options)
    server.disclosure(options)
    import os

    import osxphotos

    db = osxphotos.PhotosDB(dbfile=os.environ.get("PHOTO_LIBRARY_PATH"))
    shortlist = json.loads(Path(args.shortlist_json).read_text()) if args.shortlist_json else []
    data = build_data(db, options, shortlist=shortlist)
    if args.preview_size != 96:
        if not args.standalone:
            parser.error("Larger previews require --standalone.")
        by_id = {p.uuid: p for p in db.photos() if server.is_visible(p)}
        for uuid in data["thumbnails"]:
            for path in by_id[uuid].path_derivatives:
                if value := thumbnail(path, args.preview_size, 65):
                    data["thumbnails"][uuid] = value
                    break
    render(data, args.output, standalone=args.standalone)
    if args.data_json:
        Path(args.data_json).write_text(json.dumps(data, separators=(",", ":"), allow_nan=False))
    if args.media_index:
        by_id = {p.uuid: p for p in db.photos() if server.is_visible(p)}
        index = {}
        for row in data["records"]:
            photo = by_id[row["uuid"]]
            index[row["uuid"]] = {"original": photo.path, "motion": photo.path_live_photo}
        Path(args.media_index).write_text(json.dumps(index, indent=2))
    print(f"Private report saved to {args.output}")


if __name__ == "__main__":
    main()
