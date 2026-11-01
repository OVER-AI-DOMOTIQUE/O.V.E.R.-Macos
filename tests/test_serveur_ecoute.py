# -*- coding: utf-8 -*-
"""De bout en bout : serveur.py + écoute whisper.cpp (vrai whisper-cli, modèle tiny) + démarrage avec la session.

Usage : venv39/bin/python test_serveur_ecoute.py DOSSIER_PROJET_SOURCE WHISPER_CLI MODELE_TINY
"""
import hashlib
import json
import os
import plistlib
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time

# copie du projet sans le lourd ni le personnel (modèle, environnement, voix, cerveau, conversation…)
IGNORES = ("app-mac", "voix", "ecoute", "modeles", ".venv", "tests", "OVER.app", "_sauvegarde", "dependances",
           "__pycache__", ".DS_Store", "*.json", "*.bin")
ICI = os.path.dirname(os.path.abspath(__file__))
SOURCE, WHISPER_CLI, TINY = (os.path.abspath(x) for x in sys.argv[1:4])
sys.path.insert(0, ICI)
import serveur_fichiers  # noqa: E402

reussis, rates = [], []


def verifier(condition, nom, detail=""):
    (reussis if condition else rates).append(nom)
    print(("  ok   " if condition else "  RATÉ ") + nom + ("" if condition else f"   → {str(detail)[:500]}"))


racine = tempfile.mkdtemp(prefix="essai-serveur-ecoute-")
TAILLE = os.path.getsize(TINY)
EMPREINTE = hashlib.sha256(open(TINY, "rb").read()).hexdigest()
catalogue = os.path.join(racine, "catalogue")
os.makedirs(catalogue)
for nom in ("ggml-small-essai.bin", "ggml-base-essai.bin"):
    shutil.copy(TINY, os.path.join(catalogue, nom))
serveur_http, adresse = serveur_fichiers.demarrer(catalogue)
MAISON = os.path.join(racine, "maison")


class Moteur:
    def __init__(self, projet, parametres):
        self.projet = projet
        with open(os.path.join(projet, "parametres.json"), "w") as f:
            json.dump(parametres, f)
        lanceur = f"""
import os, runpy, sys
sys.path.insert(0, {projet!r})
os.chdir({projet!r})
import ecoute, demarrage
ecoute.MoteurEcoute.installation_permise = True
ecoute.MoteurEcoute.adresse_modeles = {adresse!r}
ecoute.MoteurEcoute.modeles = {{
    "precis": {{"fichier": "ggml-small-essai.bin", "taille": {TAILLE}, "empreinte": {EMPREINTE!r}, "nom": "précis"}},
    "rapide": {{"fichier": "ggml-base-essai.bin", "taille": {TAILLE}, "empreinte": {EMPREINTE!r}, "nom": "rapide"}},
}}
demarrage.SUR_MAC = True        # comme sur un Mac : l'application gère quand même l'ouverture de session
sys.argv = ["serveur.py", "--modele", os.path.join({projet!r}, "modeles", "mini.gguf"),
            "--cerveau", os.path.join({projet!r}, "cerveau.json"), "--fils", "1"]
runpy.run_path(os.path.join({projet!r}, "serveur.py"), run_name="__main__")
"""
        environnement = dict(os.environ, HOME=MAISON, PYTHONIOENCODING="utf-8")
        self.processus = subprocess.Popen([sys.executable, "-u", "-c", lanceur], stdin=subprocess.PIPE,
                                          stdout=subprocess.PIPE,
                                          stderr=open(os.path.join(projet, "erreurs.log"), "w"),
                                          text=True, encoding="utf-8", env=environnement, cwd=projet)
        self.evenements = queue.Queue()
        self.tous = []
        threading.Thread(target=self._lire, daemon=True).start()

    def _lire(self):
        for ligne in self.processus.stdout:
            try:
                evenement = json.loads(ligne)
            except ValueError:
                evenement = {"type": "illisible", "texte": ligne}
            self.tous.append(evenement)
            self.evenements.put(evenement)
        self.evenements.put({"type": "__fin__"})

    def envoyer(self, **commande):
        self.processus.stdin.write(json.dumps(commande, ensure_ascii=False) + "\n")
        self.processus.stdin.flush()

    def attendre(self, condition, delai=30):
        limite = time.monotonic() + delai
        while time.monotonic() < limite:
            try:
                e = self.evenements.get(timeout=max(0.05, limite - time.monotonic()))
            except queue.Empty:
                break
            if e.get("type") == "question":
                self.envoyer(type="reponse", texte=None)
            if condition(e):
                return e
            if e.get("type") == "__fin__":
                break
        return {"type": "__rien__"}

    def textes(self, genres=("dire", "info", "erreur")):
        return [e.get("texte", "") for e in self.tous if e.get("type") in genres]


def preparer_projet(nom, avec_application=True):
    projet = os.path.join(racine, nom)
    shutil.copytree(SOURCE, projet, ignore=shutil.ignore_patterns(*IGNORES))
    os.makedirs(os.path.join(projet, "modeles"))
    shutil.copy(os.path.join(ICI, "mini.gguf"), os.path.join(projet, "modeles", "mini.gguf"))
    os.makedirs(os.path.join(projet, "ecoute"))
    shutil.copy(WHISPER_CLI, os.path.join(projet, "ecoute", "whisper-cli"))   # déjà compilé (essai à part)
    if avec_application:
        os.makedirs(os.path.join(projet, "OVER.app", "Contents", "MacOS"))
        with open(os.path.join(projet, "OVER.app", "Contents", "Info.plist"), "wb") as f:
            plistlib.dump({"CFBundleIdentifier": "fr.iaover.over", "CFBundleExecutable": "OVER"}, f)
        open(os.path.join(projet, "OVER.app", "Contents", "MacOS", "OVER"), "w").write("")
    return projet


def fiche():
    try:
        with open(os.path.join(MAISON, "Library", "LaunchAgents", "fr.iaover.over.demarrage.plist"), "rb") as f:
            return plistlib.load(f)
    except OSError:
        return None


BASE = {"garder_conversation": False, "memoire_sur_disque": False, "longueur_reponses": 128,
        "recherche_web": False, "actions_mac": False, "briefing": "jamais", "voix_active": False}

# --- A. écoute -----------------------------------------------------------------------------------
print("A. Écoute par le protocole de l'application")
projet = preparer_projet("a")
m = Moteur(projet, dict(BASE))
e = m.attendre(lambda e: e.get("type") == "parametres", 60)
ecoute = e.get("etat_ecoute", {})
verifier(ecoute.get("etat") == "a_installer" and ecoute.get("moteur_installe") is True and ecoute.get("modele") == "precis",
         "panneau : écoute à installer (whisper.cpp déjà là)", ecoute)
verifier([x[0] for x in ecoute.get("modeles", [])] == ["precis", "rapide"], "les deux modèles listés", ecoute)
verifier(e.get("etat_demarrage") == "", "démarrage : géré par l'application (pas de fiche)", e.get("etat_demarrage"))
verifier("ecoute_serveurs_apple" not in e.get("valeurs", {}), "plus de réglage « serveurs Apple »")
m.attendre(lambda e: e.get("type") == "attente", 30)
phrase = os.path.join(ICI, "phrase-meteo.wav")
m.envoyer(type="transcrire", fichier=phrase)
e = m.attendre(lambda e: e.get("type") == "transcription", 30)
verifier(e.get("erreur") == "a_installer" and "/écoute installer" in e.get("explication", ""),
         "transcrire sans modèle : à installer, avec explication", e)
m.envoyer(type="ecoute_installer")
e = m.attendre(lambda e: e.get("type") == "info" and "J'installe mon écoute" in e.get("texte", ""), 10)
verifier(e["type"] == "info" and "le modèle précis" in e["texte"] and "compilation" not in e["texte"].lower(),
         "installation annoncée (modèle seulement)", e)
e = m.attendre(lambda e: e.get("type") == "info" and "Mon écoute est prête" in e.get("texte", ""), 120)
verifier(e["type"] == "info", "« ✓ Mon écoute est prête »", m.textes())
e = m.attendre(lambda e: e.get("type") == "parametres" and e["etat_ecoute"].get("etat") == "prete", 10)
verifier(e["type"] == "parametres", "panneau : écoute prête", e)
debut = time.monotonic()
m.envoyer(type="transcrire", fichier=phrase)
e = m.attendre(lambda e: e.get("type") == "transcription", 60)
verifier(e.get("erreur") == "" and "météo" in e.get("texte", "").lower() and e.get("fichier") == phrase,
         "phrase transcrite par l'application", e)
verifier(e.get("duree", 0) > 0, f"durée de calcul rendue ({e.get('duree')} s)")
verifier(os.path.exists(phrase), "le fichier de l'application n'est pas effacé par le moteur")
m.envoyer(type="transcrire", fichier=phrase, apercu=True, id=5003)
e = m.attendre(lambda e: e.get("type") == "transcription", 60)
verifier(e.get("apercu") is True and e.get("id") == 5003 and "météo" in e.get("texte", "").lower(),
         "aperçu : rendu avec son numéro", e)
m.envoyer(type="transcrire", fichier=phrase, id=5000)
e = m.attendre(lambda e: e.get("type") == "transcription", 60)
verifier(e.get("apercu") is False and e.get("id") == 5000, "finale : rendue avec son numéro", e)
m.envoyer(type="message", texte="/écoute")
e = m.attendre(lambda e: e.get("type") == "info", 10)
verifier("Écoute locale prête" in e.get("texte", ""), "/écoute : état", e)
m.attendre(lambda e: e.get("type") == "attente", 10)
m.envoyer(type="message", texte="/écoute " + os.path.join(ICI, "phrase-minuteur.wav"))
e = m.attendre(lambda e: e.get("type") == "dire", 60)
verifier("J'ai entendu" in e.get("texte", "") and "pâtes" in e.get("texte", ""), "/écoute fichier.wav", e)
m.attendre(lambda e: e.get("type") == "attente", 10)
# autre modèle : téléchargé tout seul (whisper.cpp déjà là)
m.envoyer(type="reglage", cle="ecoute_modele", valeur="rapide")
e = m.attendre(lambda e: e.get("type") == "info" and "Mon écoute est prête" in e.get("texte", ""), 120)
verifier(e["type"] == "info", "changer de modèle le télécharge", m.textes()[-4:])
e = m.attendre(lambda e: e.get("type") == "parametres" and e["etat_ecoute"].get("etat") == "prete", 10)
verifier(e["type"] == "parametres" and all(x[3] for x in e["etat_ecoute"]["modeles"]) and
         e["valeurs"]["ecoute_modele"] == "rapide", "deux modèles installés, « rapide » choisi", e.get("etat_ecoute"))
m.envoyer(type="message", texte="/écoute précis")
e = m.attendre(lambda e: e.get("type") == "parametres", 10)
verifier(e.get("valeurs", {}).get("ecoute_modele") == "precis", "/écoute précis", e.get("valeurs"))
m.attendre(lambda e: e.get("type") == "attente", 10)
m.envoyer(type="message", texte="/écoute blabla")
e = m.attendre(lambda e: e.get("type") == "erreur", 10)
verifier("option" in e.get("texte", ""), "/écoute option inconnue", e)
m.attendre(lambda e: e.get("type") == "attente", 10)

# --- B. démarrage et arrière-plan ---------------------------------------------------------------
# Dans l'application, c'est elle qui s'inscrit auprès de macOS (éléments de connexion) : le moteur ne
# fait que tenir le réglage et l'annoncer ; jamais de fiche dans ~/Library/LaunchAgents.
print("B. Démarrage avec la session (géré par l'application), arrière-plan")
m.envoyer(type="message", texte="/démarrage oui")
e = m.attendre(lambda e: e.get("type") == "info" and "ouverture de ta session" in e.get("texte", ""), 10)
verifier(e["type"] == "info" and "barre des menus" in e["texte"], "/démarrage oui (discret par défaut)", e)
e = m.attendre(lambda e: e.get("type") == "parametres", 10)
verifier(e.get("valeurs", {}).get("demarrage_session") is True and e.get("etat_demarrage") == "",
         "panneau : réglage actif, l'application s'inscrit", (e.get("valeurs", {}).get("demarrage_session"),
                                                             e.get("etat_demarrage")))
m.attendre(lambda e: e.get("type") == "attente", 10)
m.envoyer(type="message", texte="/démarrage fenêtre")
e = m.attendre(lambda e: e.get("type") == "parametres", 10)
verifier(e.get("valeurs", {}).get("demarrage_discret") is False, "/démarrage fenêtre", e.get("valeurs"))
m.attendre(lambda e: e.get("type") == "attente", 10)
m.envoyer(type="reglage", cle="demarrage_discret", valeur=True)
e = m.attendre(lambda e: e.get("type") == "parametres", 10)
verifier(e.get("valeurs", {}).get("demarrage_discret") is True, "réglage « discret » depuis le panneau", e.get("valeurs"))
m.envoyer(type="message", texte="/démarrage")
e = m.attendre(lambda e: e.get("type") == "info", 10)
verifier("barre des menus" in e.get("texte", ""), "/démarrage : état", e)
m.attendre(lambda e: e.get("type") == "attente", 10)
m.envoyer(type="message", texte="/démarrage non")
e = m.attendre(lambda e: e.get("type") == "info" and "ne s'ouvrira plus" in e.get("texte", ""), 10)
verifier(e["type"] == "info", "/démarrage non", e)
m.attendre(lambda e: e.get("type") == "attente", 10)
verifier(fiche() is None and not os.path.exists(os.path.join(MAISON, "Library", "LaunchAgents")),
         "aucune fiche LaunchAgents écrite", fiche())
m.envoyer(type="reglage", cle="arriere_plan", valeur=True)
e = m.attendre(lambda e: e.get("type") == "parametres", 10)
verifier(e.get("valeurs", {}).get("arriere_plan") is True, "arrière-plan enregistré")
m.envoyer(type="message", texte="/paramètres dock oui")
e = m.attendre(lambda e: e.get("type") == "parametres", 10)
verifier(e.get("valeurs", {}).get("masquer_dock") is True, "/paramètres dock oui")
m.attendre(lambda e: e.get("type") == "attente", 10)
m.envoyer(type="message", texte="/paramètres")
e = m.attendre(lambda e: e.get("type") == "dire", 10)
texte = e.get("texte", "")
verifier("arrière-plan : oui" in texte and "démarrage : non" in texte and "écoute : precis" in texte,
         "/paramètres : nouvelles lignes", texte[-700:])
m.attendre(lambda e: e.get("type") == "attente", 10)
m.envoyer(type="message", texte="/aide")
e = m.attendre(lambda e: e.get("type") == "aide", 10)
aide = json.dumps(e.get("lignes"), ensure_ascii=False)
verifier("/écoute" in aide and "/démarrage" in aide, "aide à jour", aide[-400:])
m.attendre(lambda e: e.get("type") == "attente", 10)
reglages = json.load(open(os.path.join(projet, "parametres.json")))
verifier(reglages.get("arriere_plan") is True and reglages.get("masquer_dock") is True, "parametres.json à jour "
         "(lu par l'application au lancement)", reglages)
m.envoyer(type="quitter")
verifier(m.attendre(lambda e: e.get("type") == "fin", 20)["type"] == "fin", "fin propre")
m.processus.wait(10)
erreurs = open(os.path.join(projet, "erreurs.log")).read()
verifier("Traceback" not in erreurs, "aucune exception", erreurs[-1500:])

serveur_http.shutdown()
shutil.rmtree(racine, True)
print(f"\n{len(reussis)} réussis, {len(rates)} ratés" + (f" : {rates}" if rates else ""))
sys.exit(1 if rates else 0)
