import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from PIL import Image, ExifTags
import reverse_geocoder as rg

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".webp"}
VIDEO_EXTS = {".mov", ".mp4", ".m4v"}
SUPPORTED_EXTS = IMAGE_EXTS | VIDEO_EXTS

def _dms_to_decimal(dms, ref):
    degrees = float(dms[0])
    minutes = float(dms[1]) / 60.0
    seconds = float(dms[2]) / 3600.0
    val = degrees + minutes + seconds
    return -val if ref in ["S", "W"] else val

def _find_takeout_sidecar(file_path: Path):
    """Finds companion .json metadata sidecar created by Google Takeout."""
    # 1. Exact match with .json appended (e.g. IMG_1234.jpg.json or IMG_1053.MOV.json)
    exact_appended = file_path.with_name(f"{file_path.name}.json")
    if exact_appended.is_file():
        return exact_appended

    # 2. Extension replaced with .json (e.g. IMG_1234.json)
    replaced_ext = file_path.with_suffix(".json")
    if replaced_ext.is_file():
        return replaced_ext

    # 3. Duplicate parentheses format: photo(1).jpg -> photo.jpg(1).json
    stem = file_path.stem
    if "(" in stem and stem.endswith(")"):
        base_stem = stem[:stem.rfind("(")].rstrip()
        counter = stem[stem.rfind("("):]
        alt_name = f"{base_stem}{file_path.suffix}{counter}.json"
        alt_path = file_path.with_name(alt_name)
        if alt_path.is_file():
            return alt_path

    # 4. Truncated filenames (Takeout cuts off long filenames before adding .json)
    if len(file_path.stem) > 40:
        prefix = file_path.name[:40]
        try:
            for candidate in file_path.parent.glob(f"{prefix}*.json"):
                if candidate.is_file():
                    return candidate
        except Exception:
            pass

    return None

def _extract_takeout_json(json_path: Path):
    """Extracts date and GPS coordinates from Google Takeout JSON sidecar."""
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        date_taken = None
        photo_time = data.get("photoTakenTime") or data.get("creationTime")
        if photo_time and "timestamp" in photo_time:
            try:
                ts = int(photo_time["timestamp"])
                if ts > 0:
                    date_taken = datetime.fromtimestamp(ts)
            except (ValueError, TypeError):
                pass

        lat, lon = None, None
        geo = data.get("geoDataExif") or data.get("geoData")
        if geo:
            try:
                raw_lat = float(geo.get("latitude", 0.0))
                raw_lon = float(geo.get("longitude", 0.0))
                # Google Takeout uses (0.0, 0.0) when no location is recorded
                if not (abs(raw_lat) < 1e-6 and abs(raw_lon) < 1e-6):
                    lat, lon = raw_lat, raw_lon
            except (ValueError, TypeError):
                pass

        return {
            "date": date_taken,
            "lat": lat,
            "lon": lon
        }
    except Exception:
        return None

def _extract_video_metadata(video_path: Path):
    """Extracts date and GPS coordinates from video files via ffprobe and macOS mdls."""
    date_taken = None
    lat, lon = None, None

    # 1. ffprobe for QuickTime / MP4 ISO6709 location & creation dates
    try:
        cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(video_path)]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0:
            data = json.loads(res.stdout)
            tags = data.get("format", {}).get("tags", {})

            # Date
            date_str = tags.get("com.apple.quicktime.creationdate") or tags.get("creation_time")
            if date_str:
                clean_str = date_str.split(".")[0].replace("Z", "")
                for fmt in [
                    "%Y-%m-%dT%H:%M:%S",
                    "%Y-%m-%d %H:%M:%S",
                    "%Y-%m-%d %H:%M:%S%z",
                    "%Y-%m-%dT%H:%M:%S%z",
                ]:
                    try:
                        date_taken = datetime.strptime(clean_str, fmt)
                        if date_taken.tzinfo:
                            date_taken = date_taken.replace(tzinfo=None)
                        break
                    except Exception:
                        pass

            # GPS ISO6709 e.g. +10.3166+123.9782+016.602/
            loc_str = tags.get("com.apple.quicktime.location.ISO6709") or tags.get("location")
            if loc_str:
                m = re.match(r"([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)", loc_str)
                if m:
                    lat, lon = float(m.group(1)), float(m.group(2))
    except Exception:
        pass

    # 2. macOS Spotlight mdls fallback
    if lat is None or lon is None or not date_taken:
        try:
            cmd = ["mdls", "-name", "kMDItemLatitude", "-name", "kMDItemLongitude", "-name", "kMDItemContentCreationDate", str(video_path)]
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    if "kMDItemLatitude" in line and "=" in line and "(null)" not in line:
                        try:
                            lat = float(line.split("=")[1].strip())
                        except Exception:
                            pass
                    elif "kMDItemLongitude" in line and "=" in line and "(null)" not in line:
                        try:
                            lon = float(line.split("=")[1].strip())
                        except Exception:
                            pass
                    elif "kMDItemContentCreationDate" in line and "=" in line and "(null)" not in line and not date_taken:
                        val = line.split("=")[1].strip().strip('"')
                        clean_date = val.split("+")[0].strip()
                        try:
                            date_taken = datetime.strptime(clean_date, "%Y-%m-%d %H:%M:%S")
                        except Exception:
                            pass
        except Exception:
            pass

    return date_taken, lat, lon

def extract_metadata(file_path: Path):
    """
    Extracts date, GPS coordinates, and media type from photos or videos.
    Supports EXIF (JPEG, HEIC, PNG), ffprobe / mdls (MOV, MP4), and Google Takeout JSON sidecars.
    """
    date_taken = None
    lat, lon = None, None
    is_video = file_path.suffix.lower() in VIDEO_EXTS
    media_type = "video" if is_video else "image"

    if is_video:
        # Video metadata extraction
        date_taken, lat, lon = _extract_video_metadata(file_path)
    else:
        # Image metadata extraction from EXIF
        try:
            with Image.open(file_path) as img:
                exif = img.getexif() if hasattr(img, "getexif") else None
                if exif:
                    # Parse Date from main EXIF tags
                    for tag_id in [306, 50971]:
                        if tag_id in exif and not date_taken:
                            try:
                                date_taken = datetime.strptime(str(exif[tag_id]), "%Y:%m:%d %H:%M:%S")
                            except Exception:
                                pass

                    # Parse Date from EXIF IFD
                    try:
                        exif_ifd = exif.get_ifd(ExifTags.IFD.Exif) if hasattr(exif, "get_ifd") else {}
                        for tag_id in [36867, 36868]:
                            if tag_id in exif_ifd and not date_taken:
                                try:
                                    date_taken = datetime.strptime(str(exif_ifd[tag_id]), "%Y:%m:%d %H:%M:%S")
                                except Exception:
                                    pass
                    except Exception:
                        pass

                    # Parse GPS from GPS IFD
                    try:
                        gps_ifd = exif.get_ifd(ExifTags.IFD.GPSInfo) if hasattr(exif, "get_ifd") else {}
                        if gps_ifd and 2 in gps_ifd and 4 in gps_ifd:
                            lat = _dms_to_decimal(gps_ifd[2], gps_ifd.get(1, "N"))
                            lon = _dms_to_decimal(gps_ifd[4], gps_ifd.get(3, "E"))
                    except Exception:
                        pass

                # Legacy fallback for older JPEG images without IFD
                if not date_taken or lat is None:
                    legacy_exif = getattr(img, "_getexif", lambda: None)()
                    if legacy_exif:
                        for tag_id, value in legacy_exif.items():
                            tag = ExifTags.TAGS.get(tag_id, tag_id)
                            if tag in ["DateTimeOriginal", "DateTime"] and not date_taken:
                                try:
                                    date_taken = datetime.strptime(str(value), "%Y:%m:%d %H:%M:%S")
                                except Exception:
                                    pass
                            if tag == "GPSInfo" and lat is None:
                                gps_info = {ExifTags.GPSTAGS.get(k, k): v for k, v in value.items()}
                                if "GPSLatitude" in gps_info and "GPSLongitude" in gps_info:
                                    try:
                                        lat = _dms_to_decimal(gps_info["GPSLatitude"], gps_info.get("GPSLatitudeRef", "N"))
                                        lon = _dms_to_decimal(gps_info["GPSLongitude"], gps_info.get("GPSLongitudeRef", "E"))
                                    except Exception:
                                        pass
        except Exception:
            pass

    # Fallback / supplement with Google Takeout JSON sidecar
    if not date_taken or lat is None or lon is None:
        sidecar_path = _find_takeout_sidecar(file_path)
        if sidecar_path:
            takeout_meta = _extract_takeout_json(sidecar_path)
            if takeout_meta:
                if not date_taken and takeout_meta.get("date"):
                    date_taken = takeout_meta["date"]
                if lat is None and takeout_meta.get("lat") is not None:
                    lat, lon = takeout_meta["lat"], takeout_meta["lon"]

    if date_taken or (lat is not None and lon is not None):
        return {
            "path": file_path,
            "date": date_taken,
            "lat": lat,
            "lon": lon,
            "media_type": media_type
        }

    return None

def find_candidates(folder_path: str, target_country_code: str, min_year: int = 2023):
    """
    Scans directory and returns matching photos and videos for the target country code.
    Works on local folders, Google Drive synced folders, and Google Takeout exports.
    """
    folder = Path(folder_path)
    candidates = []

    print(f"Scanning folder: {folder} for {target_country_code} ({min_year}-present)...")
    for file_path in folder.rglob("*"):
        if file_path.suffix.lower() in SUPPORTED_EXTS:
            meta = extract_metadata(file_path)
            if not meta or not meta["date"] or not meta["lat"]:
                continue

            # Filter by Year
            if meta["date"].year < min_year:
                continue

            # Fast offline reverse geocoding
            res = rg.search((meta["lat"], meta["lon"]))[0]
            country_code = res["cc"].upper()

            if country_code == target_country_code.upper():
                meta["country"] = country_code
                meta["city"] = res.get("name", "")
                candidates.append(meta)

    print(f"Found {len(candidates)} matching photos/videos for {target_country_code} since {min_year}.")
    return candidates
