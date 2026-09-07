# Instagram Travel Photo & Video Curator Agent

An autonomous AI agent that scans unorganized photo and video libraries (local camera rolls, Google Drive sync, or Google Takeout exports), filters by trip date and destination country using embedded GPS metadata, curates the top 10 photos and video clips using Gemini Vision, writes an engaging caption with a strict 4-hashtag strategy, stages media to Google Cloud Storage (`wheres-msim-bucket`), and publishes a mixed carousel post to Instagram.

---

## Key Features

- **No Manual Sorting Required**: Point the agent at an unorganized photo dump or a Google Takeout export. It scans recursively and automatically figures out where and when each shot was taken.
- **Photos & Videos**: Supports `.jpg`, `.jpeg`, `.png`, `.heic` (Apple iPhone), `.webp`, `.mov`, and `.mp4`.
- **Google Takeout Support**: Natively extracts dates and GPS coordinates from companion `.json` sidecar files if raw EXIF is stripped.
- **Offline Reverse Geocoding**: Converts raw GPS coordinates (EXIF or QuickTime ISO6709) into city and country names locally without external API rate limits.
- **Multimodal AI Curation**: Resizes photos and extracts video keyframes via `ffmpeg` so Gemini evaluates the visual aesthetics, lighting, composition, and landmark recognition.
- **Automated Fallback Architecture**: Automatically cascades across Gemini models (`gemini-flash-latest`, `gemini-3.5-flash`, etc.) to handle temporary 503 load spikes.
- **Strict 4-Tag Strategy**:
  1. `#wheres_msim` (hardcoded 1st tag for the creator account)
  2. Country tag (e.g. `#Malaysia`)
  3. City/Region tag (e.g. `#KualaLumpur`)
  4. Landmark/Theme tag (e.g. `#PetronasTowers`)
- **Mixed Media Publishing**: Stages files to GCS with 30-minute V4 signed URLs, converts non-JPEG images on-the-fly to meet Meta API specifications, waits for video containers to transcode, and publishes the carousel via Meta Graph API.

---

## Installation & Setup

### 1. Virtual Environment & Dependencies
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> **Note**: Requires `ffmpeg` and `ffprobe` installed on your machine (e.g. via Homebrew: `brew install ffmpeg`).

### 2. Configure Credentials (`.env`)
Copy `.env.example` to `.env` and fill in your keys:

```env
# Gemini API Key (from Google AI Studio)
GEMINI_API_KEY=your_gemini_api_key_here

# Optional: override default Gemini model
GEMINI_MODEL=gemini-flash-latest

# Google Cloud Storage Settings
GCS_BUCKET_NAME=wheres-msim-bucket
GCP_KEY_PATH=/Users/shoulicofreeman/.config/gcp/ig-staging-key.json

# Instagram Graph API Settings (required only when using --publish)
INSTAGRAM_ACCOUNT_ID=your_instagram_account_id
META_ACCESS_TOKEN=your_meta_user_access_token
```

---

## Usage

### 1. Dry Run (Curate & Preview Without Posting)
Scans your media, extracts video keyframes, calls Gemini to pick the best items and write the caption, and prints the summary:

```bash
python main.py \
    --photos-dir "/path/to/photos_and_videos" \
    --country-code "MY" \
    --country-name "Malaysia"
```

To include older trips, adjust `--min-year`:
```bash
python main.py \
    --photos-dir "/path/to/photos_and_videos" \
    --country-code "PH" \
    --country-name "Philippines" \
    --min-year 2020
```

### 2. Publish Live to Instagram
Add the `--publish` flag to upload the curated items to GCS and post the carousel to Instagram:

```bash
python main.py \
    --photos-dir "/path/to/photos_and_videos" \
    --country-code "MY" \
    --country-name "Malaysia" \
    --publish
```

---

## CLI Options

| Flag | Required | Default | Description |
| :--- | :---: | :---: | :--- |
| `--photos-dir` | **Yes** | — | Directory containing your photos, videos, or Takeout export |
| `--country-code` | **Yes** | — | Two-letter ISO country code (e.g. `PH`, `MY`, `JP`, `FR`, `US`) |
| `--country-name` | **Yes** | — | Human-readable country name for caption generation context |
| `--min-year` | No | `2023` | Minimum capture year to include in the search |
| `--publish` | No | `False` | When passed, uploads to GCS and publishes live to Instagram |

---

## Project Structure

```
├── scanner.py       # Scans directory, extracts EXIF/QuickTime GPS & Takeout JSONs, reverse-geocodes
├── curator.py       # Generates thumbnails/keyframes, runs Gemini curation, enforces 4-tag rule
├── publisher.py     # Stages media to GCS, transcode-checks videos, publishes Instagram carousel
├── main.py          # CLI entry point coordinating scan -> curate -> publish pipeline
├── requirements.txt # Python package dependencies
└── README.md        # Documentation and usage guide
```
