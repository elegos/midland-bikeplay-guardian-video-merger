from copy import copy
from dataclasses import dataclass
from datetime import datetime
import math
from pathlib import Path
import subprocess
from typing import Any, Literal
import xml.etree.cElementTree as ET

import gpxpy
from gpxpy.gpx import GPXTrackPoint, GPX

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
    
    @staticmethod
    def from_gpx_point(trkpt: GPXTrackPoint, source: str) -> 'GPSData|None':
        speed: str = next((ext.text for ext in trkpt.extensions if ext.tag == 'speed'), '0 km/h')

        default_acc_attr: dict[str, Any] = {}
        acc_attr: dict[str, Any] = next((ext.attrib for ext in trkpt.extensions if ext.tag == 'accelerometer'), default_acc_attr)
        acc_x = float(acc_attr.get('x') or 0)
        acc_y = float(acc_attr.get('y') or 0)
        acc_z = float(acc_attr.get('z') or 0)

        return GPSData(
            source_file=source,
            timestamp=trkpt.time or datetime.now(),
            latitude=trkpt.latitude,
            longitude=trkpt.longitude,
            speed=float(speed.split(' ')[0]),
            speed_unit=speed.split(' ')[1],
            hdop=trkpt.horizontal_dilution or 0,
            geoidheight=trkpt.geoid_height or 0,
            satellites=int(trkpt.satellites or 0),
            accelerometer=(acc_x, acc_y, acc_z),
        )


def get_gps_data_from_viidure(ts_file: Path) -> list[GPSData|None]:
    command = [
        'ffmpeg', '-hide_banner', '-loglevel', 'error',
        '-i', str(ts_file),
        '-map', '0:2', '-c', 'copy', '-f', 'data', '-'
    ]

    gps_bytes = subprocess.run(command, capture_output=True).stdout

    return [GPSData.from_viidure_string(ts_file.name, entry) for entry in gps_bytes.decode('utf-8', errors='ignore').split('\x00') if entry.startswith('Viidure')]

def calculate_speed(gps_data: list[GPSData|None], unit: Literal['kmph', 'mph'] = 'kmph') -> list[GPSData|None]:
    '''Use the Haversine formula to calculate the speed'''
    result: list[GPSData|None] = []

    R = 6371000.0 # Earth mean radius, in meters

    previous_point: GPSData|None = None
    for point in gps_data:
        res = copy(point)

        if previous_point and point and res:
            lat1, lon1 = previous_point.latitude, previous_point.longitude
            lat2, lon2 = point.latitude, point.longitude
            delta_t_seconds = (point.timestamp - previous_point.timestamp).total_seconds()

            if delta_t_seconds > 0:
                phi1, phi2 = math.radians(lat1), math.radians(lat2)
                dphi = math.radians(lat2 - lat1)
                dlambda = math.radians(lon2 - lon1)

                # Haversine formula for distance in meters
                a = math.sin(dphi / 2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2)**2
                c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
                distance_m = R * c

                speed_m_s = distance_m

                if unit.lower() == "kmph":
                    res.speed = round(speed_m_s * 3.6 / delta_t_seconds, 1)
                    res.speed_unit = 'km/h'
                elif unit.lower() == "mph":
                    res.speed = round(speed_m_s * 2.23694 / delta_t_seconds, 1)
                    res.speed_unit = 'mph'
        
        result.append(res)

        previous_point = point

    return result

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

def gpx_to_gpsdata(gpx_file: Path) -> list[GPSData|None]:
    gpx = gpxpy.parse(gpx_file.open('r'))
    result: list[GPSData|None] = []

    for track in gpx.tracks:
        src = track.source or ''
        for segment in track.segments:
            for point in segment.points:
                result.append(GPSData.from_gpx_point(point, src))

    return result

def gpx_points_from_gpx(gpx_file: Path, filter_src: str|None = None) -> list[GPXTrackPoint]:
    gpx = gpxpy.parse(gpx_file.open('r'))

    tracks = gpx.tracks
    if filter_src:
        track = next((track for track in gpx.tracks if track.source == filter_src), None)
        tracks = [] if track is None else [track]


    return [point for track in tracks for segment in track.segments for point in segment.points]

def gpx_to_direction(gpx: GPX, dt: datetime) -> int | None:
    '''Get the first GPX point at the given datetime and return its direction, derived by the current point and the following one.
    Direction is in the form of degrees (0-259), as follows: 0 (North), 90 (East), 180 (South), 270 (West)'''

    point: GPXTrackPoint|None = None
    next_point: GPXTrackPoint|None = None

    get_next_point = False

    try:
        for track in gpx.tracks:
            for segment in track.segments:
                for p in segment.points:
                    if get_next_point:
                        next_point = p
                        raise StopIteration()

                    if p.time == dt:
                        point = p
                        get_next_point = True
    except StopIteration:
        pass

    if point is None or next_point is None:
        return None
    
    # Calculate the direction between the two points, in degrees
    lat1_rad = math.radians(point.latitude)
    lon1_rad = math.radians(point.longitude)
    lat2_rad = math.radians(next_point.latitude)
    lon2_rad = math.radians(next_point.longitude)

    dlon_rad = lon2_rad - lon1_rad

    y = math.sin(dlon_rad) * math.cos(lat2_rad)
    x = math.cos(lat1_rad) * math.sin(lat2_rad) - math.sin(lat1_rad) * math.cos(lat2_rad) * math.cos(dlon_rad)

    bearing_rad = math.atan2(y, x)

    return int(math.degrees(bearing_rad)) % 360
