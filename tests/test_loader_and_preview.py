import asyncio
import base64
import hashlib
import io
import threading

import pytest
from PIL import Image

import server
from tests.fixtures import FixtureDB, photo


async def test_loader_is_lazy_and_shared_by_concurrent_calls():
    calls = []
    db = object()

    def factory():
        calls.append(True)
        return db

    loader = server.PhotosDBLoader(factory)
    assert calls == []
    assert await asyncio.gather(loader.get(), loader.get()) == [db, db]
    assert len(calls) == 1


async def test_timeout_does_not_cancel_the_load():
    ready = threading.Event()
    db = object()

    def factory():
        ready.wait(2)
        return db

    loader = server.PhotosDBLoader(factory, timeout=0.01)
    try:
        with pytest.raises(RuntimeError, match="still loading"):
            await loader.get()
        loader.timeout = 1
        ready.set()
        assert await loader.get() is db
    finally:
        ready.set()


async def test_cancellation_of_one_caller_preserves_shared_load():
    started, ready = threading.Event(), threading.Event()
    db = object()

    def factory():
        started.set()
        ready.wait(2)
        return db

    loader = server.PhotosDBLoader(factory)
    task = asyncio.create_task(loader.get())
    try:
        assert await asyncio.to_thread(started.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        ready.set()
        assert await loader.get() is db
    finally:
        ready.set()


async def test_permission_failure_is_not_reported_as_loading_forever():
    calls = []

    def denied():
        calls.append(True)
        raise PermissionError("fixture library")

    loader = server.PhotosDBLoader(denied)
    for _ in range(2):
        with pytest.raises(RuntimeError, match="macOS denied"):
            await loader.get()
    assert len(calls) == 1


def test_details_use_uuid_not_duplicate_filename():
    first, second = photo(1), photo(2, path="/fixture/original.jpg")
    details = server.get_photo_details(FixtureDB([first, second]), second.uuid)
    assert details["uuid"] == second.uuid
    assert details["original_path"] == "/fixture/original.jpg"
    assert first.filename == second.filename
    with pytest.raises(ValueError, match="UUID not found"):
        server.get_photo_details(FixtureDB([first, second]), first.filename)


@pytest.mark.parametrize("flag", ["hidden", "intrash"])
def test_hidden_or_trashed_uuid_cannot_be_accessed(flag):
    p = photo(1, **{flag: True})
    db = FixtureDB([p])
    with pytest.raises(ValueError, match="UUID not found"):
        server.get_photo_details(db, p.uuid)
    with pytest.raises(ValueError, match="UUID not found"):
        server.get_photo_preview(db, p.uuid)


def test_preview_is_bounded_strips_metadata_and_does_not_modify_source(tmp_path):
    path = tmp_path / "preview.jpg"
    image = Image.new("RGB", (1600, 1200), "teal")
    exif = image.getexif()
    exif[0x010E] = "fixture-private-metadata"
    image.save(path, exif=exif)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    p = photo(1, path_derivatives=[str(path)])
    result = server.get_photo_preview(FixtureDB([p]), p.uuid, max_dimension=256)
    raw = base64.b64decode(result.content[1].data)
    assert len(raw) <= server.MAX_PREVIEW_BYTES
    with Image.open(io.BytesIO(raw)) as preview:
        assert max(preview.size) == 256
        assert preview.format == "JPEG"
        assert not preview.getexif()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_preview_skips_corrupt_or_missing_files(tmp_path):
    bad = tmp_path / "bad.jpg"
    bad.write_text("not an image")
    good = tmp_path / "good.jpg"
    Image.new("RGB", (32, 32), "blue").save(good)
    p = photo(1, path_derivatives=[str(tmp_path / "missing.jpg"), str(bad), str(good)])
    assert server.get_photo_preview(FixtureDB([p]), p.uuid).content[1].type == "image"


def test_no_preview_and_oversized_output_are_actionable_errors(tmp_path, monkeypatch):
    p = photo(1)
    with pytest.raises(ValueError, match="No readable local preview"):
        server.get_photo_preview(FixtureDB([p]), p.uuid)
    good = tmp_path / "good.jpg"
    Image.new("RGB", (32, 32), "blue").save(good)
    p.path_derivatives = [str(good)]
    monkeypatch.setattr(server, "MAX_PREVIEW_BYTES", 1)
    with pytest.raises(ValueError, match="No readable local preview"):
        server.get_photo_preview(FixtureDB([p]), p.uuid)


def test_arbitrary_path_is_never_opened_as_a_photo_uuid(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("An arbitrary caller path must not be opened")

    monkeypatch.setattr(server.Image, "open", forbidden)
    with pytest.raises(ValueError, match="UUID not found"):
        server.get_photo_preview(FixtureDB([photo(1)]), "/private/arbitrary.jpg")
