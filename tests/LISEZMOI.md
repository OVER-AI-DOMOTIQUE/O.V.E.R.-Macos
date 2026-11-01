# Essais automatiques d'O.V.E.R.

Ils vérifient ce que fait le programme, sans le vrai modèle : un mini modèle (`mini.gguf`, 115 Ko) ou des réponses écrites d'avance le remplacent. Chaque essai travaille sur une copie du projet dans un dossier temporaire : ton cerveau, ta conversation et tes réglages ne sont jamais touchés, et aucune commande macOS n'est lancée pour de vrai.

```bash
bash tests/lancer_tests.sh
```

| Fichier | Ce qu'il vérifie |
|---|---|
| `test_batterie.py` | commandes, les 24 paramètres et leurs bornes, fichiers abîmés, droits 600, balises, actions et garde-fous, injection par une page web, adresses locales refusées, minuteurs gardés |
| `test_protocole.py` | messages abîmés ou énormes envoyés par l'app, rafales, signal Arrêter, octets non UTF-8 |
| `test_apercu.py` | l'aperçu de transcription ne retarde jamais la version finale |
| `test_installation.py` | premier lancement de l'application : téléchargement du modèle (reprise, connexion coupée, fichier faux, disque plein, pause, fermeture), fichier déjà téléchargé (copie déposée par l'application dans `import/`, vérifiée puis déplacée) |
| `test_voix_mac.py` | la voix de macOS (faux `say`) et les actions faites par l'application dans le bac à sable : son, ouvrir, notification, agenda et rappels du briefing, ouverture avec la session |
| `test_ecoute_demarrage.py`, `test_serveur_ecoute.py` | l'écoute whisper.cpp et l'ouverture de session (`--complet`) |

**Essais complets** (`bash tests/lancer_tests.sh --complet`) : il leur faut en plus
- `OVER_ESSAI_WHISPER_TINY` : le modèle `ggml-tiny.bin` de whisper.cpp ;
- `OVER_ESSAI_WHISPER_SOURCE` : le code source de whisper.cpp (dépôt git), pour l'essai qui refuse une autre version.

`whisper-cli` est pris dans `ecoute/` ; un autre Python se règle avec `OVER_PYTHON`.

**Application emballée** (`bash tests/lancer_tests.sh --emballe`, avec ou sans `--complet`) : les mêmes essais, avec le Python 3.12 et les scripts préparés par `bash app-mac/emballer.sh` (dossier `app-mac/build/Ressources`), tels qu'ils seront dans OVER.app ; `whisper-cli` est alors celui de l'emballage.

Les fichiers fournis : `mini.gguf` (fabriqué par `fabriquer_mini_modele.py`), deux phrases enregistrées (`phrase-meteo.wav`, `phrase-minuteur.wav`), un faux `say`, un faux `afplay` et un faux `whisper-cli` (pour les essais sans son ni modèle), un petit serveur de fichiers local (`serveur_fichiers.py`).
