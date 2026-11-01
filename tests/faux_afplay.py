#!/usr/bin/env python3
# Faux « afplay » pour les essais : note le fichier joué et attend une fraction de sa durée.
import os, sys, time, wave
chemin = sys.argv[1]
with wave.open(chemin, "rb") as son:
    duree = son.getnframes() / float(son.getframerate())
journal = os.environ.get("FAUX_AFPLAY_JOURNAL", "/dev/null")
facteur = float(os.environ.get("FAUX_AFPLAY_FACTEUR", "0.25"))
with open(journal, "a") as f:
    f.write(f"debut {time.time():.3f} {chemin} {duree:.2f}\n")
time.sleep(duree * facteur)
with open(journal, "a") as f:
    f.write(f"fin {time.time():.3f} {chemin}\n")
