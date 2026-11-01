#!/bin/bash
# =============================================================================
#  Range Python et les scripts d'O.V.E.R. (app-mac/build/Ressources, préparés par emballer.sh)
#  dans OVER.app/Contents/Resources, puis signe chaque programme qu'ils contiennent, de
#  l'intérieur vers l'extérieur : bibliothèques d'abord, programmes ensuite. L'application
#  elle-même est signée après (Xcode, construire_app.sh ou publier_app.sh) : sa signature
#  scelle alors tout son contenu.
#
#  Usage : bash embarquer.sh CHEMIN/OVER.app [IDENTITE] [MODE]
#    IDENTITE « - » (par défaut) : signature locale, pour ce Mac ; sinon un certificat Apple
#      (« Apple Development: … », « Developer ID Application: … ») : avec horodatage, exigé par Apple.
#    MODE « appstore » : bac à sable (Python et whisper-cli : droits de Programmes-AppStore.entitlements,
#      sans Hardened Runtime) ; « direct » (par défaut) : hors App Store, Hardened Runtime
#      (Python : droits de Python.entitlements).
#  Lancé tout seul par la compilation Release d'Xcode (⇧⌘I, Product ▸ Archive) en mode appstore.
# =============================================================================
set -euo pipefail
ICI="$(cd "$(dirname "$0")" && pwd)"
APP="${1:?Usage : bash embarquer.sh CHEMIN/OVER.app [IDENTITE]}"
IDENTITE="${2:--}"
MODE="${3:-direct}"
SOURCE="${ICI}/build/Ressources"
CIBLE="${APP}/Contents/Resources"
case "${MODE}" in
    appstore) DROITS_PYTHON="${ICI}/Programmes-AppStore.entitlements"; DROITS_WHISPER="${DROITS_PYTHON}" ;;
    direct)   DROITS_PYTHON="${ICI}/Python.entitlements"; DROITS_WHISPER="" ;;
    *) echo "error: mode inconnu « ${MODE} » (appstore ou direct)"; exit 1 ;;
esac

# emballage.json n'est écrit qu'à la fin d'un emballage réussi (toutes les vérifications passées)
[ -f "${SOURCE}/emballage.json" ] && [ -x "${SOURCE}/python/bin/python3" ] \
    || { echo "error: emballage absent ou incomplet : lance « bash app-mac/emballer.sh » jusqu'à « Emballage prêt »"; exit 1; }
[ -d "${APP}/Contents" ] || { echo "error: application introuvable : ${APP}"; exit 1; }

# 1. copie (l'ancienne version est d'abord retirée : aucun fichier périmé ne reste)
for element in python over bin licences emballage.json; do
    rm -rf "${CIBLE:?}/${element}"
done
mkdir -p "${CIBLE}"
for element in python over licences; do
    ditto "${SOURCE}/${element}" "${CIBLE}/${element}"
done
if [ -d "${SOURCE}/bin" ]; then ditto "${SOURCE}/bin" "${CIBLE}/bin"; fi
cp "${SOURCE}/emballage.json" "${CIBLE}/emballage.json"
# lecture seule pour tout le monde sauf toi ; rien de modifiable par les autres comptes du Mac
chmod -R go-w "${CIBLE}/python" "${CIBLE}/over" "${CIBLE}/licences"

# 2. signature : horodatage seulement avec un vrai certificat. Hors App Store : Hardened Runtime partout.
#    App Store : pas de Hardened Runtime (le bac à sable protège ; ctypes et les bibliothèques des paquets
#    marchent sans droit particulier).
OPTIONS=(--force --sign "${IDENTITE}")
if [ "${MODE}" = "direct" ]; then OPTIONS+=(--options runtime); fi
if [ "${IDENTITE}" = "-" ]; then OPTIONS+=(--timestamp=none); else OPTIONS+=(--timestamp); fi

est_macho() {
    case "$(head -c 4 "$1" 2>/dev/null | od -An -tx1 | tr -d ' \n')" in
        cffaedfe|cefaedfe|cafebabe|feedfacf|feedface|bebafeca) return 0 ;;
        *) return 1 ;;
    esac
}

LISTE="$(mktemp "${TMPDIR:-/tmp}/over-signature.XXXXXX")"
trap 'rm -f "${LISTE}"' EXIT
while IFS= read -r -d '' fichier; do
    case "${fichier}" in
        "${CIBLE}/python/bin/python3"|"${CIBLE}/bin/whisper-cli") continue ;;   # signés après, à part
    esac
    if est_macho "${fichier}"; then printf '%s\0' "${fichier}" >> "${LISTE}"; fi
done < <(find "${CIBLE}/python" "${CIBLE}/bin" -type f -print0 2>/dev/null)

NOMBRE="$(tr -cd '\0' < "${LISTE}" | wc -c | tr -d ' ')"
if [ "${NOMBRE}" -gt 0 ]; then
    xargs -0 -n 40 codesign "${OPTIONS[@]}" < "${LISTE}" >/dev/null \
        || { echo "error: signature des bibliothèques impossible"; exit 1; }
fi
# Python : droits du mode choisi (hors App Store, llama-cpp-python passe par ctypes : Python.entitlements)
codesign "${OPTIONS[@]}" --entitlements "${DROITS_PYTHON}" "${CIBLE}/python/bin/python3" \
    || { echo "error: signature de Python impossible"; exit 1; }
if [ -f "${CIBLE}/bin/whisper-cli" ]; then
    if [ -n "${DROITS_WHISPER}" ]; then
        codesign "${OPTIONS[@]}" --entitlements "${DROITS_WHISPER}" "${CIBLE}/bin/whisper-cli" \
            || { echo "error: signature de whisper-cli impossible"; exit 1; }
    else
        codesign "${OPTIONS[@]}" "${CIBLE}/bin/whisper-cli" || { echo "error: signature de whisper-cli impossible"; exit 1; }
    fi
fi
echo "Python et les scripts d'O.V.E.R. embarqués dans ${APP} (${NOMBRE} bibliothèques signées, identité ${IDENTITE}, mode ${MODE})"
