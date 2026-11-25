"""Sheet music from a picture: screenshot, phone photo or PDF -> MusicXML.

"also make it so that you can input like screenshots as well": a picture of
sheet music is read by optical music recognition (OMR). The MusicXML it writes
is then read like any other score (sheetswap.score_input).

    image_to_musicxml(image, out_dir)       one picture or PDF -> out_dir/omr.musicxml
    images_to_musicxml(images, out_dir)     several, in order, joined into one score

Three readers ("engines"), tried in the order of `chain()` page by page, the next
one taking over when one fails, finds no notes, or finds fewer staves than the
page's biggest system has ("you can do all 4 steps": a stronger reader was step
3; compared in bench/omr_rhythm.py):
- homr (vision transformers, CPU, ~10 s a page; first): the most accurate on
  the bench, time signatures and keys included; reads quartets, but can take a
  choir's unbracketed staves for separate systems (caught by the staff count).
  Its pins clash with this environment, so it lives in its own venv in
  tools/homr/ (README); SHEETSWAP_HOMR can point at another `homr` command.
- Audiveris (the established open-source OMR program, Java, ~15 s a page):
  reads time signatures, keys, voices, tuplets and any number of staves and
  parts (choirs, quartets). Not a Python package: `python -m sheetswap.omr
  --fetch-audiveris` puts the release (with its own Java) in tools/audiveris/;
  SHEETSWAP_AUDIVERIS can point at another copy.
- oemer (neural, CPU, ~40 s a page; last): reads note heads well but no time
  signature, two staves a system at most; it comes with requirements.txt.
SHEETSWAP_OMR="audiveris,oemer" (say), or `engine=`, sets another chain.

Before a reader sees a page it is cleaned up (`prepare`):
- grey, with transparency on white and dark-mode pages turned back to black
  on white; uneven light (phone photos) evened out;
- turned straight when the staff lines are tilted (up to 5 degrees);
- cropped to the staves: browser bars, side panels, titles and the empty rest
  of the page go;
- scaled so the staff lines are the reader's favourite distance apart
  (SPACING: 14 px for oemer, whose own scaling to about 3.7 million pixels
  blows a one-line screenshot up and shrinks a dense page; 24 px for Audiveris,
  which refuses staff lines under ~10 px apart, i.e. most screenshots).
A page with no staff (five evenly spaced long lines) is refused before the
slow part. Errors a user can fix come back as ValueError with a plain message.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request
import warnings
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

NO_MUSIC = "Couldn't read any music in that image — try a sharper screenshot of just the music."
NOT_A_PICTURE = "That file isn't a picture or PDF this can read — use PNG, JPG or PDF."
NO_MODELS = ("The music reader's model files are missing and couldn't be downloaded "
             "(it needs the internet once, about 110 MB).")
NO_READER = ("No sheet-music reader is installed — run "
             "`.venv/bin/python -m sheetswap.omr --fetch-audiveris` once.")

ROOT = Path(__file__).resolve().parent.parent
ENGINES = ("audiveris", "homr", "oemer")
DEFAULT_CHAIN = ("homr", "audiveris", "oemer")    # best first, see bench/omr_rhythm.py
READS_METER = {"audiveris", "homr"}       # readers that read the time signature off the page

IMAGE_EXTS = (".png", ".jpg", ".jpeg")
PDF_EXTS = (".pdf",)
OTHER_EXTS = (".heic", ".heif", ".webp", ".gif", ".bmp", ".tif", ".tiff")   # via Pillow, else sips
SUPPORTED_EXTS = IMAGE_EXTS + PDF_EXTS + OTHER_EXTS

# Measured on engraved test pages (violin, piano, folk tune, screenshot, photo):
# a staff spacing of 14 px beat 11 and 18 px; oemer's own window step of 128 px
# (windows overlap 4x) was ~1 point better on pitch than 256 but 3.5x slower
# (~70 s against ~20 s for a page of four systems on an M2's CPU).
TARGET_SPACING = 14.0     # px between staff lines handed to oemer
STEP = 256                # oemer's sliding-window step (px)
MAX_PIXELS = 6_000_000    # cap on a prepared page for oemer (time grows with the area)
# Audiveris refuses pages whose staff lines are under ~10 px apart (it asks for 300 dpi);
# on the bench 24 px beat 20 px (pitch 0.93 against 0.92, every meter right). homr scales
# staves itself: 20 px and the page's own size measured the same.
SPACING = {"oemer": TARGET_SPACING, "audiveris": 24.0, "homr": 20.0}
PIXEL_CAP = {"oemer": MAX_PIXELS, "audiveris": 16_000_000, "homr": 12_000_000}
READER_TIMEOUT = 300      # s for homr or Audiveris on one page
AUDIVERIS_RELEASE = ("https://github.com/Audiveris/audiveris/releases/download/5.11.0/"
                     "Audiveris-5.11.0-macosx-{arch}.dmg")
MIN_SIDE = 320            # oemer's windows are 256 and 288 px; smaller pages break it
PDF_LONG_SIDE = 3000      # px for the long side of a rasterized PDF page
MAX_PAGES = 20            # pages read from one PDF
MIN_STAFF_SPACING = 4.0   # px: staff lines closer than this can't be read (too small a picture)
MAX_TILT = 5.0            # degrees searched when straightening
MARGIN_ABOVE = 6.0        # staff spacings kept above the first staff and below the last
LINE_GREY = 205           # staff-line search: pixels darker than this
MARGIN_SIDE = 1.5         # staff spacings kept left and right of the staff lines

_LOCK = threading.Lock()          # oemer keeps its state in module globals
_SESSIONS: dict[str, object] = {}  # loaded ONNX models, reused across pages


# --------------------------------------------------------------------------- loading


def load_gray(path: Path) -> np.ndarray:
    """Any supported picture -> 8-bit grey, black ink on white."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    path = Path(path)
    try:
        im = Image.open(path)
        im.load()
    except (UnidentifiedImageError, OSError):
        im = _via_sips(path)
    im = ImageOps.exif_transpose(im)            # phone photos taken sideways
    if im.mode in ("RGBA", "LA", "P", "PA"):
        im = im.convert("RGBA")
        white = Image.new("RGBA", im.size, (255, 255, 255, 255))
        im = Image.alpha_composite(white, im)
    gray = np.asarray(im.convert("L"), dtype=np.uint8)
    if gray.size == 0:
        raise ValueError(NOT_A_PICTURE)
    if np.median(gray) < 110:                   # dark mode: light ink on a dark page
        gray = 255 - gray
    return gray


def _via_sips(path: Path):
    """Formats Pillow can't open (HEIC photos): macOS sips converts them."""
    from PIL import Image

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "page.png"
        r = subprocess.run(["sips", "-s", "format", "png", str(path), "--out", str(out)],
                           capture_output=True, timeout=60)
        if r.returncode != 0 or not out.exists():
            raise ValueError(NOT_A_PICTURE)
        im = Image.open(out)
        im.load()
        return im


_PDF_SCRIPT = r"""
ObjC.import('PDFKit'); ObjC.import('AppKit');
function run(argv) {
  var src = argv[0], outdir = argv[1], longest = parseFloat(argv[2]), most = parseInt(argv[3]);
  var doc = $.PDFDocument.alloc.initWithURL($.NSURL.fileURLWithPath(src));
  if (doc.isNil()) { return "0"; }
  var n = Math.min(doc.pageCount, most);
  for (var i = 0; i < n; i++) {
    var page = doc.pageAtIndex(i);
    var box = page.boundsForBox(1);
    var s = longest / Math.max(box.size.width, box.size.height);
    var w = Math.round(box.size.width * s), h = Math.round(box.size.height * s);
    var rep = $.NSBitmapImageRep.alloc.initWithBitmapDataPlanesPixelsWidePixelsHighBitsPerSampleSamplesPerPixelHasAlphaIsPlanarColorSpaceNameBytesPerRowBitsPerPixel(
      null, w, h, 8, 4, true, false, $("NSDeviceRGBColorSpace"), 0, 32);
    var ctx = $.NSGraphicsContext.graphicsContextWithBitmapImageRep(rep);
    if (ctx.isNil()) { return "0"; }
    $.NSGraphicsContext.saveGraphicsState;
    $.NSGraphicsContext.setCurrentContext(ctx);
    $.NSColor.whiteColor.setFill;
    $.NSBezierPath.fillRect($.NSMakeRect(0, 0, w, h));
    var xf = $.NSAffineTransform.transform;
    xf.scaleBy(s);
    xf.concat;
    page.drawWithBoxToContext(1, ctx.CGContext);
    ctx.flushGraphics;
    $.NSGraphicsContext.restoreGraphicsState;
    var png = rep.representationUsingTypeProperties(4, $());
    png.writeToFileAtomically(outdir + '/page' + ('000' + (i + 1)).slice(-3) + '.png', true);
  }
  return String(n);
}
"""


def pdf_pages(pdf: Path, out_dir: Path, long_side: int = PDF_LONG_SIDE,
              max_pages: int = MAX_PAGES) -> list[Path]:
    """Rasterize a PDF's pages to PNG (PDFKit through osascript; Quick Look,
    first page only, if that fails). No extra Python packages needed."""
    out_dir.mkdir(parents=True, exist_ok=True)
    script = out_dir / "pdf_pages.js"
    script.write_text(_PDF_SCRIPT)
    try:
        subprocess.run(["osascript", "-l", "JavaScript", str(script), str(Path(pdf).resolve()),
                        str(out_dir.resolve()), str(long_side), str(max_pages)],
                       capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("PDFKit rasterizing failed: %s", e)
    finally:
        script.unlink(missing_ok=True)
    pages = sorted(out_dir.glob("page[0-9][0-9][0-9].png"))
    if not pages:
        try:
            subprocess.run(["qlmanage", "-t", "-s", str(long_side), "-o", str(out_dir), str(pdf)],
                           capture_output=True, timeout=120)
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("Quick Look rasterizing failed: %s", e)
        thumb = out_dir / (Path(pdf).name + ".png")
        if thumb.exists():
            first = out_dir / "page001.png"
            thumb.rename(first)
            pages = [first]
    if not pages:
        raise ValueError("Couldn't open that PDF — try a screenshot of the music instead.")
    return pages


# --------------------------------------------------------------------------- page analysis


def _binarize(gray: np.ndarray) -> np.ndarray:
    """Ink mask (bool). Otsu's threshold, kept sane for near-empty pages."""
    import cv2

    t, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if not 60 <= t <= 220:
        t = 160
    return gray < t


def _runs(ink: np.ndarray, step: int) -> tuple[np.ndarray, np.ndarray]:
    """Vertical run lengths of ink and of the gaps between ink, every `step` columns."""
    cols = np.ascontiguousarray(ink[:, ::step].T).astype(np.int8)
    padded = np.pad(cols, ((0, 0), (1, 1)))
    d = np.diff(padded, axis=1)
    sc, sr = np.nonzero(d == 1)        # ink run starts (column, row), ordered by column then row
    _, er = np.nonzero(d == -1)        # ink run ends, same order
    black = er - sr
    same = sc[1:] == sc[:-1]
    white = (sr[1:] - er[:-1])[same]
    return black, white


def staff_spacing(ink: np.ndarray) -> tuple[float, int]:
    """(distance between staff lines, staff line thickness) from the most
    common vertical ink run and gap — the usual OMR estimate."""
    h, w = ink.shape
    black, white = _runs(ink, max(1, w // 500))
    black = black[(black > 0) & (black < max(4, h // 50))]
    white = white[(white > 1) & (white < max(8, h // 8))]
    if len(black) < 20 or len(white) < 20:
        return 0.0, 0
    thick = int(np.bincount(black).argmax())
    gap = int(np.bincount(white).argmax())
    return float(gap + thick), thick


def deskew_angle(ink: np.ndarray, max_deg: float = MAX_TILT) -> float:
    """Tilt in degrees that makes the staff lines level (rows of ink sharpest)."""
    import cv2

    h, w = ink.shape
    scale = min(1.0, 1000 / max(h, w))
    small = cv2.resize(ink.astype(np.uint8) * 255, (max(1, int(w * scale)), max(1, int(h * scale))),
                       interpolation=cv2.INTER_AREA)
    sh, sw = small.shape
    centre = (sw / 2, sh / 2)

    def score(a: float) -> float:
        m = cv2.getRotationMatrix2D(centre, a, 1.0)
        rot = cv2.warpAffine(small, m, (sw, sh), flags=cv2.INTER_LINEAR, borderValue=0)
        rows = rot.astype(np.float64).sum(axis=1)
        return float((rows ** 2).sum())

    coarse = np.arange(-max_deg, max_deg + 1e-9, 0.5)
    best = max(coarse, key=score)
    fine = np.arange(best - 0.5, best + 0.5 + 1e-9, 0.05)
    return float(max(fine, key=score))


def rotate(gray: np.ndarray, angle: float) -> np.ndarray:
    import cv2

    h, w = gray.shape
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(gray, m, (w, h), flags=cv2.INTER_CUBIC, borderValue=255)


def find_staves(gray: np.ndarray, spacing: float) -> list[tuple[int, int, int, int]]:
    """Five-line staves as (top y, bottom y, left x, right x).

    Long horizontal runs (at least 10 staff spacings) of anything darker
    than light grey are the line candidates (a thin line in a small
    screenshot is smeared over two grey rows, too light for the ink
    threshold); five of them evenly `spacing` apart make a staff. Ledger
    lines, beams, text underlines and the edges of browser bars don't.
    """
    import cv2

    length = int(max(20, 10 * spacing))
    darkish = (gray < LINE_GREY).astype(np.uint8)
    lines_img = cv2.morphologyEx(darkish, cv2.MORPH_OPEN, np.ones((1, length), np.uint8))
    rows = np.nonzero(lines_img.sum(axis=1) >= length)[0]
    if len(rows) == 0:
        return []
    lines = []                                    # (centre y, x0, x1)
    start = prev = rows[0]
    for r in list(rows[1:]) + [None]:
        if r is not None and r == prev + 1:
            prev = r
            continue
        band = lines_img[start:prev + 1]
        xs = np.nonzero(band.any(axis=0))[0]
        lines.append(((start + prev) / 2, int(xs[0]), int(xs[-1])))
        if r is not None:
            start = prev = r
    staves = []
    i = 0
    while i + 4 < len(lines):
        group = lines[i:i + 5]
        gaps = np.diff([g[0] for g in group])
        x0 = max(g[1] for g in group)
        x1 = min(g[2] for g in group)
        width = max(g[2] for g in group) - min(g[1] for g in group)
        if (np.all(gaps > 0.7 * spacing) and np.all(gaps < 1.35 * spacing)
                and x1 - x0 > 0.8 * width):
            staves.append((int(group[0][0]), int(group[-1][0]),
                           min(g[1] for g in group), max(g[2] for g in group)))
            i += 5
        else:
            i += 1
    return staves


# degrees, around the page's own tilt (perspective). A staff still 0.5 degrees off over
# 1000 px drifts ~9 px, enough to merge its lines into one band, so the steps are fine.
TILT_TRIES = (-3.0, -2.5, -2.0, -1.5, -1.0, -0.5, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0)


def find_tilted_staves(gray: np.ndarray, spacing: float,
                       staves: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
    """`staves` plus the ones only found with the page turned a little more either
    way: in a photo taken at an angle each staff tilts its own way, so after the
    page is straightened some are still too slanted for find_staves. Each is
    returned as its box on the page (top, bottom, left, right)."""
    import cv2

    h, w = gray.shape
    out = list(staves)
    for a in TILT_TRIES:
        back = cv2.invertAffineTransform(cv2.getRotationMatrix2D((w / 2, h / 2), a, 1.0))
        for t, b, x0, x1 in find_staves(rotate(gray, a), spacing):
            pts = np.array([[x0, t, 1], [x1, t, 1], [x0, b, 1], [x1, b, 1]], np.float64) @ back.T
            box = (int(pts[:, 1].min()), int(pts[:, 1].max()), int(pts[:, 0].min()), int(pts[:, 0].max()))
            mid = (box[0] + box[1]) / 2
            if all(not (s[0] - spacing <= mid <= s[1] + spacing) for s in out):
                out.append(box)
    return sorted(out)


def system_sizes(gray: np.ndarray, staves: list[tuple[int, int, int, int]],
                 spacing: float) -> list[int]:
    """Staves in each system, top to bottom: neighbouring staves joined at their left
    end by a vertical line (the system's opening bar line, beside a brace or bracket)
    belong to one system (a piano's two, a quartet's four)."""
    sizes = [1] if staves else []
    for (_, b0, l0, _), (t1, _, l1, _) in zip(staves, staves[1:]):
        x0 = int(max(0, min(l0, l1) - 2 * spacing))
        x1 = int(min(gray.shape[1], max(l0, l1) + 2 * spacing))
        gap = gray[b0 + 2:t1 - 1, x0:x1] < LINE_GREY
        if gap.shape[0] > 0 and gap.shape[1] > 0 and (gap.mean(axis=0) >= 0.85).any():
            sizes[-1] += 1
        else:
            sizes.append(1)
    return sizes


def prepare(image: Path | np.ndarray, out_png: Path | None = None,
            target: float = TARGET_SPACING, max_pixels: int = MAX_PIXELS) -> dict:
    """Clean a page up for a reader; write it to `out_png`.

    Returns {"spacing": staff spacing found (px, before scaling), "staves":
    number of staves, "systems": staves in each system (`system_sizes`),
    "angle": degrees turned, "scale": resize factor (1 when `target` is None),
    "size": (w, h) written, "image": the page as grey uint8}. Raises
    ValueError(NO_MUSIC) when no staff is found.
    """
    import cv2

    gray = load_gray(Path(image)) if not isinstance(image, np.ndarray) else image.copy()
    ink = _binarize(gray)
    spacing, _ = staff_spacing(ink)
    if spacing < MIN_STAFF_SPACING:
        raise ValueError(NO_MUSIC)

    # even out the light: divide by the local background (a max filter wider
    # than any notehead or beam), which also turns flat grey UI panels white
    k = int(3 * spacing) | 1
    background = cv2.dilate(gray, np.ones((k, k), np.uint8))
    background = cv2.GaussianBlur(background, (k, k), 0)
    gray = np.clip(gray.astype(np.float32) * 255.0 / np.maximum(background, 1), 0, 255)
    gray = gray.astype(np.uint8)

    angle = deskew_angle(_binarize(gray))
    if abs(angle) >= 0.15:
        gray = rotate(gray, angle)

    staves = find_staves(gray, spacing)
    if not staves:
        raise ValueError(NO_MUSIC)
    gaps = [(b - t) / 4 for t, b, _, _ in staves]
    spacing = float(np.median(gaps))
    staves = find_tilted_staves(gray, spacing, staves)
    systems = system_sizes(gray, staves, spacing)

    h, w = gray.shape
    top = max(0, int(staves[0][0] - MARGIN_ABOVE * spacing))
    bottom = min(h, int(staves[-1][1] + MARGIN_ABOVE * spacing))
    left = max(0, int(min(s[2] for s in staves) - MARGIN_SIDE * spacing))
    right = min(w, int(max(s[3] for s in staves) + MARGIN_SIDE * spacing))
    gray = gray[top:bottom, left:right]

    # faint ink (light scans, thin anti-aliased lines) made fully dark
    dark = gray[_binarize(gray)]
    if dark.size:
        lo = float(np.percentile(dark, 10))
        if lo > 5:
            gray = np.clip((gray.astype(np.float32) - lo) * 255.0 / (255.0 - lo), 0, 255)
            gray = gray.astype(np.uint8)

    scale = target / spacing if target else 1.0     # no target: the page's own size
    ch, cw = gray.shape
    if ch * cw * scale * scale > max_pixels:
        scale = (max_pixels / (ch * cw)) ** 0.5
    size = (max(1, round(cw * scale)), max(1, round(ch * scale)))
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
    gray = cv2.resize(gray, size, interpolation=interp)
    pad = int(2 * target)
    gray = cv2.copyMakeBorder(gray, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=255)
    gh, gw = gray.shape
    if gh < MIN_SIDE or gw < MIN_SIDE:
        gray = cv2.copyMakeBorder(gray, 0, max(0, MIN_SIDE - gh), 0, max(0, MIN_SIDE - gw),
                                  cv2.BORDER_CONSTANT, value=255)
    if out_png is not None:
        Path(out_png).parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(out_png), gray)
    return {"spacing": spacing, "staves": len(staves), "systems": systems, "angle": angle,
            "scale": scale, "size": (gray.shape[1], gray.shape[0]), "image": gray}


# --------------------------------------------------------------------------- oemer


def _ensure_models() -> None:
    """oemer's two ONNX models (~110 MB) are fetched on first use."""
    from oemer import MODULE_PATH
    from oemer.ete import CHECKPOINTS_URL

    for name, url in CHECKPOINTS_URL.items():
        if not name.endswith(".onnx"):
            continue                              # the .h5 files are for TensorFlow only
        folder = "unet_big" if name.startswith("1st") else "seg_net"
        dest = Path(MODULE_PATH) / "checkpoints" / folder / "model.onnx"
        if dest.exists() and dest.stat().st_size > 1_000_000:
            continue
        log.info("downloading the music reader's model %s", name)
        part = dest.with_suffix(".part")
        try:
            with urllib.request.urlopen(url, timeout=60) as r, open(part, "wb") as f:
                shutil.copyfileobj(r, f)
            part.rename(dest)
        except OSError as e:
            part.unlink(missing_ok=True)
            raise RuntimeError(NO_MODELS) from e


def _quiet(*_args, **_kwargs) -> None:
    pass


def _setup_oemer():
    """Import oemer and adapt it: CPU only, cached models, no prints, our own
    scaling, and numpy names it still uses (np.int went away in numpy 1.24)."""
    if not hasattr(np, "int"):
        np.int = int          # used by oemer 0.1.5 (staffline/symbol extraction)
    import onnxruntime as rt
    from oemer import dewarp, ete, inference, staffline_extraction

    for mod in (inference, dewarp, staffline_extraction):
        mod.print = _quiet
    for name in list(logging.root.manager.loggerDict):
        if name.startswith("oemer"):
            logging.getLogger(name).setLevel(logging.ERROR)

    inference.resize_image = lambda image: image      # prepare() already scaled the page

    if not getattr(ete, "_sheetswap_ready", False):
        stock = ete.inference

        def run(model_path, img_path, step_size=STEP, batch_size=16, manual_th=None,
                use_tf=False):
            real = rt.InferenceSession
            rt.InferenceSession = _cpu_session(real)
            try:
                return stock(model_path, img_path, step_size=STEP, batch_size=batch_size,
                             manual_th=manual_th, use_tf=False)
            finally:
                rt.InferenceSession = real

        ete.inference = run
        ete._sheetswap_ready = True
    return ete


def _cpu_session(real):
    def make(path, *args, **kwargs):
        sess = _SESSIONS.get(path)
        if sess is None:
            opts = _rt_options()
            sess = real(path, sess_options=opts, providers=["CPUExecutionProvider"])
            _SESSIONS[path] = sess
        return sess
    return make


def _rt_options():
    import onnxruntime as rt

    opts = rt.SessionOptions()
    opts.log_severity_level = 3
    return opts


def _run_oemer(png: Path, out_xml: Path) -> Path:
    from argparse import Namespace

    ete = _setup_oemer()
    ete.clear_data()
    try:
        with warnings.catch_warnings():
            # its symbol classifiers were pickled by an older scikit-learn
            warnings.filterwarnings("ignore", message="Trying to unpickle estimator")
            made = ete.extract(Namespace(img_path=str(png), output_path=str(out_xml),
                                         use_tf=False, save_cache=False, without_deskew=False))
    finally:
        ete.clear_data()
    return Path(made)


def fix_oemer_xml(xml: Path) -> None:
    """Repair oemer's MusicXML in place: it keeps voice positions in 16-bit
    unsigned numbers, so a step back of 12 comes out as <forward> 65524 (which
    also makes music21 crawl). Such moves are turned round; empty or absurd
    ones (over 16 bars of 4/4) are dropped."""
    import xml.etree.ElementTree as ET

    tree = ET.parse(xml)
    root = tree.getroot()
    for measure in root.iter("measure"):
        for el in list(measure):
            if el.tag not in ("forward", "backup"):
                continue
            dur_el = el.find("duration")
            try:
                d = int(dur_el.text)
            except (AttributeError, TypeError, ValueError):
                measure.remove(el)
                continue
            if d > 32767:
                d -= 65536
            if d < 0:
                el.tag = "backup" if el.tag == "forward" else "forward"
                d = -d
            divisions = int(root.findtext(".//divisions") or 1)
            if d == 0 or d > 64 * divisions:
                measure.remove(el)
                continue
            dur_el.text = str(d)
    tree.write(xml, encoding="UTF-8", xml_declaration=True)


def _count_notes(xml: Path) -> int:
    from music21 import converter

    try:
        return len(converter.parse(str(xml)).recurse().notes)
    except Exception:                               # music21 raises many kinds
        return 0


# --------------------------------------------------------------------------- Audiveris


def audiveris_program() -> Path | None:
    """Audiveris's program: SHEETSWAP_AUDIVERIS, else the copy in tools/audiveris/
    (`--fetch-audiveris`), an installed app, or `audiveris` on the PATH."""
    env = os.environ.get("SHEETSWAP_AUDIVERIS")
    if env:
        return Path(env) if Path(env).is_file() else None
    for p in (ROOT / "tools" / "audiveris" / "Audiveris.app" / "Contents" / "MacOS" / "Audiveris",
              Path("/Applications/Audiveris.app/Contents/MacOS/Audiveris")):
        if p.is_file():
            return p
    found = shutil.which("audiveris") or shutil.which("Audiveris")
    return Path(found) if found else None


def _movement(path: Path) -> int:
    m = re.search(r"\.mvt(\d+)\.mxl$", path.name)
    return int(m.group(1)) if m else 0


def unzip_mxl(mxl: Path, out: Path) -> Path:
    """A compressed MusicXML (.mxl) -> its score as plain MusicXML at `out`."""
    with zipfile.ZipFile(mxl) as z:
        names = z.namelist()
        main = None
        if "META-INF/container.xml" in names:
            box = ET.fromstring(z.read("META-INF/container.xml"))
            main = next((r.get("full-path") for r in box.iter() if r.tag.endswith("rootfile")), None)
        if main not in names:
            main = next(n for n in names if not n.startswith("META-INF")
                        and n.lower().endswith((".xml", ".musicxml")))
        Path(out).write_bytes(z.read(main))
    return Path(out)


def _run_audiveris(png: Path, out_xml: Path) -> Path:
    """Audiveris in batch mode (no window) on one prepared page -> MusicXML at
    out_xml. Its settings and logs stay in tools/audiveris/home, not the user's
    Library. RuntimeError when it reads nothing (it says why in its log)."""
    exe = audiveris_program()
    if exe is None:
        raise RuntimeError("Audiveris isn't installed")
    home = ROOT / "tools" / "audiveris" / "home"
    home.mkdir(parents=True, exist_ok=True)
    # its folders (~/Library/AudiverisLtd logs, Application Support settings) follow $HOME,
    # the native OCR libraries it unpacks (~/.javacpp) follow user.home or their own setting
    env = dict(os.environ, HOME=str(home),
               JAVA_TOOL_OPTIONS=f"-Djava.awt.headless=true -Duser.home={home} "
                                 f"-Dorg.bytedeco.javacpp.cachedir={home / '.javacpp' / 'cache'}")
    out_xml = Path(out_xml)
    with tempfile.TemporaryDirectory(dir=out_xml.parent) as tmp:
        r = subprocess.run([str(exe), "-batch", "-export", "-output", tmp, "--", str(png)],
                           capture_output=True, text=True, env=env, timeout=READER_TIMEOUT)
        books = sorted(Path(tmp).rglob("*.mxl"), key=_movement)
        if not books:
            said = [ln for ln in (r.stdout + r.stderr).splitlines()
                    if ("WARN" in ln or "ERROR" in ln) and "OCR" not in ln and "languages" not in ln]
            raise RuntimeError("Audiveris read nothing: "
                               + (said[0].split("|", 1)[-1].strip() if said else f"exit {r.returncode}"))
        scores = [unzip_mxl(b, Path(tmp) / f"mvt{i}.musicxml") for i, b in enumerate(books)]
        if len(scores) == 1:
            shutil.copy(scores[0], out_xml)
        else:            # an indented system starts a new "movement": one page here, rejoined
            join_musicxml(scores, out_xml, new_pages=False)
    return out_xml


def fetch_audiveris(dest: Path | None = None) -> Path:
    """Download the Audiveris release for this Mac (~85 MB, with its own Java) into
    tools/audiveris/. No system install: the disk image is opened without a window
    (its licence page skipped by converting it first) and the app copied out."""
    import platform

    dest = Path(dest or ROOT / "tools" / "audiveris")
    app = dest / "Audiveris.app"
    if (app / "Contents" / "MacOS" / "Audiveris").is_file():
        return app
    arch = "arm64" if platform.machine() == "arm64" else "x86_64"
    dest.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        dmg, cdr, mnt = Path(tmp) / "a.dmg", Path(tmp) / "a.cdr", Path(tmp) / "mnt"
        print(f"downloading {AUDIVERIS_RELEASE.format(arch=arch)}")
        with urllib.request.urlopen(AUDIVERIS_RELEASE.format(arch=arch), timeout=120) as r, \
                open(dmg, "wb") as f:
            shutil.copyfileobj(r, f)
        subprocess.run(["hdiutil", "convert", str(dmg), "-format", "UDTO", "-o", str(cdr)],
                       check=True, capture_output=True)
        mnt.mkdir()
        subprocess.run(["hdiutil", "attach", "-nobrowse", "-readonly", "-noautoopen",
                        "-mountpoint", str(mnt), str(cdr)], check=True, capture_output=True)
        try:
            subprocess.run(["ditto", str(mnt / "Audiveris.app"), str(app)], check=True)
        finally:
            subprocess.run(["hdiutil", "detach", str(mnt)], capture_output=True)
    return app


# --------------------------------------------------------------------------- homr


def homr_program() -> Path | None:
    """homr's command: SHEETSWAP_HOMR, else next to this Python, tools/homr/bin, the PATH."""
    import sys

    env = os.environ.get("SHEETSWAP_HOMR")
    if env:
        return Path(env) if Path(env).is_file() else None
    for p in (Path(sys.executable).parent / "homr", ROOT / "tools" / "homr" / "bin" / "homr"):
        if p.is_file():
            return p
    found = shutil.which("homr")
    return Path(found) if found else None


def _run_homr(png: Path, out_xml: Path) -> Path:
    """homr on one prepared page -> MusicXML at out_xml (it writes next to its input)."""
    exe = homr_program()
    if exe is None:
        raise RuntimeError("homr isn't installed")
    out_xml = Path(out_xml)
    with tempfile.TemporaryDirectory(dir=out_xml.parent) as tmp:
        img = Path(tmp) / f"page{Path(png).suffix}"
        shutil.copy(png, img)
        r = subprocess.run([str(exe), "--no-title", str(img)], capture_output=True, text=True,
                           timeout=READER_TIMEOUT)
        made = img.with_suffix(".musicxml")
        if not made.exists():
            raise RuntimeError(f"homr read nothing (exit {r.returncode})")
        shutil.copy(made, out_xml)
    return out_xml


# --------------------------------------------------------------------------- choosing


def engine_available(name: str) -> bool:
    if name == "audiveris":
        return audiveris_program() is not None
    if name == "homr":
        return homr_program() is not None
    if name == "oemer":
        import importlib.util
        return importlib.util.find_spec("oemer") is not None
    return False


def chain(engine: str | None = None) -> list[str]:
    """The readers to try, in order, that are installed: `engine` ("audiveris", or a
    list such as "homr,oemer"), else SHEETSWAP_OMR, else DEFAULT_CHAIN."""
    spec = engine or os.environ.get("SHEETSWAP_OMR") or ",".join(DEFAULT_CHAIN)
    names = [n.strip().lower() for n in spec.split(",") if n.strip()]
    for n in names:
        if n not in ENGINES:
            raise ValueError(f"Unknown sheet-music reader {n!r} (use {', '.join(ENGINES)}).")
    return [n for n in names if engine_available(n)]


def _read(engine: str, png: Path, xml: Path) -> None:
    """One reader on one prepared page (looked up when called, so tests can stand in)."""
    if engine == "oemer":
        _ensure_models()                           # RuntimeError(NO_MODELS) goes up
    run = globals()[f"_run_{engine}"]
    try:
        run(png, xml)
        if engine == "oemer":
            fix_oemer_xml(xml)
    except Exception as e:                         # readers fail in many ways on bad input
        log.warning("omr: %s failed on %s: %r", engine, png.name, e)
        xml.unlink(missing_ok=True)


# --------------------------------------------------------------------------- joining


def guess_meter(bar_lengths: list[float]) -> str | None:
    """oemer doesn't read time signatures; the usual bar length (in quarter
    notes) gives one back: 3 -> 3/4, 1.5 -> 3/8, 8 -> 4/2. Compound meters
    come out as their simple twin (6/8 -> 3/4): same bar, same beats."""
    from collections import Counter

    lengths = [round(q * 4) / 4 for q in bar_lengths if q > 0]
    if not lengths:
        return None
    q = Counter(lengths).most_common(1)[0][0]
    if q == int(q) and 1 <= q <= 7:
        return f"{int(q)}/4"
    if (2 * q) == int(2 * q) and int(2 * q) % 2 == 1 and 3 <= 2 * q <= 15:
        return f"{int(2 * q)}/8"
    if q == 8:
        return "4/2"
    return None


def _bar_lengths(path: Path) -> list[float]:
    """Lengths (quarter notes) of a page's bars on every staff, leaving out
    each staff's first and last bar (a pickup, a short last bar)."""
    from music21 import converter, stream

    out = []
    for part in converter.parse(str(path)).parts:
        bars = list(part.getElementsByClass(stream.Measure))
        middle = bars[1:-1] if len(bars) > 2 else bars
        out += [float(m.duration.quarterLength) for m in middle]
    return out


def _staves(root) -> int:
    counts = [int(e.text) for e in root.iter("staves") if (e.text or "").strip().isdigit()]
    return max(counts, default=1)


def _measure_length(m) -> int:
    """How far a measure's notes reach, in its divisions."""
    cursor = end = 0
    for el in m:
        if el.tag in ("note", "backup", "forward"):
            if el.tag == "note" and (el.find("chord") is not None or el.find("grace") is not None):
                continue
            try:
                d = int(el.findtext("duration"))
            except (TypeError, ValueError):
                continue
            cursor += -d if el.tag == "backup" else d
            end = max(end, cursor)
    return end


def _rest_measures(like: list, divisions: str | None) -> list:
    """Whole-bar rests standing in for a part a page doesn't have, one per bar of `like`."""
    out = []
    for j, m in enumerate(like):
        bar = ET.Element("measure")
        if j == 0 and divisions:
            ET.SubElement(ET.SubElement(bar, "attributes"), "divisions").text = divisions
        n = ET.SubElement(bar, "note")
        ET.SubElement(n, "rest", {"measure": "yes"})
        ET.SubElement(n, "duration").text = str(max(1, _measure_length(m)))
        ET.SubElement(n, "voice").text = "1"
        out.append(bar)
    return out


def join_musicxml(paths: list[Path], out_path: Path, title: str | None = None,
                  new_pages: bool = True, engines: list[str] | None = None) -> Path:
    """The readers' pages -> one MusicXML score, in order, bar numbers running on.

    Works on the XML itself, so every note, voice and step back stays as the
    reader wrote it (rebuilding the score in music21 shifts overlapping notes).
    Every part of the first page goes on (a string quartet's four, a piano's
    one with two staves); a page without one of them gets rests there, and a
    part only later pages have is dropped. Also:
    - when the first bar has no time signature, the first one read anywhere,
      else one guessed from the bar lengths (`guess_meter`; oemer never
      writes one, and without one readers assume 4/4);
    - oemer's made-up tempo (90) and its "Transcribed by Oemer" composer
      are dropped, the title set (or left out); <encoding><software> names the
      readers used (`engines`), which tells repair_rhythm the meter was read;
    - a one-part, one-staff score is named "Melody", not a reader's "Piano",
      so it is read as a single line (violin, flute, voice), not a keyboard part;
    - a page with fewer staves than the others simply has nothing on them.
    """
    trees = [ET.parse(p) for p in paths]
    roots = [t.getroot() for t in trees]
    base = roots[0]
    base_parts = base.findall("part")
    for root in roots[1:]:
        if len(root.findall("part")) > len(base_parts):
            log.info("omr: a page has more parts than the first; the extra ones are left out")

    for i, part in enumerate(base_parts):
        pages = [r.findall("part") for r in roots]
        n_staves = max(_staves(p[i]) if i < len(p) else 1 for p in pages)
        for el in list(part):                    # <sound tempo="90"/> outside any bar
            if el.tag != "measure":
                part.remove(el)
        number = 0
        for k, page_parts in enumerate(pages):
            if i < len(page_parts):
                page = page_parts[i]
                bars = page.findall("measure")
            else:
                bars = _rest_measures(page_parts[0].findall("measure"),
                                      page_parts[0].findtext(".//divisions"))
                page = None
            if page is not None and n_staves > 1 and _staves(page) == 1:
                for el in page.iter():           # a one-staff page: its notes are staff 1
                    if el.tag in ("note", "forward") and el.find("staff") is None:
                        later = [j for j, c in enumerate(el)
                                 if c.tag in ("beam", "notations", "lyric", "play", "listen")]
                        staff = ET.Element("staff")
                        staff.text = "1"
                        el.insert(later[0] if later else len(el), staff)
            for j, m in enumerate(bars):
                number += 1
                m.set("number", str(number))
                for attrs in m.findall("attributes"):   # staves: set once, below, for the part
                    for st in attrs.findall("staves"):
                        attrs.remove(st)
                for snd in m.findall("sound"):
                    m.remove(snd)
                if k > 0:
                    if j == 0 and new_pages:
                        pr = m.find("print")
                        if pr is None:
                            m.insert(0, ET.Element("print", {"new-page": "yes"}))
                        else:
                            pr.set("new-page", "yes")
                    part.append(m)
        _first_bar_attributes(part, n_staves, base, paths)

    for el in list(base):
        if el.tag in ("work", "movement-number", "movement-title", "identification"):
            base.remove(el)
    head = []
    if title:
        work = ET.Element("work")
        ET.SubElement(work, "work-title").text = title
        mt = ET.Element("movement-title")
        mt.text = title
        head += [work, mt]
    if engines:
        ident = ET.Element("identification")
        enc = ET.SubElement(ident, "encoding")
        ET.SubElement(enc, "software").text = "sheetswap.omr: " + ", ".join(sorted(set(engines)))
        head.append(ident)
    for at, el in enumerate(head):
        base.insert(at, el)
    if len(base_parts) == 1 and _staves(base_parts[0]) <= 1:
        sp = base.find("part-list/score-part")
        if sp is not None:
            for el in list(sp):
                if el.tag in ("score-instrument", "midi-instrument", "midi-device"):
                    sp.remove(el)
            name = sp.find("part-name")
            if name is not None:
                name.text = "Melody"

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(base)
    trees[0].write(out_path, encoding="UTF-8", xml_declaration=True)
    return out_path


def _first_bar_attributes(part, n_staves: int, base, paths: list[Path]) -> None:
    """The part's staff count, missing lower clefs and time signature, in its first bar."""
    first = part.find("measure")
    if first is None:
        return
    attrs = first.find("attributes")
    if attrs is None:
        attrs = ET.Element("attributes")
        at = 1 if len(first) and first[0].tag == "print" else 0
        first.insert(at, attrs)
    order = ["footnote", "level", "divisions", "key", "time", "staves"]

    def place(el):
        """Insert an <attributes> child where MusicXML's order wants it."""
        rank = order.index(el.tag)
        at = 0
        for i, child in enumerate(list(attrs)):
            if child.tag in order and order.index(child.tag) < rank:
                at = i + 1
        attrs.insert(at, el)

    if n_staves > 1:
        st = ET.Element("staves")
        st.text = str(n_staves)
        place(st)
        have = {c.get("number") for c in attrs.findall("clef")}
        for n in range(2, n_staves + 1):
            if str(n) not in have:
                clef = ET.SubElement(attrs, "clef", {"number": str(n)})
                ET.SubElement(clef, "sign").text = "F"
                ET.SubElement(clef, "line").text = "4"
    if first.find(".//time") is not None:
        return
    read = part.find(".//time")
    if read is not None:                         # a later page's reader read one: it holds from bar 1
        import copy
        place(copy.deepcopy(read))
        return
    if base.find(".//time") is not None:
        return
    lengths = []
    for p in paths:
        try:
            lengths += _bar_lengths(Path(p))
        except Exception:                        # music21 raises many kinds
            pass
    guess = guess_meter(lengths)
    if guess:
        beats, beat_type = guess.split("/")
        time_el = ET.Element("time")
        ET.SubElement(time_el, "beats").text = beats
        ET.SubElement(time_el, "beat-type").text = beat_type
        place(time_el)


# --------------------------------------------------------------------------- public


def _expand(images: list[Path], work: Path) -> list[tuple[Path, str, bool]]:
    """Inputs -> (picture, label for messages, must_have_music). PDF pages
    without music (a title page) are skipped, a picture without music is an error."""
    pages: list[tuple[Path, str, bool]] = []
    for i, img in enumerate(images, 1):
        img = Path(img)
        if not img.exists():
            raise ValueError(f"Couldn't find {img.name}.")
        ext = img.suffix.lower()
        if ext in PDF_EXTS:
            got = pdf_pages(img, work / f"pdf{i:02d}")
            pages += [(p, f"{img.name} page {k}", False) for k, p in enumerate(got, 1)]
        elif ext in SUPPORTED_EXTS:
            pages.append((img, img.name, True))
        else:
            raise ValueError(NOT_A_PICTURE)
    return pages


def _score_staves(xml: Path) -> int:
    """Staves a reading has in all (each part's staves added up)."""
    root = ET.parse(xml).getroot()
    return sum(_staves(p) for p in root.findall("part"))


def _read_page(page: Path, label: str, k: int, work: Path,
                engines: list[str]) -> tuple[Path, str] | None:
    """One page through the readers in turn until one finds notes on as many staves
    as the page's biggest system has (homr can read a choir's four unbracketed staves
    as four systems of one line): (MusicXML, reader), or None. A reading with too
    few staves is kept in case no later reader does better. ValueError(NO_MUSIC /
    NOT_A_PICTURE) when the page has no staff."""
    spare = None
    for i, engine in enumerate(engines):
        png = work / (f"page{k:02d}.png" if i == 0 else f"page{k:02d}-{engine}.png")
        info = prepare(page, png, target=SPACING[engine], max_pixels=PIXEL_CAP[engine])
        need = max(info["systems"], default=1)
        if i == 0:
            log.info("omr: %s: %d staves (%s a system), spacing %.1f px, turned %.2f deg",
                     label, info["staves"], "/".join(map(str, sorted(set(info["systems"])))),
                     info["spacing"], info["angle"])
        xml = work / f"page{k:02d}-{engine}.musicxml"
        t0 = time.time()
        with _LOCK:
            _read(engine, png, xml)
        found = _count_notes(xml) if xml.exists() else 0
        log.info("omr: %s: %s read %d notes in %.1f s", label, engine, found, time.time() - t0)
        if not found:
            continue
        staves = _score_staves(xml)
        if staves < need:
            log.info("omr: %s: %s read %d staves a system, the page has %d", label, engine,
                     staves, need)
            spare = spare or (xml, engine)
            continue
        spare = (xml, engine)
        break
    if spare is None:
        return None
    final = work / f"page{k:02d}.musicxml"
    shutil.copy(spare[0], final)
    return final, spare[1]


def images_to_musicxml(images: list[Path], out_dir: Path,
                       name: str = "omr.musicxml", title: str | None = None,
                       repair: bool = True, engine: str | None = None) -> Path:
    """Read several screenshots / photos / PDFs in order and join them into
    one MusicXML score at out_dir/name. Work files (the cleaned-up pages and
    what each reader read from them) stay in out_dir/omr/. `engine` picks the
    readers ("audiveris", "oemer", "homr", or a list "audiveris,oemer" tried in
    turn); the default is `chain()`.

    ValueError (plain words, safe to show) when a picture has no readable
    music; RuntimeError when no reader is installed or oemer's models can't be
    fetched.
    """
    images = [Path(p) for p in images]
    if not images:
        raise ValueError(NO_MUSIC)
    engines = chain(engine)
    out_dir = Path(out_dir)
    work = out_dir / "omr"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    pages = _expand(images, work)                   # a file that isn't a picture: said first
    if not engines:
        raise RuntimeError(NO_READER)
    many = len(images) > 1
    read: list[Path] = []
    used: list[str] = []
    for k, (page, label, must) in enumerate(pages, 1):
        try:
            got = _read_page(page, label, k, work, engines)
        except ValueError as e:
            if must and str(e) == NOT_A_PICTURE:
                raise
            if must:
                raise ValueError(_no_music(label, many)) from None
            log.info("omr: no staves on %s, skipped", label)
            continue
        if got is None:
            if must:
                raise ValueError(_no_music(label, many))
            continue
        read.append(got[0])
        used.append(got[1])
    if not read:
        raise ValueError(NO_MUSIC)
    if title is None:
        title = title_from_name(images[0])
    out = out_dir / name
    try:
        join_musicxml(read, out, title=title, engines=used)
    except Exception as e:                          # a page the XML tools couldn't take apart
        if len(read) != 1:
            raise ValueError(NO_MUSIC) from e
        log.warning("omr: kept the reader's own MusicXML (%r)", e)
        shutil.copy(read[0], out)
    if repair:
        try:
            repair_rhythm(out)
        except Exception as e:                      # a repair that fails leaves the reading
            log.warning("omr: rhythm repair skipped (%r)", e)
    return out


def image_to_musicxml(image: Path, out_dir: Path, name: str = "omr.musicxml",
                      engine: str | None = None) -> Path:
    """One screenshot, photo or PDF (all its pages) -> out_dir/name (MusicXML)."""
    return images_to_musicxml([Path(image)], out_dir, name=name, engine=engine)


_GENERIC_NAME = re.compile(r"\b(?:screen ?shot|screen|capture|img|image|photo|scan|page|input|"
                           r"untitled|download|file|pdf)\d*\b", re.I)


def title_from_name(path: Path) -> str | None:
    """A title from the file name ("moonlight_sonata.png" -> "Moonlight sonata"),
    none for camera and screenshot names ("IMG_1234", "Screenshot 2026-...")."""
    stem = re.sub(r"[_\-]+", " ", Path(path).stem).strip()
    if _GENERIC_NAME.search(stem) or sum(c.isalpha() for c in stem) < 3:
        return None
    return stem[:1].upper() + stem[1:]


def _no_music(label: str, many: bool) -> str:
    if not many:
        return NO_MUSIC
    return NO_MUSIC.replace("that image", f"{label}")


def main(argv: list[str] | None = None) -> None:
    """python -m sheetswap.omr --fetch-audiveris | [--engine homr] picture.png ..."""
    import argparse

    ap = argparse.ArgumentParser(prog="python -m sheetswap.omr",
                                 description="Read sheet music from pictures or PDFs into MusicXML.")
    ap.add_argument("images", nargs="*", type=Path)
    ap.add_argument("--fetch-audiveris", action="store_true",
                    help="download Audiveris (with its own Java) into tools/audiveris/")
    ap.add_argument("--engine", help=f"reader(s) to use, in order: {', '.join(ENGINES)}")
    ap.add_argument("--out", type=Path, default=ROOT / "out" / "omr")
    a = ap.parse_args(argv)
    if a.fetch_audiveris:
        print(f"Audiveris is in {fetch_audiveris()}")
    if a.images:
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        print(images_to_musicxml(a.images, a.out, engine=a.engine))
    elif not a.fetch_audiveris:
        ap.print_help()


if __name__ == "__main__":
    main()


# --------------------------------------------------------------------------- rhythm repair
#
# oemer reads note heads well but note lengths less so: a 16th rest read as an 8th
# rest, a dot missed, a flag or beam miscounted (an 8th inside a run of 16ths),
# triplets read as plain 8ths, and a stray rest added "to balance the rhythm". It
# writes no time signature (join_musicxml guesses one) and its rests have no type.
# repair_rhythm puts the bars right the way an engraver would read them:
# - every bar adds up to the time signature (R-METER-1): in each staff the cheapest
#   set of readings that fills the bar exactly is taken (dynamic programming over
#   the bar's notes): a rest's length changed or the rest dropped (cheap: rest
#   shapes are the most often misread), a dot added or removed, a note's flags
#   counted one more or one less, three equal notes read as a triplet (R-METER-9);
#   among equally cheap readings the one whose notes sit where notation puts them
#   wins (R-METER-5: a note shorter than the beat doesn't cross the beat, an 8th
#   starts on an 8th, a 16th on a 16th; R-BEAM-1: groups fall within beats);
# - a first bar that is short is a pickup (R-PAGE-5) and is left short, and so is a
#   short last bar;
# - the time signature is the one under which the bars need the fewest repairs (a
#   9/8 guessed from misread bars of 3/8 goes back to 3/8); oemer over-reads lengths
#   far more often than it under-reads them, so a bar read short counts against a
#   meter, and a compound meter must clearly beat its simple twin (6/8 vs 3/4). A
#   time signature homr or Audiveris read off the page (join_musicxml names the
#   reader) is kept unless the bars clearly disagree (READ_METER_BONUS); where the
#   page changes time signature, each bar is repaired in the one in force;
# - every part is repaired (a quartet's four, a choir's), each page's lengths
#   taken in that page's own divisions;
# - notes far below the treble staff in a piano score (three or more ledger lines,
#   G3 and lower) are the left hand's notes above the bass staff, read against the
#   wrong staff (R-PAGE-2): they move back to the left hand;
# - a system whose key signature comes out one accidental off the rest (5 flats among
#   4) is read in the piece's key (R-ACC-9);
# - each staff's notes are written as one voice after another, so readers see one
#   clean line a staff (oemer interleaves the staves note by note).

R_DIV = 48                      # divisions per quarter after repair (32nds and 16th triplets fit)
_TYPE_LEN = {"whole": 192, "half": 96, "quarter": 48, "eighth": 24, "16th": 12, "32nd": 6, "64th": 3}
_LEN_TYPE = {v: k for k, v in _TYPE_LEN.items()}
REST_COST = 0.6                 # a rest's length changed (or the rest dropped)
DOT_COST = 1.0                  # a dot added or removed
FLAG_COST = 1.0                 # one flag / beam more or fewer (half or double the value)
TRIPLET_COST = 1.5              # three equal notes read as a triplet (rare among 16ths, R-METER-9)
ODD_ONE_COST = 0.7              # an 8th between 16ths (or the reverse) taking its neighbours' value
EXACT_BONUS = 0.5               # meter choice: a bar that adds up as read
GROUP_COST = 1.5                # a whole bar's fast notes read one beam off
SHORT_READ_COST = 1.0           # meter choice: a bar read shorter than the meter's bar
DROP_NOTE_COST = 3.0            # a note left out (a smudge read as a note)
PLACE_COST = 0.3                # a note where notation would not put it (R-METER-5)
MAX_FIX = 6.0                   # meter choice: a bar costs at most this
MAX_REPAIR = 12.0               # dearer repairs are not made (a short bar is filled with rests)
READ_METER_BONUS = 3.0          # meter choice: a time signature a reader read off the page
REPAIR_METERS = [(2, 4), (3, 4), (4, 4), (3, 8), (6, 8), (9, 8), (12, 8), (6, 4), (2, 2), (3, 2)]
# a compound meter must earn its place: its bar has the length of a simple one (6/8 = 3/4,
# 12/8 = 4/4) and oemer reads no time signature to tell them apart
METER_PRIOR = {(4, 4): 0.0, (3, 4): 0.0, (2, 4): 0.0, (3, 8): 0.0, (6, 8): 0.5, (9, 8): 0.5,
               (12, 8): 1.5, (2, 2): 0.5, (6, 4): 1.0, (3, 2): 1.0}


def _std_lengths(limit: int) -> list[int]:
    """Plain and dotted note lengths (R_DIV) up to `limit`."""
    out = set()
    for v in _TYPE_LEN.values():
        for x in (v, v * 3 // 2):
            if x <= limit:
                out.add(x)
    return sorted(out)


def _spell_length(d: int) -> tuple[str, int, bool] | None:
    """(type, dots, triplet) writing a length of d divisions, or None."""
    for t, v in _TYPE_LEN.items():
        if v == d:
            return t, 0, False
        if v * 3 // 2 == d and v % 2 == 0:
            return t, 1, False
        if v * 2 // 3 == d and v % 3 == 0:
            return t, 0, True
    return None


class _Event:
    """One note, chord or rest of a staff: its elements, voice, place and length."""

    def __init__(self, el, staff: str, voice: str, start: int, dur: int):
        self.els = [el]
        self.staff = staff
        self.voice = voice
        self.start = start
        self.dur = dur
        self.rest = el.find("rest") is not None
        typ = el.findtext("type")
        self.base = _TYPE_LEN.get(typ or "")
        self.dots = len(el.findall("dot"))
        self.triplet = el.find("time-modification") is not None


def _meter_unit(num: int, den: int) -> tuple[int, int]:
    """(bar, beat) in R_DIV divisions."""
    bar = num * 4 * R_DIV // den
    if den == 8 and num % 3 == 0 and num > 3:
        return bar, 3 * R_DIV // 2
    if den == 8:
        return bar, R_DIV // 2
    if den == 2:
        return bar, 2 * R_DIV
    return bar, R_DIV


def _place_cost(start: int, d: int, beat: int) -> float:
    """Where notation lets a note start (R-METER-4/5, R-BEAM-1): a note that starts
    off the beat does not run past it (except the 8th-quarter-8th figure, half the
    cost), notes start on the 16th grid (32nds on the 32nd grid), and triplet notes on
    their own grid."""
    cost = 0.0
    off = start % beat
    if off and off + d > beat:
        cost += PLACE_COST / 2 if (2 * off == beat and d == beat) else PLACE_COST
    if d % 8 == 0 and d % 12:
        grid = d                              # triplet values
    else:
        grid = 12 if d >= 12 else 6
    if start % grid:
        cost += PLACE_COST
    return cost


def _options(ev: _Event, bar: int, prev: _Event | None = None,
             nxt: _Event | None = None) -> list[tuple[int, float]]:
    """(new length, cost) readings of one event. A note whose two neighbours share a
    value one flag away from its own (an 8th in a run of 16ths) takes their value
    cheaply: a beamed group's notes are usually all alike (R-BEAM-5)."""
    out = {ev.dur: 0.0}
    if ev.rest:
        for v in _std_lengths(bar):
            out.setdefault(v, REST_COST)
        out.setdefault(0, REST_COST)
        return sorted(out.items())
    base = ev.base or ev.dur
    vals = {}
    for b, c in ((base, 0.0), (base * 2, FLAG_COST), (base // 2, FLAG_COST)):
        if b < 3:
            continue
        for dots, dc in ((0, 0.0 if ev.dots == 0 else DOT_COST), (1, 0.0 if ev.dots == 1 else DOT_COST)):
            v = b * 3 // 2 if dots else b
            if ev.triplet:
                v = v * 2 // 3
            if 0 < v <= bar:
                vals[v] = min(vals.get(v, 99.0), c + dc)
    for v, c in vals.items():
        out[v] = min(out.get(v, 99.0), c)
    if (prev is not None and nxt is not None and not prev.rest and not nxt.rest
            and prev.dur == nxt.dur and prev.dur in (ev.dur * 2, ev.dur // 2) and prev.dur > 0):
        out[prev.dur] = min(out.get(prev.dur, 99.0), ODD_ONE_COST)
    out.setdefault(0, DROP_NOTE_COST)
    return sorted(out.items())


def _fit(events: list[_Event], bar: int, beat: int) -> tuple[float, list[int]] | None:
    """The cheapest new lengths for `events` (in order) that fill `bar` exactly:
    (cost, lengths), or None."""
    n = len(events)
    INF = float("inf")
    best: list[dict[int, tuple[float, tuple]]] = [dict() for _ in range(n + 1)]
    best[0][0] = (0.0, ())
    opts = [_options(e, bar, events[i - 1] if i else None, events[i + 1] if i + 1 < n else None)
            for i, e in enumerate(events)]
    for i in range(n):
        for t, (c0, path) in best[i].items():
            for v, c in opts[i]:
                t2 = t + v
                if t2 > bar:
                    continue
                c2 = c0 + c + (_place_cost(t, v, beat) if v else 0.0)
                if c2 < best[i + 1].get(t2, (INF,))[0]:
                    best[i + 1][t2] = (c2, path + (v,))
            # three equal plain notes as a triplet (R-METER-9)
            if i + 3 <= n and all(not e.rest and not e.triplet and e.dots == 0 and e.base
                                  and e.base == events[i].base for e in events[i:i + 3]):
                v = events[i].base * 2 // 3
                t2 = t + 3 * v
                if t2 <= bar and events[i].base % 3 == 0:
                    c2 = c0 + TRIPLET_COST + (0.0 if t % (3 * v) == 0 else PLACE_COST)
                    if c2 < best[i + 3].get(t2, (INF,))[0]:
                        best[i + 3][t2] = (c2, path + (-v, -v, -v))
    got = best[n].get(bar)
    return None if got is None else (got[0], list(got[1]))


def _fit_any(events: list[_Event], bar: int, beat: int) -> tuple[float, list[int]] | None:
    """_fit, also reading a whole run of fast notes one beam off: every note of a
    16th or shorter as twice as fast (32nd runs read as 16ths), or as 16th triplets
    (R-METER-9), at GROUP_COST for the bar."""
    best = _fit(events, bar, beat)
    short = [e for e in events if not e.rest and e.base and e.base <= 12 and not e.triplet]
    if len(short) < 4:
        return best
    saved = [(e, e.dur, e.base, e.triplet) for e in short]
    for scale_num, scale_den, trip in ((1, 2, False), (2, 3, True)):
        for e in short:
            e.dur = e.dur * scale_num // scale_den
            e.base = e.base * scale_num // scale_den if not trip else e.base
            e.triplet = trip or e.triplet
        alt = _fit(events, bar, beat)
        for e, d, b, t in saved:
            e.dur, e.base, e.triplet = d, b, t
        if alt is not None and (best is None or alt[0] + GROUP_COST < best[0]):
            best = (alt[0] + GROUP_COST, alt[1])
    return best


def _apply_length(ev: _Event, v: int) -> None:
    """Write length v (negative: a triplet of length -v) into the event's elements."""
    trip = v < 0
    v = abs(v)
    ev.dur = v
    spelled = _spell_length(v)
    for el in ev.els:
        el.find("duration").text = str(v)
        if spelled is None:
            continue
        typ, dots, _ = spelled
        if trip:
            typ, dots = _LEN_TYPE.get(v * 3 // 2, typ), 0
        for d in el.findall("dot"):
            el.remove(d)
        for tm in el.findall("time-modification"):
            el.remove(tm)
        t_el = el.find("type")
        if t_el is None:
            t_el = ET.Element("type")
            dur_el = el.find("duration")
            el.insert(list(el).index(dur_el) + 1, t_el)
        t_el.text = typ
        at = list(el).index(t_el) + 1
        for _ in range(dots):
            el.insert(at, ET.Element("dot"))
            at += 1
        if trip:
            tm = ET.Element("time-modification")
            ET.SubElement(tm, "actual-notes").text = "3"
            ET.SubElement(tm, "normal-notes").text = "2"
            el.insert(at, tm)
    if ev.rest:
        r = ev.els[0].find("rest")
        if r is not None and "measure" in r.attrib:
            del r.attrib["measure"]


def _rest_el(staff: str, voice: str, d: int):
    el = ET.Element("note")
    ET.SubElement(el, "rest")
    ET.SubElement(el, "duration").text = str(d)
    ET.SubElement(el, "voice").text = voice
    spelled = _spell_length(d)
    if spelled:
        ET.SubElement(el, "type").text = spelled[0]
        for _ in range(spelled[1]):
            ET.SubElement(el, "dot")
    ET.SubElement(el, "staff").text = staff
    return el


def _pad(events: list[_Event], bar: int, staff: str, voice: str) -> list[_Event]:
    """Fallback: cut what runs past the bar, fill a short bar with rests."""
    out, t = [], 0
    for ev in events:
        if t >= bar:
            break
        if t + ev.dur > bar:
            _apply_length(ev, bar - t)
        out.append(ev)
        t += ev.dur
    for v in sorted(_std_lengths(bar), reverse=True):
        while bar - t >= v and v > 0:
            out.append(_Event(_rest_el(staff, voice, v), staff, voice, t, v))
            t += v
    return out


def _place_voice(events: list[_Event], bar: int, staff: str, voice: str) -> list[_Event]:
    """A second voice as oemer placed it: rests before its notes, cut at the bar."""
    out, t = [], 0
    for ev in sorted(events, key=lambda e: e.start):
        if ev.start >= bar or ev.start < t:
            continue
        if ev.start > t:
            out += _pad([], ev.start - t, staff, voice)
            t = ev.start
        if t + ev.dur > bar:
            _apply_length(ev, bar - t)
        out.append(ev)
        t += ev.dur
    return out


def _measure_events(measure, scale: int) -> list[_Event]:
    """A measure's notes as events in reading order (oemer's backups give each note
    its place; notes of one staff and voice at the same place are one chord)."""
    events: list[_Event] = []
    cursor = 0
    last_at: dict[tuple[str, str, int], _Event] = {}
    for el in list(measure):
        if el.tag in ("backup", "forward"):
            try:
                d = int(el.findtext("duration")) * scale
            except (TypeError, ValueError):
                d = 0
            cursor += -d if el.tag == "backup" else d
            cursor = max(cursor, 0)
            continue
        if el.tag != "note" or el.find("grace") is not None:
            continue
        try:
            d = int(el.findtext("duration")) * scale
        except (TypeError, ValueError):
            continue
        staff = el.findtext("staff") or "1"
        voice = el.findtext("voice") or "1"
        if el.find("chord") is not None and events:
            events[-1].els.append(el)
            continue
        key = (staff, voice, cursor)
        prev = last_at.get(key)
        if prev is not None and not prev.rest and el.find("rest") is None:
            if el.find("chord") is None:
                el.insert(0, ET.Element("chord"))
            prev.els.append(el)
            cursor += d
            continue
        if prev is not None and el.find("rest") is not None:
            cursor += d                       # a rest under a note at the same place: oemer's filler
            continue
        ev = _Event(el, staff, voice, cursor, d)
        events.append(ev)
        last_at[key] = ev
        cursor += d
    return events


def _clefs(root) -> dict[str, str]:
    out = {}
    for c in root.iter("clef"):
        out.setdefault(c.get("number") or "1", c.findtext("sign") or "")
    return out


_STEPS = "CDEFGAB"


def _step_index(el) -> int | None:
    p = el.find("pitch")
    if p is None:
        return None
    try:
        return int(p.findtext("octave")) * 7 + _STEPS.index(p.findtext("step"))
    except (TypeError, ValueError):
        return None


def _set_step_index(el, idx: int, key_alter: dict[str, int]) -> None:
    p = el.find("pitch")
    step = _STEPS[idx % 7]
    p.find("step").text = step
    p.find("octave").text = str(idx // 7)
    alter = p.find("alter")
    if alter is not None:
        alter.text = str(key_alter.get(step, 0))


def _key_alters(fifths: int) -> dict[str, int]:
    sharps, flats = "FCGDAEB", "BEADGCF"
    if fifths > 0:
        return {s: 1 for s in sharps[:fifths]}
    return {s: -1 for s in flats[:-fifths]}


LOW_IN_TREBLE = 3 * 7 + 5       # A3 (two ledger lines below the treble staff) and lower
STAFF_GAP_STEPS = 8             # treble-staff step -> bass-staff step for a note between the staves


def _restaff(measure, key_alter: dict[str, int]) -> int:
    """Notes far below the treble staff of a piano score go to the bass staff
    (R-PAGE-2): read against the wrong staff, a note above the bass staff comes out
    as G3 or lower in the treble. A chord moves only if all its notes are that low.
    Returns how many notes moved."""
    chords: list[list] = []
    for el in measure:
        if el.tag != "note" or el.find("rest") is not None:
            continue
        if el.find("chord") is not None and chords:
            chords[-1].append(el)
        else:
            chords.append([el])
    moved = 0
    for ch in chords:
        if any((el.findtext("staff") or "1") != "1" for el in ch):
            continue
        idx = [_step_index(el) for el in ch]
        if any(i is None or i > LOW_IN_TREBLE for i in idx):
            continue
        for el, i in zip(ch, idx):
            _set_step_index(el, i + STAFF_GAP_STEPS, key_alter)
            st = el.find("staff")
            if st is None:
                st = ET.SubElement(el, "staff")
            st.text = "2"
            moved += 1
    return moved


def _voices(events: list[_Event]) -> dict[tuple[str, str], list[_Event]]:
    out: dict[tuple[str, str], list[_Event]] = {}
    for ev in events:
        out.setdefault((ev.staff, ev.voice), []).append(ev)
    return out


def _bar_cost(groups, bar: int, beat: int, edge: bool = False) -> float:
    """What it costs to make every staff's first voice fill `bar` (an `edge` bar, the
    first or last, may also be short: a pickup, a short last bar)."""
    cost = 0.0
    for (staff, voice), evs in groups.items():
        if voice != min(v for s, v in groups if s == staff):
            continue
        total = sum(e.dur for e in evs)
        if total == bar:
            cost -= EXACT_BONUS                       # already adds up: evidence for this meter
            continue
        if edge and total < bar:
            continue
        fit = _fit(evs, bar, beat)
        cost += MAX_FIX if fit is None else min(fit[0], MAX_FIX)
        if sum(e.dur for e in evs) < bar:
            cost += SHORT_READ_COST               # oemer over-reads lengths far more than it under-reads
    return cost


def _to_repair_grid(parts) -> bool:
    """Every duration in R_DIV divisions a quarter, page by page (each reader's page
    may count in its own divisions). False, nothing changed, when a length is finer."""
    todo = []
    for part in parts:
        div = 1
        for m in part.findall("measure"):
            for el in m:
                if el.tag == "attributes" and el.findtext("divisions"):
                    try:
                        div = max(1, int(el.findtext("divisions")))
                    except ValueError:
                        return False
                for d in el.iter("duration"):
                    try:
                        v = int(d.text) * R_DIV
                    except (TypeError, ValueError):
                        return False
                    if v % div:
                        return False
                    todo.append((d, v // div))
    for d, v in todo:
        d.text = str(v)
    for part in parts:
        for d in part.iter("divisions"):
            d.text = str(R_DIV)
    return True


def _time_of(t_el) -> tuple[int, int] | None:
    try:
        return int(t_el.findtext("beats")), int(t_el.findtext("beat-type"))
    except (TypeError, ValueError):
        return None                                # "3+2" beats, senza misura: not repaired


def _meter_read(root) -> bool:
    """The time signature came from a reader that reads it off the page."""
    said = " ".join(e.text or "" for e in root.iter("software")).lower()
    return any(name in said for name in READS_METER)


def _set_time(part, num: int, den: int) -> None:
    """The part's first time signature (made if missing) set to num/den."""
    t_el = part.find(".//time")
    if t_el is None:
        first = part.find("measure")
        attrs = first.find("attributes")
        if attrs is None:
            attrs = ET.Element("attributes")
            first.insert(0, attrs)
        t_el = ET.Element("time")
        ET.SubElement(t_el, "beats")
        ET.SubElement(t_el, "beat-type")
        at = len([c for c in attrs if c.tag in ("footnote", "level", "divisions", "key")])
        attrs.insert(at, t_el)
    t_el.find("beats").text = str(num)
    t_el.find("beat-type").text = str(den)


def repair_rhythm(xml: Path) -> dict:
    """Put an OMR score's rhythm right in place (see the notes above), in every part
    (a quartet's four, a piano's two staves). Returns what was done: {"meter": "3/8",
    "bars": bars, "fixed": staff-bars repaired, "padded": short ones filled with rests,
    "left": overfull ones beyond repair (every note kept), "moved": notes moved to
    the bass staff, "keys": bars whose misread key was put back}."""
    tree = ET.parse(xml)
    root = tree.getroot()
    parts = [p for p in root.findall("part") if p.find("measure") is not None]
    if not parts:
        return {}
    times = [_time_of(t) for t in parts[0].iter("time")]
    if None in times:
        return {}
    if not _to_repair_grid(parts):
        return {}                                 # lengths finer than the repair's grid: left alone
    keys_fixed = moved = 0
    bars_of = []                                  # per part: its measures
    groups_of = []                                # per part: per measure: voices
    for part in parts:
        measures = part.findall("measure")
        keys_fixed += _key_consistency(measures)
        clefs = _clefs(part)
        if clefs.get("1") == "G" and clefs.get("2") == "F":
            key_alter = _key_alters(int(part.findtext(".//key/fifths") or 0))
            moved += sum(_restaff(m, key_alter) for m in measures)
        bars_of.append(measures)
        groups_of.append([_voices(_measure_events(m, 1)) for m in measures])
    n_bars = len(bars_of[0])

    if len(set(times)) > 1:
        # the page changes time signature (a reader read them): each bar keeps the one in force
        meter_at, cur = [], times[0]
        for m in bars_of[0]:
            t = m.find("attributes/time")
            cur = _time_of(t) if t is not None else cur
            meter_at.append(cur)
        label = "/".join(map(str, times[0])) + " (changes)"
    else:
        cur = times[0] if times else None
        trusted = cur is not None and _meter_read(root)
        candidates = list(REPAIR_METERS) + ([cur] if trusted and cur not in REPAIR_METERS else [])
        # the time signature that needs the fewest repairs (first and last bar left out)
        best, best_cost = cur, float("inf")
        for num, den in candidates:
            bar, beat = _meter_unit(num, den)
            c = METER_PRIOR.get((num, den), 1.0)
            for groups in groups_of:
                inner = groups[1:-1] if len(groups) > 2 else groups   # the least sure bars
                c += sum(_bar_cost(g, bar, beat) for g in inner)
            if (num, den) == cur:
                c -= READ_METER_BONUS if trusted else 0.25
            if c < best_cost:
                best, best_cost = (num, den), c
        best = best or (4, 4)
        for part in parts:
            _set_time(part, *best)
        meter_at = [best] * n_bars
        label = f"{best[0]}/{best[1]}"

    fixed = padded = left = 0
    for measures, groups in zip(bars_of, groups_of):
        f, p, lft = _repair_bars(measures, groups, meter_at)
        fixed, padded, left = fixed + f, padded + p, left + lft
    tree.write(xml, encoding="UTF-8", xml_declaration=True)
    return {"meter": label, "bars": n_bars, "fixed": fixed, "padded": padded,
            "left": left, "moved": moved, "keys": keys_fixed}


def _repair_bars(measures, groups, meter_at) -> tuple[int, int, int]:
    """Every bar of one part made to add up (see repair_rhythm): (fixed, padded, left)."""
    fixed = padded = left = 0
    for k, (m, g) in enumerate(zip(measures, groups)):
        bar, beat = _meter_unit(*meter_at[min(k, len(meter_at) - 1)])
        edge = k == 0 or k == len(measures) - 1
        new_groups = {}
        firsts = {}
        for staff, voice in sorted(g):
            firsts.setdefault(staff, voice)
        for (staff, voice), evs in sorted(g.items()):
            if firsts[staff] != voice:                    # another voice: kept where it was
                new_groups[(staff, voice)] = _place_voice(evs, bar, staff, voice)
                continue
            total = sum(e.dur for e in evs)
            if total == bar or (edge and total < bar):
                new_groups[(staff, voice)] = evs          # right, or a pickup / short last bar
                continue
            fit = _fit_any(evs, bar, beat)
            if fit is not None and fit[0] <= MAX_REPAIR:
                keep = []
                for ev, v in zip(evs, fit[1]):
                    if v == 0:
                        continue
                    if v != ev.dur or (v < 0 and not ev.triplet):
                        _apply_length(ev, v)
                    keep.append(ev)
                new_groups[(staff, voice)] = keep
                fixed += 1
            elif total < bar:
                new_groups[(staff, voice)] = _pad(evs, bar, staff, voice)
                padded += 1
            else:
                new_groups[(staff, voice)] = evs          # too much to repair: every note kept
                left += 1
        for evs in new_groups.values():
            for ev in evs:
                if ev.rest:
                    _tidy_rest(ev, bar)
        _rewrite_measure(m, new_groups)
    return fixed, padded, left


def _key_consistency(measures) -> int:
    """oemer reads the key signature again on every system, and now and then one
    system's comes out one sharp or flat off (5 flats among 4). A key that differs from
    the one most of the bars are in by a single accidental, for fewer bars, is that
    misreading (R-ACC-9): it goes back, and its notes that took the extra (or missing)
    accidental from it are given the key's own. Returns how many bars were set right."""
    spans: list[list] = []                        # [fifths, first bar, [key elements]]
    for k, m in enumerate(measures):
        for key in m.iter("key"):
            try:
                f = int(key.findtext("fifths"))
            except (TypeError, ValueError):
                continue
            if spans and spans[-1][0] == f:
                spans[-1][2].append(key)
            else:
                spans.append([f, k, [key]])
    if len(spans) < 2:
        return 0
    bars: dict[int, int] = {}
    for i, (f, start, _) in enumerate(spans):
        end = spans[i + 1][1] if i + 1 < len(spans) else len(measures)
        bars[f] = bars.get(f, 0) + end - start
    main = max(bars, key=lambda f: (bars[f], f == spans[0][0]))
    fixed = 0
    good, bad_map = _key_alters(main), None
    for i, (f, start, keys) in enumerate(spans):
        if f == main or abs(f - main) != 1 or bars[f] >= bars[main]:
            continue
        bad_map = _key_alters(f)
        end = spans[i + 1][1] if i + 1 < len(spans) else len(measures)
        for key in keys:
            key.find("fifths").text = str(main)
        for m in measures[start:end]:
            for p in m.iter("pitch"):
                step, alter = p.findtext("step"), p.find("alter")
                if alter is None or bad_map.get(step, 0) == good.get(step, 0):
                    continue
                if int(alter.text or 0) == bad_map.get(step, 0):
                    alter.text = str(good.get(step, 0))
            fixed += 1
    return fixed


def _tidy_rest(ev: _Event, bar: int) -> None:
    """oemer writes every rest as a whole-bar rest (measure="yes") with no type, which
    readers stretch to the whole bar: only a rest that fills the bar keeps that; the
    others get their written value (R-REST-1)."""
    el = ev.els[0]
    r = el.find("rest")
    if ev.dur == bar:
        r.set("measure", "yes")
        return
    if "measure" in r.attrib:
        del r.attrib["measure"]
    if el.find("type") is None:
        spelled = _spell_length(ev.dur)
        if spelled:
            t_el = ET.Element("type")
            t_el.text = spelled[0]
            at = list(el).index(el.find("duration")) + 1
            el.insert(at, t_el)
            for _ in range(spelled[1]):
                el.insert(at + 1, ET.Element("dot"))


def _rewrite_measure(m, groups: dict[tuple[str, str], list[_Event]]) -> None:
    """The measure's notes again: each staff's voices one after another, a backup
    between them (no more note-by-note interleaving)."""
    head = [el for el in m if el.tag not in ("note", "backup", "forward", "barline")]
    barlines = [el for el in m if el.tag == "barline"]
    for el in list(m):
        m.remove(el)
    for el in head:
        if el.tag != "barline":
            m.append(el)
    left = [b for b in barlines if b.get("location") == "left"]
    right = [b for b in barlines if b.get("location") != "left"]
    for b in left:
        m.insert(0, b)
    blocks = [(key, evs) for key, evs in sorted(groups.items()) if evs]
    for i, (key, evs) in enumerate(blocks):
        total = 0
        for ev in evs:
            for el in ev.els:
                m.append(el)
            total += ev.dur
        if i + 1 < len(blocks) and total:
            back = ET.SubElement(m, "backup")
            ET.SubElement(back, "duration").text = str(total)
    for b in right:
        m.append(b)
