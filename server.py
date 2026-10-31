"""Read-only Apple Photos search and previews over MCP stdio."""

import asyncio
import base64
import io
import json
import logging
import os
import re
import threading
from concurrent.futures import Future
from contextlib import suppress
from datetime import date
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import jsonschema
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, ImageContent, TextContent, Tool, ToolAnnotations
from PIL import Image, ImageOps, UnidentifiedImageError
from thefuzz import fuzz

import analytics

logger = logging.getLogger(__name__)
DEFAULT_LIMIT = 25
MAX_LIMIT = 100
MAX_PREVIEW_BYTES = 1024 * 1024
Image.MAX_IMAGE_PIXELS = 40_000_000


class PhotosDBLoader:
    """Load once, on the first data request, without blocking MCP discovery."""

    def __init__(self, factory=None, timeout=60):
        self.factory = factory
        self.timeout = timeout
        self._future: Future | None = None
        self._lock = threading.Lock()

    def _load(self, future):
        try:
            if self.factory is not None:
                db = self.factory()
            else:
                import osxphotos

                library = os.environ.get("PHOTO_LIBRARY_PATH")
                db = osxphotos.PhotosDB(dbfile=library) if library else osxphotos.PhotosDB()
            future.set_result(db)
        except Exception as error:  # noqa: BLE001 - relay third-party errors through the future
            future.set_exception(error)

    async def get(self):
        with self._lock:
            if self._future is None:
                self._future = Future()
                threading.Thread(target=self._load, args=(self._future,), daemon=True).start()
            future = self._future
        try:
            # A timed-out/cancelled caller must not cancel the shared load.
            return await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(future)), self.timeout)
        except TimeoutError as error:
            raise RuntimeError(
                "Photos library is still loading. Retry shortly; "
                "increase the client's tool timeout for large libraries."
            ) from error
        except PermissionError as error:
            raise RuntimeError(
                "macOS denied Photos-library access. Grant the host app access in "
                "System Settings > Privacy & Security > Photos, or Full Disk Access "
                "if direct database access requires it, then restart the MCP server."
            ) from error
        except Exception as error:
            raise RuntimeError(f"Could not load the Photos library: {error}") from error


photos_loader = PhotosDBLoader()
server = Server(
    "smart-photo-journal",
    version="0.3.0",
    instructions=(
        "Search Apple Photos with search-photos and explicit date/place/person filters. "
        "Results are paginated; follow next_offset with the same filters to see more. "
        "Use UUIDs, not filenames, to get-photo-details or get-photo-preview. "
        "People names are Photos tags, not new face identification. Preview images "
        "are for selection; use full-resolution originals for posting. The server "
        "does not export, download from iCloud, modify Photos, or publish anything."
        " Trip analytics require inclusive dates and expose cached metadata only. "
        "City names and selected person tags are off by default and require both "
        "host policy and an explicit request; precise coordinates are never returned."
    ),
)


def is_visible(photo):
    return not photo.hidden and not photo.intrash


def photo_location(photo):
    return (photo.place.name if photo.place else None) or None


def disclosure(options):
    """Client arguments can narrow disclosure; trusted host settings set its ceiling."""
    policy = os.environ.get("PHOTO_LOCATION_POLICY", "none")
    if policy not in {"none", "city"}:
        raise ValueError("PHOTO_LOCATION_POLICY must be none or city.")
    try:
        allowed = json.loads(os.environ.get("PHOTO_ALLOWED_PERSON_NAMES", "[]"))
    except json.JSONDecodeError as error:
        raise ValueError("PHOTO_ALLOWED_PERSON_NAMES must be a JSON list of names.") from error
    if (
        not isinstance(allowed, list)
        or len(allowed) > 10
        or not all(isinstance(n, str) for n in allowed)
    ):
        raise ValueError("PHOTO_ALLOWED_PERSON_NAMES must be a JSON list of at most 10 names.")
    return {
        "location_mode": "city"
        if policy == "city" and options.get("location_mode") == "city"
        else "none",
        "person_names": [n for n in options.get("person_names", []) if n in allowed],
        "include_labels": options.get("include_labels", False),
    }


def photo_summary(photo, options=None):
    projection = disclosure(options or {})
    return {
        "uuid": photo.uuid,
        "filename": photo.original_filename or photo.filename or photo.uuid,
        "date": photo.date.isoformat(),
        "location": analytics.city_name(photo) if projection["location_mode"] == "city" else None,
        "persons": [n for n in projection["person_names"] if n in (photo.persons or [])],
        "media_type": "video" if photo.ismovie else "photo",
        "original_available": bool(photo.path),
    }


def parse_calendar_date(value):
    if value is None:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Dates must use YYYY-MM-DD.")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"Invalid calendar date: {value}") from error


def matches(value, term, fuzzy=False):
    value, term = (value or "").casefold(), term.casefold()
    return term in value or (fuzzy and fuzz.ratio(value, term) > 60)


def normalize_terms(terms):
    if any(not term.strip() for term in terms):
        raise ValueError("Search terms must not be blank.")
    return [term.strip() for term in terms]


def search_parameters(arguments):
    """Validate calendar and term semantics before attempting library access."""
    start = parse_calendar_date(arguments.get("start_date"))
    end = parse_calendar_date(arguments.get("end_date"))
    if start and end and start > end:
        raise ValueError("start_date must be on or before end_date.")
    zone = None
    if "timezone" in arguments:
        try:
            zone = ZoneInfo(arguments["timezone"])
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("timezone must be a valid IANA name, e.g. Asia/Kolkata.") from error
    locations = normalize_terms(arguments.get("locations", []))
    persons = normalize_terms(arguments.get("persons", []))
    labels = normalize_terms(arguments.get("labels", []))
    return start, end, zone, locations, persons, labels


def select_photos(db, arguments):
    """Apply exact calendar bounds and combined criteria before paging results."""
    start, end, zone, locations, persons, labels = search_parameters(arguments)
    fuzzy = arguments.get("fuzzy", False)
    media_type = arguments.get("media_type", "photo")
    requested = set(arguments.get("uuids", []))
    found = []
    for photo in db.photos():
        if not is_visible(photo):
            continue
        if requested and photo.uuid not in requested:
            continue
        if media_type != "all" and photo.ismovie != (media_type == "video"):
            continue
        taken = photo.date
        if zone is not None:
            if taken.tzinfo is None:
                raise ValueError("Cannot apply timezone to a photo with a naive creation date.")
            taken = taken.astimezone(zone)
        day = taken.date()
        if (start and day < start) or (end and day > end):
            continue
        if locations and not any(matches(photo_location(photo), term, fuzzy) for term in locations):
            continue
        if persons and not all(
            any(matches(person, term, fuzzy) for person in photo.persons or []) for term in persons
        ):
            continue
        if labels and not any(
            matches(label, term, fuzzy)
            for label in [*(photo.labels or []), *(photo.keywords or [])]
            for term in labels
        ):
            continue
        found.append(photo)
    found.sort(
        key=lambda photo: (photo.date.timestamp(), photo.uuid),
        reverse=arguments.get("sort", "oldest") == "newest",
    )
    return found


def search_photos(db, arguments):
    found = select_photos(db, arguments)
    offset, limit = arguments.get("offset", 0), arguments.get("limit", DEFAULT_LIMIT)
    page = found[offset : offset + limit]
    next_offset = offset + len(page)
    return {
        "total": len(found),
        "offset": offset,
        "returned": len(page),
        "next_offset": next_offset if next_offset < len(found) else None,
        "items": [photo_summary(photo, arguments) for photo in page],
    }


def find_photo(db, uuid):
    # Resolve against library records, never a caller-supplied filesystem path.
    for photo in db.photos():
        if photo.uuid.casefold() == uuid.casefold() and is_visible(photo):
            return photo
    raise ValueError("Photo UUID not found in the visible library.")


def get_photo_details(db, uuid, **options):
    photo = find_photo(db, uuid)
    result = {
        **photo_summary(photo, options),
        "labels": (photo.labels or []) if options.get("include_labels") else [],
        "keywords": (photo.keywords or []) if options.get("include_labels") else [],
        "preview_available": bool(photo.path_derivatives),
        "favorite": photo.favorite,
    }
    if options.get("include_paths", False):
        result.update(original_path=photo.path, edited_path=photo.path_edited)
    return result


def validate_trip(options):
    start, end, *_ = search_parameters(options)
    if not start or not end:
        raise ValueError("Trip analysis requires start_date and end_date.")
    if (end - start).days > 365:
        raise ValueError("Trip analysis supports at most 366 calendar days.")
    return start, end


def trip_records(db, options):
    validate_trip(options)
    found = select_photos(db, {**options, "media_type": options.get("media_type", "all")})
    if len(found) > analytics.MAX_ASSETS:
        raise ValueError(
            f"Analysis exceeds {analytics.MAX_ASSETS} assets. Narrow the dates or filters."
        )
    zone = ZoneInfo(options["timezone"]) if "timezone" in options else None
    return [analytics.metadata(p, zone=zone, **disclosure(options)) for p in found]


def get_trip_analytics(db, **options):
    start, end = validate_trip(options)
    rows = trip_records(db, options)
    return analytics.analyze(
        rows,
        start_date=start,
        end_date=end,
        person_names=disclosure(options)["person_names"],
        retake_seconds=options.get("retake_seconds", 5),
        session_minutes=options.get("session_minutes", 30),
    )


def get_trip_photo_metadata(db, **options):
    rows = trip_records(db, options)
    offset, limit = options.get("offset", 0), options.get("limit", DEFAULT_LIMIT)
    page = rows[offset : offset + limit]
    return {
        "total": len(rows),
        "offset": offset,
        "returned": len(page),
        "next_offset": offset + len(page) if offset + len(page) < len(rows) else None,
        "items": page,
    }


def get_trip_moments(db, **options):
    rows = [r for r in trip_records(db, options) if r["media_type"] == "photo"]
    kind = options.get("kind", "retakes")
    gap = (
        options.get("retake_seconds", 5)
        if kind == "retakes"
        else options.get("session_minutes", 30) * 60
    )
    groups = analytics.capture_groups(rows, gap)
    if kind == "retakes":
        groups = [g for g in groups if len(g["uuids"]) > 1]
    if options.get("sort") == "newest":
        groups.reverse()
    # Groups can be large; paginate groups and bound each UUID sample explicitly.
    offset, limit = options.get("offset", 0), options.get("limit", DEFAULT_LIMIT)
    page = [
        {
            **g,
            "count": len(g["uuids"]),
            "uuids": g["uuids"][:100],
            "uuids_truncated": len(g["uuids"]) > 100,
        }
        for g in groups[offset : offset + limit]
    ]
    return {
        "kind": kind,
        "total": len(groups),
        "returned": len(page),
        "offset": offset,
        "next_offset": offset + len(page) if offset + len(page) < len(groups) else None,
        "items": page,
    }


def get_photo_preview(db, uuid, max_dimension=1024):
    photo = find_photo(db, uuid)
    # Only existing Photos preview files are read. No iCloud download or export.
    for path in photo.path_derivatives:
        try:
            with Image.open(path) as original:
                image = ImageOps.exif_transpose(original).convert("RGB")
                image.thumbnail((max_dimension, max_dimension))
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG", quality=85)
            data = buffer.getvalue()
            if len(data) > MAX_PREVIEW_BYTES:
                continue
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=f"Preview of {photo.original_filename or photo.filename} "
                        f"(UUID {photo.uuid}); use the original for posting.",
                    ),
                    ImageContent(
                        type="image",
                        data=base64.b64encode(data).decode("ascii"),
                        mimeType="image/jpeg",
                    ),
                ]
            )
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
            continue
    raise ValueError(
        "No readable local preview is available for this asset. "
        "Open it in Photos and allow iCloud to download it, then restart the server."
    )


DATE = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"}
TERM = {"type": "string", "minLength": 1, "maxLength": 200}
TERMS = {"type": "array", "items": TERM, "maxItems": 10}
PROJECTION_INPUTS = {
    "location_mode": {
        "type": "string",
        "enum": ["none", "city"],
        "default": "none",
        "description": "City only when host PHOTO_LOCATION_POLICY also permits city.",
    },
    "person_names": {
        **TERMS,
        "uniqueItems": True,
        "description": "Return only these exact existing tags, intersected with the host's PHOTO_ALLOWED_PERSON_NAMES.",
    },
    "include_labels": {"type": "boolean", "default": False},
}
COMMON_INPUTS = {
    **PROJECTION_INPUTS,
    "start_date": {**DATE, "description": "First calendar day, inclusive."},
    "end_date": {**DATE, "description": "Last calendar day, inclusive for the entire day."},
    "timezone": {
        "type": "string",
        "minLength": 1,
        "maxLength": 100,
        "description": "Optional IANA zone. Omit to use each photo's capture-local calendar day.",
    },
    "media_type": {"type": "string", "enum": ["photo", "video", "all"], "default": "photo"},
    "fuzzy": {"type": "boolean", "default": False},
    "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT, "default": DEFAULT_LIMIT},
    "offset": {"type": "integer", "minimum": 0, "default": 0},
    "sort": {"type": "string", "enum": ["oldest", "newest"], "default": "oldest"},
}
TRIP_INPUTS = {
    **COMMON_INPUTS,
    "media_type": {"type": "string", "enum": ["photo", "video", "all"], "default": "all"},
    "locations": TERMS,
    "persons": TERMS,
    "labels": TERMS,
    "uuids": {
        "type": "array",
        "items": TERM,
        "minItems": 1,
        "maxItems": 1000,
        "uniqueItems": True,
        "description": "Optional exact selection, still intersected with date and other filters.",
    },
    "retake_seconds": {"type": "integer", "minimum": 0, "maximum": 60, "default": 5},
    "session_minutes": {"type": "integer", "minimum": 1, "maximum": 240, "default": 30},
}
SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "uuid": {"type": "string"},
        "filename": {"type": "string"},
        "date": {"type": "string"},
        "location": {"type": ["string", "null"]},
        "persons": {"type": "array", "items": {"type": "string"}},
        "media_type": {"type": "string", "enum": ["photo", "video"]},
        "original_available": {"type": "boolean"},
    },
    "required": [
        "uuid",
        "filename",
        "date",
        "location",
        "persons",
        "media_type",
        "original_available",
    ],
    "additionalProperties": False,
}
PAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "total": {"type": "integer", "minimum": 0},
        "offset": {"type": "integer", "minimum": 0},
        "returned": {"type": "integer", "minimum": 0, "maximum": MAX_LIMIT},
        "next_offset": {"type": ["integer", "null"]},
        "items": {"type": "array", "items": SUMMARY_SCHEMA, "maxItems": MAX_LIMIT},
    },
    "required": ["total", "offset", "returned", "next_offset", "items"],
    "additionalProperties": False,
}


def tool(name, description, properties, required=(), output_schema=None):
    return Tool(
        name=name,
        description=description,
        inputSchema={
            "type": "object",
            "properties": properties,
            "required": list(required),
            "additionalProperties": False,
        },
        outputSchema=output_schema,
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False),
    )


TOOLS = [
    tool(
        "search-photos",
        "Search with date, location and named-person filters. Locations match ANY; people match ALL; labels or keywords match ANY. Default: photos only, 25 results. Hidden/deleted assets are excluded. Follow next_offset with the same filters.",
        {**COMMON_INPUTS, "locations": TERMS, "persons": TERMS, "labels": TERMS},
        output_schema=PAGE_SCHEMA,
    ),
    *[
        tool(
            name,
            description,
            {
                **COMMON_INPUTS,
                field: TERM,
                "fuzzy": {"type": "boolean", "default": field == "person"},
            },
            (field,),
            PAGE_SCHEMA,
        )
        for name, field, description in [
            (
                "location-search",
                "location",
                "Find assets by case-insensitive place substring, with optional date and paging filters.",
            ),
            (
                "label-search",
                "label",
                "Find assets by label OR keyword, with optional date and paging filters.",
            ),
            (
                "people-search",
                "person",
                "Find assets by a Photos person-name tag. Fuzzy matching defaults to true for compatibility; set false for substring matching.",
            ),
        ]
    ],
    tool(
        "get-photo-details",
        "Get metadata for one UUID. Names and city require host policy plus explicit projection; labels and paths are opt-in.",
        {"uuid": TERM, **PROJECTION_INPUTS, "include_paths": {"type": "boolean", "default": False}},
        ("uuid",),
    ),
    tool(
        "get-trip-analytics",
        "Analyze a dated selection: daily/hourly counts, requested family tags, camera/lens/modes, crop loss, edit/storage/coverage, rapid-capture and session counts. Max 366 days/5000 assets. No precise GPS. Names/cities are host-gated and off by default; labels opt-in. Scores are undocumented hints, not quality verdicts.",
        {
            key: value
            for key, value in TRIP_INPUTS.items()
            if key not in {"limit", "offset", "sort"}
        },
        ("start_date", "end_date"),
    ),
    tool(
        "get-trip-photo-metadata",
        "Paginated per-asset trip metadata for photo comparisons, crop planning and video selection. Cached EXIF and existing Photos analysis only; no image inference, downloads or precise GPS. Same scope and projection limits as get-trip-analytics.",
        TRIP_INPUTS,
        ("start_date", "end_date"),
    ),
    tool(
        "get-trip-moments",
        "Paginated rapid-capture candidate stacks or capture sessions. Time proximity does not prove visual duplication, activity duration or dwell time. Each group includes at most 100 UUIDs with an explicit truncation flag.",
        {
            **TRIP_INPUTS,
            "kind": {"type": "string", "enum": ["retakes", "sessions"], "default": "retakes"},
        },
        ("start_date", "end_date"),
    ),
    tool(
        "get-photo-preview",
        "View an existing local JPEG preview by UUID. Bounded to 1024px and 1 MiB. Does not download, export, edit or publish the asset.",
        {
            "uuid": TERM,
            "max_dimension": {"type": "integer", "minimum": 128, "maximum": 1024, "default": 1024},
        },
        ("uuid",),
    ),
]
TOOLS_BY_NAME = {item.name: item for item in TOOLS}


@server.list_tools()
async def handle_list_tools():
    return TOOLS


def structured_result(data):
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(data, ensure_ascii=False))],
        structuredContent=data,
    )


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict[str, Any]):
    try:
        definition = TOOLS_BY_NAME.get(name)
        if definition is None:
            raise ValueError(f"Unknown tool: {name}")
        jsonschema.validate(arguments, definition.inputSchema)
        options = dict(arguments)
        disclosure(options)
        for legacy, field, target in [
            ("location-search", "location", "locations"),
            ("label-search", "label", "labels"),
            ("people-search", "person", "persons"),
        ]:
            if name == legacy:
                options[target] = [options.pop(field)]
        if name == "people-search":
            options.setdefault("fuzzy", True)
        if name not in {"get-photo-preview", "get-photo-details"}:
            search_parameters(options)
        if name.startswith("get-trip-"):
            validate_trip(options)
        db = await photos_loader.get()
        if name == "get-photo-preview":
            return await asyncio.to_thread(get_photo_preview, db, **arguments)
        if name == "get-photo-details":
            return structured_result(await asyncio.to_thread(get_photo_details, db, **arguments))
        if name.startswith("get-trip-"):
            operation = {
                "get-trip-analytics": get_trip_analytics,
                "get-trip-photo-metadata": get_trip_photo_metadata,
                "get-trip-moments": get_trip_moments,
            }[name]
            return structured_result(await asyncio.to_thread(operation, db, **arguments))
        return structured_result(await asyncio.to_thread(search_photos, db, options))
    except jsonschema.ValidationError as error:
        message = f"Invalid arguments: {error.message}"
    except (ValueError, RuntimeError) as error:
        message = str(error)
    except Exception:  # noqa: BLE001 - return an MCP error without logging personal metadata
        # Do not log photo metadata, raw tool arguments, names or query results.
        logger.error("Photo tool failed (%s).", name)
        message = (
            "Photo operation failed. Check Photos-library availability and restart the server."
        )
    return CallToolResult(content=[TextContent(type="text", text=message)], isError=True)


async def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    with suppress(KeyboardInterrupt):
        asyncio.run(main())
