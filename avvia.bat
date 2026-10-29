@echo off
rem Avvia PolyglotPDF con l'ambiente del progetto (.venv), passando gli argomenti
rem (progetto .polypdf.json o PDF). Uso:  avvia   oppure   avvia test1\test2.polypdf.json
"%~dp0.venv\Scripts\python.exe" "%~dp0polyglot_pdf.py" %*
