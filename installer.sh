#!/bin/bash
# =============================================================================
#  Installation du chat O.V.E.R. (100 % local)
#  Cible : Mac Intel — pensé pour un MacBook Pro 2017, i5 2 cœurs, 8 Go
#  Usage : bash installer.sh
#
#  Étapes :
#   1. vérifie les outils de développement Apple (le compilateur)
#   2. crée un environnement Python isolé dans .venv/
#   3. compile llama-cpp-python pour CE processeur (pas de version
#      précompilée, donc pas de plantage lié au jeu d'instructions)
#   4. télécharge le modèle Ministral 3 3B (≈ 2,1 Go) et vérifie son empreinte
#   5. vérifie la voix : celles de macOS (Thomas, Amélie…), rien à télécharger
#   6. installe l'écoute : whisper.cpp compilé pour ce Mac et le modèle « précis »
#      (≈ 220 Mo ; facultatif, sinon O.V.E.R. l'installe au premier appui sur le micro)
#
#  Relancer le script ne refait que ce qui manque.
#  Après l'installation, plus aucune connexion réseau n'est nécessaire.
#  Paquets Python : versions et empreintes SHA-256 figées dans dependances/ (pip refuse
#  tout fichier qui n'y est pas listé) ; modèle et écoute : empreintes vérifiées.
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")"

VERSION_MOTEUR="0.3.36"
NOM_MODELE="Ministral-3-3B-Instruct-2512-Q4_K_M.gguf"
URL_MODELE="https://huggingface.co/mistralai/Ministral-3-3B-Instruct-2512-GGUF/resolve/main/${NOM_MODELE}"
TAILLE_MODELE=2147023008
EMPREINTE_MODELE="9ed150d4367e68df0ac8e1540f6ddc65b42d0ee26378329d1ecbca60f93fc5f8"
DOSSIER_MODELES="modeles"
CHEMIN_MODELE="${DOSSIER_MODELES}/${NOM_MODELE}"

etape() { printf '\n\033[38;5;37m▸ %s\033[0m\n' "$1"; }
ok()    { printf '  \033[38;5;179m✓\033[0m %s\n' "$1"; }
echec() { printf '\n\033[38;5;197m✗ %s\033[0m\n\n' "$1" >&2; exit 1; }

taille_fichier()    { stat -f%z "$1" 2>/dev/null || stat -c%s "$1"; }
empreinte_fichier() { shasum -a 256 "$1" | awk '{print $1}'; }


# --- 1. Compilateur ----------------------------------------------------------
etape "1/6  Outils de compilation"
if [ "$(uname -s)" = "Darwin" ] && ! xcode-select -p >/dev/null 2>&1; then
    xcode-select --install >/dev/null 2>&1 || true
    echec "Les outils de développement Apple manquent. Une fenêtre vient de s'ouvrir :
  clique « Installer », attends la fin, puis relance :  bash installer.sh"
fi
command -v cc >/dev/null 2>&1 || echec "Aucun compilateur C trouvé."
ok "$(cc --version 2>/dev/null | head -1)"


# --- 2. Python ---------------------------------------------------------------
etape "2/6  Python"
PYTHON="$(command -v python3 || true)"
[ -n "$PYTHON" ] || echec "python3 introuvable."
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
    || echec "Python 3.9 ou plus récent est nécessaire (trouvé : $("$PYTHON" --version 2>&1))."
if [ ! -x .venv/bin/python ]; then
    "$PYTHON" -m venv .venv
fi
# pip, setuptools et les certificats HTTPS (certifi), versions figées
.venv/bin/python -m pip install --quiet --disable-pip-version-check --require-hashes -r dependances/outils.txt \
    || echec "Installation des outils Python impossible (connexion Internet ?)."
ok "$(.venv/bin/python --version) dans .venv/"


# --- 3. Moteur ---------------------------------------------------------------
etape "3/6  Moteur llama-cpp-python ${VERSION_MOTEUR}"
if .venv/bin/python -c "import sys, llama_cpp; sys.exit(0 if llama_cpp.__version__ == '${VERSION_MOTEUR}' else 1)" 2>/dev/null; then
    ok "déjà installé"
else
    OPTIONS_CMAKE="-DGGML_NATIVE=ON"
    if [ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "x86_64" ]; then
        # Mac Intel : Metal coupé, le GPU intégré ralentirait au lieu d'aider
        OPTIONS_CMAKE="${OPTIONS_CMAKE} -DGGML_METAL=OFF"
    fi
    echo "  Compilation pour ce processeur : compte 10 à 25 minutes sur un i5 2 cœurs."
    echo "  Le ventilateur va souffler, c'est normal."
    # code source de llama-cpp-python et ses dépendances : empreintes vérifiées (dependances/moteur.txt)
    CMAKE_ARGS="${OPTIONS_CMAKE}" \
    CMAKE_BUILD_PARALLEL_LEVEL="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)" \
        .venv/bin/python -m pip install --no-cache-dir --disable-pip-version-check --no-binary llama-cpp-python \
        --require-hashes -r dependances/moteur.txt \
        || echec "La compilation a échoué. Copie les dernières lignes ci-dessus et envoie-les-moi."
    ok "moteur compilé et installé"
fi


# --- 4. Modèle ---------------------------------------------------------------
etape "4/6  Modèle Ministral 3 3B (≈ 2,1 Go)"
mkdir -p "${DOSSIER_MODELES}"
if [ -f "${CHEMIN_MODELE}" ] && [ "$(taille_fichier "${CHEMIN_MODELE}")" = "${TAILLE_MODELE}" ]; then
    ok "déjà téléchargé"
else
    PARTIEL="${CHEMIN_MODELE}.part"
    if [ -f "${PARTIEL}" ] && [ "$(taille_fichier "${PARTIEL}")" -ge "${TAILLE_MODELE}" ]; then
        : # téléchargement déjà complet, on passe à la vérification
    else
        echo "  Téléchargement (si tu l'interromps, relancer le script reprend où il s'était arrêté)…"
        curl -L --fail --retry 3 --retry-delay 5 -C - --progress-bar -o "${PARTIEL}" "${URL_MODELE}" \
            || echec "Téléchargement interrompu. Relance « bash installer.sh » : il reprendra où il s'est arrêté."
    fi
    echo "  Vérification de l'empreinte SHA-256…"
    if [ "$(empreinte_fichier "${PARTIEL}")" != "${EMPREINTE_MODELE}" ]; then
        mv "${PARTIEL}" "${CHEMIN_MODELE}.corrompu"
        echec "Le fichier téléchargé ne correspond pas à l'original (mis de côté en .corrompu).
  Supprime-le et relance :  bash installer.sh"
    fi
    mv "${PARTIEL}" "${CHEMIN_MODELE}"
    ok "modèle téléchargé et vérifié"
fi


# --- 5. Voix -----------------------------------------------------------------
# O.V.E.R. parle avec les voix françaises de macOS (Thomas par défaut) : rien à installer.
# Plus de voix : Réglages Système ▸ Accessibilité ▸ Contenu énoncé ▸ Voix du système.
etape "5/6  Voix de macOS"
if .venv/bin/python voix.py >/dev/null 2>&1; then
    ok "voix française trouvée"
else
    echo "  Aucune voix française : ajoute « Thomas » dans Réglages Système ▸ Accessibilité ▸ Contenu énoncé."
fi


# --- 6. Écoute ---------------------------------------------------------------
# Reconnaissance de la parole sur le Mac (whisper.cpp, licence MIT) : le micro de
# l'application ne passe plus par Apple. Un échec ici ne bloque rien.
etape "6/6  Écoute locale whisper.cpp (compilation 5 à 15 min, ≈ 220 Mo)"
if .venv/bin/python ecoute.py --installer; then
    ok "écoute prête"
else
    echo "  Écoute non installée pour l'instant : elle s'installera depuis O.V.E.R. (/écoute installer)."
fi

printf '\n\033[38;5;179mInstallation terminée.\033[0m  Lance le chat avec :  bash lancer.sh\n\n'
