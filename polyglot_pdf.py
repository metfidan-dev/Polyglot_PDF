#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PolyglotPDF — Passo 2: evidenziazioni e note sincronizzate tra le lingue
=======================================================================
Passo 1
- Un "progetto" raggruppa lo stesso documento in più lingue (PDF già esistenti).
- Cambio lingua istantaneo (Ctrl+1..9, Ctrl+Tab) mantenendo il punto di lettura.
- Allineamento automatico dei paragrafi (semantico se c'è sentence-transformers,
  altrimenti euristico su numeri/sigle/codici). Cache in ~/.cache/polyglot_pdf.

Passo 2
- Selezione del testo col mouse -> menu: evidenzia (5 colori), evidenzia con nota, copia.
- Note a punto (tasto destro "Aggiungi nota qui" o modalità Nota).
- Ogni evidenziazione/nota compare anche nelle altre lingue, sul testo corrispondente
  (sottolineatura tratteggiata = posizione stimata). Si può correggere a mano
  per singola lingua ("Correggi posizione in questa lingua").
- Pannello laterale con tutte le annotazioni (testo mostrato nella lingua corrente).
- Annullamento (Ctrl+Z), salvataggio automatico nel progetto.
- Esportazione PDF annotati (annotazioni PDF standard, leggibili in Acrobat ecc.).

Passo 3
- Generazione di una lingua mancante con LLM locale (Ollama, es. translategemma:4b):
  prova su poche pagine con stima del tempo, traduzione in background con cache
  per paragrafo e ripresa, glossario per coppia di lingue.
- Ricostruzione del PDF con PyMuPDF: testo originale rimosso (immagini e grafica
  intatte) e traduzione nello stesso riquadro, con lo stile originale.
- File di corrispondenza <nome>.polymap.json accanto al PDF generato: allineamento
  esatto con la lingua sorgente e modifiche manuali dei paragrafi (tasto destro).

Passo 4
- Allineamento semantico anche senza sentence-transformers: embedding da Ollama
  (modello paraphrase-multilingual).
- OCR delle pagine scansionate (Tesseract integrato in PyMuPDF), in un processo
  separato e con cache: testo selezionabile, annotazioni, allineamento e traduzione.

Dipendenze:   python -m pip install PySide6 pymupdf numpy
Opzionale:    python -m pip install sentence-transformers
Passo 3:      Ollama (https://ollama.com) + ollama pull translategemma:4b
Passo 4:      ollama pull paraphrase-multilingual   (dati OCR scaricati dall'app)

Uso:  python polyglot_pdf.py [progetto.polypdf.json | file1.pdf file2.pdf ...]
"""
from __future__ import annotations

import bisect
import copy
import difflib
import hashlib
import html
import json
import math
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime

# PyMuPDF 2.x carica a ogni avvio un modello ONNX di analisi del layout (pymupdf.layout,
# ~2 s) che qui non serve: lo si disattiva. L'estrazione del testo resta identica.
sys.modules.setdefault("pymupdf.layout", None)
try:
    try:
        import pymupdf as fitz   # PyMuPDF >= 1.24.3 (il nome "fitz" è deprecato)
    except ImportError:
        import fitz
    import numpy as np
    import PySide6  # noqa: F401
except ImportError as _e:
    sys.exit(f"Modulo mancante: {'pymupdf' if _e.name == 'fitz' else _e.name}. Questo Python ({sys.executable}) non ha le dipendenze.\n"
             "Avvia con l'ambiente del progetto:  .venv\\Scripts\\python polyglot_pdf.py  (oppure avvia.bat)")
from PySide6.QtCore import (QPointF, QProcess, QRectF, Qt, QThread, QTimer, QUrl, Signal,
                            qVersion)
from PySide6.QtGui import (QAction, QActionGroup, QBrush, QColor, QCursor,
                           QDesktopServices, QIcon, QImage, QKeySequence, QPainter,
                           QPainterPath, QPen, QPixmap, QShortcut, QTransform)
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QDockWidget, QFileDialog,
                               QFormLayout, QFrame, QGraphicsDropShadowEffect,
                               QGraphicsItem, QGraphicsPixmapItem,
                               QGraphicsRectItem, QGraphicsScene, QGraphicsView,
                               QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMainWindow, QMenu,
                               QMessageBox, QPlainTextEdit, QProgressBar,
                               QPushButton, QSpinBox, QStackedWidget, QToolBar,
                               QToolButton, QVBoxLayout)

# --------------------------------------------------------------------------- #
# Costanti
# --------------------------------------------------------------------------- #
APP_NAME = "PolyglotPDF"
APP_VERSION = "0.5.1"    # 0.<passo>.<correzione>
PROJECT_EXT = ".polypdf.json"


def _data_dir() -> str:
    """Versione portabile (.exe con portable.txt accanto): dati nella cartella «dati» del
    programma, così il PC resta pulito e la chiavetta si porta dietro cache e dati OCR."""
    if getattr(sys, "frozen", False):
        here = os.path.dirname(os.path.abspath(sys.executable))
        if os.path.exists(os.path.join(here, "portable.txt")):
            return os.path.join(here, "dati")
    return os.path.join(os.path.expanduser("~"), ".cache", "polyglot_pdf")


CACHE_DIR = _data_dir()
EMB_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
OLLAMA_EMB_MODEL = "paraphrase-multilingual"      # ripiego senza sentence-transformers
ALIGN_VERSION = "v1"
METHOD_LABELS = {"semantico": "semantico", "semantico-ollama": "semantico, Ollama",
                 "euristico": "euristico"}

TESSDATA_DIR = os.path.join(CACHE_DIR, "tessdata")
TESSDATA_URL = "https://github.com/tesseract-ocr/tessdata/raw/main/{}.traineddata"
OCR_LANGS = {"it": "ita", "de": "deu", "fr": "fra", "en": "eng", "es": "spa", "pt": "por",
             "nl": "nld", "pl": "pol", "cs": "ces", "tr": "tur", "zh": "chi_sim", "ja": "jpn",
             "rm": "ita+deu"}
OCR_DPI = 300
OCR_VERSION = "v1"
OCR_ERROR_FILE = os.path.join(CACHE_DIR, "ocr_errore.txt")

PAGE_GAP = 12.0          # spazio tra pagine (punti PDF)
ANCHOR_RATIO = 1 / 3     # punto di lettura: a 1/3 dell'altezza della finestra
MIN_ZOOM, MAX_ZOOM = 0.2, 6.0
MIN_ANCHOR_CHARS = 5     # blocchi più corti (es. numeri di pagina) non fanno da ancora
NOTE_ICON = 14.0         # lato icona nota (punti PDF)
UNDO_LIMIT = 50

COLORS = [("Giallo", "#FFD400"), ("Verde", "#7ED957"), ("Azzurro", "#5BC0EB"),
          ("Rosa", "#FF7EB6"), ("Arancio", "#FFA03A")]

LANGS = ["it", "de", "fr", "en", "rm", "es", "pt", "nl", "pl", "cs", "tr", "zh", "ja"]
LANG_HINTS = {
    "it": {"it", "ita", "italiano", "italian"},
    "de": {"de", "deu", "ger", "deutsch", "german"},
    "fr": {"fr", "fra", "fre", "francais", "français", "french"},
    "en": {"en", "eng", "english"},
    "es": {"es", "esp", "spa", "espanol", "español", "spanish"},
    "rm": {"rm", "roh", "rumantsch"},
}
STOPWORDS = {
    "it": {"il", "lo", "di", "che", "per", "della", "del", "sono", "non", "una", "con", "nel", "delle", "gli"},
    "de": {"der", "die", "das", "und", "ist", "nicht", "mit", "für", "von", "den", "ein", "eine", "auf", "wird"},
    "fr": {"le", "les", "des", "et", "est", "pour", "une", "dans", "du", "que", "sur", "avec", "pas", "sont"},
    "en": {"the", "and", "of", "to", "is", "for", "with", "that", "are", "this", "be", "on", "by", "not"},
    "es": {"el", "los", "las", "y", "es", "para", "una", "del", "que", "con", "por", "se", "como", "está"},
}


def color_icon(hex_color: str) -> QIcon:
    pm = QPixmap(14, 14)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(hex_color))
    p.setPen(QColor(0, 0, 0, 90))
    p.drawRoundedRect(1, 1, 12, 12, 3, 3)
    p.end()
    return QIcon(pm)


def short(text: str, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[:n - 1] + "…"


# --------------------------------------------------------------------------- #
# Modello documento
# --------------------------------------------------------------------------- #
@dataclass
class Block:
    page: int
    rect: tuple                                  # (x0, y0, x1, y1) in punti PDF
    text: str
    words: list = field(default_factory=list)    # [(x0, y0, x1, y1, parola)] in ordine


def file_hash(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def extract_blocks(doc: fitz.Document, ocr=None):
    """Blocchi di testo (con parole) in ordine di lettura; almeno un blocco per pagina.
    ocr: {pagina: {"words": [...], "blocks": [...]}} testo riconosciuto per le pagine scansionate."""
    ocr = ocr or {}
    blocks, page_first = [], []
    for pno, page in enumerate(doc):
        page_first.append(len(blocks))
        if pno in ocr:
            words, raw = ocr[pno]["words"], ocr[pno]["blocks"]
        else:
            tp = page.get_textpage()
            words = page.get_text("words", textpage=tp)
            raw = page.get_text("blocks", textpage=tp, sort=True)
        by_block = defaultdict(list)
        for x0, y0, x1, y1, w, bno, lno, wno in words:
            by_block[bno].append((lno, wno, (x0, y0, x1, y1, w)))
        found = False
        for x0, y0, x1, y1, text, bno, btype in raw:
            if btype != 0:
                continue
            text = " ".join(text.split())
            if len(text) < 2:
                continue
            ws = [w for _l, _w, w in sorted(by_block.get(bno, []), key=lambda t: (t[0], t[1]))]
            blocks.append(Block(pno, (x0, y0, x1, y1), text, ws))
            found = True
        if not found:  # pagina senza testo (immagine/scansione): blocco fittizio
            r = page.rect
            blocks.append(Block(pno, (0.0, 0.0, r.width, r.height), ""))
    page_first.append(len(blocks))
    return blocks, page_first


# ---- OCR delle pagine scansionate (Tesseract integrato in PyMuPDF) ----
def ocr_langs(lang: str) -> str:
    return OCR_LANGS.get(lang, "eng")


def tessdata_missing(tess: str):
    return [t for t in tess.split("+")
            if not os.path.exists(os.path.join(TESSDATA_DIR, t + ".traineddata"))]


def download_tessdata(names):
    """Scarica i dati di lingua Tesseract (una volta sola) in TESSDATA_DIR."""
    os.makedirs(TESSDATA_DIR, exist_ok=True)
    for t in names:
        out = os.path.join(TESSDATA_DIR, t + ".traineddata")
        try:
            with urllib.request.urlopen(TESSDATA_URL.format(t), timeout=60) as r, \
                    open(out + ".tmp", "wb") as f:
                while chunk := r.read(1 << 16):
                    f.write(chunk)
        except (urllib.error.URLError, OSError) as e:
            raise OSError(f"Download di {t}.traineddata non riuscito: "
                          f"{getattr(e, 'reason', e)}") from None
        os.replace(out + ".tmp", out)


def ocr_cache_path(doc_hash: str, tess: str) -> str:
    return os.path.join(CACHE_DIR, f"ocr_{doc_hash}_{tess.replace('+', '-')}_{OCR_VERSION}.json")


def load_ocr(doc_hash: str, tess: str, complete_only=True) -> dict:
    """{pagina: {"words", "blocks"}} dalla cache; vuoto se manca (o è incompleta)."""
    try:
        with open(ocr_cache_path(doc_hash, tess), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if complete_only and not data.get("complete"):
        return {}
    return {int(p): v for p, v in data.get("pages", {}).items()}


def scanned_pages(doc: fitz.Document, blocks, page_first):
    """Pagine senza testo con un'immagine grande (scansioni): candidate all'OCR."""
    out = []
    for pno, page in enumerate(doc):
        a, b = page_first[pno], page_first[pno + 1]
        if b - a != 1 or blocks[a].text:
            continue
        try:
            images = page.get_image_info()
        except Exception:
            images = []
        area = abs(page.rect)
        if any(abs(fitz.Rect(im["bbox"]) & page.rect) > 0.3 * area for im in images):
            out.append(pno)
    return out


def ocr_main(argv) -> int:
    """Processo separato (PyMuPDF non è utilizzabile da più thread):
    polyglot_pdf --ocr <pdf> <hash> <lingue tesseract> <pagine separate da virgola>.
    Salva i risultati nella cache ogni poche pagine (ripresa) e l'avanzamento in <cache>.progress."""
    path, doc_hash, tess, pages_arg = argv[:4]
    pages = [int(p) for p in pages_arg.split(",") if p]
    cache = ocr_cache_path(doc_hash, tess)
    done = {str(p): v for p, v in load_ocr(doc_hash, tess, complete_only=False).items()}
    doc = fitz.open(path)

    def save(complete):
        save_json_atomic(cache, {"app": APP_NAME, "hash": doc_hash, "lang": tess,
                                 "complete": complete, "pages": done})

    def progress():
        with open(cache + ".progress", "w", encoding="utf-8") as f:
            f.write(f"{sum(str(p) in done for p in pages)} {len(pages)}")

    progress()
    last = time.monotonic()
    for pno in pages:
        if str(pno) in done:
            continue
        page = doc[pno]
        tp = page.get_textpage_ocr(language=tess, dpi=OCR_DPI, full=True, tessdata=TESSDATA_DIR)
        r2 = lambda v: round(v, 2)
        done[str(pno)] = {
            "words": [[r2(x0), r2(y0), r2(x1), r2(y1), w, bno, lno, wno]
                      for x0, y0, x1, y1, w, bno, lno, wno in page.get_text("words", textpage=tp)],
            "blocks": [[r2(x0), r2(y0), r2(x1), r2(y1), t, bno, bt]
                       for x0, y0, x1, y1, t, bno, bt in page.get_text("blocks", textpage=tp, sort=True)
                       if bt == 0]}
        progress()
        if time.monotonic() - last > 5:
            save(False)
            last = time.monotonic()
    save(True)
    return 0


def guess_lang_from_name(path: str):
    stem = os.path.splitext(os.path.basename(path))[0].lower()
    tokens = set(re.split(r"[^a-zàâäçéèêëîïôöûùüÿñ]+", stem))
    for lang, hints in LANG_HINTS.items():
        if tokens & hints:
            return lang
    return None


def detect_lang(doc: fitz.Document, max_pages: int = 3):
    text = " ".join(doc[i].get_text() for i in range(min(max_pages, len(doc)))).lower()
    words = Counter(re.findall(r"[a-zàâäçéèêëîïôöûùüÿñß]+", text))
    if not words:
        return None
    scores = {lang: sum(words[w] for w in sw) for lang, sw in STOPWORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] >= 5 else None


class Document:
    def __init__(self, path: str, lang: str):
        self.path = os.path.abspath(path)
        self.lang = lang
        self.doc = fitz.open(self.path)
        self.page_sizes = [(p.rect.width, p.rect.height) for p in self.doc]
        self.hash = file_hash(self.path)
        ocr = load_ocr(self.hash, ocr_langs(lang))
        self.blocks, self.page_first = extract_blocks(self.doc, ocr)
        self.ocr_pages = set(ocr)
        # pagine scansionate ancora da riconoscere (con OCR già fatto non sono più "senza testo")
        self.pending_ocr = scanned_pages(self.doc, self.blocks, self.page_first)
        self.pending_ocr = [p for p in self.pending_ocr if p not in ocr]
        # identifica il testo estratto: cambia quando si aggiunge l'OCR (cache, ancore)
        self.text_key = self.hash + ("o" if ocr else "")
        # indice globale delle parole: (blocco, k, pagina, x0, y0, x1, y1)
        self.wg, self.block_wstart = [], []
        self.page_words = [[] for _ in self.page_sizes]
        for bi, b in enumerate(self.blocks):
            self.block_wstart.append(len(self.wg))
            for k, (x0, y0, x1, y1, _w) in enumerate(b.words):
                self.page_words[b.page].append(len(self.wg))
                self.wg.append((bi, k, b.page, x0, y0, x1, y1))
        self.block_wstart.append(len(self.wg))

    @property
    def n(self) -> int:
        return len(self.blocks)

    # ---- posizione continua (indice blocco + frazione) ----
    def pos_from_point(self, pno: int, x: float, y: float) -> float:
        a, b = self.page_first[pno], self.page_first[pno + 1]
        fallback = None
        for k in range(a, b):
            x0, y0, x1, y1 = self.blocks[k].rect
            if y0 <= y <= y1:
                frac = (y - y0) / max(y1 - y0, 1e-3)
                if x0 <= x <= x1:
                    return k + frac
                if fallback is None:
                    fallback = k + frac
        if fallback is not None:
            return fallback
        below = [k for k in range(a, b) if self.blocks[k].rect[1] > y]
        if below:
            return float(min(below, key=lambda k: self.blocks[k].rect[1]))
        above = [k for k in range(a, b) if self.blocks[k].rect[3] < y]
        if above:
            return max(above, key=lambda k: self.blocks[k].rect[3]) + 0.999
        return float(a)

    def point_from_pos(self, pos: float):
        """-> (pagina, y, indice blocco)."""
        pos = min(max(pos, 0.0), self.n - 1e-6)
        k = int(pos)
        frac = pos - k
        b = self.blocks[k]
        x0, y0, x1, y1 = b.rect
        return b.page, y0 + frac * (y1 - y0), k

    # ---- parole ----
    def word_at(self, pno: int, x: float, y: float, nearest: bool = True):
        best, bd = None, float("inf")
        for g in self.page_words[pno]:
            _b, _k, _p, x0, y0, x1, y1 = self.wg[g]
            if x0 <= x <= x1 and y0 <= y <= y1:
                return g
            if nearest:
                dx = max(x0 - x, 0.0, x - x1)
                dy = max(y0 - y, 0.0, y - y1)
                d = dx * dx + 9 * dy * dy   # preferisce la stessa riga
                if d < bd:
                    best, bd = g, d
        return best

    def word_pos(self, g: int, end: bool = False) -> float:
        bi, k = self.wg[g][0], self.wg[g][1]
        K = len(self.blocks[bi].words)
        return bi + (k + (1 if end else 0)) / K

    def _pos_to_word(self, q: float, end: bool):
        q = min(max(q, 0.0), self.n - 1e-9)
        b = int(q)
        frac = q - b
        if end and frac < 1e-9 and b > 0:      # fine esatta di un blocco
            b, frac = b - 1, 1.0
        step = -1 if end else 1
        while 0 <= b < self.n and not self.blocks[b].words:
            b += step
            frac = 1.0 if end else 0.0
        if not 0 <= b < self.n:
            return None
        K = len(self.blocks[b].words)
        x = frac * K       # tolleranza: 7 + 3/18 non deve diventare 2,9999… o 3,0000…1 parole
        k = max(math.ceil(x - 1e-6) - 1, 0) if end else int(x + 1e-6)
        return self.block_wstart[b] + min(k, K - 1)

    def words_in_pos_range(self, q0: float, q1: float):
        g0, g1 = self._pos_to_word(q0, False), self._pos_to_word(q1, True)
        if g0 is None and g1 is None:
            return None
        if g0 is None:
            g0 = g1
        if g1 is None or g1 < g0:
            g1 = g0
        return g0, g1

    def rects_for_words(self, g0: int, g1: int):
        """Rettangoli uniti per riga: [[pagina, x0, y0, x1, y1], ...]."""
        out, cur = [], None
        for g in range(g0, g1 + 1):
            bi, _k, p, x0, y0, x1, y1 = self.wg[g]
            if (cur and cur[0] == p and cur[5] == bi
                    and abs((y0 + y1) / 2 - (cur[2] + cur[4]) / 2) < 0.5 * max(y1 - y0, 1)):
                cur = [p, min(cur[1], x0), min(cur[2], y0), max(cur[3], x1), max(cur[4], y1), bi]
            else:
                if cur:
                    out.append(cur[:5])
                cur = [p, x0, y0, x1, y1, bi]
        if cur:
            out.append(cur[:5])
        return out

    def text_for_words(self, g0: int, g1: int) -> str:
        return " ".join(self.blocks[self.wg[g][0]].words[self.wg[g][1]][4] for g in range(g0, g1 + 1))

    def rects_for_pos_range(self, q0: float, q1: float):
        """Ripiego per blocchi senza parole (pagine scansionate)."""
        p0, ya, k0 = self.point_from_pos(q0)
        _p1, yb, k1 = self.point_from_pos(q1)
        x0, y0, x1, y1 = self.blocks[k0].rect
        yb = yb if k1 == k0 else y1
        return [[p0, x0, ya, x1, max(yb, ya + 10)]]


# --------------------------------------------------------------------------- #
# Allineamento tra lingue
# --------------------------------------------------------------------------- #
TOKEN_RE = re.compile(r"\d+(?:[.,]\d+)*|[A-Z][A-Z0-9]+(?:[-/][A-Z0-9]+)*")


def lang_free_tokens(text: str) -> set:
    """Token che restano uguali in tutte le lingue: numeri, sigle, codici."""
    return {t.replace(",", ".") for t in TOKEN_RE.findall(text)}


def _word_token(w: str):
    t = w.strip(".,;:()[]«»\"'!?").replace(",", ".")
    return t if t and TOKEN_RE.fullmatch(t) else None


def _fold(w: str) -> str:
    """Parola in minuscolo, senza accenti né punteggiatura (per le parole affini)."""
    w = unicodedata.normalize("NFKD", w.lower().replace("ß", "ss"))
    return "".join(c for c in w if c.isalnum() and not unicodedata.combining(c))


def _in_order(ka_list, kb_list):
    """Occorrenze appaiate nell'ordine, solo se sono tante quante in entrambi i paragrafi."""
    return list(zip(ka_list, kb_list)) if len(ka_list) == len(kb_list) else []


def block_word_matches(words_a, words_b) -> dict:
    """Corrispondenze parola -> parola fra due paragrafi tradotti (anche in ordine diverso):
    token invarianti (numeri, sigle), punteggiatura (:, ;, parentesi), parole affini
    (Sensor/sensore, Intelligentes/intelligente). -> {ka: kb}"""
    pairs = []
    # 1) numeri, sigle, codici
    ta, tb = defaultdict(list), defaultdict(list)
    for src, dst in ((words_a, ta), (words_b, tb)):
        for k, w in enumerate(src):
            t = _word_token(w[4])
            if t:
                dst[t].append(k)
    for t, ks in ta.items():
        pairs += _in_order(ks, tb.get(t, []))
    # 2) punteggiatura che chiude o apre una parte di frase
    for test in (lambda s: s.endswith(":"), lambda s: s.endswith(";"),
                 lambda s: s.startswith("("), lambda s: s.rstrip(".,;:").endswith(")")):
        pairs += _in_order([k for k, w in enumerate(words_a) if test(w[4])],
                           [k for k, w in enumerate(words_b) if test(w[4])])
    # 3) parole affini: migliore corrispondenza reciproca fra le forme (>= 4 lettere)
    fa, fb = defaultdict(list), defaultdict(list)
    for src, dst in ((words_a, fa), (words_b, fb)):
        for k, w in enumerate(src):
            f = _fold(w[4])
            if len(f) >= 4 and not f.isdigit():
                dst[f].append(k)

    def best(f, others):
        cand = [(difflib.SequenceMatcher(None, f, g).ratio(), g) for g in others if g[0] == f[0]]
        return max(cand, default=(0.0, None))

    for f in fa:
        r, g = best(f, fb)
        if g is not None and r >= 0.72 and best(g, fa)[1] == f:
            pairs += _in_order(fa[f], fb[g])
    out = {}
    for ka, kb in sorted(pairs):
        out.setdefault(ka, kb)
    return out


def intra_block_map(words_a, words_b, matches=None):
    """Mappa fine frazione-parola -> frazione-parola fra due paragrafi tradotti,
    con le corrispondenze di block_word_matches() come punti fissi (solo quelle in ordine)."""
    Ka, Kb = len(words_a), len(words_b)
    if not Ka or not Kb:
        return np.array([0.0, 1.0]), np.array([0.0, 1.0])
    if matches is None:
        matches = block_word_matches(words_a, words_b)
    pairs = _longest_increasing([(ka, kb, 0.0) for ka, kb in sorted(matches.items())])
    xs, ys = [0.0], [0.0]
    for ka, kb, _ in pairs:
        for x, y in ((ka / Ka, kb / Kb), ((ka + 1) / Ka, (kb + 1) / Kb)):
            if x > xs[-1] and y > ys[-1]:
                xs.append(x)
                ys.append(y)
    if xs[-1] < 1.0 and ys[-1] < 1.0:
        xs.append(1.0)
        ys.append(1.0)
    return np.array(xs), np.array(ys)


def _band(i, nA, nB, w):
    c = int(round(i * (nB - 1) / max(nA - 1, 1)))
    return max(0, c - w), min(nB, c + w + 1)


def _rows_embedding(EA, EB, w):
    nA, nB = len(EA), len(EB)
    for i in range(nA):
        lo, hi = _band(i, nA, nB, w)
        yield i, np.arange(lo, hi), EB[lo:hi] @ EA[i]


def _rows_heuristic(A, B, w):
    nA, nB = len(A), len(B)
    tokA = [lang_free_tokens(b.text) for b in A]
    tokB = [lang_free_tokens(b.text) for b in B]
    df = Counter(t for s in tokA + tokB for t in s)
    N = nA + nB
    idf = {t: math.log(1 + N / c) for t, c in df.items()}
    inv = defaultdict(list)
    for j, s in enumerate(tokB):
        for t in s:
            inv[t].append(j)
    normB = [math.sqrt(sum(idf[t] ** 2 for t in s)) or 1.0 for s in tokB]
    for i, s in enumerate(tokA):
        if not s:
            continue
        lo, hi = _band(i, nA, nB, w)
        scores = defaultdict(float)
        for t in s:
            lst = inv.get(t)
            if not lst:
                continue
            for j in lst[bisect.bisect_left(lst, lo):bisect.bisect_left(lst, hi)]:
                scores[j] += idf[t] ** 2
        if not scores:
            continue
        normA = math.sqrt(sum(idf[t] ** 2 for t in s)) or 1.0
        la = max(len(A[i].text), 1)
        js = np.fromiter(scores.keys(), dtype=int)
        sims = np.empty(len(js), dtype=np.float32)
        for k, j in enumerate(js):
            r = la / max(len(B[j].text), 1)
            sims[k] = scores[j] / (normA * normB[j]) * min(r, 1 / r) ** 0.3
        yield i, js, sims


def _longest_increasing(anchors):
    """Sottosequenza più lunga con indici crescenti in entrambi i documenti."""
    tails, tails_idx, prev = [], [], [-1] * len(anchors)
    for k, (_i, j, _s) in enumerate(anchors):
        p = bisect.bisect_left(tails, j)
        if p == len(tails):
            tails.append(j)
            tails_idx.append(k)
        else:
            tails[p] = j
            tails_idx[p] = k
        prev[k] = tails_idx[p - 1] if p > 0 else -1
    out, k = [], tails_idx[-1] if tails_idx else -1
    while k != -1:
        out.append(anchors[k])
        k = prev[k]
    return out[::-1]


def align_blocks(A, B, EA=None, EB=None):
    """Restituisce le ancore [(i, j, sim)] tra blocchi di A e di B."""
    nA, nB = len(A), len(B)
    if nA == 0 or nB == 0:
        return []
    w = max(40, int(0.12 * max(nA, nB)))
    valid_a = np.array([len(b.text) >= MIN_ANCHOR_CHARS for b in A])
    valid_b = np.array([len(b.text) >= MIN_ANCHOR_CHARS for b in B])
    if EA is not None and EB is not None:
        rows, thr = _rows_embedding(EA, EB, w), 0.6
    else:
        rows, thr = _rows_heuristic(A, B, w), 0.4

    row_best = np.full(nA, -1)
    row_sim = np.zeros(nA, dtype=np.float32)
    col_best = np.full(nB, -1)
    col_sim = np.full(nB, -np.inf, dtype=np.float32)
    for i, js, sims in rows:
        if not valid_a[i] or len(js) == 0:
            continue
        m = valid_b[js]
        js, sims = js[m], sims[m]
        if len(js) == 0:
            continue
        k = int(np.argmax(sims))
        row_best[i], row_sim[i] = js[k], sims[k]
        upd = sims > col_sim[js]
        col_sim[js[upd]] = sims[upd]
        col_best[js[upd]] = i

    anchors = [(int(i), int(row_best[i]), float(row_sim[i]))
               for i in range(nA)
               if row_best[i] >= 0 and col_best[row_best[i]] == i and row_sim[i] >= thr]
    return _longest_increasing(anchors)


def build_mapping(anchors, nA, nB):
    """Funzione monotona a tratti posA -> posB (array xs, ys strettamente crescenti)."""
    xs, ys = [0.0], [0.0]
    for i, j, _ in anchors:
        for x, y in ((i, j), (i + 1, j + 1)):
            if x > xs[-1] and y > ys[-1]:
                xs.append(float(x))
                ys.append(float(y))
    if nA > xs[-1] and nB > ys[-1]:
        xs.append(float(nA))
        ys.append(float(nB))
    return np.array(xs), np.array(ys)


class _STEmbedder:
    """sentence-transformers nel processo (se installato)."""
    method, name, batch = "semantico", EMB_MODEL, 256

    def __init__(self, model):
        self.model = model

    def encode(self, texts):
        return self.model.encode(texts, batch_size=32, convert_to_numpy=True,
                                 normalize_embeddings=True, show_progress_bar=False)


class OllamaEmbedder:
    """Embedding multilingue da Ollama (/api/embed): nessuna dipendenza Python in più."""
    method, name, batch = "semantico-ollama", OLLAMA_EMB_MODEL, 64

    def encode(self, texts):
        with _ollama_open("/api/embed", {"model": self.name, "input": texts, "truncate": True},
                          timeout=600) as r:
            E = np.array(json.loads(r.read().decode("utf-8"))["embeddings"], dtype=np.float32)
        return E / np.maximum(np.linalg.norm(E, axis=1, keepdims=True), 1e-9)


_ST_MODEL = None
_ST_FAILED = False


def get_embedding_model(progress=lambda s: None):
    """sentence-transformers se installato, altrimenti Ollama; None = allineamento euristico."""
    global _ST_MODEL, _ST_FAILED
    if _ST_MODEL is None and not _ST_FAILED:
        try:
            from sentence_transformers import SentenceTransformer
            progress("Caricamento modello semantico (al primo avvio viene scaricato)…")
            _ST_MODEL = _STEmbedder(SentenceTransformer(EMB_MODEL))
        except ImportError:
            _ST_FAILED = True
        except Exception as e:  # rete assente, ecc.
            _ST_FAILED = True
            progress(f"Modello semantico non disponibile: {e}")
    if _ST_MODEL is not None:
        return _ST_MODEL
    try:                       # Ollama può essere avviato dopo: si ricontrolla ogni volta
        if any(m.split(":")[0] == OLLAMA_EMB_MODEL for m in ollama_models()):
            return OllamaEmbedder()
        progress(f"Allineamento euristico (per quello semantico: ollama pull {OLLAMA_EMB_MODEL})")
    except OSError:
        pass
    return None


def embed_blocks(doc_key, blocks, model, progress=lambda s: None, stop=lambda: False):
    path = os.path.join(CACHE_DIR, f"emb_{doc_key}_{re.sub(r'[^A-Za-z0-9.-]', '_', model.name)}.npy")
    if os.path.exists(path):
        E = np.load(path)
        if E.shape[0] == len(blocks):
            return E
    texts = [b.text[:600] if b.text else " " for b in blocks]
    parts = []
    for s in range(0, len(texts), model.batch):
        if stop():
            return None
        parts.append(model.encode(texts[s:s + model.batch]))
        progress(f"Embeddings {min(s + model.batch, len(texts))}/{len(texts)}")
    E = np.vstack(parts).astype(np.float32)
    for k, b in enumerate(blocks):
        if not b.text:
            E[k] = 0.0
    np.save(path, E)
    return E


class AlignWorker(QThread):
    progress = Signal(str)
    pair_done = Signal(str, str, str, str, object, object, int, str)

    def __init__(self, snapshot: dict, pairs: list, parent=None):
        super().__init__(parent)
        self.snapshot = snapshot   # lang -> (text_key, blocks)
        self.pairs = pairs

    def run(self):
        stop = self.isInterruptionRequested
        model = get_embedding_model(self.progress.emit)
        embs = {}
        for la, lb in self.pairs:
            if stop():
                return
            method = model.method if model is not None else "euristico"
            ha, A = self.snapshot[la]
            hb, B = self.snapshot[lb]
            cache = os.path.join(CACHE_DIR, f"align_{ha}_{hb}_{method}_{ALIGN_VERSION}.json")
            anchors = None
            if os.path.exists(cache):
                try:
                    with open(cache, encoding="utf-8") as f:
                        anchors = [tuple(a) for a in json.load(f)]
                except (OSError, ValueError):
                    anchors = None
            if anchors is None:
                self.progress.emit(f"Allineamento {la.upper()} ↔ {lb.upper()}…")
                EA = EB = None
                if model is not None:
                    try:
                        for lang, h, bl in ((la, ha, A), (lb, hb, B)):
                            if lang not in embs:
                                embs[lang] = embed_blocks(h, bl, model, self.progress.emit, stop)
                                if embs[lang] is None:
                                    return
                        EA, EB = embs[la], embs[lb]
                    except OSError as e:          # es. Ollama chiuso a metà: ripiego euristico
                        self.progress.emit(f"Allineamento semantico non riuscito ({e}): euristico")
                        model, method = None, "euristico"
                        cache = os.path.join(CACHE_DIR, f"align_{ha}_{hb}_{method}_{ALIGN_VERSION}.json")
                anchors = align_blocks(A, B, EA, EB)
                try:
                    with open(cache, "w", encoding="utf-8") as f:
                        json.dump(anchors, f)
                except OSError:
                    pass
            xs, ys = build_mapping(anchors, len(A), len(B))
            self.pair_done.emit(la, lb, ha, hb, xs, ys, len(anchors), METHOD_LABELS.get(method, method))


# --------------------------------------------------------------------------- #
# Passo 3: generazione di una lingua mancante (LLM locale via Ollama)
# --------------------------------------------------------------------------- #
LANG_NAMES = {"it": "Italian", "de": "German", "fr": "French", "en": "English", "rm": "Romansh",
              "es": "Spanish", "pt": "Portuguese", "nl": "Dutch", "pl": "Polish", "cs": "Czech",
              "tr": "Turkish", "zh": "Chinese", "ja": "Japanese"}
LANG_NAMES_UI = {"it": "italiano", "de": "tedesco", "fr": "francese", "en": "inglese",
                 "rm": "romancio", "es": "spagnolo", "pt": "portoghese", "nl": "olandese",
                 "pl": "polacco", "cs": "ceco", "tr": "turco", "zh": "cinese", "ja": "giapponese"}
TR_MODELS = ["translategemma:4b", "translategemma:12b"]
TR_VERSION = "v1"
TR_MAX_CHARS = 1500          # paragrafi più lunghi vengono spezzati per frase
GEN_MAP_EXT = ".polymap.json"
HYPHEN_KEEP = {"und", "oder", "bzw", "sowie", "e", "ed", "o", "et", "ou", "and", "or"}


OLLAMA_OPTIONS = {"temperature": 0, "num_ctx": 4096}   # cambiarle fa ricaricare il modello (~30 s)


def ollama_base() -> str:
    # 127.0.0.1 e non «localhost»: su Windows «localhost» prova prima IPv6 (::1), dove Ollama
    # non ascolta, e ogni richiesta perde ~2 s prima di ripiegare su IPv4.
    h = (os.environ.get("OLLAMA_HOST", "").strip() or "127.0.0.1:11434").rstrip("/")
    h = re.sub(r"^(https?://)?(localhost|0\.0\.0\.0)(?=[:/]|$)", r"\g<1>127.0.0.1", h)
    if not h.startswith(("http://", "https://")):
        h = "http://" + h
    if not re.search(r":\d+$", h.split("//", 1)[1]):
        h += ":11434"
    return h


def _ollama_open(path, payload=None, timeout=10.0):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(ollama_base() + path, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8")).get("error") or str(e)
        except Exception:
            msg = str(e)
        raise OSError(msg) from None
    except urllib.error.URLError as e:
        raise OSError(f"Ollama non raggiungibile su {ollama_base()} ({e.reason})") from None


def ollama_models():
    """Modelli installati in Ollama; OSError se il server non risponde."""
    with _ollama_open("/api/tags", timeout=3) as r:
        return sorted(m["name"] for m in json.loads(r.read().decode("utf-8")).get("models", []))


def ollama_loaded(model) -> bool:
    """Il modello è già in memoria (risposta in pochi secondi) o va caricato (30–60 s)?"""
    with _ollama_open("/api/ps", timeout=3) as r:
        names = [m.get("name", "") for m in json.loads(r.read().decode("utf-8")).get("models", [])]
    return any(n == model or n == model + ":latest" for n in names)


def ollama_preload(model):
    """Carica il modello in memoria senza generare nulla (stesse opzioni della traduzione)."""
    with _ollama_open("/api/generate", {"model": model, "keep_alive": "30m", "options": OLLAMA_OPTIONS},
                      timeout=300) as r:
        r.read()


def ollama_generate(model, prompt, stop=lambda: False, timeout=600.0, on_token=None) -> str:
    """Risposta in streaming: interrompibile fra un token e l'altro (on_token: ogni pezzo)."""
    payload = {"model": model, "prompt": prompt, "stream": True, "keep_alive": "30m",
               "options": OLLAMA_OPTIONS}
    parts = []
    with _ollama_open("/api/generate", payload, timeout) as r:
        for line in r:
            if stop():
                raise InterruptedError
            if not line.strip():
                continue
            msg = json.loads(line)
            if msg.get("error"):
                raise OSError(msg["error"])
            parts.append(msg.get("response", ""))
            if on_token and parts[-1]:
                on_token(parts[-1])
            if msg.get("done"):
                break
    return "".join(parts)


def parse_glossary(text):
    """Una voce per riga: «termine sorgente = traduzione» (anche → , -> o tab). # = commento."""
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for sep in ("=", "→", "->", "\t"):
            if sep in line:
                a, b = (s.strip() for s in line.split(sep, 1))
                if a and b:
                    out.append((a, b))
                break
    return out


def translation_prompt(text, src, tgt, glossary=()):
    """Prompt raccomandato per TranslateGemma, con regole su codici e glossario."""
    S, T = LANG_NAMES.get(src, src), LANG_NAMES.get(tgt, tgt)
    low = text.lower()
    terms = [(a, b) for a, b in glossary if a.lower() in low]
    rules = ("Keep numbers, units, codes, part numbers, abbreviations and product names "
             "exactly as in the original.")
    if terms:
        rules += " Use this terminology: " + "; ".join(f"{a} = {b}" for a, b in terms) + "."
    return (f"You are a professional {S} ({src}) to {T} ({tgt}) translator. Your goal is to "
            f"accurately convey the meaning and nuances of the original {S} text while adhering "
            f"to {T} grammar, vocabulary, and cultural sensitivities. {rules}\n"
            f"Produce only the {T} translation, without any additional explanations or "
            f"commentary. Please translate the following {S} text into {T}:\n\n\n{text}")


def source_text(words) -> str:
    """Testo da tradurre, con le parole spezzate a fine riga ricongiunte."""
    s, glue = "", False
    for k, (_x0, y0, _x1, y1, t) in enumerate(words):
        if s and not glue:
            s += " "
        nxt = words[k + 1] if k + 1 < len(words) else None
        glue = bool(nxt and len(t) > 2 and t[-1] in "-­" and t[-2].isalpha()
                    and nxt[1] > y0 + 0.5 * (y1 - y0) and nxt[4][:1].islower()
                    and nxt[4].lower().rstrip(".,;:") not in HYPHEN_KEEP)
        s += t[:-1] if glue else t
    return s


def _cluster_lines(words):
    """Parole raggruppate per riga visiva, ciascuna riga ordinata da sinistra a destra."""
    lines = []
    for w in sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        cy = (w[1] + w[3]) / 2
        if lines and abs(cy - lines[-1][0]) < 0.4 * (w[3] - w[1]):
            lines[-1][1].append(w)
        else:
            lines.append([cy, [w]])
    return [sorted(ws, key=lambda w: w[0]) for _cy, ws in lines]


def _bbox(words):
    return (min(w[0] for w in words), min(w[1] for w in words),
            max(w[2] for w in words), max(w[3] for w in words))


def block_cells(b: Block):
    """Il blocco intero, oppure le sue celle se PyMuPDF ha unito più celle di una tabella
    (parole sulla stessa riga separate da grandi spazi)."""
    lines = _cluster_lines(b.words)
    segs, split = [], False
    for li, ws in enumerate(lines):
        cur = [ws[0]]
        for a, c in zip(ws, ws[1:]):
            if c[0] - a[2] > max(2.0 * (a[3] - a[1]), 12.0):
                segs.append((li, cur))
                cur, split = [c], True
            else:
                cur.append(c)
        segs.append((li, cur))
    if not split:
        return [b.words]
    cells = []      # [ultima riga, x0, x1, parole]: segmenti sovrapposti di righe consecutive
    for li, ws in segs:
        x0, x1 = min(w[0] for w in ws), max(w[2] for w in ws)
        for c in cells:
            if c[0] == li - 1 and min(c[2], x1) - max(c[1], x0) > 0:
                c[0], c[1], c[2] = li, min(c[1], x0), max(c[2], x1)
                c[3] += ws
                break
        else:
            cells.append([li, x0, x1, list(ws)])
    return [c[3] for c in cells]


def doc_units(d: Document):
    """Unità di traduzione: [(chiave, blocco, rettangolo, parole)].
    Chiave "i" per un blocco intero, "i.k" per la cella k di una riga di tabella."""
    if getattr(d, "_units", None) is None:
        out = []
        for i, b in enumerate(d.blocks):
            if not b.text:
                continue
            cells = block_cells(b) if b.words else [[]]
            for k, ws in enumerate(cells):
                if len(cells) == 1:
                    out.append((str(i), i, tuple(b.rect), ws))
                else:
                    out.append((f"{i}.{k}", i, _bbox(ws), ws))
        d._units = out
    return d._units


def needs_translation(text: str) -> bool:
    """False per blocchi fatti solo di numeri, sigle e codici (es. numeri di pagina)."""
    return bool(re.search(r"[^\W\d_]{2,}", TOKEN_RE.sub(" ", text)))


def missing_tokens(src: str, dst: str):
    """Numeri/sigle/codici dell'originale che non compaiono nella traduzione.
    Le parole maiuscole lunghe (ATTENZIONE, PERICOLO…) si traducono: non contano."""
    return sorted(t for t in lang_free_tokens(src) - lang_free_tokens(dst)
                  if not (t.isalpha() and len(t) > 5))


def split_text(text, limit=TR_MAX_CHARS):
    if len(text) <= limit:
        return [text]
    chunks, cur = [], ""
    for s in re.split(r"(?<=[.!?;:])\s+", text):
        if cur and len(cur) + 1 + len(s) > limit:
            chunks.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}" if cur else s
    if cur:
        chunks.append(cur)
    return chunks


def clean_llm_output(s: str, src: str) -> str:
    s = re.sub(r"<think>.*?</think>", "", s, flags=re.S).strip()
    quotes = "\"'«»“”„"
    if len(s) > 1 and s[0] in quotes and s[-1] in quotes and src[:1] not in quotes:
        s = s[1:-1]
    return " ".join(s.split())


def ollama_translate(text, src, tgt, model, glossary=(), stop=lambda: False, on_partial=None) -> str:
    """on_partial(testo finora): traduzione parziale mentre arriva (per mostrarla subito)."""
    out = []
    for chunk in split_text(text):
        cur = []

        def tok(t, cur=cur):
            cur.append(t)
            on_partial(" ".join(out + ["".join(cur).strip()]))

        r = ollama_generate(model, translation_prompt(chunk, src, tgt, glossary), stop,
                            on_token=tok if on_partial else None)
        out.append(clean_llm_output(r, chunk))
    return " ".join(o for o in out if o)


def translation_models(models):
    """Modelli adatti alla traduzione fra quelli installati (TranslateGemma per primi)."""
    return [m for m in models if "translategemma" in m]


class OllamaProbe(QThread):
    """Controllo in background: Ollama risponde? quali modelli ci sono?"""
    result = Signal(bool, object, str)        # raggiungibile, modelli, errore

    def run(self):
        try:
            self.result.emit(True, ollama_models(), "")
        except OSError as e:
            self.result.emit(False, [], str(e))


class ModelPreloader(QThread):
    """Carica in background il modello di traduzione appena si seleziona del testo,
    così l'attesa del caricamento (30–60 s) avviene mentre si sceglie dal menu."""

    def __init__(self, model, parent=None):
        super().__init__(parent)
        self.model = model

    def run(self):
        try:
            if not ollama_loaded(self.model):
                ollama_preload(self.model)
        except OSError:
            pass


class SelectionTranslateWorker(QThread):
    """Traduzione al volo del testo selezionato (mostrata mentre arriva)."""
    partial = Signal(str)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, text, src, tgt, model, glossary, parent=None):
        super().__init__(parent)
        self.text, self.src, self.tgt, self.model, self.glossary = text, src, tgt, model, glossary

    def run(self):
        stop = self.isInterruptionRequested
        try:
            if not ollama_loaded(self.model):
                self.partial.emit("Caricamento del modello in memoria (30–60 s, solo la prima volta)")
        except OSError:
            pass
        try:
            r = ollama_translate(self.text, self.src, self.tgt, self.model, self.glossary, stop,
                                 on_partial=self.partial.emit)
            if not stop():
                self.done.emit(r)
        except InterruptedError:
            pass
        except Exception as e:
            if not stop():
                self.failed.emit(str(e) or e.__class__.__name__)


def fmt_duration(s: float) -> str:
    s = int(round(s))
    if s < 60:
        return f"{s} s"
    m = s // 60
    return f"{m} min" if m < 60 else f"{m // 60} h {m % 60:02d} min"


def default_output_path(src_path, src, tgt):
    """manuale_it.pdf -> manuale_de.pdf (oppure manuale_de.pdf da manuale.pdf)."""
    folder, name = os.path.split(src_path)
    stem = os.path.splitext(name)[0]
    new, n = re.subn(rf"(?i)(?<![a-z]){re.escape(src)}(?![a-z])", tgt, stem)
    return os.path.join(folder, (new if n else f"{stem}_{tgt}") + ".pdf")


def gen_map_path(pdf_path):
    return os.path.splitext(pdf_path)[0] + GEN_MAP_EXT


def load_gen_map(pdf_path):
    try:
        with open(gen_map_path(pdf_path), encoding="utf-8") as f:
            data = json.load(f)
        return data if data.get("kind") == "generated" else None
    except (OSError, ValueError):
        return None


def save_json_atomic(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


class TranslationCache:
    """Traduzioni per paragrafo (hash documento + blocco + lingue + modello): permette la ripresa."""

    def __init__(self, doc_hash, src, tgt, model):
        safe = re.sub(r"[^\w.-]+", "_", model)
        self.path = os.path.join(CACHE_DIR, f"tr_{doc_hash}_{src}_{tgt}_{safe}_{TR_VERSION}.json")
        try:
            with open(self.path, encoding="utf-8") as f:
                self.data = json.load(f)
        except (OSError, ValueError):
            self.data = {}

    def get(self, i, text):
        e = self.data.get(str(i))
        return e if e and e.get("src") == text else None

    def put(self, i, text, translation, sec):
        self.data[str(i)] = {"src": text, "text": translation,
                             "warn": missing_tokens(text, translation), "sec": round(sec, 2)}

    def rate(self):
        """Secondi per carattere misurati finora (None se non ci sono dati)."""
        timed = [e for e in self.data.values() if e.get("sec")]
        chars = sum(len(e["src"]) for e in timed)
        return sum(e["sec"] for e in timed) / chars if chars else None

    def save(self):
        try:
            save_json_atomic(self.path, self.data)
        except OSError:
            pass


class TranslateWorker(QThread):
    progress = Signal(int, int, float, str)     # fatti, totale, secondi rimanenti (-1 = ?), ultimo

    def __init__(self, cache, items, src, tgt, model, glossary, force=False, parent=None):
        super().__init__(parent)
        self.cache, self.items = cache, items     # items: [(indice blocco, testo)]
        self.src, self.tgt, self.model, self.glossary, self.force = src, tgt, model, glossary, force
        self.error = None
        self.completed = False

    def run(self):
        stop = self.isInterruptionRequested
        todo = [(i, t) for i, t in self.items if self.force or self.cache.get(i, t) is None]
        total, done = len(self.items), len(self.items) - len(todo)
        left = sum(len(t) for _i, t in todo)
        rate = self.cache.rate()
        self.progress.emit(done, total, rate * left if rate else -1.0, "")
        spent, chars, last_save = 0.0, 0, time.monotonic()
        try:
            for i, t in todo:
                if stop():
                    return
                t0 = time.monotonic()
                tr = ollama_translate(t, self.src, self.tgt, self.model, self.glossary, stop)
                dt = time.monotonic() - t0
                self.cache.put(i, t, tr, dt)
                spent, chars, left, done = spent + dt, chars + len(t), left - len(t), done + 1
                if time.monotonic() - last_save > 15:
                    self.cache.save()
                    last_save = time.monotonic()
                self.progress.emit(done, total, spent / max(chars, 1) * left, short(tr, 80))
            self.completed = True
        except InterruptedError:
            pass
        except Exception as e:
            self.error = str(e) or e.__class__.__name__
        finally:
            self.cache.save()


# ---- ricostruzione del PDF tradotto ----
def _drawing_obstacles(page):
    """Segmenti della grafica vettoriale (linee, bordi di riquadri e tabelle)."""
    out = []
    try:
        paths = page.get_drawings()
    except Exception:
        return out
    for p in paths:
        for it in p.get("items", ()):
            if it[0] == "l":
                a, b = it[1], it[2]
                out.append((min(a.x, b.x), min(a.y, b.y), max(a.x, b.x), max(a.y, b.y)))
            elif it[0] in ("re", "qu"):
                r = it[1].rect if it[0] == "qu" else fitz.Rect(it[1]).normalize()
                out += [(r.x0, r.y0, r.x1, r.y0), (r.x0, r.y1, r.x1, r.y1),
                        (r.x0, r.y0, r.x0, r.y1), (r.x1, r.y0, r.x1, r.y1)]
            elif it[0] == "c":
                xs, ys = [q.x for q in it[1:]], [q.y for q in it[1:]]
                out.append((min(xs), min(ys), max(xs), max(ys)))
    return out


def _free_box(rect, obstacles, bounds, centered=False):
    """Allarga il riquadro del blocco verso destra (e in basso) finché c'è spazio libero."""
    x0, y0, x1, y1 = rect
    bx0, _by0, bx1, by1 = bounds
    right, left = max(x1, bx1), min(x0, bx0)
    for o in obstacles:
        if o[1] < y1 - 0.5 and o[3] > y0 + 0.5:          # sovrapposti in verticale
            if o[0] >= x1 - 0.5:
                right = min(right, o[0] - 2)
            if o[2] <= x0 + 0.5:
                left = max(left, o[2] + 2)
    right, left = max(right, x1), min(left, x0)
    if centered:
        cx = (x0 + x1) / 2
        half = max(min(right - cx, cx - left), (x1 - x0) / 2)
        x0, x1 = cx - half, cx + half
    else:
        x1 = right
    bottom = max(y1, by1)
    for o in obstacles:
        if o[0] < x1 - 0.5 and o[2] > x0 + 0.5 and o[1] >= y1 - 0.5:
            bottom = min(bottom, o[1] - 1)
    return x0, y0, x1, max(bottom, y1)


def _block_lines(words):
    """Righe: [(y centro, x0, x1)]."""
    return [((ws[0][1] + ws[0][3]) / 2, ws[0][0], max(w[2] for w in ws)) for ws in _cluster_lines(words)]


def _block_style(rect, words, spans, bounds):
    """Stile prevalente: dimensione, colore, grassetto/corsivo, interlinea, allineamento."""
    x0, y0, x1, y1 = rect
    cnt = Counter()
    for s in spans:
        sx0, sy0, sx1, sy1 = s["bbox"]
        cx, cy = (sx0 + sx1) / 2, (sy0 + sy1) / 2
        if x0 - 1 <= cx <= x1 + 1 and y0 - 1 <= cy <= y1 + 1:
            f = s["font"].lower()
            key = (round(s["size"] * 2) / 2, s["color"],
                   bool(s["flags"] & 16) or any(k in f for k in ("bold", "black", "heavy")),
                   bool(s["flags"] & 2) or "italic" in f or "oblique" in f,
                   bool(s["flags"] & 4) or (any(k in f for k in ("times", "serif", "georgia", "garamond"))
                                            and "sans" not in f))
            cnt[key] += len(s["text"].strip())
    size, color, bold, italic, serif = cnt.most_common(1)[0][0] if cnt else (10.0, 0, False, False, False)
    lines = _block_lines(words)
    lh = 1.2
    if len(lines) > 1:
        diffs = sorted(lines[k + 1][0] - lines[k][0] for k in range(len(lines) - 1))
        lh = min(max(diffs[len(diffs) // 2] / max(size, 1.0), 1.0), 1.8)
    bx0, _by0, bx1, _by1 = bounds
    colw, colc = max(bx1 - bx0, 1.0), (bx0 + bx1) / 2      # colonna di testo della pagina
    cx, align = (x0 + x1) / 2, "left"
    if len(lines) >= 3 and all(x1 - ln[2] < 2.5 for ln in lines[:-1]):
        align = "justify"
    elif (abs(cx - colc) < 0.03 * colw and x0 - bx0 > 0.08 * colw
          and all(abs((ln[1] + ln[2]) / 2 - cx) < 2 for ln in lines)):
        align = "center"
    return {"size": max(size, 1.0), "color": color & 0xFFFFFF, "bold": bold, "italic": italic,
            "serif": serif, "lh": lh, "align": align}


def _ocr_font_size(words) -> float:
    """Pagine scansionate: dimensione del carattere stimata dall'altezza delle righe
    (le parole OCR sono rettangoli stretti attorno ai glifi: ~0,93 em per una parola
    con ascendenti e discendenti, la più alta della riga)."""
    hs = sorted(max(w[3] - w[1] for w in ln) for ln in _cluster_lines(words))
    return max(hs[len(hs) // 2] / 0.93, 4.0) if hs else 10.0


def _background(page, rect):
    """Colore di fondo attorno a un riquadro (mediana dei pixel: il testo è la minoranza)."""
    pix = page.get_pixmap(clip=fitz.Rect(rect) + (-3, -3, 3, 3), dpi=72, colorspace=fitz.csRGB)
    px = np.frombuffer(pix.samples, dtype=np.uint8).reshape(-1, pix.n)[:, :3]
    return tuple(float(v) / 255 for v in np.median(px, axis=0)) if len(px) else (1.0, 1.0, 1.0)


def _apply_redactions(page):
    try:
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                              graphics=fitz.PDF_REDACT_LINE_ART_NONE)
    except (TypeError, AttributeError):   # PyMuPDF < 1.24.2
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)


def _insert_translation(page, box, text, st, top, scratch):
    """Scrive il testo nel riquadro; la prima riga viene allineata a «top» (bordo superiore
    delle parole originali) misurando prima l'impaginazione su una pagina di prova."""
    css = ("body {margin: 0; padding: 0;} "       # MuPDF non applica il selettore *
           f"p {{margin: 0; padding: 0; font-family: {'serif' if st['serif'] else 'sans-serif'}; "
           f"font-size: {st['size']:g}px; line-height: {st['lh']:.2f}; color: #{st['color']:06x}; "
           f"text-align: {st['align']};"
           f"{' font-weight: bold;' if st['bold'] else ''}{' font-style: italic;' if st['italic'] else ''}}}")
    body = f"<p>{html.escape(text)}</p>"
    rect = fitz.Rect(box)
    probe = scratch.new_page(width=page.rect.width, height=page.rect.height)
    probe.insert_htmlbox(rect, body, css=css, scale_low=0)
    words = probe.get_text("words")
    scratch.delete_page(-1)
    if words:
        dy = top - min(w[1] for w in words)
        dy = max(-0.6 * st["size"], min(dy, 0.6 * st["size"]))
        rect = fitz.Rect(rect.x0, rect.y0 + dy, rect.x1, rect.y1 + min(dy, 0.0))
    _spare, scale = page.insert_htmlbox(rect, body, css=css, scale_low=0)
    return scale


def render_translated_pdf(src: Document, translations: dict, out_path: str, pages=None) -> dict:
    """Copia il PDF sorgente sostituendo il testo delle unità tradotte (immagini e grafica
    restano intatte). pages: solo queste pagine (anteprima). -> {chiave: riquadro usato}."""
    out = fitz.open(src.path)
    scratch = fitz.open()
    boxes = {}
    units = {key: (src.blocks[i].page, rect, ws) for key, i, rect, ws in doc_units(src)}
    page_rects = defaultdict(dict)
    for key, (p, rect, _ws) in units.items():
        page_rects[p][key] = rect
    by_page = defaultdict(list)
    for key, tr in translations.items():
        u = units.get(key)
        if tr and u and u[2]:
            by_page[u[0]].append(key)
    for pno in sorted(by_page):
        if pages is not None and pno not in pages:
            continue
        page = out[pno]
        H = page.rect.height
        scanned = pno in getattr(src, "ocr_pages", ())   # testo riconosciuto con l'OCR
        rects = page_rects[pno]
        rv = list(rects.values())
        bounds = (min(r[0] for r in rv), 0.0, max(r[2] for r in rv),
                  max(max(r[3] for r in rv), H - min(r[1] for r in rv)))
        try:
            images = [tuple(im["bbox"]) for im in page.get_image_info()]
        except Exception:
            images = []
        if scanned:     # la scansione occupa tutta la pagina: non è un ostacolo
            images = [im for im in images if abs(fitz.Rect(im) & page.rect) < 0.3 * abs(page.rect)]
        others = _drawing_obstacles(page) + images
        spans = [] if scanned else [s for blk in page.get_text("dict")["blocks"]
                                    for ln in blk.get("lines", ()) for s in ln["spans"] if s["text"].strip()]
        plan, covers = [], []
        for key in by_page[pno]:
            _p, rect, ws = units[key]
            st = _block_style(rect, ws, spans, bounds)
            if scanned:
                st["size"] = _ocr_font_size(ws)
            obst = others + [r for k, r in rects.items() if k != key]
            plan.append((key, _free_box(rect, obst, bounds, st["align"] == "center"), st))
            if scanned:     # il testo è nell'immagine: lo si copre col colore di fondo
                r = fitz.Rect(_bbox(ws)) + (-1.5, -1.5, 1.5, 1.5)
                covers.append((r, _background(page, r)))
                continue
            for line in _cluster_lines(ws):          # si tolgono solo le righe dell'unità
                x0, y0, x1, y1 = _bbox(line)
                h = y1 - y0
                page.add_redact_annot(fitz.Rect(x0, y0 + 0.2 * h, x1, y1 - 0.2 * h), fill=False)
        if covers:
            for r, bg in covers:
                page.draw_rect(r, color=None, fill=bg, overlay=True)
        else:
            _apply_redactions(page)
        for key, box, st in plan:
            top = min(w[1] for w in units[key][2])
            _insert_translation(page, box, translations[key], st, top, scratch)
            boxes[key] = [round(v, 2) for v in box]
    scratch.close()
    if pages is not None:
        out.select(sorted(p for p in pages if p < len(out)))
    out.save(out_path, garbage=3, deflate=True)
    out.close()
    return boxes


def exact_anchors(entries, tgt: Document):
    """Ancore esatte sorgente -> PDF generato, dai riquadri del file di corrispondenza."""
    by_page = defaultdict(list)
    for e in entries:
        by_page[e["page"]].append(e)
    anchors = []
    for j, b in enumerate(tgt.blocks):
        if not b.text:
            continue
        cx, cy = (b.rect[0] + b.rect[2]) / 2, (b.rect[1] + b.rect[3]) / 2
        hits = [e for e in by_page.get(b.page, ())
                if e["box"][0] - 1 <= cx <= e["box"][2] + 1 and e["box"][1] - 1 <= cy <= e["box"][3] + 1]
        if hits:
            e = min(hits, key=lambda e: (e["box"][2] - e["box"][0]) * (e["box"][3] - e["box"][1]))
            anchors.append((e["i"], j, 1.0))
    anchors.sort()
    return _longest_increasing(anchors)


# ---- dialoghi ----
class GenerateDialog(QDialog):
    def __init__(self, win, models, ollama_error):
        super().__init__(win)
        self.win = win
        self.models = models
        self.ollama_error = ollama_error
        self.gl = dict(win.glossaries)
        self._pair = None
        self._out_auto = True
        self.setWindowTitle("Genera lingua mancante")
        self.setMinimumWidth(560)

        self.src = QComboBox()
        for lang in win.docs:
            self.src.addItem(lang.upper(), lang)
        if win.current in win.docs:
            self.src.setCurrentIndex(list(win.docs).index(win.current))
        self.tgt = QComboBox()
        self.tgt.setEditable(True)
        self.tgt.addItems([l for l in LANGS if l not in win.docs])
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.addItems(list(dict.fromkeys([m for m in models if "translategemma" in m]
                                               + TR_MODELS + models)))
        self.test = QCheckBox("Solo prova sulle prime")
        self.pages = QSpinBox()
        self.pages.setRange(1, 50)
        self.pages.setValue(3)
        row_test = QHBoxLayout()
        row_test.addWidget(self.test)
        row_test.addWidget(self.pages)
        row_test.addWidget(QLabel("pagine, con stima del tempo totale"))
        row_test.addStretch(1)
        self.out = QLineEdit()
        browse = QPushButton("Sfoglia…")
        browse.clicked.connect(self._browse)
        row_out = QHBoxLayout()
        row_out.addWidget(self.out, 1)
        row_out.addWidget(browse)
        self.gloss = QPlainTextEdit()
        self.gloss.setPlaceholderText("Una voce per riga, ad esempio:\nquadro elettrico = Schaltschrank\n"
                                      "sezionatore = Trennschalter\n# le righe con # sono commenti")
        self.gloss.setMinimumHeight(110)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setTextFormat(Qt.RichText)

        form = QFormLayout()
        form.addRow("Lingua sorgente:", self.src)
        form.addRow("Lingua da generare:", self.tgt)
        form.addRow("Modello Ollama:", self.model)
        form.addRow("", row_test)
        form.addRow("File PDF generato:", row_out)
        form.addRow("Glossario:", self.gloss)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Avvia")
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.info)
        lay.addWidget(bb)

        self.out.textEdited.connect(lambda _t: setattr(self, "_out_auto", False))
        self.src.currentIndexChanged.connect(self._update)
        self.tgt.currentTextChanged.connect(self._update)
        self.model.currentTextChanged.connect(self._update)
        self._update(first=True)

    def _langs(self):
        return self.src.currentData(), self.tgt.currentText().strip().lower()

    def _browse(self):
        path, _ = QFileDialog.getSaveFileName(self, "PDF generato", self.out.text(), "PDF (*.pdf)")
        if path:
            self.out.setText(path if path.lower().endswith(".pdf") else path + ".pdf")
            self._out_auto = False

    def _update(self, *_a, first=False):
        src, tgt = self._langs()
        model = self.model.currentText().strip()
        pair = f"{src}>{tgt}"
        if pair != self._pair:                         # glossario della coppia di lingue
            if self._pair is not None:
                self.gl[self._pair] = self.gloss.toPlainText()
            self.gloss.setPlainText(self.gl.get(pair, ""))
            self._pair = pair
        d = self.win.docs[src]
        if self._out_auto and tgt:
            self.out.setText(default_output_path(d.path, src, tgt))
        items = self.win._translatable_items(src)
        cache = TranslationCache(d.hash, src, tgt, model)
        done = sum(1 for i, t in items if cache.get(i, t))
        if first:
            self.test.setChecked(done == 0)
        msgs = [f"{len(d.page_sizes)} pagine, {len(items)} paragrafi da tradurre"
                + (f" — <b>già tradotti {done}</b> (si riprende da lì)" if done else "") + "."]
        if self.ollama_error:
            msgs.append(f"<span style='color:#c0392b'>⚠ {html.escape(self.ollama_error)}<br>"
                        "Installa Ollama da https://ollama.com, avvialo e poi esegui: "
                        f"<code>ollama pull {html.escape(model or TR_MODELS[0])}</code></span>")
        elif model and model not in self.models and f"{model}:latest" not in self.models:
            msgs.append(f"<span style='color:#c0392b'>⚠ Modello non installato. Esegui: "
                        f"<code>ollama pull {html.escape(model)}</code></span>")
        self.info.setText("<br>".join(msgs))

    def _accept(self):
        src, tgt = self._langs()
        out = os.path.abspath(self.out.text().strip()) if self.out.text().strip() else ""
        err = None
        if not re.fullmatch(r"[a-z]{2,3}", tgt or ""):
            err = "Indica la lingua da generare con il codice di 2–3 lettere (es. de, fr, en)."
        elif tgt == src:
            err = "La lingua da generare deve essere diversa dalla sorgente."
        elif not self.model.currentText().strip():
            err = "Indica il modello Ollama."
        elif not out.lower().endswith(".pdf"):
            err = "Indica il file PDF da generare."
        elif any(os.path.normcase(d.path) == os.path.normcase(out)
                 for l, d in self.win.docs.items() if l != tgt):
            err = "Il file indicato è già un documento del progetto in un'altra lingua."
        if err:
            QMessageBox.warning(self, APP_NAME, err)
            return
        self.gl[self._pair] = self.gloss.toPlainText()
        self.accept()

    def values(self):
        src, tgt = self._langs()
        return {"src": src, "tgt": tgt, "model": self.model.currentText().strip(),
                "test": self.test.isChecked(), "test_pages": self.pages.value(),
                "out_path": os.path.abspath(self.out.text().strip()),
                "glossaries": {k: v for k, v in self.gl.items() if v.strip()}}


class TranslationEditDialog(QDialog):
    def __init__(self, parent, src_lang, tgt_lang, original, translation, warn):
        super().__init__(parent)
        self.setWindowTitle("Modifica traduzione del paragrafo")
        self.resize(620, 420)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"Originale ({src_lang.upper()}):"))
        orig = QPlainTextEdit(original)
        orig.setReadOnly(True)
        orig.setMaximumHeight(150)
        lay.addWidget(orig)
        lay.addWidget(QLabel(f"Traduzione ({tgt_lang.upper()}):"))
        self.edit = QPlainTextEdit(translation)
        lay.addWidget(self.edit, 1)
        if warn:
            w = QLabel("⚠ Non ritrovati nella traduzione: " + ", ".join(warn))
            w.setStyleSheet("color:#c0392b;")
            w.setWordWrap(True)
            lay.addWidget(w)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Applica e rigenera la pagina")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def text(self):
        return " ".join(self.edit.toPlainText().split())


# --------------------------------------------------------------------------- #
# Elementi grafici delle annotazioni
# --------------------------------------------------------------------------- #
class HighlightItem(QGraphicsItem):
    """Evidenziazione in stile pennarello (moltiplica: il testo resta nero)."""

    def __init__(self, ann_id, rects, color, approx, tip):
        super().__init__()
        self.ann_id = ann_id
        self.rects = rects
        self.color = QColor(color)
        self.approx = approx
        self.active = False
        br = QRectF()
        for r in rects:
            br = br.united(r)
        self._br = br.adjusted(-2, -2, 2, 2)
        self.setZValue(2)
        self.setToolTip(tip)

    def boundingRect(self):
        return self._br

    def shape(self):
        path = QPainterPath()
        for r in self.rects:
            path.addRect(r)
        return path

    def paint(self, p, _opt, _w=None):
        p.save()
        p.setCompositionMode(QPainter.CompositionMode_Multiply)
        c = QColor(self.color)
        if self.approx:
            c = c.lighter(115)
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        for r in self.rects:
            p.drawRect(r)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        if self.approx:
            p.setPen(QPen(self.color.darker(170), 0, Qt.DashLine))
            for r in self.rects:
                p.drawLine(r.bottomLeft(), r.bottomRight())
        if self.active:
            p.setPen(QPen(QColor(30, 90, 210), 0))
            p.setBrush(Qt.NoBrush)
            for r in self.rects:
                p.drawRect(r.adjusted(-1, -1, 1, 1))
        p.restore()


class NoteIconItem(QGraphicsItem):
    def __init__(self, ann_id, x, y, color, tip):
        super().__init__()
        self.ann_id = ann_id
        self.color = QColor(color)
        self.active = False
        self.setPos(x, y)
        self.setZValue(4)
        self.setToolTip(tip)
        self.setCursor(Qt.PointingHandCursor)

    def boundingRect(self):
        return QRectF(-1, -1, NOTE_ICON + 2, NOTE_ICON + 2)

    def paint(self, p, _opt, _w=None):
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(self.color)
        pen = QPen(QColor(30, 90, 210), 0) if self.active else QPen(QColor(0, 0, 0, 160), 0)
        p.setPen(pen)
        p.drawRoundedRect(QRectF(0.5, 0.5, NOTE_ICON - 1, NOTE_ICON - 1), 2.5, 2.5)
        p.setPen(QPen(QColor(0, 0, 0, 170), 0))
        for i in range(3):
            y = 4 + i * 3
            p.drawLine(QPointF(3, y), QPointF(NOTE_ICON - 3, y))
        p.restore()


class TranslationPopup(QFrame):
    """Riquadro stile nota con la traduzione della selezione: figlio della vista, ancorato
    alla selezione (segue scorrimento e zoom) e chiuso quando la selezione sparisce."""
    retarget = Signal(str)           # nuova lingua di destinazione
    save_note = Signal(str)          # salva la traduzione come evidenziazione con nota

    def __init__(self, src, tgt, model, parent):
        super().__init__(parent)
        self.worker = None
        self.result = ""
        self.setObjectName("trpop")
        self.setStyleSheet(
            "#trpop {background: #FFF6B3; border: 1px solid #C9A800; border-radius: 5px;}"
            "#trpop QLabel {color: #222; background: transparent;}"
            "#trpop QLabel#trhead {color: #6b5a00; font-size: 11px;}"
            "#trpop QLabel#trerr {color: #B00020;}")
        self.setFixedWidth(360)
        self.setCursor(Qt.ArrowCursor)
        sh = QGraphicsDropShadowEffect(self)
        sh.setBlurRadius(14)
        sh.setOffset(0, 2)
        sh.setColor(QColor(0, 0, 0, 90))
        self.setGraphicsEffect(sh)

        head = QLabel(f"Traduzione {src.upper()} →")
        head.setObjectName("trhead")
        self.tgt = QComboBox()
        self.tgt.addItems([l.upper() for l in LANGS if l != src])
        self.tgt.setCurrentText(tgt.upper())
        self.tgt.setToolTip("Lingua di destinazione")
        self.tgt.currentTextChanged.connect(lambda t: self.retarget.emit(t.lower()))
        model_lbl = QLabel(model)
        model_lbl.setObjectName("trhead")
        close = QToolButton()
        close.setText("✕")
        close.setAutoRaise(True)
        close.setToolTip("Chiudi (Esc)")
        close.clicked.connect(self.close_requested)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        for w in (head, self.tgt):
            top.addWidget(w)
        top.addStretch(1)
        top.addWidget(model_lbl)
        top.addWidget(close)

        self.text = QLabel("Traduzione in corso…")
        self.text.setWordWrap(True)
        self.text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.text.setMinimumHeight(20)
        self.btn_copy = QPushButton("Copia")
        self.btn_copy.clicked.connect(lambda: QApplication.clipboard().setText(self.result))
        self.btn_note = QPushButton("Salva come nota")
        self.btn_note.setToolTip("Evidenzia la selezione e salva la traduzione nella nota")
        self.btn_note.clicked.connect(lambda: self.save_note.emit(self.result))
        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.addStretch(1)
        bottom.addWidget(self.btn_copy)
        bottom.addWidget(self.btn_note)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 8, 8)
        lay.setSpacing(6)
        lay.addLayout(top)
        lay.addWidget(self.text)
        lay.addLayout(bottom)
        self._set_ready(False)

    def _set_ready(self, ok):
        self.btn_copy.setEnabled(ok)
        self.btn_note.setEnabled(ok)

    def _refit(self):
        self.adjustSize()
        view = self.parent().parent() if self.parent() else None
        if isinstance(view, PdfView):
            view.place_popup()

    def set_partial(self, s):
        self.text.setObjectName("")
        self.text.setText(s + " …")
        self._refit()

    def set_result(self, s):
        self.result = s
        self.text.setText(s or "(traduzione vuota)")
        self._set_ready(bool(s))
        self._refit()

    def set_error(self, msg):
        self.result = ""
        self.text.setObjectName("trerr")
        self.text.setStyleSheet("color: #B00020;")
        self.text.setText(f"Traduzione non riuscita: {msg}")
        self._set_ready(False)
        self._refit()

    def close_requested(self):
        view = self.parent().parent() if self.parent() else None
        if isinstance(view, PdfView):
            view.close_popup()

    def stop(self):
        try:
            if self.worker is not None and self.worker.isRunning():
                self.worker.requestInterruption()
        except RuntimeError:          # worker già terminato e distrutto
            pass


# --------------------------------------------------------------------------- #
# Vista PDF (scorrimento continuo, rendering pigro, selezione testo)
# --------------------------------------------------------------------------- #
class PdfView(QGraphicsView):
    positionChanged = Signal()
    selectionMade = Signal(int, int, object)                  # g0, g1, QPoint globale
    annotationClicked = Signal(str)
    annotationActivated = Signal(str)                         # doppio clic
    contextRequested = Signal(str, int, float, float, object)  # ann_id|"", pag, x, y, QPoint
    pageClicked = Signal(int, float, float)

    def __init__(self, document: Document, parent=None):
        super().__init__(parent)
        self.d = document
        self.zoom = 1.0
        self.place_mode = False
        self.sel = None                    # (g0, g1) parole selezionate
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setBackgroundBrush(QColor("#4b5058"))
        self.setRenderHint(QPainter.SmoothPixmapTransform, True)
        self.setTransformationAnchor(QGraphicsView.NoAnchor)
        self.setResizeAnchor(QGraphicsView.NoAnchor)
        self.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.viewport().setMouseTracking(True)

        self.offsets, self.page_x = [], []
        self.max_w = max((w for w, _ in document.page_sizes), default=595.0)
        y = 0.0
        for w, h in document.page_sizes:
            x = (self.max_w - w) / 2
            r = QGraphicsRectItem(x, y, w, h)
            r.setBrush(QBrush(Qt.white))
            r.setPen(QPen(QColor(0, 0, 0, 70), 0))
            self._scene.addItem(r)
            self.offsets.append(y)
            self.page_x.append(x)
            y += h + PAGE_GAP
        self._scene.setSceneRect(-PAGE_GAP, -PAGE_GAP, self.max_w + 2 * PAGE_GAP, y + PAGE_GAP)

        self._pix: dict[int, QGraphicsPixmapItem] = {}
        self._ann_items: list = []
        self._sel_items: list = []
        self._active_id = None
        self._press = None                 # (scene point, ann_id, word)
        self._dragging = False
        self._pan = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._render_visible)
        self.popup: TranslationPopup | None = None      # traduzione della selezione
        self.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self.horizontalScrollBar().valueChanged.connect(self._schedule)
        self.horizontalScrollBar().valueChanged.connect(self.place_popup)

    # ---- coordinate ----
    def page_rect_to_scene(self, p, x0, y0, x1, y1) -> QRectF:
        return QRectF(self.page_x[p] + x0, self.offsets[p] + y0, x1 - x0, y1 - y0)

    def scene_to_page(self, sp):
        n = len(self.offsets)
        pno = min(max(0, bisect.bisect_right(self.offsets, sp.y()) - 1), n - 1)
        w, h = self.d.page_sizes[pno]
        x = min(max(sp.x() - self.page_x[pno], 0.0), w)
        y = min(max(sp.y() - self.offsets[pno], 0.0), h)
        return pno, x, y

    # ---- rendering ----
    def _schedule(self, *_):
        self._timer.start()

    def _on_scroll(self, *_):
        self._schedule()
        self.place_popup()
        self.positionChanged.emit()

    # ---- riquadro della traduzione, ancorato alla selezione ----
    def show_popup(self, popup):
        self.close_popup()
        self.popup = popup
        popup.adjustSize()
        popup.show()
        self.place_popup()

    def close_popup(self):
        p, self.popup = getattr(self, "popup", None), None
        if p is not None:
            p.stop()
            p.hide()
            p.deleteLater()

    def place_popup(self, *_):
        p = getattr(self, "popup", None)
        if p is None or not self.sel:
            return
        r = QRectF()
        for pg, x0, y0, x1, y1 in self.d.rects_for_words(*self.sel):
            r = r.united(self.page_rect_to_scene(pg, x0, y0, x1, y1))
        r = self.mapFromScene(r).boundingRect()
        vp = self.viewport().rect()
        w, h = p.width(), p.height()
        x = min(max(r.left(), 4), max(vp.width() - w - 4, 4))
        y = r.bottom() + 6                                   # sotto la selezione…
        if y + h > vp.height() - 4 and r.top() - h - 6 >= 4:
            y = r.top() - h - 6                              # …o sopra, se sotto non c'è spazio
        p.move(int(x), int(y))
        p.setVisible(r.intersects(vp))                       # selezione fuori vista: nascosto

    def showEvent(self, e):
        super().showEvent(e)
        self._schedule()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._schedule()
        self.place_popup()

    def _visible_pages(self):
        r = self.mapToScene(self.viewport().rect()).boundingRect()
        n = len(self.offsets)
        first = min(max(0, bisect.bisect_right(self.offsets, r.top()) - 1), n - 1)
        last = min(max(0, bisect.bisect_right(self.offsets, r.bottom()) - 1), n - 1)
        return first, last

    def _render_visible(self):
        if not self.isVisible() or not self.offsets:
            return
        first, last = self._visible_pages()
        n = len(self.offsets)
        for p in range(max(0, first - 1), min(n, last + 2)):
            self._render_page(p)
        for p in list(self._pix):
            if p < first - 2 or p > last + 2:
                self._scene.removeItem(self._pix.pop(p))

    def _render_page(self, pno: int):
        scale = self.zoom * self.devicePixelRatioF()
        item = self._pix.get(pno)
        if item is not None and abs(item.data(0) - scale) < 1e-3:
            return
        pix = self.d.doc[pno].get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()
        if item is None:
            item = QGraphicsPixmapItem()
            item.setZValue(1)
            item.setTransformationMode(Qt.SmoothTransformation)
            self._scene.addItem(item)
            self._pix[pno] = item
        item.setPixmap(QPixmap.fromImage(img))
        item.setScale(1 / scale)
        item.setPos(self.page_x[pno], self.offsets[pno])
        item.setData(0, scale)

    # ---- zoom ----
    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            self.set_zoom(self.zoom * 1.15 ** (e.angleDelta().y() / 120))
            e.accept()
        else:
            super().wheelEvent(e)

    def set_zoom(self, z: float, keep_position: bool = True):
        z = max(MIN_ZOOM, min(MAX_ZOOM, z))
        pos = self.get_position() if keep_position else None
        self.zoom = z
        self.setTransform(QTransform.fromScale(z, z))
        if pos:
            self.set_position(pos[0], pos[2])
        self._schedule()
        self.place_popup()

    def fit_width(self):
        vw = max(self.viewport().width(), 200)
        self.set_zoom((vw - 4) / (self.max_w + 2 * PAGE_GAP))

    # ---- posizione ----
    def get_position(self):
        """(pagina, x, y) del punto di lettura, in coordinate della pagina."""
        vp = self.viewport()
        p = self.mapToScene(vp.width() // 2, int(vp.height() * ANCHOR_RATIO))
        n = len(self.offsets)
        pno = min(max(0, bisect.bisect_right(self.offsets, p.y()) - 1), n - 1)
        h = self.d.page_sizes[pno][1]
        y = min(max(p.y() - self.offsets[pno], 0.0), h)
        return pno, p.x() - self.page_x[pno], y

    def set_position(self, pno: int, y: float):
        vp = self.viewport()
        cx = self.mapToScene(vp.rect().center()).x()
        cy = self.offsets[pno] + y + vp.height() * (0.5 - ANCHOR_RATIO) / self.zoom
        self.centerOn(cx, cy)
        self._schedule()

    def goto_page(self, pno: int):
        vp = self.viewport()
        cx = self.mapToScene(vp.rect().center()).x()
        self.centerOn(cx, self.offsets[pno] - PAGE_GAP / 2 + vp.height() / 2 / self.zoom)
        self._schedule()

    def current_page(self) -> int:
        return self.get_position()[0]

    def flash_rects(self, rects, ms: int = 1200):
        items = []
        for p, x0, y0, x1, y1 in rects:
            r = QGraphicsRectItem(self.page_rect_to_scene(p, x0 - 2, y0 - 2, x1 + 2, y1 + 2))
            r.setBrush(QColor(255, 200, 0, 60))
            r.setPen(QPen(QColor(255, 140, 0, 220), 0))
            r.setZValue(5)
            self._scene.addItem(r)
            items.append(r)

        def _remove():
            for it in items:
                if it.scene() is not None:
                    self._scene.removeItem(it)
        QTimer.singleShot(ms, _remove)

    def flash_block(self, k: int, ms: int = 1200):
        b = self.d.blocks[k]
        self.flash_rects([[b.page, *b.rect]], ms)

    # ---- selezione ----
    def set_selection(self, g0, g1):
        self.clear_selection()
        self.sel = (min(g0, g1), max(g0, g1))
        for p, x0, y0, x1, y1 in self.d.rects_for_words(*self.sel):
            r = QGraphicsRectItem(self.page_rect_to_scene(p, x0, y0, x1, y1))
            r.setBrush(QColor(40, 110, 230, 70))
            r.setPen(Qt.NoPen)
            r.setZValue(3)
            self._scene.addItem(r)
            self._sel_items.append(r)

    def clear_selection(self):
        self.close_popup()               # la traduzione vive solo con la sua selezione
        for it in self._sel_items:
            self._scene.removeItem(it)
        self._sel_items = []
        self.sel = None

    def selected_text(self):
        return self.d.text_for_words(*self.sel) if self.sel else ""

    # ---- annotazioni ----
    def set_annotations(self, renders):
        for it in self._ann_items:
            self._scene.removeItem(it)
        self._ann_items = []
        for r in renders:
            tip = r["note"] or ""
            if r["approx"]:
                tip = (tip + "\n" if tip else "") + "(posizione stimata dalla traduzione)"
            if r["rects"]:
                rects = [self.page_rect_to_scene(*rc) for rc in r["rects"]]
                it = HighlightItem(r["id"], rects, r["color"], r["approx"], tip)
                it.active = r["id"] == self._active_id
                self._scene.addItem(it)
                self._ann_items.append(it)
            if r["icon"]:
                p, x, y = r["icon"]
                it = NoteIconItem(r["id"], self.page_x[p] + x, self.offsets[p] + y,
                                  r["color"], tip or "Nota")
                it.active = r["id"] == self._active_id
                self._scene.addItem(it)
                self._ann_items.append(it)

    def set_active(self, ann_id):
        self._active_id = ann_id
        for it in self._ann_items:
            it.active = it.ann_id == ann_id
            it.update()

    def annotation_at(self, sp):
        for it in self._scene.items(sp):
            if isinstance(it, (NoteIconItem, HighlightItem)) and it.contains(it.mapFromScene(sp)):
                return it.ann_id
        return None

    # ---- mouse ----
    def mousePressEvent(self, e):
        if e.button() == Qt.MiddleButton:
            self._pan = e.position()
            self.viewport().setCursor(Qt.ClosedHandCursor)
            return
        if e.button() == Qt.LeftButton:
            sp = self.mapToScene(e.position().toPoint())
            pno, x, y = self.scene_to_page(sp)
            self._press = (sp, self.annotation_at(sp), self.d.word_at(pno, x, y))
            self._dragging = False
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._pan is not None:
            d = e.position() - self._pan
            self._pan = e.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(d.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(d.y()))
            return
        pt = e.position().toPoint()
        sp = self.mapToScene(pt)
        if self._press and e.buttons() & Qt.LeftButton and not self.place_mode:
            start_sp, _ann, g_start = self._press
            if not self._dragging and (sp - start_sp).manhattanLength() * self.zoom > 4:
                self._dragging = True
            if self._dragging and g_start is not None:
                h = self.viewport().height()   # scorrimento automatico ai bordi
                if pt.y() < 0 or pt.y() > h:
                    edge = pt.y() if pt.y() < 0 else pt.y() - h
                    self.verticalScrollBar().setValue(self.verticalScrollBar().value() + int(edge / 2))
                    sp = self.mapToScene(pt)
                pno, x, y = self.scene_to_page(sp)
                g = self.d.word_at(pno, x, y)
                if g is not None:
                    self.set_selection(g_start, g)
            return
        # cursore al passaggio
        if self.place_mode:
            self.viewport().setCursor(Qt.CrossCursor)
        else:
            pno, x, y = self.scene_to_page(sp)
            over_word = self.d.word_at(pno, x, y, nearest=False) is not None
            self.viewport().setCursor(Qt.IBeamCursor if over_word else Qt.ArrowCursor)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MiddleButton and self._pan is not None:
            self._pan = None
            self.viewport().setCursor(Qt.ArrowCursor)
            return
        if e.button() == Qt.LeftButton and self._press:
            sp, ann_id, _g = self._press
            self._press = None
            if self._dragging:
                self._dragging = False
                if self.sel:
                    self.selectionMade.emit(self.sel[0], self.sel[1], e.globalPosition().toPoint())
                return
            self.clear_selection()
            if ann_id and not self.place_mode:
                self.annotationClicked.emit(ann_id)
            else:
                self.pageClicked.emit(*self.scene_to_page(sp))
            return
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        sp = self.mapToScene(e.position().toPoint())
        ann_id = self.annotation_at(sp)
        if ann_id:
            self.annotationActivated.emit(ann_id)
            return
        pno, x, y = self.scene_to_page(sp)
        g = self.d.word_at(pno, x, y, nearest=False)
        if g is not None:                       # doppio clic su una parola: la seleziona
            self.set_selection(g, g)
            self.selectionMade.emit(g, g, e.globalPosition().toPoint())

    def contextMenuEvent(self, e):
        sp = self.mapToScene(e.pos())
        pno, x, y = self.scene_to_page(sp)
        self.contextRequested.emit(self.annotation_at(sp) or "", pno, x, y, e.globalPos())


# --------------------------------------------------------------------------- #
# Finestra principale
# --------------------------------------------------------------------------- #
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1300, 900)
        self.docs: dict[str, Document] = {}
        self.views: dict[str, PdfView] = {}
        self.mappings: dict[tuple, tuple] = {}   # (la, lb) -> (xs, ys)
        self.align_info: dict[tuple, str] = {}
        self.current: str | None = None
        self.project_path: str | None = None
        self.annotations: list = []
        self._undo: list = []
        self.cur_color = COLORS[0][1]
        self._relink = None                      # (ann_id, lang) in correzione
        self._intra_cache: dict = {}             # allineamento parole dentro i paragrafi
        self.worker: AlignWorker | None = None
        self._align_pending = False
        self.lang_actions: dict[str, QAction] = {}
        self.glossaries: dict[str, str] = {}      # "it>de" -> testo del glossario
        self.gen_maps: dict[str, dict] = {}       # lingua generata -> file di corrispondenza
        self.gen_worker: TranslateWorker | None = None
        self.gen_job: dict | None = None
        self.ocr_job: dict | None = None          # processo OCR in corso
        self._ocr_skip: set = set()               # hash dei PDF senza OCR in questa sessione
        self.ocr_timer = QTimer(self)
        self.ocr_timer.setInterval(700)
        self.ocr_timer.timeout.connect(self._ocr_poll)
        # stato di Ollama (traduzione disponibile?), ricontrollato in background
        self.ollama_ok, self.ollama_models_list, self.ollama_err = None, [], ""
        self._probe: OllamaProbe | None = None
        self._preloader: ModelPreloader | None = None
        self._tr_target: str | None = None          # ultima lingua scelta per «Traduci»
        self._tr_cache: dict = {}                   # (testo, src, tgt, modello) -> traduzione
        self.probe_timer = QTimer(self)
        self.probe_timer.setInterval(10000)
        self.probe_timer.timeout.connect(self.check_ollama)

        self.stack = QStackedWidget()
        self.placeholder = QLabel("Apri un progetto (Ctrl+O) o aggiungi PDF (Ctrl+L)\n"
                                  "oppure trascina qui i file PDF")
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setStyleSheet("color:#888; font-size:15px;")
        self.stack.addWidget(self.placeholder)
        self.setCentralWidget(self.stack)

        self.page_label = QLabel("  –  ")
        self.status_align = QLabel("")
        self.gen_label = QLabel("")
        self.gen_bar = QProgressBar()
        self.gen_bar.setMaximumWidth(150)
        self.gen_bar.setMaximumHeight(14)
        self.gen_stop = QToolButton()
        self.gen_stop.setText("Interrompi")
        self.gen_stop.clicked.connect(self.stop_generation)
        self.ocr_label = QLabel("")
        self.ocr_stop = QToolButton()
        self.ocr_stop.setText("Interrompi OCR")
        self.ocr_stop.clicked.connect(self.stop_ocr)
        for w in (self.gen_label, self.gen_bar, self.gen_stop, self.ocr_label, self.ocr_stop):
            self.statusBar().addPermanentWidget(w)
            w.hide()
        self.statusBar().addPermanentWidget(self.status_align)
        self.tr_status = QToolButton()               # ● Traduzione disponibile / non disponibile
        self.tr_status.setAutoRaise(True)
        self.tr_status.clicked.connect(self.check_ollama)
        self.statusBar().addPermanentWidget(self.tr_status)
        self.version_label = QLabel(f"v{APP_VERSION}")
        self.version_label.setStyleSheet("color: #888; padding: 0 4px;")
        self.statusBar().addPermanentWidget(self.version_label)
        self._build_ui()
        self._build_panel()
        self.setAcceptDrops(True)
        self._show_tr_status()
        self.check_ollama()
        self.probe_timer.start()

    # ---- UI ----
    def _act(self, text, slot, shortcut=None, checkable=False):
        a = QAction(text, self)
        if shortcut:
            a.setShortcuts(shortcut if isinstance(shortcut, list) else [QKeySequence(shortcut)])
        a.setCheckable(checkable)
        a.triggered.connect(slot)
        return a

    def _build_ui(self):
        self.a_new = self._act("Nuovo progetto", self.new_project, "Ctrl+N")
        self.a_open = self._act("Apri progetto…", self.open_project_dialog, "Ctrl+O")
        self.a_save = self._act("Salva progetto", lambda: self.save_project(), "Ctrl+S")
        self.a_saveas = self._act("Salva con nome…", lambda: self.save_project(True), "Ctrl+Shift+S")
        self.a_add = self._act("Aggiungi PDF/lingua…", self.add_pdf_dialog, "Ctrl+L")
        self.a_remove = self._act("Rimuovi lingua corrente", self.remove_current_language)
        self.a_export = self._act("Esporta PDF annotati…", self.export_annotated, "Ctrl+E")
        self.a_quit = self._act("Esci", self.close, "Ctrl+Q")
        self.a_zin = self._act("Zoom +", lambda: self._view_call("set_zoom_rel", 1.2),
                               [QKeySequence.ZoomIn, QKeySequence("Ctrl+=")])
        self.a_zout = self._act("Zoom −", lambda: self._view_call("set_zoom_rel", 1 / 1.2),
                                [QKeySequence.ZoomOut])
        self.a_fit = self._act("Adatta larghezza", lambda: self._view_call("fit_width"), "Ctrl+0")
        self.a_goto = self._act("Vai a pagina…", self.goto_page_dialog, "Ctrl+G")
        self.a_next = self._act("Lingua successiva", lambda: self.cycle_language(1), "Ctrl+Tab")
        self.a_prev = self._act("Lingua precedente", lambda: self.cycle_language(-1), "Ctrl+Shift+Tab")
        self.a_flash = self._act("Evidenzia blocco corrispondente al cambio lingua", lambda: None,
                                 checkable=True)
        self.a_flash.setChecked(True)
        self.a_realign = self._act("Ricalcola allineamento", self.realign)
        self.a_ocr = self._act("Riconosci testo nelle pagine scansionate (OCR)", self.ocr_current)

        # annotazioni
        self.a_hl_mode = self._act("Evidenziatore", self._on_hl_mode, "Ctrl+Shift+H", checkable=True)
        self.a_hl_mode.setToolTip("Modalità evidenziatore: la selezione viene evidenziata subito "
                                  "(Ctrl+Shift+H)")
        self.a_note_mode = self._act("Nota", self._on_note_mode, "Ctrl+Shift+N", checkable=True)
        self.a_note_mode.setToolTip("Clicca sulla pagina per inserire una nota (Ctrl+Shift+N)")
        self.a_hl = self._act("Evidenzia selezione", lambda: self._highlight_selection(), "H")
        self.a_hl_note = self._act("Evidenzia selezione con nota…",
                                   lambda: self._highlight_selection(with_note=True), "N")
        self.a_copy = self._act("Copia testo selezionato", self.copy_selection, QKeySequence.Copy)
        self.a_translate_sel = self._act("Traduci selezione", lambda: self.translate_selection(), "T")
        self.a_translate_sel.setToolTip("Traduce il testo selezionato con Ollama (T)")
        self.a_about = self._act("Informazioni su PolyglotPDF…", self.about)
        self.a_undo = self._act("Annulla", self.undo, QKeySequence.Undo)
        self.a_esc = self._act("Annulla selezione/modalità", self.cancel_modes, "Esc")

        m = self.menuBar().addMenu("&File")
        for a in (self.a_new, self.a_open, self.a_save, self.a_saveas, None,
                  self.a_add, self.a_remove, None, self.a_export, None, self.a_quit):
            m.addSeparator() if a is None else m.addAction(a)
        m = self.menuBar().addMenu("&Modifica")
        for a in (self.a_undo, None, self.a_copy, self.a_translate_sel, self.a_hl, self.a_hl_note, None,
                  self.a_hl_mode, self.a_note_mode):
            m.addSeparator() if a is None else m.addAction(a)
        self.color_menu = m.addMenu("Colore evidenziatore")
        self.menu_vista = self.menuBar().addMenu("&Vista")
        for a in (self.a_zin, self.a_zout, self.a_fit, self.a_goto, None,
                  self.a_next, self.a_prev, None, self.a_flash):
            self.menu_vista.addSeparator() if a is None else self.menu_vista.addAction(a)
        m = self.menuBar().addMenu("&Allineamento")
        m.addAction(self.a_realign)
        m.addAction(self.a_ocr)

        self.a_generate = self._act("Genera lingua mancante…", self.generate_language_dialog,
                                    "Ctrl+Shift+G")
        self.a_gen_stop = self._act("Interrompi generazione", self.stop_generation)
        self.a_gen_stop.setEnabled(False)
        self.a_review = self._act("Paragrafi da rivedere…", self.review_list)
        self.a_regen = self._act("Rigenera PDF della lingua corrente",
                                 lambda: self.regenerate_language(self.current))
        m = self.menuBar().addMenu("&Traduzione")
        for a in (self.a_translate_sel, None, self.a_generate, self.a_gen_stop, None, self.a_review,
                  self.a_regen):
            m.addSeparator() if a is None else m.addAction(a)
        m = self.menuBar().addMenu("&?")
        m.addAction(self.a_about)

        self.color_group = QActionGroup(self)
        for i, (name, col) in enumerate(COLORS):
            a = QAction(color_icon(col), name, self)
            a.setCheckable(True)
            a.setChecked(i == 0)
            a.setShortcut(QKeySequence(f"Alt+{i + 1}"))
            a.triggered.connect(lambda _=False, c=col: self.set_color(c))
            self.color_group.addAction(a)
            self.color_menu.addAction(a)
        self.color_btn = QToolButton()
        self.color_btn.setMenu(self.color_menu)
        self.color_btn.setPopupMode(QToolButton.InstantPopup)
        self.color_btn.setIcon(color_icon(self.cur_color))
        self.color_btn.setToolTip("Colore evidenziatore (Alt+1…5)")

        tb = QToolBar("Principale")
        tb.setMovable(False)
        for a in (self.a_open, self.a_add, self.a_save):
            tb.addAction(a)
        tb.addSeparator()
        for a in (self.a_zout, self.a_zin, self.a_fit):
            tb.addAction(a)
        tb.addSeparator()
        tb.addAction(self.a_hl_mode)
        tb.addWidget(self.color_btn)
        tb.addAction(self.a_note_mode)
        tb.addSeparator()
        tb.addWidget(self.page_label)
        self.addToolBar(tb)

        self.lang_bar = QToolBar("Lingue")
        self.lang_bar.setMovable(False)
        self.addToolBar(self.lang_bar)
        self.lang_group = QActionGroup(self)

    def _build_panel(self):
        self.panel = QListWidget()
        self.panel.setWordWrap(True)
        self.panel.setSpacing(2)
        self.panel.setContextMenuPolicy(Qt.CustomContextMenu)
        self.panel.itemClicked.connect(lambda it: self.goto_annotation(it.data(Qt.UserRole)))
        self.panel.itemDoubleClicked.connect(lambda it: self.edit_note(it.data(Qt.UserRole)))
        self.panel.customContextMenuRequested.connect(self._panel_menu)
        QShortcut(QKeySequence.Delete, self.panel,
                  activated=lambda: self.panel.currentItem()
                  and self.delete_annotation(self.panel.currentItem().data(Qt.UserRole)))
        self.dock = QDockWidget("Note ed evidenziazioni", self)
        self.dock.setObjectName("annotations")
        self.dock.setWidget(self.panel)
        self.dock.setMinimumWidth(260)
        self.addDockWidget(Qt.RightDockWidgetArea, self.dock)
        self.menu_vista.addSeparator()
        self.menu_vista.addAction(self.dock.toggleViewAction())

    def _rebuild_lang_bar(self):
        for a in self.lang_actions.values():
            self.lang_group.removeAction(a)
            a.deleteLater()
        self.lang_bar.clear()
        self.lang_actions = {}
        for idx, (lang, d) in enumerate(self.docs.items()):
            a = QAction(lang.upper(), self)
            a.setCheckable(True)
            a.setChecked(lang == self.current)
            tip = os.path.basename(d.path)
            if idx < 9:
                a.setShortcut(QKeySequence(f"Ctrl+{idx + 1}"))
                tip += f"   (Ctrl+{idx + 1})"
            a.setToolTip(tip)
            a.triggered.connect(lambda _=False, l=lang: self.switch_language(l))
            self.lang_group.addAction(a)
            self.lang_bar.addAction(a)
            self.lang_actions[lang] = a
        self._update_title()

    def _update_title(self):
        name = os.path.basename(self.project_path) if self.project_path else "senza nome"
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION} — {name}")

    # ---- versione e stato della traduzione (Ollama) ----
    def about(self):
        if self.ollama_ok:
            oll = f"attivo su {ollama_base()}<br>Modelli: {html.escape(', '.join(self.ollama_models_list) or '–')}"
        else:
            oll = f"non rilevato ({html.escape(self.ollama_err or 'controllo in corso')})"
        QMessageBox.about(self, f"Informazioni su {APP_NAME}",
                          f"<h3>{APP_NAME} {APP_VERSION}</h3>"
                          "<p>Lettura dello stesso documento PDF in più lingue, con note ed "
                          "evidenziazioni sincronizzate, OCR e traduzione locale.</p>"
                          f"<p><b>Ollama:</b> {oll}</p>"
                          f"<p><b>Cartella dati:</b> {html.escape(CACHE_DIR)}<br>"
                          f"<b>Python</b> {sys.version.split()[0]} · <b>PyMuPDF</b> {fitz.VersionBind} · "
                          f"<b>Qt</b> {qVersion()}</p>")

    def check_ollama(self):
        """Controlla Ollama in background (non blocca l'interfaccia se non risponde)."""
        if self._probe is not None and self._probe.isRunning():
            return
        self._probe = OllamaProbe(self)
        self._probe.result.connect(self._on_probe)
        self._probe.start()

    def _on_probe(self, ok, models, err):
        self.ollama_ok, self.ollama_models_list, self.ollama_err = ok, list(models), err
        self._show_tr_status()

    def translation_available(self) -> bool:
        return bool(self.ollama_ok and translation_models(self.ollama_models_list))

    def _show_tr_status(self):
        if self.ollama_ok is None:
            text, color, tip = "● Traduzione: controllo…", "#888888", "Controllo di Ollama in corso"
        elif self.translation_available():
            text, color = "● Traduzione disponibile", "#1E8E3E"
            tip = (f"Ollama attivo su {ollama_base()}\nModelli di traduzione: "
                   + ", ".join(translation_models(self.ollama_models_list)))
        elif self.ollama_ok:
            text, color = "● Traduzione non disponibile", "#C62828"
            tip = (f"Ollama è attivo ma manca il modello di traduzione.\n"
                   f"Installalo con:  ollama pull {TR_MODELS[0]}")
        else:
            text, color = "● Traduzione non disponibile", "#C62828"
            tip = (f"Ollama non rilevato su {ollama_base()}.\n"
                   "Installalo da https://ollama.com e avvialo.")
        self.tr_status.setText(text)
        self.tr_status.setStyleSheet(f"QToolButton {{color: {color}; font-weight: bold;}}")
        self.tr_status.setToolTip(tip + "\n\nClic per ricontrollare (controllo automatico ogni 10 s)")

    # ---- traduzione della selezione ----
    def _sel_model(self):
        models = translation_models(self.ollama_models_list)
        return next((m for m in models if m.startswith(TR_MODELS[0])), models[0]) if models else None

    def preload_model(self):
        """Selezione appena fatta: si carica il modello in background (se non c'è già)."""
        if not self.translation_available():
            return
        if self._preloader is not None and self._preloader.isRunning():
            return
        self._preloader = ModelPreloader(self._sel_model(), self)
        self._preloader.start()

    def _default_target(self, src):
        if self._tr_target and self._tr_target != src:
            return self._tr_target
        return "it" if src != "it" else "de"

    def translate_selection(self, tgt=None, lang=None):
        lang = lang or self.current
        v = self.views.get(lang)
        if v is None or not v.sel:
            self.statusBar().showMessage("Seleziona prima il testo da tradurre", 3000)
            return
        if not self.translation_available():
            self._show_tr_status()
            QMessageBox.information(self, APP_NAME, "Traduzione non disponibile.\n\n"
                                    + self.tr_status.toolTip().split("\n\n")[0])
            self.check_ollama()
            return
        d = self.docs[lang]
        g0, g1 = v.sel
        text = source_text([d.blocks[d.wg[g][0]].words[d.wg[g][1]] for g in range(g0, g1 + 1)])
        tgt = (tgt or self._default_target(lang)).lower()
        self._tr_target = tgt
        model = self._sel_model()
        popup = TranslationPopup(lang, tgt, model, v.viewport())
        popup.retarget.connect(lambda t, l=lang: self.translate_selection(t, l))
        popup.save_note.connect(lambda tr, l=lang, a=g0, b=g1, t=tgt:
                                self.add_highlight(l, a, b, self.cur_color, note=f"[{t.upper()}] {tr}"))
        v.show_popup(popup)
        key = (text, lang, tgt, model)
        if key in self._tr_cache:
            popup.set_result(self._tr_cache[key])
            return
        gl = parse_glossary(self.glossaries.get(f"{lang}>{tgt}", ""))
        w = SelectionTranslateWorker(text, lang, tgt, model, gl, self)
        popup.worker = w
        w.partial.connect(popup.set_partial)
        w.done.connect(popup.set_result)
        w.done.connect(lambda r, k=key: self._tr_cache.__setitem__(k, r))
        w.failed.connect(popup.set_error)
        w.failed.connect(lambda _e: self.check_ollama())
        w.finished.connect(w.deleteLater)
        w.start()

    def _view(self):
        return self.views.get(self.current)

    def _view_call(self, what, *args):
        v = self._view()
        if v is None:
            return
        if what == "set_zoom_rel":
            v.set_zoom(v.zoom * args[0])
        else:
            getattr(v, what)(*args)
        self._update_page_label()

    def _update_page_label(self):
        v = self._view()
        if v is None:
            self.page_label.setText("  –  ")
            return
        self.page_label.setText(f"  {self.current.upper()}  ·  Pag. {v.current_page() + 1} / "
                                f"{len(v.offsets)}  ·  {v.zoom * 100:.0f}%  ")

    def _on_view_scrolled(self):
        if self.sender() is self._view():
            self._update_page_label()

    # ---- documenti ----
    def ask_lang(self, path):
        guess = guess_lang_from_name(path)
        if guess is None:
            try:
                with fitz.open(path) as d:
                    guess = detect_lang(d)
            except Exception:
                guess = None
        items = list(dict.fromkeys(([guess] if guess else []) + LANGS))
        lang, ok = QInputDialog.getItem(self, "Lingua del documento",
                                        f"Lingua di «{os.path.basename(path)}»:", items, 0, True)
        lang = lang.strip().lower()
        return lang if ok and lang else None

    def add_pdf_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Aggiungi PDF", "", "PDF (*.pdf)")
        if paths:
            self.add_pdfs(paths)

    def add_pdfs(self, paths):
        changed = False
        for p in paths:
            lang = self.ask_lang(p)
            if not lang:
                continue
            if lang in self.docs:
                r = QMessageBox.question(self, APP_NAME,
                                         f"La lingua {lang.upper()} esiste già. Sostituire il PDF?")
                if r != QMessageBox.Yes:
                    continue
            changed |= self._load_doc(p, lang)
        if changed:
            for lang in self.docs:
                self._reanchor(lang)
            self._rebuild_lang_bar()
            self._start_alignment()
            self.refresh_annotations()
            self._update_page_label()
            QTimer.singleShot(0, self._start_ocr)

    def _load_doc(self, path, lang, fit=True) -> bool:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            d = Document(path, lang)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, APP_NAME, f"Impossibile aprire {path}:\n{e}")
            return False
        QApplication.restoreOverrideCursor()
        if lang in self.views:  # sostituzione
            old = self.views.pop(lang)
            self.stack.removeWidget(old)
            old.deleteLater()
            self._drop_mappings(lang)
        gm = load_gen_map(d.path)   # PDF generato da PolyglotPDF e non modificato altrove?
        if gm and gm.get("out_hash") == d.hash:
            self.gen_maps[lang] = gm
        else:
            self.gen_maps.pop(lang, None)
        view = PdfView(d)
        view.positionChanged.connect(self._on_view_scrolled)
        view.selectionMade.connect(lambda g0, g1, gp, l=lang: self._on_selection(l, g0, g1, gp))
        view.annotationClicked.connect(self._on_annotation_clicked)
        view.annotationActivated.connect(self.edit_note)
        view.contextRequested.connect(lambda a, p, x, y, gp, l=lang: self._on_context(l, a, p, x, y, gp))
        view.pageClicked.connect(lambda p, x, y, l=lang: self._on_page_clicked(l, p, x, y))
        view.place_mode = self.a_note_mode.isChecked()
        self.docs[lang] = d
        self.views[lang] = view
        self.stack.addWidget(view)
        if self.current is None or self.current == lang:
            self.current = lang
            self.stack.setCurrentWidget(view)
            if fit:
                QTimer.singleShot(0, view.fit_width)
        else:
            view.set_zoom(self.views[self.current].zoom, keep_position=False)
        return True

    def _drop_mappings(self, lang):
        for key in [k for k in self.mappings if lang in k]:
            self.mappings.pop(key, None)
            self.align_info.pop(key, None)

    def remove_current_language(self):
        if self.current is None:
            return
        lang = self.current
        view = self.views.pop(lang)
        self.docs.pop(lang)
        self.gen_maps.pop(lang, None)
        self.stack.removeWidget(view)
        view.deleteLater()
        self._drop_mappings(lang)
        self.current = next(iter(self.docs), None)
        self.stack.setCurrentWidget(self.views[self.current] if self.current else self.placeholder)
        self._rebuild_lang_bar()
        self._update_align_status()
        self.refresh_annotations()
        self._update_page_label()

    def _close_all(self):
        self._kill_ocr()
        for v in self.views.values():
            self.stack.removeWidget(v)
            v.deleteLater()
        self.docs.clear()
        self.views.clear()
        self.mappings.clear()
        self.align_info.clear()
        self.gen_maps.clear()
        self.glossaries = {}
        self.annotations = []
        self._undo = []
        self._relink = None
        self.current = None
        self.stack.setCurrentWidget(self.placeholder)
        self._rebuild_lang_bar()
        self._update_align_status()
        self.refresh_annotations()
        self._update_page_label()

    # ---- cambio lingua ----
    def map_pos(self, la, lb, pos):
        if la == lb:
            return pos
        if (la, lb) in self.mappings:
            xs, ys = self.mappings[(la, lb)]
            return float(np.interp(pos, xs, ys))
        if (lb, la) in self.mappings:
            xs, ys = self.mappings[(lb, la)]
            return float(np.interp(pos, ys, xs))
        return pos * self.docs[lb].n / max(self.docs[la].n, 1)  # proporzionale (provvisorio)

    def map_pos_fine(self, la, lb, p, end=False):
        """Come map_pos, ma dentro paragrafi corrispondenti allinea anche le parole."""
        if la == lb:
            return p
        da, db = self.docs[la], self.docs[lb]
        sb = int(p)
        f = p - sb
        if end and f < 1e-9 and sb > 0:
            sb, f = sb - 1, 1.0
        sb = min(sb, da.n - 1)
        tb = self._paired_block(la, lb, sb)
        if tb is None:                                   # paragrafi non corrispondenti 1:1
            return self.map_pos(la, lb, p)
        xs, ys, _m = self._intra(la, lb, sb, tb)
        return tb + float(np.interp(f, xs, ys))

    def _paired_block(self, la, lb, sb):
        """Blocco di lb che corrisponde 1:1 al blocco sb di la (None se non è così)."""
        db = self.docs[lb]
        tb = int(min(max(self.map_pos(la, lb, sb + 0.5), 0.0), db.n - 1e-6))
        return tb if int(self.map_pos(lb, la, tb + 0.5)) == sb else None

    def _intra(self, la, lb, sb, tb):
        """(xs, ys, corrispondenze parola->parola) fra due paragrafi corrispondenti, in cache."""
        da, db = self.docs[la], self.docs[lb]
        key = (da.text_key, db.text_key, sb, tb)
        if key not in self._intra_cache:
            m = block_word_matches(da.blocks[sb].words, db.blocks[tb].words)
            self._intra_cache[key] = (*intra_block_map(da.blocks[sb].words, db.blocks[tb].words, m), m)
        return self._intra_cache[key]

    def _direct_words(self, la, lb, pos):
        """Evidenziazione breve dentro un paragrafo: parole corrispondenti trovate direttamente
        (anche con ordine diverso, «Intelligentes Design» -> «il design intelligente»).
        -> (g0, g1) nel documento lb, oppure None."""
        da, db = self.docs[la], self.docs[lb]
        rng = da.words_in_pos_range(*pos)
        if not rng:
            return None
        g0, g1 = rng
        sb = da.wg[g0][0]
        if da.wg[g1][0] != sb or g1 - g0 >= 8:
            return None
        tb = self._paired_block(la, lb, sb)
        if tb is None:
            return None
        m = self._intra(la, lb, sb, tb)[2]
        k0, k1 = da.wg[g0][1], da.wg[g1][1]
        hit = [(k, m[k]) for k in range(k0, k1 + 1) if k in m]
        if not hit:
            return None
        Kb = len(db.blocks[tb].words)
        lo = max(min(kb for _k, kb in hit) - (hit[0][0] - k0), 0)        # parole non trovate ai lati:
        hi = min(max(kb for _k, kb in hit) + (k1 - hit[-1][0]), Kb - 1)  # stesso numero di parole
        return db.block_wstart[tb] + lo, db.block_wstart[tb] + hi

    def switch_language(self, lang):
        if lang == self.current or lang not in self.views:
            if lang in self.lang_actions:
                self.lang_actions[lang].setChecked(True)
            return
        src_v, tgt_v = self.views[self.current], self.views[lang]
        src_d, tgt_d = self.docs[self.current], self.docs[lang]
        pno, x, y = src_v.get_position()
        pos = self.map_pos(self.current, lang, src_d.pos_from_point(pno, x, y))
        tpno, ty, k = tgt_d.point_from_pos(pos)

        src_v.clear_selection()
        tgt_v.set_zoom(src_v.zoom, keep_position=False)
        self.stack.setCurrentWidget(tgt_v)
        tgt_v.set_position(tpno, ty)
        QTimer.singleShot(0, lambda: tgt_v.set_position(tpno, ty))
        if self.a_flash.isChecked():
            tgt_v.flash_block(k)
        self.current = lang
        self.lang_actions[lang].setChecked(True)
        self._update_page_label()
        self.refresh_panel()

    def cycle_language(self, step):
        langs = list(self.docs)
        if len(langs) < 2 or self.current is None:
            return
        self.switch_language(langs[(langs.index(self.current) + step) % len(langs)])

    def goto_page_dialog(self):
        v = self._view()
        if v is None:
            return
        p, ok = QInputDialog.getInt(self, "Vai a pagina", "Pagina:", v.current_page() + 1,
                                    1, len(v.offsets))
        if ok:
            v.goto_page(p - 1)

    # ---- annotazioni: modello ----
    def _ann(self, ann_id):
        return next((a for a in self.annotations if a["id"] == ann_id), None)

    def _anchor_from_words(self, lang, g0, g1):
        d = self.docs[lang]
        return {"lang": lang, "hash": d.hash, "tk": d.text_key,
                "pos": [d.word_pos(g0), d.word_pos(g1, end=True)],
                "rects": d.rects_for_words(g0, g1), "text": d.text_for_words(g0, g1)}

    def _anchor_from_point(self, lang, page, x, y):
        d = self.docs[lang]
        p = d.pos_from_point(page, x, y)
        return {"lang": lang, "hash": d.hash, "tk": d.text_key, "pos": [p, p], "point": [page, x, y]}

    def _ann_render(self, ann, lang):
        """Geometria dell'annotazione nella lingua richiesta (esatta o stimata)."""
        d = self.docs.get(lang)
        if d is None:
            return None
        anchors = {ann["src"]["lang"]: ann["src"], **ann.get("overrides", {})}
        valid = {l: a for l, a in anchors.items() if l in self.docs and a.get("hash") == self.docs[l].hash}
        icon, rects, text = None, [], ""
        if lang in valid:
            a, approx = valid[lang], False
            pos = a["pos"][0]
            if ann["kind"] == "highlight":
                rects, text = a["rects"], a["text"]
            else:
                icon = tuple(a["point"])
        else:
            if not valid:
                return None
            base = ann["src"]["lang"] if ann["src"]["lang"] in valid else next(iter(valid))
            a, approx = valid[base], True
            q0 = self.map_pos_fine(base, lang, a["pos"][0])
            q1 = max(self.map_pos_fine(base, lang, a["pos"][1], end=True), q0)
            pos = q0
            if ann["kind"] == "highlight":
                rng = self._direct_words(base, lang, a["pos"]) or d.words_in_pos_range(q0, q1)
                if rng:
                    rects, text = d.rects_for_words(*rng), d.text_for_words(*rng)
                else:
                    rects = d.rects_for_pos_range(q0, q1)
            else:
                page, y, _k = d.point_from_pos(q0)
                x = min(a["point"][1], d.page_sizes[page][0] - NOTE_ICON - 1)
                icon = (page, x, y)
        if ann["kind"] == "highlight" and ann.get("note") and rects:
            p, _x0, y0, x1, _y1 = rects[-1]
            x = min(x1 + 2, d.page_sizes[p][0] - NOTE_ICON - 1)
            icon = (p, x, max(y0 - NOTE_ICON / 2, 0))
        page = rects[0][0] if rects else (icon[0] if icon else 0)
        return {"id": ann["id"], "kind": ann["kind"], "color": ann["color"], "note": ann.get("note", ""),
                "rects": rects, "icon": icon, "approx": approx, "text": text, "pos": pos, "page": page}

    def refresh_annotations(self):
        for lang, v in self.views.items():
            v.set_annotations([r for r in (self._ann_render(a, lang) for a in self.annotations) if r])
        self.refresh_panel()

    def refresh_panel(self):
        current_id = self.panel.currentItem().data(Qt.UserRole) if self.panel.currentItem() else None
        self.panel.clear()
        if self.current is None:
            return
        rows = [r for r in (self._ann_render(a, self.current) for a in self.annotations) if r]
        rows.sort(key=lambda r: r["pos"])
        for r in rows:
            mark = "≈ " if r["approx"] else ""
            if r["kind"] == "highlight":
                head = f"p. {r['page'] + 1}  {mark}«{short(r['text'], 90)}»"
            else:
                head = f"p. {r['page'] + 1}  {mark}📌 Nota"
            body = f"\n✎ {short(r['note'], 140)}" if r["note"] else ""
            it = QListWidgetItem(color_icon(r["color"]), head + body)
            it.setData(Qt.UserRole, r["id"])
            it.setToolTip((r["text"] + "\n\n" if r["text"] else "") + (r["note"] or ""))
            self.panel.addItem(it)
            if r["id"] == current_id:
                self.panel.setCurrentItem(it)
        self.dock.setWindowTitle(f"Note ed evidenziazioni ({len(rows)})")

    def _push_undo(self):
        self._undo.append(copy.deepcopy(self.annotations))
        del self._undo[:-UNDO_LIMIT]

    def _changed(self, select=None):
        self.refresh_annotations()
        if select:
            self._select_in_panel(select)
        if self.project_path:      # salvataggio automatico
            self.save_project(quiet=True)

    def undo(self):
        if not self._undo:
            self.statusBar().showMessage("Niente da annullare", 2000)
            return
        self.annotations = self._undo.pop()
        self._changed()
        self.statusBar().showMessage("Annullato", 2000)

    # ---- annotazioni: azioni ----
    def set_color(self, col):
        self.cur_color = col
        self.color_btn.setIcon(color_icon(col))

    def _on_hl_mode(self, checked):
        if checked and self.a_note_mode.isChecked():
            self.a_note_mode.setChecked(False)
            self._on_note_mode(False)

    def _on_note_mode(self, checked):
        if checked and self.a_hl_mode.isChecked():
            self.a_hl_mode.setChecked(False)
        for v in self.views.values():
            v.place_mode = checked
        if checked:
            self.statusBar().showMessage("Clicca sulla pagina dove inserire la nota (Esc per annullare)")

    def cancel_modes(self):
        v = self._view()
        if v:
            v.clear_selection()
        self._relink = None
        if self.a_note_mode.isChecked():
            self.a_note_mode.setChecked(False)
            self._on_note_mode(False)
        self.statusBar().clearMessage()

    def copy_selection(self):
        v = self._view()
        if v and v.sel:
            QApplication.clipboard().setText(v.selected_text())
            self.statusBar().showMessage("Testo copiato", 2000)

    def _highlight_selection(self, with_note=False, color=None):
        v = self._view()
        if v is None or not v.sel:
            self.statusBar().showMessage("Seleziona prima del testo trascinando il mouse", 3000)
            return
        self.add_highlight(self.current, *v.sel, color or self.cur_color, ask_note=with_note)

    def add_highlight(self, lang, g0, g1, color, ask_note=False, note=""):
        if ask_note:
            note, ok = QInputDialog.getMultiLineText(self, "Nota", "Testo della nota:")
            if not ok:
                return
        self._push_undo()
        ann = {"id": uuid.uuid4().hex[:12], "kind": "highlight", "color": color, "note": note.strip(),
               "created": datetime.now().isoformat(timespec="seconds"),
               "src": self._anchor_from_words(lang, g0, g1), "overrides": {}}
        self.annotations.append(ann)
        self.views[lang].clear_selection()
        self._changed(select=ann["id"])

    def add_note_at(self, lang, page, x, y):
        note, ok = QInputDialog.getMultiLineText(self, "Nuova nota", "Testo della nota:")
        if not ok or not note.strip():
            return
        self._push_undo()
        x = min(x, self.docs[lang].page_sizes[page][0] - NOTE_ICON - 1)
        ann = {"id": uuid.uuid4().hex[:12], "kind": "note", "color": self.cur_color, "note": note.strip(),
               "created": datetime.now().isoformat(timespec="seconds"),
               "src": self._anchor_from_point(lang, page, x, y), "overrides": {}}
        self.annotations.append(ann)
        self._changed(select=ann["id"])

    def edit_note(self, ann_id):
        ann = self._ann(ann_id)
        if ann is None:
            return
        note, ok = QInputDialog.getMultiLineText(self, "Nota", "Testo della nota:", ann.get("note", ""))
        if not ok:
            return
        if ann["kind"] == "note" and not note.strip():
            self.delete_annotation(ann_id)
            return
        self._push_undo()
        ann["note"] = note.strip()
        self._changed(select=ann_id)

    def recolor(self, ann_id, col):
        ann = self._ann(ann_id)
        if ann:
            self._push_undo()
            ann["color"] = col
            self._changed(select=ann_id)

    def delete_annotation(self, ann_id):
        ann = self._ann(ann_id)
        if ann:
            self._push_undo()
            self.annotations.remove(ann)
            self._changed()
            self.statusBar().showMessage("Annotazione eliminata (Ctrl+Z per annullare)", 3000)

    def start_relink(self, ann_id, lang):
        ann = self._ann(ann_id)
        if ann is None:
            return
        self._relink = (ann_id, lang)
        what = "il testo corrispondente" if ann["kind"] == "highlight" else "il punto corrispondente"
        self.statusBar().showMessage(f"Seleziona {what} in {lang.upper()} (Esc per annullare)")

    def reset_override(self, ann_id, lang):
        ann = self._ann(ann_id)
        if ann and lang in ann.get("overrides", {}):
            self._push_undo()
            ann["overrides"].pop(lang)
            self._changed(select=ann_id)

    def _set_override(self, ann_id, lang, anchor):
        ann = self._ann(ann_id)
        if ann is None:
            return
        self._push_undo()
        if lang == ann["src"]["lang"]:
            ann["src"] = anchor
        else:
            ann.setdefault("overrides", {})[lang] = anchor
        self._relink = None
        self.statusBar().showMessage(f"Posizione in {lang.upper()} aggiornata", 3000)
        self._changed(select=ann_id)

    # ---- annotazioni: eventi dalla vista ----
    def _on_selection(self, lang, g0, g1, gp):
        if self._relink:
            ann_id, rl = self._relink
            ann = self._ann(ann_id)
            if rl == lang and ann and ann["kind"] == "highlight":
                self.views[lang].clear_selection()
                self._set_override(ann_id, lang, self._anchor_from_words(lang, g0, g1))
                return
        if self.a_hl_mode.isChecked():
            self.add_highlight(lang, g0, g1, self.cur_color)
            return
        self.preload_model()             # mentre si sceglie dal menu, il modello si carica
        menu = QMenu(self)
        for name, col in COLORS:
            menu.addAction(color_icon(col), f"Evidenzia — {name}",
                           lambda c=col: self.add_highlight(lang, g0, g1, c))
        menu.addSeparator()
        menu.addAction(color_icon(self.cur_color), "Evidenzia con nota…",
                       lambda: self.add_highlight(lang, g0, g1, self.cur_color, ask_note=True))
        menu.addAction("Copia testo", self.copy_selection)
        menu.addSeparator()
        self._translate_menu(menu, lang)
        menu.exec(gp)

    def _translate_menu(self, menu, lang):
        """«Traduci» (lingua abituale) + «Traduci in» con le altre lingue."""
        ok = self.translation_available()
        tgt = self._default_target(lang)
        a = menu.addAction(f"Traduci in {LANG_NAMES_UI.get(tgt, tgt)} ({tgt.upper()})"
                           + ("" if ok else " — traduzione non disponibile"),
                           lambda: self.translate_selection(tgt, lang))
        a.setEnabled(ok)
        sub = menu.addMenu("Traduci in")
        sub.setEnabled(ok)
        for l in [l for l in LANGS if l != lang]:
            sub.addAction(f"{l.upper()} — {LANG_NAMES_UI.get(l, l)}", lambda t=l: self.translate_selection(t, lang))

    def _on_page_clicked(self, lang, page, x, y):
        if self._relink:
            ann_id, rl = self._relink
            ann = self._ann(ann_id)
            if rl == lang and ann and ann["kind"] == "note":
                self._set_override(ann_id, lang, self._anchor_from_point(lang, page, x, y))
            return
        if self.a_note_mode.isChecked():
            self.a_note_mode.setChecked(False)
            self._on_note_mode(False)
            self.statusBar().clearMessage()
            self.add_note_at(lang, page, x, y)
            return
        v = self.views.get(lang)
        if v:
            v.set_active(None)
        self.panel.clearSelection()

    def _on_annotation_clicked(self, ann_id):
        v = self._view()
        if v:
            v.set_active(ann_id)
        self._select_in_panel(ann_id)

    def _select_in_panel(self, ann_id):
        for i in range(self.panel.count()):
            it = self.panel.item(i)
            if it.data(Qt.UserRole) == ann_id:
                self.panel.setCurrentItem(it)
                self.panel.scrollToItem(it)
                break
        v = self._view()
        if v:
            v.set_active(ann_id)

    def goto_annotation(self, ann_id):
        ann, v = self._ann(ann_id), self._view()
        if ann is None or v is None:
            return
        r = self._ann_render(ann, self.current)
        if r is None:
            return
        if r["rects"]:
            p, _x0, y0, _x1, _y1 = r["rects"][0]
            v.set_position(p, y0)
            v.flash_rects(r["rects"], 900)
        elif r["icon"]:
            p, x, y = r["icon"]
            v.set_position(p, y)
            v.flash_rects([[p, x, y, x + NOTE_ICON, y + NOTE_ICON]], 900)
        v.set_active(ann_id)
        self._update_page_label()

    def _ann_menu(self, menu, lang, ann_id):
        ann = self._ann(ann_id)
        if ann is None:
            return
        menu.addAction("Modifica nota…" if ann.get("note") else "Aggiungi nota…",
                       lambda: self.edit_note(ann_id))
        cm = menu.addMenu("Colore")
        for name, col in COLORS:
            cm.addAction(color_icon(col), name, lambda c=col: self.recolor(ann_id, c))
        r = self._ann_render(ann, lang)
        if r and r["text"]:
            menu.addAction("Copia testo", lambda: QApplication.clipboard().setText(r["text"]))
        menu.addSeparator()
        if lang != ann["src"]["lang"]:
            menu.addAction(f"Correggi posizione in {lang.upper()}", lambda: self.start_relink(ann_id, lang))
        if lang in ann.get("overrides", {}):
            menu.addAction(f"Ripristina posizione automatica in {lang.upper()}",
                           lambda: self.reset_override(ann_id, lang))
        menu.addSeparator()
        menu.addAction("Elimina", lambda: self.delete_annotation(ann_id))

    def _on_context(self, lang, ann_id, page, x, y, gp):
        menu = QMenu(self)
        if ann_id:
            self._on_annotation_clicked(ann_id)
            self._ann_menu(menu, lang, ann_id)
        else:
            v = self.views[lang]
            if v.sel:
                g0, g1 = v.sel
                self.preload_model()
                for name, col in COLORS:
                    menu.addAction(color_icon(col), f"Evidenzia — {name}",
                                   lambda c=col: self.add_highlight(lang, g0, g1, c))
                menu.addAction("Evidenzia con nota…",
                               lambda: self.add_highlight(lang, g0, g1, self.cur_color, ask_note=True))
                menu.addAction("Copia testo", self.copy_selection)
                menu.addSeparator()
                self._translate_menu(menu, lang)
                menu.addSeparator()
            menu.addAction("Aggiungi nota qui…", lambda: self.add_note_at(lang, page, x, y))
        e = self._gen_entry_at(lang, page, x, y)
        if e is not None:
            menu.addSeparator()
            menu.addAction("Modifica traduzione del paragrafo…", lambda: self.edit_translation(lang, e))
            menu.addAction(f"Ritraduci paragrafo ({self.gen_maps[lang]['model']})",
                           lambda: self.retranslate_paragraph(lang, e))
        menu.exec(gp)

    def _panel_menu(self, pos):
        it = self.panel.itemAt(pos)
        if it is None or self.current is None:
            return
        menu = QMenu(self)
        self._ann_menu(menu, self.current, it.data(Qt.UserRole))
        menu.exec(self.panel.mapToGlobal(pos))

    # ---- esportazione ----
    def export_annotated(self):
        if not self.docs:
            return
        if not self.annotations:
            QMessageBox.information(self, APP_NAME, "Non ci sono annotazioni da esportare.")
            return
        folder = QFileDialog.getExistingDirectory(self, "Cartella di destinazione per i PDF annotati")
        if not folder:
            return
        written = []
        for lang, d in self.docs.items():
            out_path = os.path.join(folder, os.path.splitext(os.path.basename(d.path))[0] + "_annotato.pdf")
            if os.path.abspath(out_path) == d.path:
                continue
            self.write_annotated_pdf(lang, out_path)
            written.append(os.path.basename(out_path))
        QMessageBox.information(self, APP_NAME, "PDF esportati:\n" + "\n".join(written))

    def write_annotated_pdf(self, lang, out_path):
        d = self.docs[lang]
        out = fitz.open(d.path)
        for ann in self.annotations:
            r = self._ann_render(ann, lang)
            if r is None:
                continue
            c = QColor(r["color"])
            rgb = (c.redF(), c.greenF(), c.blueF())
            info = r["note"] + ("\n(posizione stimata)" if r["approx"] and r["note"] else "")
            if r["kind"] == "highlight":
                by_page = defaultdict(list)
                for p, x0, y0, x1, y1 in r["rects"]:
                    by_page[p].append(fitz.Rect(x0, y0, x1, y1))
                for p, rs in by_page.items():
                    page = out[p]          # riferimento alla pagina tenuto vivo
                    a = page.add_highlight_annot(rs)
                    a.set_colors(stroke=rgb)
                    a.set_info(content=info, title=APP_NAME)
                    a.update()
            elif r["icon"]:
                p, x, y = r["icon"]
                page = out[p]
                a = page.add_text_annot(fitz.Point(x, y), info, icon="Note")
                a.set_colors(stroke=rgb)
                a.set_info(title=APP_NAME)
                a.update()
        out.save(out_path, garbage=1, deflate=True)
        out.close()

    # ---- OCR delle pagine scansionate ----
    def ocr_current(self):
        """Menu: (ri)avvia l'OCR del PDF corrente, anche se rifiutato o interrotto prima."""
        d = self.docs.get(self.current)
        if d is None:
            return
        if not d.pending_ocr:
            QMessageBox.information(self, APP_NAME, "Nessuna pagina scansionata da riconoscere "
                                    "in questo PDF (o OCR già fatto).")
            return
        self._ocr_skip.discard(d.hash)
        self._start_ocr(first=self.current)

    def _start_ocr(self, first=None):
        """Avvia l'OCR del primo PDF con pagine scansionate non ancora riconosciute."""
        if self.ocr_job is not None:
            return
        langs = sorted(self.docs, key=lambda l: l != first)
        for lang in langs:
            d = self.docs[lang]
            if not d.pending_ocr or d.hash in self._ocr_skip:
                continue
            tess = ocr_langs(lang)
            missing = tessdata_missing(tess)
            if missing:
                r = QMessageBox.question(
                    self, APP_NAME,
                    f"«{os.path.basename(d.path)}» ha {len(d.pending_ocr)} pagine scansionate "
                    f"(immagini senza testo).\n\nPer riconoscerne il testo (OCR) servono i dati "
                    f"di lingua {', '.join(missing)} (circa 15 MB ciascuno), da scaricare una sola "
                    f"volta da github.com/tesseract-ocr/tessdata.\n\nScaricarli ora?")
                if r != QMessageBox.Yes:
                    self._ocr_skip.add(d.hash)
                    self.statusBar().showMessage("Pagine scansionate senza OCR (Allineamento → "
                                                 "Riconosci testo… per avviarlo)", 8000)
                    continue
                QApplication.setOverrideCursor(Qt.WaitCursor)
                try:
                    download_tessdata(missing)
                except OSError as e:
                    QApplication.restoreOverrideCursor()
                    QMessageBox.warning(self, APP_NAME, str(e))
                    self._ocr_skip.add(d.hash)
                    continue
                QApplication.restoreOverrideCursor()
            args = ["--ocr", d.path, d.hash, tess, ",".join(map(str, d.pending_ocr))]
            if not getattr(sys, "frozen", False):          # da sorgente: python polyglot_pdf.py
                args.insert(0, os.path.abspath(__file__))
            proc = QProcess(self)
            proc.finished.connect(lambda code, status, l=lang, h=d.hash: self._ocr_finished(l, h, code, status))
            progress = ocr_cache_path(d.hash, tess) + ".progress"
            self.ocr_job = {"proc": proc, "lang": lang, "hash": d.hash, "progress": progress,
                            "total": len(d.pending_ocr)}
            self.ocr_label.setText(f"OCR {lang.upper()}: avvio…")
            self.ocr_label.show()
            self.ocr_stop.show()
            self.ocr_timer.start()
            for f in (progress, OCR_ERROR_FILE):
                try:
                    os.remove(f)
                except OSError:
                    pass
            proc.start(sys.executable, args)
            return

    def _ocr_poll(self):
        job = self.ocr_job
        if job is None:
            return
        try:
            with open(job["progress"], encoding="utf-8") as f:
                done, total = map(int, f.read().split())
        except (OSError, ValueError):
            return
        self.ocr_label.setText(f"OCR {job['lang'].upper()}: pagina {min(done + 1, total)}/{total}")

    def stop_ocr(self):
        if self.ocr_job is not None:
            self._ocr_skip.add(self.ocr_job["hash"])
            self._kill_ocr()
            self.statusBar().showMessage("OCR interrotto: le pagine già riconosciute restano in cache "
                                         "(Allineamento → Riconosci testo… per riprendere)", 8000)

    def _kill_ocr(self):
        job, self.ocr_job = self.ocr_job, None
        self.ocr_timer.stop()
        self.ocr_label.hide()
        self.ocr_stop.hide()
        if job is not None:
            job["proc"].finished.disconnect()
            job["proc"].kill()
            job["proc"].waitForFinished(3000)

    def _ocr_finished(self, lang, doc_hash, code, status):
        job, self.ocr_job = self.ocr_job, None
        self.ocr_timer.stop()
        self.ocr_label.hide()
        self.ocr_stop.hide()
        err = ""
        try:
            with open(OCR_ERROR_FILE, encoding="utf-8") as f:
                err = f.read().strip()
        except OSError:
            pass
        d = self.docs.get(lang)
        if d is None or d.hash != doc_hash:
            pass                                   # PDF chiuso o sostituito nel frattempo
        elif code != 0 or status != QProcess.NormalExit:
            self._ocr_skip.add(doc_hash)
            QMessageBox.warning(self, APP_NAME, f"OCR non riuscito per {os.path.basename(d.path)}:\n"
                                + (err or "il processo si è chiuso in modo inatteso"))
        else:
            n = len(d.pending_ocr)
            self._ocr_skip.add(doc_hash)           # fatto: non ripartire in questa sessione
            self._reload_text(lang)
            self.statusBar().showMessage(f"OCR {lang.upper()}: {n} pagine riconosciute", 5000)
        QTimer.singleShot(0, self._start_ocr)

    def _reload_text(self, lang):
        """Ricarica un PDF dopo l'OCR (stesso file, blocchi nuovi) mantenendo la posizione."""
        old, v = self.docs[lang], self.views[lang]
        state = (v.zoom, *v.get_position())
        if not self._load_doc(old.path, lang, fit=False):
            return
        v = self.views[lang]
        v.set_zoom(state[0], keep_position=False)
        v.set_position(state[1], state[3])
        QTimer.singleShot(0, lambda: v.set_position(state[1], state[3]))
        self._reanchor(lang)
        self._intra_cache.clear()
        self._rebuild_lang_bar()
        self._start_alignment()
        self.refresh_annotations()
        self._update_page_label()
        if self.project_path:
            self.save_project(quiet=True)

    # ---- allineamento ----
    def _start_alignment(self):
        if self.worker is not None and self.worker.isRunning():
            self._align_pending = True
            return
        self._add_exact_mappings()
        langs = list(self.docs)
        pairs = [(a, b) for i, a in enumerate(langs) for b in langs[i + 1:]
                 if (a, b) not in self.mappings and (b, a) not in self.mappings]
        if not pairs:
            self._update_align_status()
            return
        snapshot = {l: (self.docs[l].text_key, self.docs[l].blocks) for p in pairs for l in p}
        self.worker = AlignWorker(snapshot, pairs, self)
        self.worker.progress.connect(lambda s: self.statusBar().showMessage(s, 5000))
        self.worker.pair_done.connect(self._on_pair_done)
        self.worker.finished.connect(self._on_worker_finished)
        self._update_align_status(running=True)
        self.worker.start()

    def _add_exact_mappings(self):
        """Lingue generate: allineamento esatto con la sorgente dal file di corrispondenza."""
        for tl, gm in self.gen_maps.items():
            sl = next((l for l, d in self.docs.items() if d.hash == gm["source"]["hash"]), None)
            if sl is None or sl == tl or tl not in self.docs:
                continue
            if gm["source"].get("tk", gm["source"]["hash"]) != self.docs[sl].text_key:
                continue    # blocchi della sorgente cambiati (OCR): allineamento stimato
            if (sl, tl) in self.mappings or (tl, sl) in self.mappings:
                continue
            anchors = exact_anchors(gm["entries"], self.docs[tl])
            self.mappings[(sl, tl)] = build_mapping(anchors, self.docs[sl].n, self.docs[tl].n)
            self.align_info[(sl, tl)] = f"{sl.upper()}↔{tl.upper()}: {len(anchors)} ancore (esatto)"

    def _on_pair_done(self, la, lb, ha, hb, xs, ys, n_anchors, method):
        if la not in self.docs or lb not in self.docs:
            return
        if self.docs[la].text_key != ha or self.docs[lb].text_key != hb:
            return  # documento sostituito (o testo riconosciuto con l'OCR) nel frattempo
        self.mappings[(la, lb)] = (xs, ys)
        self.align_info[(la, lb)] = f"{la.upper()}↔{lb.upper()}: {n_anchors} ancore ({method})"
        self._update_align_status(running=True)
        if self.annotations:
            self.refresh_annotations()

    def _on_worker_finished(self):
        if self._align_pending:
            self._align_pending = False
            self._start_alignment()
        else:
            self._update_align_status()
            self.statusBar().showMessage("Allineamento completato", 3000)

    def _update_align_status(self, running=False):
        n = len(self.docs)
        total = n * (n - 1) // 2
        if total == 0:
            self.status_align.setText("")
            return
        done = len(self.mappings)
        txt = f"Allineamento: {done}/{total}"
        if running and done < total:
            txt += " (in corso…)"
        self.status_align.setText(txt)
        self.status_align.setToolTip("\n".join(self.align_info.values()) or "nessuna coppia pronta")

    def realign(self):
        if not self.docs:
            return
        hashes = {d.hash for d in self.docs.values()}
        for f in os.listdir(CACHE_DIR):
            if f.startswith("align_") and any(h in f for h in hashes):
                try:
                    os.remove(os.path.join(CACHE_DIR, f))
                except OSError:
                    pass
        self.mappings.clear()
        self.align_info.clear()
        self._start_alignment()

    # ---- generazione lingua mancante (passo 3) ----
    def _translatable_items(self, lang, pages=None):
        """[(chiave unità, testo)] da tradurre."""
        d = self.docs[lang]
        out = []
        for key, i, _rect, ws in doc_units(d):
            if ws and (pages is None or d.blocks[i].page in pages):
                t = source_text(ws)
                if needs_translation(t):
                    out.append((key, t))
        return out

    def _gen_busy(self) -> bool:
        if self.gen_worker is not None and self.gen_worker.isRunning():
            QMessageBox.information(self, APP_NAME, "È già in corso una traduzione "
                                    "(Traduzione → Interrompi generazione).")
            return True
        return False

    def generate_language_dialog(self):
        if not self.docs:
            QMessageBox.information(self, APP_NAME, "Aggiungi prima il PDF nella lingua sorgente.")
            return
        if self._gen_busy():
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            models, err = ollama_models(), None
        except OSError as e:
            models, err = [], str(e)
        finally:
            QApplication.restoreOverrideCursor()
        dlg = GenerateDialog(self, models, err)
        if dlg.exec() != QDialog.Accepted:
            return
        p = dlg.values()
        self.glossaries = p.pop("glossaries")
        if p["tgt"] in self.docs and os.path.normcase(self.docs[p["tgt"]].path) != os.path.normcase(p["out_path"]):
            r = QMessageBox.question(self, APP_NAME, f"La lingua {p['tgt'].upper()} esiste già nel progetto. "
                                     "Sostituirla con quella generata, alla fine?")
            if r != QMessageBox.Yes:
                return
        if self.project_path:
            self.save_project(quiet=True)
        self._start_generation(p)

    def _start_generation(self, p):
        src = self.docs[p["src"]]
        pages = set(range(min(p["test_pages"], len(src.page_sizes)))) if p["test"] else None
        items = self._translatable_items(p["src"], pages)
        if not items:
            where = f"nelle prime {p['test_pages']} pagine" if pages else "nel documento"
            QMessageBox.information(self, APP_NAME, f"Nessun testo da tradurre {where} "
                                    "(PDF scansionato senza testo?).")
            return
        self._start_translation(dict(p, kind="generate", hash=src.hash), items)

    def _start_translation(self, job, items, force=False):
        cache = TranslationCache(job["hash"], job["src"], job["tgt"], job["model"])
        glossary = parse_glossary(self.glossaries.get(f"{job['src']}>{job['tgt']}", ""))
        w = TranslateWorker(cache, items, job["src"], job["tgt"], job["model"], glossary, force, self)
        w.progress.connect(self._on_gen_progress)
        w.finished.connect(self._on_gen_finished)
        job.update(cache=cache, items=items, t0=time.monotonic())
        self.gen_worker, self.gen_job = w, job
        self.gen_bar.setRange(0, 0)
        self.gen_label.setText(f"Avvio traduzione {job['src'].upper()}→{job['tgt'].upper()}…")
        for wd in (self.gen_label, self.gen_bar, self.gen_stop):
            wd.show()
        self.a_gen_stop.setEnabled(True)
        w.start()

    def _on_gen_progress(self, done, total, eta, last):
        job = self.gen_job
        if job is None:
            return
        self.gen_bar.setRange(0, max(total, 1))
        self.gen_bar.setValue(done)
        what = {"retranslate": "Ritraduzione"}.get(job["kind"], "Prova" if job.get("test") else "Traduzione")
        txt = f"{what} {job['src'].upper()}→{job['tgt'].upper()}: {done}/{total}"
        if eta >= 0 and done < total:
            txt += f" · ~{fmt_duration(eta)}"
        self.gen_label.setText(txt)
        self.gen_label.setToolTip(last)

    def stop_generation(self):
        if self.gen_worker is not None and self.gen_worker.isRunning():
            self.gen_worker.requestInterruption()
            self.gen_label.setText("Interruzione in corso…")

    def _on_gen_finished(self):
        w, job = self.gen_worker, self.gen_job
        self.gen_worker = self.gen_job = None
        for wd in (self.gen_label, self.gen_bar, self.gen_stop):
            wd.hide()
        self.a_gen_stop.setEnabled(False)
        if w is None or job is None:
            return
        if w.error:
            QMessageBox.warning(self, APP_NAME, f"Traduzione interrotta da un errore:\n{w.error}\n\n"
                                "I paragrafi già tradotti restano in cache: rilanciando "
                                "«Genera lingua mancante» si riprende da lì.")
            return
        if not w.completed:
            self.statusBar().showMessage("Traduzione interrotta. I paragrafi tradotti restano in cache: "
                                         "«Genera lingua mancante» riprende da lì.", 8000)
            return
        try:
            if job["kind"] == "retranslate":
                self._finish_retranslate(job)
            elif job["test"]:
                self._after_test(job)
            else:
                self._finish_generation(job)
        except Exception as e:
            QMessageBox.warning(self, APP_NAME, f"Creazione del PDF tradotto non riuscita:\n{e}")

    def _job_source(self, job):
        src = self.docs.get(job["src"])
        if src is None or src.hash != job["hash"]:
            QMessageBox.warning(self, APP_NAME, "Il PDF sorgente non è più aperto nel progetto "
                                "(o è stato sostituito). Le traduzioni restano in cache.")
            return None
        return src

    def _after_test(self, job):
        src = self._job_source(job)
        if src is None:
            return
        cache = job["cache"]
        all_items = self._translatable_items(job["src"])
        left = sum(len(t) for i, t in all_items if cache.get(i, t) is None)
        rate = cache.rate()
        if left == 0:
            est = "già tutto tradotto"
        elif rate:
            est = f"circa {fmt_duration(rate * left)}"
        else:
            est = "non disponibile"
        box = QMessageBox(self)
        box.setWindowTitle("Prova completata")
        where = "della prima pagina" if job["test_pages"] == 1 else f"delle prime {job['test_pages']} pagine"
        box.setText(f"Tradotti {len(job['items'])} paragrafi {where} "
                    f"in {fmt_duration(time.monotonic() - job['t0'])}.\n\n"
                    f"Tempo stimato per il resto del documento ({len(src.page_sizes)} pagine, "
                    f"{len(all_items)} paragrafi): {est}.\n"
                    "La traduzione si può interrompere e riprendere in qualsiasi momento.")
        b_all = box.addButton("Traduci tutto", QMessageBox.AcceptRole)
        b_prev = box.addButton("Apri anteprima", QMessageBox.ActionRole)
        box.addButton("Chiudi", QMessageBox.RejectRole)
        while True:
            box.exec()
            if box.clickedButton() is not b_prev:
                break
            self._open_preview(job, src)
        if box.clickedButton() is b_all:
            self._start_generation(dict(job, test=False))

    def _open_preview(self, job, src):
        cache = job["cache"]
        pages = set(range(min(job["test_pages"], len(src.page_sizes))))
        tr = {i: e["text"] for i, t in job["items"] if (e := cache.get(i, t))}
        path = os.path.join(CACHE_DIR, f"anteprima_{src.hash}_{job['tgt']}_{int(time.time())}.pdf")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            render_translated_pdf(src, tr, path, pages)
        finally:
            QApplication.restoreOverrideCursor()
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _finish_generation(self, job):
        src = self._job_source(job)
        if src is None:
            return
        cache, out, tgt = job["cache"], job["out_path"], job["tgt"]
        old = load_gen_map(out)                 # le correzioni manuali precedenti si conservano
        edited = ({e["key"]: e for e in old["entries"] if e.get("edited")}
                  if old and old["source"]["hash"] == src.hash else {})
        entries = []
        for key, i, rect, ws in doc_units(src):
            e = {"key": key, "i": i, "page": src.blocks[i].page, "rect": [round(v, 2) for v in rect],
                 "src": source_text(ws) if ws else src.blocks[i].text, "text": None,
                 "edited": False, "warn": []}
            if key in edited:
                e.update(text=edited[key]["text"], edited=True, warn=edited[key].get("warn", []))
            elif ws and needs_translation(e["src"]):
                c = cache.get(key, e["src"])
                if c:
                    e.update(text=c["text"], warn=c["warn"])
            entries.append(e)
        gm = {"app": APP_NAME, "kind": "generated", "version": 1,
              "source": {"lang": job["src"], "hash": src.hash, "tk": src.text_key, "path": src.path},
              "target": tgt, "model": job["model"],
              "created": datetime.now().isoformat(timespec="seconds"), "entries": entries}
        self._write_generated(tgt, src, gm, out)
        n_warn = sum(1 for e in entries if e["warn"])
        QMessageBox.information(self, APP_NAME, f"Lingua {tgt.upper()} generata:\n{out}\n\n"
                                + (f"{n_warn} paragrafi da rivedere (numeri o sigle non ritrovati): "
                                   "Traduzione → Paragrafi da rivedere." if n_warn else
                                   "Tasto destro su un paragrafo per correggerne la traduzione."))

    def _write_generated(self, lang, src, gm, out):
        """Ricostruisce il PDF tradotto, salva il file di corrispondenza e (ri)carica la lingua."""
        translations = {e["key"]: e["text"] for e in gm["entries"] if e.get("text")}
        tmp = out + ".tmp"
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            boxes = render_translated_pdf(src, translations, tmp)
        finally:
            QApplication.restoreOverrideCursor()
        for e in gm["entries"]:
            e["box"] = boxes.get(e["key"], e["rect"])
        gm["out_hash"] = file_hash(tmp)
        old = self.docs.get(lang)
        same_file = old is not None and os.path.normcase(old.path) == os.path.normcase(os.path.abspath(out))
        state = None
        if old is not None and lang in self.views:
            v = self.views[lang]
            state = (v.zoom, *v.get_position())
        if same_file:
            old.doc.close()              # su Windows un file aperto non si può sostituire
        try:
            os.replace(tmp, out)
        except OSError:
            os.remove(tmp)
            if same_file:
                self._load_doc(old.path, lang, fit=False)
            raise
        save_json_atomic(gen_map_path(out), gm)
        if not self._load_doc(out, lang, fit=state is None):
            return
        if state:
            v = self.views[lang]
            v.set_zoom(state[0], keep_position=False)
            v.set_position(state[1], state[3])
            QTimer.singleShot(0, lambda: v.set_position(state[1], state[3]))
        if same_file:
            self._reanchor(lang, old.hash)
        self._rebuild_lang_bar()
        self._start_alignment()
        self.refresh_annotations()
        self._update_page_label()
        if self.project_path:
            self.save_project(quiet=True)

    def _reanchor(self, lang, old_hash=None):
        """Le annotazioni di una lingua seguono le parole: dopo la rigenerazione del PDF
        (old_hash) o quando i blocchi sono cambiati per l'OCR (text_key diverso)."""
        d = self.docs[lang]
        for ann in self.annotations:
            for a in (ann["src"], *ann.get("overrides", {}).values()):
                if a.get("lang") != lang:
                    continue
                stale = a.get("hash") == d.hash and a.get("tk", d.hash) != d.text_key
                if a.get("hash") != old_hash and not stale:
                    continue
                if "point" in a:
                    p = d.pos_from_point(*a["point"])
                    a["pos"] = [p, p]
                elif a.get("rects"):
                    r0, r1 = a["rects"][0], a["rects"][-1]
                    g0 = d.word_at(r0[0], r0[1] + 0.5, (r0[2] + r0[4]) / 2)
                    g1 = d.word_at(r1[0], r1[3] - 0.5, (r1[2] + r1[4]) / 2)
                    if g0 is None or g1 is None:
                        continue
                    g0, g1 = min(g0, g1), max(g0, g1)
                    a.update(pos=[d.word_pos(g0), d.word_pos(g1, end=True)],
                             rects=d.rects_for_words(g0, g1), text=d.text_for_words(g0, g1))
                a["hash"], a["tk"] = d.hash, d.text_key

    def _gen_source(self, gm):
        """Documento sorgente di una lingua generata (dal progetto o riaperto dal disco)."""
        d = next((d for d in self.docs.values() if d.hash == gm["source"]["hash"]), None)
        if d is not None:
            return d, False
        p = gm["source"]["path"]
        if os.path.exists(p) and file_hash(p) == gm["source"]["hash"]:
            return Document(p, gm["source"]["lang"]), True
        QMessageBox.warning(self, APP_NAME, f"PDF sorgente non trovato o modificato:\n{p}")
        return None, False

    def regenerate_language(self, lang):
        gm = self.gen_maps.get(lang)
        if gm is None:
            QMessageBox.information(self, APP_NAME, "La lingua corrente non è stata generata con "
                                    f"{APP_NAME} (manca il file {GEN_MAP_EXT} corrispondente).")
            return
        src, temporary = self._gen_source(gm)
        if src is None:
            return
        try:
            self._write_generated(lang, src, gm, self.docs[lang].path)
        except Exception as e:
            QMessageBox.warning(self, APP_NAME, f"Rigenerazione non riuscita:\n{e}")
        finally:
            if temporary:
                src.doc.close()

    def _gen_entry_at(self, lang, page, x, y):
        gm = self.gen_maps.get(lang)
        if gm is None:
            return None
        hits = [e for e in gm["entries"] if e["page"] == page
                and e["box"][0] <= x <= e["box"][2] and e["box"][1] <= y <= e["box"][3]]
        return min(hits, key=lambda e: (e["box"][2] - e["box"][0]) * (e["box"][3] - e["box"][1]),
                   default=None)

    def _flash_entry(self, lang, key):
        gm, v = self.gen_maps.get(lang), self.views.get(lang)
        e = next((e for e in gm["entries"] if e["key"] == key), None) if gm else None
        if e and v:
            v.flash_rects([[e["page"], *e["box"]]], 1500)

    def edit_translation(self, lang, e):
        gm = self.gen_maps[lang]
        current = e.get("text") or e["src"]
        dlg = TranslationEditDialog(self, gm["source"]["lang"], lang, e["src"], current, e.get("warn"))
        if dlg.exec() != QDialog.Accepted:
            return
        new = dlg.text()
        if not new or new == current:
            return
        e.update(text=new, edited=True, warn=missing_tokens(e["src"], new))
        self.regenerate_language(lang)
        self._flash_entry(lang, e["key"])

    def retranslate_paragraph(self, lang, e):
        if self._gen_busy():
            return
        gm = self.gen_maps[lang]
        job = {"kind": "retranslate", "lang": lang, "src": gm["source"]["lang"],
               "tgt": lang, "model": gm["model"], "hash": gm["source"]["hash"]}
        self._start_translation(job, [(e["key"], e["src"])], force=True)

    def _finish_retranslate(self, job):
        lang = job["lang"]
        gm = self.gen_maps.get(lang)
        if gm is None or gm["source"]["hash"] != job["hash"]:
            return
        key, t = job["items"][0]
        c = job["cache"].get(key, t)
        e = next((e for e in gm["entries"] if e["key"] == key), None)
        if c is None or e is None:
            return
        e.update(text=c["text"], warn=c["warn"], edited=False)
        self.regenerate_language(lang)
        self._flash_entry(lang, key)

    def review_list(self):
        lang = self.current
        gm = self.gen_maps.get(lang) if lang else None
        if gm is None:
            QMessageBox.information(self, APP_NAME, f"La lingua corrente non è stata generata con {APP_NAME}.")
            return
        rows = [e for e in gm["entries"] if e.get("warn")]
        if not rows:
            QMessageBox.information(self, APP_NAME, "Nessun paragrafo da rivedere: tutti i numeri "
                                    "e le sigle dell'originale compaiono nella traduzione.")
            return
        labels = [f"p. {e['page'] + 1} — manca {', '.join(e['warn'][:4])} — «{short(e['text'] or '', 60)}»"
                  for e in rows]
        item, ok = QInputDialog.getItem(self, "Paragrafi da rivedere",
                                        f"{len(rows)} paragrafi con numeri o sigle non ritrovati "
                                        "nella traduzione\n(tasto destro sul paragrafo per correggerlo):",
                                        labels, 0, False)
        if ok:
            e = rows[labels.index(item)]
            self.views[lang].set_position(e["page"], e["box"][1])
            self._flash_entry(lang, e["key"])
            self._update_page_label()

    # ---- progetto ----
    def _state(self):
        v = self._view()
        if v is None:
            return {}
        pno, _x, y = v.get_position()
        return {"lang": self.current, "page": pno, "y": round(y, 1), "zoom": round(v.zoom, 3)}

    def _restore_state(self, st):
        lang = st.get("lang")
        if lang not in self.views:
            return
        v = self.views[lang]
        self.current = lang
        self.stack.setCurrentWidget(v)
        self._rebuild_lang_bar()
        v.set_zoom(st.get("zoom", v.zoom), keep_position=False)
        for other in self.views.values():
            other.set_zoom(v.zoom, keep_position=False)
        page = min(max(int(st.get("page", 0)), 0), len(v.offsets) - 1)
        v.set_position(page, float(st.get("y", 0)))
        self.refresh_panel()
        self._update_page_label()

    def save_project(self, save_as=False, quiet=False) -> bool:
        if not self.docs:
            return False
        if save_as or not self.project_path:
            path, _ = QFileDialog.getSaveFileName(self, "Salva progetto", "progetto" + PROJECT_EXT,
                                                  f"Progetto {APP_NAME} (*{PROJECT_EXT})")
            if not path:
                return False
            if not path.endswith(PROJECT_EXT):
                path = re.sub(r"\.json$", "", path) + PROJECT_EXT
            self.project_path = path
        base = os.path.dirname(os.path.abspath(self.project_path))
        docs = []
        for lang, d in self.docs.items():
            try:
                rel = os.path.relpath(d.path, base)
            except ValueError:  # unità diverse su Windows
                rel = d.path
            docs.append({"lang": lang, "path": rel})
        data = {"app": APP_NAME, "version": 2, "documents": docs,
                "state": self._state(), "annotations": self.annotations,
                "glossaries": self.glossaries}
        tmp = self.project_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.project_path)
        except OSError as e:
            QMessageBox.warning(self, APP_NAME, f"Salvataggio non riuscito:\n{e}")
            return False
        self._update_title()
        if not quiet:
            self.statusBar().showMessage("Progetto salvato", 3000)
        return True

    def open_project_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Apri progetto", "",
                                              f"Progetto {APP_NAME} (*{PROJECT_EXT} *.json)")
        if path:
            self.open_project(path)

    def open_project(self, path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, APP_NAME, f"Progetto non leggibile:\n{e}")
            return
        self._close_all()
        base = os.path.dirname(os.path.abspath(path))
        missing = []
        for entry in data.get("documents", []):
            p = entry["path"] if os.path.isabs(entry["path"]) else os.path.join(base, entry["path"])
            if os.path.exists(p):
                self._load_doc(p, entry["lang"])
            else:
                missing.append(p)
        if missing:
            QMessageBox.warning(self, APP_NAME, "File non trovati:\n" + "\n".join(missing))
        self.project_path = os.path.abspath(path)
        self.annotations = data.get("annotations", [])
        self.glossaries = data.get("glossaries", {})
        for lang in self.docs:          # blocchi cambiati dall'ultimo salvataggio (OCR)
            self._reanchor(lang)
        self._rebuild_lang_bar()
        self._start_alignment()
        self.refresh_annotations()
        st = data.get("state") or {}
        if st:
            QTimer.singleShot(80, lambda: self._restore_state(st))
        self._update_page_label()
        QTimer.singleShot(200, self._start_ocr)

    def new_project(self):
        if self.docs and not self._maybe_save():
            return
        self._close_all()
        self.project_path = None
        self._update_title()

    def _maybe_save(self) -> bool:
        """True se si può procedere (salvato o scartato)."""
        if self.project_path:
            return self.save_project(quiet=True)
        r = QMessageBox.question(self, APP_NAME, "Salvare il progetto (con note ed evidenziazioni)?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Cancel:
            return False
        if r == QMessageBox.Save:
            return self.save_project()
        return True

    # ---- drag & drop / chiusura ----
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        files = [u.toLocalFile() for u in e.mimeData().urls()]
        projects = [f for f in files if f.lower().endswith(".json")]
        pdfs = [f for f in files if f.lower().endswith(".pdf")]
        if projects:
            self.open_project(projects[0])
        elif pdfs:
            self.add_pdfs(pdfs)

    def closeEvent(self, e):
        if self.gen_worker is not None and self.gen_worker.isRunning():
            r = QMessageBox.question(self, APP_NAME, "È in corso una traduzione. Interromperla?\n"
                                     "I paragrafi già tradotti restano in cache e si potrà riprendere.")
            if r != QMessageBox.Yes:
                e.ignore()
                return
            self.gen_worker.requestInterruption()
            self.gen_worker.wait(10000)
        if self.docs and not self._maybe_save():
            e.ignore()
            return
        if self.worker is not None and self.worker.isRunning():
            self.worker.requestInterruption()
            self.worker.wait(3000)
        self._kill_ocr()        # le pagine già riconosciute restano in cache
        self.probe_timer.stop()
        for v in self.views.values():
            v.close_popup()
        for w in self.findChildren(QThread):        # traduzioni al volo e controllo di Ollama
            w.requestInterruption()
            w.wait(2000)
        e.accept()


def main():
    os.makedirs(CACHE_DIR, exist_ok=True)
    if len(sys.argv) > 1 and sys.argv[1] == "--ocr":     # processo OCR avviato dall'app
        try:
            code = ocr_main(sys.argv[2:])
        except Exception as e:     # l'.exe non ha console: l'errore passa da un file
            with open(OCR_ERROR_FILE, "w", encoding="utf-8") as f:
                f.write(f"{e.__class__.__name__}: {e}")
            code = 1
        sys.exit(code)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    here = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))   # anche da .exe
    for icon in (os.path.join(here, "polyglot_pdf.ico"), os.path.join(here, "packaging", "polyglot_pdf.ico")):
        if os.path.exists(icon):
            app.setWindowIcon(QIcon(icon))
            break
    w = MainWindow()
    w.show()
    args = sys.argv[1:]
    projects = [a for a in args if a.lower().endswith(".json")]
    pdfs = [a for a in args if a.lower().endswith(".pdf")]
    if projects:
        QTimer.singleShot(0, lambda: w.open_project(projects[0]))
    elif pdfs:
        QTimer.singleShot(0, lambda: w.add_pdfs(pdfs))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
