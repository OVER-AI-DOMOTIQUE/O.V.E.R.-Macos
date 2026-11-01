# -*- coding: utf-8 -*-
"""
Écoute d'O.V.E.R. — ce que tu dis devient du texte, sur ton Mac (whisper.cpp, 100 % local).

L'application enregistre ta voix au micro (fichier WAV 16 kHz), puis ce module la transcrit
avec whisper.cpp. Rien ne part chez Apple ni ailleurs.

  - whisper.cpp (licence MIT) est compilé une seule fois sur le Mac, pour son processeur,
    comme le moteur du modèle de langage. Le code est pris à une version précise
    (git, empreinte du commit vérifiée), puis seul le programme whisper-cli est gardé.
  - Les modèles Whisper d'OpenAI (licence MIT), convertis par le projet whisper.cpp, sont
    téléchargés une fois et vérifiés (taille et empreinte SHA-256).

Deux modèles au choix (paramètre ecoute_modele) :
  precis  « small » (190 Mo) : bien meilleur en français ; quelques secondes pour une phrase
  rapide  « base » (60 Mo)   : deux à trois fois plus rapide, plus d'erreurs

Pour aller vite sur un vieux Mac : le contexte audio est réduit à la durée de la phrase
(whisper travaille d'habitude sur 30 s de son, même pour 3 s de parole), décodage glouton,
pas de nouvel essai à température plus haute.

Rangement : dossier ecoute/ du dossier O.V.E.R. (whisper-cli, modèles, licence, journal
de compilation).
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave

import reglages
from voix import empreinte, taille_lisible, telecharger_fichier

SUR_MAC = sys.platform == "darwin"

DOSSIER_PROJET = os.path.dirname(os.path.abspath(__file__))
DOSSIER_ECOUTE = os.path.join(reglages.DOSSIER_DONNEES, "ecoute")   # avec tes données (voir reglages.py)
# Application emballée : whisper-cli est déjà dans l'application (compilé et signé avec elle)
PROGRAMME_FOURNI = os.environ.get("OVER_WHISPER_CLI", "")

VERSION_WHISPER = "1.9.5"
DEPOT_WHISPER = "https://github.com/ggml-org/whisper.cpp.git"
FICHIER_DEPENDANCES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dependances", "ecoute.txt")
COMMIT_WHISPER = "d1be6fde11ac6e0407606b4e42fe72d34add8037"   # étiquette v1.9.5 : le code reçu doit être celui-là

ADRESSE_MODELES = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"
MODELES = {
    "precis": {"fichier": "ggml-small-q5_1.bin", "taille": 190085487, "nom": "précis",
               "empreinte": "ae85e4a935d7a567bd102fe55afc16bb595bdb618e11b2fc7591bc08120411bb"},
    "rapide": {"fichier": "ggml-base-q5_1.bin", "taille": 59707625, "nom": "rapide",
               "empreinte": "422f1ae452ade6f30a004d7e5c6a43195e4433bc370bf23fac9cc591f01a8898"},
}
MODELE_PAR_DEFAUT = "precis"

FILS = 2                  # fils de calcul : les deux cœurs (le modèle de langage attend pendant que tu parles)
CONTEXTE_MIN = 512        # contexte audio minimal (≈ 10 s) : en dessous, la qualité baisse
DUREE_MAX = 60            # secondes de son au plus par transcription
DELAI_TRANSCRIPTION = 180

# Phrases que Whisper invente sur du silence ou du bruit (il a appris sur des vidéos sous-titrées)
HALLUCINATIONS = re.compile(
    r"sous-?titr\w*\s+(réalis|par\b|st\b|st'|société|fait)|amara\.org|merci d'avoir regardé|"
    r"merci de votre attention\W*$|abonnez-vous|"
    r"^\W*(musique|applaudissements|rires|silence)\W*$|^\W*(…|\.\.\.)?\W*$",
    flags=re.IGNORECASE)
BALISES = re.compile(r"\[[^\]]*\]|\([^)]*(musique|bruit|rires|applaudissements)[^)]*\)|\*[^*]*\*|♪+",
                     flags=re.IGNORECASE)


def duree_wav(chemin):
    """Durée d'un fichier WAV en secondes (None si illisible)."""
    try:
        with wave.open(chemin, "rb") as son:
            return son.getnframes() / float(son.getframerate() or 1)
    except (OSError, EOFError, wave.Error):
        return None


def contexte_audio(duree):
    """Taille du contexte audio pour whisper (1500 = 30 s) : juste ce qu'il faut pour la phrase.
    0 = tout (son long)."""
    if duree >= 28:
        return 0
    besoin = int((duree + 1.0) * 50)            # 50 positions par seconde de son
    return min(1500, max(CONTEXTE_MIN, (besoin + 63) // 64 * 64))


def nettoyer_transcription(texte):
    """Retire ce que Whisper invente (sous-titres, [Musique]…, phrase répétée en boucle) et remet le
    texte sur une ligne."""
    lignes = []
    for ligne in texte.splitlines():
        ligne = BALISES.sub(" ", ligne)
        ligne = " ".join(ligne.split())
        if ligne and not HALLUCINATIONS.search(ligne):
            lignes.append(ligne)
    phrases = []
    for phrase in re.split(r"(?<=[.!?…])\s+", " ".join(lignes)):
        if phrase and (not phrases or phrase.lower() != phrases[-1].lower()):
            phrases.append(phrase)   # une phrase répétée à la suite (Whisper qui boucle) n'est gardée qu'une fois
    return " ".join(phrases).strip()


def derniere_ligne_utile(sortie):
    lignes = [ligne.strip() for ligne in (sortie or "").splitlines() if ligne.strip()]
    erreurs = [ligne for ligne in lignes if "error" in ligne.lower() or "erreur" in ligne.lower()]
    return ((erreurs or lignes or [""])[-1])[:300]


def options_compilation(portable=False, machine=None):
    """Options CMake de whisper.cpp.
    portable=False : GGML_NATIVE, le code utilise tout ce que CE processeur sait faire.
    portable=True  : pour l'application à distribuer, la base commune des Mac Intel sous macOS 12 et plus
                     (AVX2, FMA, F16C : tous sauf le Mac Pro 2013) ; un Mac plus ancien ne lancerait pas
                     whisper-cli (instruction inconnue), d'où le contrôle dans l'application."""
    options = [
        "-DCMAKE_BUILD_TYPE=Release",
        "-DBUILD_SHARED_LIBS=OFF",            # un seul programme, sans bibliothèque à côté
        "-DWHISPER_BUILD_TESTS=OFF",
        "-DWHISPER_BUILD_EXAMPLES=ON",        # whisper-cli en fait partie
        "-DWHISPER_BUILD_SERVER=OFF",
        "-DWHISPER_SDL2=OFF",
        "-DWHISPER_CURL=OFF",
        "-DGGML_METAL=OFF",                   # Mac Intel : le GPU intégré ralentirait
        "-DGGML_OPENMP=OFF",
        "-DGGML_CCACHE=OFF",
    ]
    machine = machine or os.uname().machine
    if not portable:
        options.append("-DGGML_NATIVE=ON")    # pour CE processeur (AVX2 sur un i5 2017)
    elif machine in ("x86_64", "amd64", "AMD64"):
        options += ["-DGGML_NATIVE=OFF", "-DGGML_AVX=ON", "-DGGML_AVX2=ON", "-DGGML_FMA=ON", "-DGGML_F16C=ON"]
    else:
        options.append("-DGGML_NATIVE=OFF")
    if SUR_MAC:
        options.append("-DCMAKE_OSX_DEPLOYMENT_TARGET=12.0")
    return options


class MoteurEcoute:
    """whisper.cpp : installation (compilation + modèle) et transcription."""

    commande = None             # programme de transcription ; par défaut ecoute/whisper-cli (remplaçable pour les essais)
    commande_pip = None         # installation de CMake ; par défaut « python -m pip install cmake » (idem)
    installation_permise = None # None : seulement sur un Mac ; True pour les essais
    depot = DEPOT_WHISPER
    commit = COMMIT_WHISPER
    modeles = MODELES
    adresse_modeles = ADRESSE_MODELES

    def __init__(self, dossier=None):
        self.dossier = dossier or DOSSIER_ECOUTE
        self.installation_en_cours = False
        self.erreur_installation = ""
        self.message = ""                     # dernier évènement, pour le panneau Paramètres
        self.verrou_installation = threading.Lock()
        self.verrou = threading.Lock()        # une transcription à la fois
        self.processus_apercu = None          # whisper-cli d'un aperçu en cours (arrêté si la finale arrive)
        self.apercu_arrete = False

    # --- état ---

    def chemin_programme(self):
        """whisper-cli : celui de l'application emballée, sinon celui compilé dans le dossier ecoute/."""
        if PROGRAMME_FOURNI and os.access(PROGRAMME_FOURNI, os.X_OK):
            return PROGRAMME_FOURNI
        return os.path.join(self.dossier, "whisper-cli")

    def programme(self):
        return list(self.commande) if self.commande else [self.chemin_programme()]

    def moteur_installe(self):
        if self.commande:
            return True
        return os.access(self.chemin_programme(), os.X_OK)

    def chemin_modele(self, choix):
        return os.path.join(self.dossier, self.modeles[self.choix_valide(choix)]["fichier"])

    def choix_valide(self, choix):
        return choix if choix in self.modeles else MODELE_PAR_DEFAUT

    def modele_installe(self, choix):
        chemin = self.chemin_modele(choix)
        info = self.modeles[self.choix_valide(choix)]
        return os.path.isfile(chemin) and os.path.getsize(chemin) == info["taille"]

    def disponible(self, choix):
        return self.moteur_installe() and self.modele_installe(choix)

    def installation_possible(self):
        return SUR_MAC if self.installation_permise is None else bool(self.installation_permise)

    def etat(self, choix):
        """prete | installation | erreur | a_installer"""
        if self.installation_en_cours:
            return "installation"
        if self.disponible(choix):
            return "prete"
        return "erreur" if self.erreur_installation else "a_installer"

    def a_telecharger(self, choix):
        """Ce qu'il reste à installer : (liste lisible, taille approximative en Mo)."""
        morceaux, taille = [], 0
        if not self.moteur_installe():
            morceaux.append("whisper.cpp (compilé sur ce Mac)")
            taille += 10
        if not self.modele_installe(choix):
            info = self.modeles[self.choix_valide(choix)]
            morceaux.append(f"le modèle {info['nom']}")
            taille += info["taille"] // 1_000_000
        return morceaux, taille

    # --- installation ---

    def installer(self, choix, rapporter=lambda texte: None, fini=lambda: None):
        """En arrière-plan : compile whisper.cpp s'il manque, puis télécharge le modèle choisi.
        Renvoie False si une installation est déjà en cours."""
        with self.verrou_installation:
            if self.installation_en_cours:
                return False
            self.installation_en_cours = True
        self.erreur_installation = ""

        def travailler():
            try:
                self._installer(self.choix_valide(choix), rapporter)
            except Exception as erreur:   # jamais d'exception perdue dans un fil
                self.erreur_installation = f"{type(erreur).__name__} : {erreur}"
            finally:
                self.installation_en_cours = False
                self.message = ("Installation impossible : " + self.erreur_installation) if self.erreur_installation \
                    else "Écoute locale installée."
            fini()

        threading.Thread(target=travailler, name="ecoute-installation", daemon=True).start()
        return True

    def installer_en_direct(self, choix, rapporter=print):
        """Même chose, en attendant la fin (installer.sh). Renvoie True si tout est prêt."""
        self.installation_en_cours = True
        self.erreur_installation = ""
        try:
            self._installer(self.choix_valide(choix), rapporter)
        except Exception as erreur:
            self.erreur_installation = f"{type(erreur).__name__} : {erreur}"
        finally:
            self.installation_en_cours = False
        if self.erreur_installation:
            rapporter("Échec : " + self.erreur_installation)
            return False
        return self.disponible(choix)

    def _installer(self, choix, rapporter):
        os.makedirs(self.dossier, exist_ok=True)
        if not self.moteur_installe() and reglages.EMBALLE:
            # application emballée : on ne compile rien sur le Mac, whisper-cli doit être dans l'application
            self.erreur_installation = "whisper.cpp n'est pas inclus dans cette version de l'application"
            return
        if not self.moteur_installe():
            self.message = "Compilation de whisper.cpp…"
            ok, detail = self._compiler(rapporter)
            if not ok:
                self.erreur_installation = "whisper.cpp n'a pas pu être compilé (" + (detail or "raison inconnue") + ")"
                return
        if not self.modele_installe(choix):
            info = self.modeles[choix]
            self.message = f"Téléchargement du modèle d'écoute {info['nom']}…"
            rapporter(f"Téléchargement du modèle d'écoute {info['nom']} ({taille_lisible(info['taille'])})…")
            ok, detail = self.telecharger(choix)
            if not ok:
                self.erreur_installation = f"le modèle {info['nom']} n'a pas pu être téléchargé ({detail})"

    def _executer(self, commande, journal, delai, dossier=None):
        """Lance une étape de la compilation ; sa sortie va dans le journal. Renvoie (ok, détail)."""
        journal.write("\n$ " + " ".join(commande) + "\n")
        journal.flush()
        try:
            resultat = subprocess.run(commande, cwd=dossier, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT, text=True, timeout=delai)
        except subprocess.TimeoutExpired:
            return False, f"trop long (plus de {delai // 60} min)"
        except OSError as erreur:
            return False, str(erreur)
        journal.write(resultat.stdout or "")
        journal.flush()
        if resultat.returncode != 0:
            return False, derniere_ligne_utile(resultat.stdout) or f"code {resultat.returncode}"
        return True, resultat.stdout or ""

    def _trouver_cmake(self, rapporter, journal):
        """CMake (outil de compilation) : celui du Mac, sinon installé dans .venv par pip."""
        candidats = [os.path.join(os.path.dirname(sys.executable), "cmake"), shutil.which("cmake")]
        for candidat in candidats:
            if candidat and os.access(candidat, os.X_OK):
                return candidat, ""
        rapporter("Installation de CMake (outil de compilation, ≈ 30 Mo)…")
        # version et empreintes figées (dependances/ecoute.txt) : pip refuse tout autre fichier
        commande = list(self.commande_pip or [sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
                                              "--quiet", "--require-hashes", "-r", FICHIER_DEPENDANCES])
        ok, detail = self._executer(commande, journal, 900)
        if not ok:
            return None, "CMake ne s'est pas installé : " + detail
        for candidat in [os.path.join(os.path.dirname(sys.executable), "cmake"), shutil.which("cmake")]:
            if candidat and os.access(candidat, os.X_OK):
                return candidat, ""
        return None, "CMake introuvable après son installation"

    def _compiler(self, rapporter, portable=False):
        """Télécharge whisper.cpp (version fixée), le compile et garde whisper-cli.
        portable=False : pour CE processeur ; True : pour tous les Mac Intel récents (application à distribuer)."""
        git = shutil.which("git")
        if not git:
            return False, "git introuvable : installe les outils de développement Apple (xcode-select --install)"
        os.makedirs(self.dossier, exist_ok=True)
        travail = os.path.join(self.dossier, "compilation")
        source = os.path.join(travail, "whisper.cpp")
        construction = os.path.join(travail, "build")
        with open(os.path.join(self.dossier, "compilation.log"), "w", encoding="utf-8") as journal:
            cmake, detail = self._trouver_cmake(rapporter, journal)
            if not cmake:
                return False, detail
            shutil.rmtree(travail, True)
            os.makedirs(travail, exist_ok=True)
            rapporter(f"Téléchargement de whisper.cpp {VERSION_WHISPER} (code source, ≈ 25 Mo)…")
            ok, detail = self._executer([git, "-c", "advice.detachedHead=false", "clone", "--quiet", "--depth", "1",
                                         "--branch", f"v{VERSION_WHISPER}", self.depot, source], journal, 900)
            if not ok:
                return False, "téléchargement du code impossible : " + detail
            ok, commit = self._executer([git, "-C", source, "rev-parse", "HEAD"], journal, 60)
            if not ok or commit.strip() != self.commit:
                shutil.rmtree(travail, True)
                return False, "le code reçu ne correspond pas à la version attendue"
            rapporter("Compilation de whisper.cpp " + ("pour les Mac Intel" if portable else "pour ce processeur")
                      + " (5 à 15 minutes, une seule fois ; le ventilateur va souffler)…")
            options = options_compilation(portable)
            ok, detail = self._executer([cmake, "-S", source, "-B", construction, *options], journal, 600)
            if not ok:
                return False, "préparation de la compilation : " + detail
            coeurs = max(1, min(4, os.cpu_count() or 2))
            ok, detail = self._executer([cmake, "--build", construction, "--config", "Release", "--target",
                                         "whisper-cli", "-j", str(coeurs)], journal, 3600)
            if not ok:
                return False, "compilation : " + detail
            construit = os.path.join(construction, "bin", "whisper-cli")
            if not os.path.isfile(construit):
                return False, "whisper-cli introuvable après la compilation"
            ok, detail = self._executer([construit, "--help"], journal, 60)
            if not ok:
                return False, "whisper-cli ne démarre pas : " + detail
            cible = os.path.join(self.dossier, "whisper-cli")
            shutil.copyfile(construit, cible + ".part")
            os.chmod(cible + ".part", 0o755)
            os.replace(cible + ".part", cible)
            licence = os.path.join(source, "LICENSE")
            if os.path.isfile(licence):
                shutil.copyfile(licence, os.path.join(self.dossier, "LICENCE-whisper.cpp.txt"))
        shutil.rmtree(travail, True)   # le code et les fichiers de compilation (≈ 150 Mo) ne servent plus
        return True, ""

    def telecharger(self, choix):
        """Télécharge un modèle dans le dossier ecoute/ et le vérifie. Renvoie (True, "") ou (False, explication)."""
        info = self.modeles[choix]
        cible = os.path.join(self.dossier, info["fichier"])
        partiel = cible + ".part"
        os.makedirs(self.dossier, exist_ok=True)
        deja = os.path.getsize(partiel) if os.path.exists(partiel) else 0
        if deja < info["taille"]:
            ok, detail = telecharger_fichier(self.adresse_modeles + info["fichier"], partiel, reprendre=deja > 0)
            if not ok:
                return False, detail   # le morceau reçu est gardé : la prochaine fois reprend là
        if os.path.getsize(partiel) != info["taille"] or empreinte(partiel) != info["empreinte"]:
            os.replace(partiel, cible + ".corrompu")
            return False, "le fichier reçu ne correspond pas à l'original (mis de côté en .corrompu)"
        os.replace(partiel, cible)
        return True, ""

    # --- transcription ---

    def arreter_apercu(self):
        """Arrête net une transcription d'aperçu en cours (la version finale passe avant)."""
        processus = self.processus_apercu
        if processus and processus.poll() is None:
            self.apercu_arrete = True
            try:
                processus.kill()
            except OSError:
                pass

    def transcrire(self, fichier, choix, langue="fr", apercu=False):
        """Transcrit un fichier WAV. Renvoie {"ok", "texte", "duree" (calcul), "duree_son", "erreur"}.

        apercu=True : texte provisoire, pendant qu'on parle encore. Il ne passe que si whisper.cpp
        est libre (sinon erreur « occupe ») et il est arrêté net (erreur « annule ») dès qu'une
        transcription finale arrive : l'aperçu ne retarde jamais le vrai texte."""
        choix = self.choix_valide(choix)
        resultat = {"ok": False, "texte": "", "duree": 0.0, "duree_son": 0.0, "erreur": ""}
        if not self.disponible(choix):
            resultat["erreur"] = "a_installer"
            return resultat
        duree_son = duree_wav(fichier)
        if not duree_son:
            resultat["erreur"] = "son illisible"
            return resultat
        resultat["duree_son"] = round(duree_son, 2)
        if duree_son < 0.3:
            resultat.update(ok=True)   # trop court pour contenir une phrase
            return resultat
        if not apercu:
            self.arreter_apercu()
        if not self.verrou.acquire(blocking=not apercu):
            resultat["erreur"] = "occupe"
            return resultat
        contexte = contexte_audio(duree_son)
        sortie = tempfile.mkdtemp(prefix="over-ecoute-")
        base = os.path.join(sortie, "texte")
        commande = self.programme() + [
            "-m", self.chemin_modele(choix), "-f", fichier, "-l", langue, "-t", str(FILS),
            "-nt", "-np", "-nf", "-bs", "1", "-bo", "1", "-sns",
            "-d", str(int(min(duree_son, DUREE_MAX) * 1000)),
            "-otxt", "-of", base,
        ]
        if contexte:
            commande += ["-ac", str(contexte)]
        debut = time.monotonic()
        try:
            processus = subprocess.Popen(commande, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, text=True)
            if apercu:
                self.apercu_arrete = False
                self.processus_apercu = processus
            try:
                sortie_standard, erreurs = processus.communicate(timeout=DELAI_TRANSCRIPTION)
            except subprocess.TimeoutExpired:
                processus.kill()
                processus.communicate()
                resultat["erreur"] = "transcription trop longue"
                return resultat
            finally:
                if apercu:
                    self.processus_apercu = None
            if processus.returncode != 0:
                if apercu and self.apercu_arrete:
                    resultat["erreur"] = "annule"   # arrêté pour laisser passer la version finale
                elif processus.returncode < 0:
                    resultat["erreur"] = (f"whisper.cpp s'est arrêté brutalement (signal {-processus.returncode}) : "
                                          "recompile-le avec /écoute installer après avoir effacé ecoute/whisper-cli")
                else:
                    resultat["erreur"] = "whisper.cpp : " + (derniere_ligne_utile(erreurs)
                                                             or f"code {processus.returncode}")
                return resultat
            try:
                with open(base + ".txt", "r", encoding="utf-8", errors="replace") as fichier_texte:
                    brut = fichier_texte.read()
            except OSError:
                brut = sortie_standard or ""
            resultat.update(ok=True, texte=nettoyer_transcription(brut))
        except OSError as erreur:
            resultat["erreur"] = str(erreur)
        finally:
            self.verrou.release()
            resultat["duree"] = round(time.monotonic() - debut, 2)
            shutil.rmtree(sortie, True)
        return resultat


if __name__ == "__main__":
    # bash installer.sh : .venv/bin/python ecoute.py --installer
    if sys.argv[1:] == ["--installer"]:
        moteur = MoteurEcoute()
        if moteur.disponible(MODELE_PAR_DEFAUT):
            print("  whisper.cpp et le modèle d'écoute sont déjà installés.")
            sys.exit(0)
        sys.exit(0 if moteur.installer_en_direct(MODELE_PAR_DEFAUT, lambda texte: print("  " + texte, flush=True))
                 else 1)
    if len(sys.argv) == 2 and sys.argv[1].lower().endswith(".wav"):
        print(MoteurEcoute().transcrire(sys.argv[1], MODELE_PAR_DEFAUT))
        sys.exit(0)
    print("Usage : python ecoute.py --installer   (compile whisper.cpp et télécharge le modèle précis)\n"
          "        python ecoute.py son.wav       (transcrit un fichier)")
