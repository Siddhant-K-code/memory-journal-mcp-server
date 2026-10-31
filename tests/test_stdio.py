import base64
import io
import json
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PIL import Image


@pytest.mark.parametrize("denied", [False, True])
async def test_real_stdio_discovery_calls_and_images(tmp_path, denied):
    preview = tmp_path / "fixture.jpg"
    Image.new("RGB", (800, 600), "teal").save(preview)
    marker = tmp_path / "library-opened"
    root = Path(__file__).resolve().parents[1]
    args = [str(root / "tests/fixture_server.py"), str(preview), str(marker)]
    if denied:
        args.append("--deny")
    params = StdioServerParameters(command=sys.executable, args=args, cwd=str(root))
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write, read_timeout_seconds=timedelta(seconds=10)) as session,
    ):
        init = await session.initialize()
        assert init.capabilities.tools is not None
        definitions = await session.list_tools()
        assert len(definitions.tools) == 9
        assert not marker.exists()
        invalid = await session.call_tool("people-search", {})
        assert invalid.isError and not marker.exists()
        response = await session.call_tool(
            "search-photos",
            {
                "start_date": "2026-10-24",
                "end_date": "2026-10-30",
                "locations": ["River Town"],
                "limit": 1,
            },
        )
        assert marker.exists()
        if denied:
            assert response.isError
            assert "macOS denied" in response.content[0].text
            assert len((await session.list_tools()).tools) == 9
            return
        assert not response.isError
        page = response.structuredContent
        assert page["total"] == 1 and page["returned"] == 1
        assert json.loads(response.content[0].text) == page
        uuid = page["items"][0]["uuid"]
        for name, arguments in [
            ("location-search", {"location": "Hill"}),
            ("label-search", {"label": "Holiday"}),
            ("people-search", {"person": "Person A"}),
        ]:
            result = await session.call_tool(name, arguments)
            assert not result.isError and result.structuredContent["total"] > 0
        details = await session.call_tool("get-photo-details", {"uuid": uuid})
        assert not details.isError and details.structuredContent["uuid"] == uuid
        assert details.structuredContent["location"] is None
        assert details.structuredContent["persons"] == []
        assert "original_path" not in details.structuredContent
        trip = {"start_date": "2026-10-24", "end_date": "2026-10-30"}
        summary = await session.call_tool("get-trip-analytics", trip)
        assert not summary.isError and summary.structuredContent["scope"]["photos"] == 4
        metadata = await session.call_tool("get-trip-photo-metadata", {**trip, "limit": 1})
        assert not metadata.isError and metadata.structuredContent["returned"] == 1
        assert "city" not in metadata.structuredContent["items"][0]
        moments = await session.call_tool("get-trip-moments", {**trip, "kind": "sessions"})
        assert not moments.isError and moments.structuredContent["total"] > 0
        result = await session.call_tool("get-photo-preview", {"uuid": uuid, "max_dimension": 256})
        assert not result.isError
        block = next(block for block in result.content if block.type == "image")
        with Image.open(io.BytesIO(base64.b64decode(block.data))) as image:
            assert image.size == (256, 192)
