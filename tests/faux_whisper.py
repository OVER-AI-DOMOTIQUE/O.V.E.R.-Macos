# Faux whisper-cli pour les essais : attend un peu puis écrit un texte.
import os, sys, time
args = sys.argv[1:]
base = args[args.index("-of") + 1]
time.sleep(float(os.environ.get("FAUX_WHISPER_ATTENTE", "2")))
open(base + ".txt", "w").write(" Bonjour depuis le faux whisper.\n")
