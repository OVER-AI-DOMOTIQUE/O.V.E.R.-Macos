#!/bin/bash
# =============================================================================
#  Prépare O.V.E.R. pour une distribution HORS App Store (site iaover.fr) : application autonome (Python et scripts dedans), signée
#  « Developer ID », notarisée par Apple, ticket agrafé, puis une image disque (DMG) à
#  distribuer, elle aussi signée et notarisée. Sur un autre Mac, Gatekeeper l'ouvre sans
#  avertissement, même hors ligne.
#  La fenêtre du DMG est mise en page comme celle des apps du commerce : fond illustré
#  (dmg/fond.png et fond@2x.png), icônes placées dessus, icône du disque, sans barres
#  (réglages : dmg/reglages_dmg.py, outil : dmgbuild, versions figées dans dependances/dmg.txt).
#
#  À faire une seule fois :
#   1. un compte Apple Developer (99 €/an) et, dans le trousseau, le certificat
#      « Developer ID Application » (Xcode ▸ Réglages ▸ Comptes ▸ Gérer les certificats ▸ +) ;
#   2. un profil pour la notarisation, avec un mot de passe pour app (appleid.apple.com) :
#      xcrun notarytool store-credentials "OVER-notarisation" --apple-id TON_APPLE_ID --team-id TON_EQUIPE
#   3. l'emballage : bash app-mac/emballer.sh
#
#  Usage :  IDENTITE="Developer ID Application: Prénom Nom (EQUIPE)" bash publier_app.sh
#  Refaire seulement le DMG (application déjà signée, notarisée et agrafée dans build/) :
#           REPRENDRE=dmg IDENTITE="…" bash publier_app.sh
#  Refaire seulement la vérification finale (DMG déjà notarisé et agrafé) :
#           REPRENDRE=verification IDENTITE="…" bash publier_app.sh
#  Résultat : app-mac/build/OVER-<version>.dmg (et app-mac/build/OVER.app).
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")"

IDENTITE="${IDENTITE:-}"
REPRENDRE="${REPRENDRE:-}"
PROFIL="${PROFIL:-OVER-notarisation}"
APP="build/OVER.app"
ARCHIVE="build/OVER-notarisation.zip"   # format demandé par le service de notarisation d'Apple
OUTILS_DMG="build/outils-dmg"           # Python à part, avec dmgbuild (mise en page du DMG)

etape() { printf '\n\033[38;5;37m▸ %s\033[0m\n' "$1"; }
ok()    { printf '  \033[38;5;179m✓\033[0m %s\n' "$1"; }
echec() { printf '\n\033[38;5;197m✗ %s\033[0m\n\n' "$1" >&2; exit 1; }
attention() { printf '  \033[38;5;214m! %s\033[0m\n' "$1"; }

notariser() {   # $1 : fichier à envoyer (zip ou dmg)
    xcrun notarytool submit "$1" --keychain-profile "$PROFIL" --wait --timeout 2h > build/notarisation.log 2>&1 || true
    cat build/notarisation.log
    grep -q "status: Accepted" build/notarisation.log || {
        IDENT="$(awk '/^  id:/ {print $2; exit}' build/notarisation.log)"
        echec "Notarisation refusée. Détail : xcrun notarytool log ${IDENT:-<identifiant>} --keychain-profile $PROFIL"
    }
}

# dmgbuild dans un environnement à part, versions et empreintes figées (pip refuse tout autre fichier).
# Préparé AVANT la notarisation : un problème ici ne doit pas faire attendre Apple pour rien.
preparer_dmgbuild() {
    if [ -x "$OUTILS_DMG/bin/python" ] && "$OUTILS_DMG/bin/python" -c "import dmgbuild" 2>/dev/null; then
        return 0
    fi
    local python="../.venv/bin/python"
    [ -x "$python" ] || python="$(command -v python3 || true)"
    [ -n "$python" ] || echec "python3 introuvable (il faut les outils de développement Apple)."
    rm -rf "$OUTILS_DMG"
    "$python" -m venv "$OUTILS_DMG" || echec "Impossible de créer l'environnement de dmgbuild."
    "$OUTILS_DMG/bin/python" -m pip install --quiet --disable-pip-version-check --require-hashes \
        --only-binary :all: -r ../dependances/dmg.txt \
        || echec "Installation de dmgbuild impossible (dependances/dmg.txt, connexion Internet ?)."
}

# « stapler validate » interroge les serveurs d'Apple : un simple « The network connection was
# lost » le fait échouer. Trois essais, 5 secondes d'écart ; le message d'Apple reste dans « detail ».
valider_ticket() {   # $1 : application ou image disque
    local essai
    for essai in 1 2 3; do
        if detail="$(xcrun stapler validate "$1" 2>&1)"; then return 0; fi
        [ "$essai" = 3 ] || sleep 5
    done
    return 1
}

# Agrafer le ticket demande aussi les serveurs d'Apple : trois essais, 10 secondes d'écart
agrafer() {   # $1 : application ou image disque
    local essai
    for essai in 1 2 3; do
        xcrun stapler staple "$1" && return 0
        [ "$essai" = 3 ] || { printf '  (réseau : nouvel essai dans 10 secondes)\n'; sleep 10; }
    done
    return 1
}

# Ticket de la copie rangée dans l'image disque ; si les serveurs d'Apple ne répondent pas, son
# fichier (Contents/CodeResources) doit être identique à celui de l'application déjà validée.
ticket_present() {   # $1 : l'application copiée dans l'image disque
    valider_ticket "$1" && return 0
    if cmp -s "$APP/Contents/CodeResources" "$1/Contents/CodeResources"; then
        ok "ticket identique à celui de l'application validée (vérification en ligne impossible : réseau)"
        return 0
    fi
    return 1
}

# Le .DS_Store de l'image disque : fond désigné par un alias, et SANS marque-page « pBBk » (le Finder
# récent le préfère à l'alias, ne sait pas le suivre et affiche une fenêtre grise)
mise_en_page_correcte() {   # $1 : le .DS_Store de l'image disque ouverte
    "$OUTILS_DMG/bin/python" - "$1" <<'FIN_PYTHON'
import sys
from ds_store import DSStore
with DSStore.open(sys.argv[1], "r") as d:
    racine = {entree.code: entree.value for entree in d if entree.filename == "."}
icvp = racine.get(b"icvp", {})
correcte = icvp.get("backgroundType") == 2 and "backgroundImageAlias" in icvp and b"pBBk" not in racine
sys.exit(0 if correcte else 1)
FIN_PYTHON
}

# Ouvre l'image disque en lecture seule, sans l'afficher, et vérifie ce qu'un utilisateur recevra
# (en cas de problème, le message dit lequel, avec ce qu'en dit codesign ou stapler)
verifier_dmg() {
    local point detail="" probleme=""
    point="$(mktemp -d "${TMPDIR:-/tmp}/over-dmg.XXXXXX")"
    hdiutil attach -readonly -nobrowse -noautoopen -mountpoint "$point" "$DMG" >/dev/null \
        || echec "Impossible d'ouvrir l'image disque pour la vérifier."
    if ! detail="$(codesign --verify --deep --strict --verbose=2 "$point/OVER.app" 2>&1)"; then
        probleme="la signature de l'application ne se vérifie pas"
    elif ! ticket_present "$point/OVER.app"; then
        probleme="l'application n'a pas son ticket de notarisation"
    else
        detail=""
        if [ ! -f "$point/.background.tiff" ]; then probleme="le fond de la fenêtre manque"
        elif ! mise_en_page_correcte "$point/.DS_Store"; then
            probleme="la mise en page ne pointe pas correctement vers le fond (.DS_Store : alias absent ou marque-page pBBk présent, voir dmg/reglages_dmg.py)"
        elif [ ! -L "$point/Applications" ]; then probleme="le raccourci vers Applications manque"
        elif [ ! -f "$point/.VolumeIcon.icns" ]; then probleme="l'icône du disque manque"
        fi
    fi
    hdiutil detach "$point" >/dev/null 2>&1 || hdiutil detach -force "$point" >/dev/null 2>&1 || true
    rmdir "$point" 2>/dev/null || true
    [ -z "$probleme" ] || echec "Dans l'image disque, ${probleme}.${detail:+
$detail}"
}

[ -n "$IDENTITE" ] || echec "Indique ton certificat : IDENTITE=\"Developer ID Application: Prénom Nom (EQUIPE)\" bash publier_app.sh"
case "$IDENTITE" in "Developer ID Application:"*) ;; *)
    echec "Pour distribuer hors App Store, il faut un certificat « Developer ID Application: … »." ;;
esac
security find-identity -v -p codesigning | grep -F "$IDENTITE" >/dev/null \
    || echec "Certificat « $IDENTITE » introuvable dans le trousseau (security find-identity -v -p codesigning)."
xcrun notarytool history --keychain-profile "$PROFIL" >/dev/null 2>&1 \
    || echec "Profil de notarisation « $PROFIL » introuvable : voir l'étape 2 en tête de ce fichier."
[ -f build/Ressources/emballage.json ] \
    || echec "Emballage absent : lance d'abord « bash emballer.sh » (une application à vendre doit contenir Python)."

VERSION="$(sed -n 's/^VERSION="\(.*\)"/\1/p' construire_app.sh)"
DMG="build/OVER-${VERSION}.dmg"
VOLUME="O.V.E.R. ${VERSION}"
for fichier in dmg/fond.png dmg/fond@2x.png dmg/reglages_dmg.py; do
    [ -f "$fichier" ] || echec "Fond ou réglages du DMG absents : app-mac/$fichier."
done
# Un DMG précédent encore ouvert (même nom de disque) gênerait dmgbuild : on l'éjecte. Lu d'abord en
# entier (pas de « mount | grep -q » avec pipefail).
MONTAGES="$(mount)"
if [[ "$MONTAGES" == *" on /Volumes/${VOLUME} ("* ]]; then
    hdiutil detach "/Volumes/${VOLUME}" >/dev/null 2>&1 \
        || hdiutil detach -force "/Volumes/${VOLUME}" >/dev/null 2>&1 \
        || echec "Le disque « ${VOLUME} » est ouvert et refuse de s'éjecter : ferme ses fenêtres, éjecte-le (Finder ▸ ⏏), puis relance."
    ok "disque « ${VOLUME} » d'un essai précédent éjecté"
fi
command -v SetFile >/dev/null 2>&1 || [ -x /usr/bin/SetFile ] \
    || echec "SetFile introuvable : il vient avec Xcode ou les outils de développement Apple."
preparer_dmgbuild
ok "dmgbuild prêt ($("$OUTILS_DMG/bin/python" -c 'import importlib.metadata as m; print(m.version("dmgbuild"))'))"

case "$REPRENDRE" in
    ""|dmg|verification) ;;
    *) echec "REPRENDRE : « dmg » (refaire le DMG) ou « verification » (refaire la vérification finale)." ;;
esac
if [ -n "$REPRENDRE" ]; then
    # l'application de build/ doit être signée par CE certificat, notarisée et agrafée
    SIGNATURE_APP="$(codesign -d --verbose=2 "$APP" 2>&1)" \
        || echec "REPRENDRE=$REPRENDRE : $APP n'est pas signée ; relance sans REPRENDRE."
    [[ "$SIGNATURE_APP" == *"Authority=$IDENTITE"* ]] \
        || echec "REPRENDRE=$REPRENDRE : $APP n'est pas signée par « $IDENTITE » ; relance sans REPRENDRE."
    codesign --verify --deep --strict "$APP" 2>/dev/null \
        || echec "REPRENDRE=$REPRENDRE : la signature de $APP ne se vérifie pas ; relance sans REPRENDRE."
    detail=""
    valider_ticket "$APP" \
        || echec "REPRENDRE=$REPRENDRE : ticket de notarisation de $APP non validé ; relance sans REPRENDRE.
$detail"
    ok "reprise : application déjà signée, notarisée et agrafée (étapes 1 et 2 sautées)"
else
etape "1/5  Compilation, Python embarqué, signature Developer ID (de l'intérieur vers l'extérieur)"
IDENTITE="$IDENTITE" RANGER=0 bash construire_app.sh
codesign --verify --deep --strict --verbose=2 "$APP" || echec "La signature ne se vérifie pas."
# lu d'abord en entier : avec « pipefail », un « codesign | grep -q » échoue au hasard (grep s'arrête
# à la première ligne trouvée, codesign écrit encore et reçoit SIGPIPE)
SIGNATURE_PYTHON="$(codesign -dv "$APP/Contents/Resources/python/bin/python3" 2>&1)" \
    || echec "Python n'est pas signé : $SIGNATURE_PYTHON"
[[ "$SIGNATURE_PYTHON" =~ flags=0x[0-9a-f]+\([^\)]*runtime ]] \
    || echec "Python n'a pas le Hardened Runtime : $(grep -m1 "flags=" <<< "$SIGNATURE_PYTHON")"
ok "signée par $IDENTITE"

etape "2/5  Notarisation de l'application (quelques minutes)"
rm -f "$ARCHIVE"
ditto -c -k --keepParent "$APP" "$ARCHIVE"
notariser "$ARCHIVE"
rm -f "$ARCHIVE"
agrafer "$APP" || echec "Impossible d'agrafer le ticket à l'application."
ok "application notarisée, ticket agrafé"
fi

if [ "$REPRENDRE" = "verification" ]; then
    [ -f "$DMG" ] || echec "REPRENDRE=verification : $DMG introuvable ; relance avec REPRENDRE=dmg."
    detail=""
    valider_ticket "$DMG" \
        || echec "REPRENDRE=verification : $DMG n'est pas notarisée et agrafée ; relance avec REPRENDRE=dmg.
$detail"
    ok "reprise : image disque déjà notarisée et agrafée (étapes 3 et 4 sautées)"
else
etape "3/5  Image disque (fond, icônes placées, icône du disque)"
rm -f "$DMG"
# dmgbuild copie l'application avec ditto (signature et ticket intacts), pose le fond (1x + 2x
# réunis en un seul fichier), place les icônes et met l'icône du disque
"$OUTILS_DMG/bin/python" -m dmgbuild -s dmg/reglages_dmg.py \
    -D application="$(pwd)/$APP" \
    -D icone="$(pwd)/$APP/Contents/Resources/AppIcon.icns" \
    -D fond="$(pwd)/dmg/fond.png" \
    "$VOLUME" "$DMG" > build/dmgbuild.log 2>&1 \
    || { cat build/dmgbuild.log; echec "Impossible de créer l'image disque (journal : app-mac/build/dmgbuild.log)."; }
codesign --force --timestamp --sign "$IDENTITE" "$DMG" || echec "Impossible de signer l'image disque."
verifier_dmg
ok "$DMG (application signée et agrafée à l'intérieur, fond, raccourci, icône du disque)"

etape "4/5  Notarisation de l'image disque"
notariser "$DMG"
agrafer "$DMG" || echec "Impossible d'agrafer le ticket à l'image disque."
ok "image disque notarisée, ticket agrafé"
fi

etape "5/5  Vérification par Gatekeeper"
# Un refus montre ce qu'en dit spctl, le réglage de ce Mac et, sur macOS 14 ou plus récent, l'avis
# détaillé d'Apple (syspolicy_check). Cas à part : « source=Notarized Developer ID » (Gatekeeper voit
# bien la notarisation) avec « Internal Xprotect Error » = XProtect de CE Mac en panne. Vu sur un
# MacBook Pro 2017 sous macOS 15 (OpenCore Legacy Patcher) : même refus pour toute app notarisée,
# Claude.app d'Anthropic comprise. Le script le signale sans s'arrêter (signature, notarisation et
# tickets sont déjà vérifiés plus haut) ; le vrai test se fait alors sur un autre Mac.
GATEKEEPER_EN_PANNE=0
verifier_gatekeeper() {   # $1 : execute (application) ou open (image disque) ; $2 : chemin ; $3 : nom
    local reponse avis=""
    if [ "$1" = execute ]; then
        reponse="$(spctl --assess --type execute -vvv "$2" 2>&1)" && { printf '%s\n' "$reponse"; return 0; }
    else
        reponse="$(spctl --assess --type open --context context:primary-signature -vvv "$2" 2>&1)" \
            && { printf '%s\n' "$reponse"; return 0; }
    fi
    if command -v syspolicy_check >/dev/null 2>&1; then
        # syspolicy_check répond « échec » justement quand il y a un souci : avec « set -e » et
        # « pipefail », sans le « || true », le script s'arrêterait ici sans rien dire
        avis="$(syspolicy_check distribution "$APP" 2>&1 | tail -n 25)" || true
    fi
    if [[ "$reponse" == *"source=Notarized Developer ID"* && "$avis" == *"Internal Xprotect Error"* ]]; then
        printf '%s\n' "$reponse"
        attention "$3 : refus venu de XProtect, en panne sur ce Mac (« Internal Xprotect Error »)."
        GATEKEEPER_EN_PANNE=1
        return 0
    fi
    if [ -n "$avis" ]; then
        avis="
Avis d'Apple (syspolicy_check) :
$avis"
    fi
    echec "Gatekeeper refuse $3.
$reponse
Gatekeeper sur ce Mac : $(spctl --status 2>&1).$avis
Si Réglages Système ▸ Confidentialité et sécurité ▸ « Autoriser les applications téléchargées de » est sur « App Store » seulement, ce Mac refuse toute app hors App Store, même notarisée : choisis « App Store et développeurs identifiés », puis relance avec REPRENDRE=verification."
}
verifier_gatekeeper execute "$APP" "l'application"
verifier_gatekeeper open "$DMG" "l'image disque"
if [ "$GATEKEEPER_EN_PANNE" = 1 ]; then
    attention "Signature, notarisation et tickets sont bons ; XProtect de ce Mac ne sait pas juger."
    attention "Pour le vrai test, ouvre le DMG sur un autre Mac (compte quelconque)."
else
    ok "Gatekeeper accepte l'application et l'image disque"
fi
printf '\n\033[38;5;179mPrêt à distribuer :\033[0m  %s  (%s)\n\n' "$(pwd)/$DMG" "$(du -h "$DMG" | cut -f1)"
