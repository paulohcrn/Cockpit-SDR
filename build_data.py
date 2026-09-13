#!/usr/bin/env python3
"""
build_data.py — collecte HubSpot -> public/data.json + public/csv pour le dashboard SDR.

Déclenché à la demande (bouton du dashboard ou GitHub Actions). Stdlib uniquement.
Les SDR collectés sont lus dans sdrs.json.

    export HUBSPOT_PAT="pat-..."
    python3 build_data.py
"""

import argparse
import csv
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "https://api.hubapi.com"
PORTAL_ID = os.environ.get("HUBSPOT_PORTAL_ID", "00000000")

# Les SDR affichés viennent de sdrs.json (emails ou owner IDs). Cette liste ne sert
# que de repli si le fichier est absent ou illisible.
DEFAULT_SDRS = [
    "alex.martin@example.com",
    "sam.dubois@example.com",
]
SDRS_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sdrs.json")

NRP_STAGES = ["NRP0", "NRP1", "NRP2", "NRP3", "NRP4", "NRP5", "NRP6", "NRP7"]
ACTIVATION_STAGE = "En activation"

R1_STAGE = "appointmentscheduled"       # "R1 - Appointment Scheduled (20%)"
NO_SHOW_STAGE = "555471297"             # "No Show"
SALES_PIPELINE = "default"              # "Pipeline des ventes"

# Seuil d'affichage d'une campagne dans la ventilation publicitaire (strictement plus de).
MIN_LEADS_PUB = 10

# Sources considérées comme "lead entrant" (par opposition aux imports/scraping OFFLINE).
INBOUND_SOURCES = {
    "PAID_SOCIAL", "PAID_SEARCH", "ORGANIC_SEARCH", "SOCIAL_MEDIA",
    "DIRECT_TRAFFIC", "REFERRALS", "EMAIL_MARKETING", "OTHER_CAMPAIGNS",
}

try:
    from zoneinfo import ZoneInfo
    PARIS = ZoneInfo("Europe/Paris")
except Exception:  # tzdata absent (Windows sans le paquet) -> UTC, décalage d'1-2h sur les bornes
    PARIS = dt.timezone.utc


# --- HTTP -----------------------------------------------------------------

def _token():
    tok = os.environ.get("HUBSPOT_PAT") or os.environ.get("HUBSPOT_TOKEN")
    if not tok:
        sys.exit('ERREUR : export HUBSPOT_PAT="pat-..." avant de lancer le script.')
    return tok


def _echec(path, err, strict):
    """Sortie en erreur, ou avertissement + None quand l'appel est optionnel.

    `strict=False` sert aux appels dont l'absence dégrade l'affichage sans le
    casser (la liste webinar, cf. `webinar_contact_ids`) : un scope manquant sur
    la private app ne doit pas faire échouer toute la collecte.
    """
    detail = err.read().decode(errors="replace")
    if strict:
        sys.exit(f"ERREUR HubSpot {err.code} sur {path}\n{detail}")
    print(f"ATTENTION : HubSpot {err.code} sur {path}, appel ignoré.\n{detail}",
          file=sys.stderr)
    return None


def _post(path, body, _retries=5, strict=True):
    data = json.dumps(body).encode()
    req = urllib.request.Request(API + path, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {_token()}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code in (429, 502, 503, 504) and _retries > 0:
            time.sleep(3)
            return _post(path, body, _retries - 1, strict)
        return _echec(path, e, strict)


def _get(path, _retries=5, strict=True):
    req = urllib.request.Request(API + path, method="GET")
    req.add_header("Authorization", f"Bearer {_token()}")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code in (429, 502, 503, 504) and _retries > 0:
            time.sleep(3)
            return _get(path, _retries - 1, strict)
        return _echec(path, e, strict)


# --- Résolution des SDR ---------------------------------------------------

def fetch_owners():
    """Tous les owners HubSpot, indexés par ID et par email (minuscules)."""
    index, after = {}, None
    while True:
        path = "/crm/v3/owners/?limit=100" + (f"&after={after}" if after else "")
        res = _get(path)
        for o in res.get("results", []):
            name = " ".join(x for x in [o.get("firstName"), o.get("lastName")] if x).strip()
            entry = {"id": str(o.get("id")),
                     "name": name or o.get("email") or str(o.get("id")),
                     "email": (o.get("email") or "").strip().lower()}
            index[entry["id"]] = entry
            if o.get("email"):
                index[o["email"].strip().lower()] = entry
        after = res.get("paging", {}).get("next", {}).get("after")
        if not after:
            return index
        time.sleep(0.15)


def resolve_sdrs():
    """Lit sdrs.json et résout chaque entrée (email ou ID) en {id, name}."""
    entries = DEFAULT_SDRS
    if os.path.exists(SDRS_CONFIG):
        try:
            with open(SDRS_CONFIG, encoding="utf-8") as f:
                loaded = json.load(f).get("sdrs")
            if isinstance(loaded, list) and loaded:
                entries = loaded
            else:
                print(f"ATTENTION : {SDRS_CONFIG} ne contient pas de liste 'sdrs' "
                      "non vide, repli sur la liste par défaut.", file=sys.stderr)
        except (json.JSONDecodeError, OSError) as e:
            print(f"ATTENTION : {SDRS_CONFIG} illisible ({e}), repli sur la liste "
                  "par défaut.", file=sys.stderr)

    owners = fetch_owners()
    resolved, ignores, seen = [], [], set()
    for raw in entries:
        owner = owners.get(str(raw).strip().lower())
        if not owner:
            print(f"ATTENTION : SDR '{raw}' introuvable dans les owners HubSpot, ignoré. "
                  "Vérifier l'orthographe de l'email ou l'owner ID.", file=sys.stderr)
            ignores.append(str(raw))
            continue
        if owner["id"] in seen:
            continue
        seen.add(owner["id"])
        resolved.append(owner)

    if not resolved:
        sys.exit("ERREUR : aucun SDR résolu. Corriger sdrs.json avant de relancer.")
    # `entries` (la liste brute) et `ignores` remontent dans data.json pour que le
    # dashboard puisse afficher une entrée fautive au lieu de la faire disparaître.
    return resolved, [str(e) for e in entries], ignores


def search(obj, filter_groups, properties=None, limit=100, sorts=None):
    """Itère sur toutes les pages d'une recherche CRM."""
    after, out = None, []
    while True:
        body = {"filterGroups": filter_groups, "limit": limit}
        if properties:
            body["properties"] = properties
        if sorts:
            body["sorts"] = sorts
        if after:
            body["after"] = after
        res = _post(f"/crm/v3/objects/{obj}/search", body)
        out.extend(res.get("results", []))
        after = res.get("paging", {}).get("next", {}).get("after")
        if not after:
            return out
        time.sleep(0.15)


def count(obj, filter_groups):
    """Nombre total de records correspondant aux filtres (1 seul appel)."""
    res = _post(f"/crm/v3/objects/{obj}/search", {"filterGroups": filter_groups, "limit": 1})
    time.sleep(0.1)
    return res.get("total", 0)


def batch_assoc(from_obj, to_obj, ids):
    out = {}
    for i in range(0, len(ids), 100):
        res = _post(f"/crm/v4/associations/{from_obj}/{to_obj}/batch/read",
                    {"inputs": [{"id": x} for x in ids[i:i + 100]]})
        for r in res.get("results", []):
            src = r.get("from", {}).get("id")
            tos = [t.get("toObjectId") for t in r.get("to", [])]
            if src and tos:
                out[str(src)] = [str(t) for t in tos]
        time.sleep(0.15)
    return out


def batch_read(obj, ids, props):
    out = {}
    ids = list({str(x) for x in ids})
    for i in range(0, len(ids), 100):
        res = _post(f"/crm/v3/objects/{obj}/batch/read",
                    {"properties": props, "inputs": [{"id": x} for x in ids[i:i + 100]]})
        for r in res.get("results", []):
            out[str(r["id"])] = r.get("properties", {})
        time.sleep(0.15)
    return out


# --- Dates ----------------------------------------------------------------

def periods(now):
    """Bornes semaine (lundi->dimanche) et mois courant, en ms epoch."""
    today = now.date()
    week_start = dt.datetime.combine(today - dt.timedelta(days=today.weekday()),
                                     dt.time.min, tzinfo=PARIS)
    week_end = week_start + dt.timedelta(days=7)
    month_start = dt.datetime.combine(today.replace(day=1), dt.time.min, tzinfo=PARIS)
    nxt = (month_start + dt.timedelta(days=32)).replace(day=1)
    return {
        "week": (week_start, week_end),
        "month": (month_start, nxt),
    }


def ms(d):
    return str(int(d.timestamp() * 1000))


def parse_hs_date(v):
    if not v:
        return None
    s = str(v)
    if s.isdigit():
        return dt.datetime.fromtimestamp(int(s) / 1000, tz=dt.timezone.utc).astimezone(PARIS)
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(PARIS)
    except ValueError:
        return None


def in_range(d, span):
    return d is not None and span[0] <= d < span[1]


# --- Rubrique 1 : performances R1 ----------------------------------------

DEAL_PROPS = [
    "dealname", "dealstage", "pipeline", "hubspot_owner_id",
    "co_proprietaire_de_la_transaction", "hs_v2_date_entered_appointmentscheduled",
    "date_et_heure_du_r1", "r1_statut",
]


def collect_r1(spans):
    """Deals entrés en R1 ce mois-ci OU dont le R1 se tient cette semaine."""
    month, week = spans["month"], spans["week"]
    groups = [
        {"filters": [{"propertyName": "hs_v2_date_entered_appointmentscheduled",
                      "operator": "BETWEEN", "value": ms(month[0]), "highValue": ms(month[1])}]},
        {"filters": [{"propertyName": "date_et_heure_du_r1",
                      "operator": "BETWEEN", "value": ms(week[0]), "highValue": ms(week[1])}]},
    ]
    deals = search("deals", groups, DEAL_PROPS)
    if not deals:
        return []

    ids = [d["id"] for d in deals]
    d2c = batch_assoc("deals", "contacts", ids)
    contact_ids = [c for v in d2c.values() for c in v]
    contacts = batch_read("contacts", contact_ids, ["hubspot_owner_id"]) if contact_ids else {}

    enriched = []
    for d in deals:
        p = d.get("properties", {})
        # Attribution SDR : champ dédié, sinon propriétaire du contact associé.
        sdr = p.get("co_proprietaire_de_la_transaction")
        if not sdr:
            for cid in d2c.get(str(d["id"]), []):
                owner = contacts.get(cid, {}).get("hubspot_owner_id")
                if owner:
                    sdr = str(owner)
                    break
        enriched.append({
            "id": str(d["id"]),
            "name": p.get("dealname") or "(sans nom)",
            "sdr": str(sdr) if sdr else None,
            "stage": p.get("dealstage"),
            "booked_at": parse_hs_date(p.get("hs_v2_date_entered_appointmentscheduled")),
            "r1_at": parse_hs_date(p.get("date_et_heure_du_r1")),
            "r1_statut": p.get("r1_statut"),
        })
    return enriched


def perf_for(deals, sdr_id, spans):
    mine = [d for d in deals if d["sdr"] == sdr_id]
    week, month = spans["week"], spans["month"]

    r1_week = [d for d in mine if in_range(d["booked_at"], week)]
    r1_month = [d for d in mine if in_range(d["booked_at"], month)]
    happening = [d for d in mine if in_range(d["r1_at"], week)]
    # Un R1 de la semaine est un no-show si le statut le dit, ou si le deal est
    # retombé dans le stage "No Show".
    no_show = [d for d in happening
               if d["r1_statut"] == "no_show" or d["stage"] == NO_SHOW_STAGE]

    return {
        "r1_bookes_semaine": len(r1_week),
        "r1_bookes_mois": len(r1_month),
        "r1_cette_semaine": len(happening),
        "no_show_semaine": len(no_show),
        "agenda": sorted(
            [{"name": d["name"], "at": d["r1_at"].isoformat(), "id": d["id"],
              "no_show": d in no_show}
             for d in happening],
            key=lambda x: x["at"],
        ),
    }


# --- Export CSV pour le power dialer Allo --------------------------------
#
# Le schéma de colonnes reprend celui de l'endpoint Allo POST /v2/api/dialing-queues/
# current/numbers (champs d'un contact de queue), avec trois colonnes en plus
# (absentes du schéma Allo) pour la qualification manuelle avant appel : deux LinkedIn
# et le nom de la publicité LinkedIn d'origine, quand il y en a une.
ALLO_COLUMNS = ["number", "name", "last_name", "company", "job_title", "emails",
                "linkedin", "company_linkedin", "contexte", "hubspot_url"]

LEAD_PROPS = ["firstname", "lastname", "phone", "mobilephone", "company", "jobtitle",
              "email", "hs_linkedin_url", "createdate",
              "hs_analytics_source", "hs_analytics_source_data_1",
              "hs_analytics_source_data_2", "hs_analytics_first_timestamp",
              "hs_latest_source", "hs_latest_source_data_1", "hs_latest_source_data_2",
              "hs_latest_source_timestamp"]

COMPANY_PROPS = ["linkedin_company_page", "name", "concurrents",
                 "concurrents_deja_clients", "proposition_de_valeur"]

# Sources HubSpot qui correspondent à un clic sur une publicité payante.
PAID_SOURCES = {"PAID_SOCIAL", "PAID_SEARCH"}

# Libellés lisibles des sources, pour les leads qui ne viennent d'aucune pub.
SOURCE_LABELS = {
    "PAID_SOCIAL": "Publicité sociale",
    "PAID_SEARCH": "Publicité search",
    "ORGANIC_SEARCH": "Recherche organique",
    "SOCIAL_MEDIA": "Réseaux sociaux",
    "DIRECT_TRAFFIC": "Trafic direct",
    "REFERRALS": "Site référent",
    "EMAIL_MARKETING": "Email marketing",
    "OFFLINE": "Import / scraping",
    "OTHER_CAMPAIGNS": "Autre campagne",
}

# Liste HubSpot des leads venus d'un webinar ou d'un événement physique. Ces leads
# gardent une campagne publicitaire d'origine (celle qui les avait touchés avant),
# mais ce n'est pas d'elle que le SDR doit parler au rappel : l'inscription à
# l'événement est le vrai contexte, elle prend donc sa place.
WEBINAR_LIST_NAME = "[SDR-Team] - Leads entrants webinar"
WEBINAR_LABEL = "Inscrit à un webinar ou événement physique"

# Liste HubSpot des leads chauds : ils n'ont pas de tâche NRP derrière eux, mais
# le SDR doit pouvoir les appeler en priorité (ligne en tête du tableau NRP).
HOT_LIST_NAME = "[SDR-Team] - Leads chauds"

_ids_par_liste = {}


def list_contact_ids(nom_liste, consequence_repli):
    """IDs des contacts membres d'une liste HubSpot, résolue par son nom.

    Mémoïsé par nom de liste : un seul aller-retour HubSpot par exécution, quel
    que soit le nombre de SDR collectés. Résolution par nom plutôt que par ID en
    dur pour que la liste reste modifiable dans HubSpot sans toucher au script —
    au prix d'une recherche, et d'un repli silencieux si quelqu'un la renomme.

    Liste introuvable ou scope `crm.lists.read` absent -> ensemble vide, et
    `consequence_repli` décrit dans les logs ce que le dashboard perd alors.
    """
    if nom_liste in _ids_par_liste:
        return _ids_par_liste[nom_liste]

    ids = set()
    _ids_par_liste[nom_liste] = ids
    res = _post("/crm/v3/lists/search",
                {"query": nom_liste, "count": 50}, strict=False) or {}
    cible = nom_liste.strip().lower()
    list_id = next((l.get("listId") for l in res.get("lists", [])
                    if (l.get("name") or "").strip().lower() == cible), None)
    if not list_id:
        print(f"ATTENTION : liste HubSpot '{nom_liste}' introuvable, "
              f"{consequence_repli}.", file=sys.stderr)
        return ids

    after = None
    while True:
        path = (f"/crm/v3/lists/{list_id}/memberships?limit=250"
                + (f"&after={after}" if after else ""))
        page = _get(path, strict=False)
        if not page:
            break
        ids.update(str(r["recordId"]) for r in page.get("results", [])
                   if r.get("recordId"))
        after = page.get("paging", {}).get("next", {}).get("after")
        if not after:
            break
        time.sleep(0.15)
    return ids


def webinar_contact_ids():
    return list_contact_ids(
        WEBINAR_LIST_NAME,
        "les leads webinar afficheront leur campagne publicitaire d'origine")


_leads_chauds_par_owner = None


def leads_chauds_par_owner():
    """Contacts de la liste HOT_LIST_NAME regroupés par propriétaire HubSpot.

    La liste est commune à l'équipe, mais le dashboard est par SDR : chacun ne voit
    que les leads chauds dont il est le propriétaire, pour que deux SDR n'appellent
    pas le même lead. Un lead chaud sans propriétaire n'apparaît donc nulle part.

    Mémoïsé : la liste et les propriétaires sont lus une seule fois par exécution,
    puis chaque SDR ne recharge que ses propres contacts.
    """
    global _leads_chauds_par_owner
    if _leads_chauds_par_owner is not None:
        return _leads_chauds_par_owner

    _leads_chauds_par_owner = {}
    ids = list_contact_ids(HOT_LIST_NAME,
                           "la ligne « Leads chauds » restera vide")
    if ids:
        proprios = batch_read("contacts", list(ids), ["hubspot_owner_id"])
        for contact_id, props in proprios.items():
            owner = str(props.get("hubspot_owner_id") or "")
            if owner:
                _leads_chauds_par_owner.setdefault(owner, []).append(contact_id)
    return _leads_chauds_par_owner


def lead_context(props, nom_entreprise="", concurrents="", proposition_de_valeur="",
                 concurrents_clients="", est_webinar=False):
    """Contexte d'un lead NRP : dernière campagne cliquée, date du clic, entreprise.

    HubSpot ne remonte pas l'identifiant de la publicité (cf. rubrique 04) : la
    campagne (drill-down 2 de la source) est la donnée la plus fine disponible.
    Le nom du formulaire de génération de leads (recent_conversion_event_name)
    n'est pas repris : il est quasi identique sur tous les leads (il ne distingue
    que TOFU/BOFU), donc n'ajoutait rien à côté de la campagne.

    On regarde d'abord le dernier touch (hs_latest_*). S'il n'est pas payant mais
    que le lead est né d'une pub, la dernière campagne cliquée est celle d'origine
    (hs_analytics_*) — sinon un lead pub revenu par Google n'afficherait aucune pub.

    `est_webinar` (membre de la liste WEBINAR_LIST_NAME) remplace le libellé de
    tête par WEBINAR_LABEL : la campagne reste dans HubSpot, mais elle n'est plus
    l'accroche du rappel.
    """
    dernier = (props.get("hs_latest_source") or "").upper()
    origine = (props.get("hs_analytics_source") or "").upper()

    if dernier in PAID_SOURCES:
        source, reseau = dernier, props.get("hs_latest_source_data_1")
        campagne = props.get("hs_latest_source_data_2")
        quand = parse_hs_date(props.get("hs_latest_source_timestamp"))
    elif origine in PAID_SOURCES:
        source, reseau = origine, props.get("hs_analytics_source_data_1")
        campagne = props.get("hs_analytics_source_data_2")
        quand = (parse_hs_date(props.get("hs_analytics_first_timestamp"))
                 or parse_hs_date(props.get("createdate")))
    else:
        # Aucune pub : on montre la vraie source, pour que le SDR sache quand même
        # d'où vient le lead (organique, import, referral...).
        source = dernier or origine
        reseau = (props.get("hs_latest_source_data_1")
                  or props.get("hs_analytics_source_data_1"))
        campagne = ""
        quand = (parse_hs_date(props.get("hs_latest_source_timestamp"))
                 or parse_hs_date(props.get("createdate")))

    return {
        "campagne": WEBINAR_LABEL if est_webinar else (campagne or ""),
        "est_webinar": est_webinar,
        "est_pub": source in PAID_SOURCES,
        "source": SOURCE_LABELS.get(
            source, source.replace("_", " ").capitalize() if source else ""),
        "reseau": reseau or "",
        "date": quand.date().isoformat() if quand else "",
        # Le champ "company" du contact est du texte libre et souvent vide :
        # l'entreprise associee sert de repli.
        "company": props.get("company") or nom_entreprise or "",
        # Propriétés de l'entreprise HubSpot associée : absentes du dashboard si vides.
        "concurrents": concurrents or "",
        # Concurrents déjà clients de l'entreprise (propriété "concurrents_deja_clients"
        # de l'entreprise) : preuve sociale à citer au rappel quand elle est remplie.
        "concurrents_clients": concurrents_clients or "",
        "proposition_de_valeur": proposition_de_valeur or "",
    }


def to_e164(raw):
    """Normalise un numéro HubSpot en E.164. Renvoie None si inexploitable."""
    if not raw:
        return None
    s = "".join(c for c in str(raw) if c.isdigit() or c == "+")
    if s.startswith("+"):
        digits = s[1:]
        return "+" + digits if len(digits) >= 8 else None
    if s.startswith("00"):
        digits = s[2:]
        return "+" + digits if len(digits) >= 8 else None
    if s.startswith("0"):
        # Numéro national français : 0X XX XX XX XX -> +33X XX XX XX XX.
        # Toute autre longueur est un numéro tronqué ou bidon (ex. "06060606") :
        # on le jette plutôt que de fabriquer un "+0..." impossible à composer.
        return "+33" + s[1:] if len(s) == 10 else None
    return "+" + s if len(s) >= 8 else None


def leads_from_tasks(task_ids, avec_contexte=False):
    """Contacts rattachés aux tâches, dédoublonnés, avec un numéro appelable.

    `avec_contexte=True` ajoute la clé "ctx" à chaque lead (dernière campagne
    cliquée, date du clic, entreprise), qui alimente la rubrique 03. Ce n'est pas demandé
    partout : c'est du poids en plus dans data.json, servi à chaque chargement de
    page, et seules les étapes NRP et les leads chauds alimentent cette rubrique.
    """
    task_ids = [str(t) for t in task_ids]
    t2c = batch_assoc("tasks", "contacts", task_ids)
    contact_ids = [c for v in t2c.values() for c in v]

    # Certaines tâches sont posées par un workflow sur le deal et n'ont aucune
    # association directe vers un contact (cas de "Rappel anti no show") : sans
    # repli, elles ne produiraient aucun numéro, donc aucun CSV. On remonte donc
    # tâche -> deal -> contacts pour celles-là.
    orphelines = [t for t in task_ids if t not in t2c]
    if orphelines:
        t2d = batch_assoc("tasks", "deals", orphelines)
        deal_ids = list(dict.fromkeys(d for v in t2d.values() for d in v))
        if deal_ids:
            d2c = batch_assoc("deals", "contacts", deal_ids)
            contact_ids += [c for v in d2c.values() for c in v]

    return leads_from_contacts(contact_ids, avec_contexte)


def leads_from_contacts(contact_ids, avec_contexte=False):
    """Même sortie que `leads_from_tasks`, à partir d'IDs de contacts.

    Sert à la ligne « Leads chauds », qui vient d'une liste HubSpot et non de
    tâches : il n'y a donc aucune association à remonter avant.
    """
    contact_ids = list(contact_ids)
    if not contact_ids:
        return []
    contacts = batch_read("contacts", contact_ids, LEAD_PROPS)

    # Le LinkedIn de l'entreprise est une propriété de l'objet Company, pas du contact
    # (le champ "company" du contact n'est qu'un texte libre) -> passer par l'association.
    c2comp = batch_assoc("contacts", "companies", contact_ids)
    company_ids = [c for v in c2comp.values() for c in v]
    companies = batch_read("companies", company_ids, COMPANY_PROPS) if company_ids else {}

    leads, seen = [], set()
    for contact_id, props in contacts.items():
        number = to_e164(props.get("mobilephone") or props.get("phone"))
        if not number or number in seen:
            continue
        seen.add(number)
        company_linkedin = next(
            (companies[c]["linkedin_company_page"] for c in c2comp.get(contact_id, [])
             if companies.get(c, {}).get("linkedin_company_page")),
            "",
        )
        # Nom de la publicité LinkedIn d'origine, lisible dans l'activité "Source" du
        # contact — seulement quand la source est bien LinkedIn Ads (source_data_1
        # porte le réseau, source_data_2 le nom de la campagne/pub, cf. rubrique 03).
        contexte = ""
        if "linkedin" in (props.get("hs_analytics_source_data_1") or "").lower():
            contexte = props.get("hs_analytics_source_data_2") or ""
        lead = {
            "number": number,
            "name": props.get("firstname") or "",
            "last_name": props.get("lastname") or "",
            "company": props.get("company") or "",
            "job_title": props.get("jobtitle") or "",
            "emails": props.get("email") or "",
            "linkedin": props.get("hs_linkedin_url") or "",
            "company_linkedin": company_linkedin,
            "contexte": contexte,
            "hubspot_url": f"https://app.hubspot.com/contacts/{PORTAL_ID}/record/0-1/{contact_id}",
        }
        if avec_contexte:
            def prop_entreprise(nom_prop):
                return next(
                    (companies[c][nom_prop] for c in c2comp.get(contact_id, [])
                     if companies.get(c, {}).get(nom_prop)),
                    "",
                )
            lead["ctx"] = lead_context(
                props,
                prop_entreprise("name"),
                prop_entreprise("concurrents"),
                prop_entreprise("proposition_de_valeur"),
                concurrents_clients=prop_entreprise("concurrents_deja_clients"),
                est_webinar=str(contact_id) in webinar_contact_ids(),
            )
        leads.append(lead)
    return sorted(leads, key=lambda x: (x["company"].lower(), x["last_name"].lower()))


def write_allo_csv(path, leads):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # utf-8-sig : Excel ouvre le fichier avec les accents corrects.
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        # extrasaction : les leads NRP portent en plus la clé "ctx" (rubrique 03),
        # qui n'a rien à faire dans un fichier destiné au power dialer.
        w = csv.DictWriter(f, fieldnames=ALLO_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(leads)


# --- Rubrique 2 : contexte des tâches ------------------------------------

def owner_filter(sdr_id):
    return {"propertyName": "hubspot_owner_id", "operator": "EQ", "value": sdr_id}


# Une tâche "à faire" = tout sauf terminée (NOT_STARTED, IN_PROGRESS, WAITING, DEFERRED).
NOT_DONE = {"propertyName": "hs_task_status", "operator": "NEQ", "value": "COMPLETED"}


def contexte_for(sdr_id, csv_dir):
    # Ligne « Leads chauds », en tête du tableau : membres de la liste HubSpot
    # HOT_LIST_NAME dont le SDR est propriétaire. Aucune tâche derrière eux, donc le
    # compteur de la colonne « Tâches » est le nombre de leads de la liste.
    # `avec_contexte=True` : ces leads apparaissent aussi dans la rubrique 03.
    chauds_ids = leads_chauds_par_owner().get(str(sdr_id), [])
    leads_chauds = leads_from_contacts(chauds_ids, avec_contexte=True)
    leads_chauds_csv = None
    if leads_chauds:
        fname = f"{sdr_id}-LEADSCHAUDS.csv"
        write_allo_csv(os.path.join(csv_dir, fname), leads_chauds)
        leads_chauds_csv = f"csv/{fname}"

    # Rubrique NRP = tâches ouvertes assignées au SDR, une par étape.
    # Chaque étape produit aussi un CSV prêt pour le power dialer Allo.
    nrp = []
    for stage in NRP_STAGES:
        taches = search("tasks", [{"filters": [
            {"propertyName": "hs_task_subject", "operator": "EQ", "value": stage},
            NOT_DONE,
            owner_filter(sdr_id),
        ]}], ["hs_task_subject"])

        entry = {"stage": stage, "taches": len(taches), "numeros": 0, "csv": None, "leads": []}
        if taches:
            leads = leads_from_tasks([str(t["id"]) for t in taches], avec_contexte=True)
            if leads:
                fname = f"{sdr_id}-{stage}.csv"
                write_allo_csv(os.path.join(csv_dir, fname), leads)
                entry["numeros"] = len(leads)
                entry["csv"] = f"csv/{fname}"
                # Même leads que le CSV, exposés en JSON pour le bouton "Appeler"
                # (push direct dans la queue Allo, sans repasser par un fichier).
                entry["leads"] = leads
        nrp.append(entry)

    activation = count("contacts", [{"filters": [
        {"propertyName": "suivi_nrp", "operator": "EQ", "value": ACTIVATION_STAGE},
        owner_filter(sdr_id),
    ]}])

    # "à rappeler" : on récupère puis on filtre sur le libellé exact (plusieurs variantes).
    rappels = search("tasks", [{"filters": [
        {"propertyName": "hs_task_subject", "operator": "CONTAINS_TOKEN", "value": "rappeler"},
        NOT_DONE,
        owner_filter(sdr_id),
    ]}], ["hs_task_subject", "hs_timestamp"])

    a_rappeler = []
    for t in rappels:
        p = t.get("properties", {})
        subject = (p.get("hs_task_subject") or "").strip().lower()
        if subject.startswith("a rappeler") or subject.startswith("à rappeler"):
            due = parse_hs_date(p.get("hs_timestamp"))
            a_rappeler.append({"id": str(t["id"]),
                               "subject": p.get("hs_task_subject", "").strip(),
                               "due": due.isoformat() if due else None})

    # Même CSV Allo que les étapes NRP, pour pouvoir aussi les charger dans le power dialer.
    a_rappeler_csv = None
    a_rappeler_numeros = 0
    a_rappeler_leads = []
    if a_rappeler:
        leads = leads_from_tasks([t["id"] for t in a_rappeler])
        if leads:
            fname = f"{sdr_id}-RAPPEL.csv"
            write_allo_csv(os.path.join(csv_dir, fname), leads)
            a_rappeler_numeros = len(leads)
            a_rappeler_csv = f"csv/{fname}"
            a_rappeler_leads = leads

    # "Rappel anti no show" : titre exact (insensible à la casse), même logique que ci-dessus.
    rappels_ans = search("tasks", [{"filters": [
        {"propertyName": "hs_task_subject", "operator": "CONTAINS_TOKEN", "value": "show"},
        NOT_DONE,
        owner_filter(sdr_id),
    ]}], ["hs_task_subject"])

    anti_no_show = []
    for t in rappels_ans:
        p = t.get("properties", {})
        subject = (p.get("hs_task_subject") or "").strip().lower()
        if subject == "rappel anti no show":
            anti_no_show.append({"id": str(t["id"]),
                                 "subject": p.get("hs_task_subject", "").strip()})

    anti_no_show_leads = []
    anti_no_show_csv = None
    anti_no_show_numeros = 0
    if anti_no_show:
        anti_no_show_leads = leads_from_tasks([t["id"] for t in anti_no_show])
        if anti_no_show_leads:
            fname = f"{sdr_id}-ANTINOSHOW.csv"
            write_allo_csv(os.path.join(csv_dir, fname), anti_no_show_leads)
            anti_no_show_numeros = len(anti_no_show_leads)
            anti_no_show_csv = f"csv/{fname}"

    # "A qualifier manuellement (lead paid)" : titre exact (insensible à la casse).
    rappels_qm = search("tasks", [{"filters": [
        {"propertyName": "hs_task_subject", "operator": "CONTAINS_TOKEN", "value": "qualifier"},
        NOT_DONE,
        owner_filter(sdr_id),
    ]}], ["hs_task_subject"])

    a_qualifier_manuellement = []
    for t in rappels_qm:
        p = t.get("properties", {})
        subject = (p.get("hs_task_subject") or "").strip().lower()
        if subject == "a qualifier manuellement (lead paid)" or subject == "à qualifier manuellement (lead paid)":
            a_qualifier_manuellement.append({"id": str(t["id"]),
                                             "subject": p.get("hs_task_subject", "").strip()})

    a_qualifier_manuellement_leads = []
    a_qualifier_manuellement_csv = None
    a_qualifier_manuellement_numeros = 0
    if a_qualifier_manuellement:
        a_qualifier_manuellement_leads = leads_from_tasks([t["id"] for t in a_qualifier_manuellement])
        if a_qualifier_manuellement_leads:
            fname = f"{sdr_id}-QUALIFMANUEL.csv"
            write_allo_csv(os.path.join(csv_dir, fname), a_qualifier_manuellement_leads)
            a_qualifier_manuellement_numeros = len(a_qualifier_manuellement_leads)
            a_qualifier_manuellement_csv = f"csv/{fname}"

    now = dt.datetime.now(PARIS)
    return {
        "leads_chauds": len(chauds_ids),
        "leads_chauds_csv": leads_chauds_csv,
        "leads_chauds_numeros": len(leads_chauds),
        "leads_chauds_leads": leads_chauds,
        "nrp": nrp,
        "en_activation": activation,
        "a_rappeler": len(a_rappeler),
        "a_rappeler_en_retard": sum(1 for t in a_rappeler if t["due"] and t["due"] < now.isoformat()),
        "a_rappeler_csv": a_rappeler_csv,
        "a_rappeler_numeros": a_rappeler_numeros,
        "a_rappeler_leads": a_rappeler_leads,
        "anti_no_show": len(anti_no_show),
        "anti_no_show_csv": anti_no_show_csv,
        "anti_no_show_numeros": anti_no_show_numeros,
        # Numéros + noms exposés pour le sélecteur de contact du bouton "rappel Allo" ;
        # même donnée que celle qui alimente déjà le CSV, juste servie en JSON aussi.
        "anti_no_show_leads": anti_no_show_leads,
        "a_qualifier_manuellement": len(a_qualifier_manuellement),
        "a_qualifier_manuellement_csv": a_qualifier_manuellement_csv,
        "a_qualifier_manuellement_numeros": a_qualifier_manuellement_numeros,
        "a_qualifier_manuellement_leads": a_qualifier_manuellement_leads,
    }


# --- Rubrique 3 : leads entrants -----------------------------------------

CONTACT_PROPS = [
    "createdate", "hs_analytics_source", "hs_analytics_source_data_1",
    "hs_analytics_source_data_2", "statut_de_qualification_paid",
    "recent_conversion_event_name",
]


def collect_inbound(spans):
    month = spans["month"]
    contacts = search("contacts", [{"filters": [
        {"propertyName": "createdate", "operator": "BETWEEN",
         "value": ms(month[0]), "highValue": ms(month[1])},
    ]}], CONTACT_PROPS)

    rows = []
    for c in contacts:
        p = c.get("properties", {})
        if p.get("hs_analytics_source") not in INBOUND_SOURCES:
            continue
        rows.append({
            "created": parse_hs_date(p.get("createdate")),
            "source": p.get("hs_analytics_source"),
            "reseau": p.get("hs_analytics_source_data_1") or "(inconnu)",
            "campagne": p.get("hs_analytics_source_data_2") or "(sans campagne)",
            "mql": p.get("statut_de_qualification_paid") == "MQL",
            "form": p.get("recent_conversion_event_name") or "(sans formulaire)",
        })
    return rows


def inbound_stats(rows, span):
    scope = [r for r in rows if in_range(r["created"], span)]
    total = len(scope)
    mql = sum(1 for r in scope if r["mql"])

    by_ad = {}
    for r in scope:
        key = (r["reseau"], r["campagne"])
        slot = by_ad.setdefault(key, {"reseau": r["reseau"], "campagne": r["campagne"],
                                      "leads": 0, "mql": 0})
        slot["leads"] += 1
        slot["mql"] += 1 if r["mql"] else 0

    # On n'affiche que les campagnes significatives : sous 10 leads, le taux de MQL
    # n'est pas lisible (1 lead = 100 %) et la ligne pollue le tableau.
    pubs = sorted((p for p in by_ad.values() if p["leads"] > MIN_LEADS_PUB),
                  key=lambda x: x["leads"], reverse=True)
    for p in pubs:
        p["taux_mql"] = round(100 * p["mql"] / p["leads"], 1) if p["leads"] else 0.0

    return {
        "leads": total,
        "mql": mql,
        "leads_hors_seuil": total - sum(p["leads"] for p in pubs),
        "taux_mql": round(100 * mql / total, 1) if total else 0.0,
        "pubs": pubs[:12],
    }


# --- Main -----------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Génère data.json pour le dashboard SDR.")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "public", "data.json"))
    args = ap.parse_args()

    now = dt.datetime.now(PARIS)
    spans = periods(now)

    print("1/4 Résolution des SDR...", file=sys.stderr)
    sdrs, sdrs_config, sdrs_ignores = resolve_sdrs()
    print("   " + ", ".join(f"{s['name']} ({s['id']})" for s in sdrs), file=sys.stderr)

    print("2/4 R1 (deals)...", file=sys.stderr)
    deals = collect_r1(spans)

    print("3/4 Contexte NRP + rappels + CSV Allo...", file=sys.stderr)
    csv_dir = os.path.join(os.path.dirname(args.out), "csv")
    sdr_blocks = {}
    for sdr in sdrs:
        print(f"   {sdr['name']}...", file=sys.stderr)
        sdr_blocks[sdr["id"]] = {
            "name": sdr["name"],
            "performances": perf_for(deals, sdr["id"], spans),
            "contexte": contexte_for(sdr["id"], csv_dir),
        }

    print("4/4 Leads entrants...", file=sys.stderr)
    rows = collect_inbound(spans)

    non_attribues = sum(1 for d in deals if d["sdr"] is None
                        and in_range(d["booked_at"], spans["month"]))

    payload = {
        "genere_le": now.isoformat(),
        "portal_id": PORTAL_ID,
        "semaine": {"debut": spans["week"][0].date().isoformat(),
                    "fin": (spans["week"][1] - dt.timedelta(days=1)).date().isoformat()},
        "mois": spans["month"][0].strftime("%Y-%m"),
        "sdrs": [{"id": s["id"], "name": s["name"], "email": s.get("email", "")} for s in sdrs],
        "sdrs_config": sdrs_config,
        "sdrs_ignores": sdrs_ignores,
        "data": sdr_blocks,
        "entrants": {
            "semaine": inbound_stats(rows, spans["week"]),
            "mois": inbound_stats(rows, spans["month"]),
        },
        "alertes": {"r1_mois_sans_sdr": non_attribues},
    }

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"\nOK -> {args.out}", file=sys.stderr)
    print(f"   {len(deals)} deals R1, {len(rows)} leads entrants ce mois-ci.", file=sys.stderr)
    if non_attribues:
        print(f"   ATTENTION : {non_attribues} R1 du mois sans SDR identifiable.", file=sys.stderr)


if __name__ == "__main__":
    main()
