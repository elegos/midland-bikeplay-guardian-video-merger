from datetime import datetime
import logging
from pathlib import Path
import subprocess
from time import sleep
from typing import Iterator

from PIL import Image
import gpxpy

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

def make_bottom_right_overlay(gpx: Path, gpx_src_filter: str, overlay_fn: GPSInfoOverlayFunction, timezone: str, width_px: int, height_px: int) -> list[tuple[datetime, Image.Image]]:
    result: list[tuple[datetime, Image.Image]] = []

    gpx_data = gpxpy.parse(gpx.open('r'))

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
                    if pt_idx % 10 == 0:
                        sleep(0.1) # OpenStreetMap API thresholds
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
                        width_px,
                        height_px
                    )))

    return result

def make_pip(main: Path, secondary: Path, oms_vid: Path, output: Path, main_size: tuple[int, int] = (1920, 1080), oms_vid_size: tuple[int, int] = (480, 640), oms_vid_margin_px: int = 55, oms_vid_scale: float = 0.8) -> None:
    ''' Make a picture-in-picture video from two input videos.'''

    main_denoise_filter = 'hqdn3d=4:3:6:4'
    if has_filter('bilateral_cuda'):
        main_denoise_filter = f'hwupload_cuda,bilateral_cuda=sigmaS=3:sigmaR=0.5'

    secondary_scale_filter: str = next((tpl[1] for tpl in [
        ('scale_npp', 'hwupload_cuda,scale_npp=iw/4:ih/4'),
        ('scale_cuda', 'hwupload_cuda,scale_cuda=iw/4:ih/4'),
        ('scale', 'scale=iw/4:ih/4')
    ] if has_filter(tpl[0])))

    oms_scale_filter: str = next((tpl[1] for tpl in [
        ('scale_npp', f'hwupload_cuda,scale_npp=iw*{oms_vid_scale}:ih*{oms_vid_scale}'),
        ('scale_cuda', f'hwupload_cuda,scale_cuda=iw*{oms_vid_scale}:ih*{oms_vid_scale}'),
        ('scale', f'scale=iw*{oms_vid_scale}:ih*{oms_vid_scale}')
    ] if has_filter(tpl[0])))

    overlay_filter = next(tpl[1] for tpl in [
        ('overlay_cuda', 'overlay_cuda=W-w:0:shortest=1'),
        ('overlay', 'overlay=W-w:0:shortest=1')
    ] if has_filter(tpl[0]))

    oms_overlay_filter: str = next((tpl[1] for tpl in [
        ('overlay_cuda', f'overlay_cuda=x=0:y={main_size[1] - oms_vid_size[1] * oms_vid_scale - oms_vid_margin_px}:shortest=1'),
        ('overlay', f'overlay=x=0:y={main_size[1] - oms_vid_size[1] * oms_vid_scale - oms_vid_margin_px}:shortest=1')
    ] if has_filter(tpl[0])))

    video_codec  = 'hevc_nvenc -preset fast -cq 23' if has_encoder('hevc_nvenc') else 'libx265 -preset medium -crf 23'

    logging.debug(f'{__name__} Merging {main} and {secondary} into {output}')
    command = [
        'ffmpeg', '-hide_banner', '-loglevel', 'error',
        '-i', str(main), '-i', str(secondary), '-i', str(oms_vid),
        '-filter_complex',
        f'[0:v]{main_denoise_filter}[front]; '
        f'[1:v]{secondary_scale_filter},tpad=stop_mode=clone:stop_duration=60[rear]; '
        f'[2:v]fps=30,setpts=PTS*30,tpad=stop_mode=clone:stop_duration=60[oms_vid]; ' #  oms_vid is 1 FPS, this normalizes it to 30 FPS
        f'[oms_vid]{oms_scale_filter}[oms_vid_scaled]; '
        f'[front][rear]{overlay_filter}[front_rear]; '
        f'[front_rear][oms_vid_scaled]{oms_overlay_filter}[outv]',
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