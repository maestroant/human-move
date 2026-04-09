from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from queue import Queue
from typing import Any, Awaitable, Callable

from playwright.async_api import async_playwright, Error as PlaywrightError

from mover import replay_track
from web_helper import ensure_overlay, clear_overlay, update_overlay, set_page_status
from server import start_server, wait_for_recorded_session


ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
DEFAULT_SESSIONS_FILE = DATA_DIR / "mouse_sessions.jsonl"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


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


async def main() -> None:
    args = parse_args()
    session_queue: Queue[dict[str, Any]] = Queue()
    server, server_thread = start_server(args.host, args.port, args.file, session_queue)
    base_url = f"http://{args.host}:{args.port}"

    print(f"Recorder page: {base_url}")
    print(f"Sessions are appended to {args.file}")
    print("First click starts recording. Second click stops recording and starts replay.")

    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=args.headless)
            page = await browser.new_page()
            await page.goto(base_url, wait_until="domcontentloaded")

            try:
                while not page.is_closed():
                    await set_page_status(page, "waiting for recording")
                    points = await wait_for_recorded_session(page, session_queue)
                    event_count = len(points)
    
                    print(f"Recorded {event_count} points, replaying...")
                    await page.bring_to_front()
                    await page.wait_for_timeout(150)
                    await set_page_status(page, f"replaying {event_count} events")
                    
                    handle_move: Callable[[int, int, int], Awaitable[None]] | None = None
                    if not args.no_overlay:
                        await ensure_overlay(page)
                        await clear_overlay(page)
                        last_overlay_time = [-16]
                        
                        async def _on_move_callback(point_time: int, x: int, y: int) -> None:
                            if point_time - last_overlay_time[0] >= 16:
                                await update_overlay(page, x, y)
                                last_overlay_time[0] = point_time
                                
                        handle_move = _on_move_callback
    
                    await replay_track(
                        page,
                        json.dumps(points),  # Здесь можно передать просто `points`
                        scale_to_viewport=not args.no_scale,
                        initial_delay_ms=200,
                        on_move=handle_move,
                        apply_trim=False,
                        apply_noise=False,
                        apply_rotation=True,
                        
                    )
                    await set_page_status(page, f"replay finished: {event_count} events")
                    print("Replay finished. Waiting for the next recording...")
            except (RuntimeError, PlaywrightError):
                print("\nBrowser was closed. Exiting gracefully...")

            if not page.is_closed():
                await browser.close()
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)


if __name__ == "__main__":
    asyncio.run(main())
