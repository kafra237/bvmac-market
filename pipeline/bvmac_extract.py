#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 BVMAC BOC EXTRACTOR - v2.0 (production)
 Bourse des Valeurs Mobilieres de l'Afrique Centrale
================================================================================

 Extrait les donnees structurees des Bulletins Officiels de la Cote (PDF)
 vers UN classeur Excel unique en schema en etoile, pret a charger en base.

 COMPATIBILITE : Windows / macOS / Linux - Python 3.9+
 DEPENDANCES   : pip install pdfplumber pandas openpyxl

 USAGE :
   python bvmac_extract.py --pdf-dir .\\bulletins --out bvmac_master.xlsx
   python bvmac_extract.py --pdf-dir .\\bulletins --selftest

 METHODE D'EXTRACTION :
   Tokenisation par coordonnees (pdfplumber.extract_words) :
   les mots sont regroupes en lignes (proximite verticale) puis fusionnes
   en tokens-colonnes (gap horizontal < 3.5 pt = meme colonne).
   Chaque table devient une liste de tokens PUREMENT POSITIONNELLE,
   immunisee contre les problemes d'espacement du texte PDF.

 FORMATS GERES :
   ANCIEN (2019 - 2022) - 12 tokens exactement :
     [code, nominal, div_date, div_amt, NOM, ref, close,
      vol_bid, vol_ask, vol_traded, variation, statut]
     Capitalisation (>= 2022 uniquement) :
     [NOM, total_shares, float_shares, close, cap_globale,
      cap_flottante_prec, cap_flottante, variation]

   NOUVEAU (2023+) - ancres date / statut / % :
     [ISIN, mnemo, nom, prev | DATE | bid, ask, traded, valeur, ntrans
      | STATUT | open, close, seuil_haut, seuil_bas | VAR% |
      ref_suivant, plus_haut_ytd, plus_bas_ytd, var_annuelle]
     Capitalisation :
     [nom, ISIN, mnemo, nom, close, flottant, titres, div, annee,
      date_div, liquidite, bpa, per, cap_flottante, cap_totale]

================================================================================
"""

import argparse
import re
import sys
import warnings
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

warnings.filterwarnings("ignore")

# ── Console Windows : forcer UTF-8 pour eviter les UnicodeEncodeError ──
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

try:
    import pdfplumber
    import pandas as pd
except ImportError as e:
    sys.exit(f"Dependance manquante ({e.name}). Installez :\n"
             f"  pip install pdfplumber pandas openpyxl")


# ══════════════════════════════════════════════════════════════════════
#  1. REFERENTIEL DES SOCIETES  (cles stables - ne jamais renumeroter)
# ══════════════════════════════════════════════════════════════════════

COMPANIES: Dict[str, dict] = {
    "CM0000010009": dict(company_id=1, ticker="SEMC",  short_name="SEMC",
        full_name="Societe des Eaux Minerales du Cameroun",
        country="CM", sector="Agro-Alimentaire", listing_date="2006-06-30"),
    "CM0000010017": dict(company_id=2, ticker="SAF",   short_name="SAFACAM",
        full_name="Societe Africaine Forestiere et Agricole du Cameroun",
        country="CM", sector="Agro-Industrie", listing_date="2008-07-09"),
    "CM0000010025": dict(company_id=3, ticker="SOCAP", short_name="SOCAPALM",
        full_name="Societe Camerounaise de Palmeraies",
        country="CM", sector="Agro-Alimentaire", listing_date="2009-04-07"),
    "GA0000010033": dict(company_id=4, ticker="SIAT",  short_name="SIAT GABON",
        full_name="Societe d'Investissement pour l'Agriculture Tropicale",
        country="GA", sector="Agro-Industrie", listing_date="2013-09-19"),
    "CM0000010041": dict(company_id=5, ticker="REG",   short_name="LA REGIONALE",
        full_name="La Regionale d'Epargne et de Credit",
        country="CM", sector="Banque", listing_date="2021-07-16"),
    "GQ0000010050": dict(company_id=6, ticker="BANGE", short_name="BANGE",
        full_name="Banco Nacional de Guinea Ecuatorial",
        country="GQ", sector="Banque", listing_date="2022-09-28"),
    "GA0000010066": dict(company_id=7, ticker="SCGRE", short_name="SCG-Re",
        full_name="Societe Commerciale Gabonaise de Reassurance",
        country="GA", sector="Reassurance", listing_date="2023-01-26"),
    "GA0000010074": dict(company_id=8, ticker="BHC",   short_name="BGFI HC",
        full_name="BGFI Holding Corporation",
        country="GA", sector="Holding bancaire", listing_date="2026-05-07"),
}

# Nom (ancien format, normalise) -> ISIN
NAME_TO_ISIN = {
    "SEMC": "CM0000010009", "SAFACAM": "CM0000010017",
    "SOCAPALM": "CM0000010025", "SIATGABON": "GA0000010033",
    "LAREGIONALE": "CM0000010041", "BANGE": "GQ0000010050",
    "SCG-RE": "GA0000010066", "SCGRE": "GA0000010066",
    "BGFIHC": "GA0000010074", "BHC": "GA0000010074",
}

ISIN_RE   = re.compile(r"^[A-Z]{2}\d{10}$")
DATE_RE   = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
DATE2_RE  = re.compile(r"^(\d{2})/(\d{2})/(\d{2})$")
STATUS_RE = re.compile(r"^(NC|PEq|PEQ|S)$")
PCT_RE    = re.compile(r"^-?\d{1,3}(?:[.,]\d+)?%$")
NUM_RE    = re.compile(r"^-?\d{1,3}(?:\d{3})*(?:[.,]\d+)?$")


# ══════════════════════════════════════════════════════════════════════
#  2. UTILITAIRES TOKENS
# ══════════════════════════════════════════════════════════════════════

def norm(token: str) -> str:
    """Normalise un token : retire TOUS les espaces internes.
    '5 5 0 0 0 ,0 0' -> '55000,00'   |   '10 010 000' -> '10010000'"""
    return token.replace(" ", "").replace("\u00a0", "")


def to_num(token: str) -> Optional[float]:
    """Token -> float (format FCFA : virgule decimale)."""
    s = norm(token).replace(",", ".")
    if not s or s == "-" or not NUM_RE.match(s.rstrip("%")) and not re.match(r"^-?\d+(\.\d+)?$", s.rstrip("%")):
        try:
            return float(s.rstrip("%"))
        except ValueError:
            return None
    try:
        return float(s.rstrip("%"))
    except ValueError:
        return None


def to_date(token: str) -> Optional[date]:
    """Token date DD/MM/YYYY ou DD/MM/YY -> date."""
    s = norm(token)
    m = DATE_RE.match(s)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    m = DATE2_RE.match(s)
    if m:
        try:
            return date(2000 + int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None


ISIN_IN_RE = re.compile(r"[A-Z]{2}\d{10}")

def find_isin(token: str) -> Optional[str]:
    """Cherche un ISIN A L'INTERIEUR du token (les noms longs fusionnent
    parfois avec l'ISIN : 'LA REGIONALE EPARGNE CREDITCM0000010041')."""
    m = ISIN_IN_RE.search(norm(token))
    return m.group(0) if m else None


def find_isin_in_row(tokens: List[str]) -> Optional[str]:
    """Trouve aussi un ISIN coupé en plusieurs objets PDF.

    Depuis mi-juillet 2026, pdfplumber peut lire ``CM0000010009`` sous la
    forme de deux mots ``CM`` et ``0000010009``. La concaténation de la ligne
    restaure le code sans dépendre de l'espacement graphique du PDF.
    """
    direct = next((find_isin(token) for token in tokens if find_isin(token)), None)
    if direct:
        return direct
    return find_isin("".join(norm(token) for token in tokens))


def is_isin(token: str) -> bool:
    return find_isin(token) is not None


def filename_to_date(path) -> Optional[date]:
    """Date de seance depuis le nom de fichier - tolerant a TOUTES les
    variantes observees sur bvm-ac.org (1322 noms testes, 100% parses)."""
    name = Path(path).name

    def valid(y, m, d):
        try:
            dt = date(y, m, d)
            return dt if date(2010, 1, 1) <= dt <= date(2040, 1, 1) else None
        except ValueError:
            return None

    m = re.search(r"(?<!\d)(20\d{2})(\d{2})0(\d{2})(?!\d)", name)   # 9 chiffres
    if m:
        dt = valid(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if dt:
            return dt
    m = re.search(r"(20\d{2})(\d{2})(\d{2})", name)                  # YYYYMMDD
    if m:
        dt = valid(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if dt:
            return dt
    m = re.search(r"(\d{2})-(\d{2})-(20\d{2})", name)                # DD-MM-YYYY
    if m:
        dt = valid(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        if dt:
            return dt
    m = re.search(r"(\d{2})-(\d{2})-(\d{2})(?!\d)", name)            # DD-MM-YY
    if m:
        dt = valid(2000 + int(m.group(3)), int(m.group(2)), int(m.group(1)))
        if dt:
            return dt
    return None


# ══════════════════════════════════════════════════════════════════════
#  3. LECTURE PDF -> LIGNES DE TOKENS
# ══════════════════════════════════════════════════════════════════════

LINE_TOL       = 3.0   # pt : tolerance verticale pour regrouper en lignes
MERGE_GAP      = 3.5   # pt : fusion standard (cap, ancien format, entetes)
MERGE_GAP_TIGHT = 2.5  # pt : tables de cotation 2023+ a interligne serre
                       #      (gaps mesures : 1.72 intra-nombre,
                       #       3.0 - 4.9 inter-colonnes)

# Statut de marche colle au nombre suivant DANS le PDF ('NC206 850')
FUSED_STATUS_RE = re.compile(r"^(NC|PEq|PEQ|S)\s*(\d.*)$")


def read_pdf_word_rows(pdf_path) -> List[List[dict]]:
    """Lit tout le PDF : liste de lignes, chaque ligne = mots bruts
    (text, x0, x1) tries de gauche a droite."""
    all_rows: List[List[dict]] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            words = page.extract_words()
            lines: List[List[dict]] = []
            for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
                for line in lines:
                    if abs(line[0]["top"] - w["top"]) <= LINE_TOL:
                        line.append(w)
                        break
                else:
                    lines.append([w])
            lines.sort(key=lambda l: l[0]["top"])
            for line in lines:
                line.sort(key=lambda w: w["x0"])
                all_rows.append([{"text": w["text"], "top": w["top"],
                                  "x0": w["x0"], "x1": w["x1"]}
                                 for w in line])
    return all_rows


def merge_row(words: List[dict], gap: float) -> List[str]:
    """Fusionne les mots d'une ligne en tokens-colonnes (gap < seuil),
    puis eclate les statuts colles ('NC206 850' -> 'NC', '206 850')."""
    toks: List[dict] = []
    for w in words:
        if toks and w["x0"] - toks[-1]["x1"] < gap:
            toks[-1]["text"] += " " + w["text"]
            toks[-1]["x1"] = w["x1"]
        else:
            toks.append(dict(w))
    out: List[str] = []
    for t in toks:
        m = FUSED_STATUS_RE.match(norm(t["text"]))
        if m:
            out.append(m.group(1))
            out.append(m.group(2))
        else:
            out.append(t["text"])
    return out


def read_pdf_token_rows(pdf_path, gap: float = MERGE_GAP) -> List[List[str]]:
    """Compatibilite : lignes de tokens fusionnes au seuil donne."""
    return [merge_row(words, gap) for words in read_pdf_word_rows(pdf_path)]


def detect_format(rows: List[List[str]]) -> str:
    """'new' si un ISIN apparait sur une ligne de DONNEES (avec date de
    seance DD/MM/YYYY ou statut de marche), sinon 'old'.
    Un ISIN isole dans le texte (section obligations, commentaire) ne
    suffit pas : les bulletins 2022 en contiennent sans etre au nouveau
    format."""
    for row in rows:
        if not find_isin_in_row(row):
            continue
        has_date   = any(DATE_RE.match(norm(t)) for t in row)
        has_status = any(STATUS_RE.match(norm(t)) for t in row)
        has_nums   = sum(1 for t in row if to_num(norm(t)) is not None) >= 4
        if has_date or has_status or has_nums:
            return "new"
    return "old"


# ══════════════════════════════════════════════════════════════════════
#  4. PARSEUR - NOUVEAU FORMAT (2023+)
# ══════════════════════════════════════════════════════════════════════

def parse_new_actions(rows: List[List[str]],
                      bulletin_date: date) -> List[dict]:
    """
    Table MARCHE DES ACTIONS - ancres : DATE / STATUT / %
      avant DATE          : prev_price (dernier token numerique)
      DATE -> STATUT      : [vol_bid, vol_ask, vol_traded, valeur, ntrans]
      STATUT -> %         : [open, close, seuil_haut, seuil_bas]
      apres %             : [ref_suivant, ytd_haut, ytd_bas, var_annuelle]
    Les societes au nom long (BANGE, SCG-Re, BGFI HC) ont leur ligne ISIN
    SEPARE de la ligne de donnees : on associe la ligne de donnees la plus
    proche (en-dessous, <= 3 lignes) contenant une date.
    """
    records: List[dict] = []
    seen: set = set()

    def normalize_session_date(d: Optional[date]) -> Optional[date]:
        """Corrige une annee manifestement erronee dans le bulletin.

        Le BOC du 15/05/2026, par exemple, imprime 13/05/2025 dans les
        lignes actions. La correction n'est acceptee que lorsque remplacer
        l'annee precedente par celle du bulletin ramene la date a dix jours
        ou moins de la publication.
        """
        if d is None:
            return None
        if abs((d - bulletin_date).days) <= 10:
            return d
        if d.year == bulletin_date.year - 1:
            try:
                corrected = d.replace(year=bulletin_date.year)
            except ValueError:
                return None
            if abs((corrected - bulletin_date).days) <= 10:
                return corrected
        return None

    def near_bulletin(d: Optional[date]) -> bool:
        return normalize_session_date(d) is not None

    for i, row in enumerate(rows):
        isin = find_isin_in_row(row)
        if not isin or isin not in COMPANIES or isin in seen:
            continue

        # Ligne de donnees : statut de marche (NC/PEq/S) OBLIGATOIRE
        # + date de seance PROCHE du bulletin (exclut les dates de
        # dividende des lignes capitalisation, ex. REG 18/09/2024)
        data_row = None
        for j in range(i, min(i + 8, len(rows))):
            cand = rows[j]
            has_status = any(STATUS_RE.match(norm(t)) for t in cand)
            has_near_date = any(near_bulletin(to_date(norm(t)))
                                and DATE_RE.match(norm(t)) for t in cand)
            if has_status and has_near_date:
                candidate_isin = find_isin_in_row(cand)
                other = candidate_isin if candidate_isin and candidate_isin != isin else None
                if other:
                    break
                data_row = cand
                break
        if data_row is None:
            continue
        seen.add(isin)

        toks = data_row
        n    = [norm(t) for t in toks]

        idx_date = next((k for k, t in enumerate(n) if DATE_RE.match(t)), None)
        idx_stat = next((k for k, t in enumerate(toks)
                         if STATUS_RE.match(norm(t))), None)
        idx_pct  = next((k for k, t in enumerate(n) if PCT_RE.match(t)), None)
        if idx_date is None:
            continue

        rec = dict(isin=isin, bulletin_date=bulletin_date,
                   session_date=normalize_session_date(to_date(n[idx_date])),
                   status=norm(toks[idx_stat]) if idx_stat is not None else None,
                   variation_pct=to_num(n[idx_pct]) if idx_pct is not None else None)

        # prev_price : dernier numerique avant la date
        prev = None
        for k in range(idx_date - 1, -1, -1):
            v = to_num(n[k])
            if v is not None:
                prev = v
                break
        rec["prev_price"] = prev

        # Section B : volumes / valeur / transactions
        b_end = idx_stat if idx_stat is not None else (idx_pct or len(toks))
        secB  = [to_num(t) for t in n[idx_date + 1: b_end]]
        secB  = [v for v in secB if v is not None]
        rec["vol_bid"]      = secB[0] if len(secB) > 0 else None
        rec["vol_ask"]      = secB[1] if len(secB) > 1 else None
        rec["vol_traded"]   = secB[2] if len(secB) > 2 else None
        rec["value_traded"] = secB[3] if len(secB) > 3 else None
        rec["num_transactions"] = int(secB[4]) if len(secB) > 4 else None

        # Section C : open / close / seuils
        if idx_stat is not None:
            c_end = idx_pct if idx_pct is not None else len(toks)
            secC  = [to_num(t) for t in n[idx_stat + 1: c_end]]
            secC  = [v for v in secC if v is not None]
            rec["open_price"]  = secC[0] if len(secC) > 0 else None
            rec["close_price"] = secC[1] if len(secC) > 1 else rec.get("open_price")
            rec["upper_limit"] = secC[2] if len(secC) > 2 else None
            rec["lower_limit"] = secC[3] if len(secC) > 3 else None
        else:
            rec["open_price"] = rec["close_price"] = prev
            rec["upper_limit"] = rec["lower_limit"] = None

        # Section D : reference suivante / YTD / variation annuelle
        if idx_pct is not None:
            secD = [to_num(t) for t in n[idx_pct + 1:]]
            rec["next_ref_price"]    = secD[0] if len(secD) > 0 else None
            rec["ytd_high"]          = secD[1] if len(secD) > 1 else None
            rec["ytd_low"]           = secD[2] if len(secD) > 2 else None
            rec["yoy_variation_pct"] = secD[3] if len(secD) > 3 else None
        else:
            rec["next_ref_price"] = rec["ytd_high"] = None
            rec["ytd_low"] = rec["yoy_variation_pct"] = None

        rec["last_div_amount"] = None
        rec["last_div_date"]   = None
        records.append(rec)

    return records


def parse_new_capitalization(rows: List[List[str]],
                             bulletin_date: date) -> List[dict]:
    """
    Table CAPITALISATION (2023+) - 100% positionnelle apres l'ISIN :
      [.., ISIN, mnemo, nom, close, flottant, titres, div, annee,
       date_div, liquidite, bpa, per, cap_flottante, cap_totale]
    """
    records: List[dict] = []
    seen: set = set()

    for row in rows:
        n = [norm(t) for t in row]
        idx_isin = next((k for k, t in enumerate(row) if find_isin(t)), None)
        if idx_isin is None:
            continue
        isin = find_isin(row[idx_isin])
        if isin not in COMPANIES or isin in seen:
            continue
        # Ligne cap = ligne ISIN SANS date de seance ni statut de marche
        if any(STATUS_RE.match(norm(t)) for t in row):
            continue
        rest = n[idx_isin + 1:]
        # Sauter mnemo + nom (tokens non numeriques)
        k = 0
        while k < len(rest) and to_num(rest[k]) is None:
            k += 1
        vals = rest[k:]
        if len(vals) < 5:
            continue
        seen.add(isin)

        def g(idx) -> Optional[float]:
            if idx >= len(vals):
                return None
            return to_num(vals[idx])

        div_date = next((to_date(v) for v in vals if to_date(v)), None)
        records.append(dict(
            isin=isin, bulletin_date=bulletin_date,
            close_price=g(0), float_shares=g(1), total_shares=g(2),
            last_div_amount=g(3),
            last_div_year=int(g(4)) if g(4) and 2000 < g(4) < 2100 else None,
            last_div_date=div_date,
            # Colonnes positionnelles apres la date de dividende :
            #   [6] Liquidite des titres  [7] Benefice net par action (BPA)
            #   [8] PER                   [-2] Cap flottante  [-1] Cap totale
            liquidity_pct=to_num(vals[6]) if len(vals) > 6 else None,
            eps=to_num(vals[7]) if len(vals) > 7 and vals[7] != "-" else None,
            per=to_num(vals[8]) if len(vals) > 8 and vals[8] != "-" else None,
            float_market_cap=to_num(vals[-2]) if len(vals) >= 2 else None,
            total_market_cap=to_num(vals[-1]) if len(vals) >= 1 else None,
        ))
    return records


def parse_index(rows: List[List[str]]) -> Tuple[Optional[float], Optional[float]]:
    """Valeur de l'indice BVMAC-AS et variation du jour (2024+)."""
    idx_value = var_day = None
    for row in rows:
        n = [norm(t) for t in row]
        for k, t in enumerate(n):
            if t.upper() in ("BVMAC-AS", "BVMACAS") and k + 1 < len(n):
                v = to_num(n[k + 1])
                if v and 100 < v < 100_000:
                    idx_value = v
            if "Variationjour" in t.replace(" ", "") and k + 1 < len(n):
                var_day = to_num(n[k + 1].rstrip("%"))
        joined = "".join(n)
        if "Variationjour" in joined:
            m = re.search(r"Variationjour[^0-9\-]*(-?\d+[.,]\d+)%", joined)
            if m:
                var_day = to_num(m.group(1))
    return idx_value, var_day


# ══════════════════════════════════════════════════════════════════════
#  5. PARSEUR - ANCIEN FORMAT (2019 - 2022)
# ══════════════════════════════════════════════════════════════════════

def parse_old_actions(rows: List[List[str]],
                      bulletin_date: date) -> List[dict]:
    """
    Table actions ancienne - 12 tokens exactement :
      [code(1xxx), nominal, div_date, div_amt, NOM, ref, close,
       vol_bid, vol_ask, vol_traded, variation, statut]
    """
    records: List[dict] = []
    seen: set = set()

    for row in rows:
        if len(row) < 10:
            continue
        c0 = norm(row[0])
        if not re.match(r"^1\d{3}$", c0):           # codes actions 1000-1999
            continue
        # Identifier la societe par le nom (token 4, parfois fusionne)
        name_tok = norm(row[4]).upper() if len(row) > 4 else ""
        isin = NAME_TO_ISIN.get(name_tok)
        if isin is None:                              # nom eclate sur 2 tokens ?
            for t in row:
                cand = NAME_TO_ISIN.get(norm(t).upper())
                if cand:
                    isin = cand
                    break
        if isin is None or isin in seen:
            continue
        seen.add(isin)

        # Parsing positionnel DEPUIS LA FIN (robuste aux noms multi-tokens)
        toks = row[:]
        status = norm(toks[-1]) if STATUS_RE.match(norm(toks[-1])) else None
        if status:
            toks = toks[:-1]
        nums_tail = [to_num(norm(t)) for t in toks]
        # [.., ref, close, bid, ask, traded, variation]
        rec = dict(
            isin=isin, bulletin_date=bulletin_date,
            session_date=bulletin_date, status=status,
            prev_price=nums_tail[-6], close_price=nums_tail[-5],
            open_price=None,
            vol_bid=nums_tail[-4], vol_ask=nums_tail[-3],
            vol_traded=nums_tail[-2],
            variation_pct=nums_tail[-1],
            value_traded=None, num_transactions=None,
            upper_limit=None, lower_limit=None, next_ref_price=None,
            ytd_high=None, ytd_low=None, yoy_variation_pct=None,
            last_div_date=to_date(norm(row[2])),
            last_div_amount=to_num(norm(row[3])),
        )
        records.append(rec)
    return records


def parse_old_capitalization(rows: List[List[str]],
                             bulletin_date: date) -> List[dict]:
    """
    Table capitalisation ancienne (2022 uniquement) :
      [NOM, total_shares, float_shares, close, cap_globale,
       cap_flot_prec, cap_flottante, variation]
    """
    records: List[dict] = []
    in_cap = False
    seen: set = set()

    for row in rows:
        joined = " ".join(norm(t) for t in row).upper()
        if "CAPITALISATION" in joined:
            in_cap = True
            continue
        if not in_cap:
            continue
        if "OBLIGATION" in joined:
            in_cap = False
            continue
        name_tok = norm(row[0]).upper() if row else ""
        isin = NAME_TO_ISIN.get(name_tok)
        if not isin or isin in seen or len(row) < 7:
            continue
        nums = [to_num(norm(t)) for t in row[1:]]
        nums = [v for v in nums if v is not None]
        if len(nums) < 6:
            continue
        seen.add(isin)
        records.append(dict(
            isin=isin, bulletin_date=bulletin_date,
            total_shares=nums[0], float_shares=nums[1], close_price=nums[2],
            total_market_cap=nums[3], float_market_cap=nums[5],
            last_div_amount=None, last_div_year=None, last_div_date=None,
            liquidity_pct=None, eps=None, per=None,
        ))
    return records


# ══════════════════════════════════════════════════════════════════════
#  5bis. PARSEUR - OPCVM  (section publiee depuis courant 2023)
# ══════════════════════════════════════════════════════════════════════
#  Colonnes : Societe de gestion | Depositaire | OPCVM | Categorie |
#             Valeur origine | VL precedente | Date | VL actuelle | Date |
#             Date origine | Var% origine | Var% prec.
#
#  Difficultes gerees :
#   - les 3 champs texte (gestionnaire / depositaire / fonds) sont
#     souvent eclates sur les lignes adjacentes (wrapping) -> on les
#     reconstruit par clustering des coordonnees X des mots ;
#   - categorie '0' = typo PDF pour 'O' (obligataire) ;
#   - dates corrompues dans le PDF (ex. '46 120,00') -> None ;
#   - en-tetes repetes a chaque page -> filtres ;
#   - blocs de frequence (Quotidiennes / Hebdomadaires / ...) -> captures.
# ══════════════════════════════════════════════════════════════════════

FUND_NAME_RE = re.compile(r"\b(FCP[ER]?|SICAV)", re.IGNORECASE)
FREQ_RE      = re.compile(
    r"^(Quotidienne|Hebdomadaire|Bimensuelle|Mensuelle|Trimestrielle|"
    r"Semestrielle|Annuelle)s?$", re.IGNORECASE)
OPCVM_HEADER_WORDS = {
    "SOCIETE", "SOCIÉTÉ", "GESTION", "DEPOSITAIRE", "DÉPOSITAIRE", "OPCVM",
    "CATEGORIE", "CATÉGORIE", "PRECEDENTE", "PRÉCÉDENTE", "ACTUELLE",
    "VALEUR", "DATE", "%", "ORIGINE", "PREC.", "PRÉC.", "LIQUIDATIVE",
    "VARIATION", "BULLETIN", "OFFICIEL", "COTE", "CAPITAL", "VARIABLE",
    "DE", "LA", "DU", "N°", ":", "ET", "A", "FONDS", "COMMUN", "PLACEMENT",
    "D'INVESTISSEMENT", "SOCIETE",
}


def _norm_fund_name(name: str) -> str:
    """Cle naturelle d'un fonds : majuscules, espaces normalises,
    prefixe FCP recolle ('FCPHARVEST' -> 'FCP HARVEST')."""
    s = re.sub(r"\s+", " ", name.upper().strip())
    s = re.sub(r"\bFCP(?=[B-DF-QS-Z])", "FCP ", s)   # FCPE/FCPR intacts
    s = re.sub(r"\s*-\s*", "-", s)
    return s


def _is_text_only_row(words: List[dict]) -> bool:
    """Ligne purement textuelle (fragment de wrapping) : aucun nombre,
    aucune date, aucun %, et pas une ligne d'en-tete / titre / pagination."""
    if not words:
        return False
    joined = " ".join(w["text"] for w in words)
    if re.search(r"\d", joined):
        return False
    if ":" in joined or "OPCVM" in joined.upper() \
            or "BULLETIN" in joined.upper():
        return False
    if FREQ_RE.match(joined.strip()):
        return False
    up = {w["text"].upper().strip(".,") for w in words}
    if up and up <= OPCVM_HEADER_WORDS:
        return False
    return True


def _merge_keep_xy(words: List[dict], gap: float) -> List[dict]:
    """Fusion en tokens-colonnes en CONSERVANT les coordonnees."""
    toks: List[dict] = []
    for w in words:
        if toks and w["x0"] - toks[-1]["x1"] < gap:
            toks[-1]["text"] += " " + w["text"]
            toks[-1]["x1"] = w["x1"]
        else:
            toks.append(dict(w))
    return toks


def parse_opcvm(word_rows: List[List[dict]],
                bulletin_date: date) -> List[dict]:
    # ── Delimiter la section OPCVM ────────────────────────────────────
    start = None
    for i, words in enumerate(word_rows):
        joined = " ".join(w["text"] for w in words).upper()
        if "OPCVM" in joined and "FONDS" in joined:
            start = i
            break
    if start is None:
        return []

    # ── Passe 1 : lignes de donnees (suffixe numerique rigide) ────────
    #   [.., cat, val_origine, vl_prec, date_prec, vl_act, date_act,
    #    date_origine, var_origine%, var_prec%]
    data: Dict[int, dict] = {}
    frequency = None

    for i in range(start, len(word_rows)):
        toks_xy = _merge_keep_xy(word_rows[i], MERGE_GAP)
        toks    = [t["text"] for t in toks_xy]
        if not toks:
            continue
        m = FREQ_RE.match(" ".join(toks).strip())
        if m:
            frequency = m.group(1).capitalize()
            continue

        n = [norm(t) for t in toks]
        if len(n) < 9 or not (PCT_RE.match(n[-1]) and PCT_RE.match(n[-2])):
            continue
        d_orig, d_act = to_date(n[-3]), to_date(n[-4])
        if d_orig is None or d_act is None:
            continue
        nav = to_num(n[-5])
        category = norm(toks[-9]).upper().replace("0", "O")
        if nav is None or len(category) > 2:
            continue

        data[i] = dict(
            text_toks=toks_xy[:-9],            # cellules texte de la ligne
            frequency=frequency, category=category,
            initial_value=to_num(n[-8]), prev_nav=to_num(n[-7]),
            prev_nav_date=to_date(n[-6]), nav=nav, nav_date=d_act,
            inception_date=d_orig,
            var_inception_pct=to_num(n[-2]), var_prev_pct=to_num(n[-1]),
        )
    if not data:
        return []

    # ── Apprendre les positions de colonnes DEPUIS LES DONNEES ───────
    # (les libelles d'en-tete sont centres : inutilisables).
    # On collecte les x0 de tous les tokens texte (lignes data +
    # fragments wrappes voisins) puis clustering 1D par coupure de gap.
    neighbor_idx = set()
    for i in data:
        for j in (i - 1, i + 1):
            if 0 <= j < len(word_rows) and j not in data \
                    and _is_text_only_row(word_rows[j]):
                neighbor_idx.add(j)

    all_text_toks: List[dict] = []
    for i in data:
        all_text_toks += data[i]["text_toks"]
    for j in neighbor_idx:
        all_text_toks += _merge_keep_xy(word_rows[j], MERGE_GAP)

    xs = sorted(t["x0"] for t in all_text_toks)
    clusters: List[List[float]] = []
    for x in xs:
        if clusters and x - clusters[-1][-1] < 25:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    centers = [sum(c) / len(c) for c in clusters]

    # colonne 'fonds' = cluster contenant la majorite des tokens FCP/SICAV
    def nearest(x0: float) -> int:
        return min(range(len(centers)), key=lambda k: abs(centers[k] - x0))

    fund_votes: Dict[int, int] = {}
    for t in all_text_toks:
        if FUND_NAME_RE.search(t["text"]):
            k = nearest(t["x0"])
            fund_votes[k] = fund_votes.get(k, 0) + 1
    if not fund_votes:
        return []
    k_fund = max(fund_votes, key=fund_votes.get)
    others = [k for k in range(len(centers)) if k != k_fund]
    k_mgr  = min(others, key=lambda k: centers[k]) if others else None
    k_cust = [k for k in others if k != k_mgr]

    def col_of(t: dict) -> int:
        """0 = gestionnaire, 1 = depositaire, 2 = fonds."""
        k = nearest(t["x0"])
        if k == k_fund:
            return 2
        if k == k_mgr:
            return 0
        return 1

    # ── Passe 2 : repartir les cellules par colonne ───────────────────
    for i, d in data.items():
        cells = {0: [], 1: [], 2: []}
        for t in d["text_toks"]:
            cells[col_of(t)].append((i, t))
        d["cells"] = cells

    # ── Passe 3 : rattacher les fragments wrappes voisins ─────────────
    # Chaque token d'une ligne pur-texte va a la ligne de donnees
    # adjacente dont la colonne est VIDE (besoin) ; data du bas d'abord
    # (cas le plus frequent : nom du fonds imprime AU-DESSUS de sa ligne).
    for j in sorted(neighbor_idx):
        neighbors = [k for k in (j + 1, j - 1) if k in data]
        if not neighbors:
            continue
        for t in _merge_keep_xy(word_rows[j], MERGE_GAP):
            c = col_of(t)

            def score(k: int) -> int:
                cell = data[k]["cells"][c]
                if not cell:
                    return 0          # colonne vide : besoin evident
                if all(src_row != k for src_row, _ in cell):
                    return 1          # deja wrappee : continuation
                return 2              # complete sur sa propre ligne
            target = min(neighbors, key=score)
            data[target]["cells"][c].append((j, t))

    # ── Passe 4 : assembler ───────────────────────────────────────────
    records: List[dict] = []
    for i in sorted(data):
        d = data[i]
        texts = {}
        for c in (0, 1, 2):
            frags = sorted(d["cells"][c], key=lambda it: (it[0], it[1]["x0"]))
            texts[c] = re.sub(r"\s+", " ",
                              " ".join(t["text"] for _, t in frags)).strip()
        fund = texts[2]
        if not fund or not FUND_NAME_RE.search(fund):
            # nom range dans une autre colonne (rare)
            for c in (1, 0):
                if texts[c] and FUND_NAME_RE.search(texts[c]):
                    fund, texts[c] = texts[c], ""
                    break
        if not fund:
            continue
        records.append(dict(
            fund_name=_norm_fund_name(fund),
            manager=texts[0] or None,
            custodian=texts[1] or None,
            category=d["category"],
            valuation_frequency=d["frequency"],
            bulletin_date=bulletin_date,
            initial_value=d["initial_value"],
            inception_date=d["inception_date"],
            prev_nav=d["prev_nav"], prev_nav_date=d["prev_nav_date"],
            nav=d["nav"], nav_date=d["nav_date"],
            var_inception_pct=d["var_inception_pct"],
            var_prev_pct=d["var_prev_pct"],
        ))

    # ── Dedoublonnage intra-bulletin : PREMIERE occurrence ───────────
    # Les bulletins repetent certains fonds dans plusieurs blocs de
    # frequence (ex. un fonds hebdomadaire reapparait dans les blocs
    # Mensuelles et Trimestrielles avec la MEME VL actuelle mais des
    # VL precedentes / variations calculees sur ces horizons-la).
    # La premiere occurrence = bloc natif du fonds : c'est elle qui
    # porte sa vraie frequence de valorisation et sa variation
    # periodique ; les vues multi-horizons sont recalculables depuis
    # l'historique et ne sont donc pas stockees.
    dedup: Dict[str, dict] = {}
    for r in records:
        dedup.setdefault(r["fund_name"], r)
    return list(dedup.values())



# ======================================================================
#  5ter. PARSEUR - FICHES SIGNALETIQUES (fondamentaux par exercice)
# ======================================================================
#  Chaque bulletin recent contient une FICHE par societe avec un tableau
#  "INDICATEURS D'ACTIVITE AU 31 DECEMBRE" sur 4 exercices : Total Bilan,
#  Capitaux Propres, Chiffre d'Affaires (ou Produit Net Bancaire pour les
#  banques), Valeur Ajoutee, Resultat Net, Dividende, Taux de rendement
#  brut, parfois Rendement du capital (ROE publie).
#  Pieges geres : association via "Acronyme ... (Mnemo) : XXX" ; unite
#  "En million de FCFA" (BGFI HC) -> agregats x 1e6, dividende UNITAIRE
#  en FCFA pleins ; valeurs non numeriques (//, R A S, Non distribue, -) ;
#  annees annotees "2022(*)" ; donnees retraitees entre bulletins (on
#  garde le bulletin le plus recent, ecart trace en INFO).
# ======================================================================

_TICKER_SET = {c["ticker"] for c in COMPANIES.values()}
_TICKER_TO_ISIN = {c["ticker"]: isin for isin, c in COMPANIES.items()}

_FIN_LABELS = [
    ("taux_rendement_brut_pct", ["TAUXDERENDEMENTBRUT"],            False),
    ("roe_publie_pct",          ["RENDEMENTDUCAPITAL"],             False),
    ("dividende_unitaire",      ["DIVIDENDEBRUTUNITAIRE",
                                 "DIVIDENDENET/PARACTION",
                                 "DIVIDENDEBRUT/PARACTION",
                                 "DIVIDENDENET/PAR"],               False),
    ("dividende_total",         ["DIVIDENDEBRUTVERSE",
                                 "DIVIDENDEVERSE"],                 True),
    ("total_bilan",             ["TOTALBILAN"],                     True),
    ("capitaux_propres",        ["CAPITAUXPROPRES"],                True),
    ("chiffre_affaires",        ["CHIFFRED'AFFAIRES","CHIFFREDAFFAIRES",
                                 "PRODUITNETBANCAIRE"],             True),
    ("valeur_ajoutee",          ["VALEURAJOUTEE"],                  True),
    ("resultat_net",            ["RESULTATNET"],                    True),
]

def _fin_norm(s):
    s = s.upper().replace(chr(0x2019), "'").replace(chr(0xA0), " ")
    for a, b in [("É","E"),("È","E"),("Ê","E"),("À","A"),("Û","U"),("Î","I")]:
        s = s.replace(a, b)
    return re.sub(r"\s+", "", s)


def parse_financials(word_rows, bulletin_date):
    records = []
    ticker = None
    years = []
    en_millions = False
    courant = {}

    def flush():
        nonlocal courant
        isin = _TICKER_TO_ISIN.get(ticker)
        if isin:
            for y, champs in courant.items():
                if any(v is not None for v in champs.values()):
                    records.append(dict(isin=isin, fiscal_year=y,
                                        bulletin_date=bulletin_date, **champs))
        courant = {}

    for words in word_rows:
        toks = merge_row(words, MERGE_GAP)
        if not toks:
            continue
        ligne_n = _fin_norm(" ".join(toks))

        if "MNEMO" in ligne_n and "ACRONYME" in ligne_n:
            flush()
            m = re.search(r"MNEMO\)?:?([A-Z]+)$", ligne_n)
            cand = m.group(1) if m else None
            ticker = cand if cand in _TICKER_SET else None
            years, en_millions = [], False
            continue
        if ticker is None:
            continue

        if ligne_n.startswith("(EN") and "CFA" in ligne_n:
            yrs = [int(y) for y in re.findall(r"20\d{2}", " ".join(toks[1:]))]
            if yrs:
                years = yrs
                en_millions = "MILLION" in ligne_n
                for y in years:
                    courant.setdefault(y, {k: None for k, _, _ in _FIN_LABELS})
            continue
        if not years:
            continue

        for cle, frags, agrege in _FIN_LABELS:
            if any(ligne_n.startswith(fr) or ligne_n.startswith("*"+fr)
                   for fr in frags):
                vals = toks[1:][-len(years):] if len(toks) > 1 else []
                offset = len(years) - len(vals)
                for j, v in enumerate(vals):
                    y = years[offset + j]
                    n = norm(v).rstrip("%").replace("(*)", "")
                    num = to_num(n) if re.search(r"\d", n) else None
                    if num is not None and agrege and en_millions:
                        num *= 1_000_000
                    courant.setdefault(y, {k: None for k, _, _ in _FIN_LABELS})
                    if courant[y][cle] is None:
                        courant[y][cle] = num
                break

    flush()
    return records


# ======================================================================
#  6. EXTRACTION D'UN BULLETIN
# ======================================================================

def extract_bulletin(pdf_path) -> dict:
    """Extrait toutes les donnees d'un bulletin PDF."""
    pdf_path = Path(pdf_path)
    b_date = filename_to_date(pdf_path)
    if b_date is None:
        raise ValueError(f"Date de seance illisible dans le nom : {pdf_path.name}")

    word_rows  = read_pdf_word_rows(pdf_path)
    rows       = [merge_row(w, MERGE_GAP) for w in word_rows]
    fmt        = detect_format(rows)

    if fmt == "new":
        # Tables de cotation 2023+ : interligne serre -> seuil 2.5 pt
        rows_tight = [merge_row(w, MERGE_GAP_TIGHT) for w in word_rows]
        prices = parse_new_actions(rows_tight, b_date)
        caps   = parse_new_capitalization(rows, b_date)
    else:
        prices = parse_old_actions(rows, b_date)
        caps   = parse_old_capitalization(rows, b_date)

    idx_value, var_day = parse_index(rows)
    opcvm = parse_opcvm(word_rows, b_date)
    financials = parse_financials(word_rows, b_date) if fmt == "new" else []

    return dict(file=pdf_path.name, format=fmt, bulletin_date=b_date,
                prices=prices, caps=caps, opcvm=opcvm, financials=financials,
                index_value=idx_value, index_var_day=var_day)


# ══════════════════════════════════════════════════════════════════════
#  7. SCHEMA EN ETOILE
# ══════════════════════════════════════════════════════════════════════

def date_id(d) -> Optional[int]:
    if d is None or (isinstance(d, float) and pd.isna(d)):
        return None
    return int(pd.Timestamp(d).strftime("%Y%m%d"))


MONTHS_FR = ["janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet",
             "aout", "septembre", "octobre", "novembre", "decembre"]
DAYS_FR   = ["lundi", "mardi", "mercredi", "jeudi", "vendredi",
             "samedi", "dimanche"]


def build_star_schema(bulletins: List[dict]) -> Dict[str, pd.DataFrame]:
    """Bulletins extraits -> tables dimensionnelles et de faits avec cles."""

    # ── dim_company ───────────────────────────────────────────────────
    dim_company = pd.DataFrame([
        dict(company_id=c["company_id"], isin=isin, ticker=c["ticker"],
             short_name=c["short_name"], full_name=c["full_name"],
             country=c["country"], sector=c["sector"], currency="XAF",
             listing_date=c["listing_date"])
        for isin, c in COMPANIES.items()
    ]).sort_values("company_id").reset_index(drop=True)
    cid = {isin: c["company_id"] for isin, c in COMPANIES.items()}

    # ── fact_prices ───────────────────────────────────────────────────
    price_rows = []
    for b in bulletins:
        for r in b["prices"]:
            price_rows.append(dict(
                company_id=cid[r["isin"]],
                bulletin_date_id=date_id(r["bulletin_date"]),
                session_date_id=date_id(r.get("session_date")),
                status=r.get("status"),
                prev_price=r.get("prev_price"),
                open_price=r.get("open_price"),
                close_price=r.get("close_price"),
                upper_limit=r.get("upper_limit"),
                lower_limit=r.get("lower_limit"),
                vol_bid=r.get("vol_bid"), vol_ask=r.get("vol_ask"),
                vol_traded=r.get("vol_traded"),
                value_traded=r.get("value_traded"),
                num_transactions=r.get("num_transactions"),
                variation_pct=r.get("variation_pct"),
                next_ref_price=r.get("next_ref_price"),
                ytd_high=r.get("ytd_high"), ytd_low=r.get("ytd_low"),
                yoy_variation_pct=r.get("yoy_variation_pct"),
                last_div_amount=r.get("last_div_amount"),
                last_div_date_id=date_id(r.get("last_div_date")),
            ))
    fact_prices = pd.DataFrame(price_rows)
    if not fact_prices.empty:
        fact_prices = (fact_prices
                       .sort_values(["bulletin_date_id", "company_id"])
                       .drop_duplicates(["company_id", "bulletin_date_id"],
                                        keep="last")
                       .reset_index(drop=True))
        fact_prices["daily_return_pct"] = (
            (fact_prices["close_price"] - fact_prices["prev_price"])
            / fact_prices["prev_price"] * 100
        ).round(4)
        fact_prices.insert(0, "price_id", range(1, len(fact_prices) + 1))

    # ── fact_market_cap ───────────────────────────────────────────────
    cap_rows = []
    for b in bulletins:
        for r in b["caps"]:
            cap_rows.append(dict(
                company_id=cid[r["isin"]],
                date_id=date_id(r["bulletin_date"]),
                close_price=r.get("close_price"),
                float_shares=r.get("float_shares"),
                total_shares=r.get("total_shares"),
                float_market_cap=r.get("float_market_cap"),
                total_market_cap=r.get("total_market_cap"),
                last_div_amount=r.get("last_div_amount"),
                last_div_year=r.get("last_div_year"),
                last_div_date_id=date_id(r.get("last_div_date")),
                liquidity_pct=r.get("liquidity_pct"),
                eps=r.get("eps"), per=r.get("per"),
            ))
    fact_cap = pd.DataFrame(cap_rows)
    if not fact_cap.empty:
        fact_cap = (fact_cap.sort_values(["date_id", "company_id"])
                    .drop_duplicates(["company_id", "date_id"], keep="last")
                    .reset_index(drop=True))
        fact_cap.insert(0, "cap_id", range(1, len(fact_cap) + 1))

    # ── fact_index ────────────────────────────────────────────────────
    idx_rows = [dict(date_id=date_id(b["bulletin_date"]),
                     index_name="BVMAC-AS",
                     index_value=b["index_value"],
                     variation_day_pct=b["index_var_day"])
                for b in bulletins if b["index_value"] is not None]
    fact_index = pd.DataFrame(idx_rows)
    if not fact_index.empty:
        fact_index = (fact_index.sort_values("date_id")
                      .drop_duplicates("date_id", keep="last")
                      .reset_index(drop=True))
        fact_index.insert(0, "index_id", range(1, len(fact_index) + 1))

    # ── dim_opcvm + fact_opcvm_nav ────────────────────────────────────
    # fund_id : ordre alphabetique du nom normalise (reproductible) ;
    # attributs du fonds = derniere valeur publiee (bulletin le plus recent)
    fund_names = sorted({r["fund_name"] for b in bulletins
                         for r in b.get("opcvm", [])})
    fid = {name: k + 1 for k, name in enumerate(fund_names)}

    latest: Dict[str, dict] = {}
    for b in sorted(bulletins, key=lambda b: b["bulletin_date"]):
        for r in b.get("opcvm", []):
            latest[r["fund_name"]] = r
    dim_opcvm = pd.DataFrame([
        dict(fund_id=fid[name], fund_name=name,
             manager=latest[name]["manager"],
             custodian=latest[name]["custodian"],
             category=latest[name]["category"],
             valuation_frequency=latest[name]["valuation_frequency"],
             initial_value=latest[name]["initial_value"],
             inception_date_id=date_id(latest[name]["inception_date"]),
             currency="XAF")
        for name in fund_names
    ])

    nav_rows = []
    for b in bulletins:
        for r in b.get("opcvm", []):
            nav_rows.append(dict(
                fund_id=fid[r["fund_name"]],
                bulletin_date_id=date_id(r["bulletin_date"]),
                nav=r["nav"],
                nav_date_id=date_id(r["nav_date"]),
                prev_nav=r["prev_nav"],
                prev_nav_date_id=date_id(r["prev_nav_date"]),
                var_prev_pct=r["var_prev_pct"],
                var_inception_pct=r["var_inception_pct"],
            ))
    fact_nav = pd.DataFrame(nav_rows)
    if not fact_nav.empty:
        fact_nav = (fact_nav.sort_values(["bulletin_date_id", "fund_id"])
                    .drop_duplicates(["fund_id", "bulletin_date_id"],
                                     keep="last")
                    .reset_index(drop=True))
        fact_nav.insert(0, "nav_id", range(1, len(fact_nav) + 1))

    # ── dim_date (toutes les dates referencees) ──────────────────────
    ids = set()
    for df, cols in [(fact_prices, ["bulletin_date_id", "session_date_id",
                                    "last_div_date_id"]),
                     (fact_cap,   ["date_id", "last_div_date_id"]),
                     (fact_index, ["date_id"]),
                     (fact_nav,   ["bulletin_date_id", "nav_date_id",
                                   "prev_nav_date_id"]),
                     (dim_opcvm,  ["inception_date_id"])]:
        if not df.empty:
            for c in cols:
                if c in df.columns:
                    ids.update(int(v) for v in df[c].dropna())
    dim_date = pd.DataFrame([
        dict(date_id=i,
             date=datetime.strptime(str(i), "%Y%m%d").date(),
             year=int(str(i)[:4]), month=int(str(i)[4:6]),
             day=int(str(i)[6:8]),
             quarter=(int(str(i)[4:6]) - 1) // 3 + 1,
             month_name=MONTHS_FR[int(str(i)[4:6]) - 1],
             weekday=datetime.strptime(str(i), "%Y%m%d").weekday() + 1,
             weekday_name=DAYS_FR[datetime.strptime(str(i), "%Y%m%d").weekday()])
        for i in sorted(ids)
    ])


    # ── fact_financials : fondamentaux par societe x exercice ────────
    # Les bulletins republient les memes exercices ; en cas d'ecart
    # (donnees retraitees), on garde le bulletin LE PLUS RECENT et on
    # trace l'ecart pour le rapport qualite.
    fin_rows: Dict[tuple, dict] = {}
    restatements: List[dict] = []
    for b in sorted(bulletins, key=lambda b: b["bulletin_date"]):
        for r in b.get("financials", []):
            key = (cid[r["isin"]], r["fiscal_year"])
            nouveau = dict(
                company_id=key[0], fiscal_year=key[1],
                total_bilan=r.get("total_bilan"),
                capitaux_propres=r.get("capitaux_propres"),
                chiffre_affaires=r.get("chiffre_affaires"),
                valeur_ajoutee=r.get("valeur_ajoutee"),
                resultat_net=r.get("resultat_net"),
                dividende_unitaire=r.get("dividende_unitaire"),
                dividende_total=r.get("dividende_total"),
                taux_rendement_brut_pct=r.get("taux_rendement_brut_pct"),
                roe_publie_pct=r.get("roe_publie_pct"),
                source_bulletin_date_id=date_id(r["bulletin_date"]),
            )
            ancien = fin_rows.get(key)
            if ancien:
                for ch in ("resultat_net", "capitaux_propres",
                           "chiffre_affaires", "total_bilan"):
                    a, n = ancien.get(ch), nouveau.get(ch)
                    if a and n and abs(n / a - 1) > 0.01:
                        restatements.append(dict(
                            severity="INFO", ticker=None, date_id=key[0],
                            check="donnees_retraitees",
                            detail=f"company_id={key[0]} exercice {key[1]} "
                                   f"{ch}: {a:,.0f} -> {n:,.0f} "
                                   f"(bulletin plus recent retenu)"))
                # completer les champs manquants du nouveau par l'ancien
                for ch, v in ancien.items():
                    if nouveau.get(ch) is None:
                        nouveau[ch] = v
            fin_rows[key] = nouveau
    fact_fin = pd.DataFrame(sorted(fin_rows.values(),
                                   key=lambda r: (r["company_id"], r["fiscal_year"])))
    if not fact_fin.empty:
        fact_fin.insert(0, "fin_id", range(1, len(fact_fin) + 1))

    return dict(dim_company=dim_company, dim_date=dim_date,
                dim_opcvm=dim_opcvm,
                fact_prices=fact_prices, fact_market_cap=fact_cap,
                fact_index=fact_index, fact_opcvm_nav=fact_nav,
                fact_financials=fact_fin,
                _restatements=restatements)


# ══════════════════════════════════════════════════════════════════════
#  7bis. CORRECTION DES OUTLIERS  (tolerance maximale : 100 %)
# ══════════════════════════════════════════════════════════════════════

def corriger_outliers(fact_prices: pd.DataFrame,
                      tickers: Dict[int, str]) -> Tuple[pd.DataFrame, List[dict]]:
    """
    Correction robuste des valeurs aberrantes de cours, PAR SOCIETE et dans
    l'ordre chronologique des bulletins.

    Probleme traite : certaines seances ont un cours (Open / Close /
    reference) corrompu au parsing du PDF — une valeur a 2-3 chiffres
    surgit dans une serie a 4-5 chiffres (ex. 360 ou 32 au milieu de
    ~22 000), ou l'inverse. Ces points creent de faux pics « piquants »
    sur les graphes et de fausses ruptures de continuite.

    Methode : pour chaque colonne de prix, on calcule une MEDIANE MOBILE
    sur une fenetre des dernieres valeurs PLAUSIBLES (jusqu'a 5 seances
    connues — robuste meme avec des trous de collecte). Tout point qui
    s'ecarte de plus de 100 % de cette reference est considere corrompu
    et RECONSTRUIT :
      - si une valeur fiable existe avant (close connu X) et apres
        (prochain point plausible Z), reconstruction par interpolation
        lineaire dans le temps (ce qui, pour un trou simple, revient a
        relier le close connu a la reprise suivante) ;
      - sinon, report de la derniere valeur fiable connue.

    Apres correction, Open/Close incoherents d'une meme seance sont
    realignes, prev_price(t) est resynchronise sur close(t-1), et
    daily_return_pct est recalcule. Chaque correction est tracee (INFO).
    """
    if fact_prices.empty:
        return fact_prices, []
    df = fact_prices.copy()
    # Les reconstructions peuvent produire des valeurs fractionnaires :
    # on s'assure que les colonnes de prix acceptent les float.
    for c in ("close_price", "open_price", "prev_price"):
        if c in df.columns:
            df[c] = df[c].astype("float64")
    corrections: List[dict] = []
    FACTEUR = 2.0      # aberrant si > x2 ou < /2 de la reference (~ecart 100 %)
    FEN = 5            # nombre de seances connues prises pour la mediane

    def log(check, cid, did, detail):
        corrections.append(dict(severity="INFO", ticker=tickers.get(cid),
                                date_id=int(did), check=check, detail=detail))

    def mediane(vals: List[float]) -> Optional[float]:
        v = sorted(vals)
        n = len(v)
        if n == 0:
            return None
        return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2

    def hors_tranche(v: float, ref: float) -> bool:
        """True si v s'ecarte de plus de 100 % de ref, dans un sens
        comme dans l'autre (v > 2*ref  OU  v < ref/2)."""
        if ref is None or ref <= 0 or v is None:
            return False
        return v > ref * FACTEUR or v < ref / FACTEUR

    for cid, grp in df.groupby("company_id"):
        idx = grp.sort_values("bulletin_date_id").index.tolist()
        if len(idx) < 3:
            continue

        # Pivot de reference par societe : on l'estime de facon ROBUSTE en
        # ecartant d'abord les valeurs extremes. La mediane brute suffit
        # rarement quand les corrompus sont nombreux ; on prend donc la
        # mediane des valeurs proches de cette mediane (coeur de la serie).
        base_vals = [df.at[i, "close_price"] for i in idx
                     if pd.notna(df.at[i, "close_price"]) and df.at[i, "close_price"] > 0]
        m0 = mediane(base_vals)
        if m0 is None or m0 <= 0:
            continue
        coeur = [v for v in base_vals if m0 / FACTEUR <= v <= m0 * FACTEUR]
        pivot = mediane(coeur) or m0

        # ── Reconstruction colonne par colonne ───────────────────────
        for col in ("close_price", "open_price", "prev_price"):
            if col not in df.columns:
                continue
            fiables: List[float] = []     # fenetre glissante de valeurs plausibles

            for pos, i in enumerate(idx):
                v = df.at[i, col]
                if pd.isna(v):
                    continue

                ref = mediane(fiables[-FEN:]) if fiables else pivot
                # aberrant si hors tranche vs reference recente ET vs pivot
                # societe (evite de corriger une vraie tendance longue)
                aberrant = (v <= 0) or (
                    hors_tranche(v, ref) and hors_tranche(v, pivot))

                if not aberrant:
                    fiables.append(v)
                    continue

                # Prochaine valeur plausible (proche du pivot)
                suiv = None
                for j in idx[pos + 1:]:
                    for c2 in (col, "close_price", "prev_price", "open_price"):
                        w = df.at[j, c2]
                        if pd.notna(w) and w > 0 and not hors_tranche(w, pivot):
                            suiv = w
                            break
                    if suiv is not None:
                        break
                prec = fiables[-1] if fiables else None

                if prec is not None and suiv is not None:
                    recon = (prec + suiv) / 2
                elif prec is not None:
                    recon = prec
                elif suiv is not None:
                    recon = suiv
                else:
                    recon = pivot

                if abs(v - recon) > 1e-9:
                    log("correction_valeur_aberrante", cid,
                        df.at[i, "bulletin_date_id"],
                        f"{col} {v:,.0f} -> {recon:,.0f} "
                        f"(hors tranche, reference ~{(ref or pivot):,.0f})")
                    df.at[i, col] = recon
                fiables.append(df.at[i, col])

        # ── Realignement Open/Close d'une meme seance ────────────────
        for i in idx:
            o, c = df.at[i, "open_price"], df.at[i, "close_price"]
            if (pd.notna(o) and pd.notna(c) and o > 0 and c > 0
                    and hors_tranche(o, c)):
                log("correction_open_close", cid, df.at[i, "bulletin_date_id"],
                    f"Open {o:,.0f} realigne sur Close {c:,.0f}")
                df.at[i, "open_price"] = c

        # ── Resynchronisation prev_price(t) = close(t-1) ─────────────
        for k in range(1, len(idx)):
            i_prev, i_cur = idx[k - 1], idx[k]
            pc = df.at[i_prev, "close_price"]
            pv = df.at[i_cur, "prev_price"]
            if (pd.notna(pc) and pd.notna(pv) and pc > 0
                    and hors_tranche(pv, pc)):
                log("correction_reference", cid, df.at[i_cur, "bulletin_date_id"],
                    f"reference {pv:,.0f} resynchronisee sur close precedent {pc:,.0f}")
                df.at[i_cur, "prev_price"] = pc

    # Recalcul du rendement quotidien apres corrections
    if "prev_price" in df.columns:
        df["daily_return_pct"] = ((df["close_price"] - df["prev_price"])
                                  / df["prev_price"] * 100).round(4)
    return df, corrections


# ══════════════════════════════════════════════════════════════════════
#  8. CONTROLES QUALITE
# ══════════════════════════════════════════════════════════════════════

def validate(star: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Integrite referentielle + coherence temporelle par action.
    Retourne le rapport d'anomalies (ERROR / WARNING / INFO)."""
    rep: List[dict] = []
    tick = star["dim_company"].set_index("company_id")["ticker"].to_dict()

    def add(sev, cid_, did, check, detail):
        rep.append(dict(severity=sev, ticker=tick.get(cid_), date_id=did,
                        check=check, detail=detail))

    # ── Integrite referentielle ───────────────────────────────────────
    valid_cid = set(star["dim_company"]["company_id"])
    valid_did = set(star["dim_date"]["date_id"])
    dim_funds = star.get("dim_opcvm", pd.DataFrame())
    valid_fid = set(dim_funds["fund_id"]) if "fund_id" in dim_funds.columns else set()
    fn0 = star.get("fact_opcvm_nav", pd.DataFrame())
    if not fn0.empty:
        bad = fn0[~fn0["fund_id"].isin(valid_fid)]
        if len(bad):
            rep.append(dict(severity="ERROR", ticker=None, date_id=None,
                            check="fk_orpheline",
                            detail=f"fact_opcvm_nav.fund_id : {len(bad)}"))
    for tname, fk, ref in [("fact_prices", "company_id", valid_cid),
                           ("fact_prices", "bulletin_date_id", valid_did),
                           ("fact_market_cap", "company_id", valid_cid),
                           ("fact_market_cap", "date_id", valid_did),
                           ("fact_index", "date_id", valid_did),
                           ("fact_opcvm_nav", "bulletin_date_id", valid_did)]:
        df = star[tname]
        if df.empty or fk not in df.columns:
            continue
        bad = df[df[fk].notna() & ~df[fk].isin(ref)]
        if len(bad):
            add("ERROR", None, None, "fk_orpheline",
                f"{tname}.{fk} : {len(bad)} valeurs sans correspondance")

    # ── Coherence temporelle (fact_prices) ────────────────────────────
    # Note : corriger_outliers() s'execute AVANT validate(), donc les
    # valeurs aberrantes de cours sont deja reconstruites ici. On ne
    # signale que ce qui reste reellement anormal apres correction.
    fp = star["fact_prices"]
    if not fp.empty:
        for cid_, grp in fp.sort_values("bulletin_date_id").groupby("company_id"):
            prev = None
            for _, r in grp.iterrows():
                did = int(r["bulletin_date_id"])

                # Prix nul/negatif ou hors d'echelle = donnee critique
                for col in ("prev_price", "close_price", "open_price"):
                    v = r[col]
                    if pd.notna(v) and (v <= 0 or v > 5_000_000):
                        add("ERROR", cid_, did, "prix_invalide",
                            f"{col} = {v}")

                # Volume implausible = confusion de colonnes au parsing
                for col in ("vol_bid", "vol_ask", "vol_traded"):
                    v = r[col]
                    if pd.notna(v) and v > 100_000_000:
                        add("ERROR", cid_, did, "volume_aberrant",
                            f"{col} = {v:,.0f} (confusion de colonnes ?)")

                # Cloture hors des seuils reglementaires de la seance
                if (pd.notna(r["close_price"]) and pd.notna(r["upper_limit"])
                        and pd.notna(r["lower_limit"]) and r["lower_limit"] > 0
                        and not (r["lower_limit"] - 1 <= r["close_price"]
                                 <= r["upper_limit"] + 1)):
                    add("ERROR", cid_, did, "cloture_hors_seuils",
                        f"close {r['close_price']:,.0f} hors "
                        f"[{r['lower_limit']:,.0f};{r['upper_limit']:,.0f}]")

                # Variation publiee vs recalculee : on ignore le cas
                # frequent ou le PDF publie 0,00 % par defaut (champ non
                # renseigne) ; on ne signale qu'un vrai desaccord (>2 pts).
                if (pd.notna(r["variation_pct"]) and pd.notna(r["prev_price"])
                        and pd.notna(r["close_price"]) and r["prev_price"] > 0):
                    calc = (r["close_price"] - r["prev_price"]) / r["prev_price"] * 100
                    publiee_nulle = abs(r["variation_pct"]) < 0.005
                    if abs(calc - r["variation_pct"]) > 2.0 and not (
                            publiee_nulle and abs(calc) < 3.0):
                        add("WARNING", cid_, did, "variation_incoherente",
                            f"publiee {r['variation_pct']:.2f}% vs "
                            f"calculee {calc:.2f}%")

                if prev is not None:
                    d1 = datetime.strptime(str(int(prev["bulletin_date_id"])),
                                           "%Y%m%d")
                    d2 = datetime.strptime(str(did), "%Y%m%d")
                    gap = (d2 - d1).days
                    # Rupture de continuite : seulement sur bulletins
                    # CONSECUTIFS (<=4 j) et si l'ecart subsiste apres
                    # correction d'outliers (donnees plausibles des 2 cotes).
                    if (gap <= 4 and pd.notna(r["prev_price"])
                            and pd.notna(prev["close_price"])
                            and prev["close_price"] > 0):
                        ecart = abs(r["prev_price"] / prev["close_price"] - 1)
                        if ecart > 0.02:
                            add("WARNING", cid_, did, "rupture_continuite",
                                f"reference {r['prev_price']:,.0f} != cloture "
                                f"precedente {prev['close_price']:,.0f} "
                                f"({gap} j d'ecart)")
                    # Saut de cours reel sur seances rapprochees
                    if (gap <= 4 and pd.notna(r["close_price"])
                            and pd.notna(prev["close_price"])
                            and prev["close_price"] > 0):
                        jump = abs(r["close_price"] / prev["close_price"] - 1)
                        if jump > 0.50:
                            add("WARNING", cid_, did, "saut_de_cours",
                                f"{jump*100:.0f}% en {gap} j "
                                f"({prev['close_price']:,.0f} -> {r['close_price']:,.0f})")
                prev = r

    # ── Coherence temporelle (fact_market_cap) ────────────────────────
    fc = star["fact_market_cap"]
    if not fc.empty:
        for cid_, grp in fc.sort_values("date_id").groupby("company_id"):
            for _, r in grp.iterrows():
                did = int(r["date_id"])
                ts  = r["total_shares"]
                # cap / nombre de titres doit redonner ~ le cours. Un petit
                # ecart vient d'arrondis ou d'un decalage de mise a jour
                # (non critique) ; on ne signale qu'un ecart majeur (>50%),
                # signe d'une vraie confusion de colonnes au parsing.
                if (pd.notna(r["total_market_cap"]) and pd.notna(ts) and ts > 0
                        and pd.notna(r["close_price"]) and r["close_price"] > 0):
                    implied = r["total_market_cap"] / ts
                    if abs(implied / r["close_price"] - 1) > 0.50:
                        add("WARNING", cid_, did, "cap_incoherente",
                            f"capitalisation/titres = {implied:,.0f} "
                            f"incoherent avec le cours {r['close_price']:,.0f}")

    # ── Coherence des fondamentaux ────────────────────────────────────
    ff = star.get("fact_financials", pd.DataFrame())
    if not ff.empty:
        for _, r in ff.iterrows():
            tk = tick.get(r["company_id"])
            if (pd.notna(r["capitaux_propres"]) and pd.notna(r["total_bilan"])
                    and r["capitaux_propres"] > r["total_bilan"] * 1.001):
                rep.append(dict(severity="WARNING", ticker=tk,
                    date_id=int(r["fiscal_year"]), check="cp_superieurs_bilan",
                    detail=f"Capitaux propres {r['capitaux_propres']:,.0f} > "
                           f"Total bilan {r['total_bilan']:,.0f}"))
            if (pd.notna(r["resultat_net"]) and pd.notna(r["chiffre_affaires"])
                    and r["chiffre_affaires"] > 0
                    and abs(r["resultat_net"] / r["chiffre_affaires"]) > 1.0
                    and r["company_id"] != 8):   # BHC : holding, RN>CA possible
                rep.append(dict(severity="WARNING", ticker=tk,
                    date_id=int(r["fiscal_year"]), check="marge_aberrante",
                    detail=f"|RN/CA| = {abs(r['resultat_net']/r['chiffre_affaires'])*100:.0f}%"))

    # ── Coherence OPCVM ───────────────────────────────────────────────
    fn = star.get("fact_opcvm_nav", pd.DataFrame())
    if not fn.empty:
        fname = star["dim_opcvm"].set_index("fund_id")["fund_name"].to_dict()
        finit = star["dim_opcvm"].set_index("fund_id")["initial_value"].to_dict()

        def addf(sev, fund_id, did, check, detail):
            rep.append(dict(severity=sev, ticker=fname.get(fund_id, "?")[:30],
                            date_id=did, check=check, detail=detail))

        for fund_id, grp in fn.sort_values("bulletin_date_id").groupby("fund_id"):
            prev_nav_seen = None
            for _, r in grp.iterrows():
                did = int(r["bulletin_date_id"])
                if pd.notna(r["nav"]) and r["nav"] <= 0:
                    addf("ERROR", fund_id, did, "vl_non_positive",
                         f"nav = {r['nav']}")
                # variation vs VL precedente : tolerance large (arrondis
                # d'affichage des % dans le bulletin) ; >0.5 pt = vrai ecart
                if (pd.notna(r["var_prev_pct"]) and pd.notna(r["nav"])
                        and pd.notna(r["prev_nav"]) and r["prev_nav"] > 0):
                    calc = (r["nav"] / r["prev_nav"] - 1) * 100
                    if abs(calc - r["var_prev_pct"]) > 0.5:
                        addf("WARNING", fund_id, did, "var_vl_incoherente",
                             f"publiee {r['var_prev_pct']:.2f}% vs "
                             f"calculee {calc:.2f}%")
                # variation depuis l'origine : tolerance 2 pts (arrondi sur
                # la valeur d'origine elle-meme)
                init = finit.get(fund_id)
                if (init and init > 0 and pd.notna(r["nav"])
                        and pd.notna(r["var_inception_pct"])):
                    calc = (r["nav"] / init - 1) * 100
                    if abs(calc - r["var_inception_pct"]) > 2.0:
                        addf("WARNING", fund_id, did, "var_origine_incoherente",
                             f"publiee {r['var_inception_pct']:.2f}% vs "
                             f"calculee {calc:.2f}% (init {init:,.0f})")
                # saut de VL reel entre bulletins (>50%)
                if (prev_nav_seen is not None and pd.notna(r["nav"])
                        and prev_nav_seen > 0):
                    jump = abs(r["nav"] / prev_nav_seen - 1)
                    if jump > 0.50:
                        addf("WARNING", fund_id, did, "saut_de_vl",
                             f"{jump*100:.0f}% entre deux bulletins")
                if pd.notna(r["nav"]):
                    prev_nav_seen = r["nav"]

    df = pd.DataFrame(rep)
    if not df.empty:
        # Le rapport ne conserve que les anomalies ERROR et WARNING
        # (le bruit informatif — trous de collecte, operations sur titres,
        #  retraitements, corrections d'outliers — est exclu).
        df = df[df["severity"].isin(["ERROR", "WARNING"])]
    if not df.empty:
        order = {"ERROR": 0, "WARNING": 1}
        df = df.copy()
        df["_o"] = df["severity"].map(order)
        df = (df.sort_values(["_o", "ticker", "date_id"])
              .drop(columns="_o").reset_index(drop=True))
    return df


# ══════════════════════════════════════════════════════════════════════
#  9. EXPORT - UN SEUL FICHIER EXCEL
# ══════════════════════════════════════════════════════════════════════

def export_master(star: Dict[str, pd.DataFrame], quality: pd.DataFrame,
                  out_path) -> None:
    readme = pd.DataFrame([
        ("dim_company", "company_id", "-",
         "Referentiel des societes cotees (isin = cle naturelle unique)"),
        ("dim_date", "date_id (YYYYMMDD)", "-",
         "Dimension calendrier"),
        ("fact_prices", "price_id",
         "company_id -> dim_company ; bulletin_date_id, session_date_id, "
         "last_div_date_id -> dim_date",
         "Cotations par seance (cours, volumes, valeur, variations)"),
        ("fact_market_cap", "cap_id",
         "company_id -> dim_company ; date_id, last_div_date_id -> dim_date",
         "Capitalisation : titres, flottant, dividendes, market cap"),
        ("fact_index", "index_id", "date_id -> dim_date",
         "Indice BVMAC-AS (publie depuis 2024)"),
        ("dim_opcvm", "fund_id (ordre alphabetique du nom)",
         "inception_date_id -> dim_date",
         "Referentiel des OPCVM : gestionnaire, depositaire, categorie "
         "(D=diversifie, O=obligataire, M=monetaire, A=actions), frequence"),
        ("fact_opcvm_nav", "nav_id",
         "fund_id -> dim_opcvm ; bulletin_date_id, nav_date_id, "
         "prev_nav_date_id -> dim_date",
         "Valeurs liquidatives des OPCVM ; UNE ligne par fonds et par bulletin = bloc de frequence natif (les bulletins repetent les fonds en vues mensuelles/trimestrielles, non stockees). Section publiee depuis courant 2023."),
        ("fact_financials", "fin_id",
         "company_id -> dim_company ; source_bulletin_date_id -> dim_date",
         "Fondamentaux par exercice extraits des fiches signaletiques des "
         "bulletins : Total Bilan, Capitaux Propres, Chiffre d'Affaires "
         "(ou Produit Net Bancaire), Valeur Ajoutee, Resultat Net, "
         "dividendes, taux de rendement brut, ROE publie. Montants en FCFA "
         "(unites 'millions' converties). Ratios Marge nette / ROE / ROA / "
         "Autonomie / Croissances calcules par la plateforme."),
        ("quality_report", "-", "ticker, date_id",
         "Controles de coherence : ERROR / WARNING / INFO"),
    ], columns=["table", "cle_primaire", "cles_etrangeres", "description"])

    out_path = Path(out_path)
    with pd.ExcelWriter(str(out_path), engine="openpyxl") as writer:
        readme.to_excel(writer, sheet_name="README", index=False)
        for name in ("dim_company", "dim_date", "dim_opcvm",
                     "fact_prices", "fact_market_cap", "fact_index",
                     "fact_opcvm_nav", "fact_financials"):
            df = star[name]
            if df.empty:
                df = pd.DataFrame(columns=["(vide)"])
            df.to_excel(writer, sheet_name=name, index=False)
            print(f"  Feuille {name:<16s} : {len(star[name]):>5d} lignes")
        q = quality if not quality.empty else pd.DataFrame(
            [dict(severity="OK", detail="Aucune anomalie detectee")])
        q.to_excel(writer, sheet_name="quality_report", index=False)
        print(f"  Feuille {'quality_report':<16s} : {len(q):>5d} lignes")

        for ws in writer.book.worksheets:
            for col in ws.columns:
                width = max((len(str(c.value)) for c in col
                             if c.value is not None), default=8)
                ws.column_dimensions[col[0].column_letter].width = \
                    min(width + 2, 55)

    print(f"\n  Classeur maitre : {out_path.resolve()}")


# ══════════════════════════════════════════════════════════════════════
#  10. SELF-TESTS  (valeurs de reference verifiees sur les PDF sources)
# ══════════════════════════════════════════════════════════════════════

def run_selftests(star: Dict[str, pd.DataFrame]) -> bool:
    """Assertions contre des valeurs lues manuellement dans les PDF."""
    fp = star["fact_prices"]
    fc = star["fact_market_cap"]
    cid = {c["ticker"]: c["company_id"] for c in COMPANIES.values()}

    def price(ticker, did):
        m = fp[(fp.company_id == cid[ticker]) & (fp.bulletin_date_id == did)]
        return m.iloc[0] if len(m) else None

    def cap(ticker, did):
        m = fc[(fc.company_id == cid[ticker]) & (fc.date_id == did)]
        return m.iloc[0] if len(m) else None

    tests, ok = [], True

    def chk(label, cond):
        nonlocal ok
        tests.append((label, bool(cond)))
        if not cond:
            ok = False

    # ── 2026-05-29 : SOCAPALM (nouveau format, seance active) ─────────
    r = price("SOCAP", 20260529)
    chk("2026 SOCAP present", r is not None)
    if r is not None:
        chk("2026 SOCAP prev=50000",   r.prev_price == 50000)
        chk("2026 SOCAP close=55000",  r.close_price == 55000)
        chk("2026 SOCAP bid=182",      r.vol_bid == 182)
        chk("2026 SOCAP ask=1832",     r.vol_ask == 1832)
        chk("2026 SOCAP traded=182",   r.vol_traded == 182)
        chk("2026 SOCAP valeur=10010000", r.value_traded == 10_010_000)
        chk("2026 SOCAP ntrans=3",     r.num_transactions == 3)
        chk("2026 SOCAP var=10%",      abs(r.variation_pct - 10.0) < 0.01)
        chk("2026 SOCAP yoy=22.22",    abs(r.yoy_variation_pct - 22.22) < 0.01)

    # ── 2025-05-30 : SEMC ──────────────────────────────────────────────
    r = price("SEMC", 20250530)
    chk("2025 SEMC present", r is not None)
    if r is not None:
        chk("2025 SEMC prev=46000",  r.prev_price == 46000)
        chk("2025 SEMC close=46500", r.close_price == 46500)
        chk("2025 SEMC traded=400",  r.vol_traded == 400)
        chk("2025 SEMC valeur=18600000", r.value_traded == 18_600_000)

    # ── 2019-12-02 : SAFACAM (ancien format) ──────────────────────────
    r = price("SAF", 20191202)
    chk("2019 SAF present", r is not None)
    if r is not None:
        chk("2019 SAF close=21996", r.close_price == 21996)
        chk("2019 SAF bid=0",       r.vol_bid == 0)
        chk("2019 SAF ask=2945",    r.vol_ask == 2945)
        chk("2019 SAF traded=0",    r.vol_traded == 0)
        chk("2019 SAF div=409",     r.last_div_amount == 409)

    # ── 2022-05-25 : cap SEMC (cours vs flottant non inverses) ────────
    r = cap("SEMC", 20220525)
    chk("2022 cap SEMC presente", r is not None)
    if r is not None:
        chk("2022 SEMC close=47000",       r.close_price == 47000)
        chk("2022 SEMC flottant=38367",    r.float_shares == 38367)
        chk("2022 SEMC titres=192473",     r.total_shares == 192473)
        chk("2022 SEMC cap=9046231000",    r.total_market_cap == 9_046_231_000)

    # ── 2023/2024 : BANGE et SCGRE (noms longs multi-lignes) ──────────
    chk("2023 BANGE present", price("BANGE", 20230531) is not None)
    chk("2023 SCGRE present", price("SCGRE", 20230531) is not None)
    chk("2024 BANGE present", price("BANGE", 20240531) is not None)

    # ── 2026 : BGFI HC (introduit 05/2026) ────────────────────────────
    chk("2026 BHC present", price("BHC", 20260529) is not None)

    # ── 2026 : REG (nom long fusionne avec l'ISIN dans le PDF) ────────
    r = price("REG", 20260529)
    chk("2026 REG present", r is not None)
    if r is not None:
        chk("2026 REG prev=39500",   r.prev_price == 39500)
        chk("2026 REG close=39500",  r.close_price == 39500)
        chk("2026 REG bid=5",        r.vol_bid == 5)
        chk("2026 REG ask=542",      r.vol_ask == 542)
        chk("2026 REG traded=5",     r.vol_traded == 5)
        chk("2026 REG valeur=197500", r.value_traded == 197_500)
        chk("2026 REG seuil_haut=43450", r.upper_limit == 43450)
        chk("2026 REG seuil_bas=35550",  r.lower_limit == 35550)
        chk("2026 REG yoy=-7.06",    abs(r.yoy_variation_pct - (-7.06)) < 0.01)

    # ── OPCVM ──────────────────────────────────────────────────────────
    do = star["dim_opcvm"]
    fn = star["fact_opcvm_nav"]

    def fund_nav(name, did):
        m = do[do.fund_name == name]
        if not len(m):
            return None, None
        fund = m.iloc[0]
        nv = fn[(fn.fund_id == fund.fund_id) & (fn.bulletin_date_id == did)]
        return fund, (nv.iloc[0] if len(nv) else None)

    chk("OPCVM 2024 >= 30 fonds",
        (fn.bulletin_date_id == 20240531).sum() >= 30)
    chk("OPCVM 2025 >= 32 fonds",
        (fn.bulletin_date_id == 20250530).sum() >= 32)
    chk("OPCVM 2026 >= 40 fonds",
        (fn.bulletin_date_id == 20260529).sum() >= 40)

    f_, v_ = fund_nav("FCP AB AVENIR", 20260529)
    chk("OPCVM AB AVENIR present", v_ is not None)
    if v_ is not None:
        chk("AB AVENIR nav=1269.02",  abs(v_.nav - 1269.02) < 0.01)
        chk("AB AVENIR prev=1268.33", abs(v_.prev_nav - 1268.33) < 0.01)
        chk("AB AVENIR var_orig=26.90",
            abs(v_.var_inception_pct - 26.90) < 0.01)
        chk("AB AVENIR manager", f_.manager == "AFRICA BRIGHT ASSET MANAGEMENT")
        chk("AB AVENIR depositaire", f_.custodian == "BGFIBANK CAMEROUN")
        chk("AB AVENIR cat=D", f_.category == "D")
        chk("AB AVENIR init=1000", f_.initial_value == 1000)

    f_, v_ = fund_nav("FCP ECOBANK MONETAIRE CEMAC", 20260529)
    chk("OPCVM nom wrappe reconstruit (ECOBANK MONETAIRE CEMAC)",
        v_ is not None and abs(v_.nav - 1151.07) < 0.01)

    f_, v_ = fund_nav("FCP CORRIDOR RENDEMENT", 20240531)
    chk("OPCVM 2024 CORRIDOR RENDEMENT nav=9421.75",
        v_ is not None and abs(v_.nav - 9421.75) < 0.01)
    if f_ is not None:
        chk("CORRIDOR init=7723", f_.initial_value == 7723)

    f_, v_ = fund_nav("FCP CONTACTURER OBLIGATAIRE-PARTS A", 20260529)
    chk("OPCVM PARTS A (nom 3 lignes) nav=11262.16",
        v_ is not None and abs(v_.nav - 11262.16) < 0.01)

    f_, v_ = fund_nav("FCPE CONTACTURER EPARGNE SALARIALE", 20260529)
    chk("OPCVM FCPE intact", v_ is not None)

    # Fonds repete dans plusieurs blocs de frequence du meme bulletin :
    # on doit conserver le bloc NATIF (hebdomadaire), pas la vue
    # mensuelle/trimestrielle qui ecraserait variation et frequence.
    f_, v_ = fund_nav("FCP ESS CONFORT", 20260529)
    chk("OPCVM multi-blocs : frequence native conservee",
        f_ is not None and f_.valuation_frequency == "Hebdomadaire")
    chk("OPCVM multi-blocs : variation native conservee",
        v_ is not None and abs(v_.var_prev_pct - 0.10) < 0.01)

    # ── Correction des outliers (regles synthetiques) ──────────────────
    # Serie a ~22 000 avec des valeurs corrompues (centaines/unites) :
    # toutes doivent etre reconstruites dans la tranche.
    serie = [(20250101, 22000), (20250102, 22100), (20250103, 360),
             (20250104, 22050), (20250105, 32), (20250106, 22080),
             (20250107, 22090), (20250108, 5)]
    synth = pd.DataFrame([
        dict(company_id=1, bulletin_date_id=d, prev_price=c,
             open_price=c, close_price=c, upper_limit=None, lower_limit=None,
             variation_pct=None, vol_bid=None, vol_ask=None, vol_traded=None,
             value_traded=None, num_transactions=None)
        for d, c in serie])
    corr_df, corr_log = corriger_outliers(synth, {1: "T1"})
    g1 = corr_df[corr_df.company_id == 1].sort_values("bulletin_date_id")
    closes = g1["close_price"].tolist()
    chk("Valeurs hors-tranche toutes reconstruites (>= 10 000)",
        all(c >= 10_000 for c in closes))
    chk("Corrections tracees pour les 3 valeurs corrompues",
        len(corr_log) >= 3)

    # Pic isole unique : 110, 400, 120 -> le 400 ramene pres des voisins
    pic = pd.DataFrame([
        dict(company_id=2, bulletin_date_id=d, prev_price=c, open_price=c,
             close_price=c, upper_limit=None, lower_limit=None,
             variation_pct=None, vol_bid=None, vol_ask=None, vol_traded=None,
             value_traded=None, num_transactions=None)
        for d, c in [(20250101, 110), (20250102, 400), (20250103, 120)]])
    pic_df, _ = corriger_outliers(pic, {2: "T2"})
    val_pic = pic_df.sort_values("bulletin_date_id").iloc[1]["close_price"]
    chk("Pic isole 400 reconstruit pres des voisins (110-120)",
        100 <= val_pic <= 130)

    # Tendance reelle (hausse progressive) : aucune correction
    tend = pd.DataFrame([
        dict(company_id=3, bulletin_date_id=d, prev_price=c, open_price=c,
             close_price=c, upper_limit=None, lower_limit=None,
             variation_pct=None, vol_bid=None, vol_ask=None, vol_traded=None,
             value_traded=None, num_transactions=None)
        for d, c in [(20250101, 39500), (20250102, 40000), (20250103, 41000),
                     (20250104, 42000), (20250105, 43000)]])
    _, tend_log = corriger_outliers(tend, {3: "T3"})
    chk("Tendance reelle non sur-corrigee (0 correction)", len(tend_log) == 0)

    chk("Donnees reelles : aucun outlier a corriger",
        len(corriger_outliers(fp, {})[1]) == 0)

    # ── Fondamentaux (fiches signaletiques) ─────────────────────────────
    ff = star["fact_financials"]
    chk("Fondamentaux extraits (>= 25 lignes)", len(ff) >= 25)

    def fin(ticker, annee):
        m = ff[(ff.company_id == cid[ticker]) & (ff.fiscal_year == annee)]
        return m.iloc[0] if len(m) else None

    r = fin("SAF", 2024)
    chk("SAFACAM 2024 present", r is not None)
    if r is not None:
        chk("SAF 2024 CA = 29 510 124 866", r.chiffre_affaires == 29_510_124_866)
        chk("SAF 2024 RN = 2 781 417 736",  r.resultat_net == 2_781_417_736)
        chk("SAF 2024 CP = 21 414 462 333", r.capitaux_propres == 21_414_462_333)
        chk("SAF 2024 TB = 32 139 531 395", r.total_bilan == 32_139_531_395)
        chk("SAF 2024 div unitaire = 2000", r.dividende_unitaire == 2000)
        chk("SAF 2024 rendement brut = 7.51", abs(r.taux_rendement_brut_pct - 7.51) < 0.01)

    r = fin("BHC", 2024)
    chk("BHC 2024 : millions convertis (CP = 226 117 000 000)",
        r is not None and r.capitaux_propres == 226_117_000_000)
    chk("BHC 2024 : dividende unitaire NON converti (12 500)",
        r is not None and r.dividende_unitaire == 12_500)

    r = fin("REG", 2024)
    chk("REG 2024 : PNB dans chiffre_affaires (6 024 301 610)",
        r is not None and r.chiffre_affaires == 6_024_301_610)

    r = fin("SCGRE", 2022)
    chk("SCGRE 2022 : ROE publie capte (10.60)",
        r is not None and abs(r.roe_publie_pct - 10.60) < 0.01)
    chk("SCGRE 2022 : retraitement -> bulletin recent retenu "
        "(CP = 12 169 629 122)",
        r is not None and r.capitaux_propres == 12_169_629_122)

    # ── BPA / PER : colonnes presentes dans fact_market_cap ────────────
    chk("Colonnes eps/per presentes",
        "eps" in fc.columns and "per" in fc.columns)
    chk("Colonne liquidity_pct presente", "liquidity_pct" in fc.columns)

    # ── Indice 2026 ────────────────────────────────────────────────────
    fi = star["fact_index"]
    m  = fi[fi.date_id == 20260529]
    chk("2026 indice present", len(m) == 1)
    if len(m):
        chk("2026 indice=1130.81", abs(m.iloc[0].index_value - 1130.81) < 0.01)
        chk("2026 var jour=3.38",  abs(m.iloc[0].variation_day_pct - 3.38) < 0.01)

    # ── Affichage ──────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  SELF-TESTS")
    print("=" * 60)
    for label, passed in tests:
        print(f"  [{'OK  ' if passed else 'FAIL'}] {label}")
    n_ok = sum(1 for _, p in tests if p)
    print(f"\n  {n_ok}/{len(tests)} tests reussis")
    return ok


# ══════════════════════════════════════════════════════════════════════
#  11. PIPELINE PRINCIPAL
# ══════════════════════════════════════════════════════════════════════

def _cache_path(out_path: str) -> Path:
    """Fichier cache (a cote du classeur) memorisant les bulletins deja
    extraits, pour permettre une reprise incrementale fiable."""
    p = Path(out_path)
    return p.with_name(p.stem + ".cache.pkl")


def charger_cache(out_path: str) -> List[dict]:
    import pickle
    cp = _cache_path(out_path)
    if cp.exists():
        try:
            with open(cp, "rb") as f:
                return pickle.load(f)
        except Exception:
            return []
    return []


def sauver_cache(out_path: str, bulletins: List[dict]) -> None:
    import pickle
    try:
        with open(_cache_path(out_path), "wb") as f:
            pickle.dump(bulletins, f)
    except Exception as exc:
        print(f"  (cache non ecrit : {exc})")


def derniere_seance_cache(bulletins: List[dict]) -> Optional[date]:
    # Une séance avec zéro ligne action n'est pas considérée comme extraite :
    # cela permet de réparer automatiquement un changement de mise en page.
    dates = [b["bulletin_date"] for b in bulletins
             if b.get("bulletin_date") and b.get("prices")]
    return max(dates) if dates else None


def main():
    ap = argparse.ArgumentParser(
        description="Extracteur BVMAC BOC -> classeur Excel (schema en etoile)")
    ap.add_argument("--pdf-dir", default="./bulletins",
                    help="Dossier contenant les PDF BOC (defaut ./bulletins)")
    ap.add_argument("--out", default="bvmac_master.xlsx",
                    help="Fichier Excel de sortie (defaut bvmac_master.xlsx)")
    ap.add_argument("--csv", action="store_true",
                    help="Exporter aussi chaque table en CSV (meme dossier)")
    ap.add_argument("--selftest", action="store_true",
                    help="Executer les tests de non-regression apres extraction")
    ap.add_argument("--mode", choices=["all", "update", "ask"], default="ask",
                    help="all = tout re-extraire ; update = seulement les "
                         "bulletins plus recents que le dernier deja extrait ; "
                         "ask = demander au lancement (defaut)")
    args = ap.parse_args()

    pdf_dir = Path(args.pdf_dir)
    pdfs = sorted(p for p in pdf_dir.glob("*.pdf")
                  if "BOC" in p.name.upper())
    if not pdfs:
        sys.exit(f"Aucun PDF BOC trouve dans {pdf_dir.resolve()}")

    # ── Choix du mode : complet ou incremental ───────────────────────
    cache = charger_cache(args.out)
    derniere = derniere_seance_cache(cache)
    mode = args.mode
    if mode == "ask":
        print("=" * 60)
        print("  BVMAC EXTRACTOR v2.0")
        print("=" * 60)
        if derniere and Path(args.out).exists():
            print(f"  Un classeur existe deja (derniere seance extraite : "
                  f"{derniere}).")
            print()
            print("  [1] Tout extraire        (recalcule l'integralite)")
            print("  [2] Continuer / mettre a jour")
            print(f"      (ne traite que les bulletins posterieurs au {derniere})")
            print()
            try:
                rep = input("  Votre choix [1/2] (defaut 2) : ").strip()
            except EOFError:
                rep = ""
            mode = "all" if rep == "1" else "update"
        else:
            print("  Aucun classeur existant : extraction complete.")
            mode = "all"
        print()

    # ── Selection des PDF a extraire ─────────────────────────────────
    if mode == "update" and derniere:
        dates_valides = {b.get("bulletin_date") for b in cache
                         if b.get("prices")}
        limite_reparation = derniere - timedelta(days=120)
        a_traiter = [p for p in pdfs
                     if (filename_to_date(p) or date(1900, 1, 1)) > derniere
                     or (filename_to_date(p) or date(1900, 1, 1)) >= limite_reparation
                     and filename_to_date(p) not in dates_valides]
        anciens = cache
        print(f"  Mode incremental avec reparation des trous : "
              f"{len(a_traiter)} bulletin(s) nouveau(x) ou manquant(s) ; "
              f"{len(anciens)} deja en cache.")
    else:
        a_traiter = pdfs
        anciens = []
        print(f"  Mode complet : {len(a_traiter)} bulletin(s) a extraire.")

    print("=" * 60)

    bulletins, failures = list(anciens), []
    dates_connues = {b["bulletin_date"] for b in anciens}
    for p in a_traiter:
        try:
            b = extract_bulletin(p)
            if b["bulletin_date"] in dates_connues:
                # remplace une eventuelle version deja en cache
                bulletins = [x for x in bulletins
                             if x["bulletin_date"] != b["bulletin_date"]]
            bulletins.append(b)
            dates_connues.add(b["bulletin_date"])
            print(f"  [{b['format'].upper():>3s}] {b['bulletin_date']} "
                  f" actions={len(b['prices'])} cap={len(b['caps'])} "
                  f"opcvm={len(b['opcvm'])} "
                  f"indice={'oui' if b['index_value'] else 'non'}  ({p.name})")
        except Exception as exc:
            failures.append((p.name, str(exc)))
            print(f"  [ERR] {p.name} : {exc}")

    if failures:
        print(f"\n  ATTENTION : {len(failures)} fichier(s) en echec")

    # Tri chronologique stable avant consolidation
    bulletins.sort(key=lambda b: b["bulletin_date"])

    star = build_star_schema(bulletins)
    restatements = star.pop("_restatements", [])

    # Correction des outliers (tolerance 100%) sur fact_prices
    tickers_map = star["dim_company"].set_index("company_id")["ticker"].to_dict()
    star["fact_prices"], corrections = corriger_outliers(
        star["fact_prices"], tickers_map)
    if corrections:
        print(f"  Corrections d'outliers appliquees : {len(corrections)}")
        for c in corrections:
            print(f"    [{c['ticker']} @ {c['date_id']}] {c['check']}: {c['detail']}")

    quality = validate(star)
    # Les corrections d'outliers et retraitements sont des operations
    # INFO : elles sont tracees en console (ci-dessus / ci-dessous) mais
    # le rapport exporte ne conserve que les anomalies ERROR et WARNING.
    restate_err = [r for r in restatements
                   if r.get("severity") in ("ERROR", "WARNING")]
    if restate_err:
        extra = pd.DataFrame(restate_err)
        quality = pd.concat([extra, quality], ignore_index=True)
    if not quality.empty:
        quality = quality[quality["severity"].isin(["ERROR", "WARNING"])]

    n_err  = int((quality["severity"] == "ERROR").sum()) if not quality.empty else 0
    n_warn = int((quality["severity"] == "WARNING").sum()) if not quality.empty else 0
    n_corr = len(corrections)
    print(f"\n  Rapport qualite (ERROR/WARNING) : {n_err} ERROR | {n_warn} WARNING")
    print(f"  Corrections d'outliers appliquees (INFO, hors rapport) : {n_corr}")
    if n_err:
        for _, r in quality[quality.severity == "ERROR"].iterrows():
            print(f"    ERROR [{r.ticker} @ {r.date_id}] {r.check}: {r.detail}")

    print()
    export_master(star, quality, args.out)
    sauver_cache(args.out, bulletins)

    if args.csv:
        out_dir = Path(args.out).parent
        for name, df in star.items():
            if not df.empty:
                fp = out_dir / f"{name}.csv"
                df.to_csv(fp, index=False, encoding="utf-8-sig")
                print(f"  CSV : {fp}")

    if args.selftest:
        if not run_selftests(star):
            sys.exit(1)

    print("\nTermine.")


if __name__ == "__main__":
    main()
