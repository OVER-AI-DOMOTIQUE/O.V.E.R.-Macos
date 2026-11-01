# -*- coding: utf-8 -*-
"""Faux « say » de macOS pour les essais (lancé par le Python des essais, jamais par macOS).

  faux_say.py -v ?                       liste des voix, au format de say
  faux_say.py -v Thomas -r 185 -o f.wav --file-format=WAVE --data-format=LEI16@22050 -f -
                                         écrit un WAV muet dont la durée suit le nombre de mots
                                         lus sur l'entrée standard (au débit -r, en mots par minute)
Chaque appel est noté dans le fichier FAUX_SAY_JOURNAL (s'il est donné).
"""
import os
import sys
import time
import wave

VOIX = [("Albert", "en_US", "Hello! My name is Albert."),
        ("Amélie", "fr_CA", "Bonjour, je m’appelle Amélie."),
        ("Thomas", "fr_FR", "Bonjour, je m’appelle Thomas."),
        ("Audrey (Enhanced)", "fr_FR", "Bonjour, je m’appelle Audrey."),
        ("Grandpa (French (France))", "fr_FR", "Bonjour, je m’appelle Grandpa.")]


def noter(texte):
    journal = os.environ.get("FAUX_SAY_JOURNAL")
    if journal:
        with open(journal, "a", encoding="utf-8") as fichier:
            fichier.write(texte + "\n")


arguments = sys.argv[1:]
if arguments[:2] == ["-v", "?"]:
    noter("liste")
    for nom, langue, phrase in VOIX:
        print(f"{nom:<26}{langue:<9}# {phrase}")
    sys.exit(0)

def valeur(option):
    return arguments[arguments.index(option) + 1]

voix, sortie, debit = valeur("-v"), valeur("-o"), int(valeur("-r"))
texte = sys.stdin.buffer.read().decode("utf-8")
if voix not in [nom for nom, _, _ in VOIX]:
    print(f"Voice `{voix}' not found.", file=sys.stderr)
    sys.exit(1)
time.sleep(float(os.environ.get("FAUX_SAY_DELAI", "0.05")))
duree = max(0.2, len(texte.split()) * 60.0 / max(1, debit))
with wave.open(sortie, "wb") as son:
    son.setnchannels(1)
    son.setsampwidth(2)
    son.setframerate(22050)
    son.writeframes(b"\0\0" * int(22050 * duree))
noter(f"son\t{voix}\t{debit}\t{texte}")
