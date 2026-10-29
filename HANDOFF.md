# PolyglotPDF — Project status and technical notes

> Updated 30.10.2026 · version **0.6.0** from source (Windows installer last built at 0.4.0, portable ZIP at 0.4.1; executables are built only on request) · main file: `polyglot_pdf.py` (single file, ~4350 lines)
> If you use Claude Code, you can rename this file to `CLAUDE.md`: it is then read automatically at every session.

## 1. Goal
Python desktop tool to read the **same PDF document in several languages**:
1. fast language switch **keeping the reading position** ✅
2. **highlights and notes** made in one language visible in the others ✅
3. **generation of a missing language** with a local, offline, free LLM, keeping layout and images ✅ *(tested with the real translategemma:4b)*
4. **semantic alignment** without extra Python dependencies (embeddings from Ollama) and **OCR of scanned pages** ✅
5. **interface in English or Italian** and **translation of the selected text** in a note-like popup ✅

Main use case: technical manuals that already exist in several languages (IT/DE/FR/EN). Initial survey: no free software covers everything (PDFMathTranslate/BabelDOC translate but do not synchronise; PdfSideViewer shows two PDFs side by side but without synchronised notes).

## 2. Environment
- **OS**: Windows 11. Project folder: `C:\Temp\Claude workplace\Polygot PDF\`. Repository: https://github.com/metfidan-dev/Polyglot_PDF (public, AGPL-3.0).
- **Hardware of the development PC**: HP Laptop 14-cf1xxx, Intel i5-8265U (4 cores / 8 threads), **8 GB RAM** (one 8 GB DDR4-2400 module, second slot free, max 32 GB; often < 1 GB free with browser and VS Code open → model loading 50–80 s), **no dedicated GPU** (Intel UHD 620 only) → LLM on the CPU, 128 GB SSD.
- **Dependencies** (`requirements.txt`): installed in the project virtual environment **`.venv`** (PySide6 6.12, PyMuPDF 2.0, numpy 2.5), based on Python 3.13 from python.org (`%LOCALAPPDATA%\Programs\Python\Python313`). The Microsoft Store Python was uninstalled on 26.10.2026 and the old `.venv` stopped working; if it happens again: `<python> -m venv --clear .venv`, then `pip install -r requirements.txt`.
  ```bat
  .venv\Scripts\python -m pip install -r requirements.txt
  .venv\Scripts\python -m pip install sentence-transformers   :: optional: in-process semantic alignment (otherwise Ollama)
  ```
  ⚠️ The package is called `pymupdf`. Do **not** install the `fitz` package from PyPI, which is something else. Since PyMuPDF 2.0 `import fitz` is deprecated: the code uses `import pymupdf as fitz` (falling back to `fitz` for old versions).
  ⚠️ PySide6 contains very long paths: in a venv inside deep folders the installation fails without Windows "Long Paths" support (the Microsoft Store Python had this problem).
- **Ollama** ([ollama.com](https://ollama.com)) installed and running: `ollama pull translategemma:4b` (translation) and `ollama pull paraphrase-multilingual` (563 MB, semantic alignment). Address `http://127.0.0.1:11434` (or the `OLLAMA_HOST` variable; `localhost` and `0.0.0.0` are converted to 127.0.0.1, see 0.5.1).
- **OCR**: Tesseract is **not** installed separately, it is built into PyMuPDF. Language data (`ita/deu/fra/eng.traineddata`, 14–22 MB each) in `~/.cache/polyglot_pdf/tessdata`; for other languages the app asks and downloads them from github.com/tesseract-ocr/tessdata.
- **Start from source**: `avvia.bat` (always uses `.venv`, also with a double click) or `.venv\Scripts\python polyglot_pdf.py [project.polypdf.json | a_it.pdf b_de.pdf ...]`. With the system Python a message explains how to start.
- **Windows installer** (version from `APP_VERSION`): `powershell -ExecutionPolicy Bypass -File packaging\build.ps1` → `packaging\output\PolyglotPDF-Setup-<version>.exe` (~54 MB). PyInstaller (`packaging\dist\PolyglotPDF`, ~220 MB) + Inno Setup 6 (`winget install -e --id JRSoftware.InnoSetup --scope user`). Per-user installation in `%LOCALAPPDATA%\Programs\PolyglotPDF`, no administrator rights; Start menu, optional desktop icon, uninstall from "Installed apps". It does not include `sentence-transformers` nor Ollama. The executable is not signed: Windows SmartScreen may ask for confirmation at the first start ("More info" → "Run anyway").
- **Portable version**: `... packaging\build.ps1 -Portable` → `packaging\output\PolyglotPDF-<version>-portable.zip` (~104 MB, no Inno Setup). Contains `PolyglotPDF.exe`, `_internal\`, `portable.txt` and `dati\tessdata` (ita/deu/fra/eng). With `portable.txt` next to the .exe, `_data_dir()` puts cache/OCR/alignments/settings into `.\dati` (verified: 0 files in the user profile).
- `pymupdf.layout` (ONNX model that PyMuPDF 2.x loads at every import, ~2 s) is disabled at the top of the file: text extraction verified identical.
- **Data folder** `~/.cache/polyglot_pdf/` (or `.\dati` in the portable version): embeddings `emb_<text_key>_<model>.npy`, alignments `align_<tkA>_<tkB>_<method>_v1.json` (method `semantico` / `semantico-ollama` / `euristico`: internal keys, kept in Italian for cache compatibility), translations `tr_<hash>_<src>_<tgt>_<model>_v1.json`, previews `anteprima_*.pdf`, OCR `ocr_<hash>_<langs>_v1.json` (+ `.progress`), `ocr_errore.txt`, `tessdata/`, **`settings.json`** (`{"ui_lang": "en" | "it"}`).
- `text_key` = file hash + `o` if the text of scanned pages comes from OCR: identifies the block structure (embedding/alignment caches, annotation anchors).

## 3. Architecture (`polyglot_pdf.py`)
| Section | Content |
|---|---|
| Interface language | `UI_LANG` (`en`/`it`), `_t(text)` → `IT_TEXTS[text]` in Italian, otherwise the English text itself. **English is the source language**; the Italian translations are in `IT_TEXTS` at the end of the file, with the same `{placeholders}` (texts with values use `_t("… {name} …").format(name=…)`, not f-strings). `default_ui_lang()`: saved choice in `settings.json`, otherwise the Windows language (`QLocale`: Italian → `it`, otherwise `en`); set in `main()` before the window is created. `lang_display()` = document language names in the interface language. Menu *Help → Language / Lingua* (`MainWindow.set_ui_lang`): saves the choice, effective at the next start. |
| `Block`, `extract_blocks()` | Text blocks per page with their words (`get_text("words")` + `"blocks"` on the same `textpage`, or the cached OCR data). At least one block per page (placeholder block for pages without text). |
| OCR | `scanned_pages()` (pages without text with an image > 30 % of the page), `load_ocr()` (uses only **complete** caches), `ocr_main()` = **separate process** `polyglot_pdf.py --ocr <pdf> <hash> <langs> <pages>` (PyMuPDF does not support several threads; from the .exe: `PolyglotPDF.exe --ocr …`), `get_textpage_ocr(dpi=300, full=True)`, partial save every 5 s → resume. Process errors in `ocr_errore.txt` (the .exe has no console). |
| `Document` | PDF + language + SHA1 hash + `text_key`, `ocr_pages`, `pending_ocr`. **Continuous position** = `block_index + fraction`. Global word index `wg`. Methods: `pos_from_point`, `point_from_pos`, `word_at`, `word_pos`, `words_in_pos_range`, `rects_for_words` (merged per line), `text_for_words`. |
| Alignment | `align_blocks()` → anchors `(i, j, sim)`: best mutual match in a band around the diagonal, then longest increasing subsequence (`_longest_increasing`). **Semantic** similarity (`_rows_embedding`; `get_embedding_model()`: `_STEmbedder` with `paraphrase-multilingual-MiniLM-L12-v2` if sentence-transformers is installed, otherwise `OllamaEmbedder` with `paraphrase-multilingual` via `/api/embed`, checked again at every alignment; if Ollama fails half-way → heuristic) or **heuristic** (`_rows_heuristic`: language-invariant tokens — numbers, acronyms, codes — IDF-weighted + length ratio). `build_mapping()` → piecewise monotonic function `posA → posB`. |
| `block_word_matches()`, `intra_block_map()` | **Fine alignment inside a paragraph**. Word→word correspondences: invariant tokens, punctuation (`:` `;` brackets, if equal in number) and **cognates** (`_fold` without accents + `difflib` ≥ 0.72, same initial, best mutual: Sensor/sensore, Intelligentes/intelligente). The monotonic map uses those in order. `MainWindow._paired_block` / `_intra` (cache per `text_key`) / `map_pos_fine`; `_direct_words`: highlights ≤ 8 words taken directly from the correspondences, also in reversed order («Intelligentes Design» → «design intelligente»). |
| `AlignWorker(QThread)` | Computes the alignments of all language pairs in the background, with cache. |
| `HighlightItem`, `NoteIconItem` | Graphic items of the annotations (highlight in *Multiply* mode; dashed line = estimated position). |
| `PdfView(QGraphicsView)` | Continuous scrolling, lazy page rendering (±1 page, dropped beyond ±2), zoom, mouse text selection, panning with the middle button. Signals: `selectionMade`, `annotationClicked`, `annotationActivated`, `contextRequested`, `pageClicked`. |
| Ollama | `ollama_base()` (127.0.0.1), `ollama_models()` (`/api/tags`), `ollama_loaded()` (`/api/ps`), `ollama_preload()`, `ollama_generate()` (`/api/generate` **streaming**, interruptible between tokens, `on_token`), `translation_prompt()` (TranslateGemma prompt + rule on numbers/codes + glossary entries **present in the text**), `ollama_translate()` (paragraphs > 1500 characters split by sentence, `on_partial`). `OLLAMA_OPTIONS` = same options everywhere (a different `num_ctx` would reload the model). Only `urllib`. |
| Translation units | `doc_units(doc)` → `(key, block, rectangle, words)`. Key `"i"` = whole block; `"i.k"` = cell k when PyMuPDF merged several **table cells** into a block (`block_cells`: horizontal gaps > 2× line height). `source_text()` joins words hyphenated at the end of a line (not "Ein- und"). `needs_translation()` excludes blocks made only of numbers/acronyms. |
| `TranslationCache`, `TranslateWorker(QThread)` | Cache per unit (checked against the source text), saved every 15 s and at the end → **resume**. `rate()` = measured seconds/character → time estimate. `missing_tokens()` flags lost numbers/acronyms (ignores long upper-case words like ATTENZIONE). |
| `render_translated_pdf()` | For each unit: prevailing style (`_block_style`: size, colour, bold/italic, serif, line spacing, left/justified/centred alignment relative to the column), box widened towards the free space (`_free_box`: stops at text, images, lines/borders from `get_drawings()`), redaction of the **lines** (fill=False, images and graphics untouched; **OCR** pages: box covered with the paper colour, `_background`, font size from `_ocr_font_size`, the scan is not an obstacle), `insert_htmlbox` with the first line aligned to the original (measured on a scratch page). |
| `exact_anchors()` | Exact source → generated PDF anchors from the boxes of the `.polymap.json`. Put into `mappings` by `_add_exact_mappings()` instead of the estimated computation. |
| `OllamaProbe`, `ModelPreloader`, `SelectionTranslateWorker`, `TranslationPopup` | Ollama check in a thread (every 10 s, `MainWindow.check_ollama` → `_show_tr_status`); model preloading when the selection menu appears; on-the-fly translation of the selection, shown while it streams; yellow box **child of the viewport** of `PdfView`, repositioned by `place_popup` (scrolling, zoom, resize; below the selection or above if there is no room) and closed by `clear_selection`. In-memory cache per session `(text, src, tgt, model)`. |
| `GenerateDialog`, `TranslationEditDialog` | "Generate language" dialog (source, target, model, test on N pages, file, glossary per pair, Ollama and cache status); editing of a paragraph (original + translation). |
| `MainWindow` | Project, language bar, language switch, annotation panel (dock), undo, export, saving. Generation: `_start_generation` → `_after_test` (estimate) / `_finish_generation` → `_write_generated` (PDF + `.polymap.json` + reload, closes the PDF before replacing it) → `_reanchor` (annotations follow the words after a regeneration **or a `text_key` change**, also when opening the project). OCR: `_start_ocr` (automatic after opening/adding; asks to download missing language data) → `QProcess` + `_ocr_poll` (progress from the `.progress` file) → `_ocr_finished` → `_reload_text` (reload, position kept, re-anchoring, new alignment, saving). `stop_ocr`, `ocr_current`. |

### Language switch
`get_position()` (reading position at 1/3 of the window height) → `pos_from_point` → `map_pos` → `point_from_pos` → `set_position` in the target view. Every language has its own `PdfView` in a `QStackedWidget`, so the switch is instant. Before the alignment is ready a proportional mapping is used.

### Annotations
- Every annotation has an exact **source anchor** (`src`) in the language where it was created, plus optional **manual overrides** per language.
- In the other languages the position is **computed on the fly** (`_ann_render`) through `map_pos_fine`. If the alignment improves, the annotations update themselves.
- An anchor is valid only if `hash` matches the loaded PDF. Field `tk` (= `text_key` at creation; missing = hash): if different, the position is recomputed from the rectangles/point.

## 4. Project format `.polypdf.json` (version 2)
```json
{
  "app": "PolyglotPDF", "version": 2,
  "documents": [{"lang": "it", "path": "manuale_it.pdf"}, {"lang": "de", "path": "handbuch_de.pdf"}],
  "state": {"lang": "it", "page": 3, "y": 120.5, "zoom": 1.2},
  "annotations": [{
    "id": "a1b2c3d4e5f6", "kind": "highlight | note", "color": "#FFD400",
    "note": "text", "created": "2026-10-19T16:40:00",
    "src": {"lang": "it", "hash": "…", "tk": "…", "pos": [p0, p1],
            "rects": [[page, x0, y0, x1, y1]], "text": "…"},
    "overrides": {"de": {"…same structure…"}}
  }],
  "glossaries": {"it>de": "sezionatore = Trennschalter\n…"}
}
```
- Paths relative to the project folder.
- Point notes: the anchor has `"point": [page, x, y]` instead of `rects`/`text`.
- Atomic saving (`.tmp` + `os.replace`). After the first save, automatic saving at every change.
- `glossaries` (optional): one entry per line, also `->`, `→` or tab; `#` = comment.

### Mapping file `<name>.polymap.json` (next to the generated PDF)
```json
{
  "app": "PolyglotPDF", "kind": "generated", "version": 1,
  "source": {"lang": "it", "hash": "…", "tk": "…", "path": "C:\\…\\manuale_it.pdf"},
  "target": "de", "model": "translategemma:4b", "created": "…", "out_hash": "…",
  "entries": [{"key": "6.1", "i": 6, "page": 0, "rect": [x0, y0, x1, y1], "box": [x0, y0, x1, y1],
               "src": "Valore", "text": "Wert", "edited": false, "warn": []}]
}
```
- One entry per text unit of the source; `text: null` = left as it was (page numbers, values).
- `box` = box where the translation was written → exact mapping and click on the paragraph.
- Valid only if `out_hash` matches the loaded PDF (PDF modified elsewhere → treated as a normal PDF).
- `edited: true` = manual correction: **kept** even when regenerating the whole language.

## 5. User functions (English menu names; Italian in brackets)
- **Languages**: `Ctrl+1…9`, `Ctrl+Tab` / `Ctrl+Shift+Tab`. The matching paragraph flashes (can be disabled in View).
- **Navigation**: `Ctrl+wheel` zoom, `Ctrl+0` fit width, `Ctrl+G` go to page, middle button to drag.
- **Annotations**: drag to select → menu (5 colours, with note, copy, translate). `H` highlight, `N` highlight with note, `Alt+1…5` colour, `Ctrl+Shift+H` highlighter mode, `Ctrl+Shift+N` point note, double click to edit, right click: colour / delete / **Fix position in XX** / restore automatic position. `Ctrl+Z` undo (50 steps), `Esc` cancels the mode.
- **File**: `Ctrl+L` add PDF/language (language suggested from the file name or the content), drag & drop, `Ctrl+E` **export annotated PDFs** (`<name>_annotated.pdf`, Italian UI `_annotato.pdf`, with standard PDF annotations), recompute alignment.
- **OCR**: automatic in the background when a PDF with scanned pages is opened (status bar «OCR IT: page 3/40» + *Stop OCR*); afterwards the text can be selected and annotated. Menu *Alignment → Recognise text in scanned pages (OCR)* to resume after an interruption or a refusal.
- **Version**: in the window title, at the bottom right of the status bar (`v0.6.0`) and in *Help → About PolyglotPDF…* (also Ollama status, data folder, Python/PyMuPDF/Qt versions).
- **Interface language**: *Help → Language / Lingua* → English / Italiano (effective at the next start). Default: the Windows language.
- **Translation status** (status bar): **● Translation available** in green (Ollama running with a `translategemma` model), **● Translation not available** in red (Ollama not detected, or running without the model: the tooltip says what to do). Automatic check every 10 s, click to check at once.
- **Translate selection**: select text → menu (also right click) **Translate into Italian (IT)** or **Translate into ▸** another language, or key `T`. Note-like box anchored to the selection: the translation appears while it arrives, the language can be changed in the box (remembered for the session), *Copy*, *Save as note* (highlight with note «[IT] …»). It closes with ✕, `Esc`, a new selection or a click elsewhere; closing it half-way stops the translation. Items disabled if translation is not available. Uses the glossary of the language pair. The model is preloaded as soon as the selection menu appears.
- **Generate missing language** (`Ctrl+Shift+G`) → (recommended) test on 2–3 pages → message with estimated time: *Translate everything* / *Open preview* / *Close*. Progress and remaining time in the status bar, *Stop* button (resumes when restarted). At the end the PDF `manual_de.pdf` is added to the project as a new language. *Paragraphs to review…* (lost numbers/acronyms), *Regenerate PDF of the current language*. Right click on a paragraph of the generated language: **Edit paragraph translation…** / **Translate paragraph again** → PDF regenerated, reading position kept.

## 6. Version history and tests
Tests run offscreen (`QT_QPA_PLATFORM=offscreen`) with real Ollama and OCR unless stated otherwise. **The test scripts are not in the repository**: they lived in the session scratchpad and part of them was lost in a temp-folder cleanup (October 2026). If needed: ask to recreate them in `tests/`. In offscreen tests every modal dialog (`QMessageBox.question/about/information/warning`) must be replaced, otherwise the test hangs (e.g. «Save the project?» when closing the window).

**0.6.0** (29.10.2026) — everything in English for a wider audience:
- Interface bilingual EN/IT (English source texts, `IT_TEXTS` with 214 entries), README in English (`README.md`) with an Italian version (`README.it.md`), comments, docstrings and these notes in English.
- Verification: compared with 0.5.1 the **program structure (AST) is identical** apart from the planned additions (texts, comments and docstrings normalised away; only differences: `QLocale` import, language functions, *Language* menu, `set_ui_lang`, `lang_display()` in the *Translate into* menu, `_annotated` suffix, `IT_TEXTS`, choice of the language in `main()`). All `_t()` texts have a translation with the same placeholders.
- New test of the English interface (171 texts checked for leftover Italian, language menu and saved choice, Italian interface unchanged) + all previous tests.
- Fix: the "Generate missing language" dialog wrote «1 pages» / «1 pagine» for one-page documents.
- **Screenshots** for the READMEs in `docs/screenshots/en` and `docs/screenshots/it` (4 each, 580 KB in total): fictional demo manual «AquaFlow P-200» in IT/DE/EN generated with PyMuPDF (never use third-party PDFs: the repository is public), captured from the real app with Ollama through `QWidget.grab()` with `Qt.WA_DontShowOnScreen` (native Windows 11 style, nothing appears on screen; the `offscreen` platform has no fonts unless `QT_QPA_FONTDIR=C:/Windows/Fonts` and does not use the native style). The path in the generate dialog is replaced by a neutral `C:\Manuals\…` (the real one contains the Windows user name).

**0.5.1** (26.10.2026) — translation speed:
- `ollama_base()` uses **127.0.0.1** (also instead of `localhost`/`0.0.0.0` in `OLLAMA_HOST`): with «localhost» Windows tries IPv6 first and every request lost ~2 s (measured 2–3 s → 0.03 s). Applies everywhere: selection translation, language generation, embeddings, status check.
- **Model preloading** when the selection menu appears (`preload_model` → `ModelPreloader`: `/api/ps`, then `/api/generate` without prompt with the **same** `OLLAMA_OPTIONS`). The box says «Loading the model into memory…» if the model is not ready (`ollama_loaded`).
- Measurements (single word, DE→IT): model in memory 6.9 s → 5.3–5.5 s (Ollama directly); ~4.7 s are the reading of the ~100 prompt tokens. Model to be loaded: 50–80 s with little free RAM; preloading hides the part spent choosing from the menu (81 s → 44 s from the click, after 8 s of menu).
- Not applied (user's choice): shorter instructions for selections without numbers/codes (51 tokens instead of 99, ~1–2 s less).

**0.5.0** (22.10.2026) — version shown, Ollama status indicator, translation of the selection: version, green/red indicator (Ollama running / stopped / without model, automatic return to green), menu, translation of the selection (first time ~30 s with model loading, then a few seconds; streamed), box attached to the selection also when scrolling and zooming, cache, language change, save as note, closing, interruption: **33/33**.

**0.4.1** (22.10.2026) — report: «the alignment seems worse» (datasheet Turbimax CUS52D DE/IT).
- Cause: the paragraph was right, the **word** was not (proportional estimate) + bug in `Document._pos_to_word` (rounding: 7 + 3/18 → one extra word at the sides of every estimated highlight). Fixed with a 1e-6 tolerance: word→position→word round trip exact 21 588/21 588.
- DE→IT highlights on the real datasheet: 10/10 on the right words (before: «Handling:» → «un sensore», now «d'uso:»; «Intelligentes» → «il design», now «intelligente»).
- Paragraphs, content-based comparison (unambiguous translation, ±1 page): TI01136C 95.0 % semantic vs 92.3 % heuristic; CUS52D 82.1 % vs 78.6 %. Note: a «same page» criterion wrongly penalises the semantic method when the text moves to the next page in one language.

**0.4.0** (22.10.2026) — semantic alignment via Ollama and OCR. IT PDF of 6 pages (pages 2–3 scanned) + native DE PDF with different pagination, one missing paragraph and **no numbers/acronyms**: **24/24**; OCR interruption/resume/error: **10/10** (with 30 pages: the faster OCR finished 8 pages before the test could stop it).
- Heuristic: 0 anchors on these texts; **semantic via Ollama: 23/23 correct paragraphs** (similarity of translations 0.88–0.96, unrelated ≤ 0.46; ~11 paragraphs/s on CPU, first model loading ~15 s).
- OCR: ~0.7–1.8 s/page (300 dpi), exact text; highlight on a later page re-anchored on the same words, note on the scanned page, save/reopen without repeating the OCR.
- Generation from a scanned source: original text covered (the OCR of the generated page no longer finds it), image kept, 12–13 pt font for a 12 pt original.
- Packaged `.exe`: `--ocr` works without a console; when a project is opened the OCR starts by itself and the annotations are saved with the new `tk`.

**0.3.0** (19.10.2026) — generation of a missing language, with a **fake Ollama server** (IT→DE dictionary, streaming, longer text, one code "lost" on purpose), synthetic 4-page PDF with centred title, justified paragraphs, hyphenated word, coloured box, image, ruled table: **98/98**.
- TranslateGemma prompt format; glossary inserted only if the term appears.
- Test on 2 pages + estimate; interruption half-way and resume: **only** the missing units translated again.
- Generated PDF: original text removed, image and graphics kept, page number unchanged, no overlap, centred/bold/same-size title, **first line at the same height** (69.5 ↔ 69.5 pt), body not shrunk, colour and box limits respected, **every table cell in its column**.
- Exact alignment 40/40 anchors; IT highlight found on the DE text; language switch on the right page.
- Edit and re-translation of a paragraph, annotation made in DE re-anchored after regeneration, manual corrections kept, save/reopen, clear message with Ollama stopped or model missing.
- Real measurements (Ollama 0.40.6, translategemma:4b, i5-8265U CPU only): model loading ~50 s (first time; then `keep_alive` 30 min); generation ~7–8 tokens/s; **prompt processing ~4 s per call** (Ollama does not reuse the common prefix with this model) → every cell or short title costs ~6 s, a 270-character paragraph ~20 s; test page (1 100 characters, 12 units): 166 s → **100-page manual ≈ 4–8 h**. Quality IT→DE/FR good: codes and units intact, no added comments. **Glossary only partly respected** (e.g. «quadro elettrico» → «elektrisches Gehäuse» instead of «Schaltschrank»).

**0.2 / 0.1** — synthetic PDFs IT 10 p. / DE 12 p.: heuristic alignment 120/120 correct paragraphs, without numbering and with 5 missing paragraphs 115/115; switch IT→DE→IT keeps the position; IT highlight «centrale 40 con protocollo IEC 61850.» → estimated DE «40 mit dem Protokoll IEC 61850.»; mouse selection, undo, PDF export (Highlight + Text annot), save and reopen: OK.

## 7. Known limits
- `get_text("blocks", sort=True)`: on multi-column pages the reading order can mix the columns.
- The fine estimate inside a paragraph uses numbers/acronyms, punctuation and cognates: words without a match (e.g. Kalibrierung/taratura) stay proportional, and a highlight can cover a few words less than the translated phrase (same number of words as the original).
- Page rendering on the main thread (possible slowness on heavy PDFs at high zoom).
- Rotated pages not verified.
- Annotations already present in the original PDFs are shown (rendered) but not imported into the project.
- Language generation:
  - Style per paragraph: **inline** formatting (one bold word, e.g. «**ATTENZIONE:** …») is lost, the prevailing style applies.
  - Fonts: generic MuPDF `sans-serif`/`serif` families, not the original font. Non-Latin scripts (Chinese, Japanese…) not verified.
  - To replace the generated PDF the file must not be open in another program (Acrobat locks it on Windows): in that case a warning appears and the previous version stays.
  - Text that does not fit even in the free space → the font is reduced (no minimum).
  - After a regeneration, *Undo* (Ctrl+Z) can bring back annotations with the old hash → in that language they become "estimated" again.
  - Scanned pages: translated after OCR, the new text covers the original with a box of the paper colour (on patterned or photo backgrounds the box is visible).
- OCR:
  - Only on pages **entirely** without text: mixed pages (digital text + scanned parts) are not recognised.
  - Romansh: no Tesseract model, `ita+deu` is used. Languages not in `OCR_LANGS` → `eng`.
  - Annotations made on a scanned page **before** the OCR are re-anchored to the nearest word (approximate position).
  - A PDF generated from a source whose OCR changed after the generation loses the exact alignment (becomes estimated).
- Interface language: the change takes effect at the next start (the window is not rebuilt live).

## 8. Possible next steps
1. Test on more **real manuals** (complex tables, two columns, footnotes) and evaluate translation and layout quality.
2. Shorter prompt for short selections (~1–2 s less per translation).
3. Group several short units (cells, titles) into one request to amortise the ~4 s of prompt processing; flag among "to review" the paragraphs where a glossary term was not respected.
4. Inline formatting (bold/italic spans → HTML), original or embedded Noto fonts for non-Latin scripts, BabelDOC (AGPL-3.0, OpenAI-compatible endpoint → Ollama) as an alternative engine for complex layouts, Argos Translate as a "fast" engine.
5. Move the test scripts into `tests/` in the repository.

## 9. Development preferences
- **Incremental** development, complete and working code at every step, tested before delivery.
- Replies to the developer: concise and structured, **in Italian**.
- Code, comments, docstrings, documentation and commit messages **in English**; interface **bilingual EN/IT** (every new text: English in `_t("…")` + Italian entry in `IT_TEXTS`).
- Build the .exe / installer / portable version **only when explicitly asked**.
