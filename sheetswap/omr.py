"""Sheet music from a picture: screenshot, phone photo or PDF -> MusicXML.

"also make it so that you can input like screenshots as well": a picture of
piano or violin music is read by optical music recognition (OMR) with oemer,
a neural OMR engine (two image-segmentation networks plus rules that turn the
symbols into notes), run on the CPU. The MusicXML it writes is then read like
any other score (sheetswap.score_input).

    image_to_musicxml(image, out_dir)       one picture or PDF -> out_dir/omr.musicxml
    images_to_musicxml(images, out_dir)     several, in order, joined into one score

Before oemer sees a page it is cleaned up so it looks like what oemer was
trained on (`prepare`):
- grey, with transparency on white and dark-mode pages turned back to black
  on white; uneven light (phone photos) evened out;
- turned straight when the staff lines are tilted (up to 5 degrees);
- cropped to the staves: browser bars, side panels, titles and the empty rest
  of the page go;
- scaled so the staff lines are TARGET_SPACING pixels apart. oemer itself
  scales every picture to about 3.7 million pixels whatever is on it, which
  blows a one-line screenshot up and shrinks a dense page, so that step is
  replaced by this one.
A page with no staff (five evenly spaced long lines) is refused before the
slow part. Errors a user can fix come back as ValueError with a plain message.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request
import warnings
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

NO_MUSIC = "Couldn't read any music in that image — try a sharper screenshot of just the music."
NOT_A_PICTURE = "That file isn't a picture or PDF this can read — use PNG, JPG or PDF."
NO_MODELS = ("The music reader's model files are missing and couldn't be downloaded "
             "(it needs the internet once, about 110 MB).")

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
MAX_PIXELS = 6_000_000    # cap on a prepared page (time grows with the area)
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


def prepare(image: Path | np.ndarray, out_png: Path | None = None,
            target: float = TARGET_SPACING) -> dict:
    """Clean a page up for oemer; write it to `out_png`.

    Returns {"spacing": staff spacing found (px, before scaling), "staves":
    number of staves, "angle": degrees turned, "scale": resize factor,
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

    scale = target / spacing
    ch, cw = gray.shape
    if ch * cw * scale * scale > MAX_PIXELS:
        scale = (MAX_PIXELS / (ch * cw)) ** 0.5
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
    return {"spacing": spacing, "staves": len(staves), "angle": angle, "scale": scale,
            "size": (gray.shape[1], gray.shape[0]), "image": gray}


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


def join_musicxml(paths: list[Path], out_path: Path, title: str | None = None) -> Path:
    """oemer's pages -> one MusicXML score, in order, bar numbers running on.

    Works on the XML itself, so every note, voice and step back stays as oemer
    wrote it (rebuilding the score in music21 shifts overlapping notes). Also:
    - a time signature guessed from the bar lengths (`guess_meter`; oemer
      never writes one, and without one readers assume 4/4);
    - oemer's made-up tempo (90) and its "Transcribed by Oemer" composer
      are dropped, the title set (or left out);
    - a one-staff score is named "Melody", not oemer's fixed "Piano", so it
      is read as a single line (violin, flute, voice), not a keyboard part;
    - a page with fewer staves than the others simply has nothing on them.
    """
    import xml.etree.ElementTree as ET

    trees = [ET.parse(p) for p in paths]
    roots = [t.getroot() for t in trees]
    base = roots[0]
    part = base.find("part")
    n_staves = max(_staves(r) for r in roots)

    for el in list(part):                        # <sound tempo="90"/> outside any bar
        if el.tag != "measure":
            part.remove(el)
    number = 0
    for k, root in enumerate(roots):
        page = root.find("part")
        if n_staves > 1 and _staves(root) == 1:      # a one-staff page: its notes are staff 1
            for el in page.iter():
                if el.tag in ("note", "forward") and el.find("staff") is None:
                    later = [i for i, c in enumerate(el)
                             if c.tag in ("beam", "notations", "lyric", "play", "listen")]
                    staff = ET.Element("staff")
                    staff.text = "1"
                    el.insert(later[0] if later else len(el), staff)
        for j, m in enumerate(page.findall("measure")):
            number += 1
            m.set("number", str(number))
            for attrs in m.findall("attributes"):   # staves: set once, below, for the score
                for st in attrs.findall("staves"):
                    attrs.remove(st)
            for snd in m.findall("sound"):
                m.remove(snd)
            if k > 0:
                if j == 0 and m.find("print") is None:
                    m.insert(0, ET.Element("print", {"new-page": "yes"}))
                part.append(m)

    first = part.find("measure")
    attrs = first.find("attributes")
    if attrs is None:
        attrs = ET.Element("attributes")
        first.insert(0, attrs)
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
    if base.find(".//time") is None:
        lengths = []
        for p in paths:
            try:
                lengths += _bar_lengths(Path(p))
            except Exception:                    # music21 raises many kinds
                pass
        guess = guess_meter(lengths)
        if guess:
            beats, beat_type = guess.split("/")
            time_el = ET.Element("time")
            ET.SubElement(time_el, "beats").text = beats
            ET.SubElement(time_el, "beat-type").text = beat_type
            place(time_el)

    for el in list(base):
        if el.tag in ("work", "movement-title", "identification"):
            base.remove(el)
    if title:
        work = ET.Element("work")
        ET.SubElement(work, "work-title").text = title
        base.insert(0, work)
        mt = ET.Element("movement-title")
        mt.text = title
        base.insert(1, mt)
    if n_staves == 1:
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


def images_to_musicxml(images: list[Path], out_dir: Path,
                       name: str = "omr.musicxml", title: str | None = None) -> Path:
    """Read several screenshots / photos / PDFs in order and join them into
    one MusicXML score at out_dir/name. Work files (the cleaned-up pages and
    what oemer read from each) stay in out_dir/omr/.

    ValueError (plain words, safe to show) when a picture has no readable
    music; RuntimeError when the models can't be fetched.
    """
    images = [Path(p) for p in images]
    if not images:
        raise ValueError(NO_MUSIC)
    out_dir = Path(out_dir)
    work = out_dir / "omr"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    pages = _expand(images, work)
    many = len(images) > 1
    read: list[Path] = []
    for k, (page, label, must) in enumerate(pages, 1):
        png = work / f"page{k:02d}.png"
        try:
            info = prepare(page, png)
        except ValueError as e:
            if must and str(e) == NOT_A_PICTURE:
                raise
            if must:
                raise ValueError(_no_music(label, many)) from None
            log.info("omr: no staves on %s, skipped", label)
            continue
        log.info("omr: %s: %d staves, spacing %.1f px, turned %.2f deg, scaled x%.2f -> %dx%d",
                 label, info["staves"], info["spacing"], info["angle"], info["scale"],
                 *info["size"])
        xml = work / f"page{k:02d}.musicxml"
        t0 = time.time()
        with _LOCK:
            _ensure_models()
            try:
                _run_oemer(png, xml)
                fix_oemer_xml(xml)
            except Exception as e:                  # oemer fails in many ways on bad input
                log.warning("omr: oemer failed on %s: %r", label, e)
                xml.unlink(missing_ok=True)
        log.info("omr: %s read in %.1f s", label, time.time() - t0)
        if not xml.exists() or _count_notes(xml) == 0:
            if must:
                raise ValueError(_no_music(label, many))
            continue
        read.append(xml)
    if not read:
        raise ValueError(NO_MUSIC)
    if title is None:
        title = title_from_name(images[0])
    out = out_dir / name
    try:
        return join_musicxml(read, out, title=title)
    except Exception as e:                          # music21 couldn't rebuild the score
        if len(read) == 1:
            log.warning("omr: kept oemer's own MusicXML (%r)", e)
            shutil.copy(read[0], out)
            return out
        raise ValueError(NO_MUSIC) from e


def image_to_musicxml(image: Path, out_dir: Path, name: str = "omr.musicxml") -> Path:
    """One screenshot, photo or PDF (all its pages) -> out_dir/name (MusicXML)."""
    return images_to_musicxml([Path(image)], out_dir, name=name)


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
