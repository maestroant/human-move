from __future__ import annotations

import argparse
import json
import threading
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Empty, Queue
from typing import Any

from playwright.sync_api import Page, sync_playwright

from mover import replay_track


ROOT_DIR = Path(__file__).resolve().parent
STATIC_DIR = ROOT_DIR / "static"
DATA_DIR = ROOT_DIR / "data"
DEFAULT_SESSIONS_FILE = DATA_DIR / "mouse_sessions.jsonl"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


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
            payload = json.loads(raw_body)
        except json.JSONDecodeError:
            self.send_error(HTTPStatus.BAD_REQUEST, "Request body must be valid JSON")
            return

        events = payload.get("events")
        if not isinstance(events, list) or not events:
            self.send_error(HTTPStatus.BAD_REQUEST, "Payload must contain a non-empty events list")
            return

        record = {
            "session_id": payload.get("session_id"),
            "started_at": payload.get("started_at"),
            "ended_at": payload.get("ended_at"),
            "event_count": len(events),
            "page": payload.get("page"),
            "events": events,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }

        self.sessions_file.parent.mkdir(exist_ok=True)
        with self.sessions_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=True) + "\n")

        self.session_queue.put(record)

        response = json.dumps(
            {
                "status": "ok",
                "saved_to": str(self.sessions_file.name),
                "event_count": len(events),
            }
        ).encode("utf-8")
        self.send_response(HTTPStatus.CREATED)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, format: str, *args) -> None:
        print(f"[server] {self.address_string()} - {format % args}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Open the recorder page, wait for a recorded path, then replay it automatically."
    )
    parser.add_argument(
        "--line",
        type=int,
        default=-1,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--file",
        type=Path,
        default=DEFAULT_SESSIONS_FILE,
        help="Path to the JSONL session file. Default: data/mouse_sessions.jsonl",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help="Host for the local recorder server. Default: 127.0.0.1",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help="Port for the local recorder server. Default: 8000",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser in headless mode.",
    )
    parser.add_argument(
        "--no-scale",
        action="store_true",
        help="Use recorded coordinates as-is without scaling to the current viewport.",
    )
    parser.add_argument(
        "--no-overlay",
        action="store_true",
        help="Do not draw the replay cursor and trail overlay on the page.",
    )
    args, unknown = parser.parse_known_args()
    if unknown:
        print(f"Ignoring unknown arguments: {' '.join(unknown)}")
    return args


def start_server(
    host: str,
    port: int,
    sessions_file: Path,
    session_queue: Queue[dict[str, Any]],
) -> tuple[ThreadingHTTPServer, threading.Thread]:
    RecorderHandler.sessions_file = sessions_file
    RecorderHandler.session_queue = session_queue

    server = ThreadingHTTPServer((host, port), RecorderHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def set_page_status(page: Page, text: str) -> None:
    page.evaluate(
        """text => {
            const node = document.getElementById("status");
            if (node) {
                node.textContent = `Status: ${text}`;
            }
        }""",
        text,
    )


def wait_for_recorded_session(page: Page, session_queue: Queue[dict[str, Any]]) -> dict[str, Any]:
    while not page.is_closed():
        try:
            return session_queue.get_nowait()
        except Empty:
            page.wait_for_timeout(50)

    raise RuntimeError("Recorder page was closed before a trajectory was saved.")


def main() -> None:
    args = parse_args()
    session_queue: Queue[dict[str, Any]] = Queue()
    server, server_thread = start_server(args.host, args.port, args.file, session_queue)
    base_url = f"http://{args.host}:{args.port}"

    print(f"Recorder page: {base_url}")
    print(f"Sessions are appended to {args.file}")
    print("First click starts recording. Second click stops recording and starts replay.")

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=args.headless)
            page = browser.new_page()
            page.goto(base_url, wait_until="domcontentloaded")

            while not page.is_closed():
                set_page_status(page, "waiting for recording")
                session_record = wait_for_recorded_session(page, session_queue)
                points = session_record["events"]
                event_count = len(points)

                print(f"Recorded {event_count} points, replaying...")
                page.bring_to_front()
                page.wait_for_timeout(150)
                set_page_status(page, f"replaying {event_count} events")
                replay_track(
                    page,
                    points,
                    recorded_viewport=session_record.get("page", {}).get("viewport"),
                    scale_to_viewport=not args.no_scale,
                    initial_delay_ms=200,
                    show_overlay=not args.no_overlay,
                )
                set_page_status(page, f"replay finished: {event_count} events")
                print("Replay finished. Waiting for the next recording...")

            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)


if __name__ == "__main__":
    main()
