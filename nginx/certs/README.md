# Certificats TLS

nginx (en mode prod) lit ces deux fichiers :

- `fullchain.pem` — certificat serveur + chaîne intermédiaire
- `privkey.pem`   — clé privée

## Self-signed (réseau interne / maquette)

Depuis la racine du projet, en PowerShell :

```powershell
.\scripts\generate-self-signed-cert.ps1 -CommonName supervisor.local
```

Le script génère `nginx/certs/fullchain.pem` et `nginx/certs/privkey.pem`.

## Certificat émis par une CA interne (Active Directory)

Demandez à la DSI un certificat serveur au format PEM. Si vous recevez du PFX :

```powershell
# Extraire la clé privée
openssl pkcs12 -in cert.pfx -nocerts -nodes -out privkey.pem
# Extraire le certificat + chaîne
openssl pkcs12 -in cert.pfx -clcerts -nokeys -out fullchain.pem
```

Placez les deux fichiers dans ce dossier.

## Let's Encrypt (uniquement si DNS public)

Hors scope d'un déploiement interne. Si nécessaire, ajouter un service `certbot`
dans `docker-compose.prod.yml` avec challenge HTTP-01 via le bloc `location /.well-known/acme-challenge/`.

## Sécurité

Les fichiers `.pem`, `.crt`, `.key` de ce dossier sont ignorés par Git
(`.gitignore` local). Ne jamais committer une clé privée.
