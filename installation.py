# -*- coding: utf-8 -*-
"""
Installation du modèle de langage quand il manque (premier lancement de l'application).

L'application OVER.app contient Python et les scripts, mais pas le modèle Ministral 3 3B
(2,1 Go, Mistral AI, licence Apache 2.0) : il est téléchargé une seule fois depuis Hugging Face,
puis vérifié (taille et empreinte SHA-256) avant d'être rangé avec tes données :
~/Library/Application Support/O.V.E.R./modeles/  (version emballée)
ou le dossier modeles/ du dossier O.V.E.R. (version installée avec installer.sh).

Un téléchargement interrompu (bouton Pause, application fermée, connexion coupée) reprend là où
il s'était arrêté.

Si tu as déjà le fichier du modèle, il peut être repris à la place : même vérification. Dans
l'application (bac à sable de l'App Store), c'est elle qui copie le fichier choisi dans le dossier
import/ de tes données ; le moteur le vérifie puis le déplace (rien n'est copié deux fois). Sur un
disque APFS, une copie est un « clone » : instantanée, et sans place en plus.

Le serveur (serveur.py) s'en sert avant d'ouvrir la session ; l'application affiche la progression.
"""

import ctypes
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import time

import reglages

SUR_MAC = sys.platform == "darwin"
MARGE_DISQUE = 300_000_000      # place gardée libre en plus du modèle (le Mac a besoin d'air)
TAILLE_BLOC = 4 << 20
INTERVALLE_PROGRESSION = 0.4    # secondes entre deux nouvelles de la progression
PARENT = os.getppid()           # l'application : si elle disparaît, le téléchargement s'arrête aussi
ESSAI_HTTP = False              # essais seulement : accepte un petit serveur http local

_processus = {"telechargement": None}


class Modele:
    """Le fichier à installer. Les essais remplacent adresse, taille et empreinte."""

    def __init__(self, chemin=None, adresse=None, taille=None, empreinte=None):
        self.chemin = chemin or reglages.CHEMIN_MODELE
        self.adresse = adresse or reglages.ADRESSE_MODELE
        self.taille = taille or reglages.TAILLE_MODELE
        self.empreinte = empreinte or reglages.EMPREINTE_MODELE

    @property
    def partiel(self):
        return self.chemin + ".part"

    @property
    def dossier(self):
        return os.path.dirname(self.chemin)

    def present(self):
        return os.path.isfile(self.chemin)

    def deja_recu(self):
        try:
            return os.path.getsize(self.partiel)
        except OSError:
            return 0


# --- état, pour l'application --------------------------------------------------------------

def espace_libre(dossier):
    """Place libre sur le disque qui contiendra le dossier (0 si inconnue)."""
    while dossier and not os.path.isdir(dossier):
        parent = os.path.dirname(dossier)
        if parent == dossier:
            break
        dossier = parent
    try:
        return shutil.disk_usage(dossier or os.sep).free
    except OSError:
        return 0


def place_manquante(modele, a_ecrire=None):
    """Octets qui manquent sur le disque pour écrire ce qui reste (0 si ça tient)."""
    reste = (modele.taille - modele.deja_recu()) if a_ecrire is None else a_ecrire
    return max(0, reste + MARGE_DISQUE - espace_libre(modele.dossier))


# --- le processeur ------------------------------------------------------------------------------

def lire_sysctl(nom):
    """Valeur (entier) d'un réglage du système macOS, comme « sysctl -n nom » ; None s'il n'existe pas."""
    if not SUR_MAC:
        return None
    try:
        systeme = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        valeur = ctypes.c_int64(0)
        taille = ctypes.c_size_t(ctypes.sizeof(valeur))
        if systeme.sysctlbyname(nom.encode("ascii"), ctypes.byref(valeur), ctypes.byref(taille), None,
                                ctypes.c_size_t(0)) != 0:
            return None
    except (OSError, AttributeError):
        return None
    return int(valeur.value)


def version_macos():
    """(15, 1) pour macOS 15.1 ; (0,) si inconnue."""
    try:
        return tuple(int(x) for x in platform.mac_ver()[0].split(".") if x) or (0,)
    except ValueError:
        return (0,)


def explication_processeur(lire=lire_sysctl, version=None):
    """Le moteur (llama.cpp) est compilé pour les Mac Intel récents (instructions AVX2). Renvoie "" si ce Mac
    convient, sinon l'explication à afficher, au lieu d'un plantage au chargement du modèle :
      - Mac à puce Apple (l'app tourne avec Rosetta) : Rosetta ne sait faire tourner AVX2 que depuis macOS 15 ;
      - Mac Intel d'avant 2013 : pas d'AVX2."""
    if lire("sysctl.proc_translated") == 1:
        if (version or version_macos()) >= (15,):
            return ""
        return ("Sur un Mac à puce Apple (M1, M2…), O.V.E.R. 0.1 a besoin de macOS 15 Sequoia ou plus récent : "
                "avant, Rosetta ne sait pas faire tourner son moteur. Mets macOS à jour (Réglages Système ▸ "
                "Général ▸ Mise à jour de logiciels), puis relance O.V.E.R.")
    if lire("hw.optional.avx2_0") == 0:
        return ("Ce Mac est trop ancien pour le moteur d'O.V.E.R. : il faut un processeur Intel de 2013 ou plus "
                "récent (instructions AVX2).")
    return ""   # Mac Intel récent, ou rien de connu : on laisse le moteur démarrer


def dossier_import():
    """Où l'application dépose une copie du modèle choisi par toi (bac à sable : le moteur ne lit pas ailleurs)."""
    return os.path.join(reglages.DOSSIER_DONNEES, "import")


def etat(modele=None):
    """Ce que l'application affiche : taille, déjà reçu, place libre."""
    modele = modele or Modele()
    return {
        "taille": modele.taille,
        "recu": min(modele.deja_recu(), modele.taille),
        "libre": espace_libre(modele.dossier),
        "manque": place_manquante(modele),
        "dossier": modele.dossier,
    }


def lisible(octets):
    """2147023008 → « 2,1 Go » ; 63511038 → « 64 Mo »."""
    if octets >= 1e9:
        return f"{octets / 1e9:.1f} Go".replace(".", ",")
    return f"{max(1, round(octets / 1e6))} Mo"


# --- vérification et copie -------------------------------------------------------------------

def _surveiller():
    """L'application a disparu sans prévenir : on arrête tout plutôt que de télécharger pour personne."""
    if os.getppid() != PARENT:
        arreter_tout()
        os._exit(0)


def calculer_empreinte(chemin, progression=None, calcul=None):
    """SHA-256 du fichier, avec la progression (fait, total)."""
    calcul = calcul or hashlib.sha256()
    total = os.path.getsize(chemin)
    fait, dernier = 0, 0.0
    with open(chemin, "rb") as fichier:
        for bloc in iter(lambda: fichier.read(TAILLE_BLOC), b""):
            calcul.update(bloc)
            fait += len(bloc)
            if progression and time.monotonic() - dernier >= INTERVALLE_PROGRESSION:
                dernier = time.monotonic()
                _surveiller()
                progression(fait, total)
    if progression:
        progression(total, total)
    return calcul.hexdigest()


def cloner(source, cible):
    """Copie instantanée sur un disque APFS (cp -c) : les deux fichiers partagent la place tant que
    personne ne les modifie. Renvoie False si le disque ne sait pas faire (autre disque, autre format)."""
    if not SUR_MAC:
        return False
    try:
        resultat = subprocess.run(["/bin/cp", "-c", source, cible], capture_output=True, timeout=120,
                                  stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return False
    if resultat.returncode != 0:
        try:
            os.remove(cible)
        except OSError:
            pass
        return False
    return True


def copier_et_verifier(source, cible, empreinte_attendue, progression=None):
    """Copie source → cible (clone si possible), en vérifiant l'empreinte de ce qui est écrit.
    Renvoie (True, "") ou (False, explication) ; en cas d'échec, la cible n'existe pas."""
    partiel = cible + ".part"
    try:
        os.makedirs(os.path.dirname(cible), exist_ok=True)
        if os.path.exists(partiel):
            os.remove(partiel)
        if cloner(source, partiel):
            obtenue = calculer_empreinte(partiel, progression and (lambda f, t: progression("verification", f, t)))
        else:
            calcul = hashlib.sha256()
            total = os.path.getsize(source)
            fait, dernier = 0, 0.0
            with open(source, "rb") as entree, open(partiel, "wb") as sortie:
                for bloc in iter(lambda: entree.read(TAILLE_BLOC), b""):
                    sortie.write(bloc)
                    calcul.update(bloc)
                    fait += len(bloc)
                    if progression and time.monotonic() - dernier >= INTERVALLE_PROGRESSION:
                        dernier = time.monotonic()
                        _surveiller()
                        progression("copie", fait, total)
            obtenue = calcul.hexdigest()
        if obtenue != empreinte_attendue:
            os.remove(partiel)
            return False, "ce fichier n'est pas celui attendu (empreinte différente)"
        os.replace(partiel, cible)
        return True, ""
    except BaseException as erreur:
        try:
            os.remove(partiel)
        except OSError:
            pass
        if isinstance(erreur, (KeyboardInterrupt, SystemExit)):
            raise
        if isinstance(erreur, OSError):
            return False, f"copie impossible ({erreur.strerror or erreur})"
        raise


# --- installation du modèle ------------------------------------------------------------------

def arreter_tout():
    """Arrête un téléchargement en cours (l'application se ferme). Le morceau reçu est gardé."""
    processus = _processus["telechargement"]
    if processus and processus.poll() is None:
        processus.terminate()
        try:
            processus.wait(5)
        except subprocess.TimeoutExpired:
            processus.kill()


def telecharger(progression=lambda phase, fait, total: None, modele=None):
    """Télécharge le modèle (reprend un téléchargement interrompu), le vérifie et le range.
    Renvoie (True, "") ou (False, explication). Le bouton Pause lève KeyboardInterrupt : le morceau
    déjà reçu est gardé pour la fois suivante."""
    modele = modele or Modele()
    if not str(modele.adresse).startswith("https://" if not ESSAI_HTTP else ("https://", "http://")):
        return False, "adresse de téléchargement refusée"
    try:
        os.makedirs(modele.dossier, exist_ok=True)
    except OSError as erreur:
        return False, f"impossible de créer le dossier {modele.dossier} ({erreur.strerror or erreur})"
    if modele.deja_recu() > modele.taille:
        os.remove(modele.partiel)      # plus gros que l'original : rien à en tirer
    manque = place_manquante(modele)
    if manque:
        return False, (f"il manque {lisible(manque)} sur le disque (le modèle pèse {lisible(modele.taille)}) : "
                       "fais de la place, puis réessaie")
    if modele.deja_recu() < modele.taille:
        curl = "/usr/bin/curl" if os.path.isfile("/usr/bin/curl") else shutil.which("curl")
        if not curl:
            return False, "curl introuvable"
        protocoles = "=https,http" if ESSAI_HTTP else "=https"   # jamais de http, même après une redirection
        commande = [curl, "-L", "--proto", protocoles, "--proto-redir", protocoles, "--fail", "--silent", "--show-error", "--retry", "5", "--retry-delay", "3",
                    "--connect-timeout", "20", "--speed-limit", "2000", "--speed-time", "60",
                    "-C", "-", "-o", modele.partiel, modele.adresse]
        try:
            processus = subprocess.Popen(commande, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.PIPE, text=True)
        except OSError as erreur:
            return False, f"téléchargement impossible ({erreur})"
        _processus["telechargement"] = processus
        try:
            while processus.poll() is None:
                _surveiller()
                progression("telechargement", min(modele.deja_recu(), modele.taille), modele.taille)
                try:
                    processus.wait(INTERVALLE_PROGRESSION)
                except subprocess.TimeoutExpired:
                    pass
        except BaseException:
            arreter_tout()   # Pause ou fermeture : curl s'arrête, le morceau reçu reste
            raise
        finally:
            _processus["telechargement"] = None
        erreurs = (processus.stderr.read() or "").strip().splitlines() if processus.stderr else []
        if processus.returncode != 0:
            detail = erreurs[-1][:200] if erreurs else f"code {processus.returncode}"
            return False, f"téléchargement interrompu ({detail}) ; réessaie, il reprendra là où il s'est arrêté"
        progression("telechargement", min(modele.deja_recu(), modele.taille), modele.taille)
    recu = modele.deja_recu()
    if recu != modele.taille:
        if recu > modele.taille:
            os.remove(modele.partiel)
        return False, (f"fichier reçu incomplet ({lisible(recu)} sur {lisible(modele.taille)}) ; réessaie, "
                       "il reprendra là où il s'est arrêté")
    obtenue = calculer_empreinte(modele.partiel, lambda fait, total: progression("verification", fait, total))
    if obtenue != modele.empreinte:
        os.remove(modele.partiel)    # 2 Go qui ne servent à rien : effacés
        return False, "le fichier reçu ne correspond pas à l'original (il a été effacé) ; réessaie"
    os.replace(modele.partiel, modele.chemin)
    return True, ""


def importer(source, progression=lambda phase, fait, total: None, modele=None):
    """Reprend un fichier du modèle déjà téléchargé : vérification, puis copie (ou déplacement, pour la
    copie que l'application a déposée dans import/). Renvoie (True, "") ou (False, explication)."""
    modele = modele or Modele()
    if not isinstance(source, str) or not source:
        return False, "aucun fichier indiqué"
    source = os.path.abspath(os.path.expanduser(source))
    if not os.path.isfile(source):
        return False, f"« {os.path.basename(source)} » est introuvable"
    if os.path.realpath(source) == os.path.realpath(modele.chemin):
        return True, ""
    depose = os.path.dirname(os.path.realpath(source)) == os.path.realpath(dossier_import())
    if os.path.getsize(source) != modele.taille:
        if depose:
            _effacer(source)   # notre copie : elle ne sert à rien
        return False, (f"« {os.path.basename(source)} » n'est pas le modèle attendu ({lisible(modele.taille)}, "
                       f"Ministral 3 3B Q4_K_M)")
    if depose:
        return deplacer_et_verifier(source, modele, progression)
    try:
        meme_disque = os.stat(source).st_dev == os.stat(modele.dossier if os.path.isdir(modele.dossier)
                                                         else reglages.DOSSIER_DONNEES).st_dev
    except OSError:
        meme_disque = False
    if not (SUR_MAC and meme_disque):
        manque = place_manquante(modele, modele.taille)
        if manque:
            return False, f"il manque {lisible(manque)} sur le disque pour copier le modèle : fais de la place"
    ok, detail = copier_et_verifier(source, modele.chemin, modele.empreinte, progression)
    if not ok:
        return False, f"« {os.path.basename(source)} » : {detail}"
    return True, ""


def deplacer_et_verifier(source, modele, progression=None):
    """La copie déposée par l'application : vérifiée, puis rangée à sa place (même disque : instantané)."""
    obtenue = calculer_empreinte(source, progression and (lambda f, t: progression("verification", f, t)))
    if obtenue != modele.empreinte:
        _effacer(source)
        return False, "ce fichier n'est pas celui attendu (empreinte différente) ; la copie a été effacée"
    try:
        os.makedirs(modele.dossier, exist_ok=True)
        os.replace(source, modele.chemin)
    except OSError as erreur:
        return False, f"rangement impossible ({erreur.strerror or erreur})"
    return True, ""


def _effacer(chemin):
    try:
        os.remove(chemin)
    except OSError:
        pass
