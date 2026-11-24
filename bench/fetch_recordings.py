"""Download a few freely licensed real recordings from Wikimedia Commons.

Three solo-piano recordings and two songs with a singer plus accompaniment go
into samples/real/<slug>/ (one folder each), with a source.json beside each file.
The Commons API gives the real file URL, license and author; anything that is
not public domain, CC0, CC BY or CC BY-SA is skipped and a spare is tried.
Each download is then decoded with ffmpeg to check that it is real audio of
the expected length and not silent. The files are treated as plain data.

Run: .venv/bin/python -I bench/fetch_recordings.py
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit, urlunsplit

import numpy as np
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sheetswap import config  # noqa: E402

API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "sheet-swap-bench/0.1"
REAL_DIR = config.ROOT / "samples" / "real"
MAX_BYTES = 80 * 1024 * 1024
OK_LICENSES = ("public domain", "pd", "cc0", "cc by")   # "cc by" also matches "cc by-sa"
BAD_LICENSES = ("-nc", " nc", "-nd", " nd")
WANT = {"piano": 3, "song": 2}

# (slug, kind, Commons file title). Spares are only used if an earlier one fails.
RECORDINGS = [
    ("satie_gymnopedie_1", "piano", "File:Erik Satie - gymnopedies - la 1 ere. lent et douloureux.ogg"),
    ("beethoven_fur_elise", "piano", "File:For Elise (Für Elise) Beethoven JMC Han.ogg"),
    ("chopin_nocturne_op9_2", "piano", "File:Nocturne in E flat major, Op. 9 no. 2.mp3"),
    ("debussy_clair_de_lune", "piano", "File:Clair de lune (Claude Debussy) Suite bergamasque.ogg"),
    ("star_spangled_banner_vocal", "song", 'File:"The Star-Spangled Banner" - Solo vocalist - U.S. Navy Band.oga'),
    ("america_the_beautiful_vocal", "song", "File:America the Beautiful (male vocalist) - United States Navy Band.opus"),
    ("kingdom_coming_crumit_1927", "song", "File:\"Kingdom Coming\" by Henry C. Work – sung by Frank Crumit (1927).ogg"),
]

SAFE_EXT = {".ogg", ".oga", ".opus", ".flac", ".mp3", ".wav"}


def session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def get_with_retry(s: requests.Session, url: str, **kw) -> requests.Response:
    """GET that waits and retries when Wikimedia rate-limits (429) or hiccups (5xx)."""
    for attempt in range(6):
        r = s.get(url, timeout=60, **kw)
        if r.status_code == 200:
            return r
        if r.status_code in (429, 500, 502, 503, 504):
            wait = float(r.headers.get("Retry-After") or 0) or 5.0 * 2 ** attempt
            print(f"  HTTP {r.status_code}, waiting {wait:.0f} s", flush=True)
            time.sleep(min(wait, 120))
            continue
        r.raise_for_status()
    r.raise_for_status()
    return r


def plain(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html or "")).strip()


def file_info(s: requests.Session, title: str) -> dict:
    r = get_with_retry(s, API, params={
        "action": "query", "titles": title, "prop": "imageinfo", "format": "json",
        "iiprop": "url|size|mime|user|extmetadata|metadata",
    })
    page = next(iter(r.json()["query"]["pages"].values()))
    if "imageinfo" not in page:
        raise LookupError(f"not found on Commons: {title}")
    ii = page["imageinfo"][0]
    em = ii.get("extmetadata", {})
    meta = {m["name"]: m["value"] for m in (ii.get("metadata") or []) if isinstance(m, dict) and "name" in m}
    length = meta.get("length") or meta.get("playtime_seconds")
    u = urlsplit(ii["url"])
    return {
        "title": page["title"],
        "page_url": ii.get("descriptionurl"),
        "file_url": urlunsplit((u.scheme, u.netloc, u.path, "", "")),   # drop tracking query
        "file_ext": PurePosixPath(unquote(u.path)).suffix.lower(),
        "size": ii["size"],
        "mime": ii.get("mime"),
        "uploader": ii.get("user"),
        "license": plain(em.get("LicenseShortName", {}).get("value", "")),
        "license_url": plain(em.get("LicenseUrl", {}).get("value", "")),
        "artist": plain(em.get("Artist", {}).get("value", "")),
        "description": plain(em.get("ImageDescription", {}).get("value", ""))[:400],
        "credit": plain(em.get("Credit", {}).get("value", ""))[:300],
        "commons_length_s": float(length) if length else None,
    }


def license_ok(name: str) -> bool:
    n = name.lower()
    return n.startswith(OK_LICENSES) and not any(b in n for b in BAD_LICENSES)


def download(s: requests.Session, info: dict, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size == info["size"]:
        print(f"  already have {dest.name}")
        return
    part = dest.with_suffix(dest.suffix + ".part")
    r = get_with_retry(s, info["file_url"], stream=True)
    got = 0
    with open(part, "wb") as f:
        for chunk in r.iter_content(1 << 16):
            got += len(chunk)
            if got > MAX_BYTES:
                part.unlink(missing_ok=True)
                raise ValueError("download larger than the size cap")
            f.write(chunk)
    if got != info["size"]:
        part.unlink(missing_ok=True)
        raise ValueError(f"size mismatch: got {got}, Commons says {info['size']}")
    part.rename(dest)


def decode_check(path: Path, sr: int = 22050) -> tuple[float, float]:
    """Decode with ffmpeg to mono float32; return (duration_s, rms_dbfs)."""
    import imageio_ffmpeg
    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-nostdin", "-i", str(path),
           "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    p = subprocess.run(cmd, capture_output=True, timeout=300, check=True)
    x = np.frombuffer(p.stdout, dtype=np.float32)
    rms = float(np.sqrt(np.mean(np.square(x, dtype=np.float64)))) if x.size else 0.0
    return x.size / sr, 20 * np.log10(max(rms, 1e-10))


def main() -> None:
    REAL_DIR.mkdir(parents=True, exist_ok=True)
    s = session()
    got = {k: 0 for k in WANT}
    results = []
    for slug, kind, title in RECORDINGS:
        if got[kind] >= WANT[kind]:
            continue
        print(f"{slug}: {title}", flush=True)
        try:
            info = file_info(s, title)
            if not license_ok(info["license"]):
                print(f"  skip: license is {info['license']!r}")
                continue
            if info["size"] > MAX_BYTES:
                print(f"  skip: {info['size']} bytes is over the cap")
                continue
            ext = info["file_ext"]
            if ext not in SAFE_EXT:
                print(f"  skip: unexpected extension {ext!r}")
                continue
            folder = REAL_DIR / slug
            folder.mkdir(parents=True, exist_ok=True)
            dest = folder / f"{slug}{ext}"
            download(s, info, dest)
            dur, db = decode_check(dest)
            expect = info["commons_length_s"]
            if expect and abs(dur - expect) > max(3.0, 0.03 * expect):
                raise ValueError(f"decoded {dur:.1f} s but Commons says {expect:.1f} s")
            if db < -50:
                raise ValueError(f"nearly silent ({db:.1f} dBFS)")
            info.update(slug=slug, kind=kind, file=dest.name, decoded_s=round(dur, 2), rms_dbfs=round(db, 1))
            (folder / "source.json").write_text(json.dumps(info, indent=1, ensure_ascii=False))
            results.append(info)
            got[kind] += 1
            print(f"  ok: {dur:.1f} s, {db:.1f} dBFS, {info['license']}", flush=True)
        except Exception as e:   # one bad file shouldn't stop the rest
            print(f"  failed: {e}", flush=True)
        time.sleep(1.0)   # be gentle with the API
    (REAL_DIR / "index.json").write_text(json.dumps(
        [{k: r[k] for k in ("slug", "kind", "file", "title", "page_url", "license", "artist",
                             "uploader", "decoded_s")} for r in results], indent=1, ensure_ascii=False))
    print(f"\npiano: {got['piano']}/{WANT['piano']}, songs: {got['song']}/{WANT['song']} -> {REAL_DIR}")
    if any(got[k] < WANT[k] for k in WANT):
        sys.exit(1)


if __name__ == "__main__":
    main()
