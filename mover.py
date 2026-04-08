from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import Page


DEFAULT_SESSIONS_FILE = Path("data/mouse_sessions.jsonl")
OVERLAY_ROOT_ID = "__human_move_overlay__"


def load_session_line(raw_line: str) -> dict[str, Any]:
    record = json.loads(raw_line)
    events = record.get("events")
    if not isinstance(events, list) or not events:
        raise ValueError("Session record must contain a non-empty 'events' list.")
    return record


def load_session_from_file(path: str | Path = DEFAULT_SESSIONS_FILE, line_number: int = -1) -> dict[str, Any]:
    session_path = Path(path)
    if not session_path.exists():
        raise FileNotFoundError(f"Session file not found: {session_path}")

    with session_path.open("r", encoding="utf-8") as handle:
        lines = [line.strip() for line in handle if line.strip()]

    if not lines:
        raise ValueError(f"No session records found in: {session_path}")

    try:
        raw_line = lines[line_number]
    except IndexError as error:
        raise IndexError(f"Line index {line_number} is out of range for {session_path}") from error

    return load_session_line(raw_line)


def get_replay_points(session_record: dict[str, Any]) -> list[dict[str, int]]:
    normalized_points: list[dict[str, int]] = []

    for event in session_record["events"]:
        normalized_points.append(
            {
                "t": int(event["t"]),
                "x": int(event["x"]),
                "y": int(event["y"]),
            }
        )

    return normalized_points


def _get_viewport_size(page: Page) -> tuple[int, int]:
    viewport = page.viewport_size
    if viewport:
        return int(viewport["width"]), int(viewport["height"])

    width = int(page.evaluate("() => window.innerWidth"))
    height = int(page.evaluate("() => window.innerHeight"))
    return width, height


def _ensure_overlay(page: Page) -> None:
    page.evaluate(
        """overlayId => {
            if (document.getElementById(overlayId)) {
                return;
            }

            const root = document.createElement("div");
            root.id = overlayId;
            root.innerHTML = `
              <style>
                #${overlayId} {
                  position: fixed;
                  inset: 0;
                  pointer-events: none;
                  z-index: 2147483647;
                }
                #${overlayId} .hm-trail {
                  position: absolute;
                  inset: 0;
                }
                #${overlayId} .hm-dot {
                  position: absolute;
                  width: 6px;
                  height: 6px;
                  margin-left: -3px;
                  margin-top: -3px;
                  border-radius: 999px;
                  background: rgba(11, 87, 208, 0.35);
                }
                #${overlayId} .hm-cursor {
                  position: absolute;
                  width: 18px;
                  height: 18px;
                  margin-left: -9px;
                  margin-top: -9px;
                  border: 2px solid #0b57d0;
                  border-radius: 999px;
                  background: rgba(11, 87, 208, 0.12);
                  box-shadow: 0 0 0 6px rgba(11, 87, 208, 0.08);
                }
              </style>
              <div class="hm-trail"></div>
              <div class="hm-cursor"></div>
            `;

            document.documentElement.append(root);
        }""",
        OVERLAY_ROOT_ID,
    )


def _clear_overlay(page: Page) -> None:
    page.evaluate(
        """overlayId => {
            const root = document.getElementById(overlayId);
            if (!root) {
                return;
            }

            const trail = root.querySelector(".hm-trail");
            const cursor = root.querySelector(".hm-cursor");
            if (trail) {
                trail.replaceChildren();
            }
            if (cursor) {
                cursor.style.left = "0px";
                cursor.style.top = "0px";
                cursor.style.opacity = "0";
            }
        }""",
        OVERLAY_ROOT_ID,
    )


def _update_overlay(page: Page, x: int, y: int) -> None:
    page.evaluate(
        """({ overlayId, x, y }) => {
            const root = document.getElementById(overlayId);
            if (!root) {
                return;
            }

            const trail = root.querySelector(".hm-trail");
            const cursor = root.querySelector(".hm-cursor");
            if (trail) {
                const dot = document.createElement("div");
                dot.className = "hm-dot";
                dot.style.left = `${x}px`;
                dot.style.top = `${y}px`;
                trail.append(dot);
            }
            if (cursor) {
                cursor.style.left = `${x}px`;
                cursor.style.top = `${y}px`;
                cursor.style.opacity = "1";
            }
        }""",
        {"overlayId": OVERLAY_ROOT_ID, "x": x, "y": y},
    )


def replay_track(
    page: Page,
    track: list[dict[str, int]],
    *,
    recorded_viewport: dict[str, Any] | None = None,
    scale_to_viewport: bool = True,
    initial_delay_ms: int = 0,
    show_overlay: bool = True,
    overlay_interval_ms: int = 16,
) -> int:
    if not track:
        return 0

    target_width, target_height = _get_viewport_size(page)
    source_width = int(recorded_viewport.get("width", target_width)) if recorded_viewport else target_width
    source_height = int(recorded_viewport.get("height", target_height)) if recorded_viewport else target_height

    scale_x = target_width / source_width if scale_to_viewport and source_width > 0 else 1.0
    scale_y = target_height / source_height if scale_to_viewport and source_height > 0 else 1.0

    if show_overlay:
        _ensure_overlay(page)
        _clear_overlay(page)

    if initial_delay_ms > 0:
        page.wait_for_timeout(initial_delay_ms)

    origin_time = int(track[0]["t"])
    started_at = time.perf_counter()
    last_overlay_time = -overlay_interval_ms

    first_point = track[0]
    first_x = round(int(first_point["x"]) * scale_x)
    first_y = round(int(first_point["y"]) * scale_y)
    page.mouse.move(
        first_x,
        first_y,
    )
    if show_overlay:
        _update_overlay(page, first_x, first_y)
        last_overlay_time = 0

    replayed = 1

    for point in track[1:]:
        point_time = int(point["t"]) - origin_time
        target_time = point_time / 1000.0
        remaining = target_time - (time.perf_counter() - started_at)
        if remaining > 0:
            time.sleep(remaining)

        current_x = round(int(point["x"]) * scale_x)
        current_y = round(int(point["y"]) * scale_y)
        page.mouse.move(current_x, current_y)
        if show_overlay and point_time - last_overlay_time >= overlay_interval_ms:
            _update_overlay(page, current_x, current_y)
            last_overlay_time = point_time
        replayed += 1

    if show_overlay and last_overlay_time != int(track[-1]["t"]) - origin_time:
        last_point = track[-1]
        _update_overlay(
            page,
            round(int(last_point["x"]) * scale_x),
            round(int(last_point["y"]) * scale_y),
        )

    return replayed
