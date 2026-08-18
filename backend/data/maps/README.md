# Fonds de carte de la cartographie des sites

Ces trois images sont le **support** de l'export Word de la carte des sites
(`GET /api/v1/network-topology/export/word`, page **Topologie** → bouton
« Carte Word »). Le service `site_map_service` y compose les pastilles de site,
les backhauls et les noms, puis assemble le document.

| Fichier | Ville | Cadrage |
|---|---|---|
| `nouakchott.jpg` | Nouakchott — 17 en service, 2 programmés | 2051 × 2500 px, z14 |
| `nouadhibou.jpg` | Nouadhibou — 3 programmés | 1223 × 1435 px, z14 |
| `rosso.jpg` | Rosso — 2 programmés | 979 × 875 px, z15 |
| `bounds.json` | Contrat image ↔ géographie | écrit par le script |

## Pourquoi ils sont commités

L'export ne télécharge **rien** au moment où on le demande. Le serveur de prod
est derrière le FortiGate et n'a pas d'accès Internet sortant garanti : un
export qui dépendrait d'un CDN de tuiles échouerait précisément le jour où on
en a besoin. C'est aussi ce qui rend l'export instantané et reproductible.

Contrepartie assumée : **le cadrage est figé**. Un site posé hors de la fenêtre
d'une ville ne peut pas être dessiné — il est alors **nommé** en fin de document
(« Sites non représentés : … ») plutôt qu'escamoté. C'est le signal qu'il faut
élargir la fenêtre.

## Les régénérer

Depuis un poste qui a Internet, puis commiter le résultat :

```bash
python scripts/build_site_map_basemaps.py               # les 3 villes
python scripts/build_site_map_basemaps.py --city rosso  # une seule
```

Pour changer un cadrage, éditer `_WINDOWS` dans ce script — jamais `bounds.json`
à la main : ce fichier lie la fenêtre géographique aux dimensions en pixels de
l'image, et les désaccorder pose les sites à côté de leur vraie place **sans que
rien n'échoue**. C'est le script qui l'écrit, pour que les deux restent d'accord.

Le script met les tuiles en cache dans `scripts/.tilecache/` (ignoré par git) :
un second passage ne retélécharge rien.

## Sources et licence

Imagerie **satellite Esri World Imagery** (Maxar, Earthstar Geographics), avec
le calque de repères Esri (`World_Boundaries_and_Places`) composé par-dessus :
sur de l'imagerie brute, ce sont les grands axes et les noms de quartiers qui
permettent de situer un site autrement qu'en reconnaissant la forme des toits.

La mention de source est dessinée dans chaque planche par
`site_map_service._attribution` — dans l'image et non à côté, pour qu'elle
survive à un copier-coller de la carte hors du document. Son texte vient du
champ `attribution` de `bounds.json`, donc du script qui a téléchargé les
tuiles : changer de fournisseur d'imagerie sans changer la mention est
impossible.

## Polices

Les noms de site sont tracés avec **DejaVu Sans Bold**, installé dans l'image
backend par `fonts-dejavu-core` (Dockerfile). Sans police vectorielle, le
service refuse de rendre plutôt que de produire une carte aux noms illisibles.
