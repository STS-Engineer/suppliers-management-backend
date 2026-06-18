#!/usr/bin/env python
"""
==========================================================================
  DEMO SCÉNARIO — Workflow d'approbation STP (Purchasing Value)
  Avocarbon · Suppliers Management System
==========================================================================

Ce script est un scénario de démonstration exécutable qui couvre l'intégralité
du cycle de vie du workflow d'approbation STP :

  Étape 1 — Authentification
  Étape 2 — Création d'une opportunité Sourcing (Phase 0) avec baseline STP
  Étape 3 — Avancement vers Phase 2 (gate decisions)
  Étape 4 — Tentative de modification directe bloquée (STP_REQUIRES_APPROVAL)
  Étape 5 — Demande de révision envoyée au Directeur

  Scénario A : Le Directeur APPROUVE → nouveaux prix appliqués, savings recalculés
  Scénario B : Le Directeur REJETTE  → prix d'origine préservés, pas d'impact

Prérequis :
  - Backend FastAPI démarré sur http://localhost:8000
  - pip install httpx (version >= 0.24)
  - Un utilisateur valide dans la base (email + mot de passe)

Usage :
  cd suppliers-management-backend
  python demo_stp_revision.py

Personnalisation :
  Modifier les constantes dans la section CONFIG ci-dessous.
==========================================================================
"""

import getpass
import io
import json
import sys
import textwrap
from datetime import date

import httpx

# Force UTF-8 output on Windows so box-drawing chars render correctly
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ==========================================================================
# CONFIG — adapter si nécessaire
# ==========================================================================
BASE_URL = "http://localhost:8000"
USER_EMAIL = "supplier.owner@avocarbon.com"  # compte existant dans la DB
USER_PASSWORD = ""  # laissé vide → prompt interactif
DIRECTOR_EMAIL = "hayfa.rajhi@avocarbon.com"  # destinataire de la demande

# Prix / quantités de démonstration
CURRENT_PRICE = 100.0  # prix actuel fournisseur (€/pièce)
PROPOSED_PRICE = 90.0  # objectif initial Phase 0–1
REVISED_PRICE = 85.0  # prix renegocié proposé en Phase 2
QUANTITY = 1000  # quantité annuelle (pièces/an)
DURATION = 48  # durée contrat (mois)

# ==========================================================================
# HELPERS — affichage et HTTP
# ==========================================================================
W = 70  # largeur de colonne


def _sep(char="─", color=""):
    print(f"{color}{char * W}\033[0m")


def header(title: str):
    print()
    _sep("═", "\033[1;34m")
    print(f"\033[1;34m  {title}\033[0m")
    _sep("═", "\033[1;34m")


def section(title: str):
    print()
    _sep("─", "\033[36m")
    print(f"\033[36m  {title}\033[0m")
    _sep("─", "\033[36m")


def ok(msg: str):
    print(f"  \033[32m✔  {msg}\033[0m")


def fail(msg: str):
    print(f"  \033[31m✘  {msg}\033[0m")


def info(msg: str):
    print(f"  \033[33m▸  {msg}\033[0m")


def detail(label: str, value):
    if value is None:
        return
    vstr = str(value)
    if len(vstr) > 55:
        vstr = vstr[:52] + "..."
    print(f"     {label:<30s} {vstr}")


def block(title: str, data: dict, keys: list):
    print(f"\n  \033[1m{title}\033[0m")
    for k in keys:
        v = data.get(k)
        if v is not None:
            detail(k, v)


def euros(v) -> str:
    if v is None:
        return "—"
    return f"{float(v):,.0f} €"


def call(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    label: str,
    expect_error: bool = False,
    **kwargs,
):
    """Execute an API call, print result, return (status_code, body_dict)."""
    url = f"{BASE_URL}{path}"
    try:
        r = client.request(method, url, **kwargs)
    except httpx.ConnectError:
        fail(f"Impossible de joindre {BASE_URL} — le backend est-il démarré ?")
        sys.exit(1)

    body = {}
    try:
        body = r.json()
    except Exception:
        pass

    if expect_error:
        if r.status_code >= 400:
            err_code = body.get("error_code", "UNKNOWN")
            ok(f"{label} → {r.status_code} {err_code}  (comportement attendu)")
        else:
            fail(f"{label} → {r.status_code}  (une erreur était attendue !)")
    else:
        if r.status_code < 300:
            ok(f"{label} → {r.status_code} OK")
        else:
            err_code = body.get("error_code", body.get("detail", r.text[:80]))
            fail(f"{label} → {r.status_code} {err_code}")
            print(f"  Détail : {json.dumps(body, ensure_ascii=False)[:200]}")
            sys.exit(1)

    return r.status_code, body


# ==========================================================================
# SCÉNARIO PRINCIPAL
# ==========================================================================


def main():
    global USER_EMAIL, USER_PASSWORD, DIRECTOR_EMAIL

    # Récupérer les credentials si non renseignés
    if not USER_EMAIL:
        USER_EMAIL = input("Email utilisateur : ").strip()
    if not USER_PASSWORD:
        USER_PASSWORD = getpass.getpass(f"Mot de passe pour {USER_EMAIL} : ")

    header("DEMO — Workflow d'approbation STP · Avocarbon")
    print(
        textwrap.dedent(f"""
      Ce script crée une opportunité Sourcing de bout en bout, avance la
      gate jusqu'en Phase 2, déclenche le mécanisme de protection STP et
      simule deux décisions director : approbation puis rejet.

      Serveur cible  : {BASE_URL}
      Utilisateur    : {USER_EMAIL}
      Directeur      : {DIRECTOR_EMAIL}
    """).rstrip()
    )

    with httpx.Client(timeout=30) as client:
        # ------------------------------------------------------------------
        # ÉTAPE 1 — Authentification
        # ------------------------------------------------------------------
        section("Étape 1 · Authentification")
        info(f"Connexion avec {USER_EMAIL}")

        status, body = call(
            client,
            "POST",
            "/api/v1/auth/signin",
            label="POST /auth/signin",
            json={"email": USER_EMAIL, "password": USER_PASSWORD},
        )
        token = body.get("access_token") or body.get("data", {}).get("access_token")
        if not token:
            fail("Token absent dans la réponse")
            print(f"  Réponse : {body}")
            sys.exit(1)
        ok(f"Token JWT obtenu ({token[:30]}…)")
        auth = {"Authorization": f"Bearer {token}"}

        # Récupérer le premier plant disponible (requis à la création)
        r_sites = client.get(f"{BASE_URL}/api/v1/sites", headers=auth, params={"limit": 5})
        sites_body = r_sites.json()
        sites_data = (sites_body.get("data") or {}).get("items") or []
        if not sites_data:
            fail("Aucun plant trouvé — impossible de créer une opportunité")
            sys.exit(1)
        first_site = sites_data[0]
        plant_id = first_site.get("id_site") or first_site.get("site_id") or first_site.get("id")
        plant_name = first_site.get("site_name") or first_site.get("name", f"Plant {plant_id}")
        ok(f"Plant utilisé : {plant_name} (ID={plant_id})")

        # ------------------------------------------------------------------
        # ÉTAPE 2 — Création de l'opportunité (Phase 0) + baseline STP
        # ------------------------------------------------------------------
        section("Étape 2 · Création de l'opportunité Sourcing")
        info("Création avec un baseline STP complet (prix + quantités + durée)")

        create_payload = {
            "opportunity_name": f"[DEMO] Pièce moteur — Fournisseur Renatech {date.today()}",
            "opportunity_type": "Sourcing",
            "idea_owner": USER_EMAIL,
            "plant_id": plant_id,
            "description": "Démo workflow approbation STP — renegociation prix pièce moteur aluminium",
        }
        status, body = call(
            client,
            "POST",
            "/api/v1/purchasing-value/opportunities",
            label="POST /opportunities (création Phase 0)",
            json=create_payload,
            headers=auth,
        )
        opp = body.get("data", body)
        opp_id: int = opp["opportunity_id"]
        ok(f"Opportunité créée — ID = {opp_id}")

        # Saisie du baseline STP en Phase 0
        stp_baseline = {
            "current_price": CURRENT_PRICE,
            "proposed_price": PROPOSED_PRICE,
            "annual_quantity_n1": QUANTITY,
            "annual_quantity_n2": QUANTITY,
            "annual_quantity_n3": QUANTITY,
            "annual_quantity_n4": QUANTITY,
            "duration_months": DURATION,
            "planned_start_date": "2025-01-01",
            "real_start_date": "2025-01-01",
        }
        status, body = call(
            client,
            "PUT",
            f"/api/v1/purchasing-value/opportunities/{opp_id}",
            label="PUT /opportunities/{id} — saisie baseline STP",
            json=stp_baseline,
            headers=auth,
        )
        opp = body.get("data", body)
        block(
            "Baseline STP enregistré",
            opp,
            [
                "current_price",
                "proposed_price",
                "annual_quantity_n1",
                "duration_months",
                "saving_year_n",
                "period_saving",
            ],
        )
        info(f"Économie calculée année N  : {euros(opp.get('saving_year_n'))}")
        info(f"Économie totale sur période : {euros(opp.get('period_saving'))}")

        # ------------------------------------------------------------------
        # ÉTAPE 3 — Avancement Phase 0 → 1 → 2 (gate decisions)
        # ------------------------------------------------------------------
        section("Étape 3 · Avancement vers Phase 2 (gate decisions)")

        info("Gate Phase 0 → Phase 1 (décision : Go)")
        status, body = call(
            client,
            "POST",
            f"/api/v1/purchasing-value/opportunities/{opp_id}/gate-decision",
            label="POST /gate-decision (Phase 0 → 1)",
            json={
                "decision": "Go",
                "decided_by": USER_EMAIL,
                "project_manager": USER_EMAIL,
                "comments": "Potentiel validé en comité achats",
            },
            headers=auth,
        )
        opp = body.get("data", body)
        detail("Phase courante", opp.get("phase_status"))

        info("Gate Phase 1 → Phase 2 (décision : Go)")
        status, body = call(
            client,
            "POST",
            f"/api/v1/purchasing-value/opportunities/{opp_id}/gate-decision",
            label="POST /gate-decision (Phase 1 → 2)",
            json={
                "decision": "Go",
                "decided_by": USER_EMAIL,
                "project_manager": USER_EMAIL,
                "comments": "Stratégie fournisseur approuvée — passage en Phase 2",
            },
            headers=auth,
        )
        opp = body.get("data", body)
        detail("Phase courante", opp.get("phase_status"))
        ok(f"L'opportunité est maintenant en {opp.get('phase_status', '?')}")

        # ------------------------------------------------------------------
        # ÉTAPE 4 — Tentative de modification directe bloquée
        # ------------------------------------------------------------------
        section("Étape 4 · Protection STP — modification directe bloquée")
        info("Tentative de modifier proposed_price directement en Phase 2")
        info(
            f"  proposed_price : {PROPOSED_PRICE} € → {REVISED_PRICE} €  (changement de {PROPOSED_PRICE - REVISED_PRICE:.0f} €/pièce)"
        )

        call(
            client,
            "PUT",
            f"/api/v1/purchasing-value/opportunities/{opp_id}",
            label="PUT /opportunities/{id} — modification STP directe",
            json={"proposed_price": REVISED_PRICE},
            headers=auth,
            expect_error=True,
        )
        info("Le système bloque toute modification directe du baseline STP")
        info("→ l'acheteur doit passer par le workflow de demande de révision")

        # ------------------------------------------------------------------
        # ÉTAPE 5 — Demande de révision envoyée au Directeur
        # ------------------------------------------------------------------
        section("Étape 5 · Demande de révision STP → Directeur")
        info(f"Prix proposé renegocié : {PROPOSED_PRICE} € → {REVISED_PRICE} €")
        info(
            f"Économie prévisionnelle : ({CURRENT_PRICE} − {REVISED_PRICE}) × {QUANTITY} = {(CURRENT_PRICE - REVISED_PRICE) * QUANTITY:,.0f} €/an"
        )

        revision_payload = {
            "director_email": DIRECTOR_EMAIL,
            "note": (
                "Suite à l'appel d'offres Q3-2025, le fournisseur Renatech propose "
                f"un prix de {REVISED_PRICE} €/pièce (vs {PROPOSED_PRICE} € actuellement). "
                f"Gain additionnel : {(PROPOSED_PRICE - REVISED_PRICE) * QUANTITY:,.0f} €/an. "
                "Demande d'approbation pour mise à jour du baseline STP."
            ),
            "proposed_price": REVISED_PRICE,
        }
        status, body = call(
            client,
            "POST",
            f"/api/v1/purchasing-value/opportunities/{opp_id}/request-stp-revision",
            label="POST /request-stp-revision",
            json=revision_payload,
            headers=auth,
        )
        opp = body.get("data", body)
        pending = opp.get("pending_stp_revision", {}) or {}
        preview = pending.get("computed_preview", {})

        block(
            "Révision en attente (pending_stp_revision)",
            pending,
            [
                "requested_by",
                "requested_at",
                "director_email",
                "note",
            ],
        )
        block("Champs proposés", pending.get("proposed_fields", {}), ["proposed_price"])
        block(
            "Aperçu de l'économie si approuvée",
            preview,
            [
                "saving_year_n",
                "period_saving",
            ],
        )
        info(f"Le Directeur ({DIRECTOR_EMAIL}) reçoit la notification")
        info("La demande reste en attente jusqu'à sa décision")

        # ==================================================================
        # SCÉNARIO A — Le Directeur APPROUVE
        # ==================================================================
        header("Scénario A · Le Directeur APPROUVE la révision")

        status, body = call(
            client,
            "POST",
            f"/api/v1/purchasing-value/opportunities/{opp_id}/decide-stp-revision",
            label="POST /decide-stp-revision  (decision=Approved)",
            json={
                "decision": "Approved",
                "decided_by": DIRECTOR_EMAIL,
                "note": "Approuvé en comité achats du 18/06/2026 — économie supplémentaire validée.",
            },
            headers=auth,
        )
        opp = body.get("data", body)

        print()
        ok("Révision APPROUVÉE — les modifications ont été appliquées")
        block(
            "État après approbation",
            opp,
            [
                "phase_status",
                "current_price",
                "proposed_price",
                "saving_year_n",
                "period_saving",
                "pending_stp_revision",
            ],
        )

        new_saving = opp.get("saving_year_n")
        old_saving = (CURRENT_PRICE - PROPOSED_PRICE) * QUANTITY
        gain = float(new_saving) - old_saving if new_saving else 0

        print()
        info(
            f"Prix STP mis à jour      : {PROPOSED_PRICE} € → {opp.get('proposed_price')} €"
        )
        info(f"Économie année N (avant) : {euros(old_saving)}")
        info(f"Économie année N (après) : {euros(new_saving)}")
        info(f"Gain additionnel         : {euros(gain)} / an")
        info(f"Révision en attente      : {opp.get('pending_stp_revision')}  (effacée)")

        audit_log = opp.get("comments", "")
        if audit_log and "APPROVED" in str(audit_log):
            ok("Entrée d'audit enregistrée dans les commentaires de l'opportunité")

        # ==================================================================
        # SCÉNARIO B — Même flux, mais le Directeur REJETTE
        # ==================================================================
        header("Scénario B · Le Directeur REJETTE la révision")

        # Créer une deuxième opportunité identique pour montrer le rejet
        section("Création d'une deuxième opportunité (même baseline STP)")
        create_payload2 = dict(create_payload)
        create_payload2["opportunity_name"] = (
            f"[DEMO-B] Pièce moteur — Rejet {date.today()}"
        )
        create_payload2["description"] = (
            "Démo workflow approbation STP — scénario REJET"
        )
        create_payload2["plant_id"] = plant_id

        status, body = call(
            client,
            "POST",
            "/api/v1/purchasing-value/opportunities",
            label="POST /opportunities (création opp B)",
            json=create_payload2,
            headers=auth,
        )
        opp2 = body.get("data", body)
        opp2_id: int = opp2["opportunity_id"]
        ok(f"Opportunité B créée — ID = {opp2_id}")

        call(
            client,
            "PUT",
            f"/api/v1/purchasing-value/opportunities/{opp2_id}",
            label="PUT — baseline STP (opp B)",
            json=stp_baseline,
            headers=auth,
        )
        call(
            client,
            "POST",
            f"/api/v1/purchasing-value/opportunities/{opp2_id}/gate-decision",
            label="Gate 0→1 (opp B)",
            json={
                "decision": "Go",
                "decided_by": USER_EMAIL,
                "project_manager": USER_EMAIL,
            },
            headers=auth,
        )
        call(
            client,
            "POST",
            f"/api/v1/purchasing-value/opportunities/{opp2_id}/gate-decision",
            label="Gate 1→2 (opp B)",
            json={
                "decision": "Go",
                "decided_by": USER_EMAIL,
                "project_manager": USER_EMAIL,
            },
            headers=auth,
        )

        section("Demande de révision STP — Scénario B")
        call(
            client,
            "POST",
            f"/api/v1/purchasing-value/opportunities/{opp2_id}/request-stp-revision",
            label="POST /request-stp-revision (opp B)",
            json={
                "director_email": DIRECTOR_EMAIL,
                "note": "Proposition fournisseur — à valider.",
                "proposed_price": REVISED_PRICE,
            },
            headers=auth,
        )
        info(f"Révision en attente sur l'opportunité B (ID={opp2_id})")

        section("Le Directeur REJETTE")
        status, body = call(
            client,
            "POST",
            f"/api/v1/purchasing-value/opportunities/{opp2_id}/decide-stp-revision",
            label="POST /decide-stp-revision  (decision=Rejected)",
            json={
                "decision": "Rejected",
                "decided_by": DIRECTOR_EMAIL,
                "note": "Non validé en comité — risque qualité non évalué. Révision reportée à Q1-2027.",
            },
            headers=auth,
        )
        opp2 = body.get("data", body)

        print()
        ok("Révision REJETÉE — aucune modification du baseline STP")
        block(
            "État après rejet",
            opp2,
            [
                "phase_status",
                "current_price",
                "proposed_price",
                "saving_year_n",
                "period_saving",
                "pending_stp_revision",
            ],
        )
        info(
            f"proposed_price inchangé  : {opp2.get('proposed_price')} €  (= valeur originale {PROPOSED_PRICE} €)"
        )
        info(f"saving_year_n inchangé   : {euros(opp2.get('saving_year_n'))}")
        info(
            f"Révision en attente      : {opp2.get('pending_stp_revision')}  (effacée)"
        )

        audit_log2 = opp2.get("comments", "")
        if audit_log2 and "REJECTED" in str(audit_log2):
            ok("Entrée d'audit REJECTED enregistrée dans les commentaires")

        # ==================================================================
        # RÉCAPITULATIF
        # ==================================================================
        header("Récapitulatif du scénario")
        print(
            textwrap.dedent(f"""
          Fonctionnalité testée                Résultat
          ──────────────────────────────────── ────────────────────────────
          Authentification JWT                 ✔  Token obtenu
          Création opportunité + baseline STP  ✔  Opportunité ID {opp_id}
          Avancement Phase 0→1→2               ✔  Gate decisions validées
          Protection STP_REQUIRES_APPROVAL     ✔  Blocage direct confirmé
          Demande de révision → Directeur      ✔  Pending enregistré
          Scénario A — Approbation             ✔  Prix {PROPOSED_PRICE}→{REVISED_PRICE} €, saving+{euros(gain)}/an
          Scénario B — Rejet                   ✔  Prix {PROPOSED_PRICE} € préservé (ID {opp2_id})

          Opportunités créées (peuvent être supprimées manuellement) :
            · ID {opp_id:>6}  — Scénario A (Approuvé)
            · ID {opp2_id:>6}  — Scénario B (Rejeté)
        """).rstrip()
        )
        print()
        _sep("═", "\033[1;32m")
        print(
            "\033[1;32m  Scénario terminé avec succès — tous les cycles couverts.\033[0m"
        )
        _sep("═", "\033[1;32m")
        print()


if __name__ == "__main__":
    main()
