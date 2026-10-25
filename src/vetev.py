"""Načtení a uložení dat ve větvi repozitáře (trvalé úložiště mezi běhy).

  python -m src.vetev nacti            načte větev "data" do složky ./data
  python -m src.vetev uloz data        uloží ./data do větve "data"
  python -m src.vetev uloz data-zaloha uloží ./data do větve "data-zaloha" (denní záloha)

Větev obsahuje vždy jen aktuální stav (jeden commit), takže repozitář s časem nebobtná.
Pokud se data nepodaří načíst kvůli chybě, běh skončí chybou a nic nepřepíše.
"""
import datetime as dt
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .common import DATA

BOT = ["-c", "user.name=github-actions[bot]",
       "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com"]


def _url_cteni() -> str:
    return os.environ.get("REPO_URL") or f"https://github.com/{os.environ['GITHUB_REPOSITORY']}.git"


def _url_zapis() -> str:
    return (os.environ.get("REPO_PUSH_URL")
            or f"https://x-access-token:{os.environ['GH_TOKEN']}@github.com/{os.environ['GITHUB_REPOSITORY']}.git")


def nacti():
    if DATA.exists():
        shutil.rmtree(DATA)
    rc = subprocess.run(["git", "ls-remote", "--exit-code", "--heads", _url_cteni(), "data"],
                        stdout=subprocess.DEVNULL).returncode
    if rc == 0:
        subprocess.run(["git", "clone", "--quiet", "--depth", "1", "--branch", "data", _url_cteni(), str(DATA)],
                       check=True)
        shutil.rmtree(DATA / ".git", ignore_errors=True)
        print(f"Načtena uložená data: {len(list(DATA.rglob('*.json')))} souborů.")
    elif rc == 2:
        DATA.mkdir(parents=True)
        print("Větev data zatím neexistuje, první běh začíná s prázdnými daty.")
    else:
        sys.exit(f"Nepodařilo se ověřit větev data (chyba {rc}). Běh ukončuji, aby nedošlo ke ztrátě dat.")


def uloz(vetev: str):
    if not DATA.exists() or not any(DATA.iterdir()):
        print("Složka data je prázdná, není co ukládat.")
        return
    cas = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
    with tempfile.TemporaryDirectory() as tmp:
        cil = Path(tmp) / "repo"
        shutil.copytree(DATA, cil)
        (cil / "README.md").write_text(
            "# Uložená data programu\n\nTuto větev spravuje program automaticky (GitHub Actions). "
            "Ručně ji neupravuj.\nKontrakty jsou po měsících ve složce kontrakty/, ceny akcií v ceny.json, "
            f"seznam tickerů v tickery.json.\n\nPoslední uložení: {cas} UTC\n", encoding="utf-8")
        git = ["git", "-C", str(cil)]
        subprocess.run(git + ["init", "--quiet", "-b", vetev], check=True)
        subprocess.run(git + ["add", "-A"], check=True)
        subprocess.run(git + BOT + ["commit", "--quiet", "-m", f"Data {cas} UTC"], check=True)
        subprocess.run(git + ["push", "--quiet", "--force", _url_zapis(), f"HEAD:refs/heads/{vetev}"], check=True)
    print(f"Data uložena do větve {vetev}.")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "nacti":
        nacti()
    elif len(sys.argv) > 1 and sys.argv[1] == "uloz":
        uloz(sys.argv[2] if len(sys.argv) > 2 else "data")
    else:
        sys.exit("Použití: python -m src.vetev nacti | uloz [vetev]")
