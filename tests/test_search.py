from datetime import datetime

import jsonschema
import pytest

import server
from tests.fixtures import FixtureDB, library, photo


def ids(result):
    return [int(item["uuid"].split("-")[-1], 16) for item in result["items"]]


def test_inclusive_date_range_excludes_videos_hidden_and_deleted():
    result = server.search_photos(library(), {"start_date": "2026-10-24", "end_date": "2026-10-30"})
    assert ids(result) == [2, 8, 9, 3]
    assert result["total"] == 4
    jsonschema.validate(result, server.PAGE_SCHEMA)


def test_timezone_uses_requested_calendar_day():
    db = FixtureDB([photo(1, taken="2026-10-24T01:00:00+05:30")])
    assert server.search_photos(db, {"start_date": "2026-10-24"})["total"] == 1
    assert server.search_photos(db, {"start_date": "2026-10-24", "timezone": "UTC"})["total"] == 0


@pytest.mark.parametrize(
    "value", ["2026-02-30", "2026-13-01", "20261024", "yesterday", "2026-10-24\n"]
)
def test_invalid_calendar_dates(value):
    with pytest.raises(ValueError):
        server.search_photos(library(), {"start_date": value})


def test_reversed_dates_and_unknown_timezone():
    with pytest.raises(ValueError, match="start_date"):
        server.search_photos(library(), {"start_date": "2026-10-31", "end_date": "2026-10-24"})
    with pytest.raises(ValueError, match="IANA"):
        server.search_photos(library(), {"timezone": "Mars/Olympus"})


def test_combined_places_any_people_all_labels_or_keywords():
    db = FixtureDB(
        [
            photo(
                1,
                location="River Town",
                persons=["Person A", "Parent One"],
                labels=[],
                keywords=["Vacation"],
            ),
            photo(2, location="Hill Town", persons=["Person A"]),
            photo(3, location="Other Town", persons=["Person A", "Parent One"]),
        ]
    )
    result = server.search_photos(
        db,
        {"locations": ["river", "hill"], "persons": ["person a", "parent"], "labels": ["vacation"]},
    )
    assert ids(result) == [1]


@pytest.mark.parametrize("media_type,expected", [("photo", 6), ("video", 1), ("all", 7)])
def test_explicit_media_type(media_type, expected):
    assert server.search_photos(library(), {"media_type": media_type})["total"] == expected


def test_missing_original_filename_and_place_do_not_break_search():
    db = FixtureDB([photo(1, original_filename=None, location=None, path=None)])
    result = server.search_photos(db, {})
    assert result["items"][0]["filename"] == "IMG_1001.jpg"
    assert result["items"][0]["location"] is None
    assert result["items"][0]["original_available"] is False
    assert server.search_photos(db, {"locations": ["Town"]})["total"] == 0
    assert (
        server.search_photos(
            FixtureDB([photo(2, place=type("Place", (), {"name": None})())]),
            {"locations": ["Town"]},
        )["total"]
        == 0
    )


def test_local_only_photos_are_not_excluded():
    p = photo(1, path="/fixture/local.jpg", incloud=False)
    assert ids(server.search_photos(FixtureDB([p]), {})) == [1]


def test_duplicate_filenames_have_distinct_stable_ids_and_page_order():
    db = FixtureDB([photo(3), photo(1), photo(2)])
    first = server.search_photos(db, {"limit": 2})
    second = server.search_photos(db, {"limit": 2, "offset": first["next_offset"]})
    assert ids(first) == [1, 2]
    assert ids(second) == [3]
    assert second["next_offset"] is None
    assert len({p["filename"] for p in first["items"] + second["items"]}) == 1
    assert ids(server.search_photos(db, {"sort": "newest"})) == [3, 2, 1]


def test_default_page_is_bounded_and_offset_past_end_is_empty():
    db = FixtureDB([photo(n) for n in range(1, 150)])
    assert server.search_photos(db, {})["returned"] == 25
    result = server.search_photos(db, {"offset": 999})
    assert result["total"] == 149 and result["items"] == []
    assert result["next_offset"] is None


def test_fuzzy_is_opt_in_for_combined_search():
    db = FixtureDB([photo(1, persons=["Person A"])])
    assert server.search_photos(db, {"persons": ["Persno A"]})["total"] == 0
    assert server.search_photos(db, {"persons": ["Persno A"], "fuzzy": True})["total"] == 1


@pytest.mark.parametrize("key", ["locations", "persons", "labels"])
def test_blank_terms_are_errors(key):
    with pytest.raises(ValueError, match="blank"):
        server.search_photos(library(), {key: ["  "]})


def test_timezones_are_explicit_for_naive_capture_dates():
    p = photo(1, date=datetime(2026, 10, 24))
    assert server.search_photos(FixtureDB([p]), {})["total"] == 1
    with pytest.raises(ValueError, match="naive"):
        server.search_photos(FixtureDB([p]), {"timezone": "UTC"})


async def test_discovery_advertises_capabilities_without_loading_library():
    assert server.photos_loader._future is None
    definitions = await server.handle_list_tools()
    assert {tool.name for tool in definitions} == {
        "search-photos",
        "location-search",
        "label-search",
        "people-search",
        "get-photo-details",
        "get-photo-preview",
        "get-trip-analytics",
        "get-trip-photo-metadata",
        "get-trip-moments",
    }
    assert server.server.create_initialization_options().capabilities.tools is not None
    assert server.photos_loader._future is None
    assert all(tool.annotations.readOnlyHint for tool in definitions)
    assert (
        server.TOOLS_BY_NAME["people-search"].inputSchema["properties"]["fuzzy"]["default"] is True
    )


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("search-photos", {"limit": 101}),
        ("search-photos", {"limit": 0}),
        ("search-photos", {"offset": -1}),
        ("search-photos", {"media_type": "audio"}),
        ("search-photos", {"locations": ["a"] * 11}),
        ("people-search", {}),
        ("get-photo-preview", {"uuid": "a", "max_dimension": 4096}),
        ("get-photo-preview", {"uuid": "a", "path": "/private/file"}),
        ("search-photos", {"start_date": "2026-02-30"}),
        ("search-photos", {"start_date": "2026-10-31", "end_date": "2026-10-24"}),
        ("search-photos", {"timezone": "Mars/Olympus"}),
        ("location-search", {"location": "   "}),
    ],
)
async def test_invalid_inputs_fail_before_library_access(monkeypatch, name, arguments):
    def forbidden():
        pytest.fail("Invalid input must not open Photos")

    monkeypatch.setattr(server, "photos_loader", server.PhotosDBLoader(forbidden))
    result = await server.handle_call_tool(name, arguments)
    assert result.isError
    assert server.photos_loader._future is None


@pytest.mark.parametrize(
    "name,args,expected",
    [
        ("location-search", {"location": "Hill"}, [3]),
        ("people-search", {"person": "Persno A"}, [1, 2, 5, 9, 3, 4]),
        ("label-search", {"label": "Holiday"}, [1, 2, 5, 9, 3, 4]),
    ],
)
async def test_legacy_tools_dispatch_to_their_own_filter(monkeypatch, name, args, expected):
    monkeypatch.setattr(server, "photos_loader", server.PhotosDBLoader(library))
    result = await server.handle_call_tool(name, args)
    assert not result.isError
    assert ids(result.structuredContent) == [n for n in expected if n != 5]


async def test_unknown_tool_is_an_error_without_loading(monkeypatch):
    monkeypatch.setattr(server, "photos_loader", server.PhotosDBLoader(library))
    result = await server.handle_call_tool("not-a-tool", {})
    assert result.isError
    assert server.photos_loader._future is None


async def test_unexpected_error_does_not_log_private_metadata(monkeypatch, caplog):
    class BrokenDB:
        def photos(self):
            raise OSError("private-photo-name-and-person")

    monkeypatch.setattr(server, "photos_loader", server.PhotosDBLoader(BrokenDB))
    result = await server.handle_call_tool("search-photos", {})
    assert result.isError
    assert "private-photo-name-and-person" not in caplog.text
    assert "private-photo-name-and-person" not in result.content[0].text
