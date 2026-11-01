#!/bin/bash
# =============================================================================
#  Essais automatiques d'O.V.E.R.
#  Usage :  bash tests/lancer_tests.sh             essais rapides (≈ 4 min) : commandes, paramètres,
#                                                  sécurité, fichiers abîmés, protocole de l'app,
#                                                  installation du modèle, voix de macOS et actions
#                                                  faites par l'application (bac à sable)
#           bash tests/lancer_tests.sh --complet   + écoute whisper.cpp (voir tests/LISEZMOI.md)
#           bash tests/lancer_tests.sh --emballe   les mêmes essais avec le Python et les scripts emballés
#                                                  pour l'application (après bash app-mac/emballer.sh)
#  Les essais travaillent sur une copie du projet, dans un dossier temporaire : ils ne touchent ni
#  à ton cerveau, ni à ta conversation, ni à tes réglages, et ne lancent aucune commande macOS.
# =============================================================================
set -uo pipefail
ICI="$(cd "$(dirname "$0")" && pwd)"
PROJET="$(dirname "$ICI")"
COMPLET=0
EMBALLE=0
for option in "$@"; do
    case "$option" in
        --complet) COMPLET=1 ;;
        --emballe) EMBALLE=1 ;;
        *) echo "Option inconnue : $option"; exit 1 ;;
    esac
done
PYTHON="${OVER_PYTHON:-$PROJET/.venv/bin/python}"
WHISPER_PAR_DEFAUT="$PROJET/ecoute/whisper-cli"
if [ "$EMBALLE" = 1 ]; then
    # le Python de l'application et ses scripts (app-mac/build/Ressources), tels qu'ils seront dans OVER.app
    PYTHON="$PROJET/app-mac/build/Ressources/python/bin/python3"
    PROJET="$PROJET/app-mac/build/Ressources/over"
    WHISPER_PAR_DEFAUT="$(dirname "$PROJET")/bin/whisper-cli"
    [ -f "$PROJET/serveur.py" ] || { echo "Pas d'emballage : lance d'abord bash app-mac/emballer.sh"; exit 1; }
fi
[ -x "$PYTHON" ] || { echo "Python introuvable : $PYTHON (lance d'abord bash installer.sh)"; exit 1; }

ratees=()
essai() {
    local nom="$1"; shift
    printf '\n\033[38;5;37m▸ %s\033[0m\n' "$nom"
    local journal
    journal="$(mktemp "${TMPDIR:-/tmp}/essai-over.XXXXXX")"
    "$PYTHON" -B "$ICI/$nom" "$@" >"$journal" 2>&1
    local code=$?
    grep -E "RATÉ|réussis" "$journal"
    if [ $code -ne 0 ]; then
        ratees+=("$nom")
        grep -q "réussis" "$journal" || tail -15 "$journal"   # arrêt brutal : la fin du journal
    fi
    rm -f "$journal"
}

essai test_batterie.py "$PROJET"
essai test_protocole.py "$PROJET"
essai test_apercu.py "$PROJET"
essai test_installation.py "$PROJET"
essai test_voix_mac.py "$PROJET"

if [ "$COMPLET" = 1 ]; then
    WHISPER_CLI="${OVER_ESSAI_WHISPER_CLI:-$WHISPER_PAR_DEFAUT}"
    TINY="${OVER_ESSAI_WHISPER_TINY:-}"
    [ -n "$TINY" ] || { echo "Pour --complet : OVER_ESSAI_WHISPER_TINY (voir tests/LISEZMOI.md)"; exit 1; }
    essai test_ecoute_demarrage.py "$PROJET" "$WHISPER_CLI" "$TINY"
    essai test_serveur_ecoute.py "$PROJET" "$WHISPER_CLI" "$TINY"
fi

if [ ${#ratees[@]} -eq 0 ]; then
    printf '\n\033[38;5;179mTous les essais sont passés.\033[0m\n'
else
    printf '\n\033[38;5;197mEssais en échec : %s\033[0m\n' "${ratees[*]}"
    exit 1
fi
