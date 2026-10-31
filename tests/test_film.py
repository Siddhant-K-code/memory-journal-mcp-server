import json
import shutil
import subprocess

import pytest

import film
from local_editor import FilmEditor

ONE = "00000000-0000-0000-0000-000000000001"
TWO = "00000000-0000-0000-0000-000000000002"


def test_plan_ids_validate_membership_order_empty_and_duplicates():
    plan = {"schema_version": 1, "uuids": [TWO, ONE]}
    assert film.plan_ids(plan, {ONE: {}, TWO: {}}) == [TWO, ONE]
    for values in ([], [ONE, ONE], ["bad"], [TWO], [ONE] * 101):
        with pytest.raises(ValueError):
            film.plan_ids({"schema_version": 1, "uuids": values}, {ONE: {}})


def test_cumulative_beat_rounding_has_at_most_half_frame_drift():
    frames = film.clip_frames(100, bpm=130, pace="fast")
    assert set(frames) == {55, 56}
    assert abs(sum(frames) / 30 - 100 * 4 * 60 / 130) <= 1 / 60
    for bad in (0, float("nan"), float("inf"), 1000):
        with pytest.raises(ValueError):
            film.clip_frames(2, bpm=bad)
    with pytest.raises(ValueError):
        film.clip_frames(2, pace="unknown")


def test_missing_motion_does_not_silently_drop_a_selected_photo(tmp_path):
    records = [{"uuid": ONE, "media_type": "photo", "filename": "fixture.jpg"}]
    with pytest.raises(ValueError, match="No local motion"):
        film.media_sources({"schema_version": 1, "uuids": [ONE]}, {ONE: {"motion": None}}, records)


def editor(tmp_path):
    report = tmp_path / "report.html"
    report.write_text("<body>report</body>")
    motion = tmp_path / "clip.mov"
    motion.write_bytes(b"fixture")
    return FilmEditor(
        report=report,
        data={"records": [{"uuid": ONE, "filename": "fixture.jpg", "media_type": "photo"}]},
        media_index={ONE: {"motion": str(motion)}},
        output_dir=tmp_path / "renders",
    )


def test_local_write_authority_requires_exact_origin_host_and_token(tmp_path):
    e = editor(tmp_path)
    assert e.authorize("127.0.0.1:8770", "http://127.0.0.1:8770", e.token)
    assert not e.authorize("attacker.example", "http://127.0.0.1:8770", e.token)
    assert not e.authorize("127.0.0.1:8770", "https://attacker.example", e.token)
    assert not e.authorize("127.0.0.1:8770", "http://127.0.0.1:8770", "wrong")
    assert not e.authorize("127.0.0.1:8770", None, None)


def test_editor_rejects_arbitrary_paths_unknown_music_and_parallel_jobs(tmp_path):
    e = editor(tmp_path)
    for request in (
        {"path": "/private/file"},
        {"plan": {"schema_version": 1, "uuids": [ONE]}, "music": "evil"},
        {"plan": {"schema_version": 1, "uuids": [TWO]}, "music": "none"},
    ):
        with pytest.raises(ValueError):
            e.start(request)
    e.jobs["existing"] = {"status": "rendering"}
    with pytest.raises(RuntimeError):
        e.start({"plan": {"schema_version": 1, "uuids": [ONE]}, "music": "none"})


def test_injected_configuration_cannot_terminate_script(tmp_path):
    e = editor(tmp_path)
    e.credit = {"title": "</script><script>bad</script>"}
    html = e.page()
    assert "</script><script>bad</script>" not in html
    assert "\\u003c/script\\u003e" in html


def test_completed_film_remains_available_after_editor_restart(tmp_path):
    e = editor(tmp_path)
    job_id = "a" * 32
    (e.output_dir / f"trip-film-{job_id}.mp4").write_bytes(b"fixture")
    (e.output_dir / f"trip-film-{job_id}.json").write_text(
        json.dumps({"clips": [{"uuid": ONE}], "duration_seconds": 1.8, "music": None})
    )
    restarted = editor(tmp_path)
    assert restarted.jobs[job_id]["status"] == "complete"
    assert restarted.jobs[job_id]["total"] == 1
    assert '"latest":' in restarted.page()


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg not installed"
)
def test_real_ffmpeg_output_has_all_clips_and_audio_and_preserves_source(tmp_path):
    source = tmp_path / "source.mp4"
    music = tmp_path / "audio.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-nostdin",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=160x90:r=30",
            "-t",
            "0.4",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-nostdin",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=8",
            str(music),
        ],
        check=True,
    )
    before = source.read_bytes()
    plan = {"schema_version": 1, "uuids": [ONE, TWO]}
    rows = [{"uuid": u, "filename": "fixture.mp4", "media_type": "video"} for u in [ONE, TWO]]
    index = {u: {"original": str(source)} for u in [ONE, TWO]}
    result = film.render_film(
        plan,
        index,
        rows,
        tmp_path / "finished.mp4",
        music=music,
        width=720,
        credit={"credit": "Fixture audio generated for the test."},
    )
    assert [r["uuid"] for r in result["clips"]] == [ONE, TWO]
    assert all(r["short_motion_padded"] for r in result["clips"])
    assert abs(result["duration_seconds"] - sum(film.clip_frames(2)) / 30) < 0.1
    assert source.read_bytes() == before
    assert (tmp_path / "finished.credits.txt").is_file()
    with pytest.raises(ValueError, match="already exists"):
        film.render_film(plan, index, rows, tmp_path / "finished.mp4", width=720)
