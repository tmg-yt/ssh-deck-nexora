@echo off
rem Oeffnet PowerShell, wechselt in den Ordner dieser Datei, installiert fehlende Pakete
rem (nur beim ersten Mal) und startet SSH Deck. Muss neben ssh_deck.py liegen.
start "SSH Deck" powershell -NoExit -Command "Set-Location -LiteralPath '%~dp0'; py -c 'import keyring, paramiko, PySide6' 2>$null; if ($LASTEXITCODE -ne 0) { Write-Host 'Installiere fehlende Pakete ...'; py -m pip install -r requirements.txt }; python ssh_deck.py"
