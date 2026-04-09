import asyncio
import json
import threading
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Empty, Queue
from typing import Any

from playwright.async_api import Page

ROOT_DIR = Path(__file__).resolve().parent
STATIC_DIR = ROOT_DIR / "static"

class RecorderHandler(SimpleHTTPRequestHandler):
    session_queue: Queue[dict[str, Any]]
    sessions_file: Path

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def do_POST(self) -> None:
        if self.path != "/api/sessions":
            self.send_error(HTTPStatus.NOT_FOUND, "Unknown endpoint")
            return

        content_length = self.headers.get("Content-Length")
        if not content_length:
            self.send_error(HTTPStatus.BAD_REQUEST, "Missing Content-Length header")
            return

        raw_body = self.rfile.read(int(content_length))
        try:
            events = json.loads(raw_body)
        except json.JSONDecodeError:
            self.send_error(HTTPStatus.BAD_REQUEST, "Request body must be valid JSON")
            return

        if not isinstance(events, list) or not events:
            self.send_error(HTTPStatus.BAD_REQUEST, "Payload must be a non-empty JSON array of events")
            return

        self.sessions_file.parent.mkdir(exist_ok=True)
        with self.sessions_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(events, ensure_ascii=True) + "\n")

        self.session_queue.put(events)

        response = json.dumps(
            {"status": "ok", "saved_to": str(self.sessions_file.name), "event_count": len(events)}
        ).encode("utf-8")
        self.send_response(HTTPStatus.CREATED)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, format: str, *args) -> None:
        print(f"[server] {self.address_string()} - {format % args}")

def start_server(host: str, port: int, sessions_file: Path, session_queue: Queue[dict[str, Any]]) -> tuple[ThreadingHTTPServer, threading.Thread]:
    RecorderHandler.sessions_file = sessions_file
    RecorderHandler.session_queue = session_queue
    server = ThreadingHTTPServer((host, port), RecorderHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread

async def wait_for_recorded_session(page: Page, session_queue: Queue[dict[str, Any]]) -> dict[str, Any]:
    while not page.is_closed():
        try:
            return session_queue.get_nowait()
        except Empty:
            await asyncio.sleep(0.05)
    raise RuntimeError("Recorder page was closed before a trajectory was saved.")