from datetime import datetime
import math
from pathlib import Path
from typing import Protocol
from PIL import Image, ImageDraw, ImageFont
import pytz

from bikeplay_guardian.utils import textbox_size

class GPSInfoOverlayFunction(Protocol):
    def __call__(
        self,
        speed: int,
        lat: float,
        lon: float,
        direction: str,
        location: str,
        dt: datetime,
        timezone: str = "Europe/Rome",
        width: int = 800,
        height: int = 400
    ) -> Image.Image:
        ...

def draw_tachometer_flatbase(
    speed: int,
    lat: float,
    lon: float,
    direction: str,
    location: str,
    dt: datetime,
    timezone: str = "Europe/Rome",
    width: int = 800,
    height: int = 400
):
    # ---------- Canvas ----------
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # ---------- Geometry ----------
    center_x = width // 2
    center_y = height * 0.9  # lower center to have a plain base
    radius = min(width, height*1.8) * 0.45  # proportioned radius

    # ---------- Font ----------
    font_small = ImageFont.truetype(Path(__file__).parent / "DejaVuSans-Bold.ttf", width // 40)
    font_big   = ImageFont.truetype(Path(__file__).parent / "DejaVuSans-Bold.ttf", width // 14)

    # ---------- Tachometer background (dark gray) ----------
    bg_color = (30, 30, 30, 255)
    draw.ellipse(
        [center_x - radius, center_y - radius,
         center_x + radius, center_y + radius],
        fill=bg_color
    )

    # ---------- Angular parameters ----------
    start_angle = 175
    end_angle   = 410 - 45
    sweep = end_angle - start_angle

    # ---------- Speed arch  (below the ticks) ----------
    draw.arc(
        [center_x - radius, center_y - radius,
         center_x + radius, center_y + radius],
        start=start_angle,
        end=start_angle + sweep * min(speed,160) / 160,
        fill=(0, 200, 255, 180),
        width=int(width * 0.04)
    )

    # ---------- Ticks and numbers ----------
    for k in range(0, 161, 2):
        frac = k / 160
        angle = math.radians(start_angle + sweep * frac)

        # Long ticks and numbers every 10
        if k % 10 == 0:
            tick_len = width * 0.04
            lw = 3
        else:
            tick_len = width * 0.025
            lw = 2

        x1 = center_x + (radius - tick_len) * math.cos(angle)
        y1 = center_y + (radius - tick_len) * math.sin(angle)
        x2 = center_x + radius * math.cos(angle)
        y2 = center_y + radius * math.sin(angle)
        draw.line((x1, y1, x2, y2), fill="white", width=lw)

        # Numbers every 10
        if k % 10 == 0:
            tx = center_x + (radius - width * 0.07) * math.cos(angle)
            ty = center_y + (radius - width * 0.07) * math.sin(angle)
            t = str(k)
            w, h = textbox_size(draw, t, font_small)
            draw.text((tx - w/2, ty - h/2), t, fill="white", font=font_small)

    # ---------- Centered speed text ----------
    text_speed = f"{speed} km/h"
    w, h = textbox_size(draw, text_speed, font_big)
    # At the center of the gray disk
    center_text_y = center_y - radius * 0.4
    draw.text((center_x - w/2, center_text_y - h/2),
              text_speed, fill="white", font=font_big)

    # ---------- GPS Info ----------
    info_lines = [
        f"N: {lat:.5f}",
        f"E: {lon:.5f}",
        f"Dir: {direction}",
        location
    ]
    y_offset = center_text_y + h + 10
    for line in info_lines:
        w, h_line = textbox_size(draw, line, font_small)
        draw.text((center_x - w/2, y_offset),
                  line, fill="white", font=font_small)
        y_offset += h_line + 4
    
    # ---------- Date and time at the bottom right ----------
    # Format the datetime depending on the tz variable
    tz = pytz.timezone(timezone)
    if dt.tzinfo is None:
        dt_with_tz = tz.localize(dt)
    else:
        dt_with_tz = dt.astimezone(tz)
    dt_text = dt_with_tz.strftime("%d/%m/%Y %H:%M %Z%z")

    w, h = textbox_size(draw, dt_text, font_small)
    draw.text(
        (center_x - w/2, height - h - 10),
        dt_text, fill="white", font=font_small
    )

    return img
