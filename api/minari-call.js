// POST /api/minari-call — crée une liste d'appel Minari à partir des leads déjà
// connus (data.json), assignée au SDR sélectionné, pour appeler sans passer par
// l'export CSV puis un import manuel.
//
// La clé Minari reste côté serveur : le navigateur envoie les leads, l'email du
// SDR et un libellé de liste, jamais la clé. Le serveur résout l'ID utilisateur
// Minari via GET /users (Minari identifie les teammates par ID, pas par email)
// puis crée la liste via POST /lists avec ce compte en assignedTo.

import {
  requireApiKey, nonConfigure, appelMinari, resoudreUtilisateur,
} from './_minari.js';

// Colonnes du CSV Allo sans équivalent dans le schéma contact Minari : elles
// partent en customFields, qui n'accepte que des clés déjà déclarées côté Minari.
const CHAMPS_PERSO = { company_linkedin: 'LinkedIn entreprise' };

const LIMITE_CONTACTS = 1500;

// Minari refuse tout le lot si une clé de customFields n'est pas enregistrée :
// on déclare celles qui manquent, et on abandonne la colonne plutôt que l'envoi
// si la déclaration échoue.
async function champsPersoDisponibles(apiKey, requis) {
  if (!requis.length) return new Set();

  const lecture = await appelMinari('/custom-fields', apiKey);
  if (!lecture.ok) return new Set();
  const existants = new Set((lecture.corps.data || []).map(c => c.id));

  for (const id of requis) {
    if (existants.has(id)) continue;
    const creation = await appelMinari('/custom-fields', apiKey, {
      method: 'POST',
      body: JSON.stringify({ id, label: CHAMPS_PERSO[id] || id }),
    });
    // 409 = déclaré entre-temps, la clé est utilisable malgré l'échec.
    if (creation.ok || creation.statut === 409) existants.add(id);
  }
  return existants;
}

// Contexte de campagne et lien HubSpot n'ont pas de colonne Minari : ils vont en
// note, pour que le SDR ait sous les yeux ce que le CSV lui donnait.
function note(lead) {
  return [lead.contexte, lead.hubspot_url].filter(Boolean).join('\n').slice(0, 5000);
}

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    res.setHeader('Allow', 'POST');
    return res.status(405).json({ error: 'Utiliser POST.' });
  }

  const apiKey = requireApiKey();
  if (!apiKey) return nonConfigure(res);

  const corps = typeof req.body === 'string' ? JSON.parse(req.body) : req.body || {};
  const leads = Array.isArray(corps.leads) ? corps.leads : [];
  const email = typeof corps.email === 'string' ? corps.email.trim().toLowerCase() : '';
  const label = typeof corps.label === 'string' && corps.label.trim()
    ? corps.label.trim().slice(0, 255)
    : 'Cockpit SDR';

  if (!email) {
    return res.status(400).json({ error: 'Email du SDR manquant pour assigner la liste.' });
  }

  const avecNumero = leads.filter(l => l && typeof l.number === 'string' && l.number);
  // Minari exige au moins un prénom, un nom ou un email par contact : un lead
  // réduit à son numéro ferait échouer tout le lot.
  const exploitables = avecNumero.filter(l => l.name || l.last_name || l.emails);
  const sansIdentite = avecNumero.length - exploitables.length;

  if (!exploitables.length) {
    return res.status(400).json({
      error: 'Aucun contact exploitable à envoyer.',
      detail: avecNumero.length
        ? 'Minari exige un prénom, un nom ou un email en plus du numéro.'
        : undefined,
    });
  }
  if (exploitables.length > LIMITE_CONTACTS) {
    return res.status(400).json({
      error: `Plus de ${LIMITE_CONTACTS} contacts : limite Minari par liste.`,
    });
  }

  try {
    const lecture = await appelMinari('/users', apiKey);
    if (!lecture.ok) return res.status(lecture.statut === 429 ? 429 : 502).json(lecture.erreur);

    const users = lecture.corps.data || [];
    const { user, via, invalide } = resoudreUtilisateur(users, email);
    if (!user) {
      const connus = users.map(u => `${u.email} (id ${u.id})`).join(', ') || 'aucun';
      return res.status(404).json({
        error: invalide
          ? `Le compte Minari « ${invalide} » configuré pour ${email} n'existe pas.`
          : `Aucun compte Minari pour ${email}.`,
        detail: `Comptes Minari disponibles : ${connus}. `
          + 'Aligner les emails (Settings > Team) ou renseigner MINARI_USER_MAP / '
          + 'MINARI_DEFAULT_USER_ID côté Vercel.',
      });
    }

    const requis = Object.keys(CHAMPS_PERSO)
      .filter(id => exploitables.some(l => l[id]));
    const perso = await champsPersoDisponibles(apiKey, requis);

    const contacts = exploitables.map(l => {
      const customFields = {};
      for (const id of Object.keys(CHAMPS_PERSO)) {
        if (l[id] && perso.has(id)) customFields[id] = String(l[id]);
      }
      const n = note(l);
      return {
        ...(l.name ? { firstName: l.name } : {}),
        ...(l.last_name ? { lastName: l.last_name } : {}),
        ...(l.emails ? { email: l.emails } : {}),
        ...(l.company ? { company: l.company } : {}),
        ...(l.job_title ? { title: l.job_title } : {}),
        ...(l.linkedin ? { linkedinUrl: l.linkedin } : {}),
        phoneNumber1: l.number,
        ...(n ? { note: n } : {}),
        ...(Object.keys(customFields).length ? { customFields } : {}),
      };
    });

    const creation = await appelMinari('/lists', apiKey, {
      method: 'POST',
      body: JSON.stringify({ name: label, assignedTo: user.id, contacts }),
    });
    if (!creation.ok) {
      return res.status(creation.statut === 429 ? 429 : 502).json(creation.erreur);
    }

    const { listId, addedCount, skippedCount } = creation.corps.data || {};
    return res.status(200).json({
      status: 'cree',
      listId,
      addedCount: addedCount ?? contacts.length,
      skippedCount: skippedCount ?? 0,
      // Le SDR doit savoir sur quel compte la liste a atterri quand ce n'est pas le sien.
      assignedTo: { id: user.id, name: user.name, email: user.email, via },
      ...(sansIdentite ? { ignores: sansIdentite } : {}),
    });
  } catch (e) {
    return res.status(500).json({ error: 'Appel à Minari impossible.', detail: String(e) });
  }
}
