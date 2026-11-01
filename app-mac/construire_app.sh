#!/bin/bash
# =============================================================================
#  Construit l'application O.V.E.R. pour Mac SANS Xcode, hors App Store (sans bac à sable).
#  Utilise les outils de développement Apple déjà installés (swiftc, iconutil).
#  Usage : bash construire_app.sh
#  Résultat : OVER.app rangée à côté des scripts d'O.V.E.R. (dossier parent de app-mac).
#   - après « bash emballer.sh » : Python et les scripts sont DANS l'application, qui marche seule
#     (tes données : ~/Library/Application Support/O.V.E.R.) ; tu peux la ranger dans Applications ;
#   - sinon : elle se sert du dossier O.V.E.R. (scripts, .venv, modèle) ; garde-la à côté des scripts.
#
#  Avec Xcode : Product ▸ Build For ▸ Profiling (⇧⌘I) compile la version Release et la range
#  aussi à côté des scripts ; ⌘R (version Debug, pour les essais) la laisse dans Xcode.
#  Signature : locale (« ad hoc »), avec le Hardened Runtime et seulement les droits du fichier
#  OVER/OVER-HorsAppStore.entitlements (micro, rappels et agenda). Pour distribuer hors App Store :
#  bash publier_app.sh. Pour l'App Store : Xcode ▸ Product ▸ Archive (voir README).
#  Variables (pour publier_app.sh) : IDENTITE (certificat, « - » par défaut), RANGER=0 (ne pas copier
#  l'application à côté des scripts).
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")"

VERSION="0.1"
CIBLE="$(uname -m)-apple-macos12.0"
APP="build/OVER.app"
ICONES="OVER/Assets.xcassets/AppIcon.appiconset"
LOGO="OVER/Assets.xcassets/Logo.imageset"
DOSSIER_OVER="$(cd .. && pwd)"          # le dossier des scripts (serveur.py, .venv, modeles)
DESTINATION="${DOSSIER_OVER}/OVER.app"
IDENTITE="${IDENTITE:--}"
RANGER="${RANGER:-1}"

etape() { printf '\n\033[38;5;37m▸ %s\033[0m\n' "$1"; }
ok()    { printf '  \033[38;5;179m✓\033[0m %s\n' "$1"; }
echec() { printf '\n\033[38;5;197m✗ %s\033[0m\n\n' "$1" >&2; exit 1; }

command -v xcrun >/dev/null 2>&1 || echec "Outils de développement Apple absents : lance « xcode-select --install »."
SDK="$(xcrun --sdk macosx --show-sdk-path 2>/dev/null)" || echec "Kit de développement macOS introuvable."

etape "1/5  Nettoyage"
rm -rf "build/OVER.app" "build/AppIcon.iconset"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" "build/AppIcon.iconset"
ok "build/ prêt"

etape "2/5  Compilation (Swift, macOS 12 ou plus récent)"
# chemins complets : l'application s'en sert pour retrouver le dossier d'O.V.E.R.
xcrun swiftc -O -parse-as-library -sdk "$SDK" -target "$CIBLE" \
    "$(pwd)"/OVER/*.swift -o "$APP/Contents/MacOS/OVER" \
    || echec "La compilation a échoué. Copie les lignes « error: » ci-dessus et envoie-les-moi."
ok "programme compilé pour $(uname -m)"

etape "3/5  Icône et fiche de l'application"
cp "$ICONES"/icon_*.png "build/AppIcon.iconset/"
cp "$LOGO"/Logo.png "$LOGO"/Logo@2x.png "$APP/Contents/Resources/"
cp OVER/Assets.xcassets/IconeApplication.imageset/IconeApplication*.png "$APP/Contents/Resources/"
cp OVER/Assets.xcassets/IconeBarreMenus.imageset/IconeBarreMenus*.png "$APP/Contents/Resources/"
iconutil -c icns "build/AppIcon.iconset" -o "$APP/Contents/Resources/AppIcon.icns"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDevelopmentRegion</key><string>fr</string>
    <key>CFBundleDisplayName</key><string>O.V.E.R.</string>
    <key>CFBundleExecutable</key><string>OVER</string>
    <key>CFBundleIconFile</key><string>AppIcon</string>
    <key>CFBundleIdentifier</key><string>fr.iaover.over</string>
    <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
    <key>CFBundleName</key><string>OVER</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>${VERSION}</string>
    <key>CFBundleVersion</key><string>1</string>
    <key>LSApplicationCategoryType</key><string>public.app-category.productivity</string>
    <key>LSMinimumSystemVersion</key><string>12.0</string>
    <key>NSHighResolutionCapable</key><true/>
    <key>NSHumanReadableCopyright</key><string>© 2026 Jessy · iaover.fr</string>
    <key>NSPrincipalClass</key><string>NSApplication</string>
    <key>NSMicrophoneUsageDescription</key><string>O.V.E.R. t'écoute quand tu appuies sur le bouton micro. Ta voix est transformée en texte sur ton Mac : rien n'est envoyé.</string>
    <key>NSRemindersUsageDescription</key><string>O.V.E.R. crée les rappels que tu lui demandes et lit ceux du jour pour ton briefing. Rien ne quitte ton Mac.</string>
    <key>NSRemindersFullAccessUsageDescription</key><string>O.V.E.R. crée les rappels que tu lui demandes et lit ceux du jour pour ton briefing. Rien ne quitte ton Mac.</string>
    <key>NSCalendarsUsageDescription</key><string>O.V.E.R. lit tes rendez-vous du jour pour ton briefing. Rien ne quitte ton Mac.</string>
    <key>NSCalendarsFullAccessUsageDescription</key><string>O.V.E.R. lit tes rendez-vous du jour pour ton briefing. Rien ne quitte ton Mac.</string>
    <key>ITSAppUsesNonExemptEncryption</key><false/>
</dict>
</plist>
PLIST
ok "icône et Info.plist en place"

etape "4/5  Python et les scripts dans l'application"
if [ -f build/Ressources/emballage.json ]; then
    bash embarquer.sh "$APP" "$IDENTITE" direct || echec "Impossible de ranger Python dans l'application."
    ok "application autonome : $(du -sh "$APP" | cut -f1)"
else
    echo "  Pas d'emballage (bash app-mac/emballer.sh) : l'application se servira du dossier O.V.E.R."
fi

etape "5/5  Signature"
if [ "$IDENTITE" = "-" ]; then HORODATAGE="--timestamp=none"; else HORODATAGE="--timestamp"; fi
codesign --force "$HORODATAGE" --sign "$IDENTITE" --options runtime --entitlements OVER/OVER-HorsAppStore.entitlements "$APP" \
    || echec "La signature a échoué."
codesign --verify --deep --strict "$APP" || echec "La signature ne se vérifie pas."
ok "signée ($IDENTITE)"
if [ "$RANGER" = "1" ]; then
    rm -rf "$DESTINATION"
    ditto "$APP" "$DESTINATION"
    ok "rangée dans ${DOSSIER_OVER}"
    printf '\n\033[38;5;179mOVER.app est prête :\033[0m  %s\n' "$DESTINATION"
    printf 'Lance-la :  open "%s"   (ou double-clic dans le Finder)\n\n' "$DESTINATION"
fi
