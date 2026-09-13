// GET  /api/sdrs — la liste brute de sdrs.json (y compris les entrées non résolues)
// POST /api/sdrs — remplace la liste, commite, puis relance la collecte
//
// sdrs.json dans le dépôt reste la source de vérité : une modification faite ici et
// une modification faite à la main dans GitHub passent par le même fichier.

import {
  config, nonConfigure, gh, runEnCours, lancerWorkflow, messageErreur, SDRS_PATH,
} from './_github.js';

const MAX_SDRS = 30;
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
const OWNER_ID = /^\d{4,}$/;

const COMMENTAIRE =
  'Liste des SDR affichés dans le menu déroulant. Ajouter une ligne = ajouter un SDR. '
  + "Accepte un email HubSpot ou un owner ID numérique. L'ordre est celui du menu.";

/** Renvoie {sdrs} nettoyé, ou {erreur} si l'entrée est invalide. */
function valider(brut) {
  if (!Array.isArray(brut)) return { erreur: 'Le corps doit contenir un tableau "sdrs".' };

  const propres = [];
  const vus = new Set();
  for (const item of brut) {
    if (typeof item !== 'string') return { erreur: 'Chaque entrée doit être une chaîne.' };
    const v = item.trim();
    if (!v) continue;
    if (!EMAIL.test(v) && !OWNER_ID.test(v)) {
      return { erreur: `"${v}" n'est ni un email ni un owner ID HubSpot.` };
    }
    const cle = v.toLowerCase();
    if (vus.has(cle)) continue;
    vus.add(cle);
    propres.push(v);
  }

  if (!propres.length) return { erreur: 'Il faut au moins un SDR dans la liste.' };
  if (propres.length > MAX_SDRS) return { erreur: `Maximum ${MAX_SDRS} SDR.` };
  return { sdrs: propres };
}

async function lireFichier({ token, repo, ref }) {
  const res = await gh(
    `/repos/${repo}/contents/${SDRS_PATH}?ref=${encodeURIComponent(ref)}`,
    token
  );
  if (!res.ok) return { status: res.status };
  const body = await res.json();
  const texte = Buffer.from(body.content, 'base64').toString('utf-8');
  let sdrs = [];
  try {
    sdrs = JSON.parse(texte).sdrs || [];
  } catch (_) { /* fichier corrompu : on repart d'une liste vide */ }
  return { sha: body.sha, sdrs };
}

export default async function handler(req, res) {
  const cfg = config();
  if (!cfg) return nonConfigure(res);

  try {
    if (req.method === 'GET') {
      const actuel = await lireFichier(cfg);
      if (actuel.status) {
        return res.status(502).json(messageErreur(actuel.status, cfg.repo, cfg.ref));
      }
      return res.status(200).json({ sdrs: actuel.sdrs });
    }

    if (req.method !== 'POST') {
      res.setHeader('Allow', 'GET, POST');
      return res.status(405).json({ error: 'Utiliser GET ou POST.' });
    }

    const corps = typeof req.body === 'string' ? JSON.parse(req.body) : req.body || {};
    const { sdrs, erreur } = valider(corps.sdrs);
    if (erreur) return res.status(400).json({ error: erreur });

    // Un run en cours réécrirait data.json par-dessus : on refuse plutôt que
    // de laisser la liste et les données diverger.
    const enCours = await runEnCours(cfg);
    if (enCours) {
      return res.status(409).json({
        error: 'Un rafraîchissement est en cours, réessayer dans un instant.',
      });
    }

    const actuel = await lireFichier(cfg);
    if (actuel.status) {
      return res.status(502).json(messageErreur(actuel.status, cfg.repo, cfg.ref));
    }
    if (JSON.stringify(actuel.sdrs) === JSON.stringify(sdrs)) {
      return res.status(200).json({ status: 'inchange', sdrs });
    }

    const contenu = JSON.stringify({ _comment: COMMENTAIRE, sdrs }, null, 2) + '\n';
    const ecriture = await gh(`/repos/${cfg.repo}/contents/${SDRS_PATH}`, cfg.token, {
      method: 'PUT',
      body: JSON.stringify({
        message: `chore: liste SDR (${sdrs.length})`,
        content: Buffer.from(contenu, 'utf-8').toString('base64'),
        sha: actuel.sha,
        branch: cfg.ref,
      }),
    });

    if (!ecriture.ok) {
      // 409 = le sha a bougé entre la lecture et l'écriture (édition concurrente).
      if (ecriture.status === 409) {
        return res.status(409).json({
          error: 'sdrs.json a été modifié entre-temps. Recharger la page et réessayer.',
        });
      }
      const msg = messageErreur(ecriture.status, cfg.repo, cfg.ref);
      return res.status(502).json({ ...msg, detail: msg.detail || (await ecriture.text()) });
    }

    // La liste seule ne suffit pas : sans nouvelle collecte, un SDR ajouté n'aurait
    // aucune donnée dans data.json.
    const lance = await lancerWorkflow(cfg);
    if (!lance.ok) {
      return res.status(207).json({
        status: 'enregistre-sans-collecte',
        sdrs,
        error: 'Liste enregistrée, mais la collecte n\'a pas pu démarrer.',
        detail: messageErreur(lance.status, cfg.repo, cfg.ref).error,
      });
    }

    return res.status(202).json({ status: 'lance', sdrs });
  } catch (e) {
    return res.status(500).json({ error: 'Appel à GitHub impossible.', detail: String(e) });
  }
}
