from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import subprocess
import xml.etree.cElementTree as ET

import gpxpy
from gpxpy.gpx import GPXTrackPoint

@dataclass
class GPSData:
    source_file: str
    timestamp: datetime
    latitude: float
    longitude: float
    speed: float
    speed_unit: str
    hdop: float
    geoidheight: float
    satellites: int
    accelerometer: tuple[float, float, float]

    @staticmethod
    def from_viidure_string(source_file: str, data_str: str) -> 'GPSData|None':
        try:
            parts = data_str.lstrip('Viidure').strip().split()
            if len(parts) < 12:
                return None

            timestamp = datetime.strptime(f"{parts[0]} {parts[1]}", "%Y/%m/%d %H:%M:%S")
            latitude = float(parts[2].lstrip('N:'))
            longitude = float(parts[3].lstrip('E:'))
            speed = float(parts[4])
            speed_unit = parts[5]
            hdop = float(parts[6])
            geoidheight = float(parts[7])
            satellites = int(parts[8])
            acc_x = float(parts[9].lstrip('x:'))
            acc_y = float(parts[10].lstrip('y:'))
            acc_z = float(parts[11].lstrip('z:'))
            accelerometer = (acc_x, acc_y, acc_z)

            return GPSData(source_file, timestamp, latitude, longitude, speed, speed_unit, hdop, geoidheight, satellites, accelerometer)
        except Exception:
            return None


def get_gps_data_from_viidure(ts_file: Path) -> list[GPSData|None]:
    command = [
        'ffmpeg', '-hide_banner', '-loglevel', 'error',
        '-i', str(ts_file),
        '-map', '0:2', '-c', 'copy', '-f', 'data', '-'
    ]

    gps_bytes = subprocess.run(command, capture_output=True).stdout

    return [GPSData.from_viidure_string(ts_file.name, entry) for entry in gps_bytes.decode('utf-8', errors='ignore').split('\x00') if entry.startswith('Viidure')]

def gpsdata_to_gpx(gps_data: list[GPSData|None], output: Path) -> None:
    root = ET.Element(
        'gpx', version="1.1", creator="bikeplay-guardian-tool",
        xmlns="http://www.topografix.com/GPX/1/1", xmlns_xsi="http://www.w3.org/2001/XMLSchema-instance",
        xsi_schemaLocation="http://www.topografix.com/GPX/1/1 http://www.topografix.com/GPX/1/1/gpx.xsd")
    
    root.append(ET.Element('name', text="Bikeplay Guardian GPS Track"))

    chunks: dict[str, list[GPSData]] = {}
    sorted_gps_data: list[GPSData] = [point for point in gps_data if point is not None]
    sorted_gps_data.sort(key=lambda x: x.source_file)

    for point in sorted_gps_data:
        source_file = point.source_file
        if source_file not in chunks:
            chunks[source_file] = []
        
        chunks[source_file].append(point)

    idx = -1
    for filename, points in chunks.items():
        idx += 1
        trk = ET.SubElement(root, 'trk')
        ET.SubElement(trk, 'src').text = filename
        ET.SubElement(trk, 'number').text = str(idx)

        trkseg = ET.SubElement(trk, 'trkseg')
        for point in points:
            trkpt = ET.SubElement(trkseg, 'trkpt', lat=str(point.latitude), lon=str(point.longitude))

            ET.SubElement(trkpt, 'time').text = point.timestamp.strftime('%Y-%m-%dT%H:%M:%SZ')
            ET.SubElement(trkpt, 'geoidheight').text = str(point.geoidheight)
            ET.SubElement(trkpt, 'sat').text = str(point.satellites)
            ET.SubElement(trkpt, 'hdop').text = str(point.hdop)

            extensions = ET.SubElement(trkpt, 'extensions')
            ET.SubElement(extensions, 'speed').text = f"{point.speed} {point.speed_unit}"
            ET.SubElement(extensions, 'accelerometer', x=str(point.accelerometer[0]), y=str(point.accelerometer[1]), z=str(point.accelerometer[2]))

    tree = ET.ElementTree(root)
    ET.indent(tree, '    ')
    tree.write(output, encoding='utf-8', xml_declaration=True)

def gpx_points_from_gpx(gpx_file: Path, filter_src: str|None = None) -> list[GPXTrackPoint]:
    gpx = gpxpy.parse(gpx_file.open('r'))

    tracks = gpx.tracks
    if filter_src:
        track = next((track for track in gpx.tracks if track.source == filter_src), None)
        tracks = [] if track is None else [track]


    return [point for track in tracks for segment in track.segments for point in segment.points]
