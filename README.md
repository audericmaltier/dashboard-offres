# Dashboard Offres d'Emploi 💼

Dashboard mobile-first (iPhone-ready) qui centralise et classe les offres d'emploi par correspondance avec votre profil.

## Fonctionnalités

- Agrégation depuis **France Travail**, **Indeed**, **LinkedIn**, **Adzuna**, **HelloWork**
- Classement par **score de correspondance** (titre, compétences, contrat, fraîcheur)
- Filtres : source, type de contrat, score minimum, mot-clé
- **Actualisation automatique** toutes les 6 heures via GitHub Actions
- Interface optimisée **iPhone** (PWA installable)

---

## Mise en place

### 1. Personnaliser votre profil

Éditez `profile.json` avec vos informations réelles :

```json
{
  "target_titles": ["votre métier", "autre titre"],
  "skills": ["Compétence1", "Compétence2", ...],
  "contract_types": ["CDI", "CDD"],
  "target_location": {
    "city": "Biganos",
    "commune_insee": "33049",
    "lat": 44.6544,
    "lon": -0.9764,
    "radius_km": 40,
    "search_city": "Bordeaux"
  }
}
```

### 2. Configurer les clés API (GitHub Secrets)

Allez dans **Settings → Secrets and variables → Actions** de votre dépôt.

| Secret | Source | Inscription |
|--------|--------|-------------|
| `FRANCE_TRAVAIL_CLIENT_ID` | France Travail | [francetravail.io](https://francetravail.io/data/api) |
| `FRANCE_TRAVAIL_CLIENT_SECRET` | France Travail | — |
| `ADZUNA_APP_ID` | Adzuna | [developer.adzuna.com](https://developer.adzuna.com) |
| `ADZUNA_APP_KEY` | Adzuna | — |
| `RAPIDAPI_KEY` | RapidAPI (JSearch) | [rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch](https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch) |

> Le dashboard fonctionne avec zéro clé (les sources sans clé sont ignorées). Ajoutez-les progressivement.

### 3. Activer GitHub Pages

Dans **Settings → Pages** : source = `Deploy from a branch` → branche `main` → dossier `/` (root).

### 4. Lancer la première collecte

Dans **Actions → Fetch Job Offers → Run workflow**.

Les offres se mettent ensuite à jour automatiquement 3 fois par jour.

### 5. Accéder depuis votre iPhone

URL : `https://audericmaltier.github.io/dashboard-offres/`

**Pour l'installer comme app :**
1. Ouvrez l'URL dans Safari
2. Bouton Partage → "Sur l'écran d'accueil"

---

## Structure du projet

```
dashboard-offres/
├── index.html                   # Dashboard (frontend mobile)
├── manifest.json                # PWA pour installation iPhone
├── profile.json                 # Votre profil CV (à personnaliser)
├── data/
│   └── jobs.json                # Offres collectées (auto-généré)
├── scripts/
│   ├── fetch_jobs.py            # Script de collecte multi-sources
│   └── requirements.txt
└── .github/workflows/
    └── fetch-jobs.yml           # GitHub Actions (planification)
```

## Score de correspondance

| Critère | Poids |
|---------|-------|
| Titre du poste | 40 pts |
| Compétences détectées | 30 pts |
| Fraîcheur de l'offre | 20 pts |
| Type de contrat | 10 pts |

🟢 70–100 · 🟡 45–69 · 🔴 0–44
