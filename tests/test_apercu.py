# -*- coding: utf-8 -*-
"""L'aperçu de transcription ne retarde jamais la version finale (ecoute.py)."""
import os, sys, threading, time
ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(sys.argv[1]))
import ecoute
reussis, rates = [], []
def verifier(c, nom, d=""):
    (reussis if c else rates).append(nom); print(("  ok   " if c else "  RATÉ ") + nom + ("" if c else f"   → {d}"))
import tempfile, shutil
tmp = tempfile.mkdtemp()
m = ecoute.MoteurEcoute(dossier=tmp)
m.modeles = {"precis": {"fichier": "faux.bin", "taille": 4, "empreinte": "", "nom": "essai"}}
open(os.path.join(tmp, "faux.bin"), "w").write("1234")
m.commande = [sys.executable, os.path.join(ICI, "faux_whisper.py")]
os.environ["FAUX_WHISPER_ATTENTE"] = "3"
son = os.path.join(ICI, "phrase-meteo.wav")
resultats = {}
def apercu():
    resultats["apercu"] = m.transcrire(son, "precis", apercu=True)
t = threading.Thread(target=apercu); t.start()
time.sleep(0.5)
r = m.transcrire(son, "precis", apercu=True)
verifier(r["erreur"] == "occupe" and r["duree"] < 0.2, "2e aperçu pendant le 1er : abandonné tout de suite", r)
debut = time.monotonic()
final = m.transcrire(son, "precis")
duree = time.monotonic() - debut
t.join()
verifier(resultats["apercu"]["erreur"] == "annule", "l'aperçu est arrêté par la version finale", resultats["apercu"])
verifier(final["ok"] and final["texte"] == "Bonjour depuis le faux whisper.", "version finale complète", final)
verifier(duree < 3.8, f"la finale n'attend pas la fin de l'aperçu ({duree:.1f} s pour 3 s de calcul)")
verifier(m.processus_apercu is None, "plus d'aperçu en cours")
os.environ["FAUX_WHISPER_ATTENTE"] = "0.2"
r = m.transcrire(son, "precis", apercu=True)
verifier(r["ok"] and r["texte"], "aperçu seul : texte rendu", r)
shutil.rmtree(tmp, True)
print(f"\n{len(reussis)} réussis, {len(rates)} ratés"); sys.exit(1 if rates else 0)
