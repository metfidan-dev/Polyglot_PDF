# Crea PolyglotPDF.exe (PyInstaller) e l'installer PolyglotPDF-Setup-<versione>.exe (Inno Setup 6).
# Uso, dalla cartella del progetto:   powershell -ExecutionPolicy Bypass -File packaging\build.ps1
# Versione portabile (ZIP, niente installer):  ... -File packaging\build.ps1 -Portable
param([switch]$Portable)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$pkg = $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Ambiente .venv non trovato: crealo con  python -m venv .venv  e installa requirements.txt" }

$version = (Select-String -Path (Join-Path $root "polyglot_pdf.py") -Pattern '^APP_VERSION = "([^"]+)"').Matches[0].Groups[1].Value
Write-Host "== PolyglotPDF $version =="

& $py -m pip install -q --disable-pip-version-check pyinstaller
if ($LASTEXITCODE) { throw "installazione di PyInstaller non riuscita" }

Write-Host "-- icona"
& $py (Join-Path $pkg "make_icon.py")
if ($LASTEXITCODE) { throw "creazione icona non riuscita" }

Write-Host "-- eseguibile (PyInstaller)"
$ico = Join-Path $pkg "polyglot_pdf.ico"
& $py -m PyInstaller --noconfirm --clean --windowed --name PolyglotPDF `
    --icon $ico --add-data "$ico;." `
    --distpath (Join-Path $pkg "dist") --workpath (Join-Path $pkg "build") --specpath $pkg `
    --exclude-module tkinter --exclude-module sentence_transformers --exclude-module torch `
    --exclude-module matplotlib --exclude-module PIL `
    --exclude-module pymupdf.layout --exclude-module onnxruntime --exclude-module fitz `
    (Join-Path $root "polyglot_pdf.py")
if ($LASTEXITCODE) { throw "PyInstaller non riuscito" }

if ($Portable) {
    Write-Host "-- versione portabile (ZIP)"
    $dist = Join-Path $pkg "dist\PolyglotPDF"
    # portable.txt accanto all'.exe: cache e dati in .\dati invece che nel profilo utente
    Set-Content -Path (Join-Path $dist "portable.txt") -Encoding UTF8 -Value @"
PolyglotPDF $version - versione portabile

Avvio: doppio clic su PolyglotPDF.exe (nessuna installazione, nessun diritto di amministratore).
Cache, allineamenti e risultati dell'OCR vengono salvati nella cartella "dati" qui accanto:
la cartella si può copiare su una chiavetta o su un altro PC con tutto il lavoro gia' fatto.
Se si cancella questo file, i dati vanno invece nel profilo utente (%USERPROFILE%\.cache\polyglot_pdf).

Incluso: dati OCR per italiano, tedesco, francese e inglese (dati\tessdata).
Da installare a parte sul PC (facoltativo): Ollama (https://ollama.com) per la traduzione
(ollama pull translategemma:4b) e l'allineamento semantico (ollama pull paraphrase-multilingual).
Al primo avvio Windows SmartScreen puo' chiedere conferma: "Ulteriori informazioni" -> "Esegui comunque".
"@
    $td = Join-Path $dist "dati\tessdata"
    New-Item -ItemType Directory -Force $td | Out-Null
    $src = Join-Path $env:USERPROFILE ".cache\polyglot_pdf\tessdata"
    foreach ($l in "ita", "deu", "fra", "eng") {
        $f = Join-Path $src "$l.traineddata"
        if (Test-Path $f) { Copy-Item $f $td } else { Write-Warning "$l.traineddata non trovato: verra' scaricato dall'app" }
    }
    New-Item -ItemType Directory -Force (Join-Path $pkg "output") | Out-Null
    $zip = Join-Path $pkg "output\PolyglotPDF-$version-portable.zip"
    if (Test-Path $zip) { Remove-Item $zip }
    & tar.exe -a -c -f $zip -C (Join-Path $pkg "dist") PolyglotPDF
    if ($LASTEXITCODE) { throw "creazione dello ZIP non riuscita" }
    Write-Host ("Portabile: {0}  ({1:N0} MB)" -f $zip, ((Get-Item $zip).Length / 1MB))
    exit 0
}

Write-Host "-- installer (Inno Setup)"
$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
    Write-Warning "Inno Setup 6 non trovato: c'è solo la cartella packaging\dist\PolyglotPDF (portabile)."
    Write-Warning "Per l'installer:  winget install -e --id JRSoftware.InnoSetup"
    exit 0
}
& $iscc "/DAppVersion=$version" (Join-Path $pkg "installer.iss")
if ($LASTEXITCODE) { throw "Inno Setup non riuscito" }
Get-ChildItem (Join-Path $pkg "output") -Filter "PolyglotPDF-Setup-$version.exe" | ForEach-Object {
    Write-Host ("Installer: {0}  ({1:N0} MB)" -f $_.FullName, ($_.Length / 1MB))
}
