import http.client
import json
import threading

import pytest

from local_editor import ThreadingHTTPServer, handler_for
from tests.test_film import ONE, editor


@pytest.fixture
def running_editor(tmp_path):
    e = editor(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(e))
    e.port = server.server_address[1]
    e.origin = f"http://127.0.0.1:{e.port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield e
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        e.executor.shutdown(wait=True)


def request(e, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection(e.host, e.port, timeout=5)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        conn.close()


@pytest.mark.parametrize(
    "range_header,expected_status,expected_body,content_range",
    [
        (None, 200, b"0123456789abcdef", None),
        ("bytes=2-5", 206, b"2345", "bytes 2-5/16"),
        ("bytes=12-", 206, b"cdef", "bytes 12-15/16"),
        ("bytes=-3", 206, b"def", "bytes 13-15/16"),
        ("bytes=14-99", 206, b"ef", "bytes 14-15/16"),
        ("bytes=99-", 416, None, "bytes */16"),
        ("bytes=5-2", 416, None, "bytes */16"),
        ("bytes=0-1,4-5", 416, None, "bytes */16"),
    ],
)
def test_video_downloads_and_byte_ranges(
    running_editor, range_header, expected_status, expected_body, content_range
):
    e = running_editor
    job_id = "a" * 32
    e.jobs[job_id] = {"status": "complete"}
    (e.output_dir / f"trip-film-{job_id}.mp4").write_bytes(b"0123456789abcdef")
    status, headers, body = request(
        e, "GET", f"/renders/{job_id}.mp4", headers={"Range": range_header} if range_header else {}
    )
    assert status == expected_status
    assert headers.get("Content-Range") == content_range
    if expected_body is not None:
        assert body == expected_body
        assert int(headers["Content-Length"]) == len(body)
        assert headers["Accept-Ranges"] == "bytes"


def test_write_failures_are_actionable_without_poisoning_queue(running_editor):
    e = running_editor
    (e.output_dir / "motion-plan.json").mkdir()
    body = json.dumps({"plan": {"schema_version": 1, "uuids": [ONE]}, "music": "none"})
    status, _, data = request(
        e,
        "POST",
        "/api/render",
        body,
        {"Content-Type": "application/json", "Origin": e.origin, "X-Trip-Token": e.token},
    )
    assert status == 503
    assert "output folder" in json.loads(data)["error"]
    assert str(e.output_dir).encode() not in data
    assert e.jobs == {}


def test_cross_origin_write_is_rejected_over_http(running_editor):
    e = running_editor
    status, _, _ = request(
        e,
        "POST",
        "/api/render",
        "{}",
        {
            "Content-Type": "application/json",
            "Origin": "https://example.invalid",
            "X-Trip-Token": e.token,
        },
    )
    assert status == 403 and not e.jobs
