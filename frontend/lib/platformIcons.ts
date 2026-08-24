/**
 * Logos des plateformes filtrables (page « Filtre de contenu »).
 *
 * Les fichiers vivent dans `public/platforms/` et sont nommés d'après la CLÉ du
 * catalogue servie par `GET /content-filter/platforms` — donc une plateforme
 * ajoutée côté backend n'a qu'à déposer son `<clé>.png` ici pour être illustrée,
 * sans toucher à ce fichier.
 *
 * ⚠️ `public/platforms/` doit rester listé dans le matcher de `middleware.ts` :
 * sans ça le middleware d'auth happe la requête et renvoie la page /login à la
 * place du PNG — l'image casse sans qu'aucune erreur ne soit levée.
 *
 * ⚠️ Toutes les clés n'ont pas d'icône (Google et YouTube n'en ont pas
 * aujourd'hui). C'est un état NORMAL, pas une erreur à corriger dans le code :
 * l'appelant retombe sur une pastille neutre portant l'initiale, qui garde
 * l'alignement de la grille sans inventer un logo qu'on n'a pas.
 */

// Clés effectivement illustrées. Volontairement une liste explicite plutôt
// qu'un test d'existence de fichier : le rendu est statique, une image
// manquante ne se découvrirait qu'à l'écran, chez l'opérateur.
const WITH_ICON = new Set([
  'adult',
  'facebook',
  'snapchat',
  'telegram',
  'tiktok',
  'whatsapp',
])

/** Chemin du logo d'une plateforme, ou `null` si nous n'en avons pas. */
export function platformIcon(key: string): string | null {
  return WITH_ICON.has(key) ? `/platforms/${key}.png` : null
}
