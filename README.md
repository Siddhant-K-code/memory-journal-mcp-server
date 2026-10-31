# Smart Photo Journal MCP Server

Search your Apple Photos library by date, location, labels and named people, then view previews to make a shortlist in Codex or another local MCP client.

Inspired by [burningion/video-editing-mcp](https://github.com/burningion/video-editing-mcp).

<a href="https://glama.ai/mcp/servers/51jiworg5k"><img width="380" height="200" src="https://glama.ai/mcp/servers/51jiworg5k/badge" alt="Smart Photo Journal Server MCP server" /></a>

## Install

Requirements: macOS, an Apple Photos library, Python 3.11+, and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/Siddhant-K-code/memory-journal-mcp-server.git
cd memory-journal-mcp-server
uv sync --locked --no-dev
```

The server uses the last-opened Photos library by default. Set `PHOTO_LIBRARY_PATH` to select a specific `.photoslibrary` directory. This is useful when automatic library discovery cannot read the Photos preferences file.

### Codex Desktop / CLI

Add this to `~/.codex/config.toml`, using absolute paths on your Mac:

```toml
[mcp_servers.smart-photo-journal]
command = "/absolute/path/to/uv"
args = ["--directory", "/absolute/path/to/memory-journal-mcp-server", "run", "--locked", "--no-dev", "server.py"]
startup_timeout_sec = 120
tool_timeout_sec = 180

[mcp_servers.smart-photo-journal.env]
PHOTO_LIBRARY_PATH = "/Users/YOUR_USERNAME/Pictures/Photos Library.photoslibrary"
```

Find the `uv` executable with `command -v uv`. Restart the client after editing its configuration. See the [official Codex MCP configuration documentation](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).

### Claude Desktop / other stdio clients

```json
{
  "mcpServers": {
    "smart-photo-journal": {
      "command": "/absolute/path/to/uv",
      "args": ["--directory", "/absolute/path/to/memory-journal-mcp-server", "run", "--locked", "--no-dev", "server.py"],
      "env": {
        "PHOTO_LIBRARY_PATH": "/Users/YOUR_USERNAME/Pictures/Photos Library.photoslibrary"
      }
    }
  }
}
```

### macOS permissions and startup

Tools can be discovered before Photos is opened. The first search loads the library in a background thread; concurrent calls share the load. Search work also runs off the protocol event loop. A slow library can be retried after the load timeout.

If macOS denies access, allow the **host app** (Codex/ChatGPT, Claude Desktop, or your terminal) in **System Settings → Privacy & Security → Photos**. Direct database access may require **Full Disk Access** instead; that permission also covers protected data beyond Photos. Restart the client/server after changing permissions. Loading failures are returned as MCP tool errors, rather than being reported as “still loading” forever.

Library metadata is cached for the server process. Restart it to pick up new imports, changed tags or library edits. Logs go to stderr, without creating a file in the current directory.

## Tools

| Tool | Purpose |
| --- | --- |
| `search-photos` | Combine date, location, people, label/keyword and media filters. |
| `location-search` | Existing place-search name, now with date filters and pagination. |
| `label-search` | Existing label-search name; matches labels **or** keywords. |
| `people-search` | Existing people-search name; searches Photos person tags. |
| `get-photo-details` | Look up one UUID. Labels and local paths are opt-in. |
| `get-photo-preview` | Return an existing local preview as MCP image content. |
| `get-trip-analytics` | Aggregate a bounded dated selection: family, time, camera, modes, edits, crops, storage and coverage. |
| `get-trip-photo-metadata` | Paginated dimensions, cached EXIF, existing Photos scores and motion availability. |
| `get-trip-moments` | Paginated rapid-capture candidates or capture sessions, with UUID samples. |

### Find a trip

Call `search-photos` with:

```json
{
  "start_date": "2026-10-24",
  "end_date": "2026-10-30",
  "timezone": "Asia/Tokyo",
  "locations": ["Kyoto", "Osaka"],
  "media_type": "photo",
  "limit": 25
}
```

- Dates use strict `YYYY-MM-DD` format, with **both calendar days inclusive**. An end date includes photos taken late that night, including fractional seconds.
- With `timezone`, dates are interpreted in that IANA time zone. Without it, each photo's capture-local calendar date is used. Returned dates retain the capture time-zone offset.
- Locations match **any** supplied substring. People match **all** supplied names. Labels/keywords match **any** supplied term. The different filter categories are combined with AND.
- Search is case-insensitive. `fuzzy: true` additionally allows approximate matches. Combined search defaults to `false`; legacy `people-search` defaults to `true` for compatibility. People names come from existing Photos tags; this server does not identify faces or infer name aliases.
- `media_type` is `photo` (default), `video`, or `all`. Trip tools default to `all`. Live Photo still images remain photos. Hidden and trashed assets are excluded from all tools. Local-only photos are included, even when they are not synced to iCloud.
- Results default to 25 records; `limit` is 1–100. `sort` is `oldest` or `newest`. Ordering uses capture time, then UUID to break ties.

Each page is returned as `structuredContent` and serialized JSON text for clients that use text results:

```json
{
  "total": 52,
  "offset": 0,
  "returned": 25,
  "next_offset": 25,
  "items": ["... photo metadata objects ..."]
}
```

Repeat the same filters with `offset` set to `next_offset` until it is `null`. `total` counts matching assets before pagination. Each item includes its `uuid`, original filename, ISO capture date, media type and whether its original is available locally. Location is `null` and people is an empty list unless the projection and host policy below permit them.

The legacy tool names accept the same optional date/paging arguments alongside their required `location`, `label`, or `person` string. In 0.2, their output changes from unbounded prose to the same structured page format. Consumers that scrape the old text should read the JSON page instead.

### Find family photos

```json
{
  "start_date": "2026-10-24",
  "end_date": "2026-10-30",
  "persons": ["Alex", "Sam", "Taylor"],
  "limit": 25
}
```

This finds images carrying **all three** person tags. Empty location filters allow untagged travel photos to appear; adding locations narrows the results to matching place metadata.

### Location and person disclosure

Version 0.3 defaults to no location/name output, even when those fields were used to filter locally. Precise coordinates and full place strings are never returned. To permit city output and specific existing name tags, set these in the MCP server's environment on the trusted host:

```toml
PHOTO_LOCATION_POLICY = "city"
PHOTO_ALLOWED_PERSON_NAMES = '["Alex", "Sam", "Taylor"]'
```

These settings are a ceiling, not a default projection. A call must also explicitly request `"location_mode": "city"` and/or `"person_names": ["Alex"]`. The names returned are the intersection of the request, host allowlist and existing tags. Missing city metadata remains null; the server does not fall back to a detailed place name. `PHOTO_LOCATION_POLICY=none` and `PHOTO_ALLOWED_PERSON_NAMES=[]` deny output regardless of client arguments. Invalid host policy fails closed. Restart after changes.

This controls output fields, not which library records can be searched. Known place/name filters still execute locally and their matching UUIDs/counts are observable. Dates, filenames, previews, requested scene labels and opt-in filesystem paths can themselves reveal context. This is not a complete anonymization or authorization system. `get-photo-details` now requires `include_paths: true` for local paths and `include_labels: true` for labels/keywords.

### Trip analytics and photo explorer

```json
{
  "start_date": "2026-10-24",
  "end_date": "2026-10-30",
  "timezone": "Asia/Tokyo",
  "locations": ["Kyoto", "Osaka"],
  "location_mode": "city",
  "person_names": ["Alex", "Sam", "Taylor"],
  "include_labels": true
}
```

Use this with `get-trip-analytics`. All three trip tools require inclusive start/end dates, allow at most 366 days and 5,000 matched assets, and fail explicitly rather than silently truncating an oversized selection. Optional `uuids` (1–1,000 unique IDs) intersects an existing shortlist with dates and other filters. Metadata/moment pages default to 25 with maximum 100. Moment UUID samples are capped at 100 per group with `count` and `uuids_truncated` fields.

Analytics include zero-count days, capture-hour bins, requested-person co-occurrence, camera/lens/modes, dimensions, centred crop-area trade-offs, existing edit/favorite flags, local availability, original byte counts, video duration coverage and experimental stored-score coverage. Rapid groups default to adjacent gaps of at most 5 seconds (`retake_seconds`: 0–60); sessions split at gaps over 30 minutes (`session_minutes`: 1–240). They do not establish visual duplication, dwell time, activities or actual travel distance. File-size totals exclude Live Photo motion companions, edits and caches. Photos' cached EXIF may differ from current originals; missing EXIF is reported as unknown. A zero aesthetic score is treated as uncomputed/unknown. Score meanings and ranking direction are undocumented and require visual review.

Generate a private local explorer from the same filters:

```sh
export PHOTO_LOCATION_POLICY=city
export PHOTO_ALLOWED_PERSON_NAMES='["Alex", "Sam", "Taylor"]'
uv run trip_report.py --start-date 2026-10-24 --end-date 2026-10-30 \
  --timezone Asia/Tokyo --location Kyoto --location Osaka \
  --location-mode city --person-name Alex --person-name Sam \
  --include-labels --standalone --preview-size 384 --output /private/tmp/trip-report.html
```

Host disclosure settings also apply to this command. `--selection-json` accepts a JSON UUID list; `--shortlist-json` sets the initial carousel. The report supports a day/hour timeline, family coverage, rapid-shot and preview-hash comparisons, experimental score sorting, camera/mode browsing, reorderable carousel crop previews, a motion plan and archive checks. Preview-hash pairs are limited to 60 seconds and the same known city, with a Hamming distance of at most 10/64; these are comparison candidates, not verified duplicates. Reports without city disclosure disable hash-pair suggestions rather than grouping unknown cities. Crop and keeper choices change the report only. The command does not render videos, modify Photos, download iCloud assets or call an AI provider.

Reports embed private metadata and local JPEG derivatives with embedded image metadata removed. They are not uploaded; keep the output outside a public checkout. The chart library loads from jsDelivr, which receives an ordinary library request, not report data. Without `--standalone`, output is an inline fragment capped at 1 MB; narrow the selection if it exceeds the limit. Standalone reports allow larger previews. A public city-centre map requires separately supplied public geometry; no photo GPS is read to create a map.

### Inspect a candidate

Use the UUID from search, because filenames can repeat across imports:

```json
{"uuid": "UUID_FROM_SEARCH", "max_dimension": 1024}
```

Pass this to `get-photo-preview` to view a local preview. The JPEG is limited to 1024 pixels on its longest edge and 1 MiB; `max_dimension` may be 128–1024. Preview metadata is stripped. `get-photo-details` accepts the same UUID without `max_dimension`, and reports available original/edited paths. A missing original path may mean the full-resolution asset needs downloading from iCloud through Photos.

Previews are for browsing, not posting exports. Missing/corrupt previews produce an actionable tool error. There is no arbitrary-path image-reading tool, and no automatic iCloud download.

## Privacy and scope

The server reads Photos metadata and existing local previews. It does not edit the library, create albums, export originals, download from iCloud, rank photo quality, deduplicate similar poses, or publish posts. Your MCP client/model can use the returned information and images to help make a shortlist.

There are no model/API credentials to configure. The server itself makes no model API calls; a client using a hosted model may transmit tool results and previews to that provider. Photo metadata is data, not instructions. Review your shortlist and use the full-resolution originals before posting.

## Development

```sh
uv sync --locked
uv run ruff check
uv run ruff format --check
uv run python -m pytest -q
```

Tests use synthetic metadata and generated images, including real MCP stdio client/server exchanges. They do not require or inspect a personal Photos library. Live macOS/Photos validation is a separate check; fixture tests alone do not establish compatibility with a particular Photos database version.
