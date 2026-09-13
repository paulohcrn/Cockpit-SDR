// Accès Minari partagé par les endpoints /api/*. Le préfixe "_" empêche Vercel
// d'en faire une route.
//
// Variables d'environnement (Vercel > Settings > Environment Variables) :
//   MINARI_API_KEY          clé API Minari (Settings > API & webhook sur app.minari.ai)
//   MINARI_USER_MAP         (option) correspondances SDR -> compte Minari, quand les
//                           emails diffèrent des deux côtés. Format :
//                             sdr@example.com=1956,autre@example.com=bob@example.com
//                           La cible est un ID utilisateur Minari ou un email Minari.
//   MINARI_DEFAULT_USER_ID  (option) compte Minari qui récupère les listes quand ni
//                           l'email ni le mapping ne donnent de correspondance.

export const MINARI_BASE = 'https://api.minari.ai/v1';

export function requireApiKey() {
  return process.env.MINARI_API_KEY || null;
}

export function nonConfigure(res) {
  return res.status(503).json({
    error: 'Fonction non configurée.',
    detail: "Définir MINARI_API_KEY dans les variables d'environnement Vercel.",
  });
}

export function entetes(apiKey) {
  return { Authorization: `Bearer ${apiKey}`, 'Content-Type': 'application/json' };
}

// Les erreurs Minari arrivent enveloppées : { error: { code, message, details } }.
// `details` est un tableau de violations quand la validation du corps échoue.
export function messageErreur(corps, statut) {
  const err = corps && corps.error ? corps.error : {};
  const details = Array.isArray(err.details)
    ? err.details.map(d => `${d.path || ''} ${d.message || ''}`.trim()).join(' ; ')
    : typeof err.details === 'object' && err.details
      ? JSON.stringify(err.details)
      : '';
  return {
    error: err.message || `Minari a répondu ${statut}.`,
    detail: [err.code, details].filter(Boolean).join(' — ') || undefined,
  };
}

// GET/POST sur l'API Minari, avec le corps JSON déjà décodé (Minari répond en JSON
// y compris sur les erreurs) et le délai d'attente des 429 remonté tel quel.
export async function appelMinari(chemin, apiKey, options = {}) {
  const r = await fetch(`${MINARI_BASE}${chemin}`, { ...options, headers: entetes(apiKey) });
  const corps = await r.json().catch(() => ({}));
  if (r.status === 429) {
    const reset = r.headers.get('RateLimit-Reset');
    return {
      ok: false,
      statut: 429,
      corps,
      erreur: {
        error: 'Quota Minari atteint (60 requêtes/minute).',
        detail: reset ? `Réessayer dans ${reset} s.` : undefined,
      },
    };
  }
  return { ok: r.ok, statut: r.status, corps, erreur: r.ok ? null : messageErreur(corps, r.status) };
}

// Table `email SDR -> ID ou email Minari` lue depuis MINARI_USER_MAP.
function mappingSdr() {
  const brut = process.env.MINARI_USER_MAP || '';
  const table = new Map();
  for (const paire of brut.split(',')) {
    const [source, cible] = paire.split('=').map(s => (s || '').trim());
    if (source && cible) table.set(source.toLowerCase(), cible.toLowerCase());
  }
  return table;
}

// Minari identifie ses teammates par ID numérique. On résout dans cet ordre :
// email identique des deux côtés, puis MINARI_USER_MAP, puis MINARI_DEFAULT_USER_ID.
export function resoudreUtilisateur(users, email) {
  const parEmail = e => users.find(u => (u.email || '').toLowerCase() === e);
  const parId = id => users.find(u => String(u.id) === String(id));

  const direct = email && parEmail(email);
  if (direct) return { user: direct, via: 'email' };

  const cible = email && mappingSdr().get(email);
  if (cible) {
    const mappe = parId(cible) || parEmail(cible);
    if (mappe) return { user: mappe, via: 'mapping' };
    return { user: null, via: 'mapping', invalide: cible };
  }

  const defaut = process.env.MINARI_DEFAULT_USER_ID;
  if (defaut) {
    const replie = parId(defaut) || parEmail(String(defaut).toLowerCase());
    if (replie) return { user: replie, via: 'defaut' };
    return { user: null, via: 'defaut', invalide: defaut };
  }

  return { user: null, via: null };
}
