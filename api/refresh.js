// POST /api/refresh — déclenche le workflow GitHub qui régénère data.json et les CSV.
//
// Le jeton GitHub reste côté serveur : le navigateur ne voit que 202 / 409 / 5xx.

import { config, nonConfigure, runEnCours, lancerWorkflow, messageErreur } from './_github.js';

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    res.setHeader('Allow', 'POST');
    return res.status(405).json({ error: 'Utiliser POST.' });
  }

  const cfg = config();
  if (!cfg) return nonConfigure(res);

  try {
    const enCours = await runEnCours(cfg);
    if (enCours) {
      return res.status(409).json({
        error: 'Un rafraîchissement est déjà en cours.',
        runUrl: typeof enCours === 'string' ? enCours : undefined,
      });
    }

    const r = await lancerWorkflow(cfg);
    if (r.ok) return res.status(202).json({ status: 'lance' });

    const msg = messageErreur(r.status, cfg.repo, cfg.ref);
    return res.status(502).json({ ...msg, detail: msg.detail || r.detail });
  } catch (e) {
    return res.status(500).json({ error: 'Appel à GitHub impossible.', detail: String(e) });
  }
}
