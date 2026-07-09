#!/usr/bin/env python3
"""
Collecte des offres d'emploi accessibles sans qualification (SMIC) pour la zone Bordeaux/Mérignac.

Sources : France Travail + LinkedIn guest API
Sortie  : elya/data/jobs_general.json
"""

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup, NavigableString

BASE_DIR    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_PATH = os.path.join(BASE_DIR, "data", "jobs_general.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9",
    "Accept": "text/html,application/xhtml+xml",
}

FT_BASE   = "https://candidat.francetravail.fr/offres/recherche"
FT_DETAIL = "https://candidat.francetravail.fr/offres/recherche/detail"

CITY = "Bordeaux"
DEPT = "Gironde"

# Postes accessibles au grand public, souvent au SMIC
GENERAL_QUERIES_FT = [
    f"équipier polyvalent {DEPT}",
    f"caissier {DEPT}",
    f"vendeur {DEPT}",
    f"agent de sécurité {DEPT}",
    f"serveur restauration {DEPT}",
    f"livreur {DEPT}",
    f"manutentionnaire {DEPT}",
    f"agent d'entretien {DEPT}",
    f"préparateur de commandes {DEPT}",
    f"hôte de caisse {DEPT}",
    f"employé de rayon {DEPT}",
    f"opérateur de production {DEPT}",
    f"agent logistique {DEPT}",
    f"réceptionniste {DEPT}",
    f"aide soignant {DEPT}",
]

GENERAL_QUERIES_LI = [
    ("equipier polyvalent",         f"{CITY}, Gironde, France"),
    ("caissier vendeur",            f"{CITY}, Gironde, France"),
    ("agent de securite",           f"{CITY}, Gironde, France"),
    ("serveur restauration",        f"{CITY}, Gironde, France"),
    ("livreur preparateur commande",f"{CITY}, Gironde, France"),
    ("agent entretien nettoyage",   f"{CITY}, Gironde, France"),
]

SMIC_KEYWORDS = [
    "SMIC", "sans expérience", "débutant", "profil débutant",
    "sans diplôme", "sans qualification", "accessible",
    "formation assurée", "profil junior",
]

POSITIVE_KEYWORDS = [
    "équipier", "caissier", "vendeur", "serveur", "livreur",
    "manutentionnaire", "entretien", "préparateur", "hôte de caisse",
    "employé", "opérateur", "agent", "réceptionniste", "aide-soignant",
    "logistique", "magasinier", "stockiste", "animateur",
]


def parse_ft_date(text: str) -> str:
    now = datetime.now(timezone.utc)
    t = (text or "").lower().strip()
    if "aujourd" in t:
        return now.date().isoformat()
    if "hier" in t:
        return (now - timedelta(days=1)).date().isoformat()
    m = re.search(r"(\d+)\s+jours?", t)
    if m:
        return (now - timedelta(days=int(m.group(1)))).date().isoformat()
    return ""


def score_general(job) -> int:
    """Score simplifié : fraîcheur + accessibilité + contrat."""
    score = 30  # base
    text = f"{job.get('title','')} {job.get('description','')}".lower()

    # Fraîcheur
    date_str = job.get("date_posted")
    if date_str:
        try:
            posted = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - posted).days
            score += max(0, 30 - age * 2)
        except Exception:
            score += 10

    # Accessibilité
    for kw in SMIC_KEYWORDS:
        if kw.lower() in text:
            score += 8
            break

    # Titre reconnu
    for kw in POSITIVE_KEYWORDS:
        if kw.lower() in job.get("title", "").lower():
            score += 20
            break

    # Contrat
    contract = (job.get("contract_type") or "").upper()
    if contract:
        score += 12

    return min(100, score)


def fetch_france_travail() -> list:
    all_jobs: list = []
    seen_ids: set = set()

    for keywords in GENERAL_QUERIES_FT:
        for page_start in [0, 20]:
            params = {"motsCles": keywords, "tri": "0", "start": page_start}
            url = f"{FT_BASE}?{urlencode(params)}"
            try:
                resp = requests.get(url, headers=HEADERS, timeout=20)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "html.parser")

                items = soup.find_all("li", class_="result")
                if not items:
                    break

                for li in items:
                    job_id = li.get("data-id-offre", "")
                    if not job_id or job_id in seen_ids:
                        continue
                    seen_ids.add(job_id)

                    title_el = li.find("span", class_="media-heading-title")
                    title = title_el.get_text(strip=True) if title_el else ""

                    subtext = li.find("p", class_="subtext")
                    company, location = "", ""
                    if subtext:
                        span = subtext.find("span")
                        location = span.get_text(strip=True) if span else ""
                        nav = next(
                            (c for c in subtext.children
                             if isinstance(c, NavigableString)
                             and c.strip().replace(" ", "").replace("-", "").strip()),
                            None,
                        )
                        company = nav.strip().replace(" ", "").replace("-", "").strip() if nav else ""

                    desc_el = li.find("p", class_="description")
                    desc = desc_el.get_text(strip=True) if desc_el else ""

                    contract_el = li.find("div", class_="media-right")
                    contract = ""
                    if contract_el:
                        p = contract_el.find("p", class_="contrat")
                        if p:
                            contract = p.get_text(" ", strip=True).split(" ")[0]

                    date_el = li.find("p", class_="date")
                    date_text = date_el.get_text(strip=True) if date_el else ""
                    date_posted = parse_ft_date(date_text)

                    all_jobs.append({
                        "id": f"ft_{job_id}",
                        "title": title,
                        "company": company or "Non précisé",
                        "location": location or CITY,
                        "description": desc[:900],
                        "url": f"{FT_DETAIL}/{job_id}",
                        "source": "france_travail",
                        "date_posted": date_posted,
                        "contract_type": contract,
                        "salary": "",
                    })

            except Exception as e:
                print(f"FT ({keywords}, start={page_start}): {e}")
                break

            time.sleep(1)

    print(f"France Travail: {len(all_jobs)} offres")
    return all_jobs


LI_GUEST_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
LI_JOB_URL   = "https://www.linkedin.com/jobs/view"

LI_HEADERS = {**HEADERS}


def fetch_linkedin() -> list:
    all_jobs: list = []
    seen_ids: set = set()

    for keywords, location in GENERAL_QUERIES_LI:
        for start in [0, 25]:
            params = {"keywords": keywords, "location": location, "start": start}
            try:
                resp = requests.get(LI_GUEST_URL, params=params, headers=LI_HEADERS, timeout=20)
                if len(resp.text) < 100:
                    print(f"LinkedIn ({keywords}): rate-limité")
                    break

                soup = BeautifulSoup(resp.text, "html.parser")
                cards = soup.find_all("div", class_="base-search-card")
                if not cards:
                    break

                for card in cards:
                    urn = card.get("data-entity-urn", "")
                    job_id = urn.split(":")[-1] if urn else ""
                    if not job_id or job_id in seen_ids:
                        continue
                    seen_ids.add(job_id)

                    title_el = card.find("h3", class_="base-search-card__title")
                    comp_el  = card.find("h4", class_="base-search-card__subtitle")
                    loc_el   = card.find("span", class_="job-search-card__location")
                    time_el  = card.find("time")
                    link_el  = card.find("a", class_="base-card__full-link")

                    title    = title_el.get_text(strip=True) if title_el else ""
                    company  = comp_el.get_text(strip=True)  if comp_el  else "Non précisé"
                    loc      = loc_el.get_text(strip=True)   if loc_el   else CITY
                    date_posted = time_el.get("datetime", "").strip() if time_el else ""
                    url = link_el.get("href", f"{LI_JOB_URL}/{job_id}") if link_el else f"{LI_JOB_URL}/{job_id}"
                    url = url.split("?")[0] if "?" in url else url

                    all_jobs.append({
                        "id": f"li_{job_id}",
                        "title": title,
                        "company": company,
                        "location": loc,
                        "description": "",
                        "url": url,
                        "source": "linkedin",
                        "date_posted": date_posted,
                        "contract_type": "",
                        "salary": "",
                    })

            except Exception as e:
                print(f"LinkedIn ({keywords}, start={start}): {e}")
                break

            time.sleep(2)

    print(f"LinkedIn: {len(all_jobs)} offres")
    return all_jobs


def deduplicate(jobs):
    seen = set()
    unique = []
    for job in jobs:
        key = f"{(job.get('title') or '').lower()[:40]}|{(job.get('company') or '').lower()[:25]}"
        if key not in seen:
            seen.add(key)
            unique.append(job)
    return unique


def main():
    print(f"Zone : {CITY} / {DEPT} — offres tout venant\n")

    all_jobs = deduplicate(fetch_france_travail() + fetch_linkedin())
    print(f"\n{len(all_jobs)} offres uniques après déduplication")

    for job in all_jobs:
        job["score"] = score_general(job)
        job["score_breakdown"] = {}

    all_jobs.sort(key=lambda x: x["score"], reverse=True)

    sources: dict = {}
    for job in all_jobs:
        s = job.get("source", "unknown")
        sources[s] = sources.get(s, 0) + 1

    output = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "jobs": all_jobs,
        "stats": {"total": len(all_jobs), "sources": sources},
    }

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✓ {len(all_jobs)} offres sauvegardées dans elya/data/jobs_general.json")
    labels = {"france_travail": "France Travail", "linkedin": "LinkedIn"}
    for src, cnt in sorted(sources.items(), key=lambda x: -x[1]):
        print(f"  {labels.get(src, src):20s}: {cnt}")


if __name__ == "__main__":
    main()
