# Référentiel des KPIs — Tableau de Bord Achats

> **Document de référence interne — Avocarbon**
> Version 1.0 · Juin 2026
>
> Ce document décrit chaque indicateur affiché dans le tableau de bord **Value Management**, son périmètre, sa formule de calcul et les règles métier associées.

---

## Table des matières

1. [Conventions générales](#1-conventions-générales)
2. [Indicateurs P1 — Exécution](#2-indicateurs-p1--exécution)
3. [Indicateurs P2 — Pipeline](#3-indicateurs-p2--pipeline)
4. [Indicateurs secondaires](#4-indicateurs-secondaires)
5. [Onglet Économies Mensuelles](#5-onglet-économies-mensuelles)
6. [Onglet Par Usine](#6-onglet-par-usine)
7. [Onglet Par Type](#7-onglet-par-type)
8. [Onglet Par Acheteur](#8-onglet-par-acheteur)
9. [Onglet Alertes](#9-onglet-alertes)
10. [Définitions clés](#10-définitions-clés)

---

## 1. Conventions générales

### 1.1 Exercice budgétaire (FY)

L'exercice budgétaire **N** couvre **décembre N-1 à novembre N** (12 mois).

| FY | Début | Fin |
|----|-------|-----|
| FY 2025 | 1er déc. 2024 | 30 nov. 2025 |
| FY 2026 | 1er déc. 2025 | 30 nov. 2026 |
| FY 2027 | 1er déc. 2026 | 30 nov. 2027 |

Le mois de **décembre est toujours affecté à l'exercice suivant** : une économie réalisée en décembre 2025 appartient au FY 2026, pas au FY 2025.

### 1.2 Perimètre des lignes actives

Seules les **lignes financières** (`financial_line`) dont :
- le statut est `Active`
- la période active (début + durée) chevauche l'exercice sélectionné

sont prises en compte dans les calculs. Les opportunités en Phase 0 et Phase 1 n'ont pas encore de ligne financière ; elles apparaissent uniquement dans les compteurs de pipeline et la vue Par Acheteur.

### 1.3 Consolidation en EUR

Toutes les économies sont consolidées en **EUR** (devise de reporting groupe).
Pour les lignes libellées dans une autre devise, le taux de change `fx_rate_to_eur` de l'opportunité est appliqué.
Si ce taux est absent ou nul, la ligne est comptabilisée **à la parité 1:1** et un avertissement est affiché (bannière orange en haut de page).

### 1.4 Distribution mensuelle des économies attendues

Les économies attendues sont distribuées **uniformément** sur la durée de la ligne :

```
Économie mensuelle attendue = Économie annuelle / Durée (mois)
```

Les mois antérieurs à la date de démarrage réelle (`real_start_date` ou `planned_start_date`) ont une économie attendue de **0** — ils ne sont pas comptés dans les KPIs YTD.

### 1.5 Forecast EOY (fallback)

Si aucun forecast EOY n'a été saisi manuellement sur la ligne, le tableau de bord utilise par défaut l'**économie annuelle attendue** comme valeur de forecast.
La ligne "ne forecast pas zéro" — elle forecast le plan.

### 1.6 Cutoff YTD

Le cutoff YTD est le **minimum entre aujourd'hui et la fin de l'exercice** :
- Pour l'exercice en cours : cutoff = date du jour
- Pour un exercice passé : cutoff = dernier jour de l'exercice (30 nov.)

---

## 2. Indicateurs P1 — Exécution

Ces quatre KPIs mesurent la **performance de livraison** sur l'exercice sélectionné.

---

### 2.1 Forecast Fin d'Exercice (EOY Forecast)

| | |
|---|---|
| **Libellé UI** | EOY Forecast |
| **Dimension** | Forecast |

**Définition** : Projection des économies totales que l'ensemble des lignes actives va générer sur l'exercice, d'ici au 30 novembre.

**Formule** :
```
EOY Forecast = Σ forecast_eoy_current(ligne) × taux_EUR(ligne)
               pour toutes les lignes actives du FY
```

Si `forecast_eoy_current` est null sur une ligne, la valeur de repli est `expected_annual_saving`.

**Sous-libellé** : *N lignes financières actives* — nombre de lignes dont la période chevauche le FY sélectionné.

---

### 2.2 EOY vs Budget

| | |
|---|---|
| **Libellé UI** | EOY vs Budget |
| **Dimension** | Forecast |

**Définition** : Rapport entre le forecast EOY et l'économie annuelle attendue, **pour les seules opportunités engagées en budget (statut "Budgeted")**.

**Formule** :
```
EOY vs Budget (%) = Σ EOY_forecast(lignes budgétées) / Σ expected_annual_saving(lignes budgétées) × 100
```

> ⚠️ Le dénominateur est l'**économie annuelle attendue** des lignes budgétées, **pas** le montant applicable pro-rata du budget (`applicable_amount`). Diviser un forecast annuel par un montant partiel d'exercice produirait des pourcentages supérieurs à 200-600%, ce qui n'est pas pertinent.

**Interprétation** :
- = 100% → le forecast confirme exactement l'engagement budget
- > 100% → surperformance prévisionnelle
- < 100% → risque de non-atteinte du budget

**Sous-libellé** : *Budget engagé FY N : X €* — somme des `applicable_amount` des opportunités budgétées pour l'exercice.

---

### 2.3 Réalisé YTD (Actual YTD)

| | |
|---|---|
| **Libellé UI** | Actual YTD |
| **Dimension** | YTD |

**Définition** : Cumul des économies réellement réalisées depuis le début de l'exercice jusqu'au cutoff YTD.

**Formule** :
```
Actual YTD = Σ actual_saving(ligne, mois) × taux_EUR(ligne)
             pour toutes les lignes actives,
             pour les mois [démarrage ligne ≤ mois ≤ cutoff YTD]
             et mois dans le FY sélectionné
```

**Sous-libellé** : *vs attendu X €* — économie YTD attendue sur la même fenêtre (mêmes filtres de mois).

**Barre de progression** : Réalisé YTD / Attendu YTD × 100 (voir indicateur suivant).

---

### 2.4 Réalisé vs Budget YTD

| | |
|---|---|
| **Libellé UI** | Actual vs Budget YTD |
| **Dimension** | YTD |

**Définition** : Taux de réalisation YTD, calculé **uniquement sur les lignes des opportunités budgétées** (statut "Budgeted").

**Formule** :
```
Actual vs Budget YTD (%) = Σ actual_saving(lignes budgétées, mois ≤ cutoff)
                           / Σ expected_saving(lignes budgétées, mois ≤ cutoff) × 100
```

**Différence avec Actual YTD** :
- "Actual YTD" inclut toutes les lignes actives (budgétées ou non)
- "Actual vs Budget YTD" se restreint aux seules lignes dont l'opportunité est engagée budget — c'est la mesure de performance contre l'engagement formel

---

## 3. Indicateurs P2 — Pipeline

Ces KPIs mesurent la **taille et la qualité du portefeuille d'opportunités**.

---

### 3.1 Économies Annuelles — Budgétées

| | |
|---|---|
| **Libellé UI** | Est. Annual Saving — Budgeted |
| **Dimension** | Pipeline |

**Définition** : Somme des économies annuelles attendues des lignes dont l'opportunité est en statut "Budgeted" pour l'exercice sélectionné.

**Formule** :
```
Budgeted Expected Annual = Σ expected_annual_saving(ligne) × taux_EUR
                           pour lignes où opportunity.budget_status[FY] = "Budgeted"
```

**Sous-libellé** : *Budget engagé FY N : X €* — montant pro-rata (`applicable_amount`) de l'engagement.

---

### 3.2 Valeur Programme — Durée Totale

| | |
|---|---|
| **Libellé UI** | Program Value — Lifetime |
| **Dimension** | Pipeline |

**Définition** : Valeur cumulée des économies sur toute la durée de vie des lignes actives (multi-années). Représente la valeur totale du portefeuille en cours.

**Formule** :
```
Program Value = Σ expected_annual_saving(ligne) × duration_months(ligne) / 12 × taux_EUR
                pour toutes les lignes actives
```

**Usage** : Indicateur de richesse du portefeuille — ne pas confondre avec l'économie d'un exercice donné.

---

### 3.3 Taux de Conversion

| | |
|---|---|
| **Libellé UI** | Conversion Rate |
| **Dimension** | Effectiveness |

**Définition** : Part des opportunités validées (décision "Go") qui ont effectivement généré des économies réelles (`cumulated_real_saving > 0`).

**Formule** :
```
Taux de Conversion (%) = Nb opportunités validées avec réalisé > 0
                         / Nb total d'opportunités validées (Go, non Closed) × 100
```

**Interprétation** : Mesure la transformation effective des opportunités validées en économies tangibles. Un taux élevé indique une bonne exécution post-validation.

---

### 3.4 Taux de Go Phase 0

| | |
|---|---|
| **Libellé UI** | Phase 0 Go Rate |
| **Dimension** | Efficiency |

**Définition** : Part des opportunités ayant fait l'objet d'une décision de gate (Go / No Go / Review) qui ont reçu un "Go".

**Formule** :
```
Taux Go Phase 0 (%) = Nb opportunités avec validation_decision = "Go"
                      / Nb opportunités avec une décision (Go + No Go + Review) × 100
```

**Usage** : Mesure la qualité de la sélection des opportunités. Un taux trop élevé (> 95%) peut signaler un manque de sélectivité ; un taux trop faible (< 50%) peut indiquer des études STP insuffisantes en amont.

---

## 4. Indicateurs secondaires

Ces quatre tuiles complètent le tableau de bord avec des dimensions de gouvernance et de qualité des données.

---

### 4.1 Couverture Mise à Jour Mensuelle

**Définition** : Pourcentage de lignes actives ayant une valeur `actual_saving` renseignée pour le mois en cours.

**Formule** :
```
Mise à jour mensuelle (%) = Nb lignes actives avec actual_saving[mois courant] non null
                            / Nb total de lignes actives × 100
```

**Cible** : 100% — toutes les lignes doivent être mises à jour chaque mois.

**Anneau** : vert si 100%, orange si ≥ 80%, rouge si < 80%.

---

### 4.2 Score de Priorité Moyen

**Définition** : Moyenne des scores de priorité (`priority_score`) des opportunités non clôturées ayant un score renseigné.

**Formule** :
```
Score moyen = Σ priority_score(opp) / Nb opportunités avec score
```

**Échelle** : 0 à 125 points. L'anneau représente le score/125 × 100%.

---

### 4.3 Surperformance Forecast

**Définition** : Nombre et montant des lignes budgétées dont le forecast EOY dépasse l'économie annuelle attendue (situation favorable).

**Formule** :
```
Surperformance = lignes budgétées telles que
                 forecast_eoy × taux_EUR > expected_annual_saving × taux_EUR

Montant surperformance = Σ (forecast_eoy - expected_annual_saving) × taux_EUR
                          sur ces lignes
```

**Interprétation** : Une surperformance forecast est un signal positif — les lignes "sur-livrent" par rapport à leur engagement initial.

---

### 4.4 Alertes Actives

Trois compteurs d'anomalies :

| Alerte | Définition |
|--------|-----------|
| **Escaladed** | Lignes financières marquées comme escaladées (champ `is_escalated = True`) |
| **Late projects** | Projets en statut `"Late"` |
| **Missing updates** | Lignes actives sans `actual_saving` pour les mois passés de l'exercice |

---

## 5. Onglet Économies Mensuelles

### 5.1 Graphique barres mensuelles

Le graphique affiche, pour chaque mois du FY sélectionné, deux barres :

| Barre | Couleur | Valeur |
|-------|---------|--------|
| Attendue (référence) | Indigo clair | `Σ expected_saving` de toutes les lignes actives pour ce mois |
| Réalisée (overlay) | Vert si ≥ 100%, Ambre si < 100% | `Σ actual_saving` pour ce mois |

**Calcul de l'attendu mensuel** :
```
Attendu(mois M) = Σ (expected_annual_saving / duration_months) × taux_EUR
                  pour les lignes actives dont M ≥ démarrage et M dans le FY
```

Les barres futures (mois > aujourd'hui) n'ont pas de barre réalisée.

### 5.2 Tableau mensuel détaillé

| Colonne | Formule |
|---------|---------|
| Expected | `Σ expected_saving` du mois |
| Actual | `Σ actual_saving` du mois (— si absent) |
| Delta | `Actual − Expected` |
| Rate | `Actual / Expected × 100` |
| EOY Fcst | `Σ forecast_eoy_saving` renseigné pour ce mois |

### 5.3 Attribution par Année Calendaire

Pour les lignes dont la durée chevauche plusieurs années civiles, les économies attendues sont ventilées mois par mois : chaque mois reçoit `annual/duration` et est imputé à son année civile.

---

## 6. Onglet Par Usine

### Vue graphique — EOY par statut budget

Graphique en barres empilées : chaque barre = l'EOY forecast total de l'usine, décomposé par statut budget des opportunités.

| Couleur | Statut |
|---------|--------|
| Indigo | Budgeted — engagé au budget formel |
| Ambre | Opportunity — identifié, non encore budgété |
| Indigo clair | Empty — pas encore de statut budget |

### Métriques par usine

| KPI | Formule |
|-----|---------|
| **YTD On-Track (%)** | `Actual YTD usine / Expected YTD usine × 100` |
| **Delta YTD** | `Actual YTD usine − Expected YTD usine` |
| **Actual YTD** | `Σ actual_saving` des lignes de l'usine (mois ≤ cutoff, dans FY) |
| **EOY Forecast** | `Σ forecast_eoy_current` des lignes actives de l'usine |
| **EOY vs Budget (%)** | `Σ EOY_forecast(lignes budgétées usine) / Σ expected_annual(lignes budgétées usine) × 100` |
| **Expected Annual** | `Σ expected_annual_saving` de toutes les lignes actives de l'usine |

> Le **YTD On-Track** compare le réalisé au **YTD attendu** (ce qui aurait dû être livré jusqu'à aujourd'hui), pas à l'annuel complet — ce qui rend la métrique comparable quel que soit le moment de l'année.

---

## 7. Onglet Par Type

Les types d'opportunités sont :

| Type | Palette |
|------|---------|
| **Negotiation** | Violet |
| **Sourcing** | Bleu ciel |
| **Technical Productivity** | Vert |
| **Cash** | Ambre |

### Métriques par type

| KPI | Formule |
|-----|---------|
| **YTD On-Track (%)** | `Actual YTD type / Expected YTD type × 100` |
| **Delta YTD** | `Actual YTD − Expected YTD` |
| **Actual YTD** | `Σ actual_saving` des lignes de ce type (mois ≤ cutoff) |
| **EOY Forecast** | `Σ forecast_eoy_current` des lignes de ce type |
| **Annual Pipeline** | `Σ expected_annual_saving` du type |

Le **taux de validation** affiché dans l'en-tête de chaque carte est :
```
Taux validation = Nb opps validées (décision Go) / Nb total d'opps du type × 100
```

---

## 8. Onglet Par Acheteur

### 8.1 Définition du champ "Acheteur"

> ⚠️ **Point important** : "Par Acheteur" groupe par le champ **`idea_owner`** de l'opportunité — c'est le **porteur de l'idée**, la personne qui a identifié et initié l'opportunité.

Ce n'est **pas** :
- le `purchasing_owner` (responsable de l'exécution achats)
- le `follower` de la ligne financière (suivi opérationnel de la ligne)

**Conséquence** : si une opportunité est identifiée par un acheteur A mais exécutée par un acheteur B, la performance financière est créditée à A. Si cette distinction est importante pour votre reporting, envisagez d'ajouter une vue "Par Responsable d'Exécution" basée sur `purchasing_owner`.

### 8.2 Métriques par acheteur

Identiques aux métriques par usine, mais filtrées sur les lignes dont `opp.idea_owner = acheteur`.

| KPI | Formule |
|-----|---------|
| **YTD On-Track (%)** | `Actual YTD acheteur / Expected YTD acheteur × 100` |
| **Delta YTD** | `Actual YTD − Expected YTD` |
| **Actual YTD** | `Σ actual_saving` des lignes portées par cet acheteur |
| **EOY Forecast** | `Σ forecast_eoy_current` des lignes de cet acheteur |
| **EOY vs Budget (%)** | `Σ EOY(lignes budgétées acheteur) / Σ expected_annual(lignes budgétées acheteur) × 100` |
| **Expected Annual** | `Σ expected_annual_saving` toutes lignes de l'acheteur |

### 8.3 Compteurs de synthèse (bandeau haut)

| Tuile | Calcul |
|-------|--------|
| Acheteurs actifs | Nb d'acheteurs ayant au moins une ligne active dans le FY |
| Expected Annual total | `Σ expected_annual_saving` de tous les acheteurs |
| EOY Forecast total | `Σ eoy_forecast` de tous les acheteurs |
| Escalades totales | `Σ escalated_count` de tous les acheteurs |

---

## 9. Onglet Alertes

### 9.1 Lignes Escaladées

Toutes les lignes financières actives dont le champ `is_escalated` est vrai, avec :
- le nom de l'opportunité
- le motif d'escalade (`escalation_reason`)
- le Delta YTD (pour contextualiser la gravité)

### 9.2 Projets en retard

Projets en statut `"Late"` (les statuts possibles sont : `On time`, `Late`, `On hold`).
Affichés avec le nom du projet, le responsable, la phase et la date de fin planifiée.

### 9.3 Mises à jour manquantes

Lignes actives dont des mois passés de l'exercice n'ont pas de valeur `actual_saving`.

**Calcul** :
```
Mois manquants = {mois m | démarrage_ligne ≤ m ≤ cutoff YTD ET actual_saving[m] IS NULL}
```

Seules les 10 premières lignes sont affichées pour ne pas saturer l'interface.

---

## 10. Définitions clés

### Statut Budget d'une opportunité (par exercice)

Chaque opportunité possède un statut budget **par exercice budgétaire**, géré via la page Budgeting :

| Statut | Signification |
|--------|---------------|
| **Empty** | Aucune décision budget pour cet exercice |
| **Opportunity** | Identifiée comme potentielle, non encore engagée |
| **Budgeted** | Formellement engagée dans le budget de l'exercice |

Ce statut est distinct de la phase de maturité de l'opportunité (Phase 0 à Phase 4).

### Code couleur global des taux de réalisation

| Seuil | Couleur | Signification |
|-------|---------|---------------|
| ≥ 100% | Vert (emerald) | Sur la cible ou au-dessus |
| ≥ 85% | Orange (amber) | Légèrement en retard — surveillance |
| < 85% | Rouge (rose) | Risque avéré — action requise |

### Filtres multidimensionnels

Les filtres (Site, Type, Acheteur) sont **cumulatifs** (ET logique). Ils s'appliquent sur :
- les lignes financières actives (pour tous les KPIs d'exécution)
- les opportunités (pour les compteurs de pipeline)

Les options de filtre affichées sont toujours construites **avant** l'application des filtres — tous les choix restent visibles même si la combinaison sélectionnée n'a aucun résultat.

### Couverture multi-devises

| Devise | Traitement |
|--------|-----------|
| EUR | Taux = 1 (aucune conversion) |
| USD, RMB, INR… | Multiplié par `fx_rate_to_eur` de l'opportunité |
| Devise non-EUR + taux absent | Compté à 1:1, bannière d'avertissement affichée |

---

*Document généré à partir du code source `kpi_service.py` — Avocarbon Purchasing Intelligence, juin 2026.*
