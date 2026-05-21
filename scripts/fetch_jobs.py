#!/usr/bin/env python3
"""
Fetches job offers from multiple sources, scores them against the user profile,
and writes the result to data/jobs.json for the GitHub Pages dashboard.

Sources: France Travail, Adzuna, JSearch (LinkedIn/Indeed), HelloWork
"""

import json
import math
import os
import re
import sys
from datetime import datetime, timezone

import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE_PATH = os.path.join(BASE_DIR, "profile.json")
OUTPUT_PATH = os.path.join(BASE_DIR, "data", "jobs.json")


def load_profile():
    with open(PROFILE_PATH, encoding="utf-8") as f:
        return json.load(f)


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def score_job(job, profile):
    """Score a job against the profile. Returns (total_score 0-100, breakdown dict)."""
    breakdown = {}
    total = 0

    # Title match (0–40 pts)
    title_lower = (job.get("title") or "").lower()
    title_score = 0
    for target in profile.get("target_titles", []):
        if target.lower() in title_lower:
            title_score = 40
            break
        for word in target.lower().split():
            if len(word) > 3 and word in title_lower:
                title_score = max(title_score, 20)
    breakdown["title"] = title_score
    total += title_score

    # Skill match (0–30 pts)
    text = f"{job.get('title', '')} {job.get('description', '')}".lower()
    skills = profile.get("skills", [])
    matched = [s for s in skills if s.lower() in text]
    skill_score = int(30 * len(matched) / max(len(skills), 1))
    breakdown["skills"] = skill_score
    breakdown["matched_skills"] = matched
    total += skill_score

    # Recency (0–20 pts)
    recency_score = 10
    date_str = job.get("date_posted")
    if date_str:
        try:
            posted = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - posted).days
            recency_score = max(0, 20 - age_days)
        except Exception:
            pass
    breakdown["recency"] = recency_score
    total += recency_score

    # Contract type (0–10 pts)
    contract = (job.get("contract_type") or "").upper()
    target_contracts = [c.upper() for c in profile.get("contract_types", [])]
    contract_score = 10 if any(c in contract for c in target_contracts) else 0
    breakdown["contract"] = contract_score
    total += contract_score

    return min(100, max(0, total)), breakdown


# ---------------------------------------------------------------------------
# France Travail
# ---------------------------------------------------------------------------

def fetch_france_travail(profile):
    client_id = os.environ.get("FRANCE_TRAVAIL_CLIENT_ID")
    client_secret = os.environ.get("FRANCE_TRAVAIL_CLIENT_SECRET")
    if not client_id or not client_secret:
        print("France Travail: credentials not set, skipping")
        return []

    # OAuth token
    try:
        token_resp = requests.post(
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
        token_resp.raise_for_status()
        token = token_resp.json()["access_token"]
    except Exception as e:
        print(f"France Travail auth error: {e}")
        return []

    loc = profile["target_location"]
    commune = loc.get("commune_insee", "33049")
    radius = loc.get("radius_km", 40)
    keywords = " ".join(profile.get("target_titles", [])[:3])

    try:
        resp = requests.get(
            "https://api.emploi-store.fr/partenaire/offresdemploi/v2/offres/search",
            params={
                "commune": commune,
                "distance": radius,
                "motsCles": keywords,
                "range": "0-149",
                "sort": 1,
            },
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=20,
        )
        if resp.status_code not in (200, 206):
            print(f"France Travail search failed: {resp.status_code}")
            return []
        data = resp.json()
    except Exception as e:
        print(f"France Travail fetch error: {e}")
        return []

    jobs = []
    for offre in data.get("resultats", []):
        lieu = offre.get("lieuTravail", {})
        jobs.append({
            "id": f"ft_{offre.get('id', '')}",
            "title": offre.get("intitule", ""),
            "company": offre.get("entreprise", {}).get("nom") or "Non précisé",
            "location": lieu.get("libelle", ""),
            "description": offre.get("description", "")[:800],
            "url": offre.get("origineOffre", {}).get("urlOrigine")
                or f"https://www.francetravail.fr/offres-emploi/offre/{offre.get('id', '')}",
            "source": "france_travail",
            "date_posted": offre.get("dateCreation", ""),
            "contract_type": offre.get("typeContratLibelle", ""),
            "salary": offre.get("salaire", {}).get("libelle", ""),
        })

    print(f"France Travail: {len(jobs)} offres")
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
    keywords = " ".join(profile.get("target_titles", [])[:3])

    try:
        resp = requests.get(
            "https://api.adzuna.com/v1/api/jobs/fr/search/1",
            params={
                "app_id": app_id,
                "app_key": app_key,
                "results_per_page": 50,
                "what": keywords,
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
        location_str = ", ".join(display.split(",")[:2]) if display else ""
        sal_min = job.get("salary_min")
        sal_max = job.get("salary_max")
        salary = f"{int(sal_min):,}–{int(sal_max):,} €/an".replace(",", " ") if sal_min and sal_max else ""
        jobs.append({
            "id": f"az_{job.get('id', '')}",
            "title": job.get("title", ""),
            "company": job.get("company", {}).get("display_name") or "Non précisé",
            "location": location_str,
            "description": job.get("description", "")[:800],
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
    keywords = " ".join(profile.get("target_titles", [])[:2])
    city = loc.get("search_city", "Bordeaux")

    SOURCE_MAP = {
        "linkedin": "linkedin",
        "indeed": "indeed",
        "glassdoor": "glassdoor",
    }

    jobs = []
    try:
        resp = requests.get(
            "https://jsearch.p.rapidapi.com/search",
            headers={
                "X-RapidAPI-Key": api_key,
                "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
            },
            params={
                "query": f"{keywords} {city} France",
                "page": "1",
                "num_pages": "3",
                "date_posted": "month",
            },
            timeout=25,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"JSearch error: {e}")
        return []

    for job in data.get("data", []):
        publisher = (job.get("job_publisher") or "").lower()
        source = next((v for k, v in SOURCE_MAP.items() if k in publisher), "jsearch")
        jobs.append({
            "id": f"js_{job.get('job_id', '')}",
            "title": job.get("job_title", ""),
            "company": job.get("employer_name") or "Non précisé",
            "location": f"{job.get('job_city', '')}, {job.get('job_state', '') or job.get('job_country', '')}".strip(", "),
            "description": (job.get("job_description") or "")[:800],
            "url": job.get("job_apply_link", ""),
            "source": source,
            "date_posted": job.get("job_posted_at_datetime_utc", ""),
            "contract_type": job.get("job_employment_type", ""),
            "salary": "",
        })

    print(f"JSearch: {len(jobs)} offres (LinkedIn/Indeed/Glassdoor)")
    return jobs


# ---------------------------------------------------------------------------
# HelloWork (scraping basique via leur API interne non-officielle)
# ---------------------------------------------------------------------------

def fetch_hellowork(profile):
    loc = profile["target_location"]
    keywords = "+".join(profile.get("target_titles", [])[:2]).replace(" ", "+")
    city = loc.get("search_city", "Bordeaux")

    try:
        resp = requests.get(
            "https://www.hellowork.com/fr-fr/emploi/recherche.html",
            params={
                "k": " ".join(profile.get("target_titles", [])[:2]),
                "l": city,
                "ray": str(loc.get("radius_km", 40)),
            },
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; job-dashboard/1.0)",
                "Accept": "text/html",
            },
            timeout=15,
        )
    except Exception as e:
        print(f"HelloWork error: {e}")
        return []

    # Extract JSON-LD structured data from the page
    jobs = []
    try:
        matches = re.findall(r'<script type="application/ld\+json">(.*?)</script>', resp.text, re.DOTALL)
        for match in matches:
            try:
                data = json.loads(match)
                if isinstance(data, list):
                    items = data
                elif data.get("@type") == "ItemList":
                    items = [e.get("item", {}) for e in data.get("itemListElement", [])]
                else:
                    items = [data]

                for item in items:
                    if item.get("@type") != "JobPosting":
                        continue
                    org = item.get("hiringOrganization", {})
                    loc_data = item.get("jobLocation", {}).get("address", {})
                    jobs.append({
                        "id": f"hw_{len(jobs)}",
                        "title": item.get("title", ""),
                        "company": org.get("name") or "Non précisé",
                        "location": f"{loc_data.get('addressLocality', '')}, {loc_data.get('addressRegion', '')}".strip(", "),
                        "description": (item.get("description") or "")[:800],
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
# Deduplication and main
# ---------------------------------------------------------------------------

def deduplicate(jobs):
    seen = set()
    unique = []
    for job in jobs:
        key = f"{(job.get('title') or '').lower()[:35]}|{(job.get('company') or '').lower()[:25]}"
        if key not in seen:
            seen.add(key)
            unique.append(job)
    return unique


def main():
    profile = load_profile()
    print(f"Profil chargé: {profile['name']} — {profile['target_location']['city']} +{profile['target_location']['radius_km']}km\n")

    all_jobs = []
    all_jobs += fetch_france_travail(profile)
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

    sources = {}
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

    print(f"\n✓ {len(all_jobs)} offres sauvegardées dans {OUTPUT_PATH}")
    print(f"  Sources: {sources}")


if __name__ == "__main__":
    main()
