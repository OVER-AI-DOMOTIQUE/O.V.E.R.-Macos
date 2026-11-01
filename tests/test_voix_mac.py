# -*- coding: utf-8 -*-
"""Essai de bout en bout : serveur.py (protocole de l'application) + noyau + voix de macOS + actions
faites par l'application (messages « systeme », comme dans le bac à sable de l'App Store).

Mini modèle GGUF, faux « say » (tests/faux_say.py) et faux « afplay » (tests/faux_afplay.py) :
aucun son, aucune commande macOS. L'essai joue le rôle de l'application : il répond aux demandes
« systeme » du moteur (son, ouvrir, agenda…) et vérifie ce que le moteur en fait.

Usage : python test_voix_mac.py DOSSIER_PROJET_SOURCE
"""
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time

IGNORES = ("app-mac", "voix", "ecoute", "modeles", ".venv", "tests", "OVER.app", "_sauvegarde", "dependances",
           "__pycache__", ".DS_Store", "*.json", "*.bin")
ICI = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.abspath(sys.argv[1])
FAUX_SAY = os.path.join(ICI, "faux_say.py")
FAUX_AFPLAY = os.path.join(ICI, "faux_afplay.py")
reussis, rates = [], []


def verifier(condition, nom, detail=""):
    (reussis if condition else rates).append(nom)
    print(("  ok   " if condition else "  RATÉ ") + nom + ("" if condition else f"   → {str(detail)[:400]}"))


class Moteur:
    """Lance serveur.py comme le fait l'application, et lit ses évènements."""

    def __init__(self, projet, parametres, cerveau=None):
        self.projet = projet
        with open(os.path.join(projet, "parametres.json"), "w") as f:
            json.dump(parametres, f)
        if cerveau:
            with open(os.path.join(projet, "cerveau.json"), "w") as f:
                json.dump(cerveau, f)
        self.journal_afplay = os.path.join(projet, "afplay.log")
        self.journal_say = os.path.join(projet, "say.log")
        for journal in (self.journal_afplay, self.journal_say):
            open(journal, "w").close()
        # afplay est lancé par son nom : un petit programme qui appelle le faux, avec ce Python
        lecteur = os.path.join(projet, "afplay")
        with open(lecteur, "w") as f:
            f.write(f"#!/bin/sh\nexec {sys.executable!r} {FAUX_AFPLAY!r} \"$@\"\n")
        os.chmod(lecteur, 0o755)
        maison = os.path.join(projet, "maison")
        os.makedirs(maison, exist_ok=True)
        lanceur = f"""
import os, runpy, sys
sys.path.insert(0, {projet!r})
os.chdir({projet!r})
import voix
voix.SyntheseMac.commande = [sys.executable, {FAUX_SAY!r}]
voix.Voix.commande_lecture = {lecteur!r}
sys.argv = ["serveur.py", "--modele", os.path.join({projet!r}, "modeles", "mini.gguf"),
            "--cerveau", os.path.join({projet!r}, "cerveau.json"), "--fils", "1"]
runpy.run_path(os.path.join({projet!r}, "serveur.py"), run_name="__main__")
"""
        environnement = dict(os.environ, FAUX_AFPLAY_JOURNAL=self.journal_afplay, FAUX_AFPLAY_FACTEUR="0.2",
                             FAUX_SAY_JOURNAL=self.journal_say, PYTHONIOENCODING="utf-8", HOME=maison)
        self.processus = subprocess.Popen([sys.executable, "-u", "-c", lanceur], stdin=subprocess.PIPE,
                                          stdout=subprocess.PIPE, stderr=open(os.path.join(projet, "erreurs.log"), "w"),
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
        """Premier évènement qui remplit la condition. Les questions reçoivent « arrêter les questions »."""
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

    def systeme(self, action, delai=20):
        return self.attendre(lambda e: e.get("type") == "systeme" and e.get("action") == action, delai)

    def repondre(self, demande, ok=True, detail="", **donnees):
        self.envoyer(type="systeme_resultat", id=demande.get("id"), ok=ok, detail=detail, donnees=donnees)

    def textes(self, depuis=0, genres=("dire", "info", "erreur")):
        return [e.get("texte", "") for e in self.tous[depuis:] if e.get("type") in genres]

    def sons(self):
        return [ligne.split("\t") for ligne in open(self.journal_say, encoding="utf-8").read().splitlines()
                if ligne.startswith("son\t")]


def preparer_projet(racine, nom):
    projet = os.path.join(racine, nom)
    shutil.copytree(SOURCE, projet, ignore=shutil.ignore_patterns(*IGNORES))
    os.makedirs(os.path.join(projet, "modeles"))
    shutil.copy(os.path.join(ICI, "mini.gguf"), os.path.join(projet, "modeles", "mini.gguf"))
    return projet


racine = tempfile.mkdtemp(prefix="essai-voix-mac-")
BASE = {"garder_conversation": False, "memoire_sur_disque": False, "longueur_reponses": 128,
        "recherche_web": False, "actions_mac": True, "briefing": "jamais"}

# --- A. La voix de macOS -------------------------------------------------------------------------
print("A. Voix de macOS (Thomas)")
projet = preparer_projet(racine, "a")
m = Moteur(projet, dict(BASE, voix_active=True, voix_vitesse=200))
e = m.attendre(lambda e: e.get("type") == "parametres", 60)
verifier(e["type"] == "parametres", "panneau Paramètres reçu", [x.get("type") for x in m.tous])
if e["type"] == "parametres":
    ids = [v["id"] for v in e["voix_disponibles"]]
    verifier(ids[0] == "Thomas" and "Albert" not in ids and "Amélie" in ids and "Audrey (Enhanced)" in ids,
             "voix françaises du Mac, Thomas d'abord, pas les voix anglaises", ids)
    verifier(e["etat_voix"]["etat"] == "prete" and e["etat_voix"]["principale"] == "Thomas"
             and e["etat_voix"]["choisie"] == "Thomas", "voix prête : Thomas", e["etat_voix"])
    verifier(all(v["licence"] == "voix de macOS (Apple)" for v in e["voix_disponibles"]), "licence : voix de macOS")
    verifier(e["etat_demarrage"] == "", "ouverture avec la session : gérée par l'application", e["etat_demarrage"])
m.attendre(lambda e: e.get("type") == "attente", 60)
verifier(not any("Piper" in t or "Tom" in t.split() for t in m.textes()), "plus aucune trace de Piper", m.textes())

debut = len(m.tous)
m.envoyer(type="message", texte="salut")
e = m.attendre(lambda e: e.get("type") == "parole" and e.get("en_cours") is True, 30)
verifier(e["type"] == "parole", "il parle pendant la réponse", [x.get("type") for x in m.tous[debut:]])
m.attendre(lambda e: e.get("type") == "attente", 60)
limite = time.monotonic() + 120
while time.monotonic() < limite and not any(x.get("type") == "parole" and x.get("en_cours") is False
                                            for x in m.tous[debut:]):
    time.sleep(0.2)
verifier(any(x.get("type") == "parole" and x.get("en_cours") is False for x in m.tous[debut:]), "puis se tait")
sons = m.sons()
verifier(sons and all(s[1] == "Thomas" and s[2] == "200" for s in sons), "say : voix Thomas, débit 200", sons[:3])
verifier("debut" in open(m.journal_afplay).read(), "sons joués (afplay)")

m.envoyer(type="message", texte="/voix liste")
e = m.attendre(lambda e: e.get("type") == "dire", 10)
liste = e.get("texte", "")
verifier("Thomas" in liste and "Amélie (Canada)" in liste and "Audrey (France, améliorée)" in liste
         and "← utilisée" in liste.split("Thomas")[1].split("\n")[0], "/voix liste", liste)
m.attendre(lambda e: e.get("type") == "attente", 10)
m.envoyer(type="message", texte="/voix amélie")
e = m.attendre(lambda e: e.get("type") == "parametres", 10)
verifier(e.get("valeurs", {}).get("voix_nom") == "Amélie", "/voix amélie", e.get("valeurs"))
e = m.attendre(lambda e: e.get("type") == "dire", 10)
verifier("Voix activée : Amélie" in e.get("texte", ""), "annonce « Voix activée : Amélie »", e)
m.attendre(lambda e: e.get("type") == "attente", 30)
m.envoyer(type="message", texte="/voix bidule")
e = m.attendre(lambda e: e.get("type") == "erreur", 10)
verifier("Je n'ai pas de voix « bidule »" in e.get("texte", ""), "voix inconnue", e)
m.attendre(lambda e: e.get("type") == "attente", 10)
m.envoyer(type="reglage", cle="voix_nom", valeur="Tom")
e = m.attendre(lambda e: e.get("type") in ("parametres", "erreur"), 10)
verifier(e.get("type") == "erreur" or e.get("valeurs", {}).get("voix_nom") == "Amélie",
         "l'ancienne voix « Tom » est refusée", e)

# /voix plus : l'application ouvre les Réglages Système (bac à sable : le moteur ne lance pas « open »)
m.envoyer(type="message", texte="/voix plus")
e = m.systeme("ouvrir")
verifier(e.get("parametres", {}).get("cible", "").startswith("x-apple.systempreferences:") and e.get("attendre"),
         "/voix plus → l'application ouvre les Réglages Système", e)
m.repondre(e)
e = m.attendre(lambda e: e.get("type") == "info" and "Contenu énoncé" in e.get("texte", ""), 10)
verifier(e["type"] == "info", "… et explique où trouver les voix", e)
m.attendre(lambda e: e.get("type") == "attente", 10)

# essai, vitesse, tais-toi
open(m.journal_say, "w").close()
m.envoyer(type="reglage", cle="voix_vitesse", valeur=250)
m.envoyer(type="voix_essai")
e = m.attendre(lambda e: e.get("type") == "parole" and e.get("en_cours") is True, 20)
verifier(e["type"] == "parole", "essai de la voix")
m.envoyer(type="taire")
e = m.attendre(lambda e: e.get("type") == "parole" and e.get("en_cours") is False, 20)
verifier(e["type"] == "parole", "« taire » : il se tait")
verifier(any(s[1] == "Amélie" and s[2] == "250" for s in m.sons()), "essai : Amélie, débit 250", m.sons())

m.envoyer(type="quitter")
e = m.attendre(lambda e: e.get("type") == "fin", 20)
verifier(e["type"] == "fin", "fin propre")
m.processus.wait(10)
erreurs = open(os.path.join(projet, "erreurs.log")).read()
verifier("Traceback" not in erreurs, "aucune exception", erreurs[-1500:])

# --- B. Les actions, faites par l'application -------------------------------------------------
print("B. Actions sur le Mac, faites par l'application")
projet = preparer_projet(racine, "b")
m = Moteur(projet, dict(BASE, voix_active=False, briefing_meteo=False, briefing_agenda=True,
                        briefing_rappels=True),
           {"utilisateur": {"prenom": "Essai"}, "souvenirs": []})
m.attendre(lambda e: e.get("type") == "attente", 60)

# son réglé : la réponse vient de l'application (niveau réel)
m.envoyer(type="message", texte="volume 30")
e = m.systeme("volume")
verifier(e.get("parametres") == {"genre": "regler", "niveau": 30} and e.get("attendre") is True,
         "« volume 30 » → demande à l'application", e)
# pendant l'attente : un réglage et une vieille réponse arrivent ; le réglage attend son tour
m.envoyer(type="lire_parametres")
m.envoyer(type="systeme_resultat", id=-5, ok=False, detail="périmée", donnees={})
time.sleep(0.3)
verifier(not [x for x in m.tous if x.get("type") == "parametres" and m.tous.index(x) > m.tous.index(e)],
         "pendant l'action : rien d'autre n'est traité", [x.get("type") for x in m.tous[-5:]])
m.repondre(e, niveau=30)
r = m.attendre(lambda x: x.get("type") in ("dire", "erreur"), 10)
verifier(r.get("texte") == "Volume réglé à 30 %.", "réponse : « Volume réglé à 30 %. »", r)
p = m.attendre(lambda x: x.get("type") == "parametres", 10)
verifier(p["type"] == "parametres", "puis le réglage mis de côté est traité", [x.get("type") for x in m.tous[-6:]])
m.attendre(lambda x: x.get("type") == "attente", 10)
verifier("périmée" not in " ".join(m.textes()), "une réponse sans demande est ignorée", m.textes())

m.envoyer(type="message", texte="monte le son")
e = m.systeme("volume")
verifier(e.get("parametres", {}).get("genre") == "plus", "« monte le son » → plus", e)
m.repondre(e, niveau=65)
r = m.attendre(lambda x: x.get("type") in ("dire", "erreur"), 10)
verifier(r.get("texte") == "Volume réglé à 65 %.", "le niveau vient du Mac (65 %)", r)
m.attendre(lambda x: x.get("type") == "attente", 10)

m.envoyer(type="message", texte="coupe le son")
e = m.systeme("volume")
m.repondre(e, ok=False, detail="cette sortie son ne se coupe pas")
r = m.attendre(lambda x: x.get("type") in ("dire", "erreur"), 10)
verifier(r.get("type") == "erreur" and "ne se coupe pas" in r.get("texte", ""), "échec du Mac expliqué", r)
m.attendre(lambda x: x.get("type") == "attente", 10)

# ouvrir une app : hors bac à sable, sur un Mac, le moteur la trouve lui-même et donne son chemin ;
# s'il ne voit pas les dossiers d'applications (bac à sable de l'App Store, ou essai hors Mac),
# il donne ses noms à macOS. Les deux mènent à la même réponse.
m.envoyer(type="message", texte="ouvre la calculatrice")
e = m.systeme("ouvrir")
demande = e.get("parametres", {})
verifier(demande.get("noms", [])[:1] == ["Calculator"] or str(demande.get("cible", "")).endswith("/Calculator.app"),
         "calculatrice → Calculator (" + ("chemin trouvé ici" if "cible" in demande else "noms donnés à macOS") + ")", e)
m.repondre(e, nom="Calculator")
r = m.attendre(lambda x: x.get("type") in ("dire", "erreur"), 10)
verifier(r.get("texte") == "Calculator est ouvert.", "« Calculator est ouvert. »", r)
m.attendre(lambda x: x.get("type") == "attente", 10)
m.envoyer(type="message", texte="ouvre Bidule")
e = m.systeme("ouvrir")
m.repondre(e, ok=False, detail="application introuvable")
r = m.attendre(lambda x: x.get("type") in ("dire", "erreur"), 10)
verifier("Je ne trouve pas d'application « Bidule »" in r.get("texte", ""), "app inconnue de macOS", r)
m.attendre(lambda x: x.get("type") == "attente", 10)

# site : l'application l'ouvre
m.envoyer(type="message", texte="ouvre iaover.fr")
e = m.systeme("ouvrir")
verifier(e.get("parametres", {}).get("cible") == "https://iaover.fr", "site → adresse https donnée à l'application", e)
m.repondre(e)
m.attendre(lambda x: x.get("type") == "attente", 10)

# minuteur : la notification part d'un autre fil, sans attendre de réponse
m.envoyer(type="message", texte="minuteur 2 secondes pour les pâtes")
m.attendre(lambda x: x.get("type") == "attente", 10)
e = m.systeme("notifier", 15)
verifier(e.get("attendre") is False and "pâtes" in e.get("parametres", {}).get("texte", ""),
         "fin du minuteur : notification demandée, sans attendre", e)
a = m.attendre(lambda x: x.get("type") == "alerte", 10)
verifier(a["type"] == "alerte", "… et alerte dans la discussion", a)

# briefing : agenda et rappels lus par l'application
m.envoyer(type="message", texte="/briefing")
e = m.systeme("agenda")
verifier(e.get("parametres") == {"agenda": True, "rappels": True}, "briefing → agenda et rappels demandés", e)
hier = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
aujourdhui = time.strftime("%Y-%m-%d")
m.repondre(e, evenements=[{"titre": "Dentiste", "heure": "09:30", "journee": False},
                          {"titre": "Anniversaire de Léa", "heure": "00:00", "journee": True}],
           rappels=[{"titre": "Pain", "echeance": hier + "T18:00"},
                    {"titre": "Appeler le garage", "echeance": aujourdhui + "T14:05"},
                    {"titre": "Arroser", "echeance": aujourdhui + "T00:00"}])
r = m.attendre(lambda x: x.get("type") == "dire", 15)
texte = r.get("texte", "")
verifier("Agenda : toute la journée : Anniversaire de Léa · 9 h 30 Dentiste." in texte, "agenda du jour", texte)
verifier("Pain (en retard)" in texte and "Appeler le garage (14 h 05)" in texte and "Arroser" in texte
         and "Arroser (" not in texte, "rappels : en retard, avec heure, sans heure", texte)
m.attendre(lambda x: x.get("type") == "attente", 10)

# accès aux rappels refusé : l'agenda est quand même donné
m.envoyer(type="message", texte="/briefing")
e = m.systeme("agenda")
m.repondre(e, evenements=[], erreur_rappels="accès refusé : Réglages Système ▸ Confidentialité et sécurité ▸ Rappels")
r = m.attendre(lambda x: x.get("type") == "dire", 15)
texte = r.get("texte", "")
verifier("Agenda : rien de prévu aujourd'hui." in texte and "Rappels : indisponible (accès refusé" in texte,
         "rappels refusés : expliqué, agenda quand même", texte)
m.attendre(lambda x: x.get("type") == "attente", 10)

# ouverture avec la session : l'application s'en charge, aucune fiche LaunchAgents
m.envoyer(type="message", texte="/démarrage oui")
e = m.attendre(lambda x: x.get("type") == "parametres" and x["valeurs"].get("demarrage_session") is True, 10)
verifier(e["type"] == "parametres" and e.get("etat_demarrage") == "", "/démarrage oui → réglage (l'application s'inscrit)", e)
m.attendre(lambda x: x.get("type") == "attente", 10)
fiches = [nom for _, _, noms in os.walk(os.path.join(projet, "maison")) for nom in noms]
verifier(not fiches, "aucune fiche écrite dans la bibliothèque (bac à sable)", fiches)

# l'application se ferme pendant que le moteur attend une action
m.envoyer(type="message", texte="volume 10")
e = m.systeme("volume")
m.envoyer(type="quitter")
e = m.attendre(lambda x: x.get("type") == "fin", 20)
verifier(e["type"] == "fin", "« quitter » pendant une action : fin propre", e)
m.processus.wait(10)
erreurs = open(os.path.join(projet, "erreurs.log")).read()
verifier("Traceback" not in erreurs, "aucune exception", erreurs[-1500:])

shutil.rmtree(racine, True)
print(f"\n{len(reussis)} réussis, {len(rates)} ratés" + (f" : {rates}" if rates else ""))
sys.exit(1 if rates else 0)
