#!/bin/bash
# =============================================================================
#  Emballage d'O.V.E.R. : Python 3.12, les paquets, whisper.cpp et les scripts, prêts à être
#  rangés DANS l'application. Ensuite, les gens n'ont plus rien à installer ni de Terminal à ouvrir.
#  Usage : bash app-mac/emballer.sh            (avec l'écoute whisper.cpp ; la voix est celle de macOS)
#          bash app-mac/emballer.sh --sans-ecoute
#  Puis : Xcode ▸ Product ▸ Build For ▸ Profiling (⇧⌘I), ou bash app-mac/construire_app.sh.
#  Première fois : 20 à 40 minutes (compilations) ; ensuite, quelques minutes.
#  Journal : app-mac/build/emballage.log
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p build

# Python qui fait l'emballage (pas celui qui sera emballé) : celui du dossier O.V.E.R. (il a déjà
# CMake pour whisper.cpp), sinon celui du Mac
PYTHON="../.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3)"

"$PYTHON" -I -u emballer.py "$@" 2>&1 | tee build/emballage.log
exit "${PIPESTATUS[0]}"
