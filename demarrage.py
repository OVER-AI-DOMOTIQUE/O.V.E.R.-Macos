# -*- coding: utf-8 -*-
"""
Démarrage d'O.V.E.R. avec la session macOS (paramètres demarrage_session et demarrage_discret).

macOS ouvre au moment de la connexion les programmes décrits dans ~/Library/LaunchAgents.
O.V.E.R. y range une petite fiche (fr.iaover.over.demarrage.plist) qui lance OVER.app, celle
qui est rangée à côté des scripts. Rien d'autre : pas de service qui tourne en permanence,
pas de droits administrateur. La fiche est effacée quand on décoche le réglage.

La fiche lance directement le programme de l'application (OVER.app/Contents/MacOS/OVER) :
macOS l'affiche ainsi sous le nom et l'icône d'O.V.E.R. (avec « open », il affichait « open »).

« Discret » : option --discret, l'application se cache dès son lancement : pas de fenêtre,
juste son icône dans la barre des menus ; le modèle se charge en arrière-plan.

Sur macOS 13 et plus récent, la fiche apparaît dans Réglages Système ▸ Général ▸ Ouverture
et extensions (« Autoriser en arrière-plan »).
"""

import os
import plistlib
import subprocess
import sys

SUR_MAC = sys.platform == "darwin"
# Application Mac (App Store, bac à sable) : c'est elle qui s'inscrit auprès de macOS (éléments de
# connexion, SMAppService) ; aucune fiche LaunchAgents n'est écrite. serveur.py le met à True.
GERE_PAR_APPLICATION = False
DOSSIER_PROJET = os.path.dirname(os.path.abspath(__file__))
ETIQUETTE = "fr.iaover.over.demarrage"
IDENTIFIANT_PAR_DEFAUT = "fr.iaover.over"


def chemin_fiche():
    return os.path.join(os.path.expanduser("~"), "Library", "LaunchAgents", ETIQUETTE + ".plist")


def chemin_application():
    """L'application qui a lancé le moteur (version emballée), sinon OVER.app rangée à côté des scripts
    (Xcode et construire_app.sh l'y copient)."""
    return os.environ.get("OVER_APPLICATION") or os.path.join(DOSSIER_PROJET, "OVER.app")


def fiche_application(application):
    """Info.plist de l'application ({} si illisible)."""
    try:
        with open(os.path.join(application, "Contents", "Info.plist"), "rb") as fichier:
            fiche = plistlib.load(fichier)
            return fiche if isinstance(fiche, dict) else {}
    except (OSError, ValueError, plistlib.InvalidFileException):
        return {}


def identifiant_application(application):
    """CFBundleIdentifier de l'application (fr.iaover.over)."""
    return str(fiche_application(application).get("CFBundleIdentifier") or IDENTIFIANT_PAR_DEFAUT)


def programme_application(application):
    """Le programme dans l'application : OVER.app/Contents/MacOS/OVER."""
    nom = str(fiche_application(application).get("CFBundleExecutable") or "OVER")
    return os.path.join(application, "Contents", "MacOS", nom)


def contenu_fiche(application, discret):
    commande = [programme_application(application)]
    if discret:
        commande.append("--discret")   # l'application se cache dès son lancement
    return {
        "Label": ETIQUETTE,
        "ProgramArguments": commande,
        "RunAtLoad": True,
        "LimitLoadToSessionType": "Aqua",          # seulement quand on se connecte à l'écran
        "ProcessType": "Interactive",
        "AssociatedBundleIdentifiers": [identifiant_application(application)],
    }


def lire_fiche():
    try:
        with open(chemin_fiche(), "rb") as fichier:
            return plistlib.load(fichier)
    except (OSError, ValueError, plistlib.InvalidFileException):
        return None


def etat(discret):
    """actif | a_mettre_a_jour (dossier déplacé, réglage changé) | inactif"""
    fiche = lire_fiche()
    if fiche is None:
        return "inactif"
    return "actif" if fiche == contenu_fiche(chemin_application(), discret) else "a_mettre_a_jour"


def appliquer(actif, discret):
    """Range (ou efface) la fiche de démarrage. Renvoie (True, message) ou (False, explication)."""
    chemin = chemin_fiche()
    if not actif:
        existait = os.path.exists(chemin)
        try:
            if existait:
                os.remove(chemin)
        except OSError as erreur:
            return False, f"Impossible d'effacer {chemin} : {erreur}"
        if SUR_MAC and existait:
            # déjà chargée par macOS depuis la dernière connexion : on la retire aussi (sans effet sinon)
            try:
                subprocess.run(["/bin/launchctl", "bootout", f"gui/{os.getuid()}/{ETIQUETTE}"], capture_output=True,
                               timeout=10, stdin=subprocess.DEVNULL)
            except (OSError, subprocess.SubprocessError):
                pass
        return True, "O.V.E.R. ne s'ouvrira plus à l'ouverture de ta session."
    application = chemin_application()
    if not os.path.isdir(application) or not os.path.isfile(programme_application(application)):
        return False, ("Je ne trouve pas OVER.app à côté de mes scripts : construis l'application (Xcode ▸ "
                       "Exécuter, ou bash app-mac/construire_app.sh), puis réessaie.")
    contenu = contenu_fiche(application, discret)
    if lire_fiche() == contenu:
        return True, message_actif(discret)
    temporaire = chemin + ".tmp"
    try:
        os.makedirs(os.path.dirname(chemin), exist_ok=True)
        with open(temporaire, "wb") as fichier:
            plistlib.dump(contenu, fichier)
        os.chmod(temporaire, 0o644)
        os.replace(temporaire, chemin)
    except OSError as erreur:
        try:
            os.remove(temporaire)
        except OSError:
            pass
        return False, f"Impossible d'écrire {chemin} : {erreur}"
    return True, message_actif(discret)


def message_actif(discret):
    if discret:
        return ("O.V.E.R. s'ouvrira à l'ouverture de ta session, sans fenêtre : son icône sera dans la barre des "
                "menus, en haut à droite de l'écran.")
    return "O.V.E.R. s'ouvrira à l'ouverture de ta session."
