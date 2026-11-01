# -*- coding: utf-8 -*-
"""Premier lancement de l'application : installation du modèle (installation.py) et parcours complet
avec serveur.py (téléchargement, reprise, pause, fermeture, fichier déjà téléchargé et déposé par
l'application dans import/).

Tout se passe dans un dossier temporaire, avec un petit serveur HTTP local et le mini-modèle d'essai :
rien n'est téléchargé sur Internet, rien n'est écrit dans le dossier du projet.

Usage : python test_installation.py DOSSIER_PROJET_SOURCE
"""
import hashlib
import json
import os
import queue
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time

ICI = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.abspath(sys.argv[1])
sys.dont_write_bytecode = True   # rien d'écrit dans le dossier du projet
racine = tempfile.mkdtemp(prefix="essai-installation-")
os.environ["OVER_DONNEES"] = os.path.join(racine, "donnees")
sys.path.insert(0, ICI)
sys.path.insert(0, SOURCE)
import serveur_fichiers  # noqa: E402
import installation  # noqa: E402
import reglages  # noqa: E402

reussis, rates = [], []


def verifier(condition, nom, detail=""):
    (reussis if condition else rates).append(nom)
    print(("  ok   " if condition else "  RATÉ ") + nom + ("" if condition else f"   → {str(detail)[:300]}"))


def sha(chemin):
    return hashlib.sha256(open(chemin, "rb").read()).hexdigest()


# --- fichiers servis : un faux « modèle » de 3 Mo, un autre de même taille mais différent ----------
site = os.path.join(racine, "site")
os.makedirs(site)
contenu = os.urandom(3_000_000)
for nom, donnees in (("modele.gguf", contenu), ("lent.gguf", contenu), ("coupe.gguf", contenu),
                     ("faux.gguf", os.urandom(3_000_000))):
    with open(os.path.join(site, nom), "wb") as f:
        f.write(donnees)
shutil.copy(os.path.join(ICI, "mini.gguf"), os.path.join(site, "mini.gguf"))
serveur, adresse = serveur_fichiers.demarrer(site, coupure={"coupe.gguf": 500_000},
                                             lent={"lent.gguf": 0.08})
EMPREINTE = hashlib.sha256(contenu).hexdigest()
donnees = os.environ["OVER_DONNEES"]
cible = os.path.join(donnees, "modeles", "modele.gguf")


def modele(nom="modele.gguf"):
    return installation.Modele(cible, adresse + nom, len(contenu), EMPREINTE)


phases = []


def suivre(phase, fait, total):
    phases.append((phase, fait, total))


def nettoyer():
    for chemin in (cible, cible + ".part"):
        if os.path.exists(chemin):
            os.remove(chemin)


print("installation.py")

# processeur : le moteur demande AVX2 (Mac Intel de 2013 et plus, ou Rosetta de macOS 15 et plus)
def faux_mac(**valeurs):
    return lambda nom: valeurs.get(nom.replace(".", "_"))
verifier(installation.explication_processeur(faux_mac(hw_optional_avx2_0=1), (13, 6)) == "",
         "processeur : Mac Intel avec AVX2 → accepté")
verifier("2013" in installation.explication_processeur(faux_mac(hw_optional_avx2_0=0), (12, 7)),
         "processeur : Mac Intel sans AVX2 → explication")
verifier("macOS 15" in installation.explication_processeur(faux_mac(sysctl_proc_translated=1), (14, 5)),
         "processeur : puce Apple sous Rosetta, macOS 14 → explication (au lieu d'un plantage)")
verifier(installation.explication_processeur(faux_mac(sysctl_proc_translated=1, hw_optional_avx2_0=0), (15, 1)) == "",
         "processeur : puce Apple sous Rosetta, macOS 15 → accepté")
verifier(installation.explication_processeur(faux_mac(), (0,)) == "", "processeur : rien de connu → on laisse démarrer")
verifier(installation.lire_sysctl("hw.optional.avx2_0") is None or sys.platform == "darwin",
         "processeur : hors Mac, aucune lecture")
etat = installation.etat(modele())
verifier(etat["taille"] == len(contenu) and etat["recu"] == 0 and etat["manque"] == 0 and "ancien" not in etat,
         "état de départ : taille, rien de reçu, place suffisante", etat)
verifier(reglages.DOSSIER_DONNEES == donnees and reglages.CHEMIN_MODELE.startswith(donnees),
         "OVER_DONNEES : le modèle va avec les données", reglages.CHEMIN_MODELE)
ok, detail = installation.telecharger(suivre, modele())
verifier(not ok and "refusée" in detail, "adresse http refusée (https seulement)", detail)
installation.ESSAI_HTTP = True

ok, detail = installation.telecharger(suivre, modele())
verifier(ok and os.path.isfile(cible) and sha(cible) == EMPREINTE and not os.path.exists(cible + ".part"),
         "téléchargement complet, vérifié et rangé", detail)
verifier({"telechargement", "verification"} <= {p for p, _, _ in phases}, "progression : téléchargement puis vérification",
         sorted({p for p, _, _ in phases}))
verifier(phases[-1] == ("verification", len(contenu), len(contenu)), "progression finale à 100 %", phases[-1:])

# reprise d'un téléchargement interrompu
nettoyer()
os.makedirs(os.path.dirname(cible), exist_ok=True)
with open(cible + ".part", "wb") as f:
    f.write(contenu[:1_000_000])
serveur.demandes.clear()
verifier(installation.etat(modele())["recu"] == 1_000_000, "état : morceau déjà reçu annoncé")
ok, detail = installation.telecharger(suivre, modele())
verifier(ok and sha(cible) == EMPREINTE, "reprise : fichier complet et juste", detail)
verifier(("modele.gguf", "bytes=1000000-") in serveur.demandes, "reprise : seule la fin est redemandée",
         serveur.demandes)

# connexion coupée en route, puis reprise
nettoyer()
ok, detail = installation.telecharger(suivre, modele("coupe.gguf"))
recu = os.path.getsize(cible + ".part") if os.path.exists(cible + ".part") else 0
verifier(not ok and "reprendra" in detail and 0 < recu < len(contenu) and not os.path.exists(cible),
         "connexion coupée : erreur claire, morceau reçu gardé", (detail, recu))
ok, detail = installation.telecharger(suivre, modele("coupe.gguf"))
verifier(ok and sha(cible) == EMPREINTE, "connexion coupée : la fois suivante reprend et termine", detail)

# fichier différent de l'original
nettoyer()
ok, detail = installation.telecharger(suivre, modele("faux.gguf"))
verifier(not ok and "ne correspond pas" in detail and not os.path.exists(cible)
         and not os.path.exists(cible + ".part"), "empreinte fausse : refusé et effacé (pas 2 Go perdus)", detail)

# morceau plus gros que l'original : jeté
with open(cible + ".part", "wb") as f:
    f.write(os.urandom(len(contenu) + 10))
ok, detail = installation.telecharger(suivre, modele())
verifier(ok and sha(cible) == EMPREINTE, "morceau trop gros : jeté, téléchargement refait", detail)

# place insuffisante
nettoyer()
vrai_espace = installation.espace_libre
installation.espace_libre = lambda dossier: 1000
ok, detail = installation.telecharger(suivre, modele())
etat = installation.etat(modele())
installation.espace_libre = vrai_espace
verifier(not ok and "il manque" in detail and not os.path.exists(cible + ".part"),
         "disque plein : rien n'est téléchargé, explication", detail)
verifier(etat["manque"] > 0, "état : place manquante annoncée", etat)

# pause (bouton Pause = KeyboardInterrupt) pendant un envoi lent
nettoyer()
arrete = {"fait": False}


def pause(phase, fait, total):
    if phase == "telechargement" and fait > 0 and not arrete["fait"]:
        arrete["fait"] = True
        raise KeyboardInterrupt


try:
    installation.telecharger(pause, modele("lent.gguf"))
    interrompu = False
except KeyboardInterrupt:
    interrompu = True
taille1 = os.path.getsize(cible + ".part") if os.path.exists(cible + ".part") else 0
time.sleep(1.2)
taille2 = os.path.getsize(cible + ".part") if os.path.exists(cible + ".part") else 0
verifier(interrompu and 0 < taille1 == taille2 < len(contenu), "pause : curl arrêté, morceau reçu gardé",
         (interrompu, taille1, taille2))
verifier(installation._processus["telechargement"] is None, "pause : plus aucun téléchargement en cours")
ok, detail = installation.telecharger(suivre, modele("lent.gguf"))
verifier(ok and sha(cible) == EMPREINTE, "après la pause : reprise jusqu'au bout", detail)

# importer un fichier
nettoyer()
ailleurs = os.path.join(racine, "ailleurs")
os.makedirs(ailleurs)
shutil.copy(os.path.join(site, "modele.gguf"), os.path.join(ailleurs, "bon.gguf"))
shutil.copy(os.path.join(site, "faux.gguf"), os.path.join(ailleurs, "faux.gguf"))
with open(os.path.join(ailleurs, "petit.gguf"), "wb") as f:
    f.write(b"GGUF" * 10)
ok, detail = installation.importer(os.path.join(ailleurs, "absent.gguf"), suivre, modele())
verifier(not ok and "introuvable" in detail, "importer : fichier absent", detail)
ok, detail = installation.importer(os.path.join(ailleurs, "petit.gguf"), suivre, modele())
verifier(not ok and "pas le modèle attendu" in detail, "importer : mauvaise taille refusée", detail)
ok, detail = installation.importer(os.path.join(ailleurs, "faux.gguf"), suivre, modele())
verifier(not ok and "empreinte" in detail and not os.path.exists(cible) and not os.path.exists(cible + ".part"),
         "importer : même taille, autre contenu → refusé, rien gardé", detail)
ok, detail = installation.importer(None, suivre, modele())
verifier(not ok, "importer : chemin vide refusé", detail)
phases.clear()
ok, detail = installation.importer(os.path.join(ailleurs, "bon.gguf"), suivre, modele())
verifier(ok and sha(cible) == EMPREINTE and os.path.isfile(os.path.join(ailleurs, "bon.gguf")),
         "importer : copié, vérifié, l'original reste", detail)
ok, detail = installation.importer(cible, suivre, modele())
verifier(ok and sha(cible) == EMPREINTE, "importer : le fichier déjà en place est accepté tel quel", detail)

# copie déposée par l'application dans import/ (bac à sable) : vérifiée puis DÉPLACÉE, jamais copiée
nettoyer()
depot = installation.dossier_import()
os.makedirs(depot, exist_ok=True)
for nom, source in (("depose.gguf", "modele.gguf"), ("faux-depose.gguf", "faux.gguf")):
    shutil.copy(os.path.join(site, source), os.path.join(depot, nom))
with open(os.path.join(depot, "petit-depose.gguf"), "wb") as f:
    f.write(b"GGUF" * 10)
ok, detail = installation.importer(os.path.join(depot, "faux-depose.gguf"), suivre, modele())
verifier(not ok and "empreinte" in detail and not os.path.exists(os.path.join(depot, "faux-depose.gguf"))
         and not os.path.exists(cible), "import/ : copie fausse refusée et effacée (2 Go qui ne servent à rien)", detail)
ok, detail = installation.importer(os.path.join(depot, "petit-depose.gguf"), suivre, modele())
verifier(not ok and not os.path.exists(os.path.join(depot, "petit-depose.gguf")),
         "import/ : copie de mauvaise taille refusée et effacée", detail)
phases.clear()
ok, detail = installation.importer(os.path.join(depot, "depose.gguf"), suivre, modele())
verifier(ok and sha(cible) == EMPREINTE and not os.path.exists(os.path.join(depot, "depose.gguf")),
         "import/ : copie vérifiée puis déplacée à sa place (pas de deuxième copie)", detail)
verifier({p for p, _, _ in phases} == {"verification"}, "import/ : seulement la vérification",
         sorted({p for p, _, _ in phases}))
restes = [nom for _, _, fichiers in os.walk(donnees) for nom in fichiers if nom.endswith(".part")]
verifier(not restes, "aucun fichier .part qui traîne", restes)

# --- parcours complet avec serveur.py (comme l'application emballée) ------------------------------
print("serveur.py")
ENVELOPPE = r"""
import runpy, sys
projet, adresse, taille, empreinte = sys.argv[1:5]
sys.path.insert(0, projet)
import installation, reglages
reglages.ADRESSE_MODELE, reglages.TAILLE_MODELE, reglages.EMPREINTE_MODELE = adresse, int(taille), empreinte
installation.ESSAI_HTTP = True
sys.argv = [projet + "/serveur.py", "--fils", "1"]
runpy.run_path(sys.argv[0], run_name="__main__")
"""
mini = os.path.join(ICI, "mini.gguf")
TAILLE_MINI, EMPREINTE_MINI = os.path.getsize(mini), sha(mini)


class Moteur:
    def __init__(self, dossier_donnees, nom_fichier="mini.gguf", taille=None, empreinte=None):
        os.makedirs(dossier_donnees, exist_ok=True)
        with open(os.path.join(dossier_donnees, "parametres.json"), "w") as f:
            json.dump({"garder_conversation": False, "memoire_sur_disque": False, "recherche_web": False,
                       "actions_mac": False, "briefing": "jamais"}, f)
        with open(os.path.join(dossier_donnees, "cerveau.json"), "w") as f:
            json.dump({"utilisateur": {"prenom": "Essai"}, "souvenirs": []}, f)
        environnement = {cle: valeur for cle, valeur in os.environ.items() if not cle.startswith("OVER_")}
        environnement.update(OVER_DONNEES=dossier_donnees, OVER_EMBALLE="1", PYTHONIOENCODING="utf-8",
                             PYTHONDONTWRITEBYTECODE="1")
        self.journal = open(os.path.join(racine, f"err-{len(os.listdir(racine))}.log"), "w")
        self.processus = subprocess.Popen(
            [sys.executable, "-B", "-c", ENVELOPPE, SOURCE, adresse + nom_fichier, str(taille or TAILLE_MINI),
             empreinte or EMPREINTE_MINI],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.journal, env=environnement, cwd=racine)
        self.evenements, self.tous = queue.Queue(), []
        threading.Thread(target=self._lire, daemon=True).start()

    def _lire(self):
        for ligne in self.processus.stdout:
            e = json.loads(ligne.decode("utf-8"))
            self.tous.append(e)
            self.evenements.put(e)
        self.evenements.put({"type": "__fin__"})

    def envoyer(self, **commande):
        self.processus.stdin.write((json.dumps(commande) + "\n").encode("utf-8"))
        self.processus.stdin.flush()

    def attendre(self, types, delai=60):
        limite = time.monotonic() + delai
        while time.monotonic() < limite:
            try:
                e = self.evenements.get(timeout=max(0.05, limite - time.monotonic()))
            except queue.Empty:
                break
            if e["type"] == "question":
                self.envoyer(type="reponse", texte=None)
            if e["type"] in types or e["type"] == "__fin__":
                return e
        return {"type": "__rien__"}


# 1. téléchargement par l'application, jusqu'à la conversation
donnees1 = os.path.join(racine, "donnees1")
moteur = Moteur(donnees1)
e = moteur.attendre({"modele_absent"})
verifier(e["type"] == "modele_absent" and e.get("taille") == TAILLE_MINI and e.get("recu") == 0
         and "ancien" not in e and e.get("erreur") == "", "modèle absent : l'application est prévenue", e)
moteur.envoyer(type="lire_parametres")   # ignoré tant que le modèle n'est pas là
moteur.envoyer(type="modele_importer", chemin=os.path.join(racine, "nulle-part.gguf"))
e = moteur.attendre({"modele_absent"})
verifier("introuvable" in e.get("erreur", ""), "import d'un fichier absent : erreur affichée", e)
moteur.envoyer(type="modele_telecharger")
e = moteur.attendre({"modele_pret", "modele_absent"})
verifier(e["type"] == "modele_pret", "téléchargement → modèle prêt", e)
progressions = [x for x in moteur.tous if x["type"] == "modele_progression"]
verifier(progressions and progressions[-1]["phase"] == "verification" and progressions[-1]["fait"] == TAILLE_MINI,
         "progression envoyée jusqu'à la vérification", progressions[-2:])
e = moteur.attendre({"attente"}, 120)
verifier(e["type"] == "attente", "puis le modèle se charge et O.V.E.R. attend un message", [x["type"] for x in moteur.tous][-8:])
moteur.envoyer(type="quitter")
moteur.attendre({"fin"})
moteur.processus.wait(30)
verifier(moteur.processus.returncode == 0, "fermeture propre", moteur.processus.returncode)
chemin = os.path.join(donnees1, "modeles", reglages.NOM_MODELE)
verifier(os.path.isfile(chemin) and sha(chemin) == EMPREINTE_MINI, "modèle rangé dans le dossier des données")
verifier(stat.S_IMODE(os.stat(chemin).st_mode) & 0o077 == 0
         and stat.S_IMODE(os.stat(os.path.dirname(chemin)).st_mode) == 0o700,
         "application emballée : modèle et dossier lisibles par toi seul",
         oct(os.stat(chemin).st_mode), )

# 2. pause puis fermeture de l'application pendant un téléchargement lent
donnees2 = os.path.join(racine, "donnees2")
moteur = Moteur(donnees2, "lent.gguf", taille=len(contenu), empreinte=EMPREINTE)
moteur.attendre({"modele_absent"})
moteur.envoyer(type="modele_telecharger")
e = moteur.attendre({"modele_progression"})
time.sleep(0.6)
moteur.processus.send_signal(signal.SIGINT)
e = moteur.attendre({"modele_absent"})
verifier(e["type"] == "modele_absent" and e.get("erreur", "").startswith("En pause") and 0 < e.get("recu", 0) < len(contenu),
         "Pause : téléchargement arrêté, morceau gardé, l'application peut reprendre", e)
moteur.envoyer(type="modele_telecharger")
moteur.attendre({"modele_progression"})
time.sleep(0.6)
moteur.processus.terminate()   # l'application se ferme
moteur.processus.wait(10)
time.sleep(0.5)
encore = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True).stdout
orphelins = [ligne for ligne in encore.splitlines()
             if ligne.split() and ligne.split()[0].endswith("curl") and "lent.gguf" in ligne]
verifier(not orphelins, "fermeture pendant le téléchargement : curl ne continue pas seul", orphelins)
partiel = os.path.join(donnees2, "modeles", reglages.NOM_MODELE + ".part")
verifier(os.path.isfile(partiel) and not os.path.exists(partiel[:-5]), "fermeture : morceau gardé pour la prochaine fois")

# 3. fermer pendant que l'application propose l'installation
moteur = Moteur(os.path.join(racine, "donnees3"))
moteur.attendre({"modele_absent"})
moteur.envoyer(type="quitter")
e = moteur.attendre({"fin"})
moteur.processus.wait(20)
verifier(e["type"] == "fin" and moteur.processus.returncode == 0, "quitter avant l'installation : fin propre", e)

# 4. « J'ai déjà le fichier… » : l'application l'a copié dans import/ de tes données
donnees4 = os.path.join(racine, "donnees4")
os.makedirs(os.path.join(donnees4, "import"))
depose = os.path.join(donnees4, "import", reglages.NOM_MODELE)
shutil.copy(mini, depose)
moteur = Moteur(donnees4)
e = moteur.attendre({"modele_absent"})
verifier(e["type"] == "modele_absent", "modèle absent", e)
moteur.envoyer(type="modele_importer", chemin=depose)
e = moteur.attendre({"modele_pret", "modele_absent"})
verifier(e["type"] == "modele_pret", "fichier déposé repris", e)
e = moteur.attendre({"attente"}, 120)
verifier(e["type"] == "attente", "puis O.V.E.R. démarre", e)
moteur.envoyer(type="quitter")
moteur.attendre({"fin"})
moteur.processus.wait(20)
chemin = os.path.join(donnees4, "modeles", reglages.NOM_MODELE)
verifier(os.path.isfile(chemin) and sha(chemin) == EMPREINTE_MINI and not os.path.exists(depose),
         "fichier déposé : rangé dans modeles/, plus rien dans import/")

erreurs = "".join(open(os.path.join(racine, nom)).read() for nom in os.listdir(racine) if nom.startswith("err-"))
verifier("Traceback" not in erreurs, "aucune trace d'exception dans les journaux", erreurs[-1500:])
print(f"\n{len(reussis)} réussis, {len(rates)} ratés" + (f" : {rates}" if rates else ""))
serveur.shutdown()
shutil.rmtree(racine, True)
sys.exit(1 if rates else 0)
