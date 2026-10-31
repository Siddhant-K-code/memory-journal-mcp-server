import json
from datetime import date
from types import SimpleNamespace

import pytest

import analytics
import server
from tests.fixtures import FixtureDB, photo


def rich(number, **options):
    return photo(
        number,
        width=4000,
        height=3000,
        original_filesize=1000,
        live_photo=True,
        path_live_photo="/fixture/motion.mov",
        hasadjustments=False,
        exif_info=SimpleNamespace(
            camera_model="Camera", lens_model="Lens", iso=200, duration=None, fps=None
        ),
        score=SimpleNamespace(overall=0.8, noise=0),
        **options,
    )


def test_metadata_omits_location_people_paths_and_labels_by_default():
    p = rich(
        1,
        place=SimpleNamespace(
            name="Private home, 12.34 56.78", address=SimpleNamespace(city="Town")
        ),
    )
    record = analytics.metadata(p)
    text = json.dumps(record)
    assert "Private home" not in text and "12.34" not in text and "Person A" not in text
    assert "city" not in record and "persons" not in record and "labels" not in record
    assert "/fixture" not in text
    assert record["scores"]["noise"] is None


def test_trusted_policy_is_a_ceiling_even_when_client_requests_names_and_city(monkeypatch):
    monkeypatch.delenv("PHOTO_LOCATION_POLICY", raising=False)
    monkeypatch.delenv("PHOTO_ALLOWED_PERSON_NAMES", raising=False)
    p = rich(
        1, place=SimpleNamespace(name="Sensitive address", address=SimpleNamespace(city="Town"))
    )
    options = {"location_mode": "city", "person_names": ["Person A", "Parent One"]}
    db = FixtureDB([p])
    result = server.search_photos(db, options)["items"][0]
    assert result["location"] is None and result["persons"] == []
    monkeypatch.setenv("PHOTO_LOCATION_POLICY", "city")
    monkeypatch.setenv("PHOTO_ALLOWED_PERSON_NAMES", '["Person A"]')
    result = server.search_photos(db, options)["items"][0]
    assert result["location"] == "Town" and result["persons"] == ["Person A"]
    assert server.search_photos(db, {})["items"][0]["location"] is None
    assert "Sensitive address" not in json.dumps(result)
    assert "original_path" not in server.get_photo_details(db, p.uuid)


def test_city_projection_never_falls_back_to_precise_place_name():
    assert analytics.city_name(photo(1, location="Home at 123 road")) is None


def test_summary_is_exact_and_missing_values_do_not_become_zero_quality():
    rows = [
        analytics.metadata(rich(1)),
        analytics.metadata(rich(2, taken="2026-10-25T12:00:03+05:30")),
    ]
    rows.append(
        analytics.metadata(photo(3, ismovie=True, exif_info=SimpleNamespace(duration=5.5, fps=60)))
    )
    result = analytics.analyze(rows, start_date=date(2026, 10, 24), end_date=date(2026, 10, 26))
    assert result["scope"]["photos"] == 2 and result["scope"]["videos"] == 1
    assert result["daily"][0]["photos"] == 0 and result["daily"][1]["photos"] == 2
    assert result["retakes"] == {
        "gap_seconds": 5,
        "stacks": 1,
        "photos_in_stacks": 2,
        "adjacent_pairs": 1,
    }
    assert result["sessions"]["count"] == 1
    assert result["video_duration_seconds"] == 5.5
    assert result["score_coverage"]["overall"] == 2 and result["score_coverage"]["noise"] == 0
    assert result["modes"]["live_photo"] == 2 and result["photo_original_bytes"] == 2000


def test_grouping_orders_instants_across_offsets():
    rows = [
        analytics.metadata(rich(1, taken="2026-10-25T12:00:00+05:30")),
        analytics.metadata(rich(2, taken="2026-10-25T06:30:02+00:00")),
    ]
    groups = analytics.capture_groups(rows[::-1], 5)
    assert groups[0]["uuids"] == [rows[0]["uuid"], rows[1]["uuid"]]


def test_centered_crop_math_and_missing_dimensions():
    assert analytics.crop_retained(4000, 3000, 0.8) == 0.6
    assert analytics.crop_retained(3000, 4000, 0.8) == 0.9375
    assert analytics.crop_retained(None, 4000, 0.8) is None


def test_trip_scopes_exact_ids_dates_media_and_pages():
    db = FixtureDB(
        [rich(1), rich(2), rich(3, taken="2026-11-01T12:00:00+05:30"), rich(4, hidden=True)]
    )
    options = {
        "start_date": "2026-10-24",
        "end_date": "2026-10-30",
        "uuids": [db.items[1].uuid, db.items[2].uuid, db.items[3].uuid],
    }
    result = server.get_trip_analytics(db, **options)
    assert result["scope"]["assets"] == 1
    page = server.get_trip_photo_metadata(
        db, start_date="2026-10-24", end_date="2026-10-30", limit=1
    )
    assert page["returned"] == 1 and page["next_offset"] == 1 and page["total"] == 2


def test_family_projection_counts_only_permitted_requested_names(monkeypatch):
    monkeypatch.setenv("PHOTO_ALLOWED_PERSON_NAMES", '["Person A"]')
    result = server.get_trip_analytics(
        FixtureDB([rich(1)]),
        start_date="2026-10-24",
        end_date="2026-10-30",
        person_names=["Person A", "Parent One"],
    )
    assert list(result["family"]) == ["Person A"]
    assert result["family"]["Person A"]["total"] == 1
    assert "Parent One" not in json.dumps(result)


def test_moment_groups_report_bounded_samples_instead_of_silent_truncation():
    db = FixtureDB([rich(i) for i in range(1, 103)])
    page = server.get_trip_moments(db, start_date="2026-10-24", end_date="2026-10-30")
    assert page["items"][0]["count"] == 102
    assert len(page["items"][0]["uuids"]) == 100 and page["items"][0]["uuids_truncated"]


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"start_date": "2026-01-01", "end_date": "2027-01-03"},
        {"start_date": "2026-10-24", "end_date": "2026-10-30", "retake_seconds": 61},
        {"start_date": "2026-10-24", "end_date": "2026-10-30", "uuids": []},
    ],
)
async def test_invalid_trip_input_fails_before_access(monkeypatch, arguments):
    monkeypatch.setattr(
        server, "photos_loader", server.PhotosDBLoader(lambda: pytest.fail("opened library"))
    )
    result = await server.handle_call_tool("get-trip-analytics", arguments)
    assert result.isError and server.photos_loader._future is None


async def test_invalid_host_policy_fails_closed(monkeypatch):
    monkeypatch.setenv("PHOTO_LOCATION_POLICY", "precise")
    monkeypatch.setattr(
        server, "photos_loader", server.PhotosDBLoader(lambda: pytest.fail("opened library"))
    )
    result = await server.handle_call_tool("search-photos", {})
    assert result.isError and server.photos_loader._future is None


@pytest.mark.parametrize("value", ["{}", "null", "[1]", "invalid", json.dumps(["name"] * 11)])
def test_malformed_person_allowlist_fails_closed(monkeypatch, value):
    monkeypatch.setenv("PHOTO_ALLOWED_PERSON_NAMES", value)
    with pytest.raises(ValueError, match="PHOTO_ALLOWED_PERSON_NAMES"):
        server.disclosure({"person_names": ["name"]})


async def test_analytics_dispatches_structured_result(monkeypatch):
    monkeypatch.setattr(
        server, "photos_loader", server.PhotosDBLoader(lambda: FixtureDB([rich(1)]))
    )
    result = await server.handle_call_tool(
        "get-trip-analytics", {"start_date": "2026-10-24", "end_date": "2026-10-30"}
    )
    assert not result.isError and result.structuredContent["scope"]["photos"] == 1


def test_analysis_rejects_oversize_selection_without_truncation(monkeypatch):
    monkeypatch.setattr(analytics, "MAX_ASSETS", 1)
    with pytest.raises(ValueError, match="Narrow"):
        server.get_trip_analytics(
            FixtureDB([rich(1), rich(2)]), start_date="2026-10-24", end_date="2026-10-30"
        )


def test_nan_infinite_missing_and_empty_library_are_safe():
    assert analytics.finite(float("nan")) is None and analytics.finite(float("inf")) is None
    result = analytics.analyze([], start_date=date(2026, 10, 24), end_date=date(2026, 10, 30))
    json.dumps(result, allow_nan=False)
    assert result["sessions"]["count"] == 0 and result["all_requested_people"] is None
