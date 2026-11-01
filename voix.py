# -*- coding: utf-8 -*-
"""
Voix d'O.V.E.R. — il lit ses réponses à voix haute, presque en même temps qu'il les écrit.

La voix est celle de macOS : les voix françaises installées sur le Mac (Thomas, Amélie, et les
voix « améliorées » téléchargeables dans Réglages Système ▸ Accessibilité ▸ Contenu énoncé).
Elles sont gratuites, marchent sans Internet et n'ont pas de licence à gérer : O.V.E.R. ne
les distribue pas, il demande au Mac de parler. Interdit par Apple : enregistrer ce qu'elles
disent pour le publier ou le vendre ; O.V.E.R. ne garde aucun son.

Les sons sont fabriqués par la commande say de macOS (un fichier WAV par morceau de phrase),
puis joués par afplay, le lecteur de fichiers son de macOS.

Comment il parle tôt sans hacher :
  1. le premier morceau part dès les 3 premiers mots (ou la première virgule) ;
  2. pendant qu'un morceau est joué, le suivant est déjà fabriqué en fichier son :
     pas de temps mort entre les deux ;
  3. les morceaux suivants sont pris le plus tard possible (juste avant la fin du son
     en cours) : ils sont donc de plus en plus longs, et l'intonation plus naturelle.
     Le modèle écrit plus vite qu'on ne parle, la voix ne manque donc jamais de texte.
Réglage « voix_depart » : « immediat » (ci-dessus) ou « phrase » (attend chaque fin de
phrase : un peu plus tard, mais une intonation de phrase complète).

Ce qui est lu : les réponses, les phrases d'O.V.E.R. (bonjour, actions, briefing),
ses questions et la fin des minuteurs. Pas les sources, ni les chiffres de vitesse.

Pour le faire taire : un nouveau message, le bouton Arrêter, « chut » ou /tais-toi,
le bouton haut-parleur de l'application, Échap, ou Ctrl+C dans le Terminal.
"""

import atexit
import collections
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from parametres import forme_simple

SUR_MAC = sys.platform == "darwin"

VOIX_PRINCIPALE = "Thomas"   # voix française de macOS présente sur tous les Mac
VITESSE_NORMALE = 185        # valeur du réglage voix_vitesse qui correspond au débit naturel (mots par minute)
REGLAGES_VOIX = "x-apple.systempreferences:com.apple.preference.universalaccess?SpeakableItems"
OU_TROUVER_VOIX = ("Réglages Système ▸ Accessibilité ▸ Contenu énoncé ▸ Voix du système ▸ Gérer les voix… "
                   "(choisis Français, puis les voix « améliorées » ou « premium »)")
PAYS = {"FR": "France", "CA": "Canada", "BE": "Belgique", "CH": "Suisse"}

MOTS_PREMIER_MORCEAU = 3      # le premier morceau part dès 3 mots écrits
MARGE_AVANT_FIN = 0.9         # secondes : on prépare la suite quand le son en cours finit dans moins que ça
MARGE_URGENTE = 0.35          # en dessous, on prend ce qu'il y a (même un seul mot) pour ne pas laisser de blanc
MOTS_SUITE = 4                # sinon, on attend au moins 4 mots (ou une virgule, un point)
MOTS_MAX_EN_ATTENTE = 45      # au-delà, on prépare la suite sans attendre (évite un très long morceau)
SECONDES_PAR_MOT = 0.4        # durée moyenne d'un mot dit à vitesse normale (pour prévoir le temps de fabrication)


def nettoyer_pour_voix(texte):
    """Retire ce qui ne se lit pas : Markdown, adresses, émojis, symboles."""
    t = texte
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)                     # [texte](lien) → texte
    t = re.sub(r"https?://\S+|file://\S+", "", t)                       # adresses
    t = re.sub(r"`[^`]*`", lambda m: m.group(0).strip("`"), t)
    t = t.replace("O.V.E.R.", "Over").replace("O.V.E.R", "Over")
    t = re.sub(r"[*_#>|`~]+", " ", t)                                   # Markdown
    t = re.sub(r"^\s*[-•]\s+", "", t, flags=re.MULTILINE)               # puces
    t = re.sub(r"\s+·\s+", ", ", t)
    t = re.sub(r"[✓✔✗⚠⏰→←↑↓«»“”\"]", " ", t)
    t = re.sub("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]", "", t)     # émojis
    return re.sub(r"[ \t]+", " ", t).strip()


MOTIF_FIN_PHRASE = re.compile(r"(?<=[.!?…:])\s+|\n+")
MOTIF_FIN_PROPOSITION = re.compile(r"(?<=[,;])\s+")


def couper_en_phrases(texte):
    """Phrases complètes (finies par . ! ? … : ou un saut de ligne)."""
    morceaux = MOTIF_FIN_PHRASE.split(texte)
    return [m.strip() for m in morceaux if m.strip()]


def nombre_de_mots(texte):
    return len(texte.split())


def taille_lisible(octets):
    return f"{octets / 1e6:.0f} Mo"


# --- Les voix de macOS : la commande say ---------------------------------------------------------

# « Thomas              fr_FR    # Bonjour, je m'appelle Thomas. »
# « Amélie (Enhanced)   fr_CA    # … »   « Eddy (French (France)) fr_FR  # … »
MOTIF_LIGNE_VOIX = re.compile(r"^(?P<nom>.+?)\s+(?P<langue>[a-z]{2,3}[_-][A-Za-z0-9]{2,4})\s+#")


def decrire_voix(nom, langue):
    """Fiche d'une voix de macOS (id = le nom exact donné à say)."""
    court = re.sub(r"\s*\(.*$", "", nom).strip() or nom
    details = []
    pays = PAYS.get(langue[3:].upper())
    if pays:
        details.append(pays)
    if re.search(r"premium", nom, re.IGNORECASE):
        details.append("premium")
    elif re.search(r"enhanced|améliorée|amelioree", nom, re.IGNORECASE):
        details.append("améliorée")
    return {
        "id": nom,
        "nom": court,
        "libelle": court + (f" ({', '.join(details)})" if details else ""),
        "langue": langue,
        "licence": "voix de macOS (Apple)",
    }


class SyntheseMac:
    """Texte → fichier son, avec les voix françaises de macOS (commande say)."""

    commande = None          # programme de synthèse ; par défaut /usr/bin/say (remplaçable pour les essais)
    duree_catalogue = 60     # secondes : la liste des voix est relue au plus une fois par minute

    def __init__(self):
        self.verrou = threading.RLock()
        self.processus = None
        self.arrets = 0                          # compte les arrêts : une fabrication prévue avant est annulée
        self.message = ""                        # dernier évènement pour le panneau Paramètres
        self.erreur_installation = ""            # (rien à installer : gardé pour l'interface)
        self.installation_en_cours = False
        self.dossier = ""
        self._catalogue = (0.0, [])

    def programme(self):
        if self.commande:
            return list(self.commande)
        return ["/usr/bin/say"] if os.path.isfile("/usr/bin/say") else []

    # --- état ---

    def moteur_installe(self):
        return bool(self.programme()) and (SUR_MAC or self.commande is not None)

    def installation_possible(self):
        return False   # les voix viennent de macOS : rien à installer

    def disponible(self):
        return self.moteur_installe() and bool(self.voix_installees())

    def etat(self):
        """prete | indisponible"""
        return "prete" if self.disponible() else "indisponible"

    # --- catalogue ---

    def voix_installees(self):
        """Voix françaises du Mac (Thomas d'abord, puis la France, puis les autres pays)."""
        moment, fiches = self._catalogue
        if fiches and time.monotonic() - moment < self.duree_catalogue:
            return [dict(f) for f in fiches]
        if not self.moteur_installe():
            return []
        try:
            resultat = subprocess.run(self.programme() + ["-v", "?"], capture_output=True, text=True,
                                      timeout=20, stdin=subprocess.DEVNULL)
            sortie = resultat.stdout if resultat.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            sortie = ""
        fiches, vus = [], set()
        for ligne in sortie.splitlines():
            trouve = MOTIF_LIGNE_VOIX.match(ligne.strip())
            if not trouve:
                continue
            nom, langue = trouve.group("nom").strip(), trouve.group("langue").replace("-", "_")
            if not langue.lower().startswith("fr") or nom in vus:
                continue
            vus.add(nom)
            fiches.append(decrire_voix(nom, langue))
        fiches.sort(key=lambda f: (f["id"] != VOIX_PRINCIPALE, not f["langue"].upper().endswith("FR"),
                                   "premium" not in f["libelle"] and "améliorée" not in f["libelle"],
                                   f["libelle"].lower()))
        self._catalogue = (time.monotonic(), fiches)
        return [dict(f) for f in fiches]

    def trouver(self, ident):
        return next((f for f in self.voix_installees() if f["id"] == ident), None)

    def chercher(self, texte):
        """« thomas », « Thomas », « amélie »… → fiche de la voix, ou None."""
        cle = forme_simple(texte)
        if not cle:
            return None
        fiches = self.voix_installees()
        for fiche in fiches:
            if cle in (forme_simple(fiche["id"]), forme_simple(fiche["nom"]), forme_simple(fiche["libelle"])):
                return fiche
        return next((f for f in fiches if cle in forme_simple(f["id"])), None)

    # --- fabrication ---

    def fabriquer(self, texte, fichier, ident, lenteur=1.0, encore_voulu=None):
        """Écrit le fichier son (WAV) ; renvoie sa durée en secondes, ou None si ça n'a pas marché.
        encore_voulu : fonction appelée juste avant de lancer say (False = abandon)."""
        if not ident or not self.moteur_installe():
            return None
        debit = int(round(VITESSE_NORMALE / max(0.2, float(lenteur))))
        commande = self.programme() + ["-v", ident, "-r", str(debit), "-o", fichier, "--file-format=WAVE",
                                       "--data-format=LEI16@22050", "-f", "-"]
        with self.verrou:
            if encore_voulu and not encore_voulu():
                return None
            arrets = self.arrets
            try:
                processus = subprocess.Popen(commande, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                             stderr=subprocess.DEVNULL)
            except OSError as erreur:
                self.message = f"Impossible de lancer la synthèse vocale : {erreur}"
                return None
            self.processus = processus
        try:
            # le texte passe par l'entrée standard : jamais pris pour une option de say
            processus.communicate(texte.encode("utf-8"), timeout=60)
        except subprocess.TimeoutExpired:
            processus.kill()
            processus.communicate()
            return None
        finally:
            with self.verrou:
                if self.processus is processus:
                    self.processus = None
        if processus.returncode != 0 or arrets != self.arrets:
            return None
        return duree_wav(fichier)

    def prechauffer(self, ident):
        """Les voix de macOS se chargent en un instant : rien à préparer."""

    def arreter(self):
        """Arrête une fabrication en cours (voix coupée, fin du programme)."""
        with self.verrou:
            self.arrets += 1
            processus = self.processus
        if processus and processus.poll() is None:
            try:
                processus.kill()
            except OSError:
                pass


def empreinte(chemin):
    calcul = hashlib.sha256()
    with open(chemin, "rb") as fichier:
        for bloc in iter(lambda: fichier.read(1 << 20), b""):
            calcul.update(bloc)
    return calcul.hexdigest()


def telecharger_fichier(adresse, chemin, reprendre=False):
    """Télécharge avec curl (toujours présent sur macOS), sinon avec Python. Renvoie (ok, explication).
    Seules les adresses http(s) sont acceptées, et les redirections ne peuvent mener qu'à du https."""
    if not str(adresse).lower().startswith(("https://", "http://")):
        return False, "adresse de téléchargement refusée"
    curl = "/usr/bin/curl" if os.path.isfile("/usr/bin/curl") else shutil.which("curl")
    if curl:
        commande = [curl, "-L", "--proto", "=https,http", "--proto-redir", "=https", "--fail", "--silent",
                    "--show-error", "--retry", "3", "--retry-delay", "3", "--connect-timeout", "20", "-o", chemin]
        if reprendre:
            commande += ["-C", "-"]
        try:
            resultat = subprocess.run(commande + [adresse], capture_output=True, text=True, timeout=3600,
                                      stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as erreur:
            return False, str(erreur)
        if resultat.returncode == 0:
            return True, ""
        return False, (resultat.stderr.strip().splitlines() or [f"code {resultat.returncode}"])[-1][:200]
    import urllib.request
    try:
        from web import CONTEXTE_SSL
    except Exception:
        CONTEXTE_SSL = None
    try:
        with urllib.request.urlopen(adresse, timeout=60, context=CONTEXTE_SSL) as reponse, \
                open(chemin, "wb") as fichier:
            shutil.copyfileobj(reponse, fichier, 1 << 20)
        return True, ""
    except Exception as erreur:
        return False, str(erreur)[:200]


def duree_wav(fichier):
    """Durée d'un fichier WAV en secondes (None si illisible)."""
    import wave
    try:
        with wave.open(fichier, "rb") as son:
            return son.getnframes() / float(son.getframerate() or 1)
    except (OSError, EOFError, wave.Error):
        return None


# --- La voix ---------------------------------------------------------------------------------

class Voix:
    # lecteur de fichiers son de macOS, par son chemin complet (remplaçable pour les essais)
    commande_lecture = "/usr/bin/afplay" if os.path.isfile("/usr/bin/afplay") else "afplay"

    def __init__(self, parametres, signaler=lambda en_cours: None, synthese=None):
        self.parametres = parametres
        self.signaler = signaler          # prévient l'interface : True quand il parle, False quand il se tait
        self.synthese = synthese or SyntheseMac()
        self.condition = threading.Condition()
        self.generation = 0               # change à chaque « tais-toi » : ce qui était prévu avant est jeté
        self.tampon = ""                  # texte du modèle pas encore confié à la synthèse
        self.flux_ouvert = False          # le modèle est en train d'écrire
        self.premier = True               # le prochain morceau est le début d'une réponse
        self.textes = collections.deque() # textes complets à dire (phrases d'O.V.E.R., questions…)
        self.sons = collections.deque()   # (génération, fichier, durée, texte) prêts à jouer
        self.rendu_en_cours = False
        self.lecture_en_cours = False
        self.fin_prevue = 0.0             # instant (time.monotonic) où le son en cours se termine
        self.rapport_rendu = 0.25         # temps de fabrication / durée du son (mesuré : dépend du Mac)
        self.processus = {"lecture": None}
        self.en_cours = False
        self.compteur = 0
        self.dossier = tempfile.mkdtemp(prefix="over-voix-")
        atexit.register(self._nettoyer_en_partant)
        for cible, nom in ((self._boucle_rendu, "voix-rendu"), (self._boucle_lecture, "voix-lecture")):
            threading.Thread(target=cible, name=nom, daemon=True).start()

    # --- disponibilité ---

    def disponible(self):
        return self.synthese.disponible()

    def active(self):
        return bool(self.parametres["voix_active"]) and self.disponible()

    def voix_installees(self):
        return self.synthese.voix_installees()

    def voix_choisie(self):
        """Identifiant de la voix utilisée : celle choisie, sinon la principale (Thomas), sinon la première."""
        installees = [fiche["id"] for fiche in self.voix_installees()]
        demandee = self.parametres["voix_nom"]
        if demandee in installees:
            return demandee
        if VOIX_PRINCIPALE in installees:
            return VOIX_PRINCIPALE
        return installees[0] if installees else None

    def nom_voix_choisie(self):
        fiche = self.synthese.trouver(self.voix_choisie() or "")
        return fiche["nom"] if fiche else "aucune"

    def lenteur(self):
        """Réglage voix_vitesse (185 = débit naturel de la voix) → durée relative des sons."""
        return VITESSE_NORMALE / float(max(60, self.parametres["voix_vitesse"]))

    def parle(self):
        return self.en_cours

    def preparer(self):
        """Charge la voix d'avance (au lancement, quand on l'active ou qu'on en change)."""
        if self.active():
            self.synthese.prechauffer(self.voix_choisie())

    def arreter_moteur(self):
        """Voix coupée : la fabrication en cours s'arrête."""
        self.synthese.arreter()

    # --- ce qu'on lui donne à dire ---

    def dire(self, texte):
        """Lit un texte complet (phrase d'O.V.E.R., question, briefing…)."""
        if not self.active():
            return
        with self.condition:
            for phrase in couper_en_phrases(nettoyer_pour_voix(texte)):
                self.textes.append((self.generation, phrase))
            self.condition.notify_all()

    def flux(self, morceau):
        """Réponse du modèle au fil de l'eau."""
        if not self.active():
            return
        with self.condition:
            self.tampon += morceau
            self.flux_ouvert = True
            self.condition.notify_all()

    def fin_flux(self):
        """Le modèle a fini d'écrire : le reste du texte part tel quel."""
        with self.condition:
            self.flux_ouvert = False
            self.condition.notify_all()

    def taire(self):
        """Se tait tout de suite et oublie ce qui restait à dire."""
        with self.condition:
            self.generation += 1
            self.tampon = ""
            self.flux_ouvert = False
            self.premier = True
            self.textes.clear()
            jetes = list(self.sons)
            self.sons.clear()
            processus = list(self.processus.values())
            self.condition.notify_all()
        for _, fichier, _, _ in jetes:
            self._supprimer(fichier)
        for un in processus:
            if un and un.poll() is None:
                try:
                    un.terminate()
                except OSError:
                    pass

    def essayer(self):
        """Phrase de test (paramètres)."""
        self.taire()
        if self.disponible():
            with self.condition:
                self.textes.append((self.generation, f"Bonjour, je suis Over. Voici la voix {self.nom_voix_choisie()}."))
                self.condition.notify_all()

    # --- découpage : quel morceau envoyer à la synthèse, et quand ---

    def _reste_a_jouer(self):
        """Secondes de son déjà prêtes : la fin du son en cours, plus les sons fabriqués d'avance."""
        reste = max(0.0, self.fin_prevue - time.monotonic()) if self.lecture_en_cours else 0.0
        return reste + sum(duree for _, _, duree, _ in self.sons)

    def _point_de_coupe(self, texte):
        """Position où couper le texte reçu, ou None pour attendre encore un peu."""
        fins_phrase = [m.end() for m in MOTIF_FIN_PHRASE.finditer(texte)]
        if self.parametres["voix_depart"] == "phrase":
            return fins_phrase[-1] if fins_phrase else None
        fins = sorted(set(fins_phrase + [m.end() for m in MOTIF_FIN_PROPOSITION.finditer(texte)]))
        dernier_blanc = max(texte.rfind(" "), texte.rfind("\n"), texte.rfind("\t"))
        mots_complets = nombre_de_mots(texte[:dernier_blanc + 1]) if dernier_blanc >= 0 else 0
        if self.premier:
            for fin in fins:   # une virgule ou un point tôt dans la réponse : coupure naturelle
                if nombre_de_mots(texte[:fin]) >= 2:
                    return fin
            return dernier_blanc + 1 if mots_complets >= MOTS_PREMIER_MORCEAU else None
        # la suite : le plus tard possible, juste avant que la voix n'ait plus rien à dire ; sur un Mac
        # lent, la marge grandit avec le temps qu'il faudra pour fabriquer ce morceau
        reste = self._reste_a_jouer()
        fabrication = self.rapport_rendu * mots_complets * SECONDES_PAR_MOT * self.lenteur()
        if reste > max(MARGE_AVANT_FIN, 0.3 + fabrication) and mots_complets < MOTS_MAX_EN_ATTENTE:
            return None
        if fins and nombre_de_mots(texte[:fins[-1]]) >= 2:
            return fins[-1]                      # coupure naturelle : virgule ou fin de phrase
        if mots_complets >= MOTS_SUITE or (reste < MARGE_URGENTE and mots_complets >= 1):
            return dernier_blanc + 1
        return None

    def _prendre_morceau(self):
        """À appeler avec la condition tenue. Renvoie (génération, texte) ou None."""
        if self.textes:
            return self.textes.popleft()
        texte = self.tampon
        if not texte.strip():
            if not self.flux_ouvert:
                self.premier = True
            return None
        if not self.flux_ouvert:     # réponse terminée : tout le reste d'un coup
            self.tampon, self.premier = "", True
            return self.generation, texte
        coupe = self._point_de_coupe(texte)
        if not coupe:
            return None
        self.tampon, self.premier = texte[coupe:], False
        return self.generation, texte[:coupe]

    # --- les deux fils : fabriquer les sons, et les jouer ---

    def _boucle_rendu(self):
        while True:
            with self.condition:
                morceau = self._prendre_morceau()
                while morceau is None:
                    self.condition.wait(timeout=0.1)
                    morceau = self._prendre_morceau()
                generation, texte = morceau
                self.rendu_en_cours = True
            try:
                self._fabriquer(generation, nettoyer_pour_voix(texte))
            except Exception:
                pass   # un morceau raté ne doit jamais arrêter la voix
            finally:
                with self.condition:
                    self.rendu_en_cours = False
                    self.condition.notify_all()

    def _fabriquer(self, generation, texte):
        if not texte or not re.search(r"\w", texte):
            return
        ident = self.voix_choisie()
        self.compteur += 1
        fichier = os.path.join(self.dossier, f"morceau-{self.compteur}.wav")
        debut = time.monotonic()
        duree = self.synthese.fabriquer(texte, fichier, ident, self.lenteur(),
                                        encore_voulu=lambda: generation == self.generation) if ident else None
        if duree:
            mesure = (time.monotonic() - debut) / duree
            self.rapport_rendu = min(3.0, max(0.02, 0.6 * self.rapport_rendu + 0.4 * mesure))
        with self.condition:
            if duree and generation == self.generation:
                self.sons.append((generation, fichier, duree, texte))
                self.condition.notify_all()
                return
        self._supprimer(fichier)   # « tais-toi » entre-temps, ou morceau raté : on passe au suivant

    def _boucle_lecture(self):
        while True:
            with self.condition:
                while not self.sons:
                    occupe = self.rendu_en_cours or self.textes or (self.flux_ouvert and not self.premier)
                    if not occupe:
                        self.condition.release()
                        try:
                            self._changer_etat(False)
                        finally:
                            self.condition.acquire()
                    self.condition.wait(timeout=0.4)
                generation, fichier, duree, texte = self.sons.popleft()
                if generation != self.generation:
                    self._supprimer(fichier)
                    continue
                self.lecture_en_cours = True
                self.fin_prevue = time.monotonic() + duree
            self._changer_etat(True)
            try:
                self._jouer(generation, fichier)
            finally:
                self._supprimer(fichier)
                with self.condition:
                    self.lecture_en_cours = False
                    self.condition.notify_all()

    def _jouer(self, generation, fichier):
        try:
            with self.condition:
                if generation != self.generation or not shutil.which(self.commande_lecture):
                    return
                processus = subprocess.Popen([self.commande_lecture, fichier], stdin=subprocess.DEVNULL,
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.processus["lecture"] = processus
            processus.wait(timeout=300)
        except (OSError, subprocess.SubprocessError):
            pass
        finally:
            with self.condition:
                self.processus["lecture"] = None

    def _nettoyer_en_partant(self):
        self.synthese.arreter()
        shutil.rmtree(self.dossier, True)

    def fermer(self):
        """Fin du programme : se taire, arrêter la synthèse, effacer les sons."""
        self.taire()
        self._nettoyer_en_partant()

    # --- outils ---

    def _changer_etat(self, en_cours):
        if en_cours != self.en_cours:
            self.en_cours = en_cours
            try:
                self.signaler(en_cours)
            except Exception:
                pass

    @staticmethod
    def _supprimer(fichier):
        if fichier:
            try:
                os.remove(fichier)
            except OSError:
                pass


if __name__ == "__main__":
    # liste des voix françaises du Mac : python voix.py (code de sortie 1 s'il n'y en a aucune)
    trouvees = SyntheseMac().voix_installees()
    for fiche in trouvees:
        print(f"{fiche['libelle']:40} {fiche['langue']}")
    sys.exit(0 if trouvees else 1)
