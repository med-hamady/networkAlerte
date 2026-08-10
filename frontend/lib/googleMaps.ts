// Chargement du script Google Maps — UN SEUL point d'entrée pour toute
// l'application.
//
// ⚠️ Ce module existe parce que le verrou doit être au niveau MODULE, pas au
// niveau composant. Deux pages en ont besoin (la carte des clients et la vue
// carte de la topologie) ; si chacune portait sa propre copie du chargeur,
// chacune aurait sa propre variable `mapsPromise` et injecterait son <script>,
// ce que Google refuse bruyamment : « You have included the Google Maps
// JavaScript API multiple times on this page ». Le même raisonnement vaut déjà
// pour le double-rendu du StrictMode à l'intérieur d'une seule page.

// La clé est PUBLIQUE par nature (le script tourne dans le navigateur) — d'où
// NEXT_PUBLIC_. Elle doit être restreinte par référent HTTP côté Google Cloud,
// sinon n'importe qui peut la consommer sur notre facture. Ce n'est pas un
// secret : c'est un quota nominatif.
export const MAPS_KEY = process.env.NEXT_PUBLIC_GOOGLE_MAPS_API_KEY ?? ''

// Nouakchott — centre par défaut tant qu'aucun point n'est chargé.
export const DEFAULT_CENTER = { lat: 18.0858, lng: -15.9785 }

let mapsPromise: Promise<void> | null = null

export function loadGoogleMaps(): Promise<void> {
  if (typeof window === 'undefined') return Promise.resolve()
  if ((window as any).google?.maps) return Promise.resolve()
  if (mapsPromise) return mapsPromise
  mapsPromise = new Promise<void>((resolve, reject) => {
    const s = document.createElement('script')
    s.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(MAPS_KEY)}&v=weekly`
    s.async = true
    s.onload = () => resolve()
    s.onerror = () => {
      mapsPromise = null // laisse une nouvelle tentative possible
      reject(new Error('script Google Maps injoignable'))
    }
    document.head.appendChild(s)
  })
  return mapsPromise
}
