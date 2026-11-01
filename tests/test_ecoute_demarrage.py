# -*- coding: utf-8 -*-
"""Essais de l'écoute (ecoute.py, vrai whisper-cli compilé + vrai modèle tiny) et du démarrage (demarrage.py).

Usage : venv39/bin/python test_ecoute_demarrage.py DOSSIER_PROJET WHISPER_CLI MODELE_TINY
"""
import hashlib
import os
import plistlib
import shutil
import sys
import tempfile
import threading
import wave

ICI = os.path.dirname(os.path.abspath(__file__))
PROJET, WHISPER_CLI, TINY = (os.path.abspath(x) for x in sys.argv[1:4])
SOURCE_WHISPER = os.environ.get("OVER_ESSAI_WHISPER_SOURCE") or os.path.join(os.path.dirname(ICI), "wcpp", "src")
sys.path.insert(0, PROJET)
sys.path.insert(0, ICI)

import ecoute  # noqa: E402
import serveur_fichiers  # noqa: E402

reussis, rates = [], []


def verifier(condition, nom, detail=""):
    (reussis if condition else rates).append(nom)
    print(("  ok   " if condition else "  RATÉ ") + nom + ("" if condition else f"   → {str(detail)[:400]}"))


def empreinte(chemin):
    return hashlib.sha256(open(chemin, "rb").read()).hexdigest()


tmp = tempfile.mkdtemp(prefix="essai-ecoute-")
TAILLE, EMPREINTE = os.path.getsize(TINY), empreinte(TINY)
MODELES_ESSAI = {
    "precis": {"fichier": "ggml-small-essai.bin", "taille": TAILLE, "empreinte": EMPREINTE, "nom": "précis"},
    "rapide": {"fichier": "ggml-base-essai.bin", "taille": TAILLE, "empreinte": EMPREINTE, "nom": "rapide"},
}
catalogue = os.path.join(tmp, "catalogue")
os.makedirs(catalogue)
for info in MODELES_ESSAI.values():
    shutil.copy(TINY, os.path.join(catalogue, info["fichier"]))
serveur, adresse = serveur_fichiers.demarrer(catalogue, coupure={"ggml-small-essai.bin": TAILLE // 4})

# --- 1. état et installation (whisper-cli déjà compilé, modèle téléchargé) ---------------------
print("1. Installation de l'écoute")
m = ecoute.MoteurEcoute(dossier=os.path.join(tmp, "ecoute"))
m.modeles, m.adresse_modeles = MODELES_ESSAI, adresse
verifier(m.etat("precis") == "a_installer", "rien d'installé : à installer", m.etat("precis"))
morceaux, taille = m.a_telecharger("precis")
verifier(len(morceaux) == 2 and "whisper.cpp" in morceaux[0], "ce qu'il reste à installer", morceaux)
verifier(m.transcrire(os.path.join(ICI, "phrase-meteo.wav"), "precis")["erreur"] == "a_installer",
         "transcrire sans installation → a_installer")
verifier(not m.installation_possible(), "installation réservée au Mac par défaut")
os.makedirs(m.dossier)
shutil.copy(WHISPER_CLI, os.path.join(m.dossier, "whisper-cli"))
verifier(m.moteur_installe() and not m.disponible("precis"), "whisper-cli là, modèle absent")
m.installation_permise = True
fini, messages = threading.Event(), []
verifier(m.installer("precis", rapporter=messages.append, fini=fini.set), "installation lancée")
verifier(not m.installer("precis"), "pas deux installations à la fois")
fini.wait(120)
verifier(m.erreur_installation and "téléchargé" in m.erreur_installation, "connexion coupée → erreur claire",
         m.erreur_installation)
partiel = os.path.join(m.dossier, "ggml-small-essai.bin.part")
verifier(os.path.exists(partiel) and 0 < os.path.getsize(partiel) < TAILLE, "morceau reçu gardé")
verifier(m.etat("precis") == "erreur", "état erreur")
fini.clear()
m.installer("precis", rapporter=messages.append, fini=fini.set)
fini.wait(120)
verifier(not m.erreur_installation and m.etat("precis") == "prete", "reprise du téléchargement → prête",
         m.erreur_installation)
verifier(any("Téléchargement du modèle d'écoute précis" in t for t in messages), "progression annoncée", messages)
verifier(m.message == "Écoute locale installée.", "message pour le panneau", m.message)

# --- 2. transcription réelle -------------------------------------------------------------------
print("2. Transcription (whisper.cpp + modèle tiny)")
r = m.transcrire(os.path.join(ICI, "phrase-meteo.wav"), "precis")
verifier(r["ok"] and "météo" in r["texte"].lower(), "phrase transcrite", r)
verifier(r["duree"] > 0 and r["duree_son"] > 1, "durées rendues", r)
r = m.transcrire(os.path.join(ICI, "phrase-minuteur.wav"), "precis")
verifier(r["ok"] and "pâtes" in r["texte"].lower(), "deuxième phrase", r)
silence = os.path.join(tmp, "silence.wav")
with wave.open(silence, "wb") as son:
    son.setnchannels(1), son.setsampwidth(2), son.setframerate(16000)
    son.writeframes(b"\0\0" * 16000 * 3)
r = m.transcrire(silence, "precis")
verifier(r["ok"] and r["texte"] == "", "silence → rien (pas de phrase inventée)", r)
court = os.path.join(tmp, "court.wav")
with wave.open(court, "wb") as son:
    son.setnchannels(1), son.setsampwidth(2), son.setframerate(16000)
    son.writeframes(b"\0\0" * 1600)
verifier(m.transcrire(court, "precis") == {"ok": True, "texte": "", "duree": 0.0, "duree_son": 0.1, "erreur": ""},
         "son trop court → rien, sans lancer whisper")
faux = os.path.join(tmp, "faux.wav")
open(faux, "w").write("pas du son")
verifier(m.transcrire(faux, "precis")["erreur"] == "son illisible", "fichier illisible")
verifier(m.transcrire(os.path.join(ICI, "phrase-meteo.wav"), "rapide")["erreur"] == "a_installer",
         "autre modèle pas encore téléchargé")
verifier(m.choix_valide("nimporte") == "precis", "choix inconnu → précis")
# whisper qui échoue
m2 = ecoute.MoteurEcoute(dossier=m.dossier)
m2.modeles = MODELES_ESSAI
m2.commande = [sys.executable, "-c", "import sys; sys.stderr.write('error: failed to open model\\n'); sys.exit(3)"]
r = m2.transcrire(os.path.join(ICI, "phrase-meteo.wav"), "precis")
verifier(not r["ok"] and "failed to open model" in r["erreur"], "erreur de whisper.cpp remontée", r)
verifier(not [f for f in os.listdir(tempfile.gettempdir()) if f.startswith("over-ecoute-")], "fichiers de sortie effacés")

# --- 3. nettoyage du texte et contexte ---------------------------------------------------------
print("3. Nettoyage")
verifier(ecoute.nettoyer_transcription(" Sous-titres réalisés par la communauté d'Amara.org\n") == "", "Amara.org retiré")
verifier(ecoute.nettoyer_transcription(" [Musique]\n Bonjour Over.\n Bonjour Over.") == "Bonjour Over.",
         "balise et répétition retirées")
verifier(ecoute.nettoyer_transcription(" Active les sous-titres.") == "Active les sous-titres.", "vrai « sous-titres » gardé")
verifier(ecoute.contexte_audio(2) == 512 and ecoute.contexte_audio(15) == 832 and ecoute.contexte_audio(29) == 0,
         "contexte audio adapté à la durée", [ecoute.contexte_audio(x) for x in (2, 15, 29)])

# --- 4. compilation : vérifications (sans tout recompiler) --------------------------------------
print("4. Compilation (garde-fous)")
m3 = ecoute.MoteurEcoute(dossier=os.path.join(tmp, "ecoute3"))
m3.depot, m3.commit = SOURCE_WHISPER, "0" * 40
ok, detail = m3._compiler(lambda t: None)
verifier(not ok and "ne correspond pas" in detail, "code d'une autre version refusé", detail)
verifier(not os.path.exists(os.path.join(m3.dossier, "compilation")), "code refusé effacé")
ancien_path = os.environ["PATH"]
os.environ["PATH"] = "/nulle/part"
ok, detail = ecoute.MoteurEcoute(dossier=os.path.join(tmp, "ecoute4"))._compiler(lambda t: None)
os.environ["PATH"] = ancien_path
verifier(not ok and "git introuvable" in detail, "git absent : explication", detail)
m5 = ecoute.MoteurEcoute(dossier=os.path.join(tmp, "ecoute5"))
m5.commande_pip = [sys.executable, "-c", "import sys; sys.stderr.write('ERROR: pas de réseau\\n'); sys.exit(1)"]
ancien_exe = sys.executable
sys.executable = "/nulle/part/python"          # pas de cmake à côté de « ce Python »
seulement_git = os.path.join(tmp, "seulement-git")
os.makedirs(seulement_git)
os.symlink(shutil.which("git"), os.path.join(seulement_git, "git"))
os.environ["PATH"] = seulement_git                          # git oui, cmake non
ok, detail = m5._compiler(lambda t: None)
sys.executable = ancien_exe
os.environ["PATH"] = ancien_path
verifier(not ok and "CMake ne s'est pas installé" in detail and "pas de réseau" in detail, "cmake absent, pip en échec",
         detail)
verifier(os.path.isfile(os.path.join(m5.dossier, "compilation.log")), "journal de compilation écrit")

# --- 5. démarrage avec la session ---------------------------------------------------------------
print("5. Démarrage avec la session")
os.environ["HOME"] = os.path.join(tmp, "maison")
import demarrage  # noqa: E402
demarrage.DOSSIER_PROJET = os.path.join(tmp, "projet")
os.makedirs(demarrage.DOSSIER_PROJET)
verifier(demarrage.etat(True) == "inactif", "rien au départ")
ok, message = demarrage.appliquer(True, True)
verifier(not ok and "OVER.app" in message, "sans OVER.app : explication", message)
application = os.path.join(demarrage.DOSSIER_PROJET, "OVER.app")
os.makedirs(os.path.join(application, "Contents", "MacOS"))
with open(os.path.join(application, "Contents", "Info.plist"), "wb") as f:
    plistlib.dump({"CFBundleIdentifier": "fr.iaover.over", "CFBundleExecutable": "OVER"}, f)
ok, message = demarrage.appliquer(True, True)
verifier(not ok and "OVER.app" in message, "OVER.app sans programme : explication", message)
programme = os.path.join(application, "Contents", "MacOS", "OVER")
open(programme, "w").write("")
ok, message = demarrage.appliquer(True, True)
fiche = demarrage.lire_fiche()
verifier(ok and "barre des menus" in message, "fiche rangée (discret)", message)
verifier(fiche and fiche["ProgramArguments"] == [programme, "--discret"],
         "programme de l'application lancé directement, avec --discret", fiche)
verifier(fiche and fiche["AssociatedBundleIdentifiers"] == ["fr.iaover.over"] and fiche["RunAtLoad"]
         and fiche["Label"] == "fr.iaover.over.demarrage", "fiche complète", fiche)
verifier(demarrage.chemin_fiche() == os.path.join(tmp, "maison", "Library", "LaunchAgents",
                                                  "fr.iaover.over.demarrage.plist"), "rangée dans LaunchAgents")
verifier(oct(os.stat(demarrage.chemin_fiche()).st_mode & 0o777) == "0o644", "droits 644")
verifier(demarrage.etat(True) == "actif" and demarrage.etat(False) == "a_mettre_a_jour", "état suivi")
ok, message = demarrage.appliquer(True, False)
verifier(ok and demarrage.lire_fiche()["ProgramArguments"] == [programme], "avec fenêtre")
ok, message = demarrage.appliquer(False, False)
verifier(ok and not os.path.exists(demarrage.chemin_fiche()) and demarrage.etat(False) == "inactif", "fiche effacée")
ok, message = demarrage.appliquer(False, False)
verifier(ok, "effacer deux fois : sans erreur")

serveur.shutdown()
shutil.rmtree(tmp, True)
print(f"\n{len(reussis)} réussis, {len(rates)} ratés" + (f" : {rates}" if rates else ""))
sys.exit(1 if rates else 0)
