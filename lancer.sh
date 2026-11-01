#!/bin/bash
# Lance le chat O.V.E.R. (après « bash installer.sh »)
# Options : --modele chemin/vers/autre.gguf   --fils 2
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
    echo "Installation manquante. Lance d'abord :  bash installer.sh"
    exit 1
fi
exec .venv/bin/python over_chat.py "$@"
