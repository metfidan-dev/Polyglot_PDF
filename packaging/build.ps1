# Builds PolyglotPDF.exe (PyInstaller) and the installer PolyglotPDF-Setup-<version>.exe (Inno Setup 6).
# Usage, from the project folder:   powershell -ExecutionPolicy Bypass -File packaging\build.ps1
# Portable version (ZIP, no installer):  ... -File packaging\build.ps1 -Portable
param([switch]$Portable)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$pkg = $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw ".venv environment not found: create it with  python -m venv .venv  and install requirements.txt" }

$version = (Select-String -Path (Join-Path $root "polyglot_pdf.py") -Pattern '^APP_VERSION = "([^"]+)"').Matches[0].Groups[1].Value
Write-Host "== PolyglotPDF $version =="

& $py -m pip install -q --disable-pip-version-check pyinstaller
if ($LASTEXITCODE) { throw "installation of PyInstaller failed" }

Write-Host "-- icon"
& $py (Join-Path $pkg "make_icon.py")
if ($LASTEXITCODE) { throw "icon creation failed" }

Write-Host "-- executable (PyInstaller)"
$ico = Join-Path $pkg "polyglot_pdf.ico"
& $py -m PyInstaller --noconfirm --clean --windowed --name PolyglotPDF `
    --icon $ico --add-data "$ico;." `
    --distpath (Join-Path $pkg "dist") --workpath (Join-Path $pkg "build") --specpath $pkg `
    --exclude-module tkinter --exclude-module sentence_transformers --exclude-module torch `
    --exclude-module matplotlib --exclude-module PIL `
    --exclude-module pymupdf.layout --exclude-module onnxruntime --exclude-module fitz `
    (Join-Path $root "polyglot_pdf.py")
if ($LASTEXITCODE) { throw "PyInstaller failed" }

if ($Portable) {
    Write-Host "-- portable version (ZIP)"
    $dist = Join-Path $pkg "dist\PolyglotPDF"
    # portable.txt next to the .exe: cache and data in .\dati instead of the user profile
    Set-Content -Path (Join-Path $dist "portable.txt") -Encoding UTF8 -Value @"
PolyglotPDF $version - portable version

Start: double-click PolyglotPDF.exe (no installation, no administrator rights).
Cache, alignments, OCR results and settings are saved in the "dati" folder next to it:
the folder can be copied to a USB stick or to another PC together with the work already done.
If this file is deleted, the data goes to the user profile instead (%USERPROFILE%\.cache\polyglot_pdf).

Included: OCR data for Italian, German, French and English (dati\tessdata).
To be installed separately on the PC (optional): Ollama (https://ollama.com) for translation
(ollama pull translategemma:4b) and semantic alignment (ollama pull paraphrase-multilingual).
At the first start Windows SmartScreen may ask for confirmation: "More info" -> "Run anyway".

----------------------------------------------------------------------------------------------
PolyglotPDF $version - versione portabile

Avvio: doppio clic su PolyglotPDF.exe (nessuna installazione, nessun diritto di amministratore).
Cache, allineamenti, risultati dell'OCR e impostazioni vengono salvati nella cartella "dati" qui accanto:
la cartella si puo' copiare su una chiavetta o su un altro PC con tutto il lavoro gia' fatto.
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
        if (Test-Path $f) { Copy-Item $f $td } else { Write-Warning "$l.traineddata not found: the app will download it" }
    }
    New-Item -ItemType Directory -Force (Join-Path $pkg "output") | Out-Null
    $zip = Join-Path $pkg "output\PolyglotPDF-$version-portable.zip"
    if (Test-Path $zip) { Remove-Item $zip }
    & tar.exe -a -c -f $zip -C (Join-Path $pkg "dist") PolyglotPDF
    if ($LASTEXITCODE) { throw "creation of the ZIP failed" }
    Write-Host ("Portable: {0}  ({1:N0} MB)" -f $zip, ((Get-Item $zip).Length / 1MB))
    exit 0
}

Write-Host "-- installer (Inno Setup)"
$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
    Write-Warning "Inno Setup 6 not found: there is only the folder packaging\dist\PolyglotPDF (portable)."
    Write-Warning "For the installer:  winget install -e --id JRSoftware.InnoSetup"
    exit 0
}
& $iscc "/DAppVersion=$version" (Join-Path $pkg "installer.iss")
if ($LASTEXITCODE) { throw "Inno Setup failed" }
Get-ChildItem (Join-Path $pkg "output") -Filter "PolyglotPDF-Setup-$version.exe" | ForEach-Object {
    Write-Host ("Installer: {0}  ({1:N0} MB)" -f $_.FullName, ($_.Length / 1MB))
}
