@echo off
rem Starts PolyglotPDF with the project environment (.venv), passing the arguments
rem (.polypdf.json project or PDFs). Usage:  avvia   or   avvia my_project.polypdf.json
"%~dp0.venv\Scripts\python.exe" "%~dp0polyglot_pdf.py" %*
