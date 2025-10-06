from datetime import datetime
import logging
from pathlib import Path
import subprocess
from typing import Iterator

from PIL import Image
from gpxpy.gpx import GPX

from bikeplay_guardian.ffmpeg import has_encoder, has_filter
from bikeplay_guardian.gps import gpx_to_direction
from bikeplay_guardian.gps_info_overlay import GPSInfoOverlayFunction
from bikeplay_guardian.openstreetmaps import latlon_to_village_name


def split_files(folder: Path):
    jpg = folder / 'jpg'
    ts_front = folder / 'ts_front'
    ts_rear = folder / 'ts_rear'

    jpg.mkdir(exist_ok=True)
    ts_front.mkdir(exist_ok=True)
    ts_rear.mkdir(exist_ok=True)

    logging.info(f'{__name__} Moving front video segments...')
    for file_path in folder.glob('*_F.ts'):
        file_path.rename(ts_front / file_path.name)

    logging.info(f'{__name__} Moving rear video segments...')
    for file_path in folder.glob('*_R.ts'):
        file_path.rename(ts_rear / file_path.name)

    logging.info(f'{__name__} Moving snapshots...')
    for file_path in folder.glob('*.jpg'):
        file_path.rename(jpg / file_path.name)

def make_bottom_right_overlay(gpx_data: GPX, gpx_src_filter: str, overlay_fn: GPSInfoOverlayFunction, timezone: str, width_px: int, height_px: int) -> list[tuple[datetime, Image.Image]]:
    result: list[tuple[datetime, Image.Image]] = []

    last_village_name: str|None = None
    pt_idx = 0
    for track in gpx_data.tracks:
        if track.source != gpx_src_filter:
            continue

        for trkseg in track.segments:
            for trkpt in trkseg.points:
                if trkpt.time:
                    pt_idx += 1

                    speed = next((ext.text for ext in trkpt.extensions if ext.tag == 'speed'), '0 km/h')
                    speed_val, speed_unit = speed.split(' ')
                    speed_val = float(speed_val)

                    # Calculate the village name only once every 10 seconds
                    village_name = last_village_name if pt_idx % 10 != 0 and last_village_name and last_village_name != 'N/A' else latlon_to_village_name(trkpt.latitude, trkpt.longitude)
                    last_village_name = village_name

                    result.append((trkpt.time, overlay_fn(
                        speed_val,
                        speed_unit,
                        trkpt.latitude,
                        trkpt.longitude,
                        f'{gpx_to_direction(gpx_data, trkpt.time) or 0}°',
                        village_name,
                        trkpt.time,
                        timezone,
                    )))

    return result

def make_pip(
    primary_video: Path, top_right_video: Path, bottom_left_video: Path,
    output: Path, main_size: tuple[int, int] = (1920, 1080),
    bottom_left_size: tuple[int, int] = (384, 512), bottom_left_margin_px: int = 55, bottom_left_scale: float = 1,
    bottom_right_video: Path|None = None,
    bottom_right_size: tuple[int, int]|None = None, bottom_right_scale: float = 0.8,
) -> None:
    ''' Make a picture-in-picture video from two input videos.'''
    
    # Make the overlay videos None if they don't exist
    if bottom_right_video is None or not bottom_right_video.exists():
        bottom_right_video = None
        bottom_right_size = None
    

    main_denoise_filter = 'hqdn3d=4:3:6:4'
    if has_filter('bilateral_cuda'):
        main_denoise_filter = f'bilateral_cuda=sigmaS=3:sigmaR=0.5'

    top_right_scale_filter: str = next((tpl[1] for tpl in [
        ('scale_npp', 'scale_npp=iw/4:ih/4'),
        ('scale_cuda', 'scale_cuda=iw/4:ih/4'),
        ('scale', 'scale=iw/4:ih/4')
    ] if has_filter(tpl[0])))

    bottom_left_scale_filter: str = next((tpl[1] for tpl in [
        ('scale_npp', f'scale_npp=iw*{bottom_left_scale}:ih*{bottom_left_scale}'),
        ('scale_cuda', f'scale_cuda=iw*{bottom_left_scale}:ih*{bottom_left_scale}'),
        ('scale', f'scale=iw*{bottom_left_scale}:ih*{bottom_left_scale}')
    ] if has_filter(tpl[0])))

    top_right_overlay_filter = next(tpl[1] for tpl in [
        ('overlay_cuda', 'overlay_cuda=W-w:0:shortest=1'),
        ('overlay', 'overlay=W-w:0:shortest=1')
    ] if has_filter(tpl[0]))

    bottom_left_overlay_filter: str = next((tpl[1] for tpl in [
        ('overlay_cuda', f'overlay_cuda=x=0:y={main_size[1] - bottom_left_size[1] * bottom_left_scale - bottom_left_margin_px}:shortest=1'),
        ('overlay', f'overlay=x=0:y={main_size[1] - bottom_left_size[1] * bottom_left_scale - bottom_left_margin_px}:shortest=1')
    ] if has_filter(tpl[0])))

    bottom_right_scale_filter: str = next((tpl[1] for tpl in [
        ('scale_npp', f'scale_npp=iw*{bottom_right_scale}:ih*{bottom_right_scale}'),
        ('scale_cuda', f'scale_cuda=iw*{bottom_right_scale}:ih*{bottom_right_scale}'),
        ('scale', f'scale=iw*{bottom_right_scale}:ih*{bottom_right_scale}')
    ] if has_filter(tpl[0])))

    bottom_right_overlay_filter: str|None = None
    if bottom_right_size is not None:
        bottom_right_overlay_filter = next((tpl[1] for tpl in [
            ('overlay_cuda', f'overlay_cuda=x={main_size[0] - bottom_right_size[0] * bottom_right_scale}:y={main_size[1] - bottom_right_size[1] * bottom_right_scale}:shortest=1'),
            ('overlay', f'overlay=x={main_size[0] - bottom_right_size[0] * bottom_right_scale}:y={main_size[1] - bottom_right_size[1] * bottom_right_scale}:shortest=1')
        ] if has_filter(tpl[0])))

    video_codec  = 'hevc_nvenc -preset fast -cq 23' if has_encoder('hevc_nvenc') else 'libx265 -preset medium -crf 23'

    logging.debug(f'{__name__} Merging {primary_video}, {top_right_video}, {bottom_left_video}, {bottom_right_video} into {output}')

    # -------- Filter complex build --------
    with_hwupload = 'hwupload_cuda' if has_filter('hwupload_cuda') else 'null'
    filter_complex = [
        f'[0:v]{with_hwupload}[main]',
        f'[1:v]{with_hwupload}[top_right]',
        f'[2:v]{with_hwupload}[bottom_left]'
    ]
    if bottom_right_video is not None:
        filter_complex.append(f'[3:v]format=rgba,{with_hwupload}[bottom_right]')
    filter_complex.append(f'[main]{main_denoise_filter}[main]')
    filter_complex.append(f'[top_right]{top_right_scale_filter},tpad=stop_mode=clone:stop_duration=60[top_right]')
    filter_complex.append('[bottom_left]fps=30,setpts=PTS*30,tpad=stop_mode=clone:stop_duration=60[bottom_left]')
    if bottom_left_scale != 1:
        filter_complex.append(f'[bottom_left]{bottom_left_scale_filter}[bottom_left]')
    if bottom_right_video is not None:
        filter_complex.append('[bottom_right]fps=30,setpts=PTS*30,tpad=stop_mode=clone:stop_duration=60[bottom_right]')
        if bottom_right_scale != 1:
            filter_complex.append(f'[bottom_right]{bottom_right_scale_filter}[bottom_right]')
    # Composition
    filter_complex.append(f'[main][top_right]{top_right_overlay_filter}[outv]')
    filter_complex.append(f'[outv][bottom_left]{bottom_left_overlay_filter}[outv]')
    if bottom_right_video is not None:
        filter_complex.append(f'[outv][bottom_right]{bottom_right_overlay_filter}[outv]')

    filter_complex = '; '.join(filter_complex)

    command = [
        'ffmpeg', '-hide_banner', '-loglevel', 'error',
        '-i', str(primary_video), '-i', str(top_right_video), '-i', str(bottom_left_video), *(['-i', str(bottom_right_video)] if bottom_right_video is not None else []),
        '-filter_complex', filter_complex,
        '-map', '[outv]', '-map', '0:a',
        '-c:v', *video_codec.split(),
        '-c:a', 'aac',
        '-y', f'{output}'
    ]

    logging.debug(f'Running command: {' '.join(command)}')

    proc = subprocess.Popen(command, stdout=None, stderr=subprocess.STDOUT)
    _, _ = proc.communicate()

    proc.wait()
    if proc.stdout is not None:
        proc.stdout.close()

def merge_videos(input_videos: list[Path]|Iterator[Path], output_video: Path) -> None:
    ''' Merge multiple videos into a single video.'''

    if output_video.exists():
        return

    input_list_path = output_video.parent / 'input_videos.txt'
    with open(input_list_path, 'w') as f:
        for video in sorted(input_videos):
            f.write(f"file '{video.resolve()}'\n")

    logging.info(f'{__name__} Merging videos into {output_video}')
    command = [
        'ffmpeg', '-hide_banner', '-loglevel', 'error',
        '-f', 'concat', '-safe', '0',
        '-i', str(input_list_path),
        '-c', 'copy',
        '-y', str(output_video)
    ]

    logging.debug(f'Running command: {' '.join(command)}')

    proc = subprocess.Popen(command, stdout=None, stderr=subprocess.STDOUT)
    proc.wait()

    input_list_path.unlink()  # Remove the temporary file