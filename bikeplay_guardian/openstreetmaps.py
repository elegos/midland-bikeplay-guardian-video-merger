from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from threading import Semaphore
from typing import Any

import logging
import math
import time

from gpxpy import parse
from gpxpy.gpx import GPXTrackPoint
from PIL import Image, ImageDraw, ImageFont

import requests

from bikeplay_guardian.utils import progress_bar

TILE_SIZE = 256  # OSM tile size in pixels
DEFAULT_ZOOM_LEVEL = 15 # OSM zoom level
USER_AGENT = 'Bikeplay Guardian GPX Tool 1.0'

requests_semaphore = Semaphore()

def make_na_map_placeholder(window_width_px: int, window_height_px: int) -> Image.Image:
    text = 'N/A'

    img = Image.new('RGB', (window_width_px, window_height_px), (int(0.18 * 255), int(0.18 * 255), int(0.18 * 255)))
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(Path(__file__).parent / 'DejaVuSans-Bold.ttf', 40)

    text_bbox = draw.textbbox((0, 0), text, font=font)
    text_width = text_bbox[2] - text_bbox[0]
    text_height = text_bbox[3] - text_bbox[1]

    draw.text(((window_width_px - text_width) // 2, (window_height_px - text_height) // 2), text, font=font, fill=(255, 255, 255))
    return img.copy()

def gpx_to_osm_map(
        gpx: Path, osm_z_level: int, window_width_px: int, window_height_px: int
    ) -> tuple[Image.Image, tuple[int, int]]:
    """Convert a GPX file to an Open Street Map image.
    Outputs the map image and the relative origin's OSM slippy map X/Y coordinates.
    window_width_px and window_height_px are the size of the window that will need to be rendered, in pixels
    """
    with gpx.open('r') as f:
        gpx_data = parse(f)

    # For each track point, find the surrounding x/y tile coordinates at the given zoom level (osm_z_level),
    # ensuring that the margins fit within the specified window size. Save these coordinates in coords_to_download.

    # Calculate the needed number of tiles
    n = 2.0 ** osm_z_level
    coords_to_download: set[tuple[int, int]] = set()
    logging.info(f"{__name__} Calculating needed tiles...")
    for track in gpx_data.tracks:
        for trkseg in track.segments:
            for trkpt in trkseg.points:
                if trkpt.time is not None and trkpt.time.year != 1900 and trkpt.latitude != 0 and trkpt.longitude != 0:
                    # Get the tile coordinates which contains the point
                    lat_rad = math.radians(trkpt.latitude)
                    xtile = int((trkpt.longitude + 180.0) / 360.0 * n)
                    ytile = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)

                    tiles_margin_x = math.ceil(window_width_px / (2 * TILE_SIZE))
                    tiles_margin_y = math.ceil(window_height_px / (2 * TILE_SIZE))

                    for dx in range(-tiles_margin_x, tiles_margin_x + 1):
                        for dy in range(-tiles_margin_y, tiles_margin_y + 1):
                            coords_to_download.add((xtile + dx, ytile + dy))

    num_coords = len(coords_to_download)
    logging.info(f"{__name__} Total tiles to download: {num_coords}")
    tiles: list[tuple[tuple[int, int], Image.Image]] = []
    for idx, (tile_x, tile_y) in enumerate(coords_to_download):
        progress_bar(idx + 1, num_coords)

        url = f"https://a.tile.openstreetmap.org/{osm_z_level}/{tile_x}/{tile_y}.png"
        response = requests.get(url, headers={"User-Agent": USER_AGENT})
        time.sleep(0.1)  # Be nice to the server
        if response.status_code == 200:
            image = Image.open(BytesIO(response.content))
            tiles.append(((tile_x, tile_y), image))

    # Create the final map image, filled with neutral gray color, then paste the tiles in the correct positions
    logging.info(f"{__name__} Creating map image...")
    min_x = min(tile_x for tile_x, _ in coords_to_download)
    max_x = max(tile_x for tile_x, _ in coords_to_download)
    min_y = min(tile_y for _, tile_y in coords_to_download)
    max_y = max(tile_y for _, tile_y in coords_to_download)

    width = (max_x - min_x + 1) * TILE_SIZE
    height = (max_y - min_y + 1) * TILE_SIZE

    map_image = Image.new('RGB', (width, height), (128, 128, 128))

    tiles.sort(key=lambda t: (t[0][0], t[0][1]))
    for (tile_x, tile_y), tile_img in tiles:
        x = (tile_x - min_x) * TILE_SIZE
        y = (tile_y - min_y) * TILE_SIZE
        map_image.paste(tile_img, (x, y))

    return map_image, (min_x, min_y)

def latlon_to_xy(coords: tuple[float, float], origin: tuple[int, int], zoom: int = DEFAULT_ZOOM_LEVEL, tile_size: int = TILE_SIZE) -> tuple[int, int]:
    """Convert latitude/longitude to x/y coordinates on the map image."""

    lat_rad = math.radians(coords[0])

    n = 2.0 ** zoom
    x = round((coords[1] + 180.0) / 360.0 * n * TILE_SIZE)
    y = round((1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n * tile_size)

    return x - origin[0] * tile_size, y - origin[1] * tile_size

def crop_window(img: Image.Image, center: tuple[int, int], window_w: int, window_h: int):
    left = center[0] - window_w // 2
    upper = center[1] - window_h // 2
    right = left + window_w
    lower = upper + window_h

    return img.crop((left, upper, right, lower))

def draw_marker(img: Image.Image, center: tuple[int, int], angle: float|None = None, radius: int = 10, fill_color: str = "blue"):
    draw = ImageDraw.Draw(img)

    if angle is None:  # draw a circle
        draw.ellipse((center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius), fill=fill_color)
    else:  # draw an oriented triangle
        p1 = (center[0] + radius * math.cos(angle),
              center[1] + radius * math.sin(angle))
        p2 = (center[0] + radius * math.cos(angle + 2.5),
              center[1] + radius * math.sin(angle + 2.5))
        p3 = (center[0] + radius * math.cos(angle - 2.5),
              center[1] + radius * math.sin(angle - 2.5))
        draw.polygon([p1, p2, p3], fill=fill_color)

def bearing(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]

    return math.atan2(dy, dx)

def gpx_to_frames(gpx_points: list[GPXTrackPoint], img: Image.Image, osm_origin: tuple[int, int], osm_zoom: int, osm_tile_size: int, window_width_px: int, window_height_px: int) -> list[tuple[datetime, Image.Image]]:
    '''Generates a list of frames, 1 per GPX track point (which should be 1 per second).'''
    coords = [latlon_to_xy((p.latitude, p.longitude), osm_origin, osm_zoom, osm_tile_size) for p in gpx_points]

    frames: list[tuple[datetime, Image.Image]] = []
    for i, (x, y) in enumerate(coords):
        # Add a placeholder if the coordinates are out of bounds (typically 0, 0 -> tunnels or generally speaking no GPS signal)
        if x < 0 or x > img.width or y < 0 or y > img.height:
            placeholder = make_na_map_placeholder(window_width_px, window_height_px)
            frames.append((gpx_points[i].time, placeholder))

            continue

        cropped = crop_window(img, (x, y), window_width_px, window_height_px).copy()
        if i < len(coords)-1 and coords[i] != coords[i+1]:
            angle = bearing(coords[i], coords[i+1])
        else:
            angle = None

        draw_marker(cropped, (window_width_px // 2, window_height_px // 2), angle)

        # Draw open street maps attribution
        draw = ImageDraw.Draw(cropped)
        font = ImageFont.truetype(Path(__file__).parent / 'DejaVuSans-Bold.ttf', 20)
        attribution_text = '© OpenStreetMap'

        text_bbox = draw.textbbox((0, 0), attribution_text, font=font)
        text_width = text_bbox[2] - text_bbox[0]
        text_height = text_bbox[3] - text_bbox[1]

        # The text is in the bottom right corner
        draw.text((window_width_px - text_width - 10, window_height_px - text_height - 10 - (text_height - 20)), attribution_text, font=font, fill=(0, 0, 0))

        frames.append((gpx_points[i].time, cropped))

    frames.sort(key=lambda f: f[0])

    # Duplicate frames if the time difference between two points is > 1 second
    # TODO interpolate positions instead of duplicating the same frame
    # print("Adding frames for time gaps...")
    new_frames: list[tuple[datetime, Image.Image]] = []
    for i in range(len(frames)-1):
        time_diff = (frames[i+1][0] - frames[i][0]).total_seconds()
        if time_diff > 1:
            step = 1 / time_diff
            for j in range(1, int(time_diff)):
                t = frames[i][0] + timedelta(seconds=j * step)
                new_frames.append((t, frames[i][1].copy()))
        new_frames.append(frames[i])
    if len(frames):
        new_frames.append(frames[-1])
    frames = new_frames

    frames.sort(key=lambda f: f[0])

    if not frames:
        frames = [(gpx_points[0].time or datetime.now() if len(gpx_points) else datetime.now(), make_na_map_placeholder(window_width_px, window_height_px))]

    return frames


def latlon_to_village_name(lat: float, lon: float) -> str:
    '''Use the Nominatim service to get the village's / city's name'''

    # Manage edge cases
    if lat == 0 and lon == 0:
        return 'N/A'

    url = "https://nominatim.openstreetmap.org/reverse"
    params: dict[str, Any] = {
        "format": "jsonv2",
        "lat": lat,
        "lon": lon,
        "zoom": 10,          # zoom level 10 ≈ city
        "addressdetails": 1
    }

    headers = {"User-Agent": USER_AGENT}

    with requests_semaphore:
        resp = requests.get(url, params=params, headers=headers)
        time.sleep(0.2)

    data = resp.json()
    addr = data.get('address', {})

    return (
        addr.get("city")
        or addr.get("town")
        or addr.get("village")
        or addr.get("hamlet")
        or addr.get("municipality")
        or 'N/A'
    )
