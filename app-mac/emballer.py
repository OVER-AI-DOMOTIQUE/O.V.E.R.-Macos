# -*- coding: utf-8 -*-
"""
Emballage d'O.V.E.R. : prépare tout ce que l'application doit contenir pour fonctionner seule,
sans Terminal ni installation (lancé par emballer.sh).

  app-mac/build/Ressources/
    python/          Python 3.12 (python-build-standalone, licence PSF), allégé, avec les paquets
                     de dependances/ (llama-cpp-python compilé ici, numpy, certifi…)
    over/            les scripts d'O.V.E.R. (liste fixe : ni over_chat.py, ni installer.sh, ni les essais,
                     et jamais tes données : cerveau, paramètres, conversation, modèle)
    bin/whisper-cli  l'écoute (whisper.cpp)
    licences/        les licences de tout ce qui est inclus
    emballage.json   versions, tailles et empreintes

Ensuite, la compilation Release (Xcode ⇧⌘I, ou bash construire_app.sh) range ce dossier dans
OVER.app/Contents/Resources et signe chaque programme (embarquer.sh).

Tout est compilé pour les Mac Intel sous macOS 12 ou plus récent (AVX2, FMA, F16C), pas pour
« ce processeur » : l'application marche sur un autre Mac que celui qui l'a construite.
Le modèle de langage (2,1 Go) n'est pas dans l'application : elle le télécharge au premier lancement.

Ce qui est téléchargé ou compilé est gardé dans app-mac/build/cache : relancer va vite.
Sur Linux, le même script prépare un Python d'essai (validation des paquets et des essais).
"""

import argparse
import datetime
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time

ICI = os.path.dirname(os.path.abspath(__file__))
PROJET = os.path.dirname(ICI)
CONSTRUCTION = os.path.join(ICI, "build")
CACHE = os.path.join(CONSTRUCTION, "cache")
RESSOURCES = os.path.join(CONSTRUCTION, "Ressources")
SUR_MAC = sys.platform == "darwin"

# --- Python embarqué : python-build-standalone (astral-sh), version et empreintes figées -----------
VERSION_PYTHON = "3.12.15"
PUBLICATION_PYTHON = "20261009"
ADRESSE_PYTHON = "https://github.com/astral-sh/python-build-standalone/releases/download/" + PUBLICATION_PYTHON + "/"
PYTHONS = {
    ("darwin", "x86_64"): ("cpython-3.12.15+20261009-x86_64-apple-darwin-install_only.tar.gz", 24868017,
                           "4c84ef5abe4bc145646175fce9b881936d60126b9a3f9a24c6308e2d035ca5c6"),
    # Linux : seulement pour les essais (validation des paquets et des scripts dans ce Python)
    ("linux", "x86_64"): ("cpython-3.12.15+20261009-x86_64-unknown-linux-gnu-install_only.tar.gz", 66913335,
                          "5864001bf969db329fadc2b654c4878c6027d170c6bd77e28f88666260bcaf63"),
}
VERSION_COURTE = "3.12"

# --- les scripts d'O.V.E.R. qui vont dans l'application (liste fixe) ------------------------------
SCRIPTS = ["actions.py", "balises.py", "briefing.py", "cerveau.py", "demarrage.py", "ecoute.py", "identite.py",
           "installation.py", "memoire_modele.py", "noyau.py", "parametres.py", "reglages.py", "serveur.py",
           "voix.py", "web.py"]
SCRIPTS_EXCLUS = {"over_chat.py"}   # le chat dans le Terminal : pas dans l'application

# Compilation de llama.cpp : la base commune des Mac Intel depuis 2013 (comme whisper.cpp).
# PIÈGE : sur Mac Intel, le CMakeLists.txt de llama-cpp-python FORCE GGML_AVX, GGML_AVX2, GGML_FMA et
# GGML_F16C à OFF (et Metal à ON), quoi qu'on lui passe en -D. Avec GGML_NATIVE=OFF, le moteur sortait
# alors en x86-64 de base (SSE seulement, aucune instruction AVX2) : 0,8 token/s sur un i5 2017.
# Les instructions passent donc directement au compilateur (CFLAGS / CXXFLAGS), que ce CMakeLists ne
# touche pas ; le code de ggml les détecte (__AVX2__, __FMA__, __F16C__). Ni BMI2 ni AVX-512 : ils ne
# sont pas sur tous les Mac visés. La vérification de l'emballage refuse un moteur sans AVX2, FMA, F16C.
OPTIONS_LLAMA = ["-DGGML_NATIVE=OFF", "-DLLAMA_CURL=OFF"]
INSTRUCTIONS_LLAMA = "-mavx -mavx2 -mfma -mf16c"
CIBLE_MACOS = "12.0"

# Ce que l'application n'utilise pas (gagne de la place ; rien de tout ça n'est chargé par O.V.E.R.)
INUTILES_BIBLIOTHEQUE = ["idlelib", "tkinter", "turtledemo", "ensurepip", "lib2to3", "pydoc_data", "test",
                         "turtle.py", "__phello__", "venv"]
INUTILES_PAQUETS = ["pip", "setuptools", "pkg_resources", "_distutils_hack", "wheel",
                    "distutils-precedence.pth", "README.txt", "share"]

journal_debut = time.monotonic()


def etape(texte):
    print(f"\n\033[38;5;37m▸ {texte}\033[0m", flush=True)


def ok(texte):
    print(f"  \033[38;5;179m✓\033[0m {texte}", flush=True)


def note(texte):
    print(f"  {texte}", flush=True)


def echec(texte):
    print(f"\n\033[38;5;197m✗ {texte}\033[0m\n", file=sys.stderr, flush=True)
    raise SystemExit(1)


def executer(commande, **options):
    """Lance une commande ; sa sortie s'affiche (et va dans le journal). Échec = arrêt."""
    note("$ " + " ".join(str(morceau) for morceau in commande))
    resultat = subprocess.run(commande, stdin=subprocess.DEVNULL, **options)
    if resultat.returncode != 0:
        echec(f"La commande a échoué (code {resultat.returncode}). Le détail est juste au-dessus "
              "(et dans app-mac/build/emballage.log).")
    return resultat


def empreinte(chemin):
    calcul = hashlib.sha256()
    with open(chemin, "rb") as fichier:
        for bloc in iter(lambda: fichier.read(1 << 20), b""):
            calcul.update(bloc)
    return calcul.hexdigest()


def taille_dossier(dossier):
    total = 0
    for racine, _, fichiers in os.walk(dossier):
        for nom in fichiers:
            chemin = os.path.join(racine, nom)
            if not os.path.islink(chemin):
                total += os.path.getsize(chemin)
    return total


def mo(octets):
    return f"{octets / 1e6:.0f} Mo"


def effacer(chemin):
    if os.path.islink(chemin) or os.path.isfile(chemin):
        os.remove(chemin)
    elif os.path.isdir(chemin):
        shutil.rmtree(chemin)


# --- 1. Python ---------------------------------------------------------------------------------

def machine():
    nom = platform.machine().lower()
    return {"amd64": "x86_64", "aarch64": "arm64"}.get(nom, nom)


def preparer_python():
    cle = (sys.platform if sys.platform != "linux2" else "linux", machine())
    if cle not in PYTHONS:
        if cle == ("darwin", "arm64"):
            echec("Mac Apple Silicon : l'emballage pour ces Mac n'est pas encore prêt (il faut une version arm64 "
                  "du moteur, compilée et essayée sur un Mac M1/M2…). Lance-le depuis un Mac Intel.")
        echec(f"Système non prévu : {cle}.")
    fichier, taille, attendue = PYTHONS[cle]
    archive = os.path.join(CACHE, fichier)
    os.makedirs(CACHE, exist_ok=True)
    if not (os.path.isfile(archive) and os.path.getsize(archive) == taille and empreinte(archive) == attendue):
        note(f"Téléchargement de Python {VERSION_PYTHON} ({mo(taille)})…")
        partiel = archive + ".part"
        executer(["curl", "-L", "--fail", "--proto", "=https", "--proto-redir", "=https", "--retry", "3",
                  "--silent", "--show-error", "-o", partiel, ADRESSE_PYTHON + fichier.replace("+", "%2B")])
        if os.path.getsize(partiel) != taille or empreinte(partiel) != attendue:
            os.remove(partiel)
            echec("Le Python téléchargé ne correspond pas à l'original (empreinte différente) : effacé.")
        os.replace(partiel, archive)
    ok(f"Python {VERSION_PYTHON} vérifié (SHA-256 {attendue[:12]}…)")
    effacer(RESSOURCES)
    os.makedirs(RESSOURCES)
    executer(["tar", "-xzf", archive, "-C", RESSOURCES])
    python = os.path.join(RESSOURCES, "python", "bin", "python" + VERSION_COURTE)
    if not os.access(python, os.X_OK):
        echec("Archive Python inattendue (python3.12 introuvable).")
    return python


# --- 2. Paquets --------------------------------------------------------------------------------

def lire_exigences(chemin):
    """Fichier de dependances/ → {nom: (version, [empreintes])} (format pip « nom==version \\ --hash=… »)."""
    exigences, courant = {}, None
    with open(chemin, encoding="utf-8") as fichier:
        for ligne in fichier:
            ligne = ligne.split("#", 1)[0].strip().rstrip("\\").strip()
            if not ligne:
                continue
            if ligne.startswith("--hash="):
                if courant:
                    exigences[courant][1].append(ligne[len("--hash="):])
                continue
            nom, _, version = ligne.partition("==")
            courant = nom.strip().lower().replace("_", "-")
            exigences.setdefault(courant, (version.strip(), []))
    return exigences


def ecrire_exigences(exigences, chemin):
    with open(chemin, "w", encoding="utf-8") as fichier:
        for nom, (version, empreintes) in sorted(exigences.items()):
            fichier.write(f"{nom}=={version}" + "".join(f" \\\n    --hash={e}" for e in sorted(set(empreintes))) + "\n")


def installer_paquets(python):
    environnement = dict(os.environ, PIP_DISABLE_PIP_VERSION_CHECK="1", PYTHONNOUSERSITE="1",
                         CMAKE_BUILD_PARALLEL_LEVEL=str(max(1, os.cpu_count() or 2)))
    for cle in [cle for cle in environnement if cle.startswith("PYTHON") and cle != "PYTHONNOUSERSITE"]:
        del environnement[cle]
    options = list(OPTIONS_LLAMA)
    if SUR_MAC:
        options.append("-DCMAKE_OSX_DEPLOYMENT_TARGET=" + CIBLE_MACOS)
        environnement["MACOSX_DEPLOYMENT_TARGET"] = CIBLE_MACOS
    environnement["CMAKE_ARGS"] = " ".join(options)
    environnement["CFLAGS"] = environnement["CXXFLAGS"] = INSTRUCTIONS_LLAMA   # voir OPTIONS_LLAMA
    # cache à part, propre à ces options : un moteur compilé autrement n'y est jamais repris
    signature = hashlib.sha256((" ".join(options) + " | " + INSTRUCTIONS_LLAMA).encode()).hexdigest()[:10]
    cache_pip = os.path.join(CACHE, "pip-" + signature)
    dependances = os.path.join(PROJET, "dependances")
    paquets = os.path.join(RESSOURCES, "python", "lib", "python" + VERSION_COURTE, "site-packages")

    # 1. Tous les paquets déjà compilés (numpy, Jinja2…), choisis pour macOS 12 : sur un Mac
    #    plus récent, pip prendrait sinon les versions faites pour CE macOS (numpy pour macOS 14…), qui
    #    ne s'ouvriraient pas chez quelqu'un resté sur macOS 12 ou 13.
    exigences = {}
    for nom_fichier in ["moteur.txt", "outils.txt"]:
        for nom, (version, empreintes) in lire_exigences(os.path.join(dependances, nom_fichier)).items():
            if nom in exigences and exigences[nom][0] != version:
                echec(f"{nom} : deux versions différentes dans dependances/ ({exigences[nom][0]} et {version}).")
            exigences.setdefault(nom, (version, []))[1].extend(empreintes)
    for inutile in ("pip", "setuptools"):   # outils de construction : pas dans l'application
        exigences.pop(inutile, None)
    moteur = {"llama-cpp-python": exigences.pop("llama-cpp-python")}
    binaires, source = os.path.join(CONSTRUCTION, "paquets-binaires.txt"), os.path.join(CONSTRUCTION, "moteur-source.txt")
    ecrire_exigences(exigences, binaires)
    ecrire_exigences(moteur, source)
    plateforme = []
    if SUR_MAC:
        plateforme = ["--platform", "macosx_" + CIBLE_MACOS.replace(".", "_") + "_x86_64", "--implementation", "cp",
                      "--python-version", VERSION_COURTE, "--abi", "cp" + VERSION_COURTE.replace(".", "")]
    executer([python, "-I", "-m", "pip", "install", "--require-hashes", "--cache-dir", cache_pip,
              "--only-binary=:all:", "--no-deps", "--target", paquets, *plateforme, "-r", binaires], env=environnement)
    ok(f"{len(exigences)} paquets déjà compilés, pour macOS {CIBLE_MACOS} et plus")

    # 2. Le moteur llama-cpp-python, compilé ici depuis son code source (empreinte vérifiée)
    note("llama-cpp-python est compilé pour les Mac Intel récents : 10 à 25 minutes la première fois "
         "(le ventilateur va souffler), quelques secondes ensuite.")
    executer([python, "-I", "-m", "pip", "install", "--require-hashes", "--cache-dir", cache_pip, "--no-deps",
              "--no-binary", "llama-cpp-python", "-r", source], env=environnement)
    ok("moteur de langage (llama-cpp-python)")


# --- 3. Écoute ---------------------------------------------------------------------------------

def preparer_ecoute():
    sys.path.insert(0, PROJET)
    import ecoute
    signature = hashlib.sha256(" ".join(ecoute.options_compilation(True)).encode()).hexdigest()[:10]
    dossier = os.path.join(CACHE, f"whisper-{ecoute.VERSION_WHISPER}-{signature}")
    programme = os.path.join(dossier, "whisper-cli")
    if not os.access(programme, os.X_OK):
        note("whisper.cpp est compilé pour les Mac Intel récents : 5 à 15 minutes la première fois.")
        moteur = ecoute.MoteurEcoute(dossier=dossier)
        reussi, detail = moteur._compiler(note, portable=True)
        if not reussi:
            echec("whisper.cpp n'a pas pu être compilé : " + detail + f" (journal : {dossier}/compilation.log)")
    os.makedirs(os.path.join(RESSOURCES, "bin"), exist_ok=True)
    shutil.copy2(programme, os.path.join(RESSOURCES, "bin", "whisper-cli"))
    licence = os.path.join(dossier, "LICENCE-whisper.cpp.txt")
    ok(f"écoute (whisper.cpp {ecoute.VERSION_WHISPER})")
    return licence if os.path.isfile(licence) else None


# --- 4. Scripts --------------------------------------------------------------------------------

def copier_scripts():
    presents = sorted(nom for nom in os.listdir(PROJET) if nom.endswith(".py"))
    oublies = [nom for nom in presents if nom not in SCRIPTS and nom not in SCRIPTS_EXCLUS]
    if oublies:
        echec("Nouveau(x) script(s) pas encore classé(s) : " + ", ".join(oublies)
              + ". Ajoute-les à SCRIPTS (ou SCRIPTS_EXCLUS) dans app-mac/emballer.py.")
    manquants = [nom for nom in SCRIPTS if nom not in presents]
    if manquants:
        echec("Script(s) introuvable(s) : " + ", ".join(manquants))
    cible = os.path.join(RESSOURCES, "over")
    os.makedirs(cible)
    for nom in SCRIPTS:
        shutil.copyfile(os.path.join(PROJET, nom), os.path.join(cible, nom))
        os.chmod(os.path.join(cible, nom), 0o644)
    ok(f"{len(SCRIPTS)} scripts d'O.V.E.R.")


# --- 5. Allègement -----------------------------------------------------------------------------

def binaire_lie_a_libpython(binaire):
    if SUR_MAC:
        sortie = subprocess.run(["otool", "-L", binaire], capture_output=True, text=True).stdout
    else:
        sortie = subprocess.run(["readelf", "-d", binaire], capture_output=True, text=True).stdout
    return "libpython" in sortie


def alleger():
    racine = os.path.join(RESSOURCES, "python")
    bin_ = os.path.join(racine, "bin")
    reel = os.path.join(bin_, "python" + VERSION_COURTE)
    # un seul programme, sous un nom fixe : python3 (l'application lance Resources/python/bin/python3)
    os.replace(reel, os.path.join(CONSTRUCTION, "python3.tmp"))
    effacer(bin_)
    os.makedirs(bin_)
    os.replace(os.path.join(CONSTRUCTION, "python3.tmp"), os.path.join(bin_, "python3"))
    lib = os.path.join(racine, "lib")
    biblio = os.path.join(lib, "python" + VERSION_COURTE)
    if not binaire_lie_a_libpython(os.path.join(bin_, "python3")):
        for nom in os.listdir(lib):
            if nom.startswith("libpython"):
                effacer(os.path.join(lib, nom))
    for nom in os.listdir(lib):
        if re.match(r"(tcl|tk|itcl|thread|libtcl|libtk|pkgconfig)", nom):
            effacer(os.path.join(lib, nom))
    for nom in ("include", "share"):
        effacer(os.path.join(racine, nom))
    for nom in INUTILES_BIBLIOTHEQUE:
        effacer(os.path.join(biblio, nom))
    for nom in os.listdir(biblio):
        if nom.startswith("config-"):
            effacer(os.path.join(biblio, nom))
    dynload = os.path.join(biblio, "lib-dynload")
    for nom in os.listdir(dynload):
        if nom.startswith("_tkinter"):
            effacer(os.path.join(dynload, nom))
    paquets = os.path.join(biblio, "site-packages")
    for nom in INUTILES_PAQUETS:
        effacer(os.path.join(paquets, nom))
    for nom in os.listdir(paquets):
        if re.match(r"(pip|setuptools|wheel)-[\d.]+\.dist-info$", nom):
            effacer(os.path.join(paquets, nom))
    # llama-cpp-python installe une seconde copie de ses bibliothèques et ses en-têtes à la racine
    # de site-packages (include/, lib/, bin/) : seule celle de llama_cpp/lib sert
    for nom in ("include", "lib", "bin"):
        effacer(os.path.join(paquets, nom))
    for racine_, dossiers, _ in os.walk(racine, topdown=True):
        for nom in list(dossiers):
            if nom in ("tests", "__pycache__"):
                effacer(os.path.join(racine_, nom))
                dossiers.remove(nom)
    dedoublonner(os.path.join(paquets, "llama_cpp", "lib"))
    if SUR_MAC:
        amincir(racine)
        retirer_chemins_absolus(racine)
        signer_localement(RESSOURCES)
    ok(f"Python allégé : {mo(taille_dossier(racine))}")


def dedoublonner(dossier):
    """libggml.dylib, libggml.0.dylib… : le paquet en contient plusieurs copies identiques ;
    une seule est gardée, les autres deviennent des liens vers elle."""
    if not os.path.isdir(dossier):
        return
    par_empreinte = {}
    for nom in sorted(os.listdir(dossier), key=lambda n: (-len(n), n)):   # le nom le plus long (versionné) gardé
        chemin = os.path.join(dossier, nom)
        if os.path.islink(chemin) or not os.path.isfile(chemin):
            continue
        cle = empreinte(chemin)
        if cle in par_empreinte:
            os.remove(chemin)
            os.symlink(par_empreinte[cle], chemin)
        else:
            par_empreinte[cle] = nom


def est_macho(chemin):
    try:
        with open(chemin, "rb") as fichier:
            debut = fichier.read(4)
    except OSError:
        return False
    return debut in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xfe\xed\xfa\xcf",
                     b"\xfe\xed\xfa\xce", b"\xbe\xba\xfe\xca")


def programmes(dossier):
    """Tous les fichiers Mach-O (programmes et bibliothèques), sans les liens."""
    trouves = []
    for racine, _, fichiers in os.walk(dossier):
        for nom in fichiers:
            chemin = os.path.join(racine, nom)
            if not os.path.islink(chemin) and est_macho(chemin):
                trouves.append(chemin)
    return trouves


def amincir(dossier):
    """Bibliothèques « universelles » (Intel + Apple Silicon) : seule la partie Intel est gardée."""
    for chemin in programmes(dossier):
        architectures = subprocess.run(["lipo", "-archs", chemin], capture_output=True, text=True).stdout.split()
        if len(architectures) > 1 and "x86_64" in architectures:
            executer(["lipo", chemin, "-thin", "x86_64", "-output", chemin + ".mince"])
            os.replace(chemin + ".mince", chemin)


def retirer_chemins_absolus(dossier):
    """Chemins de recherche de bibliothèques pointant vers le Mac qui a compilé (dossier de construction) :
    retirés, ils ne serviraient à rien ailleurs et trahiraient l'arborescence de ce Mac."""
    for chemin in programmes(dossier):
        entetes = subprocess.run(["otool", "-l", chemin], capture_output=True, text=True).stdout
        for rpath in re.findall(r"cmd LC_RPATH[\s\S]*?path (\S+)", entetes):
            if rpath.startswith("/") and not rpath.startswith(("/usr/lib", "/System/")):
                executer(["install_name_tool", "-delete_rpath", rpath, chemin], stderr=subprocess.DEVNULL)


def signer_localement(dossier):
    """Signature locale (« ad hoc ») de chaque programme modifié (aminci, chemins retirés) : le Python
    emballé se lance ainsi tel quel pour les vérifications et les essais (tests/lancer_tests.sh --emballe).
    La vraie signature (Hardened Runtime, ton identité) est faite par embarquer.sh."""
    fichiers = programmes(dossier)
    for debut in range(0, len(fichiers), 40):
        resultat = subprocess.run(["codesign", "--force", "--sign", "-", "--timestamp=none",
                                   *fichiers[debut:debut + 40]], capture_output=True, text=True)
        if resultat.returncode != 0:
            echec("Signature locale impossible :\n" + resultat.stderr[-1500:])
    ok(f"{len(fichiers)} programmes signés localement (la vraie signature viendra à la compilation)")


# --- 6. Licences -------------------------------------------------------------------------------

LICENCES_TEXTE = """Composants inclus dans O.V.E.R. — licences et sources
====================================================================

Le dossier de chaque paquet Python contient le texte de sa licence. Code source :

- Python {python} (PSF License) — python-build-standalone (astral-sh, MPL-2.0 / BSD)
  https://www.python.org · https://github.com/astral-sh/python-build-standalone
- llama.cpp / ggml (MIT, © The ggml authors) et llama-cpp-python (MIT, © Andrei Betlen)
  https://github.com/ggml-org/llama.cpp · https://github.com/abetlen/llama-cpp-python
- whisper.cpp {whisper} (MIT, © The ggml authors) — https://github.com/ggml-org/whisper.cpp
- NumPy (BSD-3-Clause ; bibliothèques OpenBLAS (BSD) et gfortran (GPL avec exception) incluses)
- Jinja2, MarkupSafe (BSD-3-Clause) · diskcache (Apache-2.0) · typing_extensions (PSF)
- certifi (MPL-2.0) — https://github.com/certifi/python-certifi

La voix est celle de macOS (commande say) : aucune voix n'est incluse dans l'application.
Le modèle de langage (Ministral 3 3B, Mistral AI, licence Apache-2.0) n'est pas inclus :
l'application le télécharge au premier lancement depuis
https://huggingface.co/mistralai/Ministral-3-3B-Instruct-2512-GGUF
Les modèles d'écoute Whisper (OpenAI, MIT) sont téléchargés de la même façon.
"""

def rassembler_licences(licence_whisper):
    cible = os.path.join(RESSOURCES, "licences")
    os.makedirs(cible)
    biblio = os.path.join(RESSOURCES, "python", "lib", "python" + VERSION_COURTE)
    shutil.copyfile(os.path.join(biblio, "LICENSE.txt"), os.path.join(cible, f"Python-{VERSION_PYTHON}.txt"))
    if licence_whisper:
        shutil.copyfile(licence_whisper, os.path.join(cible, "whisper.cpp.txt"))
    paquets = os.path.join(biblio, "site-packages")
    versions = {}
    for nom in sorted(os.listdir(paquets)):
        if not nom.endswith(".dist-info"):
            continue
        nom_version = nom[:-len(".dist-info")]
        paquet, _, version = nom_version.partition("-")
        versions[paquet.lower().replace("_", "-")] = version
        source = os.path.join(paquets, nom)
        textes = []
        for racine, _, fichiers in os.walk(source):
            for fichier in fichiers:
                if re.match(r"(LICEN[CS]E|COPYING|NOTICE|AUTHORS)", fichier, re.IGNORECASE) \
                        or os.path.basename(racine) == "licenses" or "/licenses/" in racine + "/":
                    textes.append(os.path.join(racine, fichier))
        # certains paquets rangent leur licence dans le dossier du paquet
        try:
            with open(os.path.join(source, "top_level.txt"), encoding="utf-8") as fichier:
                modules = [ligne.strip() for ligne in fichier if ligne.strip()]
        except OSError:
            modules = []
        for module in modules:
            dossier_module = os.path.join(paquets, module)
            if os.path.isdir(dossier_module):
                for fichier in os.listdir(dossier_module):
                    if re.match(r"(LICEN[CS]E|COPYING|NOTICE|ThirdPartyNotices)", fichier, re.IGNORECASE):
                        textes.append(os.path.join(dossier_module, fichier))
        if textes:
            dossier = os.path.join(cible, nom_version)
            os.makedirs(dossier, exist_ok=True)
            for chemin in textes:
                nom_copie = os.path.relpath(chemin, source if chemin.startswith(source + os.sep) else paquets)
                shutil.copyfile(chemin, os.path.join(dossier, nom_copie.replace(os.sep, "_")))
    sys.path.insert(0, PROJET)
    import ecoute
    with open(os.path.join(cible, "LISEZMOI.txt"), "w", encoding="utf-8") as fichier:
        fichier.write(LICENCES_TEXTE.format(python=VERSION_PYTHON, whisper=ecoute.VERSION_WHISPER))
    ok(f"licences de {len(versions)} paquets rassemblées")
    return versions


# --- 7. Précompilation et vérifications ----------------------------------------------------------

def precompiler(python):
    """Fichiers .pyc fabriqués maintenant (pas au lancement : l'application signée ne doit jamais être
    modifiée). « unchecked-hash » : Python s'en sert sans regarder la date des fichiers."""
    environnement = {cle: valeur for cle, valeur in os.environ.items() if not cle.startswith("PYTHON")}
    for dossier in (os.path.join(RESSOURCES, "python", "lib"), os.path.join(RESSOURCES, "over")):
        resultat = subprocess.run([python, "-I", "-m", "compileall", "-q", "-j", "0", "--invalidation-mode",
                                   "unchecked-hash", dossier], env=environnement, capture_output=True, text=True)
        if resultat.returncode != 0:
            erreurs = (resultat.stdout + resultat.stderr).strip()
            if dossier.endswith("over"):
                echec("Un script d'O.V.E.R. ne se compile pas :\n" + erreurs[-2000:])
            note("(fichiers de paquets non compilables, sans effet : " + erreurs.splitlines()[-1][:160] + ")")
    ok("fichiers Python précompilés")


VERIFICATION = r"""
import importlib, os, sys, ssl, ctypes, sqlite3, json, tempfile
import certifi, numpy, jinja2, llama_cpp
ssl.create_default_context(cafile=certifi.where())
sys.path.insert(0, sys.argv[1])
for nom in ("reglages", "parametres", "identite", "balises", "cerveau", "memoire_modele", "web", "actions",
            "briefing", "voix", "ecoute", "demarrage", "installation", "noyau"):
    importlib.import_module(nom)
import reglages, ecoute
assert reglages.EMBALLE and reglages.DOSSIER_DONNEES == os.environ["OVER_DONNEES"], "dossier des données"
if os.path.isfile(sys.argv[2]):
    modele = llama_cpp.Llama(sys.argv[2], n_ctx=128, n_threads=1, verbose=False)
    sortie = modele("Bonjour", max_tokens=4)
    assert sortie["choices"], "le modèle d'essai ne répond pas"
# le moteur doit avoir les instructions des Mac Intel récents (sinon : plusieurs fois plus lent)
infos = llama_cpp.llama_print_system_info().decode("utf-8", "replace")
manque = [nom for nom in ("AVX2", "FMA", "F16C") if nom + " = 1" not in infos]
assert not manque, "moteur compilé sans " + ", ".join(manque) + " (il serait très lent) : " + infos
print("llama_cpp", llama_cpp.__version__, "(AVX2, FMA, F16C)", "numpy", numpy.__version__,
      "python", sys.version.split()[0])
"""


def verifier(python):
    environnement = {cle: valeur for cle, valeur in os.environ.items()
                     if not cle.startswith("PYTHON") and not cle.startswith("OVER_")}
    with tempfile.TemporaryDirectory(prefix="essai-emballage-") as donnees:
        environnement.update(OVER_EMBALLE="1", OVER_DONNEES=donnees, PYTHONDONTWRITEBYTECODE="1")
        resultat = subprocess.run([python, "-I", "-B", "-X", "utf8", "-c", VERIFICATION,
                                   os.path.join(RESSOURCES, "over"), os.path.join(PROJET, "tests", "mini.gguf")],
                                  env=environnement, capture_output=True,
                                  text=True, timeout=600)
    if resultat.returncode != 0:
        echec("Le Python emballé ne démarre pas O.V.E.R. :\n" + (resultat.stderr or resultat.stdout)[-2500:])
    ok("tout se charge : " + resultat.stdout.strip().splitlines()[-1])
    whisper = os.path.join(RESSOURCES, "bin", "whisper-cli")
    if os.path.exists(whisper):
        resultat = subprocess.run([whisper, "--help"], capture_output=True, text=True, timeout=60)
        if resultat.returncode != 0:
            echec("whisper-cli ne démarre pas.")
        ok("whisper-cli démarre")
    if SUR_MAC:
        verifier_programmes()
    # aucun fichier .pyc ne doit avoir été écrit par la vérification (-B), ni aucune donnée personnelle
    for racine, _, fichiers in os.walk(RESSOURCES):
        for nom in fichiers:
            if nom in ("cerveau.json", "parametres.json", "conversation.json", "memoire_modele.bin") \
                    or nom.endswith(".gguf"):
                echec(f"Fichier personnel ou modèle dans l'emballage : {os.path.join(racine, nom)}")


def verifier_programmes():
    """Chaque programme : Intel, macOS 12 au plus, et rien qui pointe vers un fichier de CE Mac."""
    problemes = []
    for chemin in programmes(RESSOURCES):
        relatif = os.path.relpath(chemin, RESSOURCES)
        architectures = subprocess.run(["lipo", "-archs", chemin], capture_output=True, text=True).stdout.split()
        if "x86_64" not in architectures:
            problemes.append(f"{relatif} : pas de version Intel ({' '.join(architectures)})")
        entetes = subprocess.run(["otool", "-l", chemin], capture_output=True, text=True).stdout
        for version in re.findall(r"minos (\d+(?:\.\d+)*)", entetes) + \
                re.findall(r"LC_VERSION_MIN_MACOSX[\s\S]*?version (\d+(?:\.\d+)*)", entetes):
            if tuple(int(x) for x in version.split(".")) > (12, 0):
                problemes.append(f"{relatif} : demande macOS {version} (au plus 12.0)")
        for chemin_rpath in re.findall(r"cmd LC_RPATH[\s\S]*?path (\S+)", entetes):
            if chemin_rpath.startswith("/"):
                problemes.append(f"{relatif} : chemin de recherche absolu {chemin_rpath}")
        # bibliothèques chargées (le nom propre d'une bibliothèque, LC_ID_DYLIB, ne compte pas)
        for cible in re.findall(r"cmd LC_(?:LOAD|LOAD_WEAK|REEXPORT|LAZY_LOAD|LOAD_UPWARD)_DYLIB\s+cmdsize \d+\s+"
                                r"name (\S+)", entetes):
            if not cible.startswith(("/usr/lib/", "/System/", "@rpath/", "@loader_path/", "@executable_path/")):
                problemes.append(f"{relatif} : dépend de {cible}")
    if problemes:
        echec("Programmes qui ne marcheraient pas sur un autre Mac :\n  " + "\n  ".join(sorted(set(problemes))[:30]))
    ok("programmes : Intel, macOS 12 et plus, sans dépendance à ce Mac")


def ecrire_fiche(versions, avec_ecoute):
    sys.path.insert(0, PROJET)
    import identite
    fiche = {
        "over": identite.VERSION,
        "date": datetime.datetime.now().isoformat(timespec="seconds"),
        "python": VERSION_PYTHON,
        "systeme": f"{sys.platform}-{machine()}",
        "macos_minimum": CIBLE_MACOS,
        "options_llama": OPTIONS_LLAMA + ["CFLAGS=" + INSTRUCTIONS_LLAMA],
        "voix": "macOS",
        "ecoute": avec_ecoute,
        "paquets": versions,
        "scripts": {nom: empreinte(os.path.join(RESSOURCES, "over", nom)) for nom in SCRIPTS},
        "taille": taille_dossier(RESSOURCES),
    }
    whisper = os.path.join(RESSOURCES, "bin", "whisper-cli")
    if os.path.isfile(whisper):
        fiche["whisper_cli"] = empreinte(whisper)
    with open(os.path.join(RESSOURCES, "emballage.json"), "w", encoding="utf-8") as fichier:
        json.dump(fiche, fichier, ensure_ascii=False, indent=2)
    return fiche


def main():
    analyseur = argparse.ArgumentParser(description="Prépare Python et les scripts d'O.V.E.R. pour l'application.")
    analyseur.add_argument("--sans-ecoute", action="store_true", help="sans whisper.cpp (pas d'écoute locale)")
    arguments = analyseur.parse_args()
    avec_ecoute = not arguments.sans_ecoute
    if SUR_MAC and not shutil.which("xcrun"):
        echec("Outils de développement Apple absents : lance « xcode-select --install ».")
    etape("1/7  Python " + VERSION_PYTHON)
    python = preparer_python()
    etape("2/7  Paquets Python (versions et empreintes figées dans dependances/)")
    installer_paquets(python)
    etape("3/7  Écoute (whisper.cpp)")
    licence_whisper = preparer_ecoute() if avec_ecoute else None
    if not avec_ecoute:
        note("sans écoute (--sans-ecoute)")
    etape("4/7  Scripts d'O.V.E.R.")
    copier_scripts()
    etape("5/7  Allègement")
    alleger()
    python = os.path.join(RESSOURCES, "python", "bin", "python3")
    etape("6/7  Licences et précompilation")
    versions = rassembler_licences(licence_whisper)
    precompiler(python)
    etape("7/7  Vérifications")
    verifier(python)
    fiche = ecrire_fiche(versions, avec_ecoute)
    duree = int(time.monotonic() - journal_debut)
    print(f"\n\033[38;5;179mEmballage prêt\033[0m : app-mac/build/Ressources ({mo(fiche['taille'])}, "
          f"{duree // 60} min {duree % 60:02d} s)")
    print("Ensuite : Xcode ▸ Product ▸ Build For ▸ Profiling (⇧⌘I), ou bash app-mac/construire_app.sh :\n"
          "l'application contiendra Python et les scripts, et n'aura plus besoin du dossier O.V.E.R.\n")


if __name__ == "__main__":
    main()
