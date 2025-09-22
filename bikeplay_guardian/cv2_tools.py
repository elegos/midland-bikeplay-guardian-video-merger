from datetime import datetime
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory, mktemp
from typing import Callable

from PIL import Image, ImageDraw, ImageFont
import cv2
import numpy as np

from bikeplay_guardian.utils import textbox_size

def default_na_func(width_px: int, height_px: int) -> Image.Image:
    '''Draw a "N/A" text on a gray background'''

    text = 'N/A'

    img = Image.new('RGB', (width_px, height_px), (int(0.18 * 255), int(0.18 * 255), int(0.18 * 255)))
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(Path(__file__).parent / 'DejaVuSans-Bold.ttf', 40)

    (text_width, text_height) = textbox_size(draw, text, font)

    draw.text(((width_px - text_width) // 2, (height_px - text_height) // 2), text, font=font, fill=(255, 255, 255))

    return img


def frames_to_video(frames: list[tuple[datetime, Image.Image]], output: Path, window_width_px: int, window_height_px: int, na_func: Callable[[int, int], Image.Image] = default_na_func, fps: int = 30, preserve_alpha_channel: bool = False):
    if not preserve_alpha_channel:
        out = cv2.VideoWriter(str(output), cv2.VideoWriter.fourcc(*'mp4v'), fps, (window_width_px, window_height_px))

        # No frames available, show generated N/A frame
        if not frames:
            img = na_func(window_width_px, window_height_px)
            frames = [(datetime.now(), img.copy())]

        for _, frame in frames:
            out.write(cv2.cvtColor(np.array(frame), cv2.COLOR_RGB2BGR))
        out.release()
    else:
        with TemporaryDirectory() as tmpdir:
            for idx, (_, frame) in enumerate(frames):
                frame.save(Path(tmpdir) / f'frame-{idx:04d}.png')
            subprocess.run(['ffmpeg', '-y', '-v', 'error', '-hide_banner', '-framerate', str(fps), '-i', f'{tmpdir}/frame-%04d.png', '-c:v', 'qtrle', '-pix_fmt', 'yuva444p10le', str(output)], check=True)

