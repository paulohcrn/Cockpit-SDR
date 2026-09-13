# Cockpit SDR — dashboard de priorisation commerciale temps réel

> Projet de démonstration. Adapté à partir d'un outil interne que j'ai conçu et
> développé pour une équipe de SDR (sales development reps) B2B, dans le cadre
> de mon travail. Les données ci-dessous sont **entièrement fictives** —
> aucune donnée réelle de prospect, de client ou d'entreprise n'est incluse.

## Le problème

Une équipe de SDR travaille dans HubSpot toute la journée, mais HubSpot ne
répond pas bien à la question « qui dois-je appeler maintenant, et dans quel
ordre ? ». Les leads prioritaires (chauds, en relance, en attente de
qualification) sont noyés parmi des dizaines de tâches, et il n'y a pas de vue
consolidée par commercial du pipeline + du contexte + des leads entrants.

Ce dashboard résout ça : une page par SDR, quatre rubriques (performances,
tâches à traiter par priorité, contexte de chaque lead, attribution
publicitaire des leads entrants), et un export direct vers les outils d'appel
(power dialer) pour ne jamais repasser par un copier-coller manuel.

## Ce que ça montre côté technique

- **Zéro dépendance côté front** — `public/index.html` est une page unique en
  JS/CSS vanilla, aucun bundler, aucun framework. Choix assumé : l'app est
  petite, un framework aurait ajouté de la complexité sans bénéfice.
- **Le token de l'API métier ne quitte jamais le serveur** — le navigateur ne
  lit qu'un `data.json` statique, régénéré à la demande par une fonction
  serverless qui garde le jeton d'API. Les clés des power dialers (Allo,
  Minari) suivent la même règle : les endpoints `api/*.js` les utilisent
  côté serveur et ne renvoient au client que le strict nécessaire.
- **GitHub Actions comme pipeline de données** — le bouton *Rafraîchir*
  déclenche un workflow GitHub qui interroge l'API HubSpot (script Python,
  stdlib pure, aucune dépendance), régénère `data.json` + les exports CSV, les
  commite, puis redéploie sur Vercel — sans dépendre du webhook natif
  Git↔Vercel, qui s'est révélé peu fiable en usage réel.
- **Intégration à deux power dialers tiers** (Allo, Minari) avec deux logiques
  de résolution d'identité différentes : l'un route par email de teammate,
  l'autre par ID numérique avec une chaîne de repli (email direct → mapping
  configuré → compte par défaut) pour ne jamais perdre silencieusement une
  liste d'appel envoyée au mauvais endroit.
- **Normalisation de données réelles et imparfaites** : numéros de téléphone
  hétérogènes normalisés en E.164 avec rejet des formats invalides plutôt que
  transformation en numéro impossible à composer, résolution de listes CRM par
  nom (modifiable sans toucher au code) avec repli explicite en cas de
  renommage, attribution multi-niveaux (propriétaire du deal → propriétaire du
  contact associé) avec un compteur dédié pour les cas non résolus plutôt
  qu'un zéro silencieux.

## Stack

- **Frontend** : HTML/CSS/JS vanilla (`public/index.html`)
- **Backend** : fonctions serverless Vercel (Node, `api/*.js`)
- **Collecte de données** : Python stdlib pur (`build_data.py`), CRM HubSpot
- **Orchestration** : GitHub Actions (déclenchement à la demande + déploiement)
- **Hébergement** : Vercel

```
sdrs.json             liste des commerciaux affichés (emails ou ID)
build_data.py          collecte CRM -> public/data.json + public/csv
api/refresh.js         déclenche le workflow de collecte (jeton GitHub côté serveur)
api/sdrs.js            lit / réécrit sdrs.json depuis le dashboard
api/_github.js         accès GitHub partagé par les endpoints
api/allo-*.js          intégration power dialer Allo (SMS, appel direct)
api/minari-call.js     intégration power dialer Minari (appel direct)
public/index.html      le dashboard (1 fichier, aucune dépendance)
public/data.json       snapshot de démonstration (données fictives)
public/csv/            exports de démonstration pour power dialer
.github/workflows/      collecte à la demande + déploiement direct sur Vercel
```

## Faire tourner la démo en local

Le `data.json` fourni est un jeu de données fictif fixe : pas besoin de clé
API pour explorer l'interface.

```bash
python3 -m http.server 8777 --directory public
```

Puis ouvrir `http://localhost:8777`. Les boutons *Rafraîchir*, *Appeler* et
*Minari* répondent « fonction non configurée » sans clé API — normal en mode
démo, ils ne sont pas nécessaires pour voir le dashboard fonctionner.

Pour aller plus loin et brancher une vraie source de données (un compte
HubSpot de test, par exemple) :

```bash
export HUBSPOT_PAT="pat-eu1-..."
python3 build_data.py
```

Le bouton *Rafraîchir* a besoin d'une fonction serveur (`npx vercel dev`) —
`python -m http.server` ne sait servir que du statique.

## Ce que mesure chaque rubrique

### Performances

Rendez-vous qualifiés bookés (semaine / mois), ceux qui ont lieu cette
semaine, et les no-show. L'attribution au commercial se fait via une
propriété dédiée du deal, avec repli sur le propriétaire du contact associé —
et un compteur explicite des cas non attribués plutôt qu'un chiffre trompeur.

### Tâches en attente

Les tâches CRM non terminées assignées au commercial sélectionné, groupées
par étape de relance (NRP0 → NRP7), plus les leads chauds (liste CRM commune
à l'équipe, filtrée par propriétaire pour qu'un même lead ne soit pas appelé
deux fois), les relances en retard et les cas à qualifier manuellement.
Chaque ligne expose un export CSV pour le power dialer, avec les numéros
normalisés en E.164 et dédupliqués.

### Contexte des leads

D'où vient chaque lead avant de décrocher : dernière campagne cliquée,
réseau, date, entreprise. Repli sur la source d'origine quand le dernier
point de contact n'est pas publicitaire, pour ne jamais afficher un vide sur
un lead venu d'une pub.

### Leads entrants

Volume et taux de qualification (MQL) des nouveaux contacts sur la période,
ventilés par campagne publicitaire — seules les campagnes dépassant un seuil
minimum de leads sont détaillées, en dessous le taux de MQL n'est pas
significatif.

## Ce qui a été retiré pour cette version publique

- Toutes les données réelles (noms, emails, téléphones, entreprises) ont été
  remplacées par un jeu de données fictif.
- Le nom et l'identité visuelle de l'entreprise d'origine ont été neutralisés.
- Les identifiants CRM réels (portail, propriétés propres à l'organisation
  d'origine) ont été remplacés par des valeurs génériques.

La logique métier (règles de priorisation, normalisation de données,
intégrations) est inchangée : c'est elle que ce projet a pour but de montrer.
