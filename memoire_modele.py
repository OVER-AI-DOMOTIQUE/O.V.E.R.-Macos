# -*- coding: utf-8 -*-
"""
Mémoire du modèle sur le disque — pour un démarrage quasi instantané.

Avant de répondre, le modèle « lit » sa consigne et la conversation : sur un i5
2 cœurs, environ 25 tokens par seconde, soit 1 à 3 minutes au lancement. Ce qu'il
a lu (son cache de lecture, dit « KV cache ») est enregistré ici après chaque
réponse ; au lancement suivant, on le recharge en une seconde environ et il ne
lit plus que ce qui a changé.

Fichiers, à côté du cerveau :
  memoire_modele.bin   le cache de lecture (≈ 100 Ko par token lu : 150 à 400 Mo)
  memoire_modele.json  sa carte d'identité (modèle, taille de mémoire, version du moteur)

Vie privée : ce cache contient la consigne (donc le cerveau) et la conversation,
sous forme de tokens. Il est lisible par toi seul, et /effacer le supprime.
Si quoi que ce soit ne correspond pas (autre modèle, autre version du moteur,
fichier abîmé), il est ignoré et supprimé : le modèle relit tout, comme avant.
"""

import ctypes
import hashlib
import json
import os
import threading
import time

import llama_cpp

VERSION_FORMAT = 1
NOM_FICHIER = "memoire_modele.bin"
NOM_CARTE = "memoire_modele.json"


def chemins(dossier):
    return os.path.join(dossier, NOM_FICHIER), os.path.join(dossier, NOM_CARTE)


def carte_identite(chemin_modele, taille_contexte, taille_lot):
    """Tout ce qui doit être identique pour que le cache soit réutilisable."""
    try:
        infos = os.stat(chemin_modele)
        taille, date = infos.st_size, int(infos.st_mtime)
    except OSError:
        taille, date = 0, 0
    return {
        "format": VERSION_FORMAT,
        "modele": os.path.basename(chemin_modele),
        "taille_modele": taille,
        "date_modele": date,
        "taille_contexte": taille_contexte,
        "taille_lot": taille_lot,
        "moteur": getattr(llama_cpp, "__version__", "?"),
    }


def empreinte(jetons):
    return hashlib.sha1(",".join(map(str, jetons)).encode("ascii")).hexdigest()


def effacer(dossier):
    for chemin in chemins(dossier):
        try:
            os.remove(chemin)
        except OSError:
            pass


def recharger(modele, dossier, carte):
    """Recharge le cache dans le modèle. Renvoie le nombre de tokens retrouvés (0 si rien)."""
    chemin, chemin_carte = chemins(dossier)
    if not (os.path.exists(chemin) and os.path.exists(chemin_carte)):
        return 0
    try:
        with open(chemin_carte, "r", encoding="utf-8") as fichier:
            enregistree = json.load(fichier)
    except (OSError, ValueError):
        effacer(dossier)
        return 0
    if {cle: enregistree.get(cle) for cle in carte} != carte:
        effacer(dossier)   # autre modèle ou autre version du moteur : inutilisable
        return 0
    capacite = modele.n_ctx()
    tampon = (llama_cpp.llama_token * capacite)()
    nombre = ctypes.c_size_t(0)
    try:
        ok = llama_cpp.llama_state_load_file(modele._ctx.ctx, chemin.encode("utf-8"), tampon, capacite,
                                             ctypes.byref(nombre))
    except Exception:
        ok = False
    n = int(nombre.value)
    if not ok or n <= 0 or n > capacite or empreinte(tampon[:n]) != enregistree.get("empreinte"):
        effacer(dossier)
        modele.reset()
        return 0
    # le modèle croit avoir lu ces tokens : la prochaine réponse ne lira que la suite
    modele.input_ids[:n] = tampon[:n]
    modele.n_tokens = n
    modele._requires_eval = True   # les probabilités du dernier token seront recalculées
    return n


class Sauvegarde:
    """Enregistre le cache après chaque réponse, en arrière-plan, quand le modèle est libre."""

    DELAI = 2.0   # secondes d'attente : si un autre message arrive, il passe d'abord

    def __init__(self, modele, dossier, carte, verrou):
        self.modele = modele
        self.dossier = dossier
        self.carte = carte
        self.verrou = verrou
        self.derniere_empreinte = None
        self.demande = threading.Event()
        self.actif = True
        self.duree_derniere = 0.0
        self.fil = threading.Thread(target=self._boucle, name="memoire_modele", daemon=True)
        self.fil.start()

    def connaitre(self, n):
        """Le cache vient d'être rechargé : inutile de le réécrire tel quel."""
        self.derniere_empreinte = empreinte(self.modele.input_ids[:n].tolist()) if n else None

    def demander(self):
        if self.actif:
            self.demande.set()

    def oublier(self):
        """/effacer : le cache contient la conversation, on le supprime."""
        effacer(self.dossier)
        self.derniere_empreinte = None

    def _boucle(self):
        while True:
            self.demande.wait()
            time.sleep(self.DELAI)
            self.demande.clear()
            if not self.verrou.acquire(blocking=False):
                self.demande.set()   # le modèle travaille : on réessaiera après
                continue
            try:
                self.enregistrer_maintenant()
            finally:
                self.verrou.release()

    def enregistrer_maintenant(self):
        """À appeler avec le verrou du modèle. Écrit dans un fichier temporaire puis le renomme."""
        n = int(self.modele.n_tokens)
        if n <= 0:
            return False
        jetons = self.modele.input_ids[:n].tolist()
        signature = empreinte(jetons)
        if signature == self.derniere_empreinte:
            return False
        chemin, chemin_carte = chemins(self.dossier)
        temporaire = chemin + ".tmp"
        debut = time.perf_counter()
        tableau = (llama_cpp.llama_token * n)(*jetons)
        try:
            ok = llama_cpp.llama_state_save_file(self.modele._ctx.ctx, temporaire.encode("utf-8"), tableau, n)
        except Exception:
            ok = False
        if not ok:
            try:
                os.remove(temporaire)
            except OSError:
                pass
            return False
        try:
            os.chmod(temporaire, 0o600)
            carte = dict(self.carte, empreinte=signature, nb_tokens=n,
                         enregistre_le=time.strftime("%Y-%m-%dT%H:%M:%S"))
            temporaire_carte = chemin_carte + ".tmp"
            with open(temporaire_carte, "w", encoding="utf-8") as fichier:
                json.dump(carte, fichier, indent=1)
            os.chmod(temporaire_carte, 0o600)
            # la carte est remplacée en dernier : une carte à jour désigne toujours un cache complet
            os.replace(temporaire, chemin)
            os.replace(temporaire_carte, chemin_carte)
        except OSError:
            return False
        self.derniere_empreinte = signature
        self.duree_derniere = time.perf_counter() - debut
        return True
