import io
import os
import time
from pathlib import Path
from datetime import timedelta
import requests
from PIL import Image
from google.cloud import storage

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass

GRAPH_API_URL = "https://graph.facebook.com/v21.0"
VIDEO_EXTS = {".mov", ".mp4", ".m4v"}

def _prepare_media_for_upload(local_path: Path) -> tuple[bytes, str, str]:
    """
    Ensures media complies with Meta Instagram API requirements:
    - Images must be JPEG.
    - Videos can be MP4 / MOV.
    Returns: (bytes_data, target_filename, content_type)
    """
    ext = local_path.suffix.lower()

    if ext in VIDEO_EXTS:
        content_type = "video/quicktime" if ext == ".mov" else "video/mp4"
        with open(local_path, "rb") as f:
            return f.read(), local_path.name, content_type
    else:
        # Instagram requires JPEG images for carousels
        target_name = f"{local_path.stem}.jpg"
        with Image.open(local_path) as img:
            if img.mode != "RGB":
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=95)
            return buf.getvalue(), target_name, "image/jpeg"

def upload_and_sign_media(local_paths: list[Path], bucket_name: str, key_path: str) -> list[dict]:
    """
    Uploads photos and videos to Google Cloud Storage and returns temporary V4 Signed URLs.
    Returns a list of dicts: [{"url": signed_url, "is_video": bool, "name": str}]
    """
    if not os.path.exists(key_path):
        raise FileNotFoundError(f"Service account key not found at: {key_path}")

    client = storage.Client.from_service_account_json(key_path)
    bucket = client.bucket(bucket_name)

    staged_items = []
    print(f"Uploading {len(local_paths)} media files (photos/videos) to gs://{bucket_name}/staging/...")

    for local_path in local_paths:
        data_bytes, target_name, content_type = _prepare_media_for_upload(local_path)
        blob_name = f"staging/{target_name}"
        blob = bucket.blob(blob_name)

        blob.upload_from_string(data_bytes, content_type=content_type)

        signed_url = blob.generate_signed_url(
            version="v4",
            expiration=timedelta(minutes=30),
            method="GET",
        )

        is_video = local_path.suffix.lower() in VIDEO_EXTS
        staged_items.append({
            "url": signed_url,
            "is_video": is_video,
            "name": local_path.name
        })

    return staged_items

def create_carousel_item(media_info: dict, ig_user_id: str, access_token: str) -> str:
    """Creates a single item container in Instagram (supporting both photos and videos)."""
    endpoint = f"{GRAPH_API_URL}/{ig_user_id}/media"
    payload = {
        "is_carousel_item": "true",
        "access_token": access_token
    }

    if media_info["is_video"]:
        payload["media_type"] = "VIDEO"
        payload["video_url"] = media_info["url"]
    else:
        payload["image_url"] = media_info["url"]

    res = requests.post(endpoint, data=payload).json()
    if "id" not in res:
        raise Exception(f"Failed to create item container for {media_info['name']}: {res}")

    container_id = res["id"]

    # For videos, wait until Instagram finishes transcoding the video container
    if media_info["is_video"]:
        print(f"Waiting for Instagram to process video ({media_info['name']})...")
        status_endpoint = f"{GRAPH_API_URL}/{container_id}"
        for _ in range(30):
            time.sleep(3)
            status_res = requests.get(status_endpoint, params={
                "fields": "status_code",
                "access_token": access_token
            }).json()

            status = status_res.get("status_code")
            if status == "FINISHED":
                break
            elif status == "ERROR":
                raise Exception(f"Instagram failed to process video {media_info['name']}: {status_res}")

    return container_id

def publish_instagram_carousel(staged_media: list[dict], caption: str, ig_user_id: str, access_token: str) -> str:
    """Publishes a carousel post of up to 10 photos/videos to Instagram."""
    print("Creating individual carousel items on Instagram...")
    item_ids = []
    for media_info in staged_media[:10]:
        item_id = create_carousel_item(media_info, ig_user_id, access_token)
        item_ids.append(item_id)

    print("Creating parent carousel post container...")
    endpoint = f"{GRAPH_API_URL}/{ig_user_id}/media"
    payload = {
        "media_type": "CAROUSEL",
        "children": ",".join(item_ids),
        "caption": caption,
        "access_token": access_token
    }
    res = requests.post(endpoint, data=payload).json()
    if "id" not in res:
        raise Exception(f"Failed to create carousel container: {res}")
    carousel_id = res["id"]

    print("Waiting 10 seconds for Meta to finalize carousel...")
    time.sleep(10)

    print(f"Publishing carousel post (container ID: {carousel_id})...")
    pub_endpoint = f"{GRAPH_API_URL}/{ig_user_id}/media_publish"
    pub_res = requests.post(pub_endpoint, data={
        "creation_id": carousel_id,
        "access_token": access_token
    }).json()

    if "id" not in pub_res:
        raise Exception(f"Failed to publish carousel: {pub_res}")

    print(f"Successfully published post to Instagram! Post ID: {pub_res['id']}")
    return pub_res["id"]
