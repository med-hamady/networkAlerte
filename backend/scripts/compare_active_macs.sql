-- Comparaison : liste de MAC « actives » d'un autre système  vs  nos clients ACTIFS.
--
-- « Actif » chez nous = exactement la tuile « Accès actif » de /access, c.-à-d.
-- les mêmes critères que `fn_access_clients` : un LR **non bloqué** ET **pas hors
-- supervision** (hors supervision = pas d'IP ET (jamais vu par UISP OU vu il y a
-- plus de OUT_OF_SUPERVISION_DAYS, défaut 7 j)).
--
-- Le `status` (up/down/unknown) vient du sweep de ping : il dit si le client
-- répond réellement, indépendamment du fait qu'il soit « actif » au sens contrat.
--
-- Usage :
--   dc exec -T postgres psql -U supervisor -d network_supervisor \
--     -f - < backend/scripts/compare_active_macs.sql
--
-- Pour changer la fenêtre « hors supervision », remplacer les deux occurrences
-- de `interval '7 days'` ci-dessous.
--
-- La liste externe ci-dessous est celle du 2026-08-06.

\set ON_ERROR_STOP on

-- ─────────────────────────────────────────────────────────────────────────────
-- Les deux ensembles, montés une fois en table temporaire pour les 3 sorties.
-- ─────────────────────────────────────────────────────────────────────────────

-- Pas de ON COMMIT DROP : psql valide chaque instruction, la table serait détruite
-- avant la requête suivante. Les tables temporaires meurent avec la session.
CREATE TEMP TABLE other_active (mac text PRIMARY KEY);

INSERT INTO other_active (mac)
SELECT DISTINCT lower(replace(m, '-', ':'))
FROM (VALUES
 ('f4:e2:c6:30:72:ca'),('ac:8b:a9:50:5b:e1'),('d0:21:f9:f6:08:0d'),('d0:21:f9:f6:07:5e'),
 ('d0:21:f9:f6:0a:12'),('6c:63:f8:b4:d1:70'),('6c:63:f8:b8:e7:70'),('6c:63:f8:b8:c1:63'),
 ('60:22:32:ca:57:18'),('60:22:32:c8:8e:b7'),('f4:e2:c6:30:72:4c'),('6c:63:f8:cc:d0:a3'),
 ('f4:e2:c6:30:74:5c'),('d0:21:f9:af:19:78'),('f4:92:bf:5f:6d:44'),('d0:21:f9:f0:a4:12'),
 ('6c:63:f8:b4:60:be'),('d0:21:f9:af:19:a7'),('d0:21:f9:f6:07:91'),('d0:21:f9:f6:07:4c'),
 ('6c:63:f8:b4:de:b0'),('d0:21:f9:fa:a2:3f'),('d0:21:f9:f0:b2:5f'),('d0:21:f9:f0:a3:dc'),
 ('f4:92:bf:5f:78:30'),('1c:6a:1b:c2:63:9e'),('d0:21:f9:f6:09:49'),('d0:21:f9:f6:06:5d'),
 ('d0:21:f9:f6:07:1e'),('6c:63:f8:cc:ce:8a'),('d0:21:f9:af:19:6a'),('6c:63:f8:b4:2e:6f'),
 ('d0:21:f9:f6:07:c2'),('6c:63:f8:b8:c2:3b'),('f4:92:bf:5f:91:68'),('6c:63:f8:cc:67:99'),
 ('1c:6a:1b:c2:63:49'),('6c:63:f8:b8:e2:9b'),('6c:63:f8:b2:1c:34'),('f4:92:bf:5f:73:e9'),
 ('f4:92:bf:5f:91:4d'),('6c:63:f8:cc:d4:2b'),('d0:21:f9:fa:a0:67'),('d0:21:f9:f6:07:31'),
 ('6c:63:f8:b8:ca:fc'),('6c:63:f8:b8:c8:e7'),('78:45:58:5f:00:46'),('d0:21:f9:af:04:5e'),
 ('d0:21:f9:f6:08:29'),('6c:63:f8:cc:d6:39'),('ac:8b:a9:50:5a:aa'),('60:22:32:ca:54:ff'),
 ('f4:e2:c6:30:76:6f'),('1c:6a:1b:b8:78:ea'),('1c:6a:1b:b8:78:08'),('f4:e2:c6:30:71:ec'),
 ('6c:63:f8:b6:b3:e8'),('d0:21:f9:f0:a4:2f'),('d0:21:f9:fa:a3:57'),('1c:6a:1b:bc:20:21'),
 ('6c:63:f8:d2:56:97'),('6c:63:f8:cc:d4:cf'),('6c:63:f8:b8:e6:4a'),('f4:92:bf:5f:95:06'),
 ('6c:63:f8:b4:de:a5'),('6c:63:f8:b8:d0:5b'),('60:22:32:c8:8e:8d'),('6c:63:f8:b8:cd:06'),
 ('d0:21:f9:af:15:36'),('f4:e2:c6:30:72:d5'),('60:22:32:c8:87:75'),('f4:e2:c6:30:7b:53'),
 ('6c:63:f8:cc:d5:75'),('f4:92:bf:5f:8a:9c'),('1c:6a:1b:b8:79:b0'),('1c:6a:1b:c6:97:1c'),
 ('d0:21:f9:f0:a1:27'),('9c:05:d6:8c:ef:5f'),('6c:63:f8:b8:d1:3a'),('60:22:32:ca:57:09'),
 ('78:45:58:0b:bc:3a'),('f4:e2:c6:30:71:d6'),('1c:6a:1b:b4:30:9e'),('6c:63:f8:b4:54:cc'),
 ('6c:63:f8:b8:e6:b7'),('f4:92:bf:5f:91:ae'),('6c:63:f8:b6:be:02'),('6c:63:f8:b8:d3:34'),
 ('f4:92:bf:5f:7a:8e'),('6c:63:f8:b8:e4:6d'),('d0:21:f9:f6:0a:21'),('d0:21:f9:f6:0a:6c'),
 ('d0:21:f9:f0:a2:de'),('1c:6a:1b:bc:00:2d'),('d0:21:f9:af:16:04'),('ac:8b:a9:50:58:cc'),
 ('d0:21:f9:af:19:a8'),('d0:21:f9:f0:b3:7d'),('f4:e2:c6:30:75:b9'),('ac:8b:a9:1d:1f:3a'),
 ('60:22:32:ca:50:63'),('d0:21:f9:f6:0a:c2'),('f4:e2:c6:30:75:a2'),('6c:63:f8:cc:d1:bb'),
 ('6c:63:f8:b8:e2:e3'),('1c:6a:1b:b4:32:e4'),('60:22:32:c8:89:fd'),('d0:21:f9:f6:07:0f'),
 ('6c:63:f8:d2:56:a4'),('1c:6a:1b:c6:90:57'),('0c:ea:14:9c:d2:f5'),('60:22:32:ca:4a:eb'),
 ('1c:6a:1b:b6:36:84'),('6c:63:f8:cc:d6:81'),('f4:e2:c6:8c:d3:b5'),('1c:6a:1b:b4:2f:60'),
 ('6c:63:f8:cc:cf:b5'),('f4:e2:c6:38:7c:9e'),('f4:e2:c6:30:7b:6d'),('9c:05:d6:8c:f4:eb'),
 ('6c:63:f8:b8:e4:68'),('f4:e2:c6:30:75:61'),('d0:21:f9:f6:07:51'),('1c:6a:1b:b6:35:a0'),
 ('1c:6a:1b:c6:a5:0f'),('d0:21:f9:f0:a4:2e'),('6c:63:f8:cc:d6:7b'),('e4:38:83:b4:95:ae'),
 ('1c:6a:1b:c6:92:bb'),('1c:6a:1b:c2:62:13'),('60:22:32:ca:54:91'),('1c:6a:1b:b4:32:4b'),
 ('78:45:58:0b:ae:ab'),('6c:63:f8:cc:d6:e0'),('6c:63:f8:b8:e3:05'),('6c:63:f8:b8:cd:0c'),
 ('d0:21:f9:f0:a8:49'),('f4:92:bf:5f:91:eb'),('6c:63:f8:cc:d4:32'),('6c:63:f8:b4:2f:67'),
 ('f4:92:bf:5f:99:97'),('f4:e2:c6:30:74:5b'),('6c:63:f8:b8:e2:bc'),('f4:92:bf:1f:e7:96'),
 ('6c:63:f8:cc:ca:81'),('6c:63:f8:cc:d4:2a'),('d0:21:f9:f0:a7:1c'),('6c:63:f8:b8:e4:8c'),
 ('1c:6a:1b:c6:bd:09'),('6c:63:f8:b4:47:16'),('ac:8b:a9:50:5b:11'),('6c:63:f8:cc:d5:ab'),
 ('d0:21:f9:f0:a1:53'),('6c:63:f8:d2:4e:e8'),('f4:92:bf:5f:76:92'),('d0:21:f9:af:03:d9'),
 ('f4:92:bf:5f:7c:88'),('f4:e2:c6:30:7c:56'),('f4:e2:c6:30:77:d3'),('6c:63:f8:b8:e6:c8'),
 ('d0:21:f9:f0:a4:0f'),('f4:e2:c6:30:72:d6'),('6c:63:f8:b8:e5:7b'),('f4:e2:c6:30:71:58'),
 ('d0:21:f9:af:15:28'),('6c:63:f8:b8:ca:f3'),('78:45:58:0b:bc:98'),('6c:63:f8:b8:ca:10'),
 ('6c:63:f8:b4:2f:65'),('ac:8b:a9:50:5c:65'),('6c:63:f8:b4:3e:7f'),('d0:21:f9:f2:5b:cd'),
 ('1c:6a:1b:c6:97:32'),('ac:8b:a9:50:5b:4b'),('d0:21:f9:f6:06:81'),('d0:21:f9:f0:b3:6c'),
 ('d0:21:f9:f6:07:d7'),('f4:92:bf:5f:7a:7a'),('d0:21:f9:fa:9e:07'),('e0:63:da:bf:ca:04'),
 ('d0:21:f9:af:04:dd'),('d0:21:f9:f0:a1:9d'),('d0:21:f9:f6:06:1b'),('ac:8b:a9:50:5b:d1'),
 ('6c:63:f8:cc:d6:6d'),('6c:63:f8:cc:d4:0a'),('60:22:32:ca:55:60'),('1c:6a:1b:b4:30:a1'),
 ('ac:8b:a9:50:57:be'),('6c:63:f8:b8:e2:c2'),('60:22:32:e0:61:8a'),('1c:6a:1b:c6:bd:f3'),
 ('0c:ea:14:9c:d2:81'),('d0:21:f9:f6:09:ae'),('6c:63:f8:b8:e4:aa'),('f4:e2:c6:30:7c:98'),
 ('f4:e2:c6:30:72:24'),('ac:8b:a9:50:57:a2'),('6c:63:f8:b8:e5:3d'),('d0:21:f9:fa:a3:79'),
 ('d0:21:f9:f6:08:0e'),('ac:8b:a9:50:5b:72'),('d0:21:f9:fa:9e:3f'),('1c:6a:1b:c6:90:25'),
 ('ac:8b:a9:1d:20:7b'),('6c:63:f8:b8:d2:ca'),('1c:6a:1b:b8:79:c3'),('f4:92:bf:5f:97:1f'),
 ('d0:21:f9:af:04:be'),('d0:21:f9:af:16:49'),('d0:21:f9:f6:07:7d'),('6c:63:f8:d2:4f:84'),
 ('78:45:58:0b:bc:1a'),('6c:63:f8:b4:5f:cd'),('d0:21:f9:f6:06:7b'),('ac:8b:a9:50:5b:5c'),
 ('6c:63:f8:cc:d5:32'),('f4:92:bf:5f:94:6c'),('6c:63:f8:d2:56:aa'),('6c:63:f8:cc:d0:22'),
 ('6c:63:f8:b8:c2:70'),('1c:6a:1b:b8:7c:af'),('d0:21:f9:fa:a2:a6'),('d0:21:f9:f0:b3:2c'),
 ('6c:63:f8:b8:e4:fd'),('6c:63:f8:b8:d2:53'),('d0:21:f9:f6:06:2b'),('6c:63:f8:cc:ca:b7'),
 ('6c:63:f8:cc:d4:42'),('d0:21:f9:f6:0b:aa'),('d0:21:f9:f0:b3:ba'),('d0:21:f9:f6:08:7c'),
 ('6c:63:f8:cc:d5:ca'),('6c:63:f8:b8:e6:d9'),('1c:6a:1b:bc:1f:8c'),('1c:6a:1b:b8:79:0a'),
 ('d0:21:f9:af:04:61'),('6c:63:f8:b8:cd:a5'),('6c:63:f8:b8:c6:05'),('d0:21:f9:af:04:bc'),
 ('d0:21:f9:f6:06:e8'),('1c:6a:1b:b8:7c:58'),('1c:6a:1b:c6:bd:f8'),('6c:63:f8:b8:e5:30'),
 ('6c:63:f8:b8:e3:e3'),('d0:21:f9:af:17:98'),('d0:21:f9:fa:9e:d5'),('1c:6a:1b:c6:96:c9'),
 ('6c:63:f8:b4:5e:a5'),('6c:63:f8:cc:cd:50'),('6c:63:f8:cc:d6:a5'),('6c:63:f8:cc:ca:76'),
 ('6c:63:f8:b8:e6:90'),('d0:21:f9:af:04:ae'),('6c:63:f8:cc:cf:11'),('f4:92:bf:5f:94:01'),
 ('1c:6a:1b:c6:90:4a'),('6c:63:f8:b4:e3:3b'),('6c:63:f8:b8:e3:7c'),('f4:92:bf:5f:93:5e'),
 ('1c:6a:1b:c6:8e:c0'),('6c:63:f8:b8:cd:97'),('1c:6a:1b:c6:90:70'),('6c:63:f8:b8:e5:39'),
 ('6c:63:f8:b8:cc:30'),('d0:21:f9:fa:9d:04'),('6c:63:f8:b8:c3:dd'),('6c:63:f8:b8:e4:47'),
 ('d0:21:f9:f6:06:15'),('d0:21:f9:f6:07:5b'),('f4:92:bf:5f:96:f7'),('1c:6a:1b:bc:08:a8'),
 ('1c:6a:1b:b4:36:4a'),('d0:21:f9:f6:06:dd'),('6c:63:f8:d2:56:9f'),('6c:63:f8:b8:c4:68'),
 ('d0:21:f9:f6:06:3b'),('1c:6a:1b:c6:bb:7f'),('6c:63:f8:b8:e4:2c'),('1c:6a:1b:c6:8f:e7'),
 ('6c:63:f8:b4:2c:8b'),('1c:6a:1b:b4:36:34'),('ac:8b:a9:50:57:ed'),('60:22:32:ca:56:07'),
 ('1c:6a:1b:b4:32:97'),('6c:63:f8:b8:c5:aa'),('d0:21:f9:f6:09:43'),('6c:63:f8:b4:d9:35'),
 ('6c:63:f8:b8:e2:22'),('6c:63:f8:b8:cc:6c'),('d0:21:f9:af:04:49'),('6c:63:f8:b8:e3:65'),
 ('6c:63:f8:cc:d3:c0'),('1c:6a:1b:c6:c3:79'),('9c:05:d6:8c:e8:80'),('6c:63:f8:b8:ce:94'),
 ('6c:63:f8:b8:d2:4f'),('6c:63:f8:b6:ba:43'),('6c:63:f8:cc:d5:73'),('6c:63:f8:cc:d0:a4'),
 ('d0:21:f9:fa:a0:f4'),('d0:21:f9:f6:0b:89'),('ac:8b:a9:50:5b:b2'),('6c:63:f8:cc:ca:f3'),
 ('1c:6a:1b:bc:09:29'),('1c:6a:1b:c6:a5:9c'),('6c:63:f8:cc:d3:77'),('6c:63:f8:b4:42:79'),
 ('9c:05:d6:8c:f6:c9'),('6c:63:f8:b8:e2:8e'),('9c:05:d6:8e:05:4a'),('1c:6a:1b:b4:35:06'),
 ('d0:21:f9:af:04:19'),('1c:6a:1b:c6:97:af'),('6c:63:f8:b8:e1:31'),('6c:63:f8:b4:3c:4c'),
 ('1c:6a:1b:b4:32:cc'),('1c:6a:1b:b4:35:0f'),('d0:21:f9:fa:a2:ff'),('e0:63:da:bf:c8:6f'),
 ('1c:6a:1b:b8:7a:99'),('6c:63:f8:cc:d3:3e'),('6c:63:f8:cc:d6:01'),('1c:6a:1b:b4:2b:d7'),
 ('6c:63:f8:cc:d6:fd'),('78:45:58:0b:bb:e3'),('1c:6a:1b:c6:bc:45'),('6c:63:f8:b8:cd:41'),
 ('6c:63:f8:cc:cd:b5'),('d0:21:f9:af:19:77'),('d0:21:f9:f6:07:cf'),('6c:63:f8:cc:d5:7e'),
 ('ac:8b:a9:50:5b:b3'),('60:22:32:ca:57:be'),('1c:6a:1b:c2:65:0a'),('6c:63:f8:b8:e1:88'),
 ('9c:05:d6:8c:f4:7f'),('6c:63:f8:d2:56:b7'),('d0:21:f9:f0:b0:ba'),('6c:63:f8:cc:d1:ac'),
 ('1c:6a:1b:c2:65:04'),('6c:63:f8:cc:ca:cb'),('1c:6a:1b:c2:65:7e'),('1c:6a:1b:c6:bc:fe'),
 ('6c:63:f8:b8:e6:97'),('d0:21:f9:f6:05:d5'),('f4:92:bf:5f:96:87'),('6c:63:f8:b8:d1:53'),
 ('6c:63:f8:d2:4e:c8'),('ac:8b:a9:50:57:a1'),('6c:63:f8:b8:df:a8'),('6c:63:f8:cc:d4:3c'),
 ('e4:38:83:b4:94:5b'),('d0:21:f9:f6:07:19'),('1c:6a:1b:c6:99:72'),('1c:6a:1b:b8:7b:90'),
 ('9c:05:d6:8c:f5:0c'),('78:45:58:0b:bc:5e'),('d0:21:f9:f0:a7:28'),('1c:6a:1b:bc:1e:16'),
 ('1c:6a:1b:b8:77:7b'),('1c:6a:1b:bc:1d:a1'),('6c:63:f8:cc:d2:68'),('1c:6a:1b:b4:32:fc'),
 ('6c:63:f8:cc:ca:db'),('d0:21:f9:af:04:16'),('78:45:58:0b:bc:4f'),('78:45:58:0b:bc:92'),
 ('6c:63:f8:b8:a5:fd'),('d0:21:f9:fa:a3:1d'),('6c:63:f8:b4:42:82'),('1c:6a:1b:c6:90:00'),
 ('78:45:58:0b:bc:0e'),('1c:6a:1b:c2:64:fc'),('d0:21:f9:fa:a2:a5'),('6c:63:f8:cc:d0:9b'),
 ('1c:6a:1b:c6:9a:26'),('78:45:58:0b:bb:f9'),('1c:6a:1b:c6:9a:5f'),('78:45:58:0b:bb:fa'),
 ('6c:63:f8:b8:da:ce'),('78:45:58:0b:bc:76'),('78:45:58:0b:bc:20'),('1c:6a:1b:c6:97:fa'),
 ('d0:21:f9:f0:a8:09'),('78:45:58:0b:bc:80'),('78:45:58:0b:bc:87'),('78:45:58:0b:bc:39'),
 ('d0:21:f9:f6:07:01'),('d0:21:f9:af:03:14'),('78:45:58:0b:bd:7d'),('6c:63:f8:b8:c5:ad'),
 ('60:22:32:ca:4f:82'),('d0:21:f9:af:04:4b'),('6c:63:f8:b8:cd:4b'),('6c:63:f8:b8:df:ae'),
 ('78:45:58:0b:bc:24'),('78:45:58:5f:00:3f'),('1c:6a:1b:b8:79:24'),('1c:6a:1b:c6:8f:f3'),
 ('d0:21:f9:f0:a8:28'),('78:45:58:0b:bb:f4'),('78:45:58:0b:bc:08'),('d0:21:f9:f6:06:db'),
 ('f4:92:bf:5f:8e:3a'),('d0:21:f9:af:17:32'),('d0:21:f9:af:15:30'),('9c:05:d6:8c:eb:47'),
 ('6c:63:f8:cc:cf:b3'),('d0:21:f9:af:04:bf'),('d0:21:f9:af:15:2e'),('6c:63:f8:b4:df:fe'),
 ('d0:21:f9:af:17:5a'),('d0:21:f9:af:16:2e'),('78:45:58:0b:bc:1e'),('f4:92:bf:5f:92:01'),
 ('d0:21:f9:af:14:d7'),('d0:21:f9:af:04:9c'),('78:45:58:5f:00:2d'),('78:45:58:5f:00:75'),
 ('d0:21:f9:af:16:42'),('d0:21:f9:af:15:44'),('d0:21:f9:af:17:88'),('d0:21:f9:af:16:2f'),
 ('d0:21:f9:af:16:e0'),('6c:63:f8:b8:cd:e4'),('9c:05:d6:8c:f5:79'),('6c:63:f8:cc:ce:b1'),
 ('d0:21:f9:af:17:93'),('6c:63:f8:cc:ca:70'),('d0:21:f9:af:17:79'),('d0:21:f9:af:05:1e'),
 ('d0:21:f9:af:17:b2'),('d0:21:f9:af:15:40'),('d0:21:f9:af:04:8a'),('d0:21:f9:af:16:67'),
 ('f4:92:bf:5f:91:b3'),('d0:21:f9:af:17:8c'),('d0:21:f9:af:14:d5'),('d0:21:f9:f0:a1:8d'),
 ('78:45:58:0b:bb:a7'),('1c:6a:1b:c2:65:ff'),('d0:21:f9:f6:07:38'),('d0:21:f9:af:15:96'),
 ('6c:63:f8:b8:c6:49'),('d0:21:f9:af:04:7c'),('6c:63:f8:b4:20:c3'),('d0:21:f9:af:14:c6'),
 ('6c:63:f8:b4:2a:5c'),('78:45:58:0b:bb:b1'),('d0:21:f9:f6:07:12'),('78:45:58:0b:bb:15'),
 ('f4:92:bf:5f:91:5b'),('6c:63:f8:cc:d4:5e'),('60:22:32:ca:49:67'),('d0:21:f9:af:16:b2'),
 ('d0:21:f9:af:15:37'),('d0:21:f9:af:04:9a'),('d0:21:f9:af:17:b0'),('6c:63:f8:b4:ea:8f'),
 ('d0:21:f9:af:16:00'),('d0:21:f9:af:15:ae'),('6c:63:f8:b8:c6:c1'),('78:45:58:0b:ba:ea'),
 ('60:22:32:d6:9a:e7'),('1c:6a:1b:c6:c3:7c'),('78:45:58:0b:bb:1e'),('78:45:58:0b:bb:17'),
 ('d0:21:f9:f0:a1:2e'),('6c:63:f8:cc:ca:c3'),('1c:6a:1b:c2:63:fc'),('9c:05:d6:8c:e8:2c'),
 ('d0:21:f9:af:03:ad'),('9c:05:d6:8c:ef:6f'),('9c:05:d6:8c:e8:3b'),('1c:6a:1b:c2:69:af'),
 ('d0:21:f9:af:05:2e'),('d0:21:f9:af:15:3f'),('d0:21:f9:f6:06:99'),('d0:21:f9:af:04:d6'),
 ('d0:21:f9:af:17:ba'),('1c:6a:1b:c2:65:5c'),('d0:21:f9:af:05:32'),('6c:63:f8:d2:56:b1'),
 ('d0:21:f9:af:19:86'),('d0:21:f9:af:19:94'),('d0:21:f9:af:19:92'),('d0:21:f9:f0:a0:fe'),
 ('d0:21:f9:f0:a1:9b'),('1c:6a:1b:c2:65:78'),('d0:21:f9:f0:a1:3b'),('d0:21:f9:f0:a0:fd'),
 ('78:45:58:5f:00:31'),('d0:21:f9:af:19:7a'),('d0:21:f9:af:19:7b'),('d0:21:f9:f0:a4:20'),
 ('d0:21:f9:f0:a8:24'),('d0:21:f9:f0:a1:38'),('d0:21:f9:af:19:6c'),('d0:21:f9:f0:a1:f1'),
 ('d0:21:f9:f0:a3:ed'),('d0:21:f9:f0:a1:7d'),('d0:21:f9:f0:b1:59'),('d0:21:f9:f0:a0:c8'),
 ('d0:21:f9:f0:a7:82'),('d0:21:f9:f0:a0:c3'),('d0:21:f9:f0:a1:e1'),('d0:21:f9:f0:a1:81'),
 ('d0:21:f9:af:19:a5'),('6c:63:f8:cc:cf:f9'),('d0:21:f9:af:19:8f'),('d0:21:f9:f0:b3:2f'),
 ('d0:21:f9:af:19:11'),('d0:21:f9:af:19:2c'),('d0:21:f9:f6:07:fe'),('d0:21:f9:f6:07:86'),
 ('d0:21:f9:f0:a0:f8'),('d0:21:f9:af:19:89'),('d0:21:f9:f6:08:5c'),('d0:21:f9:f6:07:c3'),
 ('d0:21:f9:f6:09:33'),('d0:21:f9:f0:a1:5d'),('d0:21:f9:fa:a1:80'),('d0:21:f9:f0:a1:9f'),
 ('d0:21:f9:f0:a0:c6'),('d0:21:f9:f0:a1:af'),('d0:21:f9:fa:a2:56'),('d0:21:f9:f0:a0:ee'),
 ('6c:63:f8:cc:cf:c6'),('d0:21:f9:af:19:70'),('d0:21:f9:fa:a3:76'),('6c:63:f8:cc:ca:b6'),
 ('d0:21:f9:af:19:79'),('f4:92:bf:5f:91:fd'),('d0:21:f9:af:19:80'),('d0:21:f9:f0:a1:e8'),
 ('d0:21:f9:f0:a1:3d'),('d0:21:f9:f0:a3:e9'),('f4:92:bf:5f:94:96'),('f4:92:bf:5f:94:9d'),
 ('f4:92:bf:5f:95:1c'),('d0:21:f9:f0:a1:b2'),('d0:21:f9:f0:a1:15'),('d0:21:f9:f0:a1:29'),
 ('d0:21:f9:f0:b2:7c'),('d0:21:f9:f0:b0:aa'),('f4:92:bf:5f:95:0f'),('d0:21:f9:f0:a7:24'),
 ('d0:21:f9:af:19:13'),('d0:21:f9:f0:a1:2b'),('d0:21:f9:f0:a1:eb'),('d0:21:f9:f6:06:b2'),
 ('d0:21:f9:f0:a7:2c'),('d0:21:f9:f0:a4:24'),('d0:21:f9:f0:a1:ec'),('d0:21:f9:f0:a8:31'),
 ('d0:21:f9:f0:b3:ac'),('d0:21:f9:f0:b3:18'),('d0:21:f9:f6:06:23'),('d0:21:f9:f0:a1:ea'),
 ('d0:21:f9:f0:a1:f2'),('d0:21:f9:f0:a4:2b'),('d0:21:f9:f6:06:44'),('d0:21:f9:f0:a4:38'),
 ('d0:21:f9:f0:b3:d7'),('d0:21:f9:f6:06:d3'),('d0:21:f9:f0:a3:cf'),('d0:21:f9:f0:a2:32'),
 ('d0:21:f9:f2:5c:e2'),('d0:21:f9:f0:a7:1f'),('d0:21:f9:f0:b3:11'),('d0:21:f9:f0:a1:f0'),
 ('d0:21:f9:f0:b3:a9'),('d0:21:f9:f6:06:20'),('d0:21:f9:f0:a8:0b'),('d0:21:f9:f2:56:7d'),
 ('d0:21:f9:f0:a3:f4'),('d0:21:f9:f0:a4:30'),('d0:21:f9:f0:a7:e3'),('d0:21:f9:f0:a8:15'),
 ('d0:21:f9:f6:05:ff'),('d0:21:f9:f0:a8:0a'),('d0:21:f9:f0:a7:2b'),('d0:21:f9:f0:a8:0d'),
 ('d0:21:f9:f6:06:90'),('d0:21:f9:f0:b3:e7'),('d0:21:f9:f0:a8:06'),('d0:21:f9:f6:06:da'),
 ('d0:21:f9:f0:a7:f8'),('d0:21:f9:f6:06:11'),('d0:21:f9:f6:06:14'),('d0:21:f9:f0:b3:16'),
 ('d0:21:f9:f0:a3:cb'),('d0:21:f9:f0:a8:10'),('d0:21:f9:f0:a3:d0'),('d0:21:f9:f0:b4:22'),
 ('d0:21:f9:f0:a8:0c'),('d0:21:f9:f0:b3:db'),('d0:21:f9:f0:a8:30'),('d0:21:f9:f6:08:6c'),
 ('d0:21:f9:f6:07:96'),('d0:21:f9:f6:05:89'),('d0:21:f9:f6:07:03'),('d0:21:f9:f6:07:66'),
 ('d0:21:f9:f6:09:77'),('d0:21:f9:f6:07:d8'),('d0:21:f9:f6:08:5b'),('d0:21:f9:f6:0b:dc'),
 ('d0:21:f9:f0:b3:6b'),('d0:21:f9:fa:a2:01'),('d0:21:f9:f6:07:21'),('d0:21:f9:f6:07:1b'),
 ('6c:63:f8:cc:cf:d1'),('d0:21:f9:f6:07:2f'),('d0:21:f9:f6:07:7e'),('d0:21:f9:f6:07:df'),
 ('d0:21:f9:f0:b3:f0'),('d0:21:f9:f6:08:84'),('d0:21:f9:f6:07:6b'),('d0:21:f9:fa:a1:bf'),
 ('d0:21:f9:f6:05:f3'),('d0:21:f9:f6:08:72'),('d0:21:f9:fa:a2:59'),('d0:21:f9:f6:07:b1'),
 ('d0:21:f9:f6:07:a7'),('d0:21:f9:f6:07:08'),('d0:21:f9:f6:09:9a'),('d0:21:f9:f6:08:64'),
 ('d0:21:f9:f6:06:b5'),('d0:21:f9:f2:5a:92'),('d0:21:f9:f6:07:55'),('d0:21:f9:f6:07:94'),
 ('d0:21:f9:f6:07:8c'),('d0:21:f9:f6:08:71'),('d0:21:f9:f6:06:60'),('d0:21:f9:f6:07:88'),
 ('d0:21:f9:f6:0a:73'),('d0:21:f9:f2:5d:06'),('d0:21:f9:f6:07:68'),('d0:21:f9:f6:07:81'),
 ('d0:21:f9:f6:0b:91'),('d0:21:f9:f6:07:d2'),('d0:21:f9:f0:b3:4e'),('d0:21:f9:f6:07:15'),
 ('d0:21:f9:f6:08:3d'),('d0:21:f9:f6:06:b6'),('d0:21:f9:fa:9e:d7'),('d0:21:f9:f6:08:6e'),
 ('d0:21:f9:f6:0a:24'),('d0:21:f9:fa:a1:23'),('d0:21:f9:f6:08:e4'),('d0:21:f9:fa:a2:91'),
 ('d0:21:f9:f6:08:74'),('6c:63:f8:b8:c2:39'),('f4:92:bf:1f:ea:7f'),('f4:92:bf:1f:e9:52'),
 ('d0:21:f9:fa:a2:5e'),('f4:92:bf:1f:eb:47'),('d0:21:f9:fa:a2:d2'),('d0:21:f9:fa:a2:fb'),
 ('d0:21:f9:fa:a1:26'),('f4:92:bf:1f:e8:05'),('f4:92:bf:5f:91:83'),('d0:21:f9:fa:a3:a2'),
 ('f4:92:bf:5f:85:00'),('d0:21:f9:fa:a3:37'),('f4:92:bf:1f:e8:21'),('d0:21:f9:f6:09:f3'),
 ('d0:21:f9:fa:a3:5a'),('d0:21:f9:fa:a3:6c'),('f4:92:bf:1f:eb:24'),('d0:21:f9:fa:a3:77'),
 ('d0:21:f9:f6:0a:39'),('d0:21:f9:f6:08:97'),('f4:92:bf:1f:eb:44'),('d0:21:f9:fa:a3:df'),
 ('f4:92:bf:5f:91:84'),('d0:21:f9:f6:0b:92'),('f4:92:bf:5f:91:7d'),('d0:21:f9:fa:a3:59'),
 ('d0:21:f9:fa:a0:95'),('d0:21:f9:fa:a3:a7'),('d0:21:f9:fa:a2:79'),('d0:21:f9:f6:0a:91'),
 ('d0:21:f9:f6:08:93'),('d0:21:f9:f6:08:be'),('d0:21:f9:fa:a3:55'),('f4:92:bf:5f:91:35'),
 ('d0:21:f9:f6:0a:a8'),('d0:21:f9:fa:a3:9c'),('d0:21:f9:f6:0a:34'),('d0:21:f9:f6:09:c4'),
 ('d0:21:f9:f6:08:e8'),('f4:92:bf:5f:91:80'),('d0:21:f9:fa:9e:09'),('d0:21:f9:f6:0a:8e'),
 ('d0:21:f9:fa:a3:8d'),('d0:21:f9:fa:a3:4e'),('d0:21:f9:fa:a3:75'),('f4:92:bf:5f:91:4f'),
 ('f4:92:bf:5f:91:5a'),('f4:92:bf:5f:92:1b'),('d0:21:f9:fa:a3:68'),('f4:92:bf:5f:93:eb'),
 ('f4:92:bf:5f:93:6d'),('d0:21:f9:fa:a3:aa'),('f4:92:bf:5f:93:e6'),('f4:92:bf:5f:99:d7'),
 ('f4:92:bf:5f:97:98'),('d0:21:f9:fa:a3:0a'),('f4:92:bf:5f:94:03'),('6c:63:f8:b2:1d:1f'),
 ('f4:e2:c6:38:79:8b'),('f4:92:bf:5f:99:de'),('f4:92:bf:5f:99:92'),('f4:92:bf:5f:94:0f'),
 ('f4:92:bf:5f:93:72'),('f4:92:bf:5f:97:be'),('f4:92:bf:5f:91:dc'),('f4:92:bf:5f:99:c8'),
 ('f4:92:bf:5f:93:f3'),('f4:92:bf:5f:91:b0'),('f4:92:bf:5f:99:94'),('f4:92:bf:5f:99:da'),
 ('f4:92:bf:5f:92:04'),('f4:92:bf:5f:91:c0'),('f4:e2:c6:38:b1:da'),('1c:6a:1b:b8:78:9b'),
 ('f4:92:bf:5f:93:f6'),('f4:92:bf:5f:92:1a'),('f4:92:bf:5f:96:62'),('d0:21:f9:fa:a3:25'),
 ('f4:92:bf:5f:99:91'),('d0:21:f9:fa:a3:31'),('f4:92:bf:5f:99:96'),('f4:92:bf:5f:99:95'),
 ('f4:92:bf:5f:91:e9'),('f4:92:bf:5f:93:63'),('f4:92:bf:5f:93:76'),('d0:21:f9:af:15:94'),
 ('1c:6a:1b:c2:63:92'),('f4:92:bf:5f:99:83'),('f4:92:bf:5f:94:9c'),('f4:92:bf:5f:97:d7'),
 ('f4:92:bf:5f:93:be'),('6c:63:f8:b4:5f:a1'),('f4:92:bf:5f:97:2f'),('f4:92:bf:5f:94:1c'),
 ('6c:63:f8:b4:e9:b4'),('f4:e2:c6:38:7b:56')
) AS v(m);

-- Nos clients ACTIFS (mêmes critères que la tuile « Accès actif » de /access).
CREATE TEMP TABLE our_active AS
SELECT lower(d.mac_address)                     AS mac,
       d.name,
       COALESCE(d.status, 'unknown')            AS status,
       d.ip_address,
       d.site,
       l.uisp_ap_name,
       l.uisp_status,
       l.uisp_last_seen
  FROM devices d
  JOIN lrs l ON l.id = d.id
 WHERE d.device_type = 'lr'
   AND d.mac_address IS NOT NULL
   AND NOT l.client_blocked
   AND NOT (
       d.ip_address IS NULL
       AND (l.uisp_last_seen IS NULL
            OR l.uisp_last_seen < now() - interval '7 days')
   );

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Le résumé chiffré
-- ─────────────────────────────────────────────────────────────────────────────
\echo '=== RÉSUMÉ ==='
SELECT (SELECT count(*) FROM our_active)                          AS nos_actifs,
       (SELECT count(*) FROM other_active)                        AS liste_autre_systeme,
       (SELECT count(*) FROM our_active o
          JOIN other_active x ON x.mac = o.mac)                   AS communs,
       (SELECT count(*) FROM our_active o
         WHERE NOT EXISTS (SELECT 1 FROM other_active x
                            WHERE x.mac = o.mac))                 AS notre_surplus,
       (SELECT count(*) FROM other_active x
         WHERE NOT EXISTS (SELECT 1 FROM our_active o
                            WHERE o.mac = x.mac))                 AS chez_eux_pas_actifs_chez_nous;

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. ACTIFS CHEZ NOUS, ABSENTS DE LEUR LISTE  (notre surplus)
--    `status` = ping : up  → vrai client qui répond, que l'autre système rate.
--                      down/unknown → probablement une ligne périmée chez nous.
-- ─────────────────────────────────────────────────────────────────────────────
\echo ''
\echo '=== ACTIFS CHEZ NOUS, ABSENTS DE LA LISTE EXTERNE ==='
SELECT o.status, o.mac, o.name, o.ip_address, o.site, o.uisp_ap_name,
       o.uisp_status, o.uisp_last_seen
  FROM our_active o
 WHERE NOT EXISTS (SELECT 1 FROM other_active x WHERE x.mac = o.mac)
 ORDER BY (o.status = 'up') DESC, o.name;

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. DANS LEUR LISTE, PAS ACTIFS CHEZ NOUS — avec la RAISON.
--    On regarde le LR même s'il n'est pas actif, pour distinguer
--    « bloqué » / « hors supervision » d'un « inconnu au bataillon ».
-- ─────────────────────────────────────────────────────────────────────────────
\echo ''
\echo '=== DANS LEUR LISTE, PAS ACTIFS CHEZ NOUS ==='
SELECT x.mac,
       CASE
         WHEN d.id IS NULL                    THEN 'absent de notre base'
         WHEN d.device_type <> 'lr'           THEN 'connu mais pas un LR client'
         WHEN l.client_blocked                THEN 'bloqué'
         ELSE 'hors supervision'
       END                                    AS raison,
       d.name, d.status, d.ip_address, d.site,
       l.client_blocked, l.block_mode, l.client_blocked_reason,
       l.uisp_status, l.uisp_last_seen
  FROM other_active x
  LEFT JOIN devices d ON lower(d.mac_address) = x.mac
  LEFT JOIN lrs l     ON l.id = d.id
 WHERE NOT EXISTS (SELECT 1 FROM our_active o WHERE o.mac = x.mac)
 ORDER BY 2, d.name NULLS LAST, x.mac;
