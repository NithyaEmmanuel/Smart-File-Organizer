# Builds dist\SmartFileOrganizer.exe  (run from the project folder, venv active)
pyinstaller --noconfirm --clean --onefile --windowed --name SmartFileOrganizer main.py
Write-Host "Built: dist\SmartFileOrganizer.exe"
Get-FileHash dist\SmartFileOrganizer.exe -Algorithm SHA256
