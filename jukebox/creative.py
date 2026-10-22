"""What Qwen writes: a song plan (title, lyrics, music caption, cover prompt) and DJ lines."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .services import Services, extract_json

LANGUAGES = {"en": "English", "es": "Spanish", "fr": "French", "de": "German",
             "it": "Italian", "pt": "Portuguese", "ja": "Japanese", "ko": "Korean", "zh": "Chinese"}

MIN_DURATION, MAX_DURATION = 15, 240
INSTRUMENTAL = "[Instrumental]"


@dataclass
class SongRequest:
    style: str
    mood: str
    duration_s: int
    vocal: bool
    theme: str = ""
    language: str = "en"

    def validate(self) -> None:
        self.style = self.style.strip()[:80]
        self.mood = self.mood.strip()[:80]
        self.theme = self.theme.strip()[:300]
        if not self.style or not self.mood:
            raise ValueError("Choose a style and a mood.")
        if not MIN_DURATION <= self.duration_s <= MAX_DURATION:
            raise ValueError(f"Duration must be between {MIN_DURATION} and {MAX_DURATION} seconds.")
        if self.language not in LANGUAGES:
            raise ValueError("Unsupported lyrics language.")


@dataclass
class SongPlan:
    title: str
    lyrics: str        # INSTRUMENTAL for instrumental tracks
    caption: str       # music-model description: genre, instruments, mood, tempo, vocals
    cover_prompt: str  # image-model prompt
    summary: str       # one line about the song, reused by the DJ


_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "caption": {"type": "string"},
        "bpm": {"type": "integer"},
        "lyrics": {"type": "string"},
        "cover_prompt": {"type": "string"},
    },
    "required": ["title", "summary", "caption", "bpm", "lyrics", "cover_prompt"],
}


def _lyric_shape(duration_s: int) -> str:
    if duration_s <= 40:
        return "one short verse and one chorus (about 8 lines in total)"
    if duration_s <= 90:
        return "a verse, a chorus, a second verse and a final chorus (about 16 lines)"
    return "two verses, a chorus repeated, and a bridge (about 24 lines)"


def plan_song(svc: Services, req: SongRequest) -> SongPlan:
    lang = LANGUAGES[req.language]
    theme = req.theme or "your choice, something fitting the style and mood"
    if req.vocal:
        lyric_rule = (f"Write ORIGINAL lyrics in {lang} with {_lyric_shape(req.duration_s)}. "
                      "Mark sections with tags on their own line: [Verse], [Chorus], [Bridge]. "
                      "Do not quote or imitate existing songs or artists.")
    else:
        lyric_rule = f'This is an instrumental track: set "lyrics" to exactly "{INSTRUMENTAL}".'
    prompt = f"""Plan an original song for an AI music generator.

Style/genre: {req.style}
Mood: {req.mood}
Theme or topic: {theme}
Length: about {req.duration_s} seconds
Vocals: {"yes, sung in " + lang if req.vocal else "none (instrumental)"}

Return JSON with:
- "title": a short, original song title (2-5 words, no quotes).
- "summary": one sentence describing what the song is about and how it feels.
- "caption": a comma-separated music description for the generator: genre, sub-genre, 3-4 lead
  instruments, mood words, tempo feel, and {"the vocal type (e.g. warm female vocal)" if req.vocal else "'instrumental, no vocals'"}.
  Do not name real artists.
- "bpm": a tempo that fits the style.
- "lyrics": {lyric_rule}
- "cover_prompt": a vivid prompt for square album cover art that reflects the song's theme, style
  and mood: subject, setting, colour palette, lighting, art style. No text, letters, words or logos."""
    data = extract_json(svc.chat([{"role": "user", "content": prompt}], schema=_PLAN_SCHEMA))
    title = _clean_line(str(data.get("title") or f"{req.mood.title()} {req.style.title()}"))[:60]
    caption = _clean_line(str(data.get("caption") or f"{req.style}, {req.mood}"))
    bpm = data.get("bpm")
    if isinstance(bpm, int) and 50 <= bpm <= 200:
        caption += f", {bpm} bpm"
    lyrics = str(data.get("lyrics") or "").strip() if req.vocal else INSTRUMENTAL
    if req.vocal and len(lyrics) < 20:
        raise ValueError("The text service returned no usable lyrics; please retry.")
    cover = _clean_line(str(data.get("cover_prompt") or f"{req.mood} {req.style} album art"))
    cover += ", square album cover art, no text, no lettering"
    return SongPlan(title=title, lyrics=lyrics, caption=caption, cover_prompt=cover,
                    summary=_clean_line(str(data.get("summary") or "")))


def _clean_line(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().strip('"').strip()


# -- DJ --------------------------------------------------------------------

def _describe(track: dict) -> str:
    vocal = "instrumental" if track.get("lyrics", INSTRUMENTAL) == INSTRUMENTAL else "vocal"
    lines = [f'"{track["title"]}" ({track.get("style", "")}, {track.get("mood", "")}, {vocal})']
    if track.get("summary"):
        lines.append(f"About it: {track['summary']}")
    if vocal == "vocal":
        sample = [l for l in track.get("lyrics", "").splitlines() if l and not l.startswith("[")][:2]
        if sample:
            lines.append("A lyric line: " + " / ".join(sample))
    return "\n".join(lines)


def write_dj_line(svc: Services, prev: dict | None, nxt: dict, dj_name: str = "DJ Nova") -> str:
    """Commentary spoken between `prev` (just finished) and `nxt` (about to play)."""
    if prev is None:
        situation = f"The show is starting. Introduce the first song:\n{_describe(nxt)}"
    else:
        situation = (f"Song that just finished:\n{_describe(prev)}\n\n"
                     f"Song that plays next:\n{_describe(nxt)}")
    prompt = f"""You are {dj_name}, an upbeat, warm radio DJ on an AI-generated music station.
Write what you say on air between songs.

{situation}

Rules: 2-3 sentences, at most 55 words. Mention the finished song and the next song by title and
one specific detail of each (its mood, sound, or what it is about). Plain spoken English only:
no emojis, no hashtags, no stage directions, no asterisks, no brackets. Output only the words to speak."""
    text = svc.chat([{"role": "user", "content": prompt}], max_tokens=200, temperature=0.9)
    return clean_speech(text) or f"That was {prev['title'] if prev else 'a great start'}. Up next, {nxt['title']}."


def clean_speech(text: str) -> str:
    """Strip things a TTS model would read aloud literally."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    text = re.sub(r"\*[^*]*\*|\([^)]*\)|\[[^\]]*\]", " ", text)  # stage directions
    text = re.sub(r"[#*_~`>|]", "", text)
    text = re.sub(r"[^\w\s.,!?'\"’:;\-–—]", "", text)  # emojis and symbols
    text = re.sub(r"\s+", " ", text).strip().strip('"')
    words = text.split()
    return " ".join(words[:70])
