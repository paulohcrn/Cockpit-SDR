// Accès Allo partagé par les endpoints /api/*. Le préfixe "_" empêche Vercel
// d'en faire une route.
//
// Variables d'environnement (Vercel > Settings > Environment Variables) :
//   ALLO_API_KEY    clé API Allo, scope SMS_SEND (web.withallo.com > Settings > API)
//   ALLO_SENDER_ID  Sender ID alphanumérique vérifié par l'équipe Allo pour l'envoi
//                   de SMS en France — sans lui l'envoi échoue en SENDER_ID_NOT_ACTIVE

export const ALLO_BASE = 'https://api.withallo.com';

export function config() {
  const apiKey = process.env.ALLO_API_KEY;
  const senderId = process.env.ALLO_SENDER_ID;
  if (!apiKey || !senderId) return null;
  return { apiKey, senderId };
}

// Le push dans la queue de dialing n'a pas besoin de Sender ID (spécifique aux SMS).
export function requireApiKey() {
  return process.env.ALLO_API_KEY || null;
}

export function nonConfigure(res) {
  return res.status(503).json({
    error: 'Fonction non configurée.',
    detail: "Définir ALLO_API_KEY et ALLO_SENDER_ID dans les variables d'environnement Vercel.",
  });
}
