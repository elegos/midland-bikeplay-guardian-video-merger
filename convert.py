from argparse import ArgumentParser
from pathlib import Path
import json
import logging
import shutil
import sys
from typing import Any

import gpxpy
from gpxpy.gpx import GPX
from PIL import Image

from bikeplay_guardian.core import make_bottom_right_overlay, make_pip, merge_videos, split_files
from bikeplay_guardian.cv2_tools import frames_to_video
from bikeplay_guardian.ffmpeg import has_encoder, has_filter
from bikeplay_guardian.gps import GPSData, calculate_speed, get_gps_data_from_viidure, gpsdata_to_gpx, gpx_to_gpsdata, gpx_points_from_gpx
from bikeplay_guardian.gps_info_overlay import GPSInfoOverlayFunction, draw_tachometer_flatbase
from bikeplay_guardian.openstreetmaps import DEFAULT_ZOOM_LEVEL, TILE_SIZE, gpx_to_osm_map
from bikeplay_guardian.utils import progress_bar
from bikeplay_guardian import openstreetmaps

args = ArgumentParser()
args.add_argument('input_folder', type=Path, help='Path to the input folder containing .jpg and .ts files')
args.add_argument('--debug', action='store_true', help='Enable debug logging')
args.add_argument('--recalculate-speed', action='store_true', help='Recalculate speed from GPS data')
args.add_argument('--recalculate-speed-unit', type=str, choices=['kmph', 'mph'], default='kmph', help='Speed unit for speed recalculation - ignored without --recalculate-speed')
args.add_argument('--map-window-width', type=int, default=384, help='Width of the Open Street Map GPS track window')
args.add_argument('--map-window-height', type=int, default=512, help='Height of the Open Street Map GPS track window')
args.add_argument('--gps-overlay', type=str, choices=['tachometer'], default=None)
args.add_argument('--timezone', type=str, default='Europe/Rome')

def check_requirements() -> bool:
    logging.info('Checking requirements...')

    # Check for ffmpeg in the system's path
    ffmpeg_executable = shutil.which('ffmpeg') or shutil.which('ffmpeg.exe')
    if ffmpeg_executable is None:
        logging.error(f'Cannot find ffmpeg executable in the system path. Please ensure that ffmpeg is installed and available in the PATH.')

        return False
    
    cuda_filters = all(has_filter(b) for b in ['bilateral_cuda', 'scale_npp', 'scale_cuda', 'overlay_cuda'])

    if not cuda_filters or not has_encoder('hevc_nvenc'):
        logging.warning('FFMPEG was found, but it was not compiled with the CUDA support or the CUDA-enabled npp filters. Falling back to CPU-bound filters.')
        logging.warning('If you have a NVIDIA GPU, it is recommended to recompile ffmpeg with CUDA and libnpp support for better performance.')

    return True

def extract_gps_data(front_file: Path) -> list[GPSData|None]|None:
    rear_file = input_path / 'ts_rear' / front_file.name.replace('_F.ts', '_R.ts')

    if not rear_file.exists():
        logging.warning(f'Rear video segment not found for {front_file.name}, skipping GPS data creation.')
        return None
    
    return get_gps_data_from_viidure(front_file)

def process_pip(front_file: Path, overlay_size: tuple[int, int]) -> None:
    rear_file = input_path / 'ts_rear' / front_file.name.replace('_F.ts', '_R.ts')
    oms_file = input_path / 'oms_videos' / (front_file.name.split('.')[0] + '.mp4')
    overlay_file = input_path / 'overlay_videos' / (front_file.name.split('.')[0] + '.mov')
    out_file = input_path / 'mp4_pip' / (front_file.stem.replace('_F', '') + '.mp4')

    if not rear_file.exists():
        logging.warning(f'Rear video segment not found for {front_file.name}, skipping PIP creation.')
        return
    
    if not out_file.exists():
        make_pip(
            primary_video=front_file,
            top_right_video=rear_file,
            bottom_left_video=oms_file,
            bottom_right_video=overlay_file,
            bottom_right_size=overlay_size,
            output=input_path / 'mp4_pip' / (front_file.stem.replace('_F', '') + '.mp4')
        )

def process_oms_video(
    osm_map: Image.Image, osm_meta: dict[str, Any], window_width_px: int, window_height_px: int, gpx_file: Path, gpx_src_filter: str, out_file: Path
):
    '''Generates a 30fps video of the Open Street Map GPS track from the gpx file'''
    out_file.parent.mkdir(parents=True, exist_ok=True)

    if out_file.exists():
        return

    frames = openstreetmaps.gpx_to_frames(
        gpx_points_from_gpx(gpx_file, gpx_src_filter),
        osm_map,
        (osm_meta['origin_x'], osm_meta['origin_y']),
        osm_meta['zoom'],
        osm_meta['tile_size'],
        window_width_px,
        window_height_px,
    )

    frames_to_video(frames, out_file, window_width_px, window_height_px, fps=30)

def process_overlay_video(
    gpx_data: GPX,
    gpx_src_filter: str,
    overlay_fn: GPSInfoOverlayFunction,
    timezone: str,
    width_px: int,
    height_px: int,
    out_file: Path
):
    out_file.parent.mkdir(parents=True, exist_ok=True)
    if out_file.exists():
        return

    frames = make_bottom_right_overlay(gpx_data, gpx_src_filter, overlay_fn, timezone, width_px, height_px)

    if frames:
        frames_to_video(frames, out_file, width_px, height_px, na_func=lambda x, y: overlay_fn(0, '', 0, 0, '0°', '', None, timezone), fps=30, preserve_alpha_channel=True)

if __name__ == '__main__':
    args = args.parse_args()
    input_path: Path = args.input_folder

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    if not check_requirements():
        sys.exit(1)

    pip_folder = input_path / 'mp4_pip'
    pip_folder.mkdir(exist_ok=True)
    openstreetmaps_map = input_path / 'osm_map.png'
    openstreetmaps_map_meta = input_path / 'osm_map.meta.json'
    gpx_track_file = input_path / 'track.gpx'

    split_files(input_path)
    num_files = len(list((input_path / 'ts_front').glob('*.ts')))
    logging.info(f'Found {num_files} video segments in {input_path}')

    gps_data: list[GPSData|None] = []
    if not gpx_track_file.exists():
        for idx, front_file in enumerate(sorted((input_path / 'ts_front').glob('*.ts'))):
            progress_bar(idx + 1, num_files)
            gps_data.extend(extract_gps_data(front_file) or [])
        
        if args.recalculate_speed:
            gps_data = calculate_speed(gps_data, args.recalculate_speed_unit)
        
        gpsdata_to_gpx(gps_data, gpx_track_file)
    else:
        gps_data = gpx_to_gpsdata(gpx_track_file)

        if args.recalculate_speed:
            gps_data = calculate_speed(gps_data, args.recalculate_speed_unit)

    osm_map_image: Image.Image|None = None
    osm_map_meta: dict[str, int] = {}

    if not openstreetmaps_map.exists() or not openstreetmaps_map_meta.exists():
        osm_map_image, (osm_origin_x, osm_origin_y) = gpx_to_osm_map(input_path / 'track.gpx', DEFAULT_ZOOM_LEVEL, args.map_window_width, args.map_window_height)
        osm_map_image.save(openstreetmaps_map)
        osm_map_meta = {'origin_x': osm_origin_x, 'origin_y': osm_origin_y, 'zoom': DEFAULT_ZOOM_LEVEL, 'tile_size': TILE_SIZE}
        json.dump(osm_map_meta, openstreetmaps_map_meta.open('w'), indent=4)
    else:
        Image.MAX_IMAGE_PIXELS = sys.maxsize # Avoid PIL error about too many pixels
        osm_map_image = Image.open(openstreetmaps_map)
        osm_map_meta = json.load(openstreetmaps_map_meta.open('r'))
    
    logging.info('Processing OpenStreetMaps track videos...')
    for idx, pip_file in enumerate((input_path / 'ts_front').glob('*.ts')):
        progress_bar(idx + 1, num_files)
        process_oms_video(
            osm_map_image,
            osm_map_meta,
            args.map_window_width,
            args.map_window_height,
            input_path / 'track.gpx', pip_file.name,
            input_path / 'oms_videos' / (pip_file.name.split('.')[0] + '.mp4')
        )
    
    overlay_size = (0, 0)
    if args.gps_overlay == 'tachometer':
        overlay_size = draw_tachometer_flatbase.width, draw_tachometer_flatbase.height
        logging.info('Processing Tachometer overlay videos...')
        gpx_data = gpxpy.parse((input_path / 'track.gpx').open('r'))

        for idx, pip_file in enumerate((input_path / 'ts_front').glob('*.ts')):
            progress_bar(idx + 1, num_files)
            process_overlay_video(
                gpx_data=gpx_data,
                gpx_src_filter=pip_file.name,
                overlay_fn=draw_tachometer_flatbase,
                timezone=args.timezone,
                width_px=800,
                height_px=400,
                out_file=input_path / 'overlay_videos' / (pip_file.name.split('.')[0] + '.mov')
            )
    
    logging.info('Merging segment videos [PIP]...')
    gps_data_len = len([datum for datum in gps_data if datum is not None])
    for idx, source_file in enumerate([datum.source_file for datum in gps_data if datum is not None]):
        progress_bar(idx + 1, gps_data_len)

        front_file = input_path / 'ts_front' / source_file
        process_pip(front_file, overlay_size)

    # Merge everything
    full_video_path = input_path / 'full_video.mp4'
    merge_videos(pip_folder.glob('*.mp4'), full_video_path)