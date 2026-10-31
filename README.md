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
| `get-photo-details` | Look up one UUID, including original/edited paths and preview availability. |
| `get-photo-preview` | Return an existing local preview as MCP image content. |

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
- `media_type` is `photo` (default), `video`, or `all`. Live Photo still images remain photos. Hidden and trashed assets are excluded from all six tools. Local-only photos are included, even when they are not synced to iCloud.
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

Repeat the same filters with `offset` set to `next_offset` until it is `null`. `total` counts matching assets before pagination. Each item includes its `uuid`, original filename, ISO capture date, place, Photos person tags, media type and whether its original is available locally.

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
