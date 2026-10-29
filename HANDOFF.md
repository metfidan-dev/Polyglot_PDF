# PolyglotPDF — Stato del progetto (handoff per VS Code)

> Aggiornato al 26.10.2026 (versione 0.5.1 da sorgente — installer fermo alla 0.4.0, portabile alla 0.4.1; build solo su richiesta) · File principale: `polyglot_pdf.py` (singolo file, ~3750 righe)
> Se usi Claude Code in VS Code, puoi rinominare questo file in `CLAUDE.md`: viene letto automaticamente a ogni sessione.

## 1. Obiettivo
Tool desktop Python per leggere lo **stesso documento PDF in più lingue**:
1. cambio lingua rapido **mantenendo il punto di lettura** ✅
2. **evidenziazioni e note** fatte in una lingua visibili anche nelle altre ✅
3. **generazione di una lingua mancante** con LLM locale, offline e gratuito, mantenendo layout e immagini ✅ *(provato con translategemma:4b reale)*
4. **allineamento semantico** senza dipendenze Python in più (embedding da Ollama) e **OCR delle pagine scansionate** ✅

Caso d'uso principale: manuali tecnici già esistenti in più lingue (IT/DE/FR/EN). Analisi iniziale: nessun software gratuito copre tutto (PDFMathTranslate/BabelDOC traducono ma non sincronizzano; PdfSideViewer affianca due PDF ma senza note sincronizzate).

## 2. Ambiente
- **OS**: Windows. Cartella attuale: `C:\Temp\Claude workplace\Polygot PDF\` (prima: `C:\Temp\Python\Polyglot PDF\`)
- **Hardware**: Intel i5-8265U (4 core / 8 thread), **8 GB RAM** (7,9 GB visibili a Windows, misurato il 26.10.2026; spesso < 1 GB liberi con browser e VS Code aperti → caricamento del modello 50–80 s), **nessuna GPU dedicata** (solo Intel UHD integrata) → LLM su CPU
- **Dipendenze** (vedi `requirements.txt`): installate nell'ambiente virtuale **`.venv`** del progetto (PySide6 6.12, PyMuPDF 2.0, numpy 2.5).
  Il Python del Microsoft Store installa i pacchetti in un percorso troppo lungo e PySide6 fallisce ("Long Path"): usare sempre `.venv`.
  Dal 26.10.2026 `.venv` è basato sul Python 3.13 di python.org (`%LOCALAPPDATA%\Programs\Python\Python313`): quello del Microsoft Store era stato disinstallato e il vecchio `.venv` non partiva più. Se succede di nuovo: `<python> -m venv --clear .venv` e poi `pip install -r requirements.txt`.
  ```bat
  .venv\Scripts\python -m pip install -r requirements.txt
  .venv\Scripts\python -m pip install sentence-transformers   :: opzionale: allineamento semantico nel processo (altrimenti Ollama)
  ```
  ⚠️ Il pacchetto si chiama `pymupdf`. **Non** installare il pacchetto `fitz` di PyPI, che è un'altra cosa. Da PyMuPDF 2.0 `import fitz` è deprecato: il codice usa `import pymupdf as fitz` (con ripiego su `fitz` per versioni vecchie).
  ⚠️ PySide6 contiene percorsi molto lunghi: in un venv dentro cartelle profonde l'installazione fallisce senza il supporto Windows "Long Paths".
- **Passo 3**: [Ollama](https://ollama.com) installato e avviato, poi `ollama pull translategemma:4b`. Indirizzo: `http://localhost:11434` (o variabile `OLLAMA_HOST`).
- **Passo 4**: `ollama pull paraphrase-multilingual` (563 MB) per l'allineamento semantico. Dati OCR Tesseract (`ita/deu/fra/eng.traineddata`, 14–22 MB l'uno) in `~/.cache/polyglot_pdf/tessdata`: già scaricati; per altre lingue l'app chiede e li scarica da github.com/tesseract-ocr/tessdata. Tesseract **non** va installato: è dentro PyMuPDF.
- **Avvio**: `.venv\Scripts\python polyglot_pdf.py [progetto.polypdf.json | a_it.pdf b_de.pdf ...]`
- **Installer Windows** (versione da `APP_VERSION` in `polyglot_pdf.py`):
  `powershell -ExecutionPolicy Bypass -File packaging\build.ps1` → `packaging\output\PolyglotPDF-Setup-<versione>.exe` (~54 MB).
  PyInstaller (cartella `packaging\dist\PolyglotPDF`, ~220 MB, anche portabile) + Inno Setup 6 (installato con `winget install -e --id JRSoftware.InnoSetup --scope user`).
  Installazione per utente in `%LOCALAPPDATA%\Programs\PolyglotPDF`, senza diritti di amministratore; menu Start, icona desktop opzionale, disinstallazione da "App installate".
  Non include `sentence-transformers` (allineamento semantico via Ollama se c'è il modello, altrimenti euristico) né Ollama, che va installato a parte. L'OCR funziona anche nell'.exe (verificato). L'eseguibile non è firmato: al primo avvio Windows SmartScreen può chiedere conferma ("Ulteriori informazioni" → "Esegui comunque").
- **Versione portabile**: `powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -Portable` → `packaging\output\PolyglotPDF-<versione>-portable.zip` (~104 MB, niente Inno Setup). Contiene `PolyglotPDF.exe`, `_internal\`, `portable.txt` e `dati\tessdata` (ita/deu/fra/eng). Con `portable.txt` accanto all'.exe, `_data_dir()` mette cache/OCR/allineamenti in `.\dati` (verificato: 0 file nel profilo utente). Ollama resta da installare a parte.
- **Avvio da sorgente**: `avvia.bat` (usa sempre `.venv`, anche con doppio clic). Con il Python di sistema compare un messaggio che spiega come avviare.
- `pymupdf.layout` (modello ONNX che PyMuPDF 2.x carica a ogni import, ~2 s) è disattivato in testa al file: estrazione del testo verificata identica.
- **Cache**: `~/.cache/polyglot_pdf/` (embeddings `emb_<text_key>_<modello>.npy`, allineamenti `align_<tkA>_<tkB>_<metodo>_v1.json` con metodo `semantico` / `semantico-ollama` / `euristico`, traduzioni `tr_<hash>_<src>_<tgt>_<modello>_v1.json`, anteprime `anteprima_*.pdf`, OCR `ocr_<hash>_<lingue>_v1.json` (+ `.progress`), `ocr_errore.txt`, dati `tessdata/`)
- `text_key` = hash del file + `o` se il testo delle pagine scansionate viene dall'OCR: identifica la suddivisione in blocchi (cache di embedding/allineamento, ancore delle annotazioni).

## 3. Architettura (`polyglot_pdf.py`)
| Sezione | Contenuto |
|---|---|
| `Block`, `extract_blocks()` | Blocchi di testo per pagina con le parole (`get_text("words")` + `"blocks"`, stessa `textpage`, oppure i dati OCR in cache). Almeno un blocco per pagina (blocco fittizio per pagine senza testo). |
| OCR | `scanned_pages()` (pagine senza testo con un'immagine > 30 % della pagina), `load_ocr()` (usa solo cache **complete**), `ocr_main()` = **processo separato** `polyglot_pdf.py --ocr <pdf> <hash> <lingue> <pagine>` (PyMuPDF non regge più thread; da .exe: `PolyglotPDF.exe --ocr …`), `get_textpage_ocr(dpi=300, full=True)`, salvataggio parziale ogni 5 s → ripresa. Errori del processo in `ocr_errore.txt` (l'.exe non ha console). |
| `Document` | PDF + lingua + hash SHA1 + `text_key`, `ocr_pages`, `pending_ocr`. **Posizione continua** = `indice_blocco + frazione`. Indice globale delle parole `wg`. Metodi: `pos_from_point`, `point_from_pos`, `word_at`, `word_pos`, `words_in_pos_range`, `rects_for_words` (unione per riga), `text_for_words`. |
| Allineamento | `align_blocks()` → ancore `(i, j, sim)`: miglior match reciproco in una banda attorno alla diagonale, poi sottosequenza crescente più lunga (`_longest_increasing`). Similarità **semantica** (`_rows_embedding`; `get_embedding_model()`: `_STEmbedder` con `paraphrase-multilingual-MiniLM-L12-v2` se c'è sentence-transformers, altrimenti `OllamaEmbedder` con `paraphrase-multilingual` via `/api/embed`, ricontrollato a ogni allineamento; se Ollama cade a metà → euristico) o **euristica** (`_rows_heuristic`: token invarianti tra lingue — numeri, sigle, codici — pesati IDF + rapporto lunghezze). `build_mapping()` → funzione monotona a tratti `posA → posB`. |
| `block_word_matches()`, `intra_block_map()` | Allineamento **fine dentro un paragrafo**. Corrispondenze parola→parola: token invarianti, punteggiatura (`:` `;` parentesi, se in numero uguale) e **parole affini** (`_fold` senza accenti + `difflib` ≥ 0,72, stessa iniziale, migliore reciproca: Sensor/sensore, Intelligentes/intelligente). La mappa monotona usa quelle in ordine (`_longest_increasing`). `MainWindow._paired_block` / `_intra` (cache per `text_key`) / `map_pos_fine`; `_direct_words`: evidenziazioni ≤ 8 parole prese direttamente dalle corrispondenze, anche con ordine invertito («Intelligentes Design» → «design intelligente»). |
| `AlignWorker(QThread)` | Calcola gli allineamenti di tutte le coppie di lingue in background, con cache. |
| `HighlightItem`, `NoteIconItem` | Elementi grafici delle annotazioni (evidenziazione in modalità *Multiply*; tratteggio = posizione stimata). |
| `PdfView(QGraphicsView)` | Scorrimento continuo, rendering pigro delle pagine (±1 pagina, scarta oltre ±2), zoom, selezione del testo col mouse, pan con la rotella centrale. Segnali: `selectionMade`, `annotationClicked`, `annotationActivated`, `contextRequested`, `pageClicked`. |
| Ollama | `ollama_models()` (`/api/tags`), `ollama_generate()` (`/api/generate` in **streaming**, interrompibile fra un token e l'altro), `translation_prompt()` (prompt TranslateGemma + regola su numeri/codici + voci di glossario **presenti nel testo**), `ollama_translate()` (paragrafi > 1500 caratteri spezzati per frase). Solo `urllib`. |
| Unità di traduzione | `doc_units(doc)` → `(chiave, blocco, rettangolo, parole)`. Chiave `"i"` = blocco intero; `"i.k"` = cella k quando PyMuPDF ha unito più **celle di tabella** in un blocco (`block_cells`: spazi orizzontali > 2× altezza riga). `source_text()` ricongiunge le parole spezzate a fine riga (non "Ein- und"). `needs_translation()` esclude blocchi di soli numeri/sigle. |
| `TranslationCache`, `TranslateWorker(QThread)` | Cache per unità (verificata sul testo sorgente), salvata ogni 15 s e alla fine → **ripresa**. `rate()` = secondi/carattere misurati → stima del tempo. `missing_tokens()` segnala numeri/sigle persi (ignora parole maiuscole lunghe come ATTENZIONE). |
| `render_translated_pdf()` | Per ogni unità: stile prevalente (`_block_style`: dimensione, colore, grassetto/corsivo, serif, interlinea, allineamento sinistra/giustificato/centrato rispetto alla colonna), riquadro allargato verso lo spazio libero (`_free_box`: si ferma a testo, immagini, linee/bordi di `get_drawings()`), redazione delle **righe** (fill=False, immagini e grafica intatte; pagine **OCR**: riquadro coperto col colore della carta, `_background`, dimensione del carattere da `_ocr_font_size`, la scansione non è un ostacolo), `insert_htmlbox` con prima riga allineata all'originale (misura su pagina di prova). |
| `exact_anchors()` | Ancore esatte sorgente → PDF generato dai riquadri del `.polymap.json`. Inserite in `mappings` da `_add_exact_mappings()` al posto del calcolo stimato. |
| `OllamaProbe`, `SelectionTranslateWorker`, `TranslationPopup` | 0.5.0: controllo di Ollama in un thread (ogni 10 s, `MainWindow.check_ollama` → `_show_tr_status`); traduzione al volo della selezione (`ollama_translate(..., on_partial)` con `ollama_generate(on_token)` in streaming); riquadro giallo **figlio del viewport** di `PdfView`, riposizionato da `place_popup` (scorrimento, zoom, resize; sotto la selezione o sopra se manca spazio) e chiuso da `clear_selection`. Cache in memoria per sessione `(testo, src, tgt, modello)`. |
| `GenerateDialog`, `TranslationEditDialog` | Dialogo "Genera lingua" (sorgente, destinazione, modello, prova su N pagine, file, glossario per coppia, stato di Ollama e della cache); modifica di un paragrafo (originale + traduzione). |
| `MainWindow` | Progetto, barra delle lingue, cambio lingua, pannello annotazioni (dock), annulla, export, salvataggio. Passo 3: `_start_generation` → `_after_test` (stima) / `_finish_generation` → `_write_generated` (PDF + `.polymap.json` + ricarica, chiude il PDF prima di sostituirlo) → `_reanchor` (annotazioni seguono le parole dopo una rigenerazione **o un cambio di `text_key`**, anche all'apertura del progetto). Passo 4: `_start_ocr` (automatico dopo apertura/aggiunta; chiede di scaricare i dati di lingua mancanti) → `QProcess` + `_ocr_poll` (avanzamento dal file `.progress`) → `_ocr_finished` → `_reload_text` (ricarica, posizione mantenuta, riaggancio, nuovo allineamento, salvataggio). `stop_ocr`, `ocr_current`. |

### Cambio lingua
`get_position()` (punto di lettura a 1/3 dell'altezza della finestra) → `pos_from_point` → `map_pos` → `point_from_pos` → `set_position` nella vista di destinazione. Ogni lingua ha la propria `PdfView` in un `QStackedWidget`, quindi il cambio è istantaneo. Prima che l'allineamento sia pronto si usa una mappatura proporzionale.

### Annotazioni
- Ogni annotazione ha un'**ancora sorgente** esatta (`src`) nella lingua in cui è stata creata, più eventuali **override manuali** per lingua.
- Nelle altre lingue la posizione è **calcolata al volo** (`_ann_render`) tramite `map_pos_fine`. Se l'allineamento migliora, le annotazioni si aggiornano da sole.
- Ancora valida solo se `hash` corrisponde al PDF caricato. Campo `tk` (= `text_key` alla creazione; assente = hash): se diverso, la posizione viene ricalcolata dai rettangoli/punto.

## 4. Formato progetto `.polypdf.json` (version 2)
```json
{
  "app": "PolyglotPDF", "version": 2,
  "documents": [{"lang": "it", "path": "manuale_it.pdf"}, {"lang": "de", "path": "handbuch_de.pdf"}],
  "state": {"lang": "it", "page": 3, "y": 120.5, "zoom": 1.2},
  "annotations": [{
    "id": "a1b2c3d4e5f6", "kind": "highlight | note", "color": "#FFD400",
    "note": "testo", "created": "2026-10-19T16:40:00",
    "src": {"lang": "it", "hash": "…", "pos": [p0, p1],
            "rects": [[pagina, x0, y0, x1, y1]], "text": "…"},
    "overrides": {"de": {"…stessa struttura…"}}
  }]
}
```
- Percorsi relativi alla cartella del progetto.
- Note a punto: l'ancora ha `"point": [pagina, x, y]` al posto di `rects`/`text`.
- Salvataggio atomico (`.tmp` + `os.replace`). Dopo il primo salvataggio, salvataggio automatico a ogni modifica.
- Chiave opzionale `"glossaries": {"it>de": "sezionatore = Trennschalter\n…"}` (una voce per riga, anche `->`, `→` o tab; `#` = commento). Retrocompatibile: resta `version: 2`.

### File di corrispondenza `<nome>.polymap.json` (accanto al PDF generato)
```json
{
  "app": "PolyglotPDF", "kind": "generated", "version": 1,
  "source": {"lang": "it", "hash": "…", "tk": "…", "path": "C:\\…\\manuale_it.pdf"},
  "target": "de", "model": "translategemma:4b", "created": "…", "out_hash": "…",
  "entries": [{"key": "6.1", "i": 6, "page": 0, "rect": [x0, y0, x1, y1], "box": [x0, y0, x1, y1],
               "src": "Valore", "text": "Wert", "edited": false, "warn": []}]
}
```
- Una voce per unità di testo della sorgente; `text: null` = lasciato com'era (numeri di pagina, valori).
- `box` = riquadro in cui è stata scritta la traduzione → mappatura esatta e clic sul paragrafo.
- Valido solo se `out_hash` coincide con il PDF caricato (PDF modificato altrove → trattato come PDF normale).
- `edited: true` = correzione manuale: **conservata** anche rigenerando tutta la lingua.

## 5. Funzioni utente (comandi)
- **Lingue**: `Ctrl+1…9`, `Ctrl+Tab` / `Ctrl+Shift+Tab`. Il paragrafo corrispondente lampeggia (disattivabile in Vista).
- **Navigazione**: `Ctrl+rotella` zoom, `Ctrl+0` adatta larghezza, `Ctrl+G` vai a pagina, rotella centrale per trascinare.
- **Annotazioni**: trascina per selezionare → menu (5 colori, con nota, copia). `H` evidenzia, `N` evidenzia con nota, `Alt+1…5` colore, `Ctrl+Shift+H` modalità evidenziatore, `Ctrl+Shift+N` nota a punto, doppio clic modifica, tasto destro: colore / elimina / **Correggi posizione in XX** / Ripristina automatica. `Ctrl+Z` annulla (50 passi), `Esc` annulla la modalità.
- **File**: `Ctrl+L` aggiungi PDF/lingua (lingua proposta da nome file o contenuto), drag & drop, `Ctrl+E` **esporta PDF annotati** (`<nome>_annotato.pdf` con annotazioni PDF standard), Ricalcola allineamento.
- **OCR**: automatico in background all'apertura di un PDF con pagine scansionate (barra di stato: «OCR IT: pagina 3/40» + *Interrompi OCR*); al termine il testo è selezionabile e annotabile. Menu *Allineamento → Riconosci testo nelle pagine scansionate (OCR)* per riprendere dopo un'interruzione o un rifiuto.
- **Versione**: nel titolo della finestra, in basso a destra nella barra di stato (`v0.5.0`) e in *? → Informazioni su PolyglotPDF…* (anche stato di Ollama, cartella dati, versioni di Python/PyMuPDF/Qt).
- **Stato della traduzione** (barra di stato): **● Traduzione disponibile** in verde (Ollama attivo con un modello `translategemma`), **● Traduzione non disponibile** in rosso (Ollama non rilevato, oppure attivo senza modello: il tooltip dice cosa fare). Ricontrollo automatico ogni 10 s, clic per ricontrollare subito.
- **Traduci selezione**: seleziona il testo → menu (anche tasto destro) **Traduci in italiano (IT)** o **Traduci in ▸** altra lingua, oppure tasto `T`. Riquadro stile nota ancorato alla selezione: la traduzione compare mentre arriva, lingua cambiabile dal riquadro (ricordata per la sessione), *Copia*, *Salva come nota* (evidenziazione con nota «[IT] …»). Si chiude con ✕, `Esc`, una nuova selezione o un clic altrove; chiudendo a metà la traduzione si interrompe. Voci disattivate se la traduzione non è disponibile. Usa il glossario della coppia di lingue.
- **Traduzione**: `Ctrl+Shift+G` **Genera lingua mancante…** → (consigliata) prova su 2–3 pagine → messaggio con tempo stimato: *Traduci tutto* / *Apri anteprima* / *Chiudi*. Avanzamento e tempo rimanente nella barra di stato, pulsante *Interrompi* (si riprende rilanciando). Alla fine il PDF `manuale_de.pdf` viene aggiunto al progetto come nuova lingua. *Paragrafi da rivedere…* (numeri/sigle persi), *Rigenera PDF della lingua corrente*. Tasto destro su un paragrafo della lingua generata: **Modifica traduzione del paragrafo…** / **Ritraduci paragrafo** → PDF rigenerato, posizione di lettura mantenuta.

## 6. Test eseguiti (PDF sintetici IT 10 pag. / DE 12 pag.)
- Allineamento euristico: 120/120 paragrafi corretti. Senza numerazione e con 5 paragrafi mancanti: 115/115.
- Cambio IT→DE→IT: posizione mantenuta (es. IT pag. 7 ↔ DE pag. 8, stesso paragrafo).
- Evidenziazione IT «centrale 40 con protocollo IEC 61850.» → DE stimata «40 mit dem Protokoll IEC 61850.» (la correzione manuale funziona).
- Selezione col mouse (QTest), annulla, export PDF (Highlight + Text annot), salva e riapri: OK.
- ⚠️ **Non ancora testato su manuali reali** (tabelle, due colonne, figure).

**Passo 3** — test automatici (offscreen) con **finto server Ollama** (dizionario IT→DE, streaming, testo più lungo, un codice "perso" apposta), PDF sintetico di 4 pagine con titolo centrato, paragrafi giustificati, parola spezzata, riquadro colorato, immagine, tabella con linee: **98/98 controlli OK**.
- Prompt nel formato TranslateGemma; glossario inserito solo se il termine compare.
- Prova su 2 pagine + stima; interruzione a metà e ripresa: ritradotte **solo** le unità mancanti.
- PDF generato: testo originale rimosso, immagine e grafica conservate, numero di pagina invariato, nessuna sovrapposizione, titolo centrato/grassetto/stessa dimensione, **prima riga alla stessa altezza** (69,5 ↔ 69,5 pt), corpo non rimpicciolito, colore e confini del riquadro rispettati, **ogni cella della tabella nella sua colonna**.
- Allineamento esatto 40/40 ancore; evidenziazione IT ritrovata sul testo DE; cambio lingua sulla pagina giusta.
- Modifica e ritraduzione di un paragrafo, annotazione fatta in DE riagganciata dopo la rigenerazione, correzioni manuali conservate, salva/riapri, messaggio chiaro con Ollama spento o modello mancante.
- I test stanno nella scratchpad della sessione (non nel progetto). Se servono in futuro: chiedere di ricrearli in `tests/`.

**Passo 4** (22.10.2026) — test offscreen con Ollama e OCR reali, PDF IT di 6 pagine (pag. 2–3 scansionate) + DE nativo impaginato diversamente, con un paragrafo mancante e **senza numeri/sigle**: **24/24**; interruzione/ripresa/errore OCR: **10/10**; test del passo 3 e dei dialoghi: tutto OK.
- Euristico: 0 ancore su questi testi; **semantico via Ollama: 23/23 paragrafi corretti** (similarità traduzioni 0,88–0,96, non correlati ≤ 0,46; ~11 paragrafi/s su CPU, primo caricamento del modello ~15 s).
- OCR: ~1,8 s/pagina (300 dpi), testo esatto; evidenziazione su una pagina successiva riagganciata sulle stesse parole, nota sulla pagina scansionata, salva/riapri senza ripetere l'OCR.
- Generazione da sorgente scansionata: testo originale coperto (l'OCR della pagina generata non lo trova più), immagine conservata, carattere 12–13 pt per un originale da 12.
- `.exe` impacchettato: `--ocr` funziona senza console; aprendo un progetto l'OCR parte da solo e le annotazioni vengono salvate con il nuovo `tk`.

**0.4.1** (22.10.2026) — segnalazione: «l'allineamento sembra peggiorato» (scheda Turbimax CUS52D DE/IT in `test1\`).
- Causa: il paragrafo era giusto, la **parola** no (stima proporzionale) + bug in `Document._pos_to_word` (arrotondamento: 7 + 3/18 → una parola in più ai lati di ogni evidenziazione stimata). Corretto con tolleranza 1e-6: andata/ritorno parola→posizione→parola esatto 21 588/21 588.
- Evidenziazioni DE→IT sulla scheda reale: 10/10 sulle parole giuste (prima 0/2 per le due del progetto: «Handling:» → «un sensore», ora «d'uso:»; «Intelligentes» → «il design», ora «intelligente»).
- Paragrafi, confronto sul contenuto (traduzione inequivocabile, ±1 pagina): TI01136C 95,0 % semantico vs 92,3 % euristico; CUS52D 82,1 % vs 78,6 %. Attenzione: il criterio «stessa pagina» penalizza a torto il semantico quando il testo passa alla pagina dopo in una lingua.
- Regressione: passo 3, dialoghi, passo 4 (24/24), OCR ripresa (10/10): tutto OK.

**0.5.1** (26.10.2026) — velocità della traduzione:
- `ollama_base()` usa **127.0.0.1** (anche al posto di `localhost`/`0.0.0.0` in `OLLAMA_HOST`): con «localhost» Windows prova prima IPv6 e ogni richiesta perdeva ~2 s (misurato 2–3 s → 0,03 s). Vale per tutto: traduzione della selezione, generazione lingua, embedding, controllo di stato.
- **Precaricamento** del modello quando compare il menu della selezione (`preload_model` → `ModelPreloader`: `/api/ps`, poi `/api/generate` senza prompt con le **stesse** `OLLAMA_OPTIONS`; con `num_ctx` diverso Ollama ricaricherebbe il modello, ~30 s). Il riquadro scrive «Caricamento del modello in memoria…» se il modello non è pronto (`ollama_loaded`).
- Misure (parola singola, DE→IT): modello in memoria 6,9 s → 5,3–5,5 s (Ollama diretto); ~4,7 s sono la lettura delle ~100 istruzioni del prompt. Modello da caricare: 50–80 s con poca RAM libera; il precaricamento ne nasconde la parte trascorsa mentre si sceglie dal menu (81 s → 44 s dal clic, dopo 8 s di menu).
- Non applicata (scelta dell'utente): istruzioni più corte per selezioni senza numeri/codici (51 token invece di 99, ~1–2 s in meno).

**0.5.0** (22.10.2026) — test offscreen con Ollama reale sulla scheda CUS52D DE: versione, indicatore verde/rosso (Ollama attivo / spento / senza modello, ritorno automatico al verde), menu, traduzione della selezione (prima volta ~30 s con caricamento del modello, poi pochi secondi; mostrata in streaming), riquadro attaccato alla selezione anche scorrendo e con lo zoom, cache, cambio lingua, salva come nota, chiusure, interruzione.
- Nota per i test offscreen: chiudere la finestra con un progetto non salvato apre «Salvare il progetto?» e il test resta fermo → sostituire `QMessageBox.question`.

## 7. Limiti noti
- `get_text("blocks", sort=True)`: nelle pagine a più colonne l'ordine di lettura può mescolare le colonne.
- La stima fine dentro il paragrafo usa numeri/sigle, punteggiatura e parole affini: le parole senza corrispondenza (es. Kalibrierung/taratura) restano proporzionali, e un'evidenziazione può coprire qualche parola in meno della frase tradotta (stesso numero di parole dell'originale).
- Rendering pagine sul thread principale (possibile lentezza su PDF pesanti a zoom alto).
- Pagine ruotate non verificate.
- Le annotazioni già presenti nei PDF originali vengono mostrate (renderizzate) ma non importate nel progetto.
- **Passo 3**:
  - Stile per paragrafo: la formattazione **interna** (una parola in grassetto, es. «**ATTENZIONE:** …») si perde, vale lo stile prevalente.
  - Font: famiglie generiche `sans-serif`/`serif` di MuPDF, non il font originale. Caratteri non latini (cinese, giapponese…) non verificati.
  - Per sostituire il PDF generato il file non deve essere aperto in un altro programma (Acrobat lo blocca su Windows): in quel caso compare un avviso e resta la versione precedente.
  - Testo che non sta nemmeno nello spazio libero → il carattere viene ridotto (nessun limite minimo).
  - Dopo una rigenerazione, *Annulla* (Ctrl+Z) può riportare annotazioni con il vecchio hash → in quella lingua tornano "stimate".
  - Pagine scansionate: tradotte dopo l'OCR, il testo nuovo copre l'originale con un riquadro del colore della carta (su sfondi a motivi o foto il riquadro si vede). Due colonne: ordine dei blocchi come al passo 1, ma ogni blocco resta nel suo riquadro.

- **Passo 4**:
  - OCR solo su pagine **interamente** prive di testo: le pagine miste (testo digitale + parti scansionate) non vengono riconosciute.
  - Romancio: nessun modello Tesseract, si usa `ita+deu`. Lingue non in `OCR_LANGS` → `eng`.
  - Le annotazioni fatte su una pagina scansionata **prima** dell'OCR vengono riagganciate alla parola più vicina (posizione approssimata).
  - Un PDF generato da una sorgente il cui OCR è cambiato dopo la generazione perde l'allineamento esatto (torna stimato).

## 8. Passo 3: generazione lingua mancante — fatto, da verificare con il modello reale
**Prossimi passi**
1. Installare dipendenze e Ollama, `ollama pull translategemma:4b`, misurare la velocità reale (prova su 2–3 pagine nel dialogo, oppure `ollama run translategemma:4b --verbose "<prompt>"` → **eval rate**).
2. Provare su un **manuale reale** (tabelle complesse, due colonne, note a piè di pagina) e valutare la qualità della traduzione e del layout.
3. Se serve: formattazione interna (span grassetto/corsivo → HTML), font originali o Noto incorporati per lingue non latine, BabelDOC come motore alternativo, Argos Translate come motore "rapido".

**Decisioni prese (implementate)**
- LLM: **Ollama + `translategemma:4b`** (3.3 GB, default; `translategemma:12b` 8.1 GB come opzione). Su CPU: 100 pagine ≈ qualche ora (stima da verificare) → traduzione in background con cache e ripresa.
- Prompt raccomandato da Ollama per TranslateGemma: *"You are a professional {SRC} ({src}) to {TGT} ({tgt}) translator … Produce only the {TGT} translation, without any additional explanations or commentary."* + **due righe vuote** + testo.
- API Ollama: `POST http://localhost:11434/api/generate` (o `/api/chat`), solo `urllib`/`requests`, nessuna libreria in più.
- Motore PDF **integrato con PyMuPDF**: per ogni blocco → `add_redact_annot` + `apply_redactions(images=PDF_REDACT_IMAGE_NONE)` per togliere il testo senza toccare immagini e grafica → `insert_htmlbox` / `insert_textbox` nello stesso riquadro con riduzione automatica del carattere. Font di riserva (es. Noto Sans) per i caratteri mancanti.
- **Mappatura esatta**: il PDF generato salva accanto un file di corrispondenza blocco→blocco, così l'allineamento non va stimato.
- Opzionale in futuro: BabelDOC (AGPL-3.0, endpoint compatibile OpenAI → Ollama) come motore alternativo per layout complessi, chiamato come processo esterno. Argos Translate come motore "rapido".

**Implementato (19.10.2026)**
1. ✅ Dialogo "Genera lingua…" (sorgente, destinazione, modello, prova su N pagine con stima del tempo, file, glossario).
2. ✅ Regole nel prompt: non tradurre codici/sigle/numeri/unità + **glossario utente** (nel progetto, per coppia di lingue) + controllo dei numeri/sigle persi.
3. ✅ Worker in background con avanzamento, cache per unità (`hash_doc + chiave unità + lingue + modello`), ripresa.
4. ✅ Ricostruzione PDF + inserimento nel progetto come nuova lingua, con allineamento esatto.
5. ✅ Revisione: tasto destro su un paragrafo tradotto → modifica o ritraduci → il PDF si rigenera.
   *Nota*: si rigenera l'intero PDF (non la sola pagina): più semplice e deterministico, pochi secondi anche per 100 pagine.

**Misure reali (19.10.2026, Ollama 0.40.6, translategemma:4b, i5-8265U solo CPU)**
- Caricamento del modello in memoria: ~50 s (solo la prima volta; poi `keep_alive` 30 min).
- Generazione ~7–8 token/s. **Elaborazione del prompt ~4 s a ogni chiamata** (~100 token di istruzioni, ~25 token/s): Ollama non riutilizza il prefisso comune con questo modello, nemmeno con prompt identico.
  → ogni cella o titolo breve costa ~6 s; un paragrafo di 270 caratteri ~20 s.
- Pagina di prova (1 100 caratteri, 12 unità): 166 s, ~7 caratteri/s → **manuale di 100 pagine ≈ 4–8 h**.
- Qualità IT→DE/FR buona: codici e unità intatti, nessun commento aggiunto. **Glossario rispettato solo in parte** (es. «quadro elettrico» → «elektrisches Gehäuse» invece di «Schaltschrank» nel 2° paragrafo; «sezionatore» → «Schalter» in una frase breve).
- Idee: raggruppare più unità brevi (celle, titoli) in una sola richiesta, una per riga, per ammortizzare i 4 s; segnalare tra i "da rivedere" i paragrafi in cui un termine del glossario non è stato rispettato.

## 9. Preferenze di sviluppo
- Sviluppo **incrementale**, codice completo e funzionante a ogni passo, testato prima della consegna.
- Risposte concise e strutturate, in italiano.
- Interfaccia del tool in italiano.
