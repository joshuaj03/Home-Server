#!/usr/bin/env python3
"""
Watch /home/jake/jellyfin/media for pasted video/subtitle files or release folders,
rename and sort them into Jellyfin format (Movies/ or Shows/), attach/download
an English .en.srt subtitle, and trigger a Jellyfin library scan.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr
from datetime import datetime
import io
import logging
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
from typing import Any
from urllib.parse import quote

from babelfish import Language
from guessit import guessit
import pyinotify
import pysubs2
import requests
from subliminal import download_best_subtitles, region, scan_video

BASE_DIR = Path(__file__).resolve().parent.parent
MEDIA_ROOT = Path(os.environ.get("JELLYFIN_MEDIA_DIR", "/home/jake/jellyfin/media"))
MOVIES_DIR = MEDIA_ROOT / "Movies"
SHOWS_DIR = MEDIA_ROOT / "Shows"
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "media-sorter.log"
CACHE_FILE = LOG_DIR / "subliminal_cache.dbm"

JELLYFIN_URL = os.environ.get("JELLYFIN_URL", "http://127.0.0.1:8096").rstrip("/")
JELLYFIN_API_KEY = os.environ.get("JELLYFIN_API_KEY", "").strip()

PROTECTED_TOP_DIRS = {"Movies", "Shows", "Anime", "Music", "MusicVideos", "Books"}
VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".webm", ".ts", ".wmv", ".flv"}
SUB_EXTS = {".srt", ".ass", ".ssa", ".vtt", ".sub"}
TEMP_EXTS = {".part", ".tmp", ".crdownload", ".!qb", ".aria2", ".downloading", ".filepart"}
JUNK_EXTS = {".txt", ".nfo", ".jpg", ".jpeg", ".png", ".url", ".sfv", ".srr", ".exe"}

NON_ENGLISH_TAGS = {
    "spa", "spanish", "esp", "fre", "french", "fra", "ger", "german", "deu",
    "ita", "italian", "por", "portuguese", "bra", "rus", "russian", "ara",
    "arabic", "hin", "hindi", "tam", "tamil", "tel", "telugu", "mal", "malayalam",
    "chi", "chinese", "chs", "cht", "jpn", "japanese", "kor", "korean", "dut",
    "dutch", "nld", "pol", "polish", "tur", "turkish", "swe", "swedish", "nor",
    "norwegian", "dan", "danish", "fin", "finnish", "hun", "hungarian", "cze",
    "czech", "ces", "rum", "romanian", "ron", "heb", "hebrew", "vie", "vietnamese",
    "ind", "indonesian", "tha", "thai", "ukr", "ukrainian", "gre", "greek", "ell",
}

STABLE_SECONDS = 10
POLL_INTERVAL = 2

LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("media-sorter")

# Silence overly verbose third-party provider tracebacks while retaining our own logs
logging.getLogger("subliminal").setLevel(logging.CRITICAL)
logging.getLogger("rebulk").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("guessit").setLevel(logging.WARNING)

_running = True


def _handle_signal(signum: int, _frame: Any) -> None:
    global _running
    log.info("Received signal %s, shutting down gracefully...", signum)
    _running = False


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)

try:
    region.configure("dogpile.cache.dbm", arguments={"filename": str(CACHE_FILE)}, replace_existing_backend=True)
except Exception:
    region.configure("dogpile.cache.memory", replace_existing_backend=True)


def sanitize_filename(name: str) -> str:
    cleaned = name.replace(":", " -").replace("/", "-").replace("\\", "-")
    cleaned = re.sub(r'[<>"|?*]', "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned or "Unknown"


def smart_title(text: str) -> str:
    if not text:
        return "Unknown"
    if text != text.lower() and text != text.upper():
        return text.strip()
    minor = {"a", "an", "and", "as", "at", "but", "by", "for", "in", "nor", "of", "on", "or", "the", "to", "vs"}
    words = text.split()
    out = []
    for i, w in enumerate(words):
        if i == 0 or i == len(words) - 1 or w.lower() not in minor:
            out.append(w.capitalize())
        else:
            out.append(w.lower())
    return " ".join(out)


def is_ignored_name(name: str) -> bool:
    if name.startswith(".") or name.startswith("._") or name.startswith("~$"):
        return True
    if name in {"Thumbs.db", "desktop.ini"}:
        return True
    return Path(name).suffix.lower() in TEMP_EXTS


def snapshot_item(path: Path) -> tuple[bool, int, float]:
    """Return (has_temp_files, total_bytes, latest_mtime) for a file or folder."""
    if not path.exists():
        return True, 0, 0.0
    if path.is_file():
        st = path.stat()
        return is_ignored_name(path.name), st.st_size, st.st_mtime
    total_size = 0
    max_mtime = path.stat().st_mtime
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fname in files:
            if is_ignored_name(fname):
                return True, 0, 0.0
            fpath = Path(root) / fname
            try:
                st = fpath.stat()
                total_size += st.st_size
                if st.st_mtime > max_mtime:
                    max_mtime = st.st_mtime
            except FileNotFoundError:
                return True, 0, 0.0
    return False, total_size, max_mtime


def wait_until_stable(path: Path, timeout_sec: int = 1800) -> bool:
    """Wait until file/directory size and mtime stop changing for STABLE_SECONDS."""
    deadline = time.monotonic() + timeout_sec
    last_sig: tuple[int, float] | None = None
    stable_since: float | None = None

    while _running and time.monotonic() < deadline:
        if not path.exists():
            return False
        has_tmp, total_size, max_mtime = snapshot_item(path)
        if has_tmp:
            last_sig = None
            stable_since = None
            time.sleep(POLL_INTERVAL)
            continue

        sig = (total_size, max_mtime)
        now = time.monotonic()
        if sig == last_sig and (total_size > 0 or path.is_dir()):
            if stable_since is None:
                stable_since = now
            if now - stable_since >= STABLE_SECONDS:
                return True
        else:
            last_sig = sig
            stable_since = now
        time.sleep(POLL_INTERVAL)

    return False


def ffprobe_valid_video(video_path: Path) -> bool:
    try:
        res = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if res.returncode != 0:
            return False
        dur = float((res.stdout or "0").strip() or "0")
        return dur > 0
    except Exception:
        return False


def lookup_movie_imdb(title: str, year: int | None) -> tuple[str, int | None]:
    """Query keyless IMDb suggestions endpoint for canonical movie title and year."""
    cleaned = re.sub(r"[^a-zA-Z0-9\s]", " ", title).strip()
    if not cleaned:
        return smart_title(title), year
    first = cleaned[0].lower()
    if not first.isalnum():
        first = "x"
    query = f"{cleaned} {year}" if year else cleaned
    url = f"https://v2.sg.media-imdb.com/suggestion/{first}/{quote(query.lower())}.json"
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if resp.status_code != 200:
            return smart_title(title), year
        items = resp.json().get("d") or []
        movie_types = {"movie", "tvMovie", "video"}
        candidates = [it for it in items if it.get("qid") in movie_types or it.get("q") in {"feature", "TV movie", "video"}]
        if not candidates:
            candidates = [it for it in items if str(it.get("id", "")).startswith("tt")]
        if not candidates:
            return smart_title(title), year

        norm_target = re.sub(r"[^a-z0-9]", "", title.lower())
        best = None
        best_score = -1
        for idx, it in enumerate(candidates[:10]):
            cand_title = str(it.get("l") or "")
            cand_year = it.get("y")
            norm_cand = re.sub(r"[^a-z0-9]", "", cand_title.lower())
            score = 10 - idx
            if norm_cand == norm_target:
                score += 50
            elif norm_target in norm_cand or norm_cand in norm_target:
                score += 20
            if year and isinstance(cand_year, int):
                if cand_year == year:
                    score += 40
                elif abs(cand_year - year) <= 1:
                    score += 15
                else:
                    score -= 20
            if score > best_score:
                best_score = score
                best = it

        if best and best.get("l"):
            cand_year = best.get("y") if isinstance(best.get("y"), int) else year
            return str(best["l"]).strip(), cand_year
    except Exception as exc:
        log.warning("IMDb lookup failed for '%s': %s", title, exc)
    return smart_title(title), year


def lookup_episode_tvmaze(
    show_title: str,
    season: int,
    episode: int,
    guessed_year: int | None,
) -> tuple[str, int | None, str | None]:
    """Query keyless TVMaze API for canonical show title, premiere year, and episode title."""
    try:
        resp = requests.get(
            "https://api.tvmaze.com/singlesearch/shows",
            params={"q": show_title, "embed": "episodes"},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            canon_name = str(data.get("name") or "").strip() or smart_title(show_title)
            premiered = str(data.get("premiered") or "")
            show_year = int(premiered[:4]) if len(premiered) >= 4 and premiered[:4].isdigit() else guessed_year
            ep_title = None
            for ep in data.get("_embedded", {}).get("episodes", []):
                if ep.get("season") == season and ep.get("number") == episode:
                    ep_title = str(ep.get("name") or "").strip() or None
                    break
            return canon_name, show_year, ep_title
    except Exception as exc:
        log.warning("TVMaze lookup failed for '%s': %s", show_title, exc)
    return smart_title(show_title), guessed_year, None


def find_existing_show_dir(show_name: str, show_year: int | None) -> Path:
    """Reuse an existing show directory if one matches case-insensitively."""
    SHOWS_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = sanitize_filename(show_name)
    norm_target = re.sub(r"[^a-z0-9]", "", safe_name.lower())
    for entry in SHOWS_DIR.iterdir():
        if not entry.is_dir():
            continue
        folder_no_year = re.sub(r"\s*\(\d{4}\)\s*$", "", entry.name)
        if re.sub(r"[^a-z0-9]", "", folder_no_year.lower()) == norm_target:
            return entry
    folder_name = f"{safe_name} ({show_year})" if show_year else safe_name
    return SHOWS_DIR / folder_name


def parse_media_metadata(video_path: Path, folder_hint: str | None = None) -> dict[str, Any]:
    guess = dict(guessit(video_path.name))
    # Fallback to descriptive parent folder if filename itself was generic (e.g., movie.mkv)
    if (not guess.get("title") or str(guess.get("title", "")).lower() in {"movie", "video", "film"}) and folder_hint:
        guess = dict(guessit(folder_hint))

    raw_title = str(guess.get("title") or video_path.stem)
    guessed_year = guess.get("year") if isinstance(guess.get("year"), int) else None
    media_type = guess.get("type", "movie")

    if media_type == "episode" and guess.get("season") is not None and guess.get("episode") is not None:
        season = int(guess["season"])
        ep_val = guess["episode"]
        episodes = [int(e) for e in ep_val] if isinstance(ep_val, list) else [int(ep_val)]
        show_name, show_year, ep_title = lookup_episode_tvmaze(raw_title, season, episodes[0], guessed_year)
        return {
            "kind": "episode",
            "show_name": sanitize_filename(show_name),
            "year": show_year,
            "season": season,
            "episodes": episodes,
            "episode_title": sanitize_filename(ep_title) if ep_title and len(episodes) == 1 else None,
        }

    canon_title, canon_year = lookup_movie_imdb(raw_title, guessed_year)
    return {
        "kind": "movie",
        "title": sanitize_filename(canon_title),
        "year": canon_year,
    }


def compute_target_paths(meta: dict[str, Any], ext: str) -> tuple[Path, Path]:
    if meta["kind"] == "episode":
        show_dir = find_existing_show_dir(meta["show_name"], meta["year"])
        season_dir = show_dir / f"Season {meta['season']:02d}"
        ep_tag = "-".join(f"E{e:02d}" for e in meta["episodes"])
        stem = f"{meta['show_name']} - S{meta['season']:02d}{ep_tag}"
        if meta.get("episode_title"):
            stem = f"{stem} - {meta['episode_title']}"
        return season_dir, season_dir / f"{stem}{ext.lower()}"

    folder_name = f"{meta['title']} ({meta['year']})" if meta.get("year") else meta["title"]
    movie_dir = MOVIES_DIR / folder_name
    return movie_dir, movie_dir / f"{folder_name}{ext.lower()}"


def unique_target_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for i in range(1, 50):
        candidate = path.with_name(f"{stem} ({i}){suffix}")
        if not candidate.exists():
            return candidate
    return path.with_name(f"{stem}_{int(time.time())}{suffix}")


def score_english_subtitle(sub_path: Path, video_stem: str | None = None) -> int:
    name_low = sub_path.name.lower()
    tokens = set(re.split(r"[^a-z0-9]+", name_low))
    if tokens & NON_ENGLISH_TAGS:
        return -100

    score = 0
    if {"eng", "english", "en"} & tokens:
        score += 50
    if "forced" in tokens or "sdh" in tokens or "hi" in tokens:
        score -= 5
    if sub_path.suffix.lower() == ".srt":
        score += 15
    elif sub_path.suffix.lower() in {".ass", ".vtt"}:
        score += 5
    if video_stem:
        v_norm = re.sub(r"[^a-z0-9]", "", video_stem.lower())
        s_norm = re.sub(r"[^a-z0-9]", "", sub_path.stem.lower())
        if s_norm.startswith(v_norm) or v_norm in s_norm:
            score += 30
    return score


def convert_sub_to_srt_if_needed(src_sub: Path, dest_stem_path: Path) -> Path:
    ext = src_sub.suffix.lower()
    if ext == ".srt":
        target = dest_stem_path.with_suffix(".en.srt")
        shutil.move(str(src_sub), str(target))
        os.chmod(target, 0o644)
        return target
    if ext in {".ass", ".ssa", ".vtt"}:
        try:
            subs = pysubs2.load(str(src_sub))
            target = dest_stem_path.with_suffix(".en.srt")
            subs.save(str(target), format_="srt", encoding="utf-8")
            os.chmod(target, 0o644)
            src_sub.unlink(missing_ok=True)
            return target
        except Exception:
            pass
    target = dest_stem_path.with_suffix(f".en{ext}")
    shutil.move(str(src_sub), str(target))
    os.chmod(target, 0o644)
    return target


def download_english_subtitle(src_video: Path, dest_video: Path) -> Path | None:
    """Download English subtitle using original filename/hash hints, saving as <dest>.en.srt."""
    target_srt = dest_video.with_suffix(".en.srt")
    if target_srt.exists():
        return target_srt

    try:
        with redirect_stderr(io.StringIO()):
            video = scan_video(str(src_video))
            subs = download_best_subtitles(
                {video},
                {Language("eng")},
                providers=["opensubtitles", "bsplayer"],
                only_one=True,
            )
            downloaded = subs.get(video) or []
            if downloaded:
                sub = downloaded[0]
                text = sub.text or ""
                if text.strip():
                    fmt = (sub.subtitle_format or "").lower()
                    if fmt and fmt != "srt":
                        try:
                            parsed = pysubs2.SSAFile.from_string(text)
                            parsed.save(str(target_srt), format_="srt", encoding="utf-8")
                        except Exception:
                            target_srt.write_text(text, encoding="utf-8", errors="replace")
                    else:
                        target_srt.write_text(text, encoding="utf-8", errors="replace")
                    os.chmod(target_srt, 0o644)
                    log.info("Downloaded English subtitle (%s) -> %s", sub.provider_name, target_srt.name)
                    return target_srt
    except Exception as exc:
        log.warning("Online subtitle download skipped/failed for %s: %s", src_video.name, exc)
    return None


def trigger_jellyfin_scan() -> None:
    if not JELLYFIN_API_KEY:
        return
    try:
        resp = requests.post(
            f"{JELLYFIN_URL}/Library/Refresh",
            params={"api_key": JELLYFIN_API_KEY},
            timeout=10,
        )
        if resp.status_code in {200, 204}:
            log.info("Triggered Jellyfin library refresh")
        else:
            log.warning("Jellyfin refresh returned HTTP %s", resp.status_code)
    except Exception as exc:
        log.warning("Failed to trigger Jellyfin refresh: %s", exc)


def sort_video_unit(
    src_video: Path,
    local_subs: list[Path],
    folder_hint: str | None = None,
) -> bool:
    if not ffprobe_valid_video(src_video):
        log.warning("Skipping invalid/incomplete video: %s", src_video)
        return False

    meta = parse_media_metadata(src_video, folder_hint=folder_hint)
    target_dir, raw_dest_video = compute_target_paths(meta, src_video.suffix)
    target_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(target_dir, 0o755)
    if meta["kind"] == "episode":
        os.chmod(target_dir.parent, 0o755)

    dest_video = unique_target_path(raw_dest_video)

    # Fetch subtitle BEFORE moving so Subliminal sees original release filename & hash
    best_local_sub = None
    if local_subs:
        scored = sorted(
            ((score_english_subtitle(s, src_video.stem), s) for s in local_subs),
            key=lambda x: x[0],
            reverse=True,
        )
        if scored and scored[0][0] >= 0:
            best_local_sub = scored[0][1]

    if best_local_sub is None:
        download_english_subtitle(src_video, dest_video)

    log.info("Sorting '%s' -> '%s'", src_video.name, dest_video)
    shutil.move(str(src_video), str(dest_video))
    os.chmod(dest_video, 0o644)

    if best_local_sub is not None and best_local_sub.exists():
        moved_sub = convert_sub_to_srt_if_needed(best_local_sub, dest_video)
        log.info("Moved local English subtitle -> %s", moved_sub.name)

    return True


def find_existing_target_for_loose_sub(sub_path: Path) -> Path | None:
    """Match a standalone .srt dropped into media root against an already-sorted video."""
    meta = parse_media_metadata(sub_path)
    target_dir, _expected_video = compute_target_paths(meta, ".mkv")
    if not target_dir.exists():
        return None
    ep_code = f"S{meta['season']:02d}E{meta['episodes'][0]:02d}" if meta["kind"] == "episode" else None
    for f in target_dir.iterdir():
        if f.is_file() and f.suffix.lower() in VIDEO_EXTS:
            if meta["kind"] == "movie" or (ep_code and ep_code in f.stem):
                return f
    return None


def cleanup_folder_if_junk_only(folder: Path, sorted_any_video: bool = False) -> None:
    if not folder.exists() or not folder.is_dir():
        return
    remaining_real = []
    for root, _dirs, files in os.walk(folder):
        for fname in files:
            fpath = Path(root) / fname
            low = fname.lower()
            ext = fpath.suffix.lower()
            if ext in JUNK_EXTS or "sample" in low or "trailer" in low or is_ignored_name(fname):
                continue
            if sorted_any_video and (ext in SUB_EXTS or ext == ".idx"):
                continue
            remaining_real.append(fpath)
    if not remaining_real:
        shutil.rmtree(folder, ignore_errors=True)
        log.info("Cleaned up processed release folder: %s", folder.name)
    else:
        log.info("Kept folder %s (%d remaining files)", folder.name, len(remaining_real))


def process_top_level_entries() -> None:
    if not MEDIA_ROOT.exists():
        return

    entries = sorted(MEDIA_ROOT.iterdir(), key=lambda p: p.name.lower())
    loose_videos: list[Path] = []
    loose_subs: list[Path] = []
    pasted_dirs: list[Path] = []

    for entry in entries:
        if entry.name in PROTECTED_TOP_DIRS or is_ignored_name(entry.name):
            continue
        if entry.is_dir():
            pasted_dirs.append(entry)
        elif entry.is_file():
            ext = entry.suffix.lower()
            if ext in VIDEO_EXTS:
                loose_videos.append(entry)
            elif ext in SUB_EXTS:
                loose_subs.append(entry)

    changed = False

    # 1. Process loose videos pasted into /media root (pair with matching loose subs)
    used_subs: set[Path] = set()
    for vid in loose_videos:
        if not wait_until_stable(vid):
            continue
        matching_subs = [s for s in loose_subs if s not in used_subs and s.exists()]
        # If multiple videos pasted together, only pair subs that share title/stem similarity or sole video
        if len(loose_videos) > 1:
            matching_subs = [s for s in matching_subs if score_english_subtitle(s, vid.stem) >= 30]
        if sort_video_unit(vid, matching_subs):
            changed = True
            for s in matching_subs:
                if not s.exists():
                    used_subs.add(s)

    # 2. Process loose subtitles pasted on their own
    for sub in loose_subs:
        if sub in used_subs or not sub.exists():
            continue
        if score_english_subtitle(sub) < 0:
            continue
        if not wait_until_stable(sub):
            continue
        target_video = find_existing_target_for_loose_sub(sub)
        if target_video:
            moved = convert_sub_to_srt_if_needed(sub, target_video)
            log.info("Matched loose subtitle '%s' -> %s", sub.name, moved)
            changed = True

    # 3. Process pasted folders (e.g., release folders containing video + Subs/)
    for folder in pasted_dirs:
        if not wait_until_stable(folder):
            continue
        all_videos: list[Path] = []
        all_subs: list[Path] = []
        for root, _dirs, files in os.walk(folder):
            for fname in files:
                fpath = Path(root) / fname
                if is_ignored_name(fname):
                    continue
                ext = fpath.suffix.lower()
                if ext in VIDEO_EXTS:
                    all_videos.append(fpath)
                elif ext in SUB_EXTS:
                    all_subs.append(fpath)

        if not all_videos:
            continue

        # Filter out obvious sample/trailer clips when a main feature exists
        main_videos = [
            v for v in all_videos
            if not any(tag in v.name.lower() or tag in v.parent.name.lower() for tag in ("sample", "trailer"))
        ]
        if not main_videos:
            main_videos = all_videos
        if len(main_videos) > 1:
            big = [v for v in main_videos if v.stat().st_size >= 25 * 1024 * 1024]
            if big:
                main_videos = big

        sorted_in_folder = False
        for vid in sorted(main_videos):
            if len(main_videos) == 1:
                candidates = [s for s in all_subs if s.exists()]
            else:
                candidates = [s for s in all_subs if s.exists() and score_english_subtitle(s, vid.stem) >= 30]
            if sort_video_unit(vid, candidates, folder_hint=folder.name):
                changed = True
                sorted_in_folder = True

        cleanup_folder_if_junk_only(folder, sorted_any_video=sorted_in_folder)

    if changed:
        trigger_jellyfin_scan()


class MediaInboxHandler(pyinotify.ProcessEvent):
    def __init__(self, notify_cb) -> None:
        super().__init__()
        self._notify = notify_cb

    def _check(self, event) -> None:
        if event.dir and event.name in PROTECTED_TOP_DIRS:
            return
        if is_ignored_name(event.name):
            return
        self._notify()

    def process_IN_CLOSE_WRITE(self, event) -> None:
        self._check(event)

    def process_IN_MOVED_TO(self, event) -> None:
        self._check(event)

    def process_IN_CREATE(self, event) -> None:
        self._check(event)


def watch_loop() -> None:
    MOVIES_DIR.mkdir(parents=True, exist_ok=True)
    SHOWS_DIR.mkdir(parents=True, exist_ok=True)
    log.info("Watching %s for new media...", MEDIA_ROOT)

    pending_event = {"dirty": True}

    def mark_dirty() -> None:
        pending_event["dirty"] = True

    wm = pyinotify.WatchManager()
    mask = pyinotify.IN_CLOSE_WRITE | pyinotify.IN_MOVED_TO | pyinotify.IN_CREATE
    wm.add_watch(str(MEDIA_ROOT), mask, rec=False)
    notifier = pyinotify.Notifier(wm, MediaInboxHandler(mark_dirty), timeout=2000)

    last_periodic = 0.0
    while _running:
        notifier.process_events()
        if notifier.check_events(timeout=2000):
            notifier.read_events()

        now = time.monotonic()
        if pending_event["dirty"] or (now - last_periodic >= 15.0):
            pending_event["dirty"] = False
            last_periodic = now
            try:
                process_top_level_entries()
            except Exception as exc:
                log.exception("Unexpected error during media sorting: %s", exc)


def main() -> None:
    parser = argparse.ArgumentParser(description="Jellyfin Media Auto-Sorter")
    parser.add_argument("--once", action="store_true", help="Scan media root once and exit")
    args = parser.parse_args()

    if args.once:
        process_top_level_entries()
    else:
        watch_loop()


if __name__ == "__main__":
    main()
