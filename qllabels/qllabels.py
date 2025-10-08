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

__version__ = "1.0.1"

import sys
import os
import socket
import subprocess
import traceback
import xml.etree.ElementTree as ET

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

from pdf2image import convert_from_bytes

import datetime

from PIL import Image, ImageDraw, ImageFont, ImageOps

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

def parse_cli_args(argv: List[str]) -> tuple[str, Optional[str]]:
    save_png_prefix: Optional[str] = None
    save_raster_prefix: Optional[str] = None
    dpi_600 = False
    positional: List[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ('--dpi_600', ):
            dpi_600 = True
        elif arg in ('--save_raster', '--save-raster'):
            if save_raster_prefix is not None:
                usage('Duplicate --save_png specified')
            i += 1
            if i >= len(argv):
                usage('Missing value for --save_raster')
            save_raster_prefix = argv[i]
        elif arg in ('--save_png', '--save-png'):
            if save_png_prefix is not None:
                usage('Duplicate --save_png specified')
            i += 1
            if i >= len(argv):
                usage('Missing value for --save_png')
            save_png_prefix = argv[i]
        else:
            positional.append(arg)
        i += 1
    if not positional:
        usage('No filename argument')
    print('save_png_prefix: %s save_raster_prefix: %s' % (save_png_prefix, save_raster_prefix), file=sys.stderr)
    return positional[0], save_png_prefix, save_raster_prefix, dpi_600


raw_fname, save_png_prefix, save_raster_prefix, dpi_600 = parse_cli_args(sys.argv[1:])
print('raw_fname: %s save_png_prefix: %s save_raster_prefix: %s dpi_600: %s' % (raw_fname, save_png_prefix, save_raster_prefix, dpi_600), file=sys.stderr)
fname = os.path.basename(raw_fname)


# Split file name apart to get information about the label.
#   bib, port, antenna and type parameters
# e.g:
#   230489203498023809_bib-719_port-8000_antenna-0_type-Frame.pdf
#
# Numeric fields are converted to numbers to allow comparisons like params['antenna'] == 1
params = { k:(int(v) if v.isdigit() else v) for k, v in (p.split('-') for p in os.path.splitext(fname)[0].split('_')[1:] if '-' in p ) }
print('params: %s' % (params))

Sizes = {
    "Tag": "small",
    "Frame": "small",
    "Shoulder": "small",
    "Emergency": "small",
    "Body": "large",
    "bib": "large",
  }

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
      
try:
    size = Sizes[params['type']]
except:
    usage('Do not understand type-%s' % (params['type']))

poolMatch = "%s-%d" % (params['port'], params['antenna'])
try:
    pool = Pools[poolMatch]
except:
    usage('Do not understand %s' % (poolMatch))

try:
    printerName = pool[size]
except:
    usage('Do not understand printerName %s' % (printerName))

try:
    printer = Printers[printerName]
except:
    usage('Cannot find printerName %s' % (printerName))


imagesize_300 = {
    '62': (1109, 696),
    '62x100': (1109, 696),
    '102': (1660, 1164),
    '102x152': (1660, 1164),
}

imagesize_600 = {
    '62': (2218, 1392),
    '62x100': (2218, 1392),
    '102': (3320, 2328),
    '102x152': (3320, 2328),
}

LABEL_DPI = 600 if dpi_600 else 300
imagesize = imagesize_600 if dpi_600 else imagesize_300

MARGIN_INCH = 0.00
VERTICAL_TEXT_GAP_MULTIPLIER = 2  # top + gap + bottom margins around rotated text strip
VERTICAL_TEXT_STRIP_MAX_INCH = 0.05
VERTICAL_TEXT_HEIGHT_SCALE = 2.6
BIB_HORIZONTAL_OFFSET_INCH = 0.125
#BODY_BOTTOM_BAND_RATIO = 0.12
BODY_BOTTOM_BAND_RATIO = 0.00
BODY_TEXT_PADDING_RATIO = 0.025
BODY_TEXT_HEIGHT_FACTOR = 0.46

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FONT_DIR = PROJECT_ROOT / 'fonts'
DIN_ENG_FONT = FONT_DIR / 'TGL_0-1451Eng.ttf'

try:
    RESAMPLING_LANCZOS = Image.Resampling.LANCZOS
except AttributeError:  # Pillow < 9
    RESAMPLING_LANCZOS = Image.LANCZOS

FONT_PATHS = {
    'bib': (
        str(DIN_ENG_FONT),
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
        '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
        '/usr/share/fonts/truetype/freefont/FreeSansBold.ttf',
    ),
    'bold': (
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
        '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
        '/usr/share/fonts/truetype/freefont/FreeSansBold.ttf',
    ),
    'regular': (
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
        '/usr/share/fonts/truetype/freefont/FreeSans.ttf',
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


def _fit_vertical_font(text: str, strip_width: int, max_vertical_extent: int) -> ImageFont.ImageFont:
    if not text:
        return _load_font(10, 'regular')
    font_path = _find_font_path('regular')
    if not font_path:
        return ImageFont.load_default()
    low, high = 1, max(1, strip_width)
    best_font = None
    while low <= high:
        mid = (low + high) // 2
        font = _load_font(mid, 'regular')
        bbox = _text_bbox(text, font)
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        if height <= strip_width and width <= max_vertical_extent:
            best_font = font
            low = mid + 1
        else:
            high = mid - 1
    return best_font or _load_font(max(1, high), 'regular')


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


def _parse_lines(pdf_bytes: bytes) -> List[Line]:
    raw_xml = _run_pdftotext_bbox(pdf_bytes)
    try:
        root = ET.fromstring(raw_xml)
    except ET.ParseError as exc:
        raise LabelExtractionError('Unable to parse pdftotext output') from exc
    doc = None
    for elem in root.iter():
        if _strip_namespace(elem.tag) == 'doc':
            doc = elem
            break
    if doc is None:
        raise LabelExtractionError('No document data found in pdftotext output')

    lines: List[Line] = []
    for page_index, page_elem in enumerate(doc.iter()):
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
            words.append(Word(text=text, x_min=x_min, y_min=y_min, x_max=x_max, y_max=y_max, page=page_index, page_width=width))
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


def _paste_rotated_text(base: Image.Image, img: Image.Image, x_offset: int, y_offset: int) -> None:
    if img.width == 0 or img.height == 0:
        return
    x_offset = max(0, min(x_offset, base.width - img.width))
    y_offset = max(0, min(y_offset, base.height - img.height))
    mask = ImageOps.invert(img)
    base.paste(img, (x_offset, y_offset), mask)


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

    strip_width = max(1, int(round(LABEL_DPI * VERTICAL_TEXT_STRIP_MAX_INCH)))
    available_vertical = max(height - VERTICAL_TEXT_GAP_MULTIPLIER * margin_px, height // 2)
    per_text_vertical = max(1, available_vertical // 2)

    event_font = _fit_vertical_font(fields.event, strip_width, per_text_vertical)
    participant_font = _fit_vertical_font(fields.participant, strip_width, per_text_vertical)

    if vertical_side.lower() == 'right':
        event_img = _create_rotated_text_image_right(fields.event, event_font)
        participant_img = _create_rotated_text_image_right(fields.participant, participant_font)
    else:
        event_img = _create_rotated_text_image(fields.event, event_font)
        participant_img = _create_rotated_text_image(fields.participant, participant_font)

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
    _paste_rotated_text(image, event_img, event_x, margin_px)

    participant_x = event_x if vertical_left else max(margin_px, width - margin_px - participant_img.width)

    min_participant_y = margin_px + event_img.height + margin_px
    max_participant_y = height - margin_px - participant_img.height
    if max_participant_y >= min_participant_y:
        participant_y = max_participant_y
    else:
        participant_y = min(height - participant_img.height, max(min_participant_y, margin_px))
    participant_y = max(margin_px, participant_y)
    _paste_rotated_text(image, participant_img, participant_x, participant_y)

    return image


def render_body_label(fields: LabelFields, target_size: tuple[int, int]) -> Image.Image:
    width, height = target_size
    margin_px = max(0, int(round(LABEL_DPI * MARGIN_INCH)))
    bottom_band_height = max(1, int(round(height * BODY_BOTTOM_BAND_RATIO)))
    digit_area_height = max(1, height - bottom_band_height)
    image = Image.new('L', (width, height), color=255)

    digit_width = max(1, width - 2 * margin_px)
    digit_height = max(1, digit_area_height - margin_px)
    bib_block = _render_bib_block(fields.bib, digit_width, digit_height)
    bib_x = margin_px + (digit_width - bib_block.width) // 2
    bib_y = max(0, margin_px)
    image.paste(bib_block, (max(0, bib_x), bib_y))

    band = Image.new('L', (width, bottom_band_height), color=255)
    drawer = ImageDraw.Draw(band)
    padding_x = max(4, int(width * BODY_TEXT_PADDING_RATIO))
    padding_y = max(4, int(bottom_band_height * BODY_TEXT_PADDING_RATIO))

    event_area_width = width // 2
    participant_area_width = width - event_area_width

    max_text_height_limit = max(1, int((bottom_band_height - padding_y) * BODY_TEXT_HEIGHT_FACTOR))
    event_font = _fit_horizontal_font(fields.event, event_area_width - 2 * padding_x, max_text_height_limit, 'regular')
    participant_font = _fit_horizontal_font(fields.participant, participant_area_width - 2 * padding_x, max_text_height_limit, 'regular')

    participant_text = fields.participant.strip()
    if participant_text.lower() == 'crossmgr' or not participant_text:
        participant_text = 'results.wimsey.co'

    event_bbox = _text_bbox(fields.event, event_font) if fields.event else [0, 0, 0, 0]
    participant_bbox = _text_bbox(participant_text, participant_font) if participant_text else [0, 0, 0, 0]
    baseline_height = max(event_bbox[3] - event_bbox[1], participant_bbox[3] - participant_bbox[1], 1)
    baseline_y = bottom_band_height - padding_y - baseline_height
    baseline_y = max(0, baseline_y)

    def draw_text(text: str, font: ImageFont.ImageFont, bbox: List[int], area_start: int, area_width: int, anchor: str = 'left') -> None:
        if not text:
            return
        text_width = bbox[2] - bbox[0]
        text_height = max(1, bbox[3] - bbox[1])
        if anchor == 'left':
            x = area_start + padding_x - bbox[0]
        else:
            x = area_start + area_width - padding_x - text_width - bbox[0]
        y = baseline_y - bbox[1]
        drawer.text((x, y), text, font=font, fill=0)

    draw_text(fields.event, event_font, event_bbox, 0, event_area_width, anchor='left')
    draw_text(participant_text, participant_font, participant_bbox, event_area_width, participant_area_width, anchor='right')

    image.paste(band, (0, height - bottom_band_height))
    return image

try:
    hostname = '172.17.0.1' if is_docker() else '127.0.0.1'
    port = printer['port']
    model = printer['model']
    labelsize = printer['labelsize']
except:
    usage('Cannot find one of hostname, port, model, labelsize: %s' % (printer))
    usage()


supports_600 = {'62', '62x100'}
effective_dpi_600 = dpi_600 and labelsize in supports_600
if dpi_600 and not effective_dpi_600:
    log(f"dpi_600 requested but not supported for label {labelsize}; falling back to 300 dpi")

LABEL_DPI = 600 if effective_dpi_600 else 300
imagesize = imagesize_600 if LABEL_DPI == 600 else imagesize_300


payload = sys.stdin.buffer.read()
label_dimensions = imagesize[labelsize]
print('hostname: %s port: %s model: %s labelsize: %s label_dimensions: %s LABEL_DPI: %s' % (hostname, port, model, labelsize, label_dimensions, LABEL_DPI), file=sys.stderr)
images: List[Image.Image]
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
    elif label_type == 'Body' and labelsize in ('102', '102x152'):
        body_image = render_body_label(fields, label_dimensions)
        images = [body_image]
        log(f"Rendered custom Body label for bib {fields.bib}")
    else:
        log(f"Custom layout not defined for type {label_type}; rasterizing PDF")
        images = convert_from_bytes(payload, size=label_dimensions, dpi=LABEL_DPI, grayscale=True)

if not images:
    usage('No images produced from input PDF')

if label_type == 'Frame':
    preview_images = images
else:
    preview_images = images

if save_png_prefix:
    directory = os.path.dirname(save_png_prefix)
    if directory:
        os.makedirs(directory, exist_ok=True)
    for index, image in enumerate(preview_images, start=1):
        png_path = f"{save_png_prefix}-{index}.png"
        image.save(png_path)
        log(f'Saved preview image: {png_path}')
    sys.exit(0)

# convert PNG images to Brother Raster file, Note we use --no-cut for 0..N-1, 
# the last file will have a cut so that multiple labels will be kept together.
#

print('brother_ql: hostname: %s port: %s model: %s labelsize: %s' % (hostname, port, model, labelsize), file=sys.stderr)
args_base = [ 
    'brother_ql', '--printer', f"tcp://{hostname}:{port}",
    '--model', model, 'print', '--rotate', '90', '--label', labelsize, 
    ]

# use brother_ql to convert the pillow images to raster format instructions for the printer
# and send them via the network to the printer (or qlmuxd).
#
backend = 'network'
printer = f"tcp://{hostname}:{port}"
base_kwargs = { 'rotate': '90', 'label': labelsize }
if LABEL_DPI == 600:
    base_kwargs['dpi_600'] = True
print('brother_ql: backend: %s printer: %s model: %s kwargs: %s' % (backend, printer, model, base_kwargs), file=sys.stderr)
#print('*********************', file=sys.stderr)

# N.b. In theory we can convert and print all labels with one convert/send, but 
# I cannot figure out how to get two labels printed with a single cut at the end.
#
#    qlr = BrotherQLRaster(model)
#    instructions = convert(qlr, images, **kwargs)
#    send(instructions=instructions, printer_identifier=printer, backend_identifier=backend, blocking=True)
#
# N.b. the brother_ql send works, but we need to send two labels with a single cut at the end as a single job,
# this produces two jobs, which qlmuxd may send to two printers. Which is not what we want. 
# We need to take all of the instructions, save them in a bytearray and send them as a single job.

data = None
databytes = 0
for index, image in enumerate(images):
    job_kwargs = base_kwargs.copy()
    job_kwargs['cut'] = index == len(images) - 1
    print('brother_ql[%d] image: %s size: %s mode: %s' % (index, type(image), image.size, image.mode), file=sys.stderr)
    qlr = BrotherQLRaster(model)

    # convert the image to raster format instructions, we get bytes back
    print('brother_ql[%d] kwargs: %s' % (index, job_kwargs), file=sys.stderr)
    instructions = convert(qlr, [image], **job_kwargs)

    # append the instructions to the data buffer bytearray, this is slightly painful
    databytes += len(instructions)
    if data is None:
        data = bytearray(instructions)
    else:
        data += bytearray(instructions)

    #print('brother_ql[%d] instructions: %s %d data: %s %s databytes: %s ' % (index, type(instructions), len(instructions), type(data), len(data), databytes), file=sys.stderr)
    #send(instructions=instructions, printer_identifier=printer, backend_identifier=backend, blocking=True)

if save_raster_prefix:
    directory = os.path.dirname(save_raster_prefix)
    raster_path = f"{save_raster_prefix}.raster"
    with open(raster_path, 'wb') as f:
        f.write(data)
    log(f'Saved preview image: {raster_path}')
    sys.exit(0)
print('brother_ql total databytes: %s ' % (databytes), file=sys.stderr)


#exit(0)

# This
#print('brother_ql[%d] data: %s %s databytes: %s ' % (index, type(data), len(data), databytes), file=sys.stderr)

# Send *.rast to qlmuxd or direct to printer
#
def main():
    s = socket.socket()
    hostname = '172.17.0.1' if is_docker() else '127.0.0.1'
    try:
        # XXX
        port = 9100
        hostname = '192.168.40.42'
        s.connect((hostname, port))
        s.sendall(data)
        s.close()
    except Exception as e:
        log('s.connect(%s,%d) %s' % ( hostname, port, e))
        log(traceback.format_exc())
        exit(1)
