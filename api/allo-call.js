// POST /api/allo-call — pousse une liste de leads dans la queue de power dialer Allo
// (POST /v2/api/dialing-queues/current/numbers), pour appeler directement depuis
// web.withallo.com sans repasser par l'import manuel du CSV.
//
// La clé Allo reste côté serveur : le navigateur envoie les leads (déjà connus via
// data.json) et l'email du SDR, jamais la clé. `email` cible la queue du bon
// teammate — sans lui, les numéros iraient dans la queue du titulaire de la clé API.

import { ALLO_BASE, requireApiKey, nonConfigure } from './_allo.js';

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    res.setHeader('Allow', 'POST');
    return res.status(405).json({ error: 'Utiliser POST.' });
  }

  const apiKey = requireApiKey();
  if (!apiKey) return nonConfigure(res);

  const corps = typeof req.body === 'string' ? JSON.parse(req.body) : req.body || {};
  const leads = Array.isArray(corps.leads) ? corps.leads : [];
  const email = typeof corps.email === 'string' ? corps.email.trim() : '';

  // Le schéma Allo n'a pas de colonnes LinkedIn (ajoutées côté CSV pour la
  // qualification manuelle) : on ne garde que ce que l'API accepte.
  const numbers = leads
    .filter(l => l && typeof l.number === 'string' && l.number)
    .map(l => ({
      number: l.number,
      ...(l.name ? { name: l.name } : {}),
      ...(l.last_name ? { last_name: l.last_name } : {}),
      ...(l.company ? { company: l.company } : {}),
      ...(l.job_title ? { job_title: l.job_title } : {}),
      ...(l.emails ? { emails: [l.emails] } : {}),
    }));

  if (!numbers.length) {
    return res.status(400).json({ error: 'Aucun numéro exploitable à envoyer.' });
  }

  try {
    const r = await fetch(`${ALLO_BASE}/v2/api/dialing-queues/current/numbers`, {
      method: 'POST',
      headers: {
        Authorization: `Api-Key ${apiKey}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(email ? { numbers, email } : { numbers }),
    });

    if (r.ok) return res.status(200).json({ status: 'ajoute', count: numbers.length });

    const erreur = await r.json().catch(() => ({}));
    // Même forme imbriquée que l'endpoint SMS : {"error":{"code":..,"message":..}}.
    const dedans = erreur.error || erreur;
    return res.status(502).json({
      error: dedans.message || `Allo (dialer) a répondu ${r.status}.`,
      detail: dedans.code || dedans.message,
    });
  } catch (e) {
    return res.status(500).json({ error: 'Appel à Allo impossible.', detail: String(e) });
  }
}
