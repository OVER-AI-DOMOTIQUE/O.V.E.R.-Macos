# -*- coding: utf-8 -*-
"""Robustesse du protocole application ↔ serveur.py : messages abîmés, inconnus, énormes, signaux.

Usage : venv39/bin/python test_protocole.py DOSSIER_PROJET_SOURCE
"""
import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

# copie du projet sans le lourd ni le personnel (modèle, environnement, voix, cerveau, conversation…)
IGNORES = ("app-mac", "voix", "ecoute", "modeles", ".venv", "tests", "OVER.app", "_sauvegarde", "dependances",
           "__pycache__", ".DS_Store", "*.json", "*.bin")
ICI = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.abspath(sys.argv[1])
reussis, rates = [], []


def verifier(condition, nom, detail=""):
    (reussis if condition else rates).append(nom)
    print(("  ok   " if condition else "  RATÉ ") + nom + ("" if condition else f"   → {str(detail)[:300]}"))


racine = tempfile.mkdtemp(prefix="essai-protocole-")
projet = os.path.join(racine, "p")
shutil.copytree(SOURCE, projet, ignore=shutil.ignore_patterns(*IGNORES))
os.makedirs(os.path.join(projet, "modeles"))
shutil.copy(os.path.join(ICI, "mini.gguf"), os.path.join(projet, "modeles", "mini.gguf"))
with open(os.path.join(projet, "parametres.json"), "w") as f:
    json.dump({"garder_conversation": False, "memoire_sur_disque": False, "longueur_reponses": 128,
               "recherche_web": False, "actions_mac": False, "briefing": "jamais"}, f)
with open(os.path.join(projet, "cerveau.json"), "w") as f:
    json.dump({"utilisateur": {"prenom": "Essai"}, "souvenirs": []}, f)

processus = subprocess.Popen(
    [sys.executable, "-u", os.path.join(projet, "serveur.py"), "--modele", os.path.join(projet, "modeles", "mini.gguf"),
     "--fils", "1"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=open(os.path.join(racine, "err.log"), "w"),
    cwd=projet, env=dict(os.environ, PYTHONIOENCODING="utf-8"))
evenements = queue.Queue()
tous = []


def lire():
    for ligne in processus.stdout:
        try:
            e = json.loads(ligne.decode("utf-8"))
        except ValueError:
            e = {"type": "illisible", "brut": ligne[:200]}
        tous.append(e)
        evenements.put(e)
    evenements.put({"type": "__fin__"})


threading.Thread(target=lire, daemon=True).start()


def brut(octets):
    processus.stdin.write(octets)
    processus.stdin.flush()


def envoyer(**commande):
    brut((json.dumps(commande, ensure_ascii=False) + "\n").encode("utf-8"))


def attendre(condition, delai=30):
    limite = time.monotonic() + delai
    while time.monotonic() < limite:
        try:
            e = evenements.get(timeout=max(0.05, limite - time.monotonic()))
        except queue.Empty:
            break
        if e.get("type") == "question":
            envoyer(type="reponse", texte=None)
        if condition(e):
            return e
        if e["type"] == "__fin__":
            return e
    return {"type": "__rien__"}


def vivant(nom):
    """Le serveur répond encore : /aide renvoie la liste."""
    envoyer(type="message", texte="/aide")
    e = attendre(lambda e: e["type"] == "aide", 20)
    verifier(e["type"] == "aide", f"toujours vivant après : {nom}", e)


e = attendre(lambda e: e["type"] == "attente", 90)
verifier(e["type"] == "attente", "serveur prêt", [x.get("type") for x in tous])

brut(b"{ceci n'est pas du json\n")
e = attendre(lambda e: e["type"] == "erreur", 10)
verifier("illisible" in e.get("texte", ""), "JSON cassé → erreur signalée", e)
vivant("JSON cassé")
for octets, nom in ((b"[1, 2, 3]\n", "tableau"), (b"42\n", "nombre"), (b"null\n", "null"), (b"   \n", "ligne blanche"),
                    (b'{"type": 5}\n', "type numérique"),
                    (b'{"type": "inconnu", "x": 1}\n', "type inconnu"), (b'{"type": "reponse", "texte": "o"}\n',
                                                                         "réponse sans question")):
    brut(octets)
    vivant(nom)
for commande, nom in (({"type": "message", "texte": None}, "message null"), ({"type": "message", "texte": 12345}, "message nombre"),
                      ({"type": "message", "texte": ["a"]}, "message liste"),
                      ({"type": "reglage", "cle": "voix_vitesse", "valeur": {"x": 1}}, "réglage objet"),
                      ({"type": "reglage", "cle": ["a"], "valeur": 1}, "clé de réglage liste"),
                      ({"type": "cerveau", "action": "modifier_souvenir", "numero": "2", "texte": "x"}, "cerveau numéro texte"),
                      ({"type": "cerveau", "action": "definir", "champ": None}, "cerveau champ null"),
                      ({"type": "transcrire", "fichier": "/etc/passwd"}, "transcrire un fichier quelconque"),
                      ({"type": "transcrire", "fichier": None, "apercu": "oui", "id": {"x": 1}}, "transcrire abîmé"),
                      ({"type": "voix_ajouter", "chemin": "/etc"}, "voix_ajouter /etc"),
                      ({"type": "voix_ajouter", "chemin": "../../../etc/passwd.onnx"}, "voix_ajouter chemin relatif")):
    envoyer(**commande)
    vivant(nom)
e = attendre(lambda e: e["type"] == "transcription", 10)
verifier(e.get("erreur") in ("a_installer", "son illisible") or not e.get("ok"), "transcrire sans écoute : erreur propre", e)
# message énorme (1 Mo) et caractères spéciaux
envoyer(type="message", texte="éléphant " * 110_000)
e = attendre(lambda e: e["type"] in ("erreur", "fin_reponse"), 120)
verifier(e["type"] == "erreur" and "trop long" in e.get("texte", ""), "message de 1 Mo → « trop long »", e)
vivant("message de 1 Mo")
envoyer(type="message", texte="\u0000‮😀 bonjour ퟿")
e = attendre(lambda e: e["type"] == "fin_reponse", 60)
verifier(e["type"] == "fin_reponse", "caractères spéciaux → réponse normale", e)
# rafale : 200 commandes d'un coup
for k in range(200):
    envoyer(type="lire_parametres")
e = attendre(lambda e: False, 3)
vivant("rafale de 200 messages")
# Arrêter (SIGINT) au repos : ignoré
processus.send_signal(signal.SIGINT)
time.sleep(0.5)
verifier(processus.poll() is None, "SIGINT au repos : ignoré (pas de fermeture)")
vivant("SIGINT au repos")
envoyer(type="quitter")
e = attendre(lambda e: e["type"] == "fin", 20)
verifier(e["type"] == "fin", "quitter → « fin »", e)
processus.wait(20)
verifier(processus.returncode == 0, "fermeture propre (code 0)", processus.returncode)
erreurs = open(os.path.join(racine, "err.log")).read()
verifier("Traceback" not in erreurs, "aucune trace d'exception dans le journal", erreurs[-1500:])
# octets non UTF-8 : relancé à part, car le serveur s'arrête
processus = subprocess.Popen(
    [sys.executable, "-u", os.path.join(projet, "serveur.py"), "--modele", os.path.join(projet, "modeles", "mini.gguf"),
     "--fils", "1"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=open(os.path.join(racine, "err2.log"), "w"),
    cwd=projet, env=dict(os.environ, PYTHONIOENCODING="utf-8"))
evenements = queue.Queue()
threading.Thread(target=lire, daemon=True).start()
attendre(lambda e: e["type"] == "attente", 90)
brut(b"\xff\xfe\xfd\n")
time.sleep(2)
fin_brutale = processus.poll() is not None
erreurs = open(os.path.join(racine, "err2.log")).read()
verifier(not fin_brutale, "octets non UTF-8 sur l'entrée : ligne ignorée, le moteur continue",
         erreurs.strip().splitlines()[-1:] if fin_brutale else "")
if not fin_brutale:
    vivant("octets non UTF-8")
    processus.kill()
illisibles = [e for e in tous if e["type"] == "illisible"]
verifier(not illisibles, "toutes les lignes envoyées par le serveur sont du JSON valide", illisibles[:2])
print(f"\n{len(reussis)} réussis, {len(rates)} ratés" + (f" : {rates}" if rates else ""))
shutil.rmtree(racine, True)
sys.exit(1 if rates else 0)
