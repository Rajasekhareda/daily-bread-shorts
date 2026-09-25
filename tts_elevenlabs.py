"""tts_elevenlabs.py - Optional ElevenLabs narration for Daily Bread Shorts.

Model choice:
- Telugu  -> "eleven_v3": the only ElevenLabs model line covering Telugu
  (eleven_multilingual_v2 does NOT include Telugu).
- English -> "eleven_turbo_v2_5": much cheaper/faster than v3 with equal
  or better English quality.

This module is OPTIONAL: when ELEVENLABS_API_KEY or the voice IDs are
absent, the generator silently falls back to music-only mode.
"""
import os
import time

import requests

ELEVEN_BASE = "https://api.elevenlabs.io/v1/text-to-speech"
MODELS = {
    "te": "eleven_v3",
    "en": "eleven_turbo_v2_5",
}


def tts_available():
    """True only when the API key AND both voice IDs are configured."""
    api_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    te = os.environ.get("ELEVEN_VOICE_TELUGU", "").strip()
    en = os.environ.get("ELEVEN_VOICE_ENGLISH", "").strip()
    if api_key and te and en:
        return True
    missing = [name for name, val in (
        ("ELEVENLABS_API_KEY", api_key),
        ("ELEVEN_VOICE_TELUGU", te),
        ("ELEVEN_VOICE_ENGLISH", en),
    ) if not val]
    print(f"TTS disabled (missing: {', '.join(missing)}); using music-only mode.")
    return False


def synthesize(text, lang, out_path):
    """Synthesize `text` to an mp3 at out_path. lang: 'te' or 'en'.

    Retries 3x with exponential backoff (2s, 4s) on 429/5xx and network
    errors; raises RuntimeError including the response body (ElevenLabs
    error JSON is descriptive) on final failure.
    """
    if lang not in MODELS:
        raise ValueError(f"lang must be 'te' or 'en', got {lang!r}")
    api_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    voice_env = "ELEVEN_VOICE_TELUGU" if lang == "te" else "ELEVEN_VOICE_ENGLISH"
    voice = os.environ.get(voice_env, "").strip()

    payload = {
        "text": text,
        "model_id": MODELS[lang],
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
    }
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    url = f"{ELEVEN_BASE}/{voice}"

    last_err = None
    for attempt in range(1, 4):
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=180)
            if r.status_code == 200:
                with open(out_path, "wb") as fh:
                    fh.write(r.content)
                return out_path
            if r.status_code in (429, 500, 502, 503, 504):
                last_err = RuntimeError(f"HTTP {r.status_code}: {r.text[:500]}")
            else:
                raise RuntimeError(
                    f"ElevenLabs error {r.status_code}: {r.text[:500]}")
        except requests.RequestException as e:
            last_err = RuntimeError(f"network error: {e}")
        if attempt < 3:
            time.sleep(2 * 2 ** (attempt - 1))
    raise RuntimeError(
        f"ElevenLabs TTS failed after {attempt} attempts: {last_err}")


def audio_duration(path):
    """Duration of an audio file in seconds (moviepy compat import)."""
    try:
        from moviepy import AudioFileClip
    except ImportError:
        from moviepy.editor import AudioFileClip
    clip = AudioFileClip(path)
    try:
        return round(clip.duration, 4)
    finally:
        clip.close()