// POST /api/allo-remind — envoie le SMS de rappel générique via Allo à un contact.
//
// La clé Allo reste côté serveur : le navigateur envoie juste un numéro et ne voit
// que le statut de l'envoi. Le message est fixe (pas transmis par le client) pour
// que cet endpoint ne puisse pas servir à envoyer un texte arbitraire.

import { config, nonConfigure, ALLO_BASE } from './_allo.js';

const FR_NUMBER = /^\+33\d{9}$/;

const MESSAGE = `Bonjour,

Je reviens vers vous concernant notre rendez-vous prévu dans les prochains jours.

Je souhaitais m'assurer que ce créneau vous convient toujours. Dans le cas contraire, je reste à votre disposition pour trouver un autre moment.

Bien à vous,
L'équipe commerciale`;

// Traduit les codes d'erreur Allo (voir doc SMS) en message actionnable. Allo
// imbrique le code sous `error` : {"error":{"type":..,"code":..,"message":..}}.
const URL_SMS = `${ALLO_BASE}/v1/api/sms`;

const CODES = {
  INVALID_SENDER_ID: 'Sender ID invalide — vérifier ALLO_SENDER_ID.',
  SENDER_ID_NOT_FOUND: "Sender ID introuvable sur le compte Allo — vérifier qu'il est bien créé dans Settings > Compliance et que ALLO_SENDER_ID reprend exactement ce nom.",
  SENDER_ID_NOT_ACTIVE: "Sender ID pas encore activé par l'équipe Allo.",
  INVALID_TO_NUMBER: 'Numéro de destination refusé par Allo.',
  INVALID_LENGTH: 'Message vide ou trop long.',
  API_KEY_INVALID: 'Clé API Allo invalide ou révoquée.',
  API_KEY_INSUFFICIENT_SCOPE: "La clé API Allo n'a pas le scope SMS_SEND.",
  API_KEY_QUOTA_EXCEEDED: 'Quota Allo dépassé, réessayer plus tard.',
};

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    res.setHeader('Allow', 'POST');
    return res.status(405).json({ error: 'Utiliser POST.' });
  }

  const cfg = config();
  if (!cfg) return nonConfigure(res);

  const corps = typeof req.body === 'string' ? JSON.parse(req.body) : req.body || {};
  const to = typeof corps.to === 'string' ? corps.to.trim() : '';
  if (!FR_NUMBER.test(to)) {
    return res.status(400).json({
      error: 'Numéro invalide — attendu un numéro français au format +33XXXXXXXXX.',
    });
  }

  try {
    const r = await fetch(URL_SMS, {
      method: 'POST',
      headers: {
        Authorization: `Api-Key ${cfg.apiKey}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ sender_id: cfg.senderId, to, message: MESSAGE }),
    });

    if (r.ok) return res.status(200).json({ status: 'envoye' });

    // Allo peut répondre autre chose que le JSON documenté : page 404 de la
    // gateway, ou code d'erreur absent de CODES. Sans le corps brut, un 404 est
    // indistinguable d'un mauvais chemin — on le remonte donc tel quel.
    const brut = await r.text().catch(() => '');
    let erreur = {};
    try { erreur = JSON.parse(brut); } catch { /* corps non JSON */ }
    // Le code vit sous `error`; on accepte aussi la racine au cas où un autre
    // endpoint réponde à plat. Faute de code connu, on montre le corps brut.
    const code = erreur.error?.code || erreur.code;
    const extrait = brut.replace(/\s+/g, ' ').trim().slice(0, 200);
    return res.status(502).json({
      error:
        CODES[code] ||
        `Allo (SMS) a répondu ${r.status}${extrait ? ` — ${extrait}` : ''}`,
      detail: { status: r.status, url: URL_SMS, code, corps: brut.slice(0, 500) },
    });
  } catch (e) {
    return res.status(500).json({ error: 'Appel à Allo impossible.', detail: String(e) });
  }
}
