from __future__ import annotations

import asyncio
import copy
import json
import math
import random
import time
from typing import Any, Awaitable, Callable

from playwright.async_api import Page


def _trim_track(track: list[dict[str, Any]], max_start_pct: float = 0.15, max_end_pct: float = 0.05) -> list[dict[str, Any]]:
    """
    Случайным образом обрезает начало (до max_start_pct) и конец (до max_end_pct) трека.
    """
    if len(track) < 10:  # Слишком короткие треки лучше не трогать
        return track
        
    start_trim = int(len(track) * random.uniform(0, max_start_pct))
    end_trim = int(len(track) * random.uniform(0, max_end_pct))
    
    if end_trim == 0:
        return track[start_trim:]
    return track[start_trim:-end_trim]


def _add_noise_to_track(track: list[dict[str, Any]], tremor_amplitude: float = 1.5) -> list[dict[str, Any]]:
    """
    Добавляет естественный микротремор к координатам трека.
    Использует плавное случайное блуждание, чтобы избежать "роботизированных" резких скачков.
    """
    if len(track) < 3:
        return track

    noisy_track = copy.deepcopy(track)
    offset_x, offset_y = 0.0, 0.0
    
    # Первую и последнюю точки оставляем на месте, чтобы не сбить старт и прицеливание
    for i in range(1, len(noisy_track) - 1):
        offset_x += random.gauss(0, tremor_amplitude * 0.25)
        offset_y += random.gauss(0, tremor_amplitude * 0.25)
        
        offset_x = max(-tremor_amplitude, min(tremor_amplitude, offset_x))
        offset_y = max(-tremor_amplitude, min(tremor_amplitude, offset_y))
        
        noisy_track[i]["x"] = int(round(noisy_track[i]["x"] + offset_x))
        noisy_track[i]["y"] = int(round(noisy_track[i]["y"] + offset_y))
        
    return noisy_track


def _rotate_track(track: list[dict[str, Any]], angle_degrees: float | None = None, max_variance: float = 10.0) -> list[dict[str, Any]]:
    """
    Поворачивает трек на заданный угол вокруг его начальной точки.
    Если угол не задан, выбирает случайный угол в диапазоне от -max_variance до +max_variance.
    """
    if len(track) < 2:
        return track
        
    rotated = copy.deepcopy(track)
    ox = rotated[0]["x"]
    oy = rotated[0]["y"]
    
    # Если угол не передан жестко, выбираем случайное отклонение
    if angle_degrees is None:
        angle_degrees = random.uniform(-max_variance, max_variance)
        
    radians = math.radians(angle_degrees)
    cos_a = math.cos(radians)
    sin_a = math.sin(radians)
    
    for point in rotated[1:]:
        dx = point["x"] - ox
        dy = point["y"] - oy
        
        point["x"] = int(round(ox + dx * cos_a - dy * sin_a))
        point["y"] = int(round(oy + dx * sin_a + dy * cos_a))
        
    return rotated


async def _get_viewport_size(page: Page) -> tuple[int, int]:
    viewport = page.viewport_size
    if viewport:
        return int(viewport["width"]), int(viewport["height"])

    width = int(await page.evaluate("() => window.innerWidth"))
    height = int(await page.evaluate("() => window.innerHeight"))
    return width, height


async def replay_track(
    page: Page,
    track_data: str | list[dict[str, Any]],
    *,
    recorded_viewport: dict[str, Any] | None = None,
    scale_to_viewport: bool = True,
    initial_delay_ms: int = 0,
    on_move: Callable[[int, int, int], Awaitable[None]] | None = None,
    apply_trim: bool = True,
    apply_noise: bool = True,
    apply_rotation: bool = True,
) -> int:
    """
    Воспроизводит записанную траекторию движения мыши на указанной странице Playwright.

    Аргументы:
        page: Экземпляр асинхронной страницы Playwright (Page), на которой будет воспроизведен трек.
        track_data: Строка JSON или список словарей, представляющих события мыши.
            Каждое событие должно содержать 't' (временная метка), а также координаты 'x' и 'y'.
        recorded_viewport: Словарь, содержащий 'width' (ширину) и 'height' (высоту)
            области просмотра, в которой изначально был записан трек.
        scale_to_viewport: Если True, масштабирует записанные координаты в соответствии с текущим
            размером области просмотра. По умолчанию True.
        initial_delay_ms: Количество миллисекунд для ожидания перед началом воспроизведения. По умолчанию 0.
        on_move: Опциональная асинхронная функция обратного вызова, выполняемая при каждом движении мыши.
            Она получает относительную временную метку, а также координаты x и y.
        apply_trim: Включить обрезку трека (по умолчанию True).
        apply_noise: Включить добавление микротремора (по умолчанию True).
        apply_rotation: Включить случайный поворот трека (по умолчанию True).

    Возвращает:
        Количество успешно воспроизведенных событий мыши.
    """
    if isinstance(track_data, str):
        track = json.loads(track_data)
    else:
        track = track_data

    if not track:
        return 0

    if apply_trim:
        track = _trim_track(track)
    if apply_noise:
        track = _add_noise_to_track(track)
    if apply_rotation:
        track = _rotate_track(track)

    if not track:
        return 0

    target_width, target_height = await _get_viewport_size(page)
    source_width = int(recorded_viewport.get("width", target_width)) if recorded_viewport else target_width
    source_height = int(recorded_viewport.get("height", target_height)) if recorded_viewport else target_height

    scale_x = target_width / source_width if scale_to_viewport and source_width > 0 else 1.0
    scale_y = target_height / source_height if scale_to_viewport and source_height > 0 else 1.0

    if initial_delay_ms > 0:
        await page.wait_for_timeout(initial_delay_ms)

    origin_time = int(track[0]["t"])
    started_at = time.perf_counter()

    first_point = track[0]
    first_x = round(int(first_point["x"]) * scale_x)
    first_y = round(int(first_point["y"]) * scale_y)
    await page.mouse.move(
        first_x,
        first_y,
    )
    if on_move:
        await on_move(0, first_x, first_y)

    replayed = 1

    for point in track[1:]:
        point_time = int(point["t"]) - origin_time
        target_time = point_time / 1000.0
        remaining = target_time - (time.perf_counter() - started_at)
        if remaining > 0:
            await asyncio.sleep(remaining)

        current_x = round(int(point["x"]) * scale_x)
        current_y = round(int(point["y"]) * scale_y)
        await page.mouse.move(current_x, current_y)
        if on_move:
            await on_move(point_time, current_x, current_y)
        replayed += 1

    return replayed
