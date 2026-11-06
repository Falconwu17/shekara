"""Озвучка ролика через ElevenLabs: текст сцен из script_ru.md (раздел «Сцены», строки **Текст:**)
в video/public/voice/<id>.mp3. Ключ берётся из переменной окружения ELEVENLABS_API_KEY.

Запуск из корня репозитория:
    .venv\\Scripts\\python video/voice_tts.py                 # все сцены
    .venv\\Scripts\\python video/voice_tts.py 06 06s          # только эти
    .venv\\Scripts\\python video/voice_tts.py --sample 01     # проба голосов в video/out/samples/
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
VOICE_DIR = HERE / "public" / "voice"
VOICE_ID = os.environ.get("SHEKARA_VOICE", "drOVtcvHhJKNEXv5h1l5")  # Vladislav Pro: русский диктор
SAMPLE_VOICES = {"vladislav": "drOVtcvHhJKNEXv5h1l5", "dimitri": "u3pPktIMgx1Kt5EJ391v", "mikhail": "RvcToHi9ENVDatbeYNJe"}
MODEL = "eleven_multilingual_v2"
SETTINGS = {"stability": 0.5, "similarity_boost": 0.8, "style": 0.15, "use_speaker_boost": True}

# произношение: ударение в «Шекара» на последний слог (по-казахски), аббревиатуры словами
SAY = [("ШЕКАРА", "Шекара́"), ("ШЕКАРЫ", "Шекары́"), ("ШЕКАРОЙ", "Шекаро́й"), ("ШЕКАРУ", "Шекару́"),
       ("IBM", "Ай-Би-Эм"), ("НБК", "Национального банка")]


def scenes() -> dict[str, str]:
    text = (HERE / "script_ru.md").read_text(encoding="utf-8")
    out = {}
    for block in re.split(r"\n### ", text)[1:]:
        sid = block.split()[0]
        m = re.search(r"\*\*Текст:\*\*\s*(.+)", block)
        if m:
            out[sid] = m.group(1).strip()
    return out


def speakable(text: str) -> str:
    for a, b in SAY:
        text = text.replace(a, b)
    return text.replace("«", "").replace("»", "")


def tts(text: str, voice: str, path: Path) -> None:
    key = os.environ["ELEVENLABS_API_KEY"]
    body = json.dumps({"text": speakable(text), "model_id": MODEL, "voice_settings": SETTINGS}).encode("utf-8")
    req = urllib.request.Request(f"https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=mp3_44100_128",
                                 data=body, headers={"xi-api-key": key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(r.read())
    print(f"{path.name}: {path.stat().st_size // 1024} КБ")


def main(argv: list[str]) -> int:
    all_scenes = scenes()
    if argv[:1] == ["--sample"]:
        sid = argv[1] if len(argv) > 1 else "01"
        for name, voice in SAMPLE_VOICES.items():
            tts(all_scenes[sid], voice, HERE / "out" / "samples" / f"{sid}_{name}.mp3")
        return 0
    for sid in argv or list(all_scenes):
        tts(all_scenes[sid], VOICE_ID, VOICE_DIR / f"{sid}.mp3")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
