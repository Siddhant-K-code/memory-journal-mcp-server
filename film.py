"""Local film rendering from explicit UUID plans and a trusted private media index."""

import argparse
import json
import math
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

MAX_CLIPS = 100
PACES = {"fast": 4, "balanced": 6}


def plan_ids(plan, allowed):
    if not isinstance(plan, dict) or plan.get("schema_version") != 1:
        raise ValueError("Unsupported motion-plan format.")
    values = plan.get("uuids")
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_CLIPS:
        raise ValueError(f"Choose between 1 and {MAX_CLIPS} clips.")
    try:
        ids = [str(uuid.UUID(value)).upper() for value in values]
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("Plan contains an invalid photo UUID.") from error
    if len(set(ids)) != len(ids):
        raise ValueError("The motion plan contains duplicate UUIDs.")
    if any(value not in allowed for value in ids):
        raise ValueError("A selected asset is outside this report's media index.")
    return ids


def clip_frames(count, *, pace="fast", bpm=130, fps=30):
    if pace not in {*PACES, "original"}:
        raise ValueError("Pace must be fast, balanced or original.")
    if not isinstance(bpm, (int, float)) or not math.isfinite(bpm) or not 60 <= bpm <= 200:
        raise ValueError("Music tempo must be between 60 and 200 BPM.")
    seconds = PACES[pace] * 60 / bpm if pace in PACES else 3
    # Round cumulative boundaries, so rounding error does not accumulate over the film.
    return [round((i + 1) * seconds * fps) - round(i * seconds * fps) for i in range(count)]


def media_sources(plan, index, records):
    ids = plan_ids(plan, index)
    by_id = {row["uuid"]: row for row in records}
    result = []
    for value in ids:
        if value not in by_id:
            raise ValueError("A selected asset is outside this report's selection.")
        row = by_id[value]
        path = index[value].get("original" if row["media_type"] == "video" else "motion")
        if not path or not Path(path).is_file():
            raise ValueError(
                f"No local motion file for {row['filename']}. Download it in Photos and refresh the media index."
            )
        result.append((row, Path(path)))
    return result


def probe(path, ffprobe):
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,color_transfer",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return json.loads(result.stdout)


def render_film(
    plan,
    index,
    records,
    output,
    *,
    pace="fast",
    bpm=130,
    music=None,
    music_start=0,
    credit=None,
    width=1080,
    progress=None,
):
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise ValueError("Install FFmpeg (including ffprobe) to render a local film.")
    if width not in {720, 1080}:
        raise ValueError("Film width must be 720 or 1080 pixels.")
    if (
        not isinstance(music_start, (int, float))
        or not math.isfinite(music_start)
        or not 0 <= music_start <= 3600
    ):
        raise ValueError("Invalid music start time.")
    if music and not Path(music).is_file():
        raise ValueError("The configured music file is unavailable.")
    sources = media_sources(plan, index, records)
    frames = clip_frames(len(sources), pace=pace, bpm=bpm)
    height, fps = width * 16 // 9, 30
    duration = sum(frames) / fps
    if duration > 300:
        raise ValueError("Film exceeds five minutes. Choose fewer clips or a faster pace.")
    music_looped = False
    if music:
        music_info = probe(music, ffprobe)
        if not any(s.get("codec_type") == "audio" for s in music_info.get("streams", [])):
            raise ValueError("The soundtrack has no audio stream.")
        music_duration = float(music_info["format"]["duration"])
        if (
            not math.isfinite(music_duration)
            or music_duration <= 0
            or music_start >= music_duration
        ):
            raise ValueError("Music start must be within a nonempty soundtrack.")
        music_looped = music_start + duration > music_duration
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if any(
        path.exists()
        for path in (output, output.with_suffix(".json"), output.with_suffix(".credits.txt"))
    ):
        raise ValueError("Output already exists. Choose a new filename.")
    manifest = []
    with tempfile.TemporaryDirectory(prefix="film-parts-", dir=output.parent) as temp:
        work = Path(temp)
        for i, ((row, source), count) in enumerate(zip(sources, frames, strict=True)):
            info = probe(source, ffprobe)
            if not any(s.get("codec_type") == "video" for s in info.get("streams", [])):
                raise ValueError(f"No video stream in {row['filename']}.")
            seconds = count / fps
            source_seconds = float(info["format"]["duration"])
            source_start = (
                max(0, (source_seconds - seconds) / 2) if row["media_type"] == "photo" else 0
            )
            hdr = any(
                s.get("color_transfer") in {"arib-std-b67", "smpte2084"}
                for s in info.get("streams", [])
            )
            tone = (
                "zscale=t=linear:npl=100,format=gbrpf32le,tonemap=hable,zscale=p=bt709:t=bt709:m=bt709:r=tv,format=yuv420p,"
                if hdr
                else ""
            )
            video = (
                f"[0:v]setpts=PTS-STARTPTS,{tone}split=2[fg][bg];"
                f"[bg]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},boxblur=24:2[back];"
                f"[fg]scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2[front];"
                f"[back][front]overlay=(W-w)/2:(H-h)/2,setsar=1,fps={fps},"
                f"tpad=stop_mode=clone:stop_duration={seconds:.6f},trim=end_frame={count},setpts=PTS-STARTPTS[v]"
            )
            part = work / f"{i:03d}.mp4"
            subprocess.run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-nostdin",
                    "-y",
                    "-ss",
                    str(source_start),
                    "-i",
                    str(source),
                    "-filter_complex",
                    video,
                    "-map",
                    "[v]",
                    "-an",
                    "-frames:v",
                    str(count),
                    "-c:v",
                    "libx264",
                    "-preset",
                    "fast",
                    "-crf",
                    "21",
                    "-pix_fmt",
                    "yuv420p",
                    "-colorspace",
                    "bt709",
                    "-color_primaries",
                    "bt709",
                    "-color_trc",
                    "bt709",
                    "-map_metadata",
                    "-1",
                    str(part),
                ],
                check=True,
                capture_output=True,
                timeout=180,
            )
            manifest.append(
                {
                    "uuid": row["uuid"],
                    "filename": row["filename"],
                    "frames": count,
                    "seconds": seconds,
                    "source_start_seconds": source_start,
                    "short_motion_padded": source_seconds < seconds,
                }
            )
            if progress:
                progress(i + 1, len(sources))
        concat = work / "parts.txt"
        concat.write_text("".join(f"file '{i:03d}.mp4'\n" for i in range(len(sources))))
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat),
        ]
        if music:
            if music_looped:
                command += ["-stream_loop", "-1"]
            command += [
                "-ss",
                str(music_start),
                "-i",
                str(music),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-af",
                f"atrim=duration={duration:.6f},asetpts=PTS-STARTPTS,loudnorm=I=-16:TP=-1.5:LRA=9,afade=t=in:d=0.1,afade=t=out:st={max(0, duration - 0.8):.6f}:d=0.8",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-ar",
                "48000",
            ]
        else:
            command += ["-map", "0:v:0", "-an"]
        command += [
            "-c:v",
            "copy",
            "-t",
            str(duration),
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-movflags",
            "+faststart",
        ]
        if music and credit:
            command += ["-metadata", "comment=" + credit.get("credit", "")]
        temporary_output = work / "finished.mp4"
        command.append(str(temporary_output))
        subprocess.run(command, check=True, capture_output=True, timeout=120)
        checked = probe(temporary_output, ffprobe)
        if music and not any(s.get("codec_type") == "audio" for s in checked.get("streams", [])):
            raise ValueError("Rendered film has no audio stream.")
        shutil.move(temporary_output, output)
    result = {
        "schema_version": 1,
        "clips": manifest,
        "duration_seconds": float(checked["format"]["duration"]),
        "width": width,
        "height": height,
        "fps": fps,
        "pace": pace,
        "bpm": bpm,
        "music": credit if music else None,
        "music_looped": music_looped,
        "music_start_seconds": music_start if music else None,
        "source_audio": "muted",
        "metadata": "Source metadata removed; optional music credit retained.",
    }
    output.with_suffix(".json").write_text(json.dumps(result, indent=2))
    if music and credit:
        output.with_suffix(".credits.txt").write_text(
            credit.get("credit", "")
            + "\nSource: "
            + credit.get("source", "")
            + "\nLicense: "
            + credit.get("license_url", "")
            + "\nChanges: excerpted, looped if needed, normalized and faded.\n"
        )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("plan", "data", "media-index", "output"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--music")
    parser.add_argument("--credit-json")
    parser.add_argument("--music-start", type=float, default=0)
    parser.add_argument("--bpm", type=float, default=130)
    parser.add_argument("--pace", choices=[*PACES, "original"], default="fast")
    parser.add_argument("--width", type=int, choices=[720, 1080], default=1080)
    args = parser.parse_args()
    result = render_film(
        json.loads(Path(args.plan).read_text()),
        json.loads(Path(args.media_index).read_text()),
        json.loads(Path(args.data).read_text())["records"],
        args.output,
        pace=args.pace,
        bpm=args.bpm,
        music=args.music,
        music_start=args.music_start,
        width=args.width,
        credit=json.loads(Path(args.credit_json).read_text()) if args.credit_json else None,
        progress=lambda done, total: print(f"Rendered {done}/{total}", flush=True),
    )
    print(
        json.dumps(
            {
                "output": args.output,
                "clips": len(result["clips"]),
                "seconds": result["duration_seconds"],
            }
        )
    )


if __name__ == "__main__":
    main()
