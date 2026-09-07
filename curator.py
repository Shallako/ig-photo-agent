import io
import os
import time
import subprocess
from pathlib import Path
from PIL import Image
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass

class CurationResult(BaseModel):
    selected_indices: list[int] = Field(description="The indices of the best items (photos or videos) selected (up to 10).")
    curation_reasoning: str = Field(description="Why these items were selected.")
    instagram_caption: str = Field(description="Engaging travel caption ready for Instagram with emojis.")
    hashtags: list[str] = Field(description="Exactly 3 meaningful, specific hashtags: 1 country, 1 city, and 1 landmark or theme. Do not include generic tags or #wheres_msim.")


def make_media_thumbnail(item_path, max_dim=1024, is_video=False):
    """Resizes photo or extracts video keyframe for fast API upload to Gemini."""
    if is_video or Path(item_path).suffix.lower() in {".mov", ".mp4", ".m4v"}:
        for ss in ["1", "0"]:
            cmd = [
                "ffmpeg", "-y", "-ss", ss,
                "-i", str(item_path),
                "-vf", f"scale=min({max_dim}\\,iw):-2",
                "-vframes", "1",
                "-f", "image2",
                "pipe:1"
            ]
            res = subprocess.run(cmd, capture_output=True)
            if res.returncode == 0 and res.stdout:
                return res.stdout

    # Image thumbnail
    with Image.open(item_path) as img:
        if img.mode != "RGB":
            img = img.convert("RGB")
        img.thumbnail((max_dim, max_dim))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue()

def curate_photos_with_gemini(candidates: list[dict], country_name: str) -> CurationResult:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable is not set.")

    client = genai.Client(api_key=api_key)

    # Take up to 60 candidates to keep prompt snappy and focused
    pool = candidates[:60]
    num_to_select = min(10, len(pool))

    contents = [
        f"You are an expert travel photographer curating an Instagram Carousel post for a trip to {country_name}.",
        f"Evaluate the attached {len(pool)} items (photos and video clips) and select the {num_to_select} BEST item indices.",
        "Curation Rules:",
        "1. Select a diverse, vibrant collection of photos and videos: landscapes, architecture, food, street life, and candid moments.",
        "2. Reject blurry, poorly lit, redundant, or burst duplicate shots.",
        "3. Write an engaging caption with emojis for an Instagram Creator account.",
        "4. Generate EXACTLY 3 meaningful, high-intent hashtags grounded directly in this trip: 1 for the country, 1 for the city/region, and 1 for the primary landmark, location, or cultural theme (e.g. #Malaysia, #KualaLumpur, #PetronasTowers). Never output generic spam tags like #travel, #wanderlust, or #photooftheday."
    ]

    print(f"Preparing {len(pool)} media thumbnails for Gemini analysis...")
    for idx, item in enumerate(pool):
        is_video = item.get("media_type") == "video" or Path(item["path"]).suffix.lower() in {".mov", ".mp4", ".m4v"}
        thumb_bytes = make_media_thumbnail(item["path"], is_video=is_video)
        tag = "Video Clip" if is_video else "Photo"
        contents.append(f"{tag} Index [{idx}]:")
        contents.append(types.Part.from_bytes(data=thumb_bytes, mime_type="image/jpeg"))

    print("Requesting Gemini curation and caption generation...")
    models_to_try = [
        os.getenv("GEMINI_MODEL", "gemini-flash-latest"),
        "gemini-flash-latest",
        "gemini-3.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-3.1-flash-lite",
    ]
    seen = set()
    models_to_try = [m for m in models_to_try if not (m in seen or seen.add(m))]

    last_error = None
    for model_name in models_to_try:
        try:
            time.sleep(1)
            response = client.models.generate_content(
                model=model_name,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=CurationResult,
                    temperature=0.2,
                ),
            )
            result = CurationResult.model_validate_json(response.text)

            # Enforce exactly 3 meaningful tags + #wheres_msim as the 1st tag (4 tags total max)
            clean_tags = []
            for item in result.hashtags:
                parts = item.replace(",", " ").split()
                for part in parts:
                    sub_parts = [p for p in part.split("#") if p]
                    for sub in sub_parts:
                        tag_name = "".join(ch for ch in sub if ch.isalnum() or ch == "_")
                        if tag_name and tag_name.lower() != "wheres_msim":
                            formatted_tag = f"#{tag_name}"
                            if formatted_tag not in clean_tags:
                                clean_tags.append(formatted_tag)

            result.hashtags = ["#wheres_msim"] + clean_tags[:3]

            return result
        except Exception as e:
            last_error = e
            print(f"Model {model_name} encountered an issue ({e}). Retrying with next available model...")

    raise last_error
