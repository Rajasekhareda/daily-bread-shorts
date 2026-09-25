# Daily Bread - Verse Shorts Generator

Cinematic 45-second YouTube **Shorts** (9:16 vertical, 1080x1920) Bible-verse
video generator. Music-only by default; optional ElevenLabs narration (Telugu + English) with the video length adapting to the voice.

## What it does

1. Reads the next unused row from a Google Sheet (Column A = Telugu,
   B = English, C = optional explanation, D = "used" marker)
2. Renders a 45-second vertical short: line-by-line text entrance from
   the top, 6-second hold, clean fade-away
3. Background: a **random cinematic animated scene GIF** (floral red,
   aurora, forest, waterfall, desert) from `assets/backgrounds/`,
   dimmed for text contrast; falls back to a random subdued gradient
   if no GIF is available
4. Loops background music to exactly 45 seconds (no voice)
5. Generates a vertical thumbnail and uploads the short to YouTube
   (private by default) and marks the row "used"

## Setup

1. Install dependencies:

   ```
   pip install -r requirements.txt
   ```

2. Environment variables (or GitHub Actions secrets):

   | Variable | Purpose |
   |---|---|
   | `SHEET_ID` | Google Sheet ID with verses |
   | `GCP_SERVICE_ACCOUNT_JSON` | service account JSON (Sheets access) |
   | `YT_REFRESH_TOKEN`, `YT_CLIENT_ID`, `YT_CLIENT_SECRET` | YouTube upload OAuth |
   | `PRIVACY_STATUS` | `private` (default), `public`, or `unlisted` |
   | `BACKGROUND_MODE` | `gif` (default, random scenic) \| `gradient` \| `image` \| `video` |
   | `BACKGROUND_GIF` | pin one specific GIF (optional) |
   | `MUSIC_DIR` | music folder (default `assets/music`) |
   | `ELEVENLABS_API_KEY`, `ELEVEN_VOICE_TELUGU`, `ELEVEN_VOICE_ENGLISH` | ElevenLabs narration; all three required to enable TTS, otherwise music-only |
   | `MUSIC_DUCK_VOLUME` | default 0.22, music level under narration |
   | `PUBLISH_AT` | optional RFC3339 UTC; auto-release time, requires PRIVACY_STATUS=private |

3. Local test render (no Sheets/YouTube):

   ```
   python generate_video_pro.py --test
   ```

4. Production run:

   ```
   python generate_video_pro.py
   ```

## Backgrounds

Scenic animated GIFs live in `assets/backgrounds/`:

- `floral_red.gif` - deep red drifting rose bokeh
- `aurora.gif` - flowing northern lights
- `forest.gif` - misty green forest with light rays
- `waterfall.gif` - cascading blue water with mist
- `desert.gif` - golden dunes at dusk

Drop in your own 9:16 GIFs (any name); one is picked at random each
run. The GIF is cover-fit to 1080x1920 and dimmed so white text stays
readable.

## ElevenLabs narration

Narration is optional. To enable it:

1. Get the API key from the ElevenLabs dashboard: **Profile -> API Keys**.
2. Get the voice IDs from **Voices -> click voice -> copy ID** (one for
   Telugu, one for English).
3. Set `ELEVENLABS_API_KEY`, `ELEVEN_VOICE_TELUGU` and
   `ELEVEN_VOICE_ENGLISH`.

Telugu uses the model `eleven_v3` (the only ElevenLabs model line
covering Telugu); English uses `eleven_turbo_v2_5` (cheaper/faster with
equal or better English quality). With the keys absent the pipeline
stays music-only (fixed 45s). After upload, the sheet's column E
(video URL) and column F (timestamp) are written automatically.

Local narrated test run (PowerShell):

```powershell
$env:ELEVENLABS_API_KEY="..."; $env:ELEVEN_VOICE_TELUGU="..."; $env:ELEVEN_VOICE_ENGLISH="..."
python generate_video_pro.py --test
```

## Google Sheet layout

| Column | Content |
|---|---|
| A | Telugu verse text |
| B | English verse text |
| C | Optional brief explanation |
| D | "used" marker (written automatically) |
| E | video URL (written automatically) |
| F | upload timestamp (written automatically) |

Rows are consumed **in order** (row 2, then row 3, ...).
