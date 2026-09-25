#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════╗
║  BVMAC BOC Downloader  ─  v1.0                                        ║
║  Téléchargement automatique des Bulletins Officiels de la Cote        ║
║  depuis https://www.bvm-ac.org  (2019 → aujourd'hui)                  ║
║                                                                        ║
║  ⚠ NOMENCLATURE NON NORMALISÉE — 4 conventions successives :          ║
║    • 2019–2021 : BOC-DD-MM-YY.pdf                                     ║
║    •     (var) : BOC-BVMAC-DD-MM-YY.pdf / BOC-BVMAC-DD-MM-YYYY.pdf   ║
║    • 2023 (tr) : BOC-automatise-YYYYMMDD.pdf                          ║
║    • 2023–...  : BOC-YYYYMMDD.pdf                                     ║
║                                                                        ║
║  ⚠ Le dossier /YYYY/MM/ = date d'UPLOAD, pas de séance !              ║
║    (un BOC de fin décembre peut être dans le dossier de janvier,      ║
║     un BOC de janvier 2023 est dans 2023/04, etc.)                    ║
║                                                                        ║
║  Stratégie en 2 niveaux :                                              ║
║    1. DÉCOUVERTE  : sitemap WordPress + page BOC → liste exhaustive   ║
║    2. SONDAGE     : génération de toutes les URLs candidates par date ║
║       (calendrier de cotation lun/mer/ven puis quotidien) + HEAD      ║
╚══════════════════════════════════════════════════════════════════════╝

Usage :
    # Tout télécharger depuis 2019
    python bvmac_downloader.py --start 2019-01-01 --out ./bulletins

    # Une période spécifique
    python bvmac_downloader.py --start 2024-01-01 --end 2024-12-31

    # Télécharger PUIS extraire les données avec bvmac_extract.py
    python bvmac_downloader.py --start 2025-01-01 --extract

Dépendances :  pip install requests
"""

import re
import sys
import time
import argparse
import json
import html
from pathlib import Path
from datetime import datetime, date, timedelta
from typing import List, Set, Optional, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

try:
    import requests
except ImportError:
    sys.exit("Module 'requests' manquant :  pip install requests")


BASE_URL    = "https://www.bvm-ac.org"
UPLOADS_URL = f"{BASE_URL}/wp-content/uploads"
BOC_PAGE    = f"{BASE_URL}/bulletin-officiel-de-la-cote-boc/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9",
}

# Délai entre requêtes (politesse envers le serveur)
REQUEST_DELAY = 0.4
# Threads parallèles pour le sondage HEAD
PROBE_WORKERS = 4
DOWNLOAD_WORKERS = 4


# ══════════════════════════════════════════════════════════════════════
#  SESSION HTTP avec retry
# ══════════════════════════════════════════════════════════════════════

def _make_session() -> requests.Session:
    sess = requests.Session()
    sess.headers.update(HEADERS)
    adapter = requests.adapters.HTTPAdapter(
        max_retries=requests.adapters.Retry(
            total=3, backoff_factor=1.5,
            status_forcelist=[429, 500, 502, 503, 504],
        )
    )
    sess.mount("https://", adapter)
    sess.mount("http://", adapter)
    return sess


# ══════════════════════════════════════════════════════════════════════
#  CALENDRIER DE COTATION
# ══════════════════════════════════════════════════════════════════════
#  • Avant ~mi-2024 : séances lundi / mercredi / vendredi
#  • Après          : tous les jours ouvrés (lundi → vendredi)
#  On génère TOUS les jours ouvrés sur toute la période : un sondage
#  inutile coûte juste une requête HEAD 404, c'est plus sûr que de
#  rater des bulletins à cause d'un changement de calendrier.
# ══════════════════════════════════════════════════════════════════════

def trading_days(start: date, end: date) -> Iterator[date]:
    """Génère tous les jours ouvrés (lun-ven) entre start et end inclus."""
    d = start
    while d <= end:
        if d.weekday() < 5:          # 0=lundi … 4=vendredi
            yield d
        d += timedelta(days=1)


# ══════════════════════════════════════════════════════════════════════
#  GÉNÉRATION DES URLs CANDIDATES PAR DATE DE SÉANCE
# ══════════════════════════════════════════════════════════════════════

def candidate_urls(session_date: date) -> List[str]:
    """
    Retourne toutes les URLs plausibles pour le BOC d'une date de séance,
    en combinant chaque convention de nommage avec les dossiers d'upload
    possibles (mois de la séance + 3 mois suivants — les uploads sont
    parfois très en retard, ex. BOC de janv. 2023 dans 2023/04).
    """
    d, m, y4 = session_date.day, session_date.month, session_date.year
    y2 = y4 % 100

    # Depuis 2023, l'URL officielle suit presque toujours la convention
    # BOC-YYYYMMDD.pdf. On ne conserve que deux noms et deux dossiers
    # possibles, au lieu de lancer des dizaines de requêtes par date.
    if y4 >= 2023:
        names = [
            f"BOC-{y4}{m:02d}{d:02d}.pdf",
            f"BOC-{y4}{m:02d}{d:02d}-1.pdf",
        ]
        folder_count = 2
    else:
        names = [
            f"BOC-{d:02d}-{m:02d}-{y2:02d}.pdf",
            f"BOC-BVMAC-{d:02d}-{m:02d}-{y2:02d}.pdf",
            f"BOC-BVMAC-{d:02d}-{m:02d}-{y4}.pdf",
            f"BOC-{d:02d}-{m:02d}-{y4}.pdf",
        ]
        folder_count = 4

    folders = []
    fy, fm = y4, m
    for _ in range(folder_count):
        folders.append(f"{fy}/{fm:02d}")
        fm += 1
        if fm > 12:
            fm, fy = 1, fy + 1

    return [f"{UPLOADS_URL}/{folder}/{name}"
            for folder in folders for name in names]


# ══════════════════════════════════════════════════════════════════════
#  STRATÉGIE 1 ─ DÉCOUVERTE via sitemap WordPress + page BOC
# ══════════════════════════════════════════════════════════════════════

_PDF_LINK_RE = re.compile(
    r"https?://www\.bvm-ac\.org/wp-content/uploads/\d{4}/\d{2}/"
    r"BOC[^\"'\s<>]*?\.pdf",
    re.IGNORECASE,
)


def discover_from_sitemap(sess: requests.Session) -> Set[str]:
    """
    Parcourt les sitemaps WordPress (wp-sitemap.xml et variantes) pour
    découvrir toutes les URLs de PDF BOC référencées.
    """
    found: Set[str] = set()
    sitemap_roots = [
        f"{BASE_URL}/wp-sitemap.xml",
        f"{BASE_URL}/sitemap_index.xml",
        f"{BASE_URL}/sitemap.xml",
    ]

    to_visit, visited = list(sitemap_roots), set()
    while to_visit:
        url = to_visit.pop(0)
        if url in visited:
            continue
        visited.add(url)
        try:
            r = sess.get(url, timeout=30)
            if r.status_code != 200:
                continue
            body = r.text
            # PDFs BOC directement référencés
            found.update(_PDF_LINK_RE.findall(body))
            # Sous-sitemaps à explorer
            for sub in re.findall(r"<loc>\s*(https?://[^<]+\.xml)\s*</loc>", body):
                if sub not in visited and len(visited) < 200:
                    to_visit.append(sub)
            time.sleep(REQUEST_DELAY)
        except requests.RequestException:
            continue

    return found


def discover_from_boc_page(sess: requests.Session, max_pages: int = 400) -> Set[str]:
    """
    Parcourt la page « Bulletin Officiel de la Cote » (paginée) et extrait
    tous les liens PDF. Le site utilise le plugin Download Monitor : les
    liens peuvent être directs (…/uploads/…BOC….pdf) ou indirects
    (/download/xxxx/) — on suit les deux.
    """
    found: Set[str] = set()
    dlm_links: Set[str] = set()

    page_params = [
        "",                       # page 1
        *[f"?frm-page-19={i}" for i in range(2, max_pages)],
        *[f"page/{i}/" for i in range(2, max_pages)],
    ]

    empty_streak = 0
    for suffix in page_params:
        url = BOC_PAGE + suffix
        try:
            r = sess.get(url, timeout=30)
        except requests.RequestException:
            break
        if r.status_code != 200:
            empty_streak += 1
            if empty_streak >= 3:
                break
            continue

        before = len(found) + len(dlm_links)
        body = html.unescape(r.text)
        found.update(_PDF_LINK_RE.findall(body))

        # Récupérer directement tous les href, y compris lorsqu'ils sont
        # relatifs. C'est la page officielle qui fournit le catalogue.
        for href in re.findall(r"href\s*=\s*[\"']([^\"']+)[\"']", body, re.I):
            full = urljoin(url, href.strip())
            low = full.lower()
            if "/wp-content/uploads/" in low and "boc" in low and ".pdf" in low:
                found.add(full.split("?", 1)[0])
            elif "/download/" in low:
                dlm_links.add(full)

        if len(found) + len(dlm_links) == before:
            empty_streak += 1
            if empty_streak >= 3:        # 3 pages sans nouveauté → fin
                break
        else:
            empty_streak = 0
        time.sleep(REQUEST_DELAY)

    # Résoudre les liens Download-Monitor avec GET : le serveur BVMAC renvoie
    # parfois un faux 404 aux requêtes HEAD alors que le PDF est accessible.
    for dlm in sorted(dlm_links):
        try:
            with sess.get(dlm, allow_redirects=True, stream=True,
                          timeout=(10, 30)) as r:
                if r.status_code == 200:
                    final = r.url.split("?", 1)[0]
                    disposition = r.headers.get("Content-Disposition", "")
                    if ((final.lower().endswith(".pdf") and "BOC" in final.upper())
                            or "boc" in disposition.lower()):
                        found.add(final)
            time.sleep(REQUEST_DELAY)
        except requests.RequestException:
            continue

    return found


# ══════════════════════════════════════════════════════════════════════
#  STRATÉGIE 2 ─ SONDAGE direct des URLs candidates
# ══════════════════════════════════════════════════════════════════════

def _url_is_pdf(sess: requests.Session, url: str) -> bool:
    """Teste une URL sans se fier à HEAD, souvent rejeté à tort par WordPress."""
    try:
        head = sess.head(url, timeout=(8, 18), allow_redirects=True)
        if head.status_code == 200 and "pdf" in head.headers.get("Content-Type", "").lower():
            return True
    except requests.RequestException:
        pass
    try:
        # GET partiel : certains liens affichables dans le navigateur répondent
        # 404 à HEAD. On ne lit que la signature pour préserver la bande passante.
        with sess.get(url, headers={"Range": "bytes=0-31"}, stream=True,
                      timeout=(10, 25), allow_redirects=True) as rep:
            if rep.status_code not in (200, 206):
                return False
            debut = next(rep.iter_content(chunk_size=32), b"")
            return debut.startswith(b"%PDF") or "pdf" in rep.headers.get("Content-Type", "").lower()
    except (requests.RequestException, StopIteration):
        return False


def _probe_one(session_date: date) -> Optional[str]:
    """Teste les variantes pour une date avec une session propre au thread."""
    sess = _make_session()
    try:
        for url in candidate_urls(session_date):
            if _url_is_pdf(sess, url):
                return url
        return None
    finally:
        sess.close()


def probe_date_range(sess: requests.Session, start: date, end: date,
                     already_found: Set[str]) -> Set[str]:
    """
    Sonde toutes les dates de séance de la période pour lesquelles
    aucun PDF n'a encore été découvert.
    """
    # Dates déjà couvertes par la découverte (extraites des noms de fichiers)
    covered: Set[date] = set()
    for url in already_found:
        dt = url_to_session_date(url)
        if dt:
            covered.add(dt)

    to_probe = [d for d in expected_publication_days(start, end)
                if d not in covered]
    found: Set[str] = set()

    print(f"  Sondage de {len(to_probe)} dates non couvertes "
          f"({len(covered)} déjà découvertes)…")

    with ThreadPoolExecutor(max_workers=PROBE_WORKERS) as pool:
        futures = {pool.submit(_probe_one, d): d for d in to_probe}
        done = 0
        for fut in as_completed(futures):
            done += 1
            url = fut.result()
            if url:
                found.add(url)
            if done % 50 == 0:
                print(f"    … {done}/{len(to_probe)} dates sondées, "
                      f"{len(found)} PDF trouvés")

    return found


# ══════════════════════════════════════════════════════════════════════
#  UTILITAIRES : nom de fichier → date de séance
# ══════════════════════════════════════════════════════════════════════

def url_to_session_date(url: str) -> Optional[date]:
    """
    Déduit la date de séance depuis le nom du fichier (pas du dossier !).
    Tolérant aux nombreuses irrégularités observées sur le site :
      BOC-20260609.pdf, BOC-20241231-1.pdf, BOC20230227.pdf,
      BOC-automatise-20230120.pdf, BOC-automtise-20230109.pdf (typo),
      BOC-automatise-du-20221214.pdf, BOC-202308011.pdf (9 chiffres),
      BOC-20240124..pdf, BOC-20240507-.pdf, BOC-20240614_compressed.pdf,
      BOC-20241018-compresse-1.pdf, BOC-BVMAC-02-12-19.pdf,
      BOC-BVMAC22-01-2021.pdf, BOC-BVMAC.-08-02-2021.pdf,
      BOC-BVMAC27-01-2021-002.pdf, BOC-30-12-19.pdf …
    """
    name = url.rsplit("/", 1)[-1]

    def _valid(y: int, m: int, d: int) -> Optional[date]:
        try:
            dt = date(y, m, d)
            return dt if date(2010, 1, 1) <= dt <= date(2040, 1, 1) else None
        except ValueError:
            return None

    # ── 1. Format compact YYYYMMDD (avec préfixes/suffixes quelconques) ──
    # Cas 9 chiffres avec zéro parasite D'ABORD : BOC-202308011 → 2023-08-11
    # (sinon « 20230801 » matcherait et les 4 fichiers d'août 2023 à
    #  9 chiffres seraient tous mappés à tort sur le 1er août)
    m9 = re.search(r"(?<!\d)(20\d{2})(\d{2})0(\d{2})(?!\d)", name)
    if m9:
        dt = _valid(int(m9.group(1)), int(m9.group(2)), int(m9.group(3)))
        if dt:
            return dt

    # Gère BOC-20260609, BOC20230227, BOC-automatise-…, _compressed, -1, etc.
    m = re.search(r"(20\d{2})(\d{2})(\d{2})", name)
    if m:
        dt = _valid(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if dt:
            return dt

    # ── 2. Format DD-MM-YYYY (séparateurs ou collage BVMAC tolérés) ──────
    # Gère BOC-BVMAC-29-05-2020, BOC-BVMAC22-01-2021, BOC-BVMAC.-08-02-2021
    m = re.search(r"(\d{2})-(\d{2})-(20\d{2})", name)
    if m:
        dt = _valid(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        if dt:
            return dt

    # ── 3. Format DD-MM-YY (2 chiffres d'année) ──────────────────────────
    # Gère BOC-30-12-19, BOC-BVMAC-02-12-19
    m = re.search(r"(\d{2})-(\d{2})-(\d{2})(?!\d)", name)
    if m:
        dt = _valid(2000 + int(m.group(3)), int(m.group(2)), int(m.group(1)))
        if dt:
            return dt

    return None


def canonical_filename(url: str) -> str:
    """
    Nom de fichier local normalisé : BOC-YYYYMMDD.pdf
    (compatible avec _filename_to_date() de bvmac_extractor.py)
    """
    dt = url_to_session_date(url)
    if dt:
        return f"BOC-{dt:%Y%m%d}.pdf"
    return url.rsplit("/", 1)[-1]


# ══════════════════════════════════════════════════════════════════════
#  TÉLÉCHARGEMENT
# ══════════════════════════════════════════════════════════════════════

def _upload_rank(url: str) -> tuple:
    name = url.rsplit("/", 1)[-1].lower()
    suffix_m = re.search(r"-(\d+)\.pdf$", name)
    is_wp_suffix = bool(suffix_m) and bool(re.search(r"\d{8}-\d+\.pdf$", name))
    return (
        int(suffix_m.group(1)) if is_wp_suffix else 0,
        0 if "automatise" in name else 1,
    )

def _download_single(dt: date, url: str, out_dir: Path) -> tuple[Optional[Path], Optional[str]]:
    """Télécharge atomiquement un PDF et essaie les variantes si l'URL échoue."""
    dest = out_dir / canonical_filename(url)

    if dest.exists() and dest.stat().st_size > 1_000:
        try:
            if dest.open("rb").read(4) == b"%PDF":
                return dest, None
        except OSError:
            pass

    sess = _make_session()
    erreurs = []
    essais = [url] + [u for u in candidate_urls(dt) if u != url]
    try:
        for candidate in essais:
            part = dest.with_suffix(".pdf.part")
            try:
                with sess.get(candidate, stream=True, timeout=(12, 120),
                              allow_redirects=True) as rep:
                    if rep.status_code == 404:
                        continue
                    rep.raise_for_status()
                    taille = 0
                    with part.open("wb") as flux:
                        for bloc in rep.iter_content(chunk_size=64 * 1024):
                            if bloc:
                                flux.write(bloc); taille += len(bloc)
                if taille < 1_000 or part.open("rb").read(4) != b"%PDF":
                    part.unlink(missing_ok=True)
                    erreurs.append(f"{candidate}: contenu non-PDF")
                    continue
                part.replace(dest)
                print(f"    ✓ {dt}  ←  {candidate.rsplit('/', 1)[-1]}  ({taille//1024} Ko)")
                return dest, None
            except (requests.RequestException, OSError) as exc:
                part.unlink(missing_ok=True)
                erreurs.append(f"{candidate}: {exc}")
        message = erreurs[-1] if erreurs else "aucune variante publiée"
        print(f"    ✗ {dt} : {message}")
        return None, message
    finally:
        sess.close()

def download_all(urls: Set[str], out_dir: Path,
                 start: date, end: date) -> tuple[List[Path], List[dict]]:
    """Télécharge tous les PDF en parallèle vers out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)
    downloaded: List[Path] = []
    failures: List[dict] = []

    by_date: dict = {}
    for url in sorted(urls):
        dt = url_to_session_date(url)
        if dt is None or not (start <= dt <= end):
            continue
        if dt not in by_date or _upload_rank(url) > _upload_rank(by_date[dt]):
            by_date[dt] = url

    print(f"\n  {len(by_date)} bulletins à télécharger "
          f"({start} → {end}) - {DOWNLOAD_WORKERS} fichiers simultanés")

    # Utilisation d'un ThreadPoolExecutor au lieu d'une boucle for séquentielle
    with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as pool:
        futures = {
            pool.submit(_download_single, dt, url, out_dir): dt
            for dt, url in by_date.items()
        }
        for fut in as_completed(futures):
            res, erreur = fut.result()
            if res:
                downloaded.append(res)
            elif erreur:
                failures.append({"date": str(futures[fut]), "error": erreur})

    return downloaded, failures


# ══════════════════════════════════════════════════════════════════════
#  AMORCE : liste d'URLs connue (fichier texte, une URL par ligne)
# ══════════════════════════════════════════════════════════════════════

def load_url_list(path: Path) -> Set[str]:
    """
    Charge une liste d'URLs de PDF BOC depuis un fichier texte
    (une URL par ligne ; les lignes tronquées/incomplètes sont réparées).
    """
    urls: Set[str] = set()
    if not path.exists():
        print(f"  ⚠ Fichier liste introuvable : {path}")
        return urls

    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip().rstrip("\r")
        if not line:
            continue
        # Réparer une ligne tronquée en début de fichier (copier-coller)
        if not line.startswith("http"):
            idx = line.find("/wp-content/")
            if idx >= 0:
                line = BASE_URL + line[idx:]
            elif "/20" in line and line.lower().endswith(".pdf"):
                line = UPLOADS_URL + "/" + line.lstrip("s/")
            else:
                continue
        if line.lower().endswith(".pdf") and "boc" in line.lower():
            urls.add(line)

    return urls


def latest_local_date(out_dir: Path) -> Optional[date]:
    """Date de séance du bulletin le plus récent déjà téléchargé."""
    latest = None
    if out_dir.exists():
        for f in out_dir.glob("BOC*.pdf"):
            dt = url_to_session_date(f.name)
            if dt and (latest is None or dt > latest):
                latest = dt
    return latest


def local_dates(out_dir: Path) -> Set[date]:
    """Dates des PDF locaux valides, utilisées pour détecter les trous."""
    dates: Set[date] = set()
    if not out_dir.exists():
        return dates
    for fichier in out_dir.glob("BOC*.pdf"):
        dt = url_to_session_date(fichier.name)
        try:
            valide = fichier.stat().st_size > 1_000 and fichier.open("rb").read(4) == b"%PDF"
        except OSError:
            valide = False
        if dt and valide:
            dates.add(dt)
    return dates


def expected_publication_days(start: date, end: date) -> Iterator[date]:
    """Calendrier prudent : lun/mer/ven historique, puis chaque jour ouvré."""
    for dt in trading_days(start, end):
        if dt < date(2024, 7, 1) and dt.weekday() not in (0, 2, 4):
            continue
        yield dt


# ══════════════════════════════════════════════════════════════════════
#  PIPELINE COMPLET
# ══════════════════════════════════════════════════════════════════════

def fetch_bulletins(start: date, end: date, out_dir: Path,
                    skip_sitemap: bool = False,
                    skip_probe: bool = False,
                    url_list: Optional[Path] = None,
                    probe_start: Optional[date] = None) -> tuple[List[Path], List[dict], Set[str]]:
    """
    Pipeline complet : amorce (liste) → découverte → sondage → téléchargement.
    Retourne la liste des fichiers PDF locaux.
    """
    sess = _make_session()
    all_urls: Set[str] = set()

    print("═" * 60)
    print(f"  BVMAC BOC Downloader  │  {start} → {end}")
    print("═" * 60)

    # ── Étape 0 : amorce depuis la liste fournie ─────────────────────
    if url_list:
        print(f"\n[0/3] Chargement de la liste d'URLs : {url_list}")
        seeded = load_url_list(url_list)
        print(f"      {len(seeded)} URLs chargées")
        all_urls |= seeded

    # ── Étape 1 : découverte ─────────────────────────────────────────
    if not skip_sitemap:
        print("\n[1/3] Découverte via sitemap WordPress…")
        urls = discover_from_sitemap(sess)
        print(f"      {len(urls)} PDF BOC trouvés dans le sitemap")
        all_urls |= urls

    # La page officielle est la source principale et reste toujours parcourue,
    # même lorsque --skip-sitemap est demandé pour accélérer le traitement.
    print("[1b ] Lecture directe des URL de la page BOC…")
    urls = discover_from_boc_page(sess)
    print(f"      {len(urls)} PDF BOC trouvés sur la page")
    all_urls |= urls

    # ── Étape 2 : sondage des dates manquantes ───────────────────────
    if not skip_probe:
        print("\n[2/3] Sondage direct des URLs candidates…")
        all_urls |= probe_date_range(sess, probe_start or start, end, all_urls)

    print(f"\n      TOTAL : {len(all_urls)} URLs uniques découvertes")

    # ── Étape 3 : téléchargement ─────────────────────────────────────
    print("\n[3/3] Téléchargement…")
    files, failures = download_all(all_urls, out_dir, start, end)

    print("\n" + "═" * 60)
    print(f"  TERMINÉ ✓  {len(files)} bulletins dans {out_dir}/")
    print("═" * 60)
    return files, failures, all_urls


# ══════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Télécharge les BOC de la BVMAC (bvm-ac.org)")
    parser.add_argument("--start", default="2019-01-01",
                        help="Date de début (YYYY-MM-DD, défaut 2019-01-01)")
    parser.add_argument("--end", default=None,
                        help="Date de fin (YYYY-MM-DD, défaut aujourd'hui)")
    parser.add_argument("--out", default="./bulletins",
                        help="Dossier de destination (défaut ./bulletins)")
    parser.add_argument("--url-list", default=None,
                        help="Fichier texte d'URLs connues (une par ligne) "
                             "utilisé comme amorce — évite de re-sonder ces dates")
    parser.add_argument("--update", action="store_true", default=True,
                        help="Mode quotidien avec contrôle des trous récents (défaut).")
    parser.add_argument("--no-update", action="store_false", dest="update",
                        help="Désactiver le mode quotidien et sonder toute la période.")
    parser.add_argument("--repair-days", type=int, default=120,
                        help="Fenêtre de re-sondage des dates manquantes (défaut 120 jours)")
    parser.add_argument("--publication-grace-days", type=int, default=10,
                        help="Délai de republication recherché après une date absente (défaut 10 jours)")
    parser.add_argument("--full-probe", action="store_true",
                        help="Sondage de réparation de toute la période (à lancer ponctuellement)")
    parser.add_argument("--report", default=None,
                        help="Chemin du rapport JSON de suivi des trous")
    parser.add_argument("--skip-sitemap", action="store_true",
                        help="Sauter la découverte sitemap (sondage seul)")
    parser.add_argument("--skip-probe", action="store_true",
                        help="Sauter le sondage (sitemap/liste seuls, plus rapide)")
    parser.add_argument("--extract", action="store_true",
                        help="Lancer bvmac_extract.py sur les PDF téléchargés")
    args = parser.parse_args()

    out_dir = Path(args.out)
    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end   = (datetime.strptime(args.end, "%Y-%m-%d").date()
             if args.end else date.today())

    # Ne jamais déplacer la date de début après le dernier fichier : cela créait
    # des trous définitifs. En quotidien, seule la partie coûteuse du sondage
    # direct est limitée à une fenêtre récente ; sitemap et catalogue peuvent
    # toujours réparer un ancien fichier manquant.
    if args.full_probe or not args.update:
        probe_start = start
        print(f"Mode réparation complète : sondage depuis {probe_start}")
    else:
        probe_start = max(start, end - timedelta(days=max(10, args.repair_days)))
        print(f"Mode quotidien : catalogue complet + sondage des trous depuis {probe_start}")

    url_list = Path(args.url_list) if args.url_list else None

    files, failures, _ = fetch_bulletins(
        start, end, out_dir,
        skip_sitemap=args.skip_sitemap,
        skip_probe=args.skip_probe,
        url_list=url_list,
        probe_start=probe_start,
    )

    presentes = local_dates(out_dir)
    attendues = set(expected_publication_days(probe_start, end))
    absentes = sorted(attendues - presentes)
    limite = end - timedelta(days=max(1, args.publication_grace_days))
    pending = [str(dt) for dt in absentes if dt > limite]
    missing = [str(dt) for dt in absentes if dt <= limite]
    report_path = Path(args.report) if args.report else out_dir / "bvmac_download_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    rapport = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "range_start": str(start), "range_end": str(end),
        "probe_start": str(probe_start),
        "publication_grace_days": max(1, args.publication_grace_days),
        "local_pdf_count": len(presentes),
        "pending_publication_dates": pending,
        "missing_published_dates": missing,
        "download_failures": failures[-30:],
    }
    temp_report = report_path.with_suffix(report_path.suffix + ".part")
    temp_report.write_text(json.dumps(rapport, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_report.replace(report_path)
    print(f"Rapport de couverture : {report_path} ({len(pending)} en attente, {len(missing)} trous à réparer)")

    # ── Extraction optionnelle ───────────────────────────────────────
    if args.extract and files:
        print("\n▶ Lancement de l'extraction…")
        extractor = Path(__file__).with_name("bvmac_extract.py")
        if not extractor.is_file():
            print(f"  ✗ Extracteur introuvable : {extractor}")
        else:
            import subprocess
            out_xlsx = out_dir / "bvmac_master.xlsx"
            subprocess.run([
                sys.executable, str(extractor),
                "--pdf-dir", str(out_dir),
                "--out", str(out_xlsx),
                "--mode", "update",
                "--csv",
            ], check=True)


if __name__ == "__main__":
    main()
