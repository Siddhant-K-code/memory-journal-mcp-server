from datetime import date

from PIL import Image

import analytics
import trip_report
from tests.test_analytics import rich


def test_preview_hash_and_thumbnail_strip_metadata(tmp_path):
    source = tmp_path / "photo.jpg"
    image = Image.new("RGB", (300, 200), "green")
    exif = Image.Exif()
    exif[270] = "private metadata"
    image.save(source, exif=exif)
    thumb = trip_report.thumbnail(source)
    assert thumb.startswith("data:image/jpeg;base64,")
    assert trip_report.difference_hash(source) == "0000000000000000"
    import base64
    import io

    with Image.open(io.BytesIO(base64.b64decode(thumb.split(",")[1]))) as result:
        assert max(result.size) == 96 and not result.getexif()
    assert trip_report.thumbnail(tmp_path / "missing.jpg") is None


def test_report_escapes_photo_strings_and_keeps_data_private(tmp_path):
    row = analytics.metadata(rich(1, original_filename="</script><script>alert(1)</script>"))
    data = {
        "records": [row],
        "shortlist": [],
        "aggregate": analytics.analyze(
            [row], start_date=date(2026, 10, 24), end_date=date(2026, 10, 30)
        ),
    }
    output = tmp_path / "report.html"
    trip_report.render(data, output)
    text = output.read_text()
    assert "</script><script>alert(1)</script>" not in text
    assert "\\u003c/script\\u003e" in text
    assert "/*TRIP_DATA*/null" not in text
    assert "record_keys" in text and row["uuid"] in text
    trip_report.render(data, output, standalone=True)
    assert output.read_text().startswith("<!doctype html>")
    assert "connect-src" in output.read_text()


def test_inline_report_size_limit_is_explicit(tmp_path):
    import pytest

    with pytest.raises(ValueError, match="1 MB"):
        trip_report.render({"records": [], "large": "x" * 1_000_001}, tmp_path / "large.html")
