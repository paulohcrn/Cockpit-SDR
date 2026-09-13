// Accès GitHub partagé par les endpoints /api/*. Le préfixe "_" empêche Vercel
// d'en faire une route.
//
// Variables d'environnement (Vercel > Settings > Environment Variables) :
//   GITHUB_TOKEN  jeton fine-grained : "Actions: read and write" + "Contents: read and write"
//   GITHUB_REPO   "organisation/depot"
//   GITHUB_REF    optionnel, branche visée (défaut "main")

export const WORKFLOW = 'refresh-sdr-dashboard.yml';
export const SDRS_PATH = 'sdrs.json';

export function config() {
  const token = process.env.GITHUB_TOKEN;
  const repo = process.env.GITHUB_REPO;
  const ref = process.env.GITHUB_REF || 'main';
  if (!token || !repo) return null;
  return { token, repo, ref };
}

export function nonConfigure(res) {
  return res.status(503).json({
    error: 'Fonction non configurée.',
    detail: "Définir GITHUB_TOKEN et GITHUB_REPO dans les variables d'environnement Vercel.",
  });
}

export async function gh(path, token, init = {}) {
  return fetch(`https://api.github.com${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'Content-Type': 'application/json',
      'User-Agent': 'cockpit-sdr',
      ...(init.headers || {}),
    },
  });
}

/** Un run est-il déjà en cours ? Deux runs se marcheraient dessus au commit. */
export async function runEnCours({ token, repo }) {
  const res = await gh(
    `/repos/${repo}/actions/workflows/${WORKFLOW}/runs?status=in_progress&per_page=1`,
    token
  );
  if (!res.ok) return null;
  const body = await res.json();
  return body.total_count > 0 ? body.workflow_runs?.[0]?.html_url || true : null;
}

export async function lancerWorkflow({ token, repo, ref }) {
  const res = await gh(`/repos/${repo}/actions/workflows/${WORKFLOW}/dispatches`, token, {
    method: 'POST',
    body: JSON.stringify({ ref }),
  });
  if (res.status === 204) return { ok: true };
  return { ok: false, status: res.status, detail: await res.text() };
}

/** Traduit une erreur GitHub en message actionnable pour le SDR. */
export function messageErreur(status, repo, ref) {
  if (status === 401 || status === 403) {
    return {
      error: 'GitHub a refusé le jeton.',
      detail: 'Vérifier que GITHUB_TOKEN a les permissions "Actions: read and write" et "Contents: read and write" sur le dépôt.',
    };
  }
  if (status === 404) {
    return {
      error: 'Dépôt, branche ou fichier introuvable.',
      detail: `Vérifier GITHUB_REPO ("${repo}") et la branche "${ref}".`,
    };
  }
  return { error: `GitHub a répondu ${status}.` };
}
