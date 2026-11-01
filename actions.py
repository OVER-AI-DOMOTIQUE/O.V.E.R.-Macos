# -*- coding: utf-8 -*-
"""
Actions d'O.V.E.R. sur le Mac.

Deux façons de déclencher une action :
  1. directement, sans passer par le modèle (instantané) : « ouvre Safari »,
     « volume 30 », « quelle heure est-il ? », « minuteur 10 minutes pour les pâtes » ;
  2. par le modèle, qui écrit une ligne cachée « [ACTION] nom : argument | argument »
     (rappels…).

Actions : ouvrir une app ou un site, régler le son, l'heure, les minuteurs, les rappels.

Garde-fous :
  - la liste des actions est fermée : rien d'autre ne peut être lancé ;
  - tout ce qui crée quelque chose de durable (un rappel) demande confirmation ;
  - une action proposée par le modèle n'est exécutée que si le message de
    l'utilisateur la demande vraiment (mots-clés attendus pour chaque action).

Qui agit ?
  - dans l'application (App Store, bac à sable d'Apple) : l'application elle-même, avec les outils
    d'Apple (NSWorkspace pour ouvrir, Core Audio pour le son, EventKit pour les rappels et l'agenda,
    les notifications de macOS). serveur.py branche sa fonction dans SYSTEME ;
  - dans le Terminal (over_chat.py) : les commandes de macOS (open, osascript).
Le texte de l'utilisateur est toujours passé en argument, jamais collé dans un
script : pas d'injection possible.
"""

import datetime
import os
import re
import subprocess
import sys
import threading
import unicodedata
from dataclasses import dataclass, field
from typing import List

SUR_MAC = sys.platform == "darwin"
# Commandes de macOS appelées par leur chemin complet : un PATH modifié ne peut pas en glisser d'autres.
CHEMINS_SYSTEME = {"open": "/usr/bin/open", "osascript": "/usr/bin/osascript"}

# Dans l'application : fonction(action, attendre=True, **paramètres) → (réussie, détail, données),
# exécutée par l'application (voir serveur.py). None dans le Terminal.
SYSTEME = None
DOSSIERS_APPLICATIONS = (
    "/Applications", "/Applications/Utilities", "/System/Applications",
    "/System/Applications/Utilities", os.path.expanduser("~/Applications"),
)

# Noms français des applications d'Apple → nom du paquet sur le disque
NOMS_FRANCAIS = {
    "calendrier": ["Calendar"], "musique": ["Music"], "plans": ["Maps"], "rappels": ["Reminders"],
    "reglages": ["System Settings", "System Preferences"],
    "reglages systeme": ["System Settings", "System Preferences"],
    "preferences systeme": ["System Preferences", "System Settings"],
    "calculette": ["Calculator"], "calculatrice": ["Calculator"], "apercu": ["Preview"],
    "horloge": ["Clock"], "meteo": ["Weather"], "livres": ["Books"], "raccourcis": ["Shortcuts"],
    "moniteur d activite": ["Activity Monitor"], "dictaphone": ["Voice Memos"],
    "editeur de texte": ["TextEdit"], "utilitaire de disque": ["Disk Utility"],
    "capture d ecran": ["Screenshot"], "bourse": ["Stocks"], "lecteur dvd": ["DVD Player"],
    "word": ["Microsoft Word"], "excel": ["Microsoft Excel"], "powerpoint": ["Microsoft PowerPoint"],
    "chrome": ["Google Chrome"], "edge": ["Microsoft Edge"], "vscode": ["Visual Studio Code"],
    "vs code": ["Visual Studio Code"], "visual studio code": ["Visual Studio Code"],
}


@dataclass
class Action:
    nom: str
    arguments: List[str] = field(default_factory=list)

    def argument(self, numero, defaut=""):
        return self.arguments[numero].strip() if numero < len(self.arguments) else defaut


@dataclass
class Resultat:
    reussie: bool
    message: str                 # ce qu'O.V.E.R. affiche
    note: str = ""               # résumé ajouté à la conversation, pour que le modèle sache ce qui s'est passé
    contenu: bool = False        # vrai si le message apporte une information (heure, fichiers) plutôt qu'un simple compte rendu


# --- Outils -------------------------------------------------------------------------

def sans_accents(texte):
    decompose = unicodedata.normalize("NFD", texte)
    return "".join(c for c in decompose if unicodedata.category(c) != "Mn")


def forme_simple(texte):
    """« Moniteur d'activité » → « moniteur d activite » (pour comparer des noms)."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", sans_accents(texte.lower())).split())


def lancer(commande, delai=20, entree=None):
    """Lance une commande macOS. Renvoie (réussie, sortie ou message d'erreur).
    entree : texte envoyé sur l'entrée standard de la commande (ex. la durée pour un Raccourci).
    Sans entree, la commande reçoit une entrée vide : elle ne doit jamais lire celle d'O.V.E.R.
    (dans l'application, c'est le canal des messages : une commande qui lit l'entrée l'aurait avalé)."""
    options = {"input": entree} if entree is not None else {"stdin": subprocess.DEVNULL}
    commande = list(commande)
    if SUR_MAC and os.path.isfile(CHEMINS_SYSTEME.get(commande[0], "")):
        commande[0] = CHEMINS_SYSTEME[commande[0]]
    try:
        resultat = subprocess.run(commande, capture_output=True, text=True, timeout=delai, **options)
    except FileNotFoundError:
        return False, f"commande introuvable : {commande[0]}"
    except subprocess.TimeoutExpired:
        return False, "trop long, abandonné"
    if resultat.returncode != 0:
        return False, (resultat.stderr or resultat.stdout).strip()[:300] or f"code {resultat.returncode}"
    return True, resultat.stdout.strip()


def applescript(lignes, *arguments, delai=20):
    """Exécute un AppleScript ; les textes de l'utilisateur arrivent dans argv (aucune injection)."""
    commande = ["osascript"]
    for ligne in ["on run argv"] + list(lignes) + ["end run"]:
        commande += ["-e", ligne]
    return lancer(commande + list(arguments), delai)


def notifier(titre, texte):
    """Notification macOS avec un son (sans attendre : appelée aussi par les minuteurs, depuis un autre fil)."""
    if SYSTEME:
        SYSTEME("notifier", attendre=False, titre=titre, texte=texte)
    elif SUR_MAC:
        applescript(['display notification (item 2 of argv) with title (item 1 of argv) sound name "Glass"'],
                    titre, texte)


def ouvrir(cible):
    """Ouvre une application (chemin du .app) ou une adresse web. Renvoie (réussie, erreur)."""
    if SYSTEME:
        ok, detail, _ = SYSTEME("ouvrir", cible=cible)
        return ok, detail
    return lancer(["open", cible])


def heure_fr(moment):
    return f"{moment.hour} h {moment.minute:02d}"


JOURS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
MOIS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet",
        "août", "septembre", "octobre", "novembre", "décembre")


def date_fr(moment):
    numero = "1er" if moment.day == 1 else str(moment.day)
    return f"{JOURS[moment.weekday()]} {numero} {MOIS[moment.month - 1]}"


# --- Trouver une application -------------------------------------------------------------

_cache_applications = {}


def applications_installees():
    """{forme simple du nom : chemin} pour les applications du Mac (lu une fois)."""
    if not _cache_applications:
        for dossier in DOSSIERS_APPLICATIONS:
            try:
                for nom in os.listdir(dossier):
                    if nom.endswith(".app"):
                        _cache_applications.setdefault(forme_simple(nom[:-4]), os.path.join(dossier, nom))
            except OSError:
                continue
    return _cache_applications


def trouver_application(nom):
    """Chemin de l'application demandée (nom anglais, français ou approchant), ou None."""
    cle = forme_simple(nom)
    if not cle:
        return None
    installees = applications_installees()
    candidats = [cle] + [forme_simple(n) for n in NOMS_FRANCAIS.get(cle, [])]
    for candidat in candidats:
        if candidat in installees:
            return installees[candidat]
    for candidat in candidats:  # « word » → « microsoft word », « chrome » → « google chrome »
        approchants = [chemin for nom_app, chemin in installees.items()
                       if nom_app.endswith(" " + candidat) or nom_app.startswith(candidat + " ")]
        if len(approchants) == 1:
            return approchants[0]
    return None


def nom_application(chemin):
    return os.path.basename(chemin)[:-4] if chemin.endswith(".app") else os.path.basename(chemin)


# --- Les actions -----------------------------------------------------------------------

MINUTEURS = []   # minuteurs en cours (threading.Timer, avec .fin, .motif et .secondes)
# Appelé à chaque minuteur lancé ou terminé, avec la liste à garder (voir minuteurs_en_cours) :
# la session l'enregistre pour retrouver ses minuteurs au prochain lancement.
AU_CHANGEMENT_MINUTEURS = None


def minuteurs_en_cours():
    """Les minuteurs d'O.V.E.R. qui tournent : [{"fin": "AAAA-MM-JJTHH:MM:SS", "motif", "secondes"}]."""
    return [{"fin": m.fin.isoformat(timespec="seconds"), "motif": m.motif, "secondes": m.secondes}
            for m in list(MINUTEURS)]


def _minuteurs_changes():
    if AU_CHANGEMENT_MINUTEURS:
        try:
            AU_CHANGEMENT_MINUTEURS(minuteurs_en_cours())
        except Exception:
            pass   # garder la liste est un confort : jamais une raison de faire échouer le minuteur


def armer_minuteur(fin, motif, secondes, signaler):
    """Programme la sonnerie à l'instant fin (datetime) ; renvoie le minuteur."""
    texte_fin = f"Minuteur terminé{' : ' + motif if motif else ''} ({texte_duree(secondes)})."

    def sonner():
        notifier("O.V.E.R.", texte_fin)
        signaler(texte_fin)
        if minuteur in MINUTEURS:
            MINUTEURS.remove(minuteur)
        _minuteurs_changes()

    attente = max(0.0, (fin - datetime.datetime.now()).total_seconds())
    minuteur = threading.Timer(attente, sonner)
    minuteur.daemon = True
    minuteur.fin, minuteur.motif, minuteur.secondes = fin, motif, secondes
    minuteur.start()
    MINUTEURS.append(minuteur)
    _minuteurs_changes()
    return minuteur


def action_ouvrir_app(action, signaler):
    nom = action.argument(0)
    chemin = trouver_application(nom)
    if not chemin and SYSTEME and nom.strip():
        # dans le bac à sable, certains dossiers d'applications ne se lisent pas : l'application demande
        # alors à macOS où se trouve l'app (par son nom anglais ou tel quel)
        noms = list(NOMS_FRANCAIS.get(forme_simple(nom), [])) + [nom.strip()]
        ok, erreur, donnees = SYSTEME("ouvrir", noms=noms)
        if ok:
            ouverte = str((donnees or {}).get("nom") or nom.strip())
            return Resultat(True, f"{ouverte} est ouvert.", f"{ouverte} ouvert")
    if not chemin:
        return Resultat(False, f"Je ne trouve pas d'application « {nom} » sur ce Mac.", "application introuvable")
    ok, erreur = ouvrir(chemin)
    if not ok:
        return Resultat(False, f"Impossible d'ouvrir {nom_application(chemin)} : {erreur}", "échec")
    return Resultat(True, f"{nom_application(chemin)} est ouvert.", f"{nom_application(chemin)} ouvert")


def action_ouvrir_site(action, signaler):
    adresse = action.argument(0).strip(" <>\"'")
    if not re.match(r"^https?://", adresse, re.IGNORECASE):
        if not re.match(r"^([a-z0-9-]+\.)+[a-z]{2,24}(/\S*)?$", adresse, re.IGNORECASE):
            return Resultat(False, f"« {adresse} » n'est pas une adresse de site.", "adresse invalide")
        adresse = "https://" + adresse
    ok, erreur = ouvrir(adresse)
    if not ok:
        return Resultat(False, f"Impossible d'ouvrir {adresse} : {erreur}", "échec")
    return Resultat(True, f"J'ai ouvert {adresse} dans ton navigateur.", f"{adresse} ouvert")


def lire_demande_volume(texte):
    """« couper », « plus », « 30 »… → ("couper"|"remettre"|"plus"|"moins"|"regler", niveau) ou None."""
    demande = forme_simple(texte)
    if demande in ("couper", "coupe", "muet", "silence", "0 muet"):
        return "couper", None
    if demande in ("remettre", "remets", "reactiver", "remettre le son", "on"):
        return "remettre", None
    if demande in ("plus", "monter", "augmenter", "fort", "plus fort"):
        return "plus", None
    if demande in ("moins", "baisser", "diminuer", "moins fort"):
        return "moins", None
    chiffres = re.findall(r"\d+", demande)
    return ("regler", max(0, min(100, int(chiffres[0])))) if chiffres else None


def action_volume(action, signaler):
    demande = lire_demande_volume(action.argument(0))
    if not demande:
        return Resultat(False, f"Je n'ai pas compris le volume « {action.argument(0)} ».", "volume incompris")
    genre, niveau = demande
    if SYSTEME:
        ok, erreur, donnees = SYSTEME("volume", genre=genre, niveau=niveau if niveau is not None else -1)
        niveau = (donnees or {}).get("niveau", niveau)
    elif genre == "couper":
        ok, erreur = applescript(["set volume with output muted"])
    elif genre == "remettre":
        ok, erreur = applescript(["set volume without output muted"])
    else:
        if genre in ("plus", "moins"):
            ok, actuel = applescript(["output volume of (get volume settings)"])
            base = int(actuel) if ok and actuel.isdigit() else 45
            niveau = min(100, base + 15) if genre == "plus" else max(0, base - 15)
        ok, erreur = applescript([f"set volume output volume {niveau}", "set volume without output muted"])
    if not ok:
        return Resultat(False, f"Impossible de régler le son : {erreur}", "échec")
    message = {"couper": "Son coupé.", "remettre": "Son remis."}.get(genre) or f"Volume réglé à {niveau} %."
    return Resultat(True, message, message)


def action_heure(action, signaler):
    maintenant = datetime.datetime.now()
    message = f"Il est {heure_fr(maintenant)}, nous sommes le {date_fr(maintenant)} {maintenant.year}."
    return Resultat(True, message, message, contenu=True)


def lire_duree(texte):
    """« 10 », « 10 min », « 1h30 », « 90 s », « 2 heures » → secondes (ou None)."""
    t = sans_accents(texte.lower()).replace(",", ".").replace(" ", "")
    trouve = re.fullmatch(r"(\d+(?:\.\d+)?)h(\d{1,2})?(?:min)?", t)
    if trouve:
        return float(trouve.group(1)) * 3600 + float(trouve.group(2) or 0) * 60
    trouve = re.fullmatch(r"(\d+(?:\.\d+)?)(s|sec|secs|seconde|secondes|min|mins|minute|minutes|mn|h|heure|heures)?", t)
    if not trouve:
        return None
    nombre, unite = float(trouve.group(1)), trouve.group(2) or "min"
    if unite.startswith("s"):
        return nombre
    if unite.startswith("h"):
        return nombre * 3600
    return nombre * 60


def texte_duree(secondes):
    secondes = int(round(secondes))
    heures, reste = divmod(secondes, 3600)
    minutes, secs = divmod(reste, 60)
    morceaux = []
    if heures:
        morceaux.append(f"{heures} h")
    if minutes:
        morceaux.append(f"{minutes} min")
    if secs and not heures:
        morceaux.append(f"{secs} s")
    return " ".join(morceaux) or "0 s"


def nettoyer_motif(motif):
    """« *Fin du minuteur* », « Minuteur système Mac » → "" ; « les pâtes » → « les pâtes »."""
    propre = " ".join(re.sub(r"[*_`\"«»“”()\[\]]", " ", motif or "").split()).strip(" .:;,-–—")
    simple = forme_simple(propre)
    if not simple or re.search(r"\b(minuteur|timer|horloge|systeme|fin du|mac)\b", simple):
        return ""   # le modèle a mis une étiquette sans intérêt
    return propre[:40]


def action_minuteur(action, signaler):
    secondes = lire_duree(action.argument(0))
    if not secondes or secondes > 24 * 3600:
        return Resultat(False, f"Je n'ai pas compris la durée « {action.argument(0)} ».", "durée incomprise")
    motif = nettoyer_motif(action.argument(1))
    fin = datetime.datetime.now() + datetime.timedelta(seconds=secondes)
    armer_minuteur(fin, motif, secondes, signaler)
    message = f"Minuteur de {texte_duree(secondes)} lancé{' (' + motif + ')' if motif else ''} : il sonnera à {heure_fr(fin)}."
    return Resultat(True, message, message)


def lire_moment(texte):
    """« 2026-10-13 09:00 », « 2026-10-13 », « demain 9h », « 09:00 » → datetime (ou None)."""
    t = sans_accents(texte.strip().lower())
    trouve = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[ t]+(\d{1,2})[:h](\d{2})?)?", t)
    try:
        if trouve:
            annee, mois, jour = int(trouve.group(1)), int(trouve.group(2)), int(trouve.group(3))
            heures = int(trouve.group(4)) if trouve.group(4) else 9
            minutes = int(trouve.group(5)) if trouve.group(5) else 0
            return datetime.datetime(annee, mois, jour, heures, minutes)
        decalage = 2 if "apres-demain" in t or "apres demain" in t else (1 if "demain" in t else 0)
        trouve = re.search(r"\b(\d{1,2})\s*[:h]\s*(\d{2})?\b", t)
        if trouve or decalage:
            maintenant = datetime.datetime.now()
            heures = int(trouve.group(1)) if trouve else 9
            minutes = int(trouve.group(2) or 0) if trouve else 0
            moment = maintenant.replace(hour=heures, minute=minutes, second=0, microsecond=0)
            moment += datetime.timedelta(days=decalage)
            if not decalage and moment <= maintenant:
                moment += datetime.timedelta(days=1)   # « 9h » déjà passé aujourd'hui : demain
            return moment
    except ValueError:
        return None
    return None


def separer_moment_et_texte(action):
    """Le modèle écrit « date | texte » ; on accepte aussi « texte | date » ou le texte seul."""
    premier, second = action.argument(0), action.argument(1)
    if not second:
        return None, premier
    moment = lire_moment(premier)
    if moment:
        return moment, second
    moment = lire_moment(second)
    if moment:
        return moment, premier
    return None, f"{premier} {second}".strip()


def action_rappel(action, signaler):
    moment, texte = separer_moment_et_texte(action)
    if not texte:
        return Resultat(False, "Il me faut le texte du rappel.", "texte manquant")
    if SYSTEME:
        ok, erreur, _ = SYSTEME("rappel", texte=texte, date=moment.isoformat(timespec="minutes") if moment else "")
    elif moment:
        lignes = [
            'set d to current date', 'set day of d to 1',
            f'set year of d to {moment.year}', f'set month of d to {moment.month}', f'set day of d to {moment.day}',
            f'set hours of d to {moment.hour}', f'set minutes of d to {moment.minute}', 'set seconds of d to 0',
            'tell application "Reminders"',
            'tell default list to make new reminder with properties {name:(item 1 of argv), remind me date:d}',
            'end tell',
        ]
    else:
        lignes = ['tell application "Reminders"',
                  'tell default list to make new reminder with properties {name:(item 1 of argv)}',
                  'end tell']
    if not SYSTEME:
        ok, erreur = applescript(lignes, texte)
    if not ok:
        return Resultat(False, f"Impossible de créer le rappel : {erreur}", "échec")
    quand = f" pour le {date_fr(moment)} à {heure_fr(moment)}" if moment else ""
    return Resultat(True, f"Rappel créé{quand} : « {texte} ».", f"rappel créé{quand}")


# Le catalogue : fonction, confirmation, mots attendus dans le message de l'utilisateur,
# et la description donnée au modèle.
CATALOGUE = {
    "ouvrir_app": (action_ouvrir_app, False, None, "ouvrir_app : Nom"),
    "ouvrir_site": (action_ouvrir_site, False, r"\bsite\b|\.[a-z]{2,}\b|http|ouvr|va sur", "ouvrir_site : adresse"),
    "volume": (action_volume, False, r"\bvolume\b|\b(le|du) son\b|plus fort|moins fort|\bsilence\b|\bmuet\b|\bmute\b",
               "volume : 0-100, plus, moins, couper ou remettre"),
    "heure": (action_heure, False, r"\bheures?\b|\bla date\b|quel jour|on est le", "heure"),
    "minuteur": (action_minuteur, False, r"minut|chrono|timer|sonne|alarme", "minuteur : durée | motif"),
    "rappel": (action_rappel, True, r"rappel|rappelle|souvien|oubli|pense a", "rappel : AAAA-MM-JJ HH:MM | texte"),
}


def analyser(contenu):
    """« ouvrir_app : Safari » → Action ; renvoie None si l'action n'existe pas."""
    if ":" in contenu:
        nom, _, reste = contenu.partition(":")
    else:
        nom, _, reste = contenu.strip().partition(" ")
    nom = forme_simple(nom).replace(" ", "_")
    if nom not in CATALOGUE:
        return None
    arguments = [morceau.strip(" \"'«»") for morceau in reste.split("|")] if reste.strip() else []
    return Action(nom, arguments)


def est_demandee(action, message):
    """Garde-fou : le message de l'utilisateur doit vraiment demander cette action."""
    simple = forme_simple(message)
    if action.nom == "ouvrir_app":
        if not re.search(r"ouvr|lanc|demarr|va sur", simple):
            return False
        nom = forme_simple(action.argument(0))
        # le nom de l'application, ou son nom français (« calendrier » pour Calendar), doit figurer dans le message
        noms = {nom} | {francais for francais, anglais in NOMS_FRANCAIS.items()
                        if nom in (forme_simple(a) for a in anglais)}
        return any(mot in simple.split() or mot in simple
                   for n in noms for mot in [n] + [m for m in n.split() if len(m) >= 4])
    motif = CATALOGUE[action.nom][2]
    return bool(re.search(motif, sans_accents(message.lower())))


QUESTIONS_FORCEES = {
    "ouvrir_app": "J'ouvre cette application ?", "ouvrir_site": "J'ouvre ce site dans ton navigateur ?",
    "volume": "Je règle le son ?", "minuteur": "Je lance ce minuteur ?",
}


def question_de_confirmation(action, forcer=False):
    """Texte de la question si l'action demande confirmation, sinon None.
    forcer : demander même pour une action d'habitude directe (proposée par le modèle après avoir lu
    une page web, ou vers un site que l'utilisateur n'a pas cité). L'heure ne demande jamais rien."""
    if not CATALOGUE[action.nom][1]:
        if not forcer or action.nom not in QUESTIONS_FORCEES:
            return None
        return QUESTIONS_FORCEES[action.nom], " | ".join(a for a in action.arguments if a)
    if action.nom == "rappel":
        moment, texte = separer_moment_et_texte(action)
        quand = f" pour le {date_fr(moment)} à {heure_fr(moment)}" if moment else " (sans date)"
        return f"Je crée ce rappel{quand} ?", texte
    return f"Je lance l'action {action.nom} ?", " | ".join(action.arguments)


def executer(action, signaler=lambda texte: None):
    """Exécute l'action. signaler(texte) est appelé plus tard pour les minuteurs."""
    if not SUR_MAC and not SYSTEME:
        return Resultat(False, "Les actions ne fonctionnent que sur un Mac.", "indisponible")
    fonction = CATALOGUE[action.nom][0]
    try:
        return fonction(action, signaler)
    except Exception as erreur:  # une action ne doit jamais faire tomber la discussion
        return Resultat(False, f"L'action {action.nom} a échoué : {erreur}", "échec")


# --- Commandes directes (sans le modèle) ---------------------------------------------------

MOTIF_OUVRIR = re.compile(
    r"^(?:ouvre|lance|d[ée]marre)(?:-moi| moi)?\s+(?:l'app(?:lication)?\s+|le site\s+)?(?P<cible>.+?)[\s.!?]*$",
    re.IGNORECASE)
MOTIF_VOLUME_CHIFFRE = re.compile(
    r"^(?:mets |r[èe]gle |passe )?(?:le )?(?:volume|son)\s*(?:[àa]\s*)?(?P<niveau>\d{1,3})\s*%?[\s.!]*$", re.IGNORECASE)
MOTIF_VOLUME_PLUS = re.compile(r"^(?:monte|augmente)(?:-le| le)?\s+(?:son|volume)[\s.!]*$|^plus fort[\s.!]*$", re.IGNORECASE)
MOTIF_VOLUME_MOINS = re.compile(r"^(?:baisse|diminue)(?:-le| le)?\s+(?:son|volume)[\s.!]*$|^moins fort[\s.!]*$", re.IGNORECASE)
MOTIF_COUPER = re.compile(r"^(?:coupe|mute)(?:-le| le)?\s+(?:son|volume)[\s.!]*$|^silence[\s.!]*$", re.IGNORECASE)
MOTIF_REMETTRE = re.compile(r"^(?:remets|r[ée]active)(?:-le| le)?\s+(?:son|volume)[\s.!]*$", re.IGNORECASE)
MOTIF_HEURE = re.compile(r"^(?:quelle heure (?:est-il|il est)|il est quelle heure|l'heure|quelle heure)\s*\??\s*$", re.IGNORECASE)
# L'heure et la date, tournées librement (comparées sans accents ni ponctuation) : « donnez-moi l'heure
# exacte », « tu as l'heure ? », « on est quel jour ? »… La réponse vient de l'horloge du Mac, jamais du
# modèle, qui peut se tromper. « À quelle heure ferme la poste ? » n'en est pas une : rien d'autre autour.
_DEMANDE = (r"(?:(?:dis|dites|donne|donnez|redonne|redonnez|rappelle|rappelez)(?: moi| nous)? |"
            r"(?:tu as|tu aurais|t as|vous avez|vous auriez|as tu|avez vous) |"
            r"(?:tu peux|tu pourrais|peux tu|pourrais tu|vous pouvez|pouvez vous|pourriez vous) "
            r"(?:me |nous )?(?:dire |donner )?|"
            r"(?:je veux|je voudrais|j aimerais) (?:savoir |connaitre )?)?")
_POLITESSE = r"(?: (?:exacte|exactement|precise|precisement|actuelle|qu il est|maintenant|aujourd hui|stp|svp|s il te plait|s il vous plait|merci|over|o v e r))*"
MOTIF_HEURE_LIBRE = re.compile(
    r"^(?:(?:bonjour|salut|dis|eh|alors|over|o v e r) )*" + _DEMANDE +
    r"(?:l heure|quelle heure(?: est il| il est| c est)?|il est quelle heure|c est quelle heure|"
    r"la date|la date d aujourd hui|quelle est la date(?: d aujourd hui)?|c est quelle date|"
    r"quelle date(?: on est| sommes nous| nous sommes| est on)?|quel jour(?: on est| sommes nous| nous sommes| est on| est ce| c est)?|"
    r"on est quel jour|on est le combien|nous sommes le combien|c est quel jour|quel jour sommes nous|"
    r"l heure et la date|la date et l heure)" + _POLITESSE + r"$")


def est_demande_heure(texte):
    """« Donnez-moi l'heure exacte. », « Tu as l'heure ? », « On est quel jour ? » → True."""
    simple = forme_simple(texte)
    return bool(simple) and len(simple.split()) <= 12 and bool(MOTIF_HEURE_LIBRE.match(simple))
# « minuteur 10 min », « mets un minuteur de 20 min sur l'horloge », « tu peux lancer un timer de 5 min pour les pâtes ? »
MOTIF_MINUTEUR = re.compile(
    r"\b(?:minuteur|timer|chrono(?:m[èe]tre)?|compte [àa] rebours)\s+(?:de\s+|d'\s*|pour\s+|sur\s+)?"
    r"(?P<duree>\d+(?:[.,]\d+)?\s*(?:h(?:eures?)?\s*\d{0,2}(?:\s*min)?|min(?:ute)?s?|mn|s(?:ec(?:onde)?s?)?\b)?)",
    re.IGNORECASE)
MOTIF_MOTIF_MINUTEUR = re.compile(
    r"\bpour\s+(?P<motif>(?:le|la|les|l'|mon|ma|mes|un|une|des|du)\s*[^,.!?]+?)"
    r"(?=\s+(?:sur|dans|avec|stp|svp|s'il)\b|\s*[,.!?]|\s*$)", re.IGNORECASE)
MOTIF_QUESTION_GENERALE = re.compile(r"^(?:comment|pourquoi|qu|quel|quelle|c'est quoi|est-ce|explique)", re.IGNORECASE)


def detecter_minuteur(texte):
    """Une demande de minuteur, même tournée librement ; None si ce n'en est pas une."""
    if len(texte.split()) > 16 or MOTIF_QUESTION_GENERALE.match(texte):
        return None
    trouve = MOTIF_MINUTEUR.search(texte)
    if not trouve or lire_duree(trouve.group("duree")) is None:
        return None
    motif = MOTIF_MOTIF_MINUTEUR.search(texte[trouve.end():])
    return Action("minuteur", [trouve.group("duree").strip(), motif.group("motif").strip() if motif else ""])


def extraire_commande_directe(message):
    """Reconnaît les demandes simples et renvoie l'Action à exécuter tout de suite, sinon None."""
    texte = message.strip()
    trouve = MOTIF_OUVRIR.match(texte)
    if trouve:
        cible = trouve.group("cible").strip(" \"'«»")
        cible = re.sub(r"^(?:le|la|les|l'|un|une|mon|ma|mes)\s*", "", cible, flags=re.IGNORECASE).strip()
        if re.match(r"^(https?://)?([a-z0-9-]+\.)+[a-z]{2,24}(/\S*)?$", cible, re.IGNORECASE):
            return Action("ouvrir_site", [cible])
        if trouver_application(cible):
            return Action("ouvrir_app", [cible])
        if SYSTEME and len(cible.split()) <= 2 and re.fullmatch(r"[^\W\d_][\w.&+'’ -]{0,40}", cible) \
                and not MOTIF_MINUTEUR.search(texte):
            # application (bac à sable) : un nom court qu'on ne voit pas d'ici, macOS le cherchera
            return Action("ouvrir_app", [cible])
        # « lance un minuteur de 5 min » continue plus bas ; « ouvre une note pour demain » ira au modèle
    trouve = MOTIF_VOLUME_CHIFFRE.match(texte)
    if trouve:
        return Action("volume", [trouve.group("niveau")])
    for motif, valeur in ((MOTIF_VOLUME_PLUS, "plus"), (MOTIF_VOLUME_MOINS, "moins"),
                          (MOTIF_COUPER, "couper"), (MOTIF_REMETTRE, "remettre")):
        if motif.match(texte):
            return Action("volume", [valeur])
    if MOTIF_HEURE.match(texte) or est_demande_heure(texte):
        return Action("heure")
    return detecter_minuteur(texte)


def consigne_actions():
    """Explique au modèle comment agir sur le Mac (version courte : chaque mot compte au démarrage)."""
    liste = " ; ".join(description for _, _, _, description in CATALOGUE.values())
    return (
        "Actions sur le Mac : quand l'utilisateur demande d'agir, écris une phrase courte au futur "
        "(« Je lance le minuteur. ») puis une ligne [ACTION] nom : arguments. "
        f"Actions : {liste}. Pour un rappel, calcule la date à partir d'aujourd'hui. "
        "Seulement si l'utilisateur le demande. N'écris jamais de résultat, d'heure ou de confirmation : "
        "le programme exécute, demande confirmation si besoin et affiche le résultat. Pour l'heure ou la date, "
        "écris toujours [ACTION] heure. Ne propose jamais ce qui n'est pas dans cette liste."
    )
