#!/usr/bin/env python3
# vim: expandtab shiftwidth=4 tabstop=4
#
# RaceDB QLLabels Python Script
#
# Usage
# In the RaceDB System Info Edit screen:
#
#   Cmd used to print Bib Tag (parameter is PDF file)
#
#       [  ssh racedb@qlabels.local  QLLABELS.py $1 ]
# 
# This script will convert the PDF file to Brother Raster file(s) and 
# send the Raster file(s) to a Brother QL style label printer or to 
# the qlmuxd printer spooler.
#
# N.b. the file name is provided as a parameter, the PDF data is 
# provided on stdin.
# 
# Sending the files directly to the printers on port 9100 works, but 
# only when # there is only a single person using RaceDB. Multiple prints 
# to a QL printer will result in over lapping labels.
#
# The qlmuxd program manages pools of QL printers and will spool
# the data allowing multiple people to print to them, with support
# for different printers for the different antennas and fall-over
# support if (when) the printers are not available (typically when
# they run out of labels. The qlmuxd program takes the same raster
# file data that would be sent to the printers on port 9100, but
# uses ports 910N to allow us to specify which pool of printers
# to use.
#
# This script will get labels printed (either directly or via qlmuxd) 
# far faster than CUPS using the standard Brother QL support files.
#
# The argument provided is the file name which contains information about what is to 
# be printed. E.g.:
#
#       230489203498023809_bib-356_port-8000_antenna-2_type-Frame.pdf
#
# "type" is one of Frame, Body, Shoulder or Emergency.
# "port" is the RaceDB server port.
# "antenna" is the antenna of the user.
#
# The combination of server port and antenna allows different printers to 
# be used for different registration stations. The server port refers to
# the TCP port that the server responds to, e.g. 8000 or 8001 etc.
#

__version__ = "1.0.3"

import sys
import os
import socket
import traceback

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import xml.etree.ElementTree as ET
import subprocess

from pdf2image import convert_from_bytes

import argparse
import datetime

from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageFilter

# brother_ql2 is a forked version of brother_ql that is maintained
# It supports the Image.LANCZOS resampling filter required in newer versions of Pillow.
#
# Available from https://github.com/matmair/brother_ql2
#
from brother_ql.conversion import convert
from brother_ql.backends.helpers import send
from brother_ql.raster import BrotherQLRaster
from jaraco.docker import is_docker

getTimeNow = datetime.datetime.now

def usage(s):
    log('Usage: QLLABELS.py [--save_png output-prefix] 130489203498023809_bib-719_port-8000_antenna-1_type-Frame.pdf')
    log('       %s' % (s))
    exit(1)

def log(s):
        print('%s %s' % (getTimeNow().strftime('%H:%M:%S'), s.rstrip()), file=sys.stderr)

def parse_cli_args(argv: List[str]) -> tuple[str, Optional[str], Optional[str], bool, Optional[str]]:
    parser = argparse.ArgumentParser( prog='QLLABELS.py', description='Convert RaceDB label PDFs into Brother raster instructions.')
    parser.add_argument( '--save-png', '--save_png', action='store_true', help='saved rendered PNG',)
    parser.add_argument( '--save-raster', '--save_raster', action='store_true', help='save Brother raster output',)
    parser.add_argument( '--dpi-600', '--dpi_600', dest='dpi_600', action='store_true', 
                        help='render at 600 dpi when the target label supports it',)
    parser.add_argument( '--hostname', dest='hostname', help='override detected hostname',)
    parser.add_argument( '--labelsize', dest='labelsize', help='override detected label size (e.g. 62x100, 102x152)',)
    parser.add_argument( 'pdf_path', help='label PDF produced by RaceDB (e.g. *_type-Frame.pdf)')
    parser.add_argument( '--no-print', action='store_true', help='do not forward to host for printing',)

    args = parser.parse_args(argv)
    print(
        'save_png: %s save_raster: %s labelsize: %s'
        % (args.save_png, args.save_raster, args.labelsize),
        file=sys.stderr,
    )
    return args.pdf_path, args.save_png, args.save_raster, args.dpi_600, args.labelsize, args.hostname, args.no_print


Sizes = {
    "Tag": "small",
    "Frame": "small",
    "Shoulder": "small",
    "Emergency": "small",
    "Body": "large",
    "bib": "large",
  }

# XXX
# Need to refactor this.
# We get an indication of what size of label to print from the type:
#   "type" is one of Frame, Body, Shoulder or Emergency.
# We get an indication of what pool of printers to use from the antenna port:
#   [012] - first set (left) printers
#   [34] - second set (right) printers

# The size of the label is one of "small" or "large":
#   small = 62, 62x100
#   large = 102, 103, 104, 102x152, 103x164    
#
# N.b. 103 and 103x164 actually have media width of 104mm
# 
    #Label("62",     ( 62,   0), FormFactor.ENDLESS,       ( 732,    0), ( 696,    0),  12 , feed_margin=35),
    #Label("62x100", ( 62, 100), FormFactor.DIE_CUT,       ( 732, 1179), ( 696, 1109),  12 ),
    #Label("103",    (104,   0), FormFactor.ENDLESS,       (1224,    0), (1200,    0),  12 , feed_margin=35, restricted_to_models=['QL-1100', 'QL-1110NWB']),
    #Label("104",    (104,   0), FormFactor.ENDLESS,       (1227,    0), (1200,    0),  -8 , feed_margin=35, restricted_to_models=['QL-1050', 'QL-1060N', 'QL-1100', 'QL-1110NWB', 
    #Label("102x152",(102, 153), FormFactor.DIE_CUT,       (1200, 1804), (1164, 1660),  12 , restricted_to_models=['QL-1050', 'QL-1060N', 'QL-1100', 'QL-1110NWB', 'QL-1115NWB']),
    #Label("103x164",(104, 164), FormFactor.DIE_CUT,       (1224, 1941), (1200, 1822),  12 , restricted_to_models=['QL-1100', 'QL-1110NWB']),


Pools = {
    "8000-0": { "small": "small1", "large": "large1"},
    "8000-1": { "small": "small1", "large": "large1"},
    "8000-2": { "small": "small1", "large": "large1"},
    "8000-3": { "small": "small2", "large": "large1"},
    "8000-4": { "small": "small2", "large": "large1"},
}
Printers = {
    "small1" : { "port":9101, "model":"QL-710W",  "labelsize": "62x100"},
    "small2" : { "port":9102, "model":"QL-710W",  "labelsize": "62x100"},
    "large1":  { "port":9103, "model":"QL-1060N", "labelsize": "102x152"},
    "large2":  { "port":9104, "model":"QL-1060N", "labelsize": "102x152"},
} 
      
IMAGESIZE_300 = {
    '62': (1109, 696),
    '62x100': (1109, 696),
    '102': (1660, 1164),
    '102x152': (1660, 1164),
    '103': (1822, 1200),
    '104': (1822, 1200),
    '103x164': (1822, 1200),
}

IMAGESIZE_600 = {
    '62': (2218, 1392),
    '62x100': (2218, 1392),
    '102': (3320, 2328),
    '102x152': (3320, 2328),
}

LABEL_DPI = 300
MARGIN_INCH = 0.00
VERTICAL_TEXT_GAP_MULTIPLIER = 2  # top + gap + bottom margins around rotated text strip
VERTICAL_TEXT_STRIP_MAX_INCH = 0.06
VERTICAL_TEXT_HEIGHT_SCALE = 2.6
BIB_HORIZONTAL_OFFSET_INCH = 0.125
BODY_VERTICAL_STRIP_MULTIPLIER = 2.5
BODY_VERTICAL_MARGIN_INCH = 0.02
RESULTS_FOOTER_TEXT = 'results.wimsey.co'
VERTICAL_EVENT_SHIFT_MM = 0.0
VERTICAL_PARTICIPANT_SHIFT_MM = 2.0

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FONT_DIR = PROJECT_ROOT / 'fonts'
DIN_ENG_FONT = FONT_DIR / 'TGL_0-1451Eng.ttf'

try:
    import fitz
except ImportError:  # pragma: no cover - optional dependency
    fitz = None  # type: ignore
    print('Warning: PyMuPDF not installed, falling back to pdftotext for PDF text extraction', file=sys.stderr)

try:
    RESAMPLING_LANCZOS = Image.Resampling.LANCZOS
except AttributeError:  # Pillow < 9
    RESAMPLING_LANCZOS = Image.LANCZOS

FONT_PATHS = {
    'bib': (
        str(DIN_ENG_FONT),
        str(FONT_DIR / 'RobotoCondensed-Bold.ttf'),
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
        '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
        '/usr/share/fonts/truetype/freefont/FreeSansBold.ttf',
        '/usr/share/fonts/ttf-dejavu/DejaVuSans-Bold.ttf',
        '/usr/share/fonts/TTF/DejaVuSans-Bold.ttf',
        '/usr/share/fonts/ttf-freefont/FreeSansBold.ttf',
    ),
    'bold': (
        str(FONT_DIR / 'RobotoCondensed-Bold.ttf'),
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
        '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
        '/usr/share/fonts/truetype/freefont/FreeSansBold.ttf',
        '/usr/share/fonts/ttf-dejavu/DejaVuSans-Bold.ttf',
        '/usr/share/fonts/TTF/DejaVuSans-Bold.ttf',
        '/usr/share/fonts/ttf-freefont/FreeSansBold.ttf',
    ),
    'regular': (
        str(FONT_DIR / 'RobotoCondensed-Regular.ttf'),
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
        '/usr/share/fonts/truetype/freefont/FreeSans.ttf',
        '/usr/share/fonts/ttf-dejavu/DejaVuSans.ttf',
        '/usr/share/fonts/TTF/DejaVuSans.ttf',
        '/usr/share/fonts/ttf-freefont/FreeSans.ttf',
    ),
}

IGNORED_TEXTS = {'crossmgr'}


class LabelExtractionError(Exception):
    pass


@dataclass
class Word:
    text: str
    x_min: float
    y_min: float
    x_max: float
    y_max: float
    page: int
    page_width: float


@dataclass
class Line:
    text: str
    x_min: float
    y_min: float
    x_max: float
    y_max: float
    page: int
    page_width: float

    @property
    def height(self) -> float:
        return self.y_max - self.y_min

    @property
    def center_x(self) -> float:
        return (self.x_min + self.x_max) / 2.0

    @property
    def normalized_text(self) -> str:
        return self.text.strip().lower()


@dataclass
class LabelFields:
    bib: str
    event: str
    participant: str


def _find_font_path(weight: str) -> Optional[str]:
    for candidate in FONT_PATHS.get(weight, ()):  # pragma: no branch - tiny loop
        if os.path.isfile(candidate):
            return candidate
    return None


_FONT_CACHE = {}


def _load_font(size: int, weight: str = 'regular') -> ImageFont.FreeTypeFont:
    cache_key = (weight, size)
    if cache_key in _FONT_CACHE:
        return _FONT_CACHE[cache_key]
    if weight == 'bib' and DIN_ENG_FONT.is_file():
        try:
            font = ImageFont.truetype(str(DIN_ENG_FONT), size=size)
            _FONT_CACHE[cache_key] = font
            return font
        except OSError:
            log(f'Warning: failed to load DIN Engschrift font at {DIN_ENG_FONT}')
    candidates = FONT_PATHS.get(weight, ())
    font: Optional[ImageFont.FreeTypeFont] = None
    for path in candidates:
        if not path or not os.path.isfile(path):
            continue
        try:
            font = ImageFont.truetype(path, size=size)
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()
    _FONT_CACHE[cache_key] = font
    return font


def _text_bbox(text: str, font: ImageFont.ImageFont) -> List[int]:
    dummy = Image.new('L', (1, 1), color=255)
    drawer = ImageDraw.Draw(dummy)
    return list(drawer.textbbox((0, 0), text, font=font))


def _fit_vertical_font(text: str, strip_width: int, max_vertical_extent: int, weight: str = 'regular') -> ImageFont.ImageFont:
    if not text:
        return _load_font(10, weight)
    font_path = _find_font_path(weight)
    if not font_path:
        return ImageFont.load_default()
    low, high = 1, max(1, strip_width)
    best_font = None
    while low <= high:
        mid = (low + high) // 2
        font = _load_font(mid, weight)
        bbox = _text_bbox(text, font)
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        if height <= strip_width and width <= max_vertical_extent:
            best_font = font
            low = mid + 1
        else:
            high = mid - 1
    return best_font or _load_font(max(1, high), weight)


def _extract_pdf_words(pdf_bytes: bytes) -> List[Tuple[int, float, List[Word]]]:
    if fitz is None:  # pragma: no cover - dependency check
        return _extract_pdftotext_words(pdf_bytes)
    try:
        doc = fitz.open(stream=pdf_bytes, filetype='pdf')
    except Exception:
        return _extract_pdftotext_words(pdf_bytes)

    pages: List[Tuple[int, float, List[Word]]] = []
    try:
        for page_index in range(len(doc)):
            page = doc[page_index]
            width = float(page.rect.width)
            words: List[Word] = []
            for entry in page.get_text('words'):
                if len(entry) < 5:
                    continue
                x_min, y_min, x_max, y_max, text = entry[:5]
                text = (text or '').strip()
                if not text:
                    continue
                words.append(
                    Word(
                        text=text,
                        x_min=float(x_min),
                        y_min=float(y_min),
                        x_max=float(x_max),
                        y_max=float(y_max),
                        page=page_index,
                        page_width=width,
                    )
                )
            pages.append((page_index, width, words))
    finally:
        doc.close()

    if any(words for _, _, words in pages):
        return pages

    return _extract_pdftotext_words(pdf_bytes)


def _run_pdftotext_bbox(pdf_bytes: bytes) -> bytes:
    try:
        proc = subprocess.run(
            ['pdftotext', '-bbox', '-', '-'],
            input=pdf_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
    except FileNotFoundError as exc:  # pragma: no cover - environment dependent
        raise LabelExtractionError('pdftotext command not found') from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode('utf-8', errors='ignore').strip()
        raise LabelExtractionError(f'pdftotext failed: {stderr or exc.returncode}') from exc
    return proc.stdout


def _strip_namespace(tag: str) -> str:
    if '}' in tag:
        return tag.split('}', 1)[1]
    return tag


def _extract_pdftotext_words(pdf_bytes: bytes) -> List[Tuple[int, float, List[Word]]]:
    raw_xml = _run_pdftotext_bbox(pdf_bytes)
    try:
        root = ET.fromstring(raw_xml)
    except ET.ParseError as exc:
        raise LabelExtractionError('Unable to parse pdftotext output') from exc

    doc_elem = None
    for elem in root.iter():
        if _strip_namespace(elem.tag) == 'doc':
            doc_elem = elem
            break
    if doc_elem is None:
        raise LabelExtractionError('No document data found in pdftotext output')

    pages: List[Tuple[int, float, List[Word]]] = []
    for page_index, page_elem in enumerate(doc_elem.iter()):
        if _strip_namespace(page_elem.tag) != 'page':
            continue
        width = float(page_elem.attrib.get('width', '0'))
        words: List[Word] = []
        for word_elem in page_elem.iter():
            if _strip_namespace(word_elem.tag) != 'word':
                continue
            text = (word_elem.text or '').strip()
            if not text:
                continue
            try:
                x_min = float(word_elem.attrib['xMin'])
                y_min = float(word_elem.attrib['yMin'])
                x_max = float(word_elem.attrib['xMax'])
                y_max = float(word_elem.attrib['yMax'])
            except KeyError as exc:
                raise LabelExtractionError('Incomplete bounding box data') from exc
            words.append(
                Word(
                    text=text,
                    x_min=x_min,
                    y_min=y_min,
                    x_max=x_max,
                    y_max=y_max,
                    page=page_index,
                    page_width=width,
                )
            )
        pages.append((page_index, width, words))
    return pages


def _parse_lines(pdf_bytes: bytes) -> List[Line]:
    pages = _extract_pdf_words(pdf_bytes)

    lines: List[Line] = []
    for page_index, width, words in pages:
        if not words:
            continue
        words.sort(key=lambda w: (w.y_min, w.x_min))
        current_line: List[Word] = []
        current_y: Optional[float] = None
        y_tolerance = 1.5
        for word in words:
            if current_line and current_y is not None and abs(word.y_min - current_y) > y_tolerance:
                lines.extend(_words_to_lines(current_line, page_index, width))
                current_line = []
                current_y = None
            current_line.append(word)
            if current_y is None:
                current_y = word.y_min
            else:
                current_y = (current_y * (len(current_line) - 1) + word.y_min) / len(current_line)
        if current_line:
            lines.extend(_words_to_lines(current_line, page_index, width))
    if not lines:
        raise LabelExtractionError('No text lines found in PDF')
    return lines


def _words_to_lines(words: List[Word], page_index: int, page_width: float) -> List[Line]:
    if not words:
        return []
    words_sorted = sorted(words, key=lambda w: w.x_min)
    segments: List[List[Word]] = []
    current_segment: List[Word] = [words_sorted[0]]
    gap_threshold = 10.0
    for prev, word in zip(words_sorted, words_sorted[1:]):
        if word.x_min - prev.x_max > gap_threshold:
            segments.append(current_segment)
            current_segment = [word]
        else:
            current_segment.append(word)
    segments.append(current_segment)

    lines: List[Line] = []
    for segment in segments:
        text = ' '.join(word.text for word in segment)
        lines.append(
            Line(
                text=text,
                x_min=min(word.x_min for word in segment),
                y_min=min(word.y_min for word in segment),
                x_max=max(word.x_max for word in segment),
                y_max=max(word.y_max for word in segment),
                page=page_index,
                page_width=page_width,
            )
        )
    return lines


def _has_alpha(text: str) -> bool:
    return any(ch.isalpha() for ch in text)


def _select_bib_line(lines: Iterable[Line]) -> Line:
    digit_lines = [line for line in lines if line.text.replace(' ', '').isdigit()]
    if not digit_lines:
        raise LabelExtractionError('Bib number not found in PDF content')
    return max(digit_lines, key=lambda line: line.height)


def _select_event_line(lines: Iterable[Line]) -> Line:
    candidates = [
        line
        for line in lines
        if _has_alpha(line.text)
        and len(line.text.strip()) > 1
        and line.normalized_text not in IGNORED_TEXTS
    ]
    if not candidates:
        candidates = [
            line
            for line in lines
            if _has_alpha(line.text)
            and len(line.text.strip()) > 1
        ]
    if not candidates:
        log('Event extraction fallback failed; available lines: %s' % [line.text for line in lines])
        raise LabelExtractionError('Event name not found in PDF content')
    left_candidates = [line for line in candidates if line.center_x <= line.page_width / 2]
    if left_candidates:
        candidates = left_candidates
    return min(candidates, key=lambda line: (line.y_min, line.center_x))


def _select_participant_line(lines: Iterable[Line], preferred_page: int, event_text: str) -> Line:
    candidates = [
        line
        for line in lines
        if _has_alpha(line.text)
        and len(line.text.strip()) > 1
        and line.normalized_text != event_text.strip().lower()
        and line.normalized_text not in IGNORED_TEXTS
    ]
    if not candidates:
        candidates = [
            line
            for line in lines
            if _has_alpha(line.text)
            and len(line.text.strip()) > 1
            and line.normalized_text != event_text.strip().lower()
        ]
    if not candidates:
        log('Participant extraction fallback failed; available lines: %s' % [line.text for line in lines])
        raise LabelExtractionError('Participant name not found in PDF content')
    same_page = [line for line in candidates if line.page == preferred_page]
    if same_page:
        candidates = same_page
    right_side = [line for line in candidates if line.center_x >= line.page_width / 2]
    if right_side:
        candidates = right_side
    return max(candidates, key=lambda line: line.y_min)


def extract_label_fields(pdf_bytes: bytes) -> LabelFields:
    lines = _parse_lines(pdf_bytes)
    bib_line = _select_bib_line(lines)
    event_line = _select_event_line(lines)
    participant_line = _select_participant_line(lines, event_line.page, event_line.text)
    return LabelFields(
        bib=bib_line.text.replace(' ', ''),
        event=event_line.text.strip(),
        participant=participant_line.text.strip(),
    )


def _create_rotated_text_image(text: str, font: ImageFont.ImageFont) -> Image.Image:
    if not text:
        return Image.new('L', (1, 1), color=255)
    bbox = _text_bbox(text, font)
    width = max(1, bbox[2] - bbox[0])
    height = max(1, bbox[3] - bbox[1])
    text_img = Image.new('L', (width, height), color=255)
    drawer = ImageDraw.Draw(text_img)
    drawer.text((-bbox[0], -bbox[1]), text, font=font, fill=0)
    return text_img.rotate(90, expand=True)


def _create_rotated_text_image_right(text: str, font: ImageFont.ImageFont) -> Image.Image:
    base = _create_rotated_text_image(text, font)
    return base.rotate(180, expand=True)


def _binarize_text_image(img: Image.Image, threshold: int = 200) -> Image.Image:
    if img.mode != 'L':
        img = img.convert('L')
    binary = img.point(lambda px: 0 if px < threshold else 255, mode='1')
    result = binary.convert('L')
    if result.width > 1 and result.height > 1:
        result = result.filter(ImageFilter.MinFilter(3))
    return result


def _paste_rotated_text(base: Image.Image, img: Image.Image, x_offset: int, y_offset: int) -> None:
    if img.width == 0 or img.height == 0:
        return
    paste_img = img
    x_off = x_offset
    y_off = y_offset

    if x_off < 0:
        crop_left = min(-x_off, paste_img.width)
        paste_img = paste_img.crop((crop_left, 0, paste_img.width, paste_img.height))
        x_off = 0
    if y_off < 0:
        crop_top = min(-y_off, paste_img.height)
        paste_img = paste_img.crop((0, crop_top, paste_img.width, paste_img.height))
        y_off = 0

    if paste_img.width == 0 or paste_img.height == 0:
        return

    max_x = base.width - paste_img.width
    max_y = base.height - paste_img.height
    if max_x < 0 or max_y < 0:
        return

    x_off = min(x_off, max_x)
    y_off = min(y_off, max_y)

    mask = ImageOps.invert(paste_img)
    base.paste(paste_img, (x_off, y_off), mask)


def _scale_vertical_text(img: Image.Image, factor: float, max_width: int, max_height: int) -> Image.Image:
    if img.width == 0 or img.height == 0 or factor <= 0:
        return img
    target_width = min(max_width, max(1, int(round(img.width * factor))))
    target_height = min(max_height, max(1, int(round(img.height * factor))))
    if target_width == img.width and target_height == img.height:
        return img
    return ImageOps.contain(img, (target_width, target_height), RESAMPLING_LANCZOS)


def _fit_horizontal_font(text: str, max_width: int, max_height: int, weight: str = 'regular') -> ImageFont.ImageFont:
    if not text:
        return _load_font(max(10, max_height // 2), weight)
    low, high = 1, max(1, max_height * 3)
    best = _load_font(1, weight)
    while low <= high:
        mid = (low + high) // 2
        font = _load_font(mid, weight)
        bbox = _text_bbox(text, font)
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        if width <= max_width and height <= max_height:
            best = font
            low = mid + 1
        else:
            high = mid - 1
    return best


def _render_bib_block(text: str, target_width: int, target_height: int) -> Image.Image:
    if not text:
        return Image.new('L', (target_width, target_height), color=255)
    font_size = max(target_height * 2, 100)
    font = _load_font(font_size, 'bib')
    bbox = _text_bbox(text, font)
    width = max(1, bbox[2] - bbox[0])
    height = max(1, bbox[3] - bbox[1])
    base = Image.new('L', (width, height), color=0)
    drawer = ImageDraw.Draw(base)
    drawer.text((-bbox[0], -bbox[1]), text, font=font, fill=255)
    contained = ImageOps.contain(base, (max(1, target_width), max(1, target_height)), RESAMPLING_LANCZOS)
    if contained.width < target_width:
        contained = contained.resize((target_width, max(1, contained.height)), RESAMPLING_LANCZOS)
    if contained.height < target_height:
        contained = contained.resize((max(1, contained.width), target_height), RESAMPLING_LANCZOS)
    inverted = ImageOps.invert(contained)
    content_bbox = inverted.getbbox()
    if content_bbox:
        cropped = inverted.crop(content_bbox)
    else:
        cropped = inverted
    result = Image.new('L', (target_width, target_height), color=255)
    offset_x = (target_width - cropped.width) // 2
    offset_y = (target_height - cropped.height) // 2
    result.paste(cropped, (offset_x, offset_y))
    return result


def render_frame_label(fields: LabelFields, target_size: tuple[int, int], vertical_side: str = 'left') -> Image.Image:
    width, height = target_size
    margin_px = max(0, int(round(LABEL_DPI * MARGIN_INCH)))
    offset_px = max(0, int(round(LABEL_DPI * BIB_HORIZONTAL_OFFSET_INCH)))
    image = Image.new('L', (width, height), color=255)

    base_strip_width = max(1, int(round(LABEL_DPI * VERTICAL_TEXT_STRIP_MAX_INCH)))
    strip_width = max(1, int(round(base_strip_width * 1.7)))
    event_shift_px = int(round(LABEL_DPI * (VERTICAL_EVENT_SHIFT_MM / 25.4)))
    participant_shift_px = int(round(LABEL_DPI * (VERTICAL_PARTICIPANT_SHIFT_MM / 25.4)))
    available_vertical = max(height - VERTICAL_TEXT_GAP_MULTIPLIER * margin_px, height // 2)
    per_text_vertical = max(1, available_vertical // 2)

    #event_font = _fit_vertical_font(fields.event, strip_width, per_text_vertical)
    event_font = _fit_vertical_font(RESULTS_FOOTER_TEXT, strip_width, per_text_vertical)
    participant_font = _fit_vertical_font(fields.participant, strip_width, per_text_vertical)

    if vertical_side.lower() == 'right':
        event_img = _binarize_text_image(_create_rotated_text_image_right(RESULTS_FOOTER_TEXT, event_font))
        participant_img = _binarize_text_image(_create_rotated_text_image_right(fields.participant, participant_font))
    else:
        event_img = _binarize_text_image(_create_rotated_text_image(RESULTS_FOOTER_TEXT, event_font))
        participant_img = _binarize_text_image(_create_rotated_text_image(fields.participant, participant_font))

    combined_height = event_img.height + participant_img.height
    max_combined_height = max(1, height - 2 * margin_px)
    if combined_height > 0:
        scale_factor = min(VERTICAL_TEXT_HEIGHT_SCALE, max_combined_height / combined_height)
        if scale_factor > 1:
            event_img = _scale_vertical_text(event_img, scale_factor, strip_width, max_combined_height)
            participant_img = _scale_vertical_text(participant_img, scale_factor, strip_width, max_combined_height)
            combined_height = event_img.height + participant_img.height
        if combined_height > max_combined_height:
            reduction = max_combined_height / combined_height
            event_img = _scale_vertical_text(event_img, reduction, strip_width, max_combined_height)
            participant_img = _scale_vertical_text(participant_img, reduction, strip_width, max_combined_height)

    vertical_band_width = max(strip_width, event_img.width, participant_img.width)

    vertical_side = vertical_side.lower()
    vertical_left = vertical_side != 'right'
    if vertical_left:
        left_margin_px = margin_px + vertical_band_width + offset_px
        right_margin_px = margin_px
        event_x = margin_px
    else:
        left_margin_px = margin_px
        right_margin_px = margin_px + vertical_band_width + offset_px
        event_x = max(margin_px, width - margin_px - event_img.width)

    if left_margin_px + right_margin_px >= width:
        overlap = left_margin_px + right_margin_px - (width - 1)
        if vertical_left:
            left_margin_px = max(0, left_margin_px - overlap)
        else:
            right_margin_px = max(0, right_margin_px - overlap)

    digit_area_width = max(1, width - left_margin_px - right_margin_px)
    digit_area_height = height - (2 * margin_px)
    bib_block = _render_bib_block(fields.bib, digit_area_width, digit_area_height)
    bib_x = min(max(0, width - right_margin_px - bib_block.width), left_margin_px)
    image.paste(bib_block, (bib_x, margin_px))

    if vertical_left:
        event_x = margin_px
    else:
        event_x = max(margin_px, width - margin_px - event_img.width)
    event_y = margin_px - event_shift_px
    max_event_y = height - margin_px - event_img.height
    if max_event_y < event_y:
        event_y = max_event_y
    _paste_rotated_text(image, event_img, event_x, event_y)

    participant_x = event_x if vertical_left else max(margin_px, width - margin_px - participant_img.width)

    min_participant_y = max(margin_px, event_y + event_img.height + margin_px)
    max_participant_y = max(margin_px, height - margin_px - participant_img.height)
    bottom_aligned = max_participant_y >= min_participant_y
    participant_y = max_participant_y
    participant_y -= participant_shift_px
    participant_y = max(margin_px, participant_y)
    if bottom_aligned:
        participant_y = max(min_participant_y, participant_y)
    participant_y = min(participant_y, max_participant_y)
    _paste_rotated_text(image, participant_img, participant_x, participant_y)

    return image


def render_body_label(fields: LabelFields, target_size: tuple[int, int]) -> Image.Image:
    width, height = target_size
    margin_px = max(0, int(round(LABEL_DPI * MARGIN_INCH)))
    offset_px = max(0, int(round(LABEL_DPI * BIB_HORIZONTAL_OFFSET_INCH)))
    vertical_margin_px = max(4, int(round(LABEL_DPI * BODY_VERTICAL_MARGIN_INCH)))
    image = Image.new('L', (width, height), color=255)

    participant_text = fields.participant.strip()
    if not participant_text or participant_text.lower() == 'crossmgr':
        participant_text = fields.event.strip()

    vertical_texts: List[str] = [RESULTS_FOOTER_TEXT]
    if participant_text and participant_text.lower() != RESULTS_FOOTER_TEXT.lower():
        vertical_texts.append(participant_text)

    strip_width = max(1, int(round(LABEL_DPI * VERTICAL_TEXT_STRIP_MAX_INCH * BODY_VERTICAL_STRIP_MULTIPLIER)))
    available_vertical = max(height - 2 * vertical_margin_px, height // 2)
    per_text_vertical = max(1, available_vertical // max(1, len(vertical_texts)))

    text_images: List[Image.Image] = []
    for text_value in vertical_texts:
        font = _fit_vertical_font(text_value, strip_width, per_text_vertical)
        text_images.append(_binarize_text_image(_create_rotated_text_image(text_value, font)))

    combined_height = sum(img.height for img in text_images)
    max_combined_height = max(1, height - 2 * vertical_margin_px)
    if combined_height > 0:
        scale_factor = min(VERTICAL_TEXT_HEIGHT_SCALE, max_combined_height / combined_height)
        if scale_factor > 1:
            text_images = [
                _scale_vertical_text(img, scale_factor, strip_width, max_combined_height)
                for img in text_images
            ]
            combined_height = sum(img.height for img in text_images)
        if combined_height > max_combined_height:
            reduction = max_combined_height / max(1, combined_height)
            text_images = [
                _scale_vertical_text(img, reduction, strip_width, max_combined_height)
                for img in text_images
            ]

    vertical_band_width = max([strip_width] + [img.width for img in text_images]) if text_images else strip_width

    if text_images:
        first_img = text_images[0]
        y_pos = max(vertical_margin_px, (height - first_img.height) // 2) if len(text_images) == 1 else vertical_margin_px
        y_pos = min(y_pos, height - vertical_margin_px - first_img.height)
        _paste_rotated_text(image, first_img, margin_px, y_pos)

        if len(text_images) > 1:
            remaining = text_images[1:]
            previous_bottom = y_pos + first_img.height
            for img in remaining:
                min_y = previous_bottom + vertical_margin_px
                max_y = height - vertical_margin_px - img.height
                if max_y >= min_y:
                    paste_y = max_y
                else:
                    paste_y = max(vertical_margin_px, min_y)
                _paste_rotated_text(image, img, margin_px, paste_y)
                previous_bottom = paste_y + img.height

    left_margin_px = margin_px + vertical_band_width + offset_px
    right_margin_px = margin_px
    digit_area_width = max(1, width - left_margin_px - right_margin_px)
    digit_area_height = max(1, height - 2 * margin_px)
    bib_block = _render_bib_block(fields.bib, digit_area_width, digit_area_height)
    bib_x = max(0, width - right_margin_px - bib_block.width)
    bib_y = margin_px + max(0, (digit_area_height - bib_block.height) // 2)
    bib_y = min(bib_y, height - margin_px - bib_block.height)
    image.paste(bib_block, (bib_x, bib_y))
    return image



def render_label(
    raw_fname: str,
    payload: bytes,
    save_png: Optional[str] = None,
    save_raster: Optional[str] = None,
    dpi_600: bool = False,
    labelsize_override: Optional[str] = None,
) -> tuple[Optional[bytes], str, int]:
    fname = os.path.basename(raw_fname)

    params = {
        key: (int(value) if value.isdigit() else value)
        for key, value in (
            part.split('-')
            for part in os.path.splitext(fname)[0].split('_')[1:]
            if '-' in part
        )
    }
    print('params: %s' % (params))

    try:
        size = Sizes[params['type']]
    except KeyError:
        usage('Do not understand type-%s' % (params.get('type')))

    pool_key = f"{params['port']}-{params['antenna']}"
    try:
        pool = Pools[pool_key]
    except KeyError:
        usage('Do not understand %s' % (pool_key))

    try:
        printer_name = pool[size]
    except KeyError:
        usage('Do not understand printerName %s' % (size))

    try:
        printer = Printers[printer_name]
    except KeyError:
        usage('Cannot find printerName %s' % (printer_name))

    try:
        port = printer['port']
        model = printer['model']
        labelsize = printer['labelsize']
    except KeyError:
        usage('Cannot find one of port, model, labelsize: %s' % (printer))

    if labelsize_override:
        labelsize = labelsize_override
        log(f'Label size overridden via CLI: {labelsize}')

    supports_600 = {'62', '62x100'}
    effective_dpi_600 = dpi_600 and labelsize in supports_600
    if dpi_600 and not effective_dpi_600:
        log(f"dpi_600 requested but not supported for label {labelsize}; falling back to 300 dpi")

    global LABEL_DPI
    LABEL_DPI = 600 if effective_dpi_600 else 300
    imagesize_map = IMAGESIZE_600 if LABEL_DPI == 600 else IMAGESIZE_300

    try:
        label_dimensions = imagesize_map[labelsize]
    except KeyError:
        usage(f'Unknown label size {labelsize}')
    print('port: %s model: %s labelsize: %s label_dimensions: %s LABEL_DPI: %s' % (port, model, labelsize, label_dimensions, LABEL_DPI), file=sys.stderr)

    label_type = params.get('type')
    try:
        fields = extract_label_fields(payload)
    except LabelExtractionError as exc:
        log(f'Field extraction failed ({exc}); reverting to rasterized PDF')
        images = convert_from_bytes(payload, size=label_dimensions, dpi=LABEL_DPI, grayscale=True)
    else:
        if label_type == 'Frame' and labelsize in ('62', '62x100'):
            left_image = render_frame_label(fields, label_dimensions, vertical_side='left')
            right_image = render_frame_label(fields, label_dimensions, vertical_side='right')
            images = [left_image, right_image]
            log(f"Rendered custom Frame label set for bib {fields.bib}")
        elif label_type == 'Body' and labelsize in ('102', '102x152', '103', '104', '103x164'):
            body_image = render_body_label(fields, label_dimensions)
            images = [body_image]
            log(f"Rendered custom Body label for bib {fields.bib}")
        else:
            log(f"Custom layout not defined for type {label_type}; rasterizing PDF")
            images = convert_from_bytes(payload, size=label_dimensions, dpi=LABEL_DPI, grayscale=True)

    if not images:
        usage('No images produced from input PDF')

    bib = params.get('bib', 'unknown')
    if save_png:
        for index, image in enumerate(images, start=1):
            png_path = f"{label_type}-{bib}-{labelsize}-{index}.png"
            image.save(png_path)
            log(f'Saved preview image: {png_path}')

    print('brother_ql: port: %s model: %s labelsize: %s' % (port, model, labelsize), file=sys.stderr)
    backend = 'network'
    #printer_identifier = f"tcp://{hostname}:{port}"
    base_kwargs = {'rotate': '90', 'label': labelsize}
    if LABEL_DPI == 600:
        base_kwargs['dpi_600'] = True
    print('brother_ql: backend: %s model: %s kwargs: %s' % (backend, model, base_kwargs), file=sys.stderr)

    data = bytearray()
    databytes = 0
    for index, image in enumerate(images):
        job_kwargs = base_kwargs.copy()
        job_kwargs['cut'] = index == len(images) - 1
        print('brother_ql[%d] image: %s size: %s mode: %s' % (index, type(image), image.size, image.mode), file=sys.stderr)
        qlr = BrotherQLRaster(model)
        print('brother_ql[%d] kwargs: %s' % (index, job_kwargs), file=sys.stderr)
        instructions = convert(qlr, [image], **job_kwargs)
        databytes += len(instructions)
        data.extend(instructions)

    if save_raster:
        raster_path = f"{label_type}-{bib}-{labelsize}.raster"
        with open(raster_path, 'wb') as f:
            f.write(data)
        log(f'Saved preview image: {raster_path}')

    print('brother_ql total databytes: %s ' % (databytes), file=sys.stderr)
    return bytes(data), port


def main() -> None:
    raw_fname, save_png, save_raster, dpi_600, labelsize_override, hostname, no_print = parse_cli_args(sys.argv[1:])

    print('raw_fname: %s save_png: %s save_raster: %s dpi_600: %s labelsize: %s hostname: %s' % (
        raw_fname, save_png, save_raster, dpi_600, labelsize_override, hostname), file=sys.stderr)
    payload = sys.stdin.buffer.read()

    data, port = render_label(raw_fname, payload, save_png, save_raster, dpi_600, labelsize_override)

    if no_print:
        print('No print flag set; exiting without sending to printer', file=sys.stderr)
        return

    if hostname:
        port = 9100
    else:
        hostname = '172.17.0.1' if is_docker() else '127.0.0.1'


    print('data: %s hostname: %s port: %s' % ('<omitted>' if data else None, hostname, port), file=sys.stderr)
    if data is None:
        return

    s = socket.socket()
    try:
        s.connect((hostname, port))
        s.sendall(data)
    except Exception as e:
        log('s.connect(%s,%d) %s' % (hostname, port, e))
        log(traceback.format_exc())
        exit(1)
    finally:
        try:
            s.close()
        except Exception:
            pass


if __name__ == '__main__':
    main()
