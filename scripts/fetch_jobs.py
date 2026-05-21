#!/usr/bin/env python3
"""
Fetches job offers from multiple sources, scores them against Audéric Maltier's
profile (Ingénieur Méthodes & Maintenance, ENSAM), and writes data/jobs.json.

Sources: France Travail, APEC, Adzuna, JSearch (LinkedIn/Indeed), HelloWork
"""

import json
import math
import os
import re
from datetime import datetime, timezone

import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE_PATH = os.path.join(BASE_DIR, "profile.json")
OUTPUT_PATH = os.path.join(BASE_DIR, "data", "jobs.json")


def load_profile():
    with open(PROFILE_PATH, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_job(job, profile):
    """Returns (score 0-100, breakdown dict)."""
    breakdown = {}
    total = 0

    title_lower = (job.get("title") or "").lower()
    text = f"{job.get('title', '')} {job.get('description', '')}".lower()

    # Title match — 40 pts
    title_score = 0
    for target in profile.get("target_titles", []):
        words = target.lower().split()
        if all(w in title_lower for w in words):
            title_score = 40
            break
        matched_words = sum(1 for w in words if len(w) > 3 and w in title_lower)
        partial = int(30 * matched_words / max(len(words), 1))
        title_score = max(title_score, partial)
    breakdown["title"] = title_score
    total += title_score

    # Skill match — 30 pts
    skills = profile.get("skills", [])
    matched_skills = [s for s in skills if s.lower() in text]
    skill_score = int(30 * len(matched_skills) / max(len(skills), 1))
    breakdown["skills"] = skill_score
    breakdown["matched_skills"] = matched_skills
    total += skill_score

    # Sector relevance — 10 pts (bonus if sector mentioned)
    sectors = profile.get("sectors", [])
    sector_hit = any(s.lower() in text for s in sectors)
    sector_score = 10 if sector_hit else 0
    breakdown["sector"] = sector_score
    total += sector_score

    # Recency — 10 pts
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

    # Contract type — 10 pts
    contract = (job.get("contract_type") or "").upper()
    target_contracts = [c.upper() for c in profile.get("contract_types", [])]
    contract_score = 10 if any(c in contract for c in target_contracts) else 0
    breakdown["contract"] = contract_score
    total += contract_score

    return min(100, max(0, total)), breakdown


# ---------------------------------------------------------------------------
# France Travail
# ---------------------------------------------------------------------------

def _ft_token(client_id, client_secret):
    resp = requests.post(
        "https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=%2Fpartenaire",
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "api_offresdemploiv2 o2dsoffre",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _ft_search(token, commune, radius, keywords, start=0):
    resp = requests.get(
        "https://api.emploi-store.fr/partenaire/offresdemploi/v2/offres/search",
        params={
            "commune": commune,
            "distance": radius,
            "motsCles": keywords,
            "range": f"{start}-{start + 49}",
            "sort": 1,
        },
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=20,
    )
    if resp.status_code not in (200, 206):
        return []
    data = resp.json()

    jobs = []
    for o in data.get("resultats", []):
        lieu = o.get("lieuTravail", {})
        jobs.append({
            "id": f"ft_{o.get('id', '')}",
            "title": o.get("intitule", ""),
            "company": o.get("entreprise", {}).get("nom") or "Non précisé",
            "location": lieu.get("libelle", ""),
            "description": (o.get("description") or "")[:900],
            "url": o.get("origineOffre", {}).get("urlOrigine")
                or f"https://www.francetravail.fr/offres-emploi/offre/{o.get('id', '')}",
            "source": "france_travail",
            "date_posted": o.get("dateCreation", ""),
            "contract_type": o.get("typeContratLibelle", ""),
            "salary": o.get("salaire", {}).get("libelle", ""),
        })
    return jobs


def fetch_france_travail(profile):
    client_id = os.environ.get("FRANCE_TRAVAIL_CLIENT_ID")
    client_secret = os.environ.get("FRANCE_TRAVAIL_CLIENT_SECRET")
    if not client_id or not client_secret:
        print("France Travail: credentials not set, skipping")
        return []

    try:
        token = _ft_token(client_id, client_secret)
    except Exception as e:
        print(f"France Travail auth error: {e}")
        return []

    loc = profile["target_location"]
    commune = loc.get("commune_insee", "33049")
    radius = loc.get("radius_km", 40)

    # Multiple keyword queries for better coverage
    search_queries = [
        "ingénieur méthodes maintenance",
        "ingénieur fiabilisation",
        "GMAO AMDEC",
    ]

    all_jobs = []
    for kw in search_queries:
        jobs = _ft_search(token, commune, radius, kw)
        all_jobs.extend(jobs)

    print(f"France Travail: {len(all_jobs)} offres (avant dédup)")
    return all_jobs


# ---------------------------------------------------------------------------
# APEC (Association Pour l'Emploi des Cadres) — parfait pour ingénieurs
# ---------------------------------------------------------------------------

def fetch_apec(profile):
    loc = profile["target_location"]
    titles = profile.get("target_titles", [])
    keyword = " ".join(titles[:3])

    try:
        resp = requests.get(
            "https://api.apec.fr/cms-diffusion/v1/offres",
            params={
                "motsCles": keyword,
                "lieu": "33",          # département Gironde
                "distance": loc.get("radius_km", 40),
                "page": 0,
                "par_page": 50,
                "tri": "date",
            },
            headers={
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (compatible; job-dashboard/1.0)",
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"APEC error: {e}")
        return []

    jobs = []
    for o in data.get("resultats", []):
        lieu = o.get("lieu", {})
        sal = o.get("salaire", {})
        salary = ""
        if sal.get("commentaire"):
            salary = sal["commentaire"]
        elif sal.get("fourchetteBasse") and sal.get("fourchetteHaute"):
            salary = f"{sal['fourchetteBasse']}–{sal['fourchetteHaute']} k€/an"

        jobs.append({
            "id": f"apec_{o.get('numOffre', '')}",
            "title": o.get("intitule", ""),
            "company": o.get("nomEntreprise") or "Non précisé",
            "location": lieu.get("libelle", ""),
            "description": (o.get("texteHtml") or o.get("texte") or "")[:900],
            "url": f"https://www.apec.fr/candidat/recherche-emploi.html/emploi/{o.get('numOffre', '')}",
            "source": "apec",
            "date_posted": o.get("dateCreation", ""),
            "contract_type": o.get("typeContrat", {}).get("libelle", ""),
            "salary": salary,
        })

    print(f"APEC: {len(jobs)} offres")
    return jobs


# ---------------------------------------------------------------------------
# Adzuna
# ---------------------------------------------------------------------------

def fetch_adzuna(profile):
    app_id = os.environ.get("ADZUNA_APP_ID")
    app_key = os.environ.get("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        print("Adzuna: credentials not set, skipping")
        return []

    loc = profile["target_location"]
    keyword = "ingénieur méthodes maintenance"

    try:
        resp = requests.get(
            "https://api.adzuna.com/v1/api/jobs/fr/search/1",
            params={
                "app_id": app_id,
                "app_key": app_key,
                "results_per_page": 50,
                "what": keyword,
                "where": loc.get("search_city", "Bordeaux"),
                "distance": loc.get("radius_km", 40),
                "content-type": "application/json",
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"Adzuna error: {e}")
        return []

    jobs = []
    for job in data.get("results", []):
        display = job.get("location", {}).get("display_name", "")
        loc_str = ", ".join(display.split(",")[:2])
        sal_min = job.get("salary_min")
        sal_max = job.get("salary_max")
        salary = ""
        if sal_min and sal_max:
            salary = f"{int(sal_min):,}–{int(sal_max):,} €/an".replace(",", " ")

        jobs.append({
            "id": f"az_{job.get('id', '')}",
            "title": job.get("title", ""),
            "company": job.get("company", {}).get("display_name") or "Non précisé",
            "location": loc_str,
            "description": (job.get("description") or "")[:900],
            "url": job.get("redirect_url", ""),
            "source": "adzuna",
            "date_posted": job.get("created", ""),
            "contract_type": job.get("contract_type", ""),
            "salary": salary,
        })

    print(f"Adzuna: {len(jobs)} offres")
    return jobs


# ---------------------------------------------------------------------------
# JSearch via RapidAPI (LinkedIn + Indeed + Glassdoor)
# ---------------------------------------------------------------------------

def fetch_jsearch(profile):
    api_key = os.environ.get("RAPIDAPI_KEY")
    if not api_key:
        print("JSearch: API key not set, skipping")
        return []

    loc = profile["target_location"]
    city = loc.get("search_city", "Bordeaux")

    SOURCE_MAP = {
        "linkedin": "linkedin",
        "indeed": "indeed",
        "glassdoor": "glassdoor",
    }

    queries = [
        f"ingénieur méthodes maintenance {city}",
        f"ingénieur fiabilisation AMDEC {city}",
    ]

    all_jobs = []
    try:
        for query in queries:
            resp = requests.get(
                "https://jsearch.p.rapidapi.com/search",
                headers={
                    "X-RapidAPI-Key": api_key,
                    "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
                },
                params={
                    "query": query,
                    "page": "1",
                    "num_pages": "2",
                    "date_posted": "month",
                    "country": "fr",
                },
                timeout=25,
            )
            resp.raise_for_status()
            data = resp.json()

            for job in data.get("data", []):
                publisher = (job.get("job_publisher") or "").lower()
                source = next((v for k, v in SOURCE_MAP.items() if k in publisher), "jsearch")
                all_jobs.append({
                    "id": f"js_{job.get('job_id', '')}",
                    "title": job.get("job_title", ""),
                    "company": job.get("employer_name") or "Non précisé",
                    "location": f"{job.get('job_city', '')}, {job.get('job_state', '') or job.get('job_country', '')}".strip(", "),
                    "description": (job.get("job_description") or "")[:900],
                    "url": job.get("job_apply_link", ""),
                    "source": source,
                    "date_posted": job.get("job_posted_at_datetime_utc", ""),
                    "contract_type": job.get("job_employment_type", ""),
                    "salary": "",
                })
    except Exception as e:
        print(f"JSearch error: {e}")

    print(f"JSearch: {len(all_jobs)} offres (LinkedIn/Indeed/Glassdoor)")
    return all_jobs


# ---------------------------------------------------------------------------
# HelloWork (structured data scraping)
# ---------------------------------------------------------------------------

def fetch_hellowork(profile):
    loc = profile["target_location"]
    city = loc.get("search_city", "Bordeaux")
    keyword = "ingénieur méthodes maintenance"

    try:
        resp = requests.get(
            "https://www.hellowork.com/fr-fr/emploi/recherche.html",
            params={
                "k": keyword,
                "l": city,
                "ray": str(loc.get("radius_km", 40)),
            },
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "fr-FR,fr;q=0.9",
            },
            timeout=20,
        )
    except Exception as e:
        print(f"HelloWork error: {e}")
        return []

    jobs = []
    try:
        matches = re.findall(
            r'<script type="application/ld\+json">(.*?)</script>',
            resp.text, re.DOTALL
        )
        for match in matches:
            try:
                data = json.loads(match)
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
                    org = item.get("hiringOrganization", {})
                    addr = item.get("jobLocation", {}).get("address", {})
                    loc_str = f"{addr.get('addressLocality', '')}, {addr.get('addressRegion', '')}".strip(", ")
                    jobs.append({
                        "id": f"hw_{len(jobs)}",
                        "title": item.get("title", ""),
                        "company": org.get("name") or "Non précisé",
                        "location": loc_str,
                        "description": re.sub(r"<[^>]+>", " ", item.get("description") or "")[:900],
                        "url": item.get("url", "https://www.hellowork.com"),
                        "source": "hellowork",
                        "date_posted": item.get("datePosted", ""),
                        "contract_type": item.get("employmentType", ""),
                        "salary": "",
                    })
            except Exception:
                continue
    except Exception as e:
        print(f"HelloWork parse error: {e}")

    print(f"HelloWork: {len(jobs)} offres")
    return jobs


# ---------------------------------------------------------------------------
# Deduplication & main
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
    all_jobs += fetch_adzuna(profile)
    all_jobs += fetch_jsearch(profile)
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
    for src, cnt in sorted(sources.items(), key=lambda x: -x[1]):
        src_info = {
            "france_travail": "France Travail",
            "apec": "APEC",
            "adzuna": "Adzuna",
            "linkedin": "LinkedIn",
            "indeed": "Indeed",
            "glassdoor": "Glassdoor",
            "hellowork": "HelloWork",
        }.get(src, src)
        print(f"  {src_info:20s}: {cnt}")


if __name__ == "__main__":
    main()
