import os
import argparse
from pathlib import Path
from dotenv import load_dotenv
from scanner import find_candidates
from curator import curate_photos_with_gemini
from publisher import upload_and_sign_media, publish_instagram_carousel

load_dotenv()

def main():
    parser = argparse.ArgumentParser(description="AI Instagram Photo & Video Curation Agent")
    parser.add_argument("--photos-dir", required=True, help="Path to local, Google Takeout, or Google Drive synced directory")
    parser.add_argument("--country-code", required=True, help="ISO-2 Country code (e.g. PH, JP, FR, IT, US)")
    parser.add_argument("--country-name", required=True, help="Human readable country name (e.g. Philippines, Japan)")
    parser.add_argument("--min-year", type=int, default=2023, help="Earliest year to include (default: 2023)")
    parser.add_argument("--publish", action="store_true", help="Publish directly to Instagram (otherwise dry run)")
    args = parser.parse_args()

    # 1. Scan Photos & Videos
    candidates = find_candidates(args.photos_dir, args.country_code, min_year=args.min_year)
    if len(candidates) == 0:
        print(f"\n[!] Found 0 photos or videos matching {args.country_code} since {args.min_year}. Exiting.")
        return

    if len(candidates) < 10:
        print(f"\n[i] Note: Found {len(candidates)} media items (less than 10). Curating all available matching media for preview.")

    # 2. Curate via Gemini Multimodal Vision
    curation = curate_photos_with_gemini(candidates, args.country_name)
    selected_items = [candidates[idx] for idx in curation.selected_indices[:10]]
    selected_files = [item["path"] for item in selected_items]

    formatted_hashtags = " ".join(curation.hashtags)
    full_caption = f"{curation.instagram_caption}\n\n{formatted_hashtags}"

    print("\n================ CURATION SUMMARY ================")
    print(f"Reasoning:\n{curation.curation_reasoning}\n")
    print(f"Top Selected Media ({len(selected_files)} items):")
    for i, item in enumerate(selected_items, 1):
        media_label = "VIDEO" if item.get("media_type") == "video" else "PHOTO"
        print(f"  {i}. [{media_label}] {item['path'].name} ({item.get('city', '')})")
    print(f"\nInstagram Caption:\n{full_caption}")
    print("==================================================\n")

    # 3. Publish (if --publish is passed)
    if args.publish:
        bucket_name = os.getenv("GCS_BUCKET_NAME", "wheres-msim-bucket")
        key_path = os.getenv("GCP_KEY_PATH", os.path.expanduser("~/.config/gcp/ig-staging-key.json"))
        ig_user_id = os.getenv("INSTAGRAM_ACCOUNT_ID")
        access_token = os.getenv("META_ACCESS_TOKEN")

        if not ig_user_id or not access_token:
            print("[!] INSTAGRAM_ACCOUNT_ID or META_ACCESS_TOKEN missing from .env! Cannot publish.")
            return

        staged_media = upload_and_sign_media(selected_files, bucket_name, key_path)
        publish_instagram_carousel(staged_media, full_caption, ig_user_id, access_token)
    else:
        print("[i] Dry run complete! Run with '--publish' flag to upload and post to Instagram.")

if __name__ == "__main__":
    main()
