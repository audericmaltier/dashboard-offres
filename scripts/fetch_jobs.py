#!/usr/bin/env python3
"""
Collecte les offres d'emploi depuis des sources PUBLIQUES, sans clé API.

Sources :
  - France Travail  → scraping HTML (rendu côté serveur, pas de JS requis)

Les offres sont scorées par rapport au profil (profile.json) et
sauvegardées dans data/jobs.json pour le dashboard GitHub Pages.
"""

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup, NavigableString

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE_PATH = os.path.join(BASE_DIR, "profile.json")
OUTPUT_PATH = os.path.join(BASE_DIR, "data", "jobs.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9",
    "Accept": "text/html,application/xhtml+xml",
}

FT_BASE = "https://candidat.francetravail.fr/offres/recherche"
FT_DETAIL = "https://candidat.francetravail.fr/offres/recherche/detail"


def load_profile():
    with open(PROFILE_PATH, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_job(job, profile):
    """Retourne (score 0-100, breakdown dict)."""
    breakdown = {}
    total = 0

    title_lower = (job.get("title") or "").lower()
    text = f"{job.get('title', '')} {job.get('description', '')}".lower()

    # Titre — 40 pts
    title_score = 0
    for target in profile.get("target_titles", []):
        words = target.lower().split()
        if all(w in title_lower for w in words):
            title_score = 40
            break
        matched = sum(1 for w in words if len(w) > 3 and w in title_lower)
        title_score = max(title_score, int(35 * matched / max(len(words), 1)))
    breakdown["title"] = title_score
    total += title_score

    # Compétences — 30 pts
    skills = profile.get("skills", [])
    matched_skills = [s for s in skills if s.lower() in text]
    skill_score = int(30 * len(matched_skills) / max(len(skills), 1))
    breakdown["skills"] = skill_score
    breakdown["matched_skills"] = matched_skills
    total += skill_score

    # Secteur — 10 pts
    sectors = profile.get("sectors", [])
    sector_score = 10 if any(s.lower() in text for s in sectors) else 0
    breakdown["sector"] = sector_score
    total += sector_score

    # Fraîcheur — 10 pts
    recency_score = 5
    date_str = job.get("date_posted")
    if date_str:
        try:
            posted = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - posted).days
            recency_score = max(0, 10 - age_days // 3)
        except Exception:
            pass
    breakdown["recency"] = recency_score
    total += recency_score

    # Contrat — 10 pts
    contract = (job.get("contract_type") or "").upper()
    target_contracts = [c.upper() for c in profile.get("contract_types", [])]
    contract_score = 10 if any(c in contract for c in target_contracts) else 0
    breakdown["contract"] = contract_score
    total += contract_score

    return min(100, max(0, total)), breakdown


# ---------------------------------------------------------------------------
# Utilitaire date France Travail
# ---------------------------------------------------------------------------

def parse_ft_date(text: str) -> str:
    """Convertit 'Publié aujourd'hui', 'hier', 'il y a X jours' → ISO 8601."""
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


# ---------------------------------------------------------------------------
# France Travail — scraping HTML (SSR, aucune JS requise)
# ---------------------------------------------------------------------------

def fetch_france_travail(profile) -> list:
    """
    France Travail rend ses résultats côté serveur (HTML statique).
    On scrape directement la page de résultats pour extraire les offres.
    """
    loc = profile["target_location"]
    city = loc.get("search_city", "Bordeaux")
    dept = "Gironde"

    # Plusieurs requêtes couvrant le profil de l'utilisateur
    search_queries = [
        f"ingénieur méthodes {dept}",
        f"ingénieur méthodes {city}",
        f"ingénieur maintenance {dept}",
        f"ingénieur fiabilisation {dept}",
        f"GMAO AMDEC {dept}",
        f"amélioration continue {dept}",
        f"ingénieur process {dept}",
    ]

    all_jobs: list = []
    seen_ids: set = set()

    for keywords in search_queries:
        for page_start in [0, 20]:  # 2 pages × 20 = 40 résultats par query
            params = {"motsCles": keywords, "tri": "0", "start": page_start}
            url = f"{FT_BASE}?{urlencode(params)}"
            try:
                resp = requests.get(url, headers=HEADERS, timeout=20)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "html.parser")

                items = soup.find_all("li", class_="result")
                if not items:
                    break  # plus de résultats

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
                        # Structure: NavigableString (company) + <span>location</span>
                        span = subtext.find("span")
                        location = span.get_text(strip=True) if span else ""
                        nav = next(
                            (c for c in subtext.children
                             if isinstance(c, NavigableString)
                             and c.strip().replace(" ", "").replace("-", "").strip()),
                            None,
                        )
                        company = nav.strip().replace(" ", "").replace("-", "").strip() if nav else ""

                    desc_el = li.find("p", class_="description")
                    desc = desc_el.get_text(strip=True) if desc_el else ""

                    # Contrat (hors mobile pour éviter doublon)
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
                        "location": location or city,
                        "description": desc[:900],
                        "url": f"{FT_DETAIL}/{job_id}",
                        "source": "france_travail",
                        "date_posted": date_posted,
                        "contract_type": contract,
                        "salary": "",
                    })

            except Exception as e:
                print(f"France Travail ({keywords}, start={page_start}): erreur — {e}")
                break

            time.sleep(1)

    print(f"France Travail scraping: {len(all_jobs)} offres")
    return all_jobs


# ---------------------------------------------------------------------------
# LinkedIn — scraping guest API (aucune auth requise)
# ---------------------------------------------------------------------------

LI_GUEST_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
LI_JOB_URL   = "https://www.linkedin.com/jobs/view"

LI_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9",
    "Accept": "text/html,application/xhtml+xml",
}


def _parse_li_date(dt_attr: str) -> str:
    """datetime='2026-05-16' → '2026-05-16' (déjà ISO)."""
    return dt_attr.strip() if dt_attr else ""


def fetch_linkedin(profile) -> list:
    """
    LinkedIn expose une API guest publique (sans connexion) qui retourne
    du HTML parsable avec les offres d'emploi.
    Si LinkedIn retourne une réponse vide (<100 octets), on s'arrête.
    """
    loc = profile["target_location"]
    city = loc.get("search_city", "Bordeaux")

    search_queries = [
        ("ingenieur methodes maintenance", f"{city}, Gironde, France"),
        ("ingenieur fiabilisation GMAO",  f"{city}, Gironde, France"),
        ("ingenieur process industriel",  f"{city}, Gironde, France"),
        ("ingenieur amelioration continue", f"{city}, France"),
    ]

    all_jobs: list = []
    seen_ids: set = set()

    for keywords, location in search_queries:
        for start in [0, 25]:
            params = {"keywords": keywords, "location": location, "start": start}
            try:
                resp = requests.get(
                    LI_GUEST_URL, params=params, headers=LI_HEADERS, timeout=20
                )
                # LinkedIn renvoie une réponse vide quand il rate-limite
                if len(resp.text) < 100:
                    print(f"LinkedIn ({keywords}): rate-limité, arrêt")
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

                    title_el  = card.find("h3", class_="base-search-card__title")
                    comp_el   = card.find("h4", class_="base-search-card__subtitle")
                    loc_el    = card.find("span", class_="job-search-card__location")
                    time_el   = card.find("time")
                    link_el   = card.find("a", class_="base-card__full-link")

                    title    = title_el.get_text(strip=True) if title_el else ""
                    company  = comp_el.get_text(strip=True)  if comp_el  else "Non précisé"
                    location = loc_el.get_text(strip=True)   if loc_el   else city
                    date_posted = _parse_li_date(time_el.get("datetime", "") if time_el else "")
                    url = link_el.get("href", f"{LI_JOB_URL}/{job_id}") if link_el else f"{LI_JOB_URL}/{job_id}"
                    # Nettoie les paramètres de tracking de l'URL
                    url = url.split("?")[0] if "?" in url else url

                    all_jobs.append({
                        "id": f"li_{job_id}",
                        "title": title,
                        "company": company,
                        "location": location,
                        "description": "",
                        "url": url,
                        "source": "linkedin",
                        "date_posted": date_posted,
                        "contract_type": "",
                        "salary": "",
                    })

            except Exception as e:
                print(f"LinkedIn ({keywords}, start={start}): erreur — {e}")
                break

            time.sleep(2)  # pause entre requêtes pour éviter le rate-limit

    print(f"LinkedIn scraping: {len(all_jobs)} offres")
    return all_jobs


# ---------------------------------------------------------------------------
# Déduplication & main
# ---------------------------------------------------------------------------

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
    profile = load_profile()
    loc = profile["target_location"]
    print(f"Profil : {profile['name']} — {profile['current_title']}")
    print(f"Zone   : {loc['city']} + {loc['radius_km']} km\n")

    all_jobs: list = []
    all_jobs += fetch_france_travail(profile)
    all_jobs += fetch_linkedin(profile)

    all_jobs = deduplicate(all_jobs)
    print(f"\n{len(all_jobs)} offres uniques après déduplication")

    for job in all_jobs:
        score, breakdown = score_job(job, profile)
        job["score"] = score
        job["score_breakdown"] = breakdown

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

    print(f"\n✓ {len(all_jobs)} offres sauvegardées dans data/jobs.json")
    labels = {"france_travail": "France Travail", "linkedin": "LinkedIn"}
    for src, cnt in sorted(sources.items(), key=lambda x: -x[1]):
        print(f"  {labels.get(src, src):20s}: {cnt}")


if __name__ == "__main__":
    main()
