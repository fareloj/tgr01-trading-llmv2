import subprocess
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[2]
LOG_DIR = PROJECT_DIR / "backend" / "logs"


def start_workers() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    script = r"""
$logDir = Join-Path (Get-Location) 'backend\logs'
$existingPrice = Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'python' -and $_.CommandLine -match 'price_worker\.py' }
if ($existingPrice) {
  Write-Host '[OK] price_worker ja esta rodando.'
} else {
  Start-Process -FilePath 'python' -ArgumentList @('-u', '.\backend\data\price_worker.py') -WorkingDirectory (Get-Location) -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir 'price_worker.out.log') -RedirectStandardError (Join-Path $logDir 'price_worker.err.log')
  Write-Host '[OK] price_worker iniciado.'
}
$existingNews = Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'python' -and $_.CommandLine -match 'news_worker\.py' }
if ($existingNews) {
  Write-Host '[OK] news_worker ja esta rodando.'
} else {
  Start-Process -FilePath 'python' -ArgumentList @('-u', '.\backend\data\news_worker.py', '--mode', 'real', '--interval', '900') -WorkingDirectory (Get-Location) -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir 'news_worker.out.log') -RedirectStandardError (Join-Path $logDir 'news_worker.err.log')
  Write-Host '[OK] news_worker iniciado.'
}
"""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        cwd=PROJECT_DIR,
        check=False,
    )
    print("Aguarde 30-60s e rode o preflight estrito.")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(start_workers())
