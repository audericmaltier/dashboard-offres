# Dashboard Offres d'Emploi 💼

Dashboard mobile-first (iPhone-ready) qui centralise et classe les offres d'emploi par correspondance avec votre profil.

**Aucune clé API requise** — toutes les sources utilisées exposent leurs offres publiquement (flux RSS ou données JSON-LD intégrées dans leurs pages).

## Fonctionnalités

- Agrégation depuis **France Travail**, **APEC**, **Indeed**, **HelloWork** — données publiques
- Classement par **score de correspondance** (titre, compétences, secteur, contrat, fraîcheur)
- Filtres : source, type de contrat, score minimum, mot-clé
- **Actualisation automatique** 3×/jour via GitHub Actions
- Interface optimisée **iPhone** (PWA installable)

---

## Mise en place

### 1. Personnaliser votre profil (optionnel)

`profile.json` est déjà configuré avec votre profil. Vous pouvez ajuster les titres et compétences :

```json
{
  "target_titles": ["ingénieur méthodes", "ingénieur maintenance", ...],
  "skills": ["AMDEC", "GMAO", "Power BI", ...]
}
```

### 2. Activer GitHub Pages

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
