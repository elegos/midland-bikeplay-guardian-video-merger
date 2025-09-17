import json
from pathlib import Path
import subprocess
from typing import Literal


def has_feature(feature_type: Literal['filter', 'encoder'], feature_name: str) -> bool:
    result = subprocess.run(['ffmpeg', '-hide_banner', '-filters' if feature_type == 'filter' else '-encoders'],
                            capture_output=True, text=True)

    return feature_name in result.stdout

def has_filter(filter_name: str) -> bool:
    return has_feature('filter', filter_name)

def has_encoder(encoder_name: str) -> bool:
    return has_feature('encoder', encoder_name)

def get_video_size(video_path: Path) -> tuple[int, int]:
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "json",
        str(video_path)
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    info = json.loads(result.stdout)
    width = info['streams'][0]['width']
    height = info['streams'][0]['height']

    return width, height