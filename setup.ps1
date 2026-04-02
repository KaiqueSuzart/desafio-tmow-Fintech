# setup.ps1 — Instala dependências do projecto Extrator de Tabelas HTML
$ErrorActionPreference = "Stop"

# Actualizar PATH caso Python tenha sido instalado recentemente
$env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
            [System.Environment]::GetEnvironmentVariable("Path", "User")

$python = $null
foreach ($candidate in @("py", "python3", "python")) {
    try {
        $ver = & $candidate --version 2>&1
        if ($LASTEXITCODE -eq 0) { $python = $candidate; break }
    } catch {}
}

if (-not $python) {
    Write-Host "`n[ERRO] Python nao encontrado." -ForegroundColor Red
    Write-Host "Instale com:  winget install Python.Python.3.12"
    Write-Host "Ou baixe de:  https://www.python.org/downloads/"
    exit 1
}

Write-Host "`nUsando: $python" -ForegroundColor Cyan

Write-Host "`n--- pip install -r requirements.txt ---" -ForegroundColor Yellow
& $python -m pip install --upgrade pip
& $python -m pip install -r requirements.txt

Write-Host "`n--- playwright install chromium ---" -ForegroundColor Yellow
& $python -m playwright install chromium

Write-Host "`n[OK] Setup concluido. Copie .env.example para .env e preencha OPENAI_API_KEY." -ForegroundColor Green
Write-Host "Para iniciar:  $python -m uvicorn app:app --host 127.0.0.1 --port 8765"
