"""Loopback-only video rendering for a private report. Never an MCP write tool."""

import argparse
import json
import secrets
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from film import clip_frames, media_sources, render_film


class FilmEditor:
    def __init__(
        self,
        *,
        report,
        data,
        media_index,
        output_dir,
        host="127.0.0.1",
        port=8770,
        music=None,
        credit=None,
        bpm=130,
        music_start=0,
        seed_plan=None,
    ):
        self.report = Path(report)
        self.records = data["records"]
        allowed = {row["uuid"] for row in self.records}
        self.index = {key: value for key, value in media_index.items() if key in allowed}
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        if host != "127.0.0.1":
            raise ValueError("The editor must bind to 127.0.0.1.")
        self.host, self.port, self.token = host, port, secrets.token_urlsafe(32)
        self.origin = f"http://{host}:{port}"
        self.music, self.credit, self.bpm, self.music_start = music, credit, bpm, music_start
        self.seed_plan = seed_plan
        if seed_plan:
            media_sources(seed_plan, self.index, self.records)
        self.jobs, self.lock = {}, threading.Lock()
        # Completed outputs remain available after restarting the editor.
        for manifest in sorted(
            self.output_dir.glob("trip-film-*.json"), key=lambda p: p.stat().st_mtime
        )[-100:]:
            job_id = manifest.stem.removeprefix("trip-film-")
            try:
                if (
                    uuid.UUID(hex=job_id).hex != job_id
                    or not manifest.with_suffix(".mp4").is_file()
                ):
                    continue
                result = json.loads(manifest.read_text())
                if not 1 <= len(result["clips"]) <= 100 or any(
                    r["uuid"] not in self.index for r in result["clips"]
                ):
                    continue
                self.jobs[job_id] = {
                    "id": job_id,
                    "status": "complete",
                    "completed": len(result["clips"]),
                    "total": len(result["clips"]),
                    "seconds": result["duration_seconds"],
                    "video_url": f"/renders/{job_id}.mp4",
                    "credit": result.get("music"),
                }
            except (ValueError, KeyError, TypeError, OSError):
                continue
        self.executor = ThreadPoolExecutor(max_workers=1)

    def authorize(self, host, origin, token):
        return (
            host == f"{self.host}:{self.port}"
            and origin == self.origin
            and isinstance(token, str)
            and secrets.compare_digest(token, self.token)
        )

    def start(self, request):
        if not isinstance(request, dict) or set(request) - {"plan", "music", "pace"}:
            raise ValueError("Invalid render request.")
        plan, music, pace = (
            request.get("plan"),
            request.get("music", "energy"),
            request.get("pace", "fast"),
        )
        if music not in {"energy", "none"}:
            raise ValueError("Unknown music preset.")
        if music == "energy" and not self.music:
            raise ValueError("No soundtrack is configured. Choose No music.")
        sources = media_sources(plan, self.index, self.records)
        clip_frames(len(sources), pace=pace, bpm=self.bpm)
        with self.lock:
            if any(job["status"] in {"queued", "rendering"} for job in self.jobs.values()):
                raise RuntimeError("A film is already rendering. Wait for it to finish.")
            job_id = uuid.uuid4().hex
            self.jobs[job_id] = {
                "id": job_id,
                "status": "queued",
                "completed": 0,
                "total": len(sources),
            }
            self.seed_plan = plan
            (self.output_dir / "motion-plan.json").write_text(json.dumps(plan, indent=2))
            self.executor.submit(self._run, job_id, plan, music, pace)
            return dict(self.jobs[job_id])

    def _run(self, job_id, plan, music, pace):
        output = self.output_dir / f"trip-film-{job_id}.mp4"

        def progress(done, total):
            with self.lock:
                self.jobs[job_id].update(completed=done, total=total)

        with self.lock:
            self.jobs[job_id]["status"] = "rendering"
        try:
            result = render_film(
                plan,
                self.index,
                self.records,
                output,
                pace=pace,
                bpm=self.bpm,
                music=self.music if music == "energy" else None,
                credit=self.credit,
                music_start=self.music_start,
                progress=progress,
            )
            with self.lock:
                self.jobs[job_id].update(
                    status="complete",
                    seconds=result["duration_seconds"],
                    video_url=f"/renders/{job_id}.mp4",
                    credit=self.credit if music == "energy" else None,
                )
        except Exception:  # noqa: BLE001 - subprocess errors may contain private filesystem paths
            with self.lock:
                self.jobs[job_id].update(
                    status="failed",
                    error="Rendering failed. Check local media availability and FFmpeg support.",
                )

    def page(self):
        html = self.report.read_text()
        with self.lock:
            complete = [job for job in self.jobs.values() if job["status"] == "complete"]
            latest = dict(complete[-1]) if complete else None
        config = {
            "token": self.token,
            "music_title": (self.credit or {}).get("title", "Travel energy"),
            "bpm": self.bpm,
            "music_available": bool(self.music),
            "seed_plan": self.seed_plan,
            "latest": latest,
        }
        encoded = (
            json.dumps(config)
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
            .replace("&", "\\u0026")
        )
        html = html.replace("connect-src 'none'", "connect-src 'self'; media-src 'self'")
        html = html.replace("script-src 'unsafe-inline'", "script-src 'self' 'unsafe-inline'")
        bridge = Path(__file__).with_name("editor_bridge.js").read_text()
        injected = f'<script id="trip-editor-config" type="application/json">{encoded}</script><script>{bridge}</script>'
        return html.replace("</body>", injected + "</body>")


def handler_for(editor):
    class Handler(BaseHTTPRequestHandler):
        def send(self, status, content, mime="application/json"):
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            # Browsers cancel video preloads when they have enough metadata.
            with suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(content)

        def json(self, status, data):
            self.send(status, json.dumps(data).encode())

        def do_GET(self):
            if self.headers.get("Host") != f"{editor.host}:{editor.port}":
                self.json(403, {"error": "Invalid local host."})
                return
            path = urlsplit(self.path).path
            if path == "/":
                self.send(200, editor.page().encode(), "text/html; charset=utf-8")
            elif path.startswith("/api/render/"):
                with editor.lock:
                    job = dict(editor.jobs.get(path.rsplit("/", 1)[-1], {}))
                self.json(200 if job else 404, job or {"error": "Render not found."})
            elif path.startswith("/renders/"):
                job_id = path.rsplit("/", 1)[-1].removesuffix(".mp4")
                with editor.lock:
                    job = editor.jobs.get(job_id, {})
                if job.get("status") != "complete":
                    self.json(404, {"error": "Video not available."})
                else:
                    self.send(
                        200,
                        (editor.output_dir / f"trip-film-{job_id}.mp4").read_bytes(),
                        "video/mp4",
                    )
            else:
                self.json(404, {"error": "Not found."})

        def do_POST(self):
            if not editor.authorize(
                self.headers.get("Host"),
                self.headers.get("Origin"),
                self.headers.get("X-Trip-Token"),
            ):
                self.json(403, {"error": "This action must come from the local editor."})
                return
            if self.path != "/api/render" or self.headers.get("Content-Type") != "application/json":
                self.json(400, {"error": "Invalid render request."})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 32768:
                    raise ValueError("Invalid request size.")
                request = json.loads(self.rfile.read(length))
                self.json(202, editor.start(request))
            except (ValueError, TypeError, KeyError) as error:
                self.json(400, {"error": str(error)})
            except RuntimeError as error:
                self.json(409, {"error": str(error)})

        def log_message(self, *args):
            pass

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("report", "data", "media-index", "output-dir"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--music")
    parser.add_argument("--credit-json")
    parser.add_argument("--bpm", type=float, default=130)
    parser.add_argument("--music-start", type=float, default=0)
    parser.add_argument("--seed-plan")
    parser.add_argument("--port", type=int, default=8770)
    args = parser.parse_args()
    data = json.loads(Path(args.data).read_text())
    if "record_keys" in data:
        data["records"] = [
            dict(zip(data["record_keys"], r, strict=True)) if isinstance(r, list) else r
            for r in data["records"]
        ]
    editor = FilmEditor(
        report=args.report,
        data=data,
        media_index=json.loads(Path(args.media_index).read_text()),
        output_dir=args.output_dir,
        port=args.port,
        music=args.music,
        bpm=args.bpm,
        music_start=args.music_start,
        credit=json.loads(Path(args.credit_json).read_text()) if args.credit_json else None,
        seed_plan=json.loads(Path(args.seed_plan).read_text()) if args.seed_plan else None,
    )
    print(editor.origin, flush=True)
    ThreadingHTTPServer((editor.host, editor.port), handler_for(editor)).serve_forever()


if __name__ == "__main__":
    main()
