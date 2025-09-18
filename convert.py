from argparse import ArgumentParser
from pathlib import Path
import json
import logging
import shutil
import sys
from typing import Any

from PIL import Image

from bikeplay_guardian.core import make_pip, merge_videos, split_files
from bikeplay_guardian.ffmpeg import has_encoder, has_filter
from bikeplay_guardian.gps import GPSData, get_gps_data_from_viidure, gpsdata_to_gpx, gpx_points_from_gpx
from bikeplay_guardian.openstreetmaps import DEFAULT_ZOOM_LEVEL, TILE_SIZE, gpx_to_osm_map
from bikeplay_guardian.utils import progress_bar
from bikeplay_guardian import openstreetmaps

args = ArgumentParser()
args.add_argument('input_folder', type=Path, help='Path to the input folder containing .jpg and .ts files')
args.add_argument('--debug', action='store_true', help='Enable debug logging')
args.add_argument('--window-width', type=int, default=480, help='Width of the Open Street Map GPS track window')
args.add_argument('--window-height', type=int, default=640, help='Height of the Open Street Map GPS track window')

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
        logging.warning(f'Rear video segment not found for {front_file.name}, skipping PIP creation.')
        return None
    
    return get_gps_data_from_viidure(front_file)

def process_pip(front_file: Path):
    rear_file = input_path / 'ts_rear' / front_file.name.replace('_F.ts', '_R.ts')
    oms_file = input_path / 'oms_videos' / (front_file.name.split('.')[0] + '.mp4')
    out_file = input_path / 'mp4_pip' / (front_file.stem.replace('_F', '') + '.mp4')

    if not rear_file.exists():
        logging.warning(f'Rear video segment not found for {front_file.name}, skipping PIP creation.')
        return
    
    if not out_file.exists():
        make_pip(front_file, rear_file, oms_file, input_path / 'mp4_pip' / (front_file.stem.replace('_F', '') + '.mp4'))

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

    openstreetmaps.frames_to_video(frames, out_file, window_width_px, window_height_px, fps=30)

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

    split_files(input_path)
    num_files = len(list((input_path / 'ts_front').glob('*.ts')))
    logging.info(f'Found {num_files} video segments in {input_path}')

    gps_data: list[GPSData|None] = []
    for idx, front_file in enumerate(sorted((input_path / 'ts_front').glob('*.ts'))):
        progress_bar(idx + 1, num_files)
        gps_data.extend(extract_gps_data(front_file) or [])
    
    gpsdata_to_gpx(gps_data, input_path / 'track.gpx')

    osm_map_image: Image.Image|None = None
    osm_map_meta: dict[str, int] = {}

    if not openstreetmaps_map.exists() or not openstreetmaps_map_meta.exists():
        osm_map_image, (osm_origin_x, osm_origin_y) = gpx_to_osm_map(input_path / 'track.gpx', DEFAULT_ZOOM_LEVEL, args.window_width, args.window_height)
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
            args.window_width,
            args.window_height,
            input_path / 'track.gpx', pip_file.name,
            input_path / 'oms_videos' / (pip_file.name.split('.')[0] + '.mp4')
        )
    
    logging.info('Merging segment videos [PIP]...')
    gps_data_len = len([datum for datum in gps_data if datum is not None])
    for idx, source_file in enumerate([datum.source_file for datum in gps_data if datum is not None]):
        progress_bar(idx + 1, gps_data_len)

        front_file = input_path / 'ts_front' / source_file
        process_pip(front_file)

    # Merge everything
    full_video_path = input_path / 'full_video.mp4'
    merge_videos((input_path / 'mp4_pip').glob('*.mp4'), full_video_path)