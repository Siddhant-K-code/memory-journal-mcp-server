# Connect Smart Photo Journal to a desktop app

Smart Photo Journal reads an Apple Photos library on your Mac. Your MCP client starts `server.py` as a local **stdio** subprocess. The client can then search, inspect bounded previews and request trip analytics. The server needs no API key and makes no model API calls. Your client may send returned metadata and previews to its model provider.

Client instructions and official links were checked on November 1, 2026. Menu names and availability can vary with the app version and workspace policy.

## Choose a client

| Client | Connection | Configuration |
| --- | --- | --- |
| ChatGPT Desktop with local MCP settings | Local stdio | Settings → MCP servers, or the shared Codex host configuration |
| Codex Desktop, CLI or IDE extension | Local stdio | `~/.codex/config.toml` |
| Claude Desktop | Local stdio | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Cursor desktop | Local stdio | `~/.cursor/mcp.json` for your user |
| ChatGPT web / a remote-only MCP client | No direct connection to this server | Requires a separately implemented and secured remote transport |

The supported path here runs on the Mac that holds your Photos library. A remote container does not acquire that library by reading your local configuration. This repository does not provide a hosted MCP endpoint, remote bridge or installable ChatGPT plugin package. A browser URL such as `http://127.0.0.1:8770/` belongs to the optional report/film editor, **not** to the MCP transport.

## 1. Install once on your Mac

You need macOS, an Apple Photos library, Git, Python 3.11 or newer and [uv](https://docs.astral.sh/uv/getting-started/installation/). If you use Homebrew, `brew install uv` installs uv. uv can install a compatible Python when needed. Node.js, FFmpeg and model credentials are not required for the MCP server. FFmpeg is needed only for the optional film renderer described in the [README](../README.md#regenerate-a-film-from-the-motion-plan).

```sh
mkdir -p "$HOME/Developer"
git clone https://github.com/Siddhant-K-code/memory-journal-mcp-server.git "$HOME/Developer/memory-journal-mcp-server"
cd "$HOME/Developer/memory-journal-mcp-server"
uv sync --locked --no-dev
command -v uv
```

Record the absolute `uv` path printed by the last command. Desktop apps may not inherit your terminal's `PATH`. Replace every placeholder below with an absolute path; do not put `~` or `$HOME` into JSON/TOML path values and expect shell expansion.

In Photos, open the library you want to search. Its default location is `~/Pictures/Photos Library.photoslibrary`. To check it, open Photos → Settings → General and look at Library Location. A custom library is fine: use its actual `.photoslibrary` directory as `PHOTO_LIBRARY_PATH`. This variable selects a library; it does not grant access to it.

## 2. Configure your client

### ChatGPT Desktop and Codex Desktop

For versions exposing local MCP settings, open **Settings → MCP servers → Add server**, choose **STDIO**, and enter:

- Name: `smart-photo-journal`
- Command: the absolute path printed by `command -v uv`
- Arguments: `--directory`, your absolute repository directory, `run`, `--locked`, `--no-dev`, `server.py` (six separate arguments)
- Environment: `PHOTO_LIBRARY_PATH` set to your library directory, `PHOTO_LOCATION_POLICY` set to `none`, and `PHOTO_ALLOWED_PERSON_NAMES` set to the literal string `[]`

Save, then restart the server/client. Use `/mcp` to inspect connected tools where available. The [official OpenAI MCP guide](https://learn.chatgpt.com/docs/extend/mcp) documents shared configuration for the ChatGPT desktop app, Codex CLI and IDE on the same Codex host.

You can instead merge [examples/codex.toml](../examples/codex.toml) into `~/.codex/config.toml`. Keep your other settings and server entries. The example uses a 120-second startup timeout and a 180-second tool timeout to accommodate an initial library load. Do not add a second entry for a server you already configured in the UI.

If your ChatGPT build only offers an MCP **Server URL** field, it is a remote connection flow. Do not paste the Python command or film-editor URL there. Use a local stdio-capable client for this repository. [ChatGPT's web custom-server flow](https://developers.openai.com/api/docs/guides/custom-mcp-server) uses a remote transport and is a different setup.

### Codex CLI

The CLI can register the same local server without manually editing TOML:

```sh
codex mcp add smart-photo-journal \
  --env PHOTO_LIBRARY_PATH="$HOME/Pictures/Photos Library.photoslibrary" \
  --env PHOTO_LOCATION_POLICY=none \
  --env PHOTO_ALLOWED_PERSON_NAMES='[]' \
  -- "$(command -v uv)" --directory "$HOME/Developer/memory-journal-mcp-server" \
  run --locked --no-dev server.py
codex mcp list
```

Adjust the library path first if yours differs. To increase timeouts, edit the registered table using [examples/codex.toml](../examples/codex.toml). Do not configure both methods as separate servers. This stdio server does not need `codex mcp login`; that command is for OAuth-capable HTTP servers.

### Claude Desktop

Open **Settings → Developer → Edit Config**. On macOS the file is `~/Library/Application Support/Claude/claude_desktop_config.json`. Merge the `smart-photo-journal` entry from [examples/stdio-clients.json](../examples/stdio-clients.json) into its `mcpServers` object, substitute your paths, save and fully quit/reopen Claude Desktop.

The [official MCP local-server walkthrough](https://modelcontextprotocol.io/docs/develop/connect-local-servers) explains this configuration flow. Its filesystem-server example needs Node.js; Smart Photo Journal uses Python and uv instead. Do not run `mcp install server.py`: this repository uses the low-level MCP server API and is configured with an explicit launch command.

### Cursor

Merge [examples/cursor.json](../examples/cursor.json) into `~/.cursor/mcp.json`, substitute your paths and enable the server in Cursor's MCP settings. This example explicitly sets `type` to `stdio`. A project `.cursor/mcp.json` is also supported, but a user configuration keeps your personal library path out of shared repositories. Restart/reload the server after changes.

See [Cursor's MCP documentation](https://cursor.com/docs/mcp) for current configuration options. The desktop app must run on the Mac with your Photos library; this guide does not configure a cloud agent.

## 3. Allow macOS to read the library

If a search reports permission denied, open **System Settings → Privacy & Security → Photos** and check access for the app launching the server. Direct database access through `osxphotos` may instead require **Full Disk Access** for that host app: ChatGPT/Codex, Claude Desktop, Cursor, or your terminal when testing there.

For Full Disk Access, unlock the setting if asked, enable the actual host app, or use **+** to add it from Applications if absent. Full Disk Access covers protected files beyond Photos, so grant it only to the host you intend to use. Fully quit and reopen that app after changing permissions. Terminal permission does not establish desktop-app permission.

For iCloud libraries, existing metadata can be searchable while an original or preview is absent locally. Download the desired item through Photos yourself, then restart the MCP server. The server never downloads iCloud originals automatically.

## 4. Verify discovery, then a bounded search

First ask your client to list Smart Photo Journal's tools. Version 0.3 exposes nine: `search-photos`, `location-search`, `label-search`, `people-search`, `get-photo-details`, `get-photo-preview`, `get-trip-analytics`, `get-trip-photo-metadata` and `get-trip-moments`. Discovery does not load your library. The first query does, so allow extra time.

Try this prompt, replacing the dates with a trip in your own library:

> Use smart-photo-journal to search photos from October 24 through October 30, 2026, in Asia/Kolkata. Return the first 25 results with names and location output off. Follow pagination if I ask for more. Show previews for a few UUIDs before suggesting a shortlist.

The underlying `search-photos` arguments are:

```json
{
  "start_date": "2026-10-24",
  "end_date": "2026-10-30",
  "timezone": "Asia/Kolkata",
  "media_type": "photo",
  "limit": 25
}
```

An empty result is valid if you have no matching photos. Both dates are inclusive. Use the same filters and `next_offset` to continue; pass a returned UUID to `get-photo-preview` or `get-photo-details`. Filenames are not stable identifiers. Previews are limited to 1024 pixels and 1 MiB with metadata stripped; use full-resolution originals when posting.

## Optional: disclose selected names or cities

Defaults return no person names or location values; search records retain `persons: []` and `location: null`. To permit city output, set `PHOTO_LOCATION_POLICY` to `city` in the host's server environment. To permit specific existing Photos tags, set `PHOTO_ALLOWED_PERSON_NAMES` to a **JSON array encoded as an environment string** containing at most 10 names, for example `["Example Person"]`. In a JSON client configuration the value is written as `"[\"Example Person\"]"`. In TOML you can write `PHOTO_ALLOWED_PERSON_NAMES = '["Example Person"]'`.

Restart the server, then explicitly request `location_mode: "city"` and/or the exact allowed tags in `person_names` on the relevant tool. Both host permission and tool projection must allow the field. A prompt alone cannot raise the host ceiling. Precise coordinates are never returned. Set the environment back to `none` and `[]` and restart to revoke those projections.

These are output controls, not complete authorization or anonymization: filters can still reveal matching UUIDs/counts, and filenames or images can reveal context. Details paths and scene labels are separately opt-in. Read the [privacy and scope notes](../README.md#privacy-and-scope) before sharing a report.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `uv` not found / server immediately exits | Use the absolute executable path and run `uv sync --locked --no-dev` in the checkout. Keep startup diagnostics on stderr. |
| No tools after editing config | Validate JSON/TOML, preserve the client's required outer key, restart the client and inspect its MCP logs. Check workspace policies too. |
| Nine tools appear, but first search fails | Discovery does not prove Photos access. Verify the library path and host-app permissions. |
| First call times out on a large library | Increase the client tool timeout; retry after the background load finishes. Concurrent queries share that load. |
| New imports or tags do not appear | Metadata is cached per process. Restart the MCP server after changing the library. |
| Preview/original unavailable | Open/download the item in Photos manually; metadata availability does not imply a local original. |
| Names/cities still absent | Check host environment, restart, then request the matching tool projection. Only exact allowed names can be returned. |
| A browser URL does not connect as MCP | The film editor is an HTTP app; the MCP server speaks stdio. Use the launch command above. |

Installing the MCP server does not automatically start the seven-view browser report or film editor. Their explicit local generation/render commands live in the [README](../README.md#trip-analytics-and-photo-explorer).
