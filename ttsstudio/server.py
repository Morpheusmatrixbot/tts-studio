"""Local HTTP server and JSON API.

Binds to the loopback interface only. The Host header is checked on every
request so a hostile page cannot reach this API by pointing a domain it
controls at 127.0.0.1 (DNS rebinding); file serving is confined to folders the
user has actually narrated into.
"""

from __future__ import annotations

import json
import mimetypes
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import audio, cloud, extract, jobs, paths, runtime, settings, voices
from .engines import ENGINES, backend_for, platform_label

HOST = "127.0.0.1"
DEFAULT_PORT = 8766
MAX_UPLOAD_BYTES = 200 * 1024 * 1024

_ALLOWED_MEDIA_ROOTS: set[Path] = set()
_MEDIA_LOCK = threading.Lock()


def allow_media_root(path: Path) -> None:
    with _MEDIA_LOCK:
        _ALLOWED_MEDIA_ROOTS.add(path.resolve())


def _media_allowed(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    if resolved.suffix.lower() not in {".wav", ".mp3", ".json"}:
        return False
    with _MEDIA_LOCK:
        roots = list(_ALLOWED_MEDIA_ROOTS)
    roots.append(voices.samples_dir().resolve())
    roots.append(paths.default_output_dir().resolve())
    for root in roots:
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


class Handler(BaseHTTPRequestHandler):
    server_version = "TTSStudio"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        return  # quiet by default; job logs carry what matters

    # -- plumbing -----------------------------------------------------------

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0].strip().lower()
        return host in {"127.0.0.1", "localhost", "[::1]", "::1", ""}

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        try:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _json(self, code: int, payload) -> None:
        self._send(code, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return b""
        if length > MAX_UPLOAD_BYTES:
            raise ValueError("Upload is too large")
        return self.rfile.read(length)

    def _json_body(self) -> dict:
        raw = self._body()
        return json.loads(raw.decode("utf-8")) if raw else {}

    # -- routes -------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        if not self._host_ok():
            self._json(403, {"error": "forbidden"})
            return
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)
        try:
            if path in {"/", "/index.html"}:
                self._serve_asset("index.html", "text/html; charset=utf-8")
                return
            if path.startswith("/static/"):
                self._serve_static(path[len("/static/") :])
                return
            if path == "/api/health":
                self._json(200, {"ok": True})
                return
            if path == "/api/bootstrap":
                self._json(200, self._bootstrap())
                return
            if path == "/api/edge-voices":
                self._json(200, {"voices": cloud.edge_voices(refresh="refresh" in query)})
                return
            if path == "/api/samples":
                self._json(200, {"samples": voices.list_samples()})
                return
            if path == "/api/settings":
                self._json(200, self._public_settings())
                return
            if path.startswith("/api/jobs/"):
                snap = jobs.snapshot(path.rsplit("/", 1)[-1])
                self._json(200, snap) if snap else self._json(404, {"error": "job not found"})
                return
            if path == "/media":
                self._serve_media(query)
                return
            self._json(404, {"error": "not found"})
        except Exception as exc:  # noqa: BLE001
            self._json(500, {"error": str(exc)})

    def do_POST(self) -> None:  # noqa: N802
        if not self._host_ok():
            self._json(403, {"error": "forbidden"})
            return
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)
        try:
            if path == "/api/extract":
                filename = (query.get("filename") or ["document.txt"])[0]
                doc = extract.extract(self._body(), filename)
                self._json(200, doc)
                return
            if path == "/api/samples":
                filename = (query.get("filename") or ["voice.wav"])[0]
                self._json(200, voices.save_sample(self._body(), filename))
                return
            if path == "/api/samples/delete":
                voices.delete_sample(str(self._json_body().get("id") or ""))
                self._json(200, {"ok": True, "samples": voices.list_samples()})
                return
            if path == "/api/settings":
                saved = settings.save(self._json_body())
                self._json(200, {"ok": True, "settings": _redact(saved)})
                return
            if path == "/api/elevenlabs/verify":
                key = str(self._json_body().get("key") or "").strip()
                result = cloud.verify_elevenlabs_key(key) if key else {"ok": False, "error": "empty key"}
                if result.get("ok"):
                    settings.save({"elevenlabs_api_key": key})
                self._json(200, result)
                return
            if path == "/api/elevenlabs/voices":
                self._json(200, {"voices": cloud.elevenlabs_voices()})
                return
            if path.startswith("/api/engines/") and path.endswith("/install"):
                engine_id = path.split("/")[3]
                self._json(200, {"job_id": jobs.start_install(engine_id)})
                return
            if path.startswith("/api/engines/") and path.endswith("/uninstall"):
                engine_id = path.split("/")[3]
                runtime.uninstall_engine(engine_id)
                self._json(200, {"ok": True, "status": runtime.engine_status(engine_id)})
                return
            if path == "/api/jobs":
                payload = self._json_body()
                out_dir = str(payload.get("output_dir") or "").strip()
                if out_dir:
                    allow_media_root(Path(out_dir).expanduser())
                self._json(200, {"job_id": jobs.start_narration(payload)})
                return
            if path.startswith("/api/jobs/") and path.endswith("/cancel"):
                self._json(200, {"ok": jobs.cancel(path.split("/")[3])})
                return
            if path == "/api/reveal":
                self._reveal(Path(str(self._json_body().get("path") or "")))
                self._json(200, {"ok": True})
                return
            self._json(404, {"error": "not found"})
        except Exception as exc:  # noqa: BLE001
            self._json(500, {"error": str(exc)})

    # -- helpers ------------------------------------------------------------

    def _serve_asset(self, name: str, content_type: str) -> None:
        f = paths.resource_path("web", name)
        if not f.exists():
            self._json(500, {"error": f"missing asset {name}"})
            return
        self._send(200, f.read_bytes(), content_type)

    def _serve_static(self, rel: str) -> None:
        if ".." in rel.split("/"):
            self._json(403, {"error": "forbidden"})
            return
        f = paths.resource_path("web", *rel.split("/"))
        web_root = paths.resource_path("web").resolve()
        try:
            f.resolve().relative_to(web_root)
        except ValueError:
            self._json(403, {"error": "forbidden"})
            return
        if not f.is_file():
            self._json(404, {"error": "not found"})
            return
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        if f.suffix == ".js":
            ctype = "text/javascript; charset=utf-8"
        elif f.suffix == ".json":
            ctype = "application/json; charset=utf-8"
        elif f.suffix == ".css":
            ctype = "text/css; charset=utf-8"
        self._send(200, f.read_bytes(), ctype)

    def _serve_media(self, query: dict) -> None:
        raw = (query.get("path") or [""])[0]
        if not raw:
            self._json(400, {"error": "missing path"})
            return
        f = Path(raw)
        if not _media_allowed(f):
            self._json(403, {"error": "forbidden"})
            return
        if not f.is_file():
            self._json(404, {"error": "file missing"})
            return
        ctype = {"mp3": "audio/mpeg", "wav": "audio/wav"}.get(
            f.suffix.lower().lstrip("."), "application/json"
        )
        self._send(200, f.read_bytes(), ctype)

    def _bootstrap(self) -> dict:
        engine_list = []
        for engine in ENGINES:
            status = runtime.engine_status(engine.id)
            backend = backend_for(engine.id)
            engine_list.append(
                {
                    "id": engine.id,
                    "label": engine.label,
                    "kind": engine.kind,
                    "summary": engine.summary,
                    "needs_install": engine.needs_install,
                    "needs_api_key": engine.needs_api_key,
                    "supports_voice_clone": engine.supports_voice_clone,
                    "supports_voice_presets": engine.supports_voice_presets,
                    "installed": status.get("installed", False),
                    "backend": status.get("backend"),
                    "backend_label": status.get("backend_label"),
                    "accelerator": status.get("accelerator"),
                    "approx_mb": status.get("approx_mb", 0),
                    "worker_available": backend is not None,
                }
            )
        return {
            "platform": platform_label(),
            "engines": engine_list,
            "kokoro_voices": voices.KOKORO_VOICES,
            "kokoro_languages": voices.KOKORO_LANGUAGES,
            "chatterbox_languages": voices.CHATTERBOX_LANGUAGES,
            "elevenlabs_models": voices.ELEVENLABS_MODELS,
            "elevenlabs_keys_url": cloud.ELEVENLABS_KEYS_URL,
            "samples": voices.list_samples(),
            "settings": self._public_settings(),
            "mp3_supported": audio.mp3_supported(),
            "data_dir": str(paths.data_dir()),
            "disk_usage": runtime.disk_usage(),
        }

    def _public_settings(self) -> dict:
        return _redact(settings.load())

    def _reveal(self, target: Path) -> None:
        if not target.exists():
            raise FileNotFoundError(str(target))
        if sys.platform == "darwin":
            subprocess.run(["open", "-R", str(target)], check=False)
        elif sys.platform == "win32":
            subprocess.run(["explorer", "/select,", str(target)], check=False)
        else:
            subprocess.run(["xdg-open", str(target.parent)], check=False)


def _redact(data: dict) -> dict:
    """Never hand the API key back to the page — only whether one is stored."""
    out = dict(data)
    key = str(out.pop("elevenlabs_api_key", "") or "")
    out["has_elevenlabs_key"] = bool(key or settings.elevenlabs_key())
    return out


class Server(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def serve(port: int = DEFAULT_PORT, open_browser: bool = True) -> None:
    paths.ensure_dirs()
    allow_media_root(paths.default_output_dir())
    stored = settings.load().get("output_dir")
    if stored:
        allow_media_root(Path(stored))

    for candidate in range(port, port + 20):
        try:
            httpd = Server((HOST, candidate), Handler)
            break
        except OSError:
            continue
    else:
        raise SystemExit(f"Could not bind a port in {port}–{port + 19}")

    url = f"http://{HOST}:{httpd.server_address[1]}/"
    print(f"TTS Studio → {url}", flush=True)
    print(f"Data folder: {paths.data_dir()}", flush=True)

    threading.Thread(target=cloud.edge_voices, daemon=True, name="edge-voices").start()
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping…", flush=True)
    finally:
        httpd.server_close()
