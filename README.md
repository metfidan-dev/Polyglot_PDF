# PolyglotPDF

Lettore desktop per lo **stesso documento PDF in più lingue** (ad esempio un manuale tecnico in italiano, tedesco, francese e inglese).
Funziona offline, con modelli locali e gratuiti. L'interfaccia è in italiano.

## Funzioni

- **Cambio lingua istantaneo mantenendo il punto di lettura** (`Ctrl+1…9`, `Ctrl+Tab`): i paragrafi delle varie lingue vengono allineati automaticamente.
- **Evidenziazioni e note sincronizzate**: ciò che evidenzi in una lingua compare sul testo corrispondente nelle altre, con correzione manuale se serve. Esportazione in PDF annotati standard.
- **Traduzione della selezione**: selezioni del testo → *Traduci* → la traduzione compare in un riquadro ancorato alla selezione (copia, salva come nota).
- **Generazione di una lingua mancante**: traduce l'intero PDF con un LLM locale mantenendo impaginazione, immagini e grafica; traduzione in background con ripresa, glossario per coppia di lingue, revisione dei singoli paragrafi.
- **OCR delle pagine scansionate** (Tesseract integrato in PyMuPDF): il testo diventa selezionabile, annotabile e traducibile.
- **Allineamento semantico** fra le lingue tramite embedding multilingue; senza modello, ripiego euristico su numeri, sigle e codici.

## Requisiti

- Windows (sviluppato e provato su Windows 11; il codice è Python + Qt, ma su altri sistemi non è verificato)
- Python 3.10 o successivo
- Facoltativo, per traduzione e allineamento semantico: [Ollama](https://ollama.com) con i modelli

  ```bat
  ollama pull translategemma:4b
  ollama pull paraphrase-multilingual
  ```

  In basso nella finestra l'indicatore **● Traduzione disponibile / non disponibile** dice se Ollama è stato rilevato.

Su un portatile senza scheda grafica dedicata la traduzione gira sulla CPU: alcuni secondi per una frase breve, ore per un manuale intero. Con 8 GB di RAM il primo caricamento del modello può richiedere circa un minuto.

## Installazione da sorgente

```bat
git clone https://github.com/metfidan-dev/Polyglot_PDF.git
cd Polyglot_PDF
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

Avvio: doppio clic su `avvia.bat`, oppure

```bat
.venv\Scripts\python polyglot_pdf.py [progetto.polypdf.json | file1.pdf file2.pdf ...]
```

Il pacchetto si chiama `pymupdf`: non installare il pacchetto `fitz` di PyPI, che è un'altra cosa.
I dati OCR per lingue diverse da italiano, tedesco, francese e inglese vengono scaricati dall'app alla prima necessità (da [tesseract-ocr/tessdata](https://github.com/tesseract-ocr/tessdata)).

## Uso in breve

1. **File → Aggiungi PDF/lingua…** (`Ctrl+L`) o trascina i PDF nella finestra; la lingua viene proposta dal nome del file o dal contenuto.
2. Cambia lingua con la barra in alto o `Ctrl+1…9`.
3. Seleziona del testo con il mouse: menu con evidenziazione (5 colori), nota, copia e traduzione.
4. **File → Salva progetto** (`Ctrl+S`): un file `.polypdf.json` con i percorsi dei PDF, le annotazioni e il glossario.
5. **Traduzione → Genera lingua mancante…** (`Ctrl+Shift+G`) per creare una nuova lingua; conviene prima la prova su 2–3 pagine, che stima il tempo totale.

## Eseguibile per Windows

```bat
powershell -ExecutionPolicy Bypass -File packaging\build.ps1            :: installer (richiede Inno Setup 6)
powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -Portable  :: versione portabile (ZIP)
```

## Struttura

| File | Contenuto |
|---|---|
| `polyglot_pdf.py` | l'applicazione (un solo file) |
| `requirements.txt` | dipendenze Python |
| `avvia.bat` | avvio con l'ambiente `.venv` |
| `packaging/` | script per installer e versione portabile |
| `HANDOFF.md` | note tecniche di sviluppo: architettura, formati dei file, test eseguiti, limiti noti |

## Licenza

PolyglotPDF è distribuito con licenza [GNU AGPL-3.0](LICENSE).

Componenti di terze parti: [PyMuPDF](https://github.com/pymupdf/PyMuPDF) (AGPL-3.0), [PySide6 / Qt](https://www.qt.io/qt-for-python) (LGPL-3.0), [NumPy](https://numpy.org) (BSD), dati [Tesseract](https://github.com/tesseract-ocr/tessdata) (Apache-2.0). I modelli usati tramite Ollama hanno licenze proprie (TranslateGemma: Gemma Terms of Use).
