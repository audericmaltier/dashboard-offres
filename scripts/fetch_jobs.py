#!/usr/bin/env python3
"""
Collecte les offres d'emploi depuis des sources PUBLIQUES, sans clé API.

Sources gratuites (RSS + scraping JSON-LD) :
  - France Travail  → flux RSS public
  - Indeed France   → flux RSS public
  - APEC            → flux RSS public
  - HelloWork       → données JSON-LD dans les pages de résultats

Les offres sont scorées par rapport au profil (profile.json) et
sauvegardées dans data/jobs.json pour le dashboard GitHub Pages.
"""

import json
import math
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote_plus

import feedparser
import requests

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
}


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
# Utilitaires RSS
# ---------------------------------------------------------------------------

def parse_rss_date(entry):
    """Convertit une date feedparser en ISO 8601."""
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        try:
            return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc).isoformat()
        except Exception:
            pass
    return ""


def strip_html(text):
    return re.sub(r"<[^>]+>", " ", text or "").strip()


# ---------------------------------------------------------------------------
# France Travail — flux RSS public (aucune clé requise)
# ---------------------------------------------------------------------------

def fetch_france_travail(profile):
    """
    France Travail expose un flux RSS public pour les recherches d'offres.
    URL : https://candidat.francetravail.fr/offres/recherche/rss
    Paramètres : motsCles, communes (code INSEE), distance, tri
    """
    loc = profile["target_location"]
    commune = loc.get("commune_insee", "33049")
    radius = loc.get("radius_km", 40)

    search_queries = [
        "ingénieur méthodes",
        "ingénieur maintenance",
        "ingénieur fiabilisation",
        "GMAO AMDEC",
    ]

    all_jobs = []
    seen_ids = set()

    for keywords in search_queries:
        url = (
            f"https://candidat.francetravail.fr/offres/recherche/rss"
            f"?motsCles={quote_plus(keywords)}"
            f"&communes={commune}"
            f"&distance={radius}"
            f"&tri=0"
        )
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries:
                job_id = f"ft_{entry.get('id', entry.get('link', ''))}"
                if job_id in seen_ids:
                    continue
                seen_ids.add(job_id)

                summary = strip_html(entry.get("summary", ""))
                # Le résumé FT contient souvent : "Entreprise : X | Lieu : Y | Contrat : Z"
                company = ""
                location = ""
                contract = ""
                for line in summary.split("|"):
                    line = line.strip()
                    if line.lower().startswith("entreprise"):
                        company = line.split(":", 1)[-1].strip()
                    elif line.lower().startswith("lieu"):
                        location = line.split(":", 1)[-1].strip()
                    elif line.lower().startswith("contrat") or line.lower().startswith("type"):
                        contract = line.split(":", 1)[-1].strip()

                all_jobs.append({
                    "id": job_id,
                    "title": entry.get("title", ""),
                    "company": company or "Non précisé",
                    "location": location or loc.get("city", ""),
                    "description": summary[:900],
                    "url": entry.get("link", ""),
                    "source": "france_travail",
                    "date_posted": parse_rss_date(entry),
                    "contract_type": contract,
                    "salary": "",
                })
        except Exception as e:
            print(f"France Travail RSS ({keywords}): erreur — {e}")

        time.sleep(0.5)  # politesse

    print(f"France Travail RSS: {len(all_jobs)} offres")
    return all_jobs


# ---------------------------------------------------------------------------
# Indeed France — flux RSS public (aucune clé requise)
# ---------------------------------------------------------------------------

def fetch_indeed(profile):
    """
    Indeed expose des flux RSS publics pour les recherches d'offres.
    URL : https://fr.indeed.com/rss
    Paramètres : q (mots-clés), l (lieu), radius (km), sort (date/relevance)
    """
    loc = profile["target_location"]
    city = loc.get("search_city", "Bordeaux")
    radius_miles = int(loc.get("radius_km", 40) * 0.621)  # Indeed utilise les miles

    search_queries = [
        "ingénieur méthodes maintenance",
        "ingénieur fiabilisation industriel",
        "GMAO AMDEC ingénieur",
    ]

    all_jobs = []
    seen_ids = set()

    for keywords in search_queries:
        url = (
            f"https://fr.indeed.com/rss"
            f"?q={quote_plus(keywords)}"
            f"&l={quote_plus(city)}"
            f"&radius={radius_miles}"
            f"&sort=date"
        )
        try:
            feed = feedparser.parse(url, request_headers=HEADERS)
            for entry in feed.entries:
                job_id = f"in_{entry.get('id', entry.get('link', ''))}"
                if job_id in seen_ids:
                    continue
                seen_ids.add(job_id)

                all_jobs.append({
                    "id": job_id,
                    "title": entry.get("title", ""),
                    "company": entry.get("source", {}).get("value", "Non précisé")
                               if hasattr(entry.get("source", {}), "get")
                               else "Non précisé",
                    "location": entry.get("indeed_city", city),
                    "description": strip_html(entry.get("summary", ""))[:900],
                    "url": entry.get("link", ""),
                    "source": "indeed",
                    "date_posted": parse_rss_date(entry),
                    "contract_type": "",
                    "salary": entry.get("indeed_salary", ""),
                })
        except Exception as e:
            print(f"Indeed RSS ({keywords}): erreur — {e}")

        time.sleep(0.5)

    print(f"Indeed RSS: {len(all_jobs)} offres")
    return all_jobs


# ---------------------------------------------------------------------------
# APEC — flux RSS public (aucune clé requise)
# ---------------------------------------------------------------------------

def fetch_apec(profile):
    """
    L'APEC expose un flux RSS public pour les offres cadres.
    URL : https://www.apec.fr/candidat/recherche-emploi.html/emploi/rss
    """
    loc = profile["target_location"]
    search_queries = [
        "ingénieur méthodes",
        "ingénieur maintenance",
        "ingénieur fiabilisation",
    ]

    all_jobs = []
    seen_ids = set()

    for keywords in search_queries:
        # APEC RSS feed pour les cadres
        url = (
            f"https://www.apec.fr/candidat/recherche-emploi.html/emploi/rss"
            f"?motsCles={quote_plus(keywords)}"
            f"&lieu=100-33"   # Gironde
            f"&distance={loc.get('radius_km', 40)}"
        )
        try:
            feed = feedparser.parse(url, request_headers=HEADERS)
            for entry in feed.entries:
                job_id = f"apec_{entry.get('id', entry.get('link', ''))}"
                if job_id in seen_ids:
                    continue
                seen_ids.add(job_id)

                summary = strip_html(entry.get("summary", ""))
                all_jobs.append({
                    "id": job_id,
                    "title": entry.get("title", ""),
                    "company": "Non précisé",
                    "location": loc.get("search_city", "Bordeaux"),
                    "description": summary[:900],
                    "url": entry.get("link", ""),
                    "source": "apec",
                    "date_posted": parse_rss_date(entry),
                    "contract_type": "CDI",
                    "salary": "",
                })
        except Exception as e:
            print(f"APEC RSS ({keywords}): erreur — {e}")

        time.sleep(0.5)

    print(f"APEC RSS: {len(all_jobs)} offres")
    return all_jobs


# ---------------------------------------------------------------------------
# HelloWork — scraping JSON-LD (aucune clé requise)
# ---------------------------------------------------------------------------

def fetch_hellowork(profile):
    """
    HelloWork intègre des données JSON-LD (schema.org/JobPosting) dans ses
    pages de résultats, accessibles publiquement sans authentification.
    """
    loc = profile["target_location"]
    city = loc.get("search_city", "Bordeaux")

    search_queries = [
        ("ingénieur méthodes", city),
        ("ingénieur maintenance", city),
        ("GMAO fiabilisation", city),
    ]

    all_jobs = []
    seen_ids = set()

    for keywords, location in search_queries:
        url = "https://www.hellowork.com/fr-fr/emploi/recherche.html"
        params = {
            "k": keywords,
            "l": location,
            "ray": str(loc.get("radius_km", 40)),
        }
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=20)
            resp.raise_for_status()

            # Extraire les blocs JSON-LD de la page
            blocks = re.findall(
                r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                resp.text,
                re.DOTALL | re.IGNORECASE,
            )
            for block in blocks:
                try:
                    data = json.loads(block)
                except Exception:
                    continue

                items = []
                if isinstance(data, list):
                    items = data
                elif data.get("@type") == "ItemList":
                    items = [e.get("item", {}) for e in data.get("itemListElement", [])]
                elif data.get("@type") == "JobPosting":
                    items = [data]

                for item in items:
                    if item.get("@type") != "JobPosting":
                        continue
                    job_id = f"hw_{item.get('url', '')}"
                    if job_id in seen_ids:
                        continue
                    seen_ids.add(job_id)

                    org = item.get("hiringOrganization", {})
                    addr = item.get("jobLocation", {}).get("address", {})
                    loc_str = addr.get("addressLocality", location)

                    all_jobs.append({
                        "id": job_id,
                        "title": item.get("title", ""),
                        "company": org.get("name") or "Non précisé",
                        "location": loc_str,
                        "description": strip_html(item.get("description", ""))[:900],
                        "url": item.get("url", "https://www.hellowork.com"),
                        "source": "hellowork",
                        "date_posted": item.get("datePosted", ""),
                        "contract_type": item.get("employmentType", ""),
                        "salary": "",
                    })
        except Exception as e:
            print(f"HelloWork ({keywords}): erreur — {e}")

        time.sleep(1)

    print(f"HelloWork: {len(all_jobs)} offres")
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
    all_jobs += fetch_apec(profile)
    all_jobs += fetch_indeed(profile)
    all_jobs += fetch_hellowork(profile)

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

    print(f"\n✓ {len(all_jobs)} offres sauvegardées")
    labels = {
        "france_travail": "France Travail",
        "apec": "APEC",
        "indeed": "Indeed",
        "hellowork": "HelloWork",
    }
    for src, cnt in sorted(sources.items(), key=lambda x: -x[1]):
        print(f"  {labels.get(src, src):20s}: {cnt}")


if __name__ == "__main__":
    main()
