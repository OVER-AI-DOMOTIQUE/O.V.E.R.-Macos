# -*- coding: utf-8 -*-
"""
Noyau d'O.V.E.R. — le cerveau du programme, sans affichage.

Il charge le modèle, tient la conversation (et la garde d'un lancement à l'autre),
lance les recherches web et les actions sur le Mac, gère le cerveau, les paramètres, la voix,
le briefing et les commandes. Il ne parle jamais directement à l'écran : il passe
par une « interface », fournie soit par le Terminal (over_chat.py), soit par
l'application Mac (serveur.py). Les deux se comportent donc exactement pareil.
"""

import contextlib
import ctypes
import datetime
import json
import os
import re
import tempfile
import threading
import time

import llama_cpp
from llama_cpp import Llama

import actions
import briefing
import identite
import memoire_modele
import reglages
import web
from balises import MOTIF_BALISE, FiltreBalises, nettoyer_contenu
from cerveau import (LIBELLES, QUESTIONS, Cerveau, fait_vient_de_l_utilisateur, forme_nom, interpreter_confirmation,
                     nettoyer_fait, questionnaire, trouver_champ)
from parametres import Parametres, cle_depuis_nom
import demarrage
import ecoute as module_ecoute
import voix as module_voix
from voix import Voix


# --- Interface ----------------------------------------------------------------------

class Interface:
    """Ce que le noyau attend de l'interface. Chaque interface (Terminal, application) la complète."""

    nom = "dans le Terminal du Mac"   # où O.V.E.R. tourne, pour son identité
    astuces_clavier = True            # afficher les raccourcis clavier (inutile avec des boutons)

    def dire(self, texte):
        """Phrase d'O.V.E.R. écrite d'avance (sans passer par le modèle)."""

    def info(self, texte):
        """Renseignement discret (mémoire pleine, conversation effacée…)."""

    def erreur(self, texte):
        """Problème à signaler."""

    def demander(self, question, valeur_actuelle="", choix=None, aide="", extrait=""):
        """Pose une question et renvoie la réponse (texte), ou None si l'utilisateur arrête.
        choix : [(libellé, valeur)] proposés en boutons (valeur None = arrêter).
        extrait : passage à mettre en valeur (l'information à retenir)."""
        return None

    def texte(self, morceau):
        """Morceau visible de la réponse du modèle, au fil de l'eau."""

    def fin_passage(self):
        """Le modèle a fini d'écrire (une réponse peut avoir deux passages : avant et après une recherche)."""

    def fin_reponse(self, sources, statistiques):
        """Réponse complète : sources consultées et chiffres de vitesse."""

    def recherche_debut(self, requete):
        """Une recherche web commence."""

    def recherche_fin(self, recherche):
        """Recherche terminée (recherche = None si elle a été annulée)."""

    def afficher_cerveau(self, fiche):
        """Contenu du cerveau : {"vide", "lignes": [(libellé, valeur)], "souvenirs", "taille", "limite", "chemin"}."""

    def afficher_aide(self, lignes):
        """Liste des commandes."""

    def alerte(self, texte):
        """Évènement qui arrive tout seul, plus tard (fin d'un minuteur). Peut venir d'un autre fil."""

    def preparation(self, en_cours):
        """Le modèle lit sa consigne en arrière-plan (True), puis a fini (False). Peut venir d'un autre fil."""

    def historique(self, messages):
        """Conversation reprise au démarrage : [(« utilisateur » ou « over », texte affiché)]."""

    def parole(self, en_cours):
        """O.V.E.R. commence (True) ou finit (False) de parler à voix haute. Peut venir d'un autre fil."""

    def parametres(self, donnees):
        """Paramètres actuels (pour le panneau Paramètres de l'application)."""

    def fiche_cerveau(self, fiche):
        """Cerveau complet, pour le panneau Paramètres de l'application."""

    def transcription(self, resultat):
        """Ce que le micro a entendu, transcrit par whisper.cpp : {ok, texte, duree, duree_son, erreur, fichier}.
        Vient d'un autre fil."""

    def annonce(self, texte, erreur=False):
        """Message qui arrive tout seul, plus tard (installation de la voix). Peut venir d'un autre fil."""
        (self.erreur if erreur else self.info)(texte)


class InterfaceParlante:
    """Enveloppe une interface : tout passe, et ce qu'O.V.E.R. dit est aussi lu à voix haute."""

    def __init__(self, interface, voix):
        self._interface = interface
        self._voix = voix

    def __getattr__(self, nom):
        return getattr(self._interface, nom)

    def dire(self, texte):
        self._interface.dire(texte)
        self._voix.dire(texte)

    def texte(self, morceau):
        self._interface.texte(morceau)
        self._voix.flux(morceau)

    def fin_passage(self):
        self._interface.fin_passage()
        self._voix.fin_flux()

    def demander(self, question, valeur_actuelle="", choix=None, aide="", extrait=""):
        self._voix.dire(question)
        return self._interface.demander(question, valeur_actuelle, choix, aide, extrait)

    def alerte(self, texte):
        self._interface.alerte(texte)
        self._voix.dire(texte)


# --- Interruption (bouton Arrêter / Ctrl+C) ------------------------------------------

ETAT_INTERRUPTION = {"possible": False}


@contextlib.contextmanager
def zone_interruptible():
    """Marque les moments où l'arrêt est permis : lecture, écriture, recherche.
    L'application n'arrête le moteur que dans ces zones, jamais au milieu d'une sauvegarde."""
    ancien = ETAT_INTERRUPTION["possible"]
    ETAT_INTERRUPTION["possible"] = True
    try:
        yield
    finally:
        ETAT_INTERRUPTION["possible"] = ancien


# --- Journal du moteur ------------------------------------------------------
# llama-cpp-python 0.3.36 range mal les niveaux de journal de llama.cpp : en mode
# silencieux, il affiche les avertissements (comme « llama_context: setting new
# yarn_attn_factor… ») et cache les erreurs. On le remplace : rien n'est affiché,
# les erreurs sont gardées pour expliquer un éventuel échec de chargement.

NIVEAU_ERREUR_GGML = 4
NIVEAU_SUITE_GGML = 5
JOURNAL_ERREURS_MOTEUR = []
_dernier_niveau_journal = [0]


@llama_cpp.llama_log_callback
def journal_moteur(niveau, texte, donnees_utilisateur):
    if niveau != NIVEAU_SUITE_GGML:
        _dernier_niveau_journal[0] = niveau
    if _dernier_niveau_journal[0] == NIVEAU_ERREUR_GGML:
        JOURNAL_ERREURS_MOTEUR.append(texte.decode("utf-8", errors="replace"))
        del JOURNAL_ERREURS_MOTEUR[:-20]


llama_cpp.llama_log_set(journal_moteur, ctypes.c_void_p(0))


# --- Moteur -----------------------------------------------------------------

# Un seul passage dans le modèle à la fois : le préchauffage en arrière-plan et les réponses
# se le partagent.
VERROU_MODELE = threading.Lock()

def nombre_coeurs_physiques():
    """Les cœurs physiques donnent la meilleure vitesse ; l'hyperthreading n'aide pas ici."""
    import subprocess
    import sys
    if sys.platform == "darwin":
        try:
            sortie = subprocess.run(["/usr/sbin/sysctl", "-n", "hw.physicalcpu"], capture_output=True, text=True,
                                    check=True)
            return max(1, int(sortie.stdout.strip()))
        except (OSError, ValueError, subprocess.CalledProcessError):
            pass
    return max(1, (os.cpu_count() or 2) // 2)


def charger_modele(chemin_modele, nb_fils):
    debut = time.perf_counter()
    JOURNAL_ERREURS_MOTEUR.clear()
    try:
        modele = Llama(
            model_path=chemin_modele,
            n_ctx=reglages.TAILLE_CONTEXTE,
            n_batch=reglages.TAILLE_LOT_LECTURE,
            n_threads=nb_fils,
            n_threads_batch=nb_fils,
            n_gpu_layers=0,  # le GPU Intel du Mac 2017 n'apporte rien : tout passe par le processeur
            # llama-cpp-python compile Metal d'office sur Mac : sans ceci, llama.cpp peut envoyer les
            # gros calculs de la lecture au GPU intégré (copie des poids à chaque fois), bien plus lent
            op_offload=False,
            verbose=False,
        )
    except Exception as erreur:
        details = "".join(JOURNAL_ERREURS_MOTEUR[-5:]).strip()
        raise RuntimeError(f"{erreur}\n{details}" if details else str(erreur)) from erreur
    return modele, time.perf_counter() - debut


def compter_tokens_texte(modele, texte):
    return len(modele.tokenize(texte.encode("utf-8"), add_bos=False, special=False))


def compter_tokens(modele, historique):
    """Poids de la conversation en tokens (estimation : +8 par message pour le gabarit, +12 par note)."""
    return sum(compter_tokens_texte(modele, message["content"] + " ".join(message.get("notes") or [])) + 8
               + 12 * len(message.get("notes") or []) for message in historique)


def elaguer_historique(modele, historique, budget, garder_fin=1, cible=None):
    """Quand la conversation dépasse le budget, oublie les plus anciens échanges jusqu'à
    redescendre à la cible (par défaut 60 % du budget). Oublier fait tout relire au modèle
    (plusieurs dizaines de secondes sur un i5) : en faisant de la place d'un coup, ça n'arrive
    qu'une fois de temps en temps, au lieu de recommencer à chaque message.
    Le message système (position 0) et les garder_fin derniers messages restent toujours.
    Renvoie le nombre d'échanges oubliés."""
    if compter_tokens(modele, historique) <= budget:
        return 0
    cible = int(budget * 0.6) if cible is None else cible
    oublies = 0
    while len(historique) > 1 + garder_fin and compter_tokens(modele, historique) > cible:
        del historique[1:3]  # le plus ancien message utilisateur et la réponse qui le suit
        oublies += 1
    return oublies


def pour_le_modele(historique):
    """Ce que le modèle reçoit : le rôle et le contenu de chaque message (pas le texte d'affichage).
    Ses réponses lui sont rendues telles qu'il les a écrites, lignes [ACTION] comprises. Le compte
    rendu des actions (« ✓ Minuteur lancé… ») arrive au début du message suivant de l'utilisateur,
    comme une note du programme : placé dans ses propres réponses, il l'imitait et inventait des
    résultats (« Note créée » avant même la confirmation, une heure fausse…).
    La date et l'heure (et le briefing du jour) arrivent de la même façon, dans les « notes » du
    message : la consigne reste ainsi identique d'un jour à l'autre, et sa lecture peut être
    gardée sur le disque (memoire_modele.py)."""
    messages = []
    resultats = []
    for message in historique:
        contenu = message["content"]
        if message["role"] == "user":
            notes = resultats + list(message.get("notes") or [])
            if notes:
                contenu = f"(Programme O.V.E.R., pas l'utilisateur : {' ; '.join(notes)})\n{contenu}"
        resultats = message.get("resultats") or [] if message["role"] == "assistant" else []
        messages.append({"role": message["role"], "content": contenu})
    return messages


def retirer_actions(texte, contenus):
    """Retire de la réponse brute les lignes [ACTION] non exécutées (pas demandées, inconnues) :
    gardées, elles apprendraient au modèle à agir sans qu'on le lui demande."""
    if not contenus:
        return texte
    lignes = []
    for ligne in texte.split("\n"):
        trouve = MOTIF_BALISE.search(ligne)
        if trouve and trouve.group(1).lower() == "action" and nettoyer_contenu(ligne[trouve.end():]) in contenus:
            ligne = ligne[:trouve.start()].rstrip()
            if not ligne:
                continue
        lignes.append(ligne)
    return "\n".join(lignes).strip()


def compte_rendu(action, resultat):
    """Ce que le modèle apprend du résultat d'une action."""
    if resultat.note == "annulée par l'utilisateur":
        return f"action {action.nom} annulée par l'utilisateur"
    if not resultat.reussie:
        return f"échec de l'action {action.nom} : {resultat.message}"
    return "✓ " + (resultat.note or resultat.message)


def balise_action(action):
    """Action → « [ACTION] minuteur : 20 min | les pâtes » (la forme que le modèle doit écrire)."""
    arguments = " | ".join(a for a in action.arguments if a)
    return f"[ACTION] {action.nom}" + (f" : {arguments}" if arguments else "")


PHRASES_ACTIONS_DIRECTES = {
    "ouvrir_app": "J'ouvre {0}.", "ouvrir_site": "J'ouvre {0}.", "volume": "Je règle le son.",
    "heure": "Je regarde l'heure.", "minuteur": "Je lance le minuteur.",
}


def visible(texte):
    """Ce que l'utilisateur a vu d'une réponse : le texte sans les lignes [CERVEAU], [RECHERCHE], [ACTION]."""
    filtre = FiltreBalises()
    return (filtre.ajouter(texte) + filtre.terminer()).strip()


def generer_reponse(modele, historique, interface):
    """Envoie la conversation au modèle et transmet la réponse à l'interface au fil de l'eau.
    Les lignes [CERVEAU], [RECHERCHE] et [ACTION] sont retirées et rangées dans le filtre.
    Renvoie (texte brut, filtre, statistiques)."""
    debut = time.perf_counter()
    debut_lecture = debut
    instant_premier_mot = None
    morceaux = []
    raison_fin = None
    interrompu = False
    affiche = False
    filtre = FiltreBalises()

    # La zone d'arrêt est ouverte à l'intérieur du try : un arrêt, même au tout premier instant,
    # est toujours rattrapé ici.
    try:
        with zone_interruptible(), VERROU_MODELE:
            # si le préchauffage n'était pas fini, on l'a attendu : ce n'est pas de la lecture
            debut_lecture = time.perf_counter()
            flux = modele.create_chat_completion(
                messages=pour_le_modele(historique),
                max_tokens=reglages.TOKENS_REPONSE_MAX,
                temperature=reglages.TEMPERATURE,
                stream=True,
            )
            for morceau in flux:
                choix = morceau["choices"][0]
                contenu = choix["delta"].get("content")
                if contenu:
                    if instant_premier_mot is None:
                        instant_premier_mot = time.perf_counter()
                    morceaux.append(contenu)
                    visible = filtre.ajouter(contenu)
                    if visible:
                        interface.texte(visible)
                        affiche = True
                if choix.get("finish_reason"):
                    raison_fin = choix["finish_reason"]
    except KeyboardInterrupt:
        interrompu = True
    reste = filtre.terminer()
    if reste:
        interface.texte(reste)
        affiche = True
    interface.fin_passage()

    fin = time.perf_counter()
    # gardé tel quel (espaces compris) : rendu au modèle à l'identique, il n'aura pas à le relire
    texte = "".join(morceaux)
    debut_ecriture = instant_premier_mot or fin
    statistiques = {
        "duree_preparation": debut_lecture - debut,
        "duree_lecture": max(0.0, debut_ecriture - debut_lecture),
        "duree_ecriture": fin - debut_ecriture,
        "nb_tokens": compter_tokens_texte(modele, texte) if texte.strip() else 0,
        "coupee": raison_fin == "length",
        "interrompu": interrompu,
        "affiche": affiche,
    }
    return texte, filtre, statistiques


# --- Consigne ---------------------------------------------------------------

JOURS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
MOIS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet",
        "août", "septembre", "octobre", "novembre", "décembre")

CONSIGNE_SANS_APPRENTISSAGE = (
    "Tu ne peux pas enregistrer toi-même de nouvelles informations dans ton cerveau : "
    "si l'utilisateur te demande de retenir quelque chose, dis-lui de taper /cerveau ajoute "
    "suivi de ce qu'il faut retenir."
)


def date_en_francais(jour):
    numero = "1er" if jour.day == 1 else str(jour.day)
    return f"{JOURS[jour.weekday()]} {numero} {MOIS[jour.month - 1]} {jour.year}"


def regles_de_base():
    # Pas de date ici : elle change chaque jour et obligerait à relire toute la consigne.
    # Le programme la donne au début des messages (voir Session.notes_du_message).
    texte = (
        "Réponds en français, clairement ; sois concis sauf demande contraire. Si tu ne sais pas, "
        "dis-le au lieu d'inventer. La date et l'heure te sont données par le programme dans les messages."
    )
    if not reglages.RECHERCHE_WEB:
        texte += " Tu n'as pas accès au web : si une question en dépend, dis-le."
    return texte


def construire_consigne(cerveau, nom_interface):
    parties = [
        identite.texte_pour_consigne(reglages.RECHERCHE_WEB, reglages.APPRENTISSAGE_AUTO,
                                     reglages.ACTIONS_MAC, nom_interface),
        regles_de_base(),
    ]
    if reglages.RECHERCHE_WEB:
        parties.append(web.consigne_recherche())
    if reglages.ACTIONS_MAC:
        parties.append(actions.consigne_actions())
    parties.append(cerveau.consigne_apprentissage() if reglages.APPRENTISSAGE_AUTO else CONSIGNE_SANS_APPRENTISSAGE)
    connaissances = cerveau.texte_pour_consigne()
    if connaissances:
        parties.append(connaissances)
    return "\n\n".join(parties)


LIGNES_AIDE_ACTIONS = [
    ("ouvre Safari", "ouvre une app ou un site, tout de suite (aussi : volume 30, quelle heure est-il, minuteur 10 min)"),
    ("rappels", "demande-le simplement : « rappelle-moi demain à 9h d'appeler le garage »"),
]
LIGNES_AIDE_WEB = [
    ("/web requête", "cherche sur le web tout de suite (ou écris « cherche … »)"),
]
LIGNES_AIDE = [
    ("/cerveau", "ce que je sais de toi  ·  /cerveau aide : toutes les commandes du cerveau"),
    ("/cerveau ajoute âge : 30 ans", "ajoute une info ou un souvenir (« /cerveau ajoute j'ai un chat, Filou »)"),
    ("/cerveau modifie ville Paris", "change une info : prénom, activité, ville, passions, réponses"),
    ("/cerveau oublie 2", "retire le souvenir n° 2 (ou une info : /cerveau oublie ville)"),
    ("/effacer", "oublie la conversation (le cerveau est gardé)"),
    ("/quitter", "ferme le programme"),
]
LIGNES_AIDE_CERVEAU = [
    ("/cerveau", "affiche ce que je sais de toi (les souvenirs sont numérotés)"),
    ("/cerveau ajoute texte", "nouveau souvenir  ·  « /cerveau ajoute ville : Lyon » remplit la fiche"),
    ("/cerveau modifie info valeur", "change une info de la fiche, ou un souvenir : /cerveau modifie 2 nouveau texte"),
    ("/cerveau oublie n", "retire le souvenir n (plusieurs : 1 3)  ·  une info : oublie ville  ·  un mot : oublie garage"),
    ("/cerveau vide", "efface tout (je demande confirmation)"),
    ("/cerveau questions", "repasse les questions d'accueil"),
    ("Infos de la fiche", "prénom, relation (tu/vous), activité, ville, passions, réponses (courtes/détaillées)"),
    ("En anglais aussi", "add, set, delete, reset  ·  et sans le « / » : « cerveau add âge : 30 ans »"),
]

# Sous-commandes de /cerveau : nom interne → mots acceptés (français, anglais, raccourcis)
SOUS_COMMANDES_CERVEAU = {
    "voir": ("voir", "affiche", "liste", "show", "list"),
    "ajoute": ("ajoute", "ajouter", "ajout", "add", "+", "retiens", "retenir", "new", "nouveau"),
    "modifie": ("modifie", "modifier", "change", "changer", "corrige", "corriger", "set", "edit", "mets"),
    "oublie": ("oublie", "oublier", "supprime", "supprimer", "retire", "retirer", "efface", "effacer",
               "delete", "del", "remove", "rm", "-"),
    "vide": ("vide", "vider", "reset", "clear", "raz"),
    "questions": ("questions", "question", "quiz", "accueil"),
    "aide": ("aide", "help", "?"),
}


def sous_commande_cerveau(mot):
    mot = mot.lower().strip(" :")
    for nom, mots in SOUS_COMMANDES_CERVEAU.items():
        if mot in mots:
            return nom
    return None


# « tais-toi », « chut » : O.V.E.R. arrête de parler (« silence » reste « coupe le son du Mac »)
MOTIF_TAIRE = re.compile(r"^(?:tais[- ]?toi|taisez[- ]vous|chut+|stop|arr[êe]te de parler|ferme[- ]la)[\s.!]*$",
                         re.IGNORECASE)
MOTIF_BRIEFING = re.compile(r"^(?:(?:mon |le |fais(?:-moi)? (?:le |mon )?)?briefing|le point du jour)[\s.!?]*$",
                            re.IGNORECASE)
VERBES_DEMANDE = {"donne", "donnes", "donner", "fais", "fait", "faire", "lance", "montre", "dis", "lis", "affiche",
                  "redonne", "refais", "veux", "voudrais", "aimerais", "peux", "voir", "quel", "quoi"}
MOTS_REGLAGE = ("desactiv", "activ", "regl", "param", "coupe", "arret", "supprim", "enleve", "change", "modifi",
                "pourquoi", "comment", "c est quoi", "qu est ce", "explique", "definition", "quand")


def est_demande_de_briefing(saisie):
    """« briefing », « donne-moi le brinfing du jour », « fais le point » → True (fautes de frappe
    comprises), sans le confondre avec « désactive le briefing » ou « c'est quoi un briefing ? »."""
    if MOTIF_BRIEFING.match(saisie):
        return True
    simple = forme_nom(saisie)
    mots = simple.split()
    if not mots or len(mots) > 10 or any(m in simple for m in MOTS_REGLAGE):
        return False
    import difflib
    proche = any(m == "brief" or difflib.SequenceMatcher(None, m, "briefing").ratio() >= 0.75 for m in mots)
    point = "point du jour" in simple or re.search(r"\bfais? (moi )?le point\b", simple) is not None
    if not (proche or point):
        return False
    return len(mots) <= 4 or point or bool(VERBES_DEMANDE & set(mots)) or "du jour" in simple \
        or "du matin" in simple or "aujourd hui" in simple

LIGNES_AIDE_REGLAGES = [
    ("/paramètres", "tous les paramètres : briefing, voix, web, actions, mémoire, créativité… (application : ⌘,)"),
    ("/voix", "lire les réponses à voix haute (voix de macOS) : on / off  ·  /voix liste, /voix thomas, "
              "/voix test, /voix plus (en avoir d'autres)"),
    ("/écoute", "reconnaissance du micro sur le Mac (whisper.cpp) : état  ·  /écoute installer, /écoute précis, "
                "/écoute rapide, /écoute fichier.wav"),
    ("/démarrage", "ouvrir O.V.E.R. avec ta session : /démarrage oui | non | discret  ·  arrière-plan : "
                   "/paramètres arrière-plan oui"),
    ("/tais-toi", "arrête de parler (ou écris « chut », ou un nouveau message)"),
    ("/briefing", "le point du jour, tout de suite (ou écris « briefing »)"),
]


# --- La session de discussion ------------------------------------------------

VERSION_CONVERSATION = 2   # format de conversation.json

class Session:
    """Tout ce qui vit pendant une discussion : modèle, cerveau, historique."""

    def __init__(self, modele, cerveau, interface, chemin_conversation=None, parametres=None, sauvegarde=None,
                 tokens_recharges=0):
        self.modele = modele
        self.cerveau = cerveau
        self.parametres = parametres or Parametres.charger(
            os.path.join(os.path.dirname(os.path.abspath(cerveau.chemin)), "parametres.json"))
        self.voix = Voix(self.parametres, signaler=interface.parole)
        self.ecoute = module_ecoute.MoteurEcoute()      # micro de l'application → texte, sur le Mac
        self.interface_brute = interface
        self.interface = InterfaceParlante(interface, self.voix)   # ce qu'il dit est aussi lu, si la voix est active
        self.sauvegarde = sauvegarde    # mémoire du modèle sur le disque (memoire_modele.Sauvegarde) ou None
        self.chemin_conversation = chemin_conversation
        self.notes_en_attente = []      # notes du programme pour le prochain message (briefing…)
        self.derniere_heure_donnee = None
        self._verrou_prechauffage = threading.Lock()
        self._prechauffage_demande = False
        self._prechauffage_actif = False
        self.tokens_recharges = tokens_recharges   # tokens retrouvés dans la mémoire enregistrée sur le disque
        self.budget = reglages.TAILLE_CONTEXTE - reglages.TOKENS_REPONSE_MAX - reglages.MARGE_SECURITE
        self.limite_cerveau = self.budget // 2  # le cerveau ne doit pas prendre plus de la moitié de la mémoire
        self.historique = [{"role": "system", "content": self.consigne()}]
        # Ce qui est appris en discutant est enregistré tout de suite, mais la consigne n'est
        # mise à jour qu'au prochain /effacer ou élagage : l'information est déjà dans la
        # conversation, et changer la consigne obligerait le modèle à tout relire (lent sur un i5).
        self.consigne_en_retard = False
        # minuteurs d'O.V.E.R. : gardés dans parametres.json, retrouvés au prochain lancement
        actions.AU_CHANGEMENT_MINUTEURS = self.garder_minuteurs
        self.nb_repris = 0
        self.probleme_reprise = ""
        self.note_reprise = ""
        if reglages.GARDER_CONVERSATION and chemin_conversation:
            self.reprendre_conversation()

    # --- outils ---

    def consigne(self):
        return construire_consigne(self.cerveau, self.interface.nom)

    def tokens(self, texte):
        return compter_tokens_texte(self.modele, texte)

    def rafraichir_consigne(self):
        self.historique[0]["content"] = self.consigne()
        self.consigne_en_retard = False

    def prechauffer(self):
        """Fait lire au modèle la consigne et la conversation en arrière-plan, pendant que
        l'utilisateur écrit : la réponse suivante n'aura plus que son message à lire.
        Sur un i5 2 cœurs, la consigne seule représente plusieurs dizaines de secondes de lecture ;
        avec la mémoire rechargée du disque, il ne lit que ce qui a changé depuis la dernière fois.
        Plusieurs demandes rapprochées (réglages du cerveau…) ne donnent qu'une seule lecture."""
        with self._verrou_prechauffage:
            self._prechauffage_demande = True
            if self._prechauffage_actif:
                return
            self._prechauffage_actif = True
        self.interface.preparation(True)

        def lire():
            while True:
                with self._verrou_prechauffage:
                    if not self._prechauffage_demande:
                        self._prechauffage_actif = False
                        break
                    self._prechauffage_demande = False
                with VERROU_MODELE:
                    historique = list(self.historique)
                    if historique[-1]["role"] != "user":
                        historique.append({"role": "user", "content": "."})
                    try:
                        self.modele.create_chat_completion(messages=pour_le_modele(historique),
                                                           max_tokens=1, temperature=0.0)
                    except Exception:
                        pass  # simple optimisation : en cas d'échec, la vraie réponse relira tout
            self.interface.preparation(False)
            if self.sauvegarde:
                self.sauvegarde.demander()

        threading.Thread(target=lire, name="prechauffage", daemon=True).start()

    def garder_minuteurs(self, liste):
        """Liste des minuteurs en cours, rangée dans parametres.json (lisible par toi seul)."""
        if liste or self.parametres.etat.get("minuteurs"):
            self.parametres.etat["minuteurs"] = liste
            try:
                self.parametres.sauvegarder()
            except OSError:
                pass

    def reprendre_minuteurs(self):
        """Au lancement : relance les minuteurs encore en cours, annonce ceux qui ont sonné pendant la fermeture."""
        gardes = self.parametres.etat.get("minuteurs")
        if not isinstance(gardes, list) or not gardes:
            return
        maintenant = datetime.datetime.now()
        self.parametres.etat["minuteurs"] = []
        relances, passes = 0, []
        for garde in gardes[:20]:
            try:
                fin = datetime.datetime.fromisoformat(str(garde["fin"]))
                motif = str(garde.get("motif") or "")[:40]
                secondes = float(garde.get("secondes") or 0)
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
            if fin > maintenant:
                actions.armer_minuteur(fin, motif, secondes, self.interface.alerte)
                relances += 1
            elif maintenant - fin < datetime.timedelta(days=1):
                passes.append(f"{actions.heure_fr(fin)}{' (' + motif + ')' if motif else ''}")
        if passes:
            self.interface.info("Pendant que j'étais fermé, minuteur terminé à " + ", ".join(passes) + ".")
        if relances:
            self.interface.info(f"{relances} minuteur(s) en cours retrouvé(s) : ils sonneront à l'heure prévue.")
        self.garder_minuteurs(actions.minuteurs_en_cours())

    def terminer(self):
        """Fin du programme : se taire et garder la dernière lecture du modèle sur le disque."""
        self.voix.fermer()
        if self.sauvegarde and self.sauvegarde.actif:
            with VERROU_MODELE:
                self.sauvegarde.enregistrer_maintenant()

    def notes_du_message(self):
        """Notes du programme jointes au message de l'utilisateur : la date et l'heure (au premier
        message, quand le jour change ou toutes les 20 minutes), et ce qui attendait (briefing…)."""
        notes = list(self.notes_en_attente)
        self.notes_en_attente = []
        maintenant = datetime.datetime.now()
        derniere = self.derniere_heure_donnee
        if derniere is None or derniere.date() != maintenant.date() or maintenant - derniere >= datetime.timedelta(minutes=20):
            notes.insert(0, f"nous sommes le {date_en_francais(maintenant)}, il est {actions.heure_fr(maintenant)}")
            self.derniere_heure_donnee = maintenant
        return notes

    # --- conversation gardée d'un lancement à l'autre ---

    def reprendre_conversation(self):
        """Reprend la fin de la conversation précédente (au plus REPRISE_MAX_TOKENS)."""
        try:
            with open(self.chemin_conversation, "r", encoding="utf-8") as fichier:
                contenu = fichier.read()
            if not contenu.strip():
                return   # conversation vidée : on repart de zéro, sans alerte
            donnees = json.loads(contenu)
            messages = donnees.get("messages", []) if isinstance(donnees, dict) else []
        except FileNotFoundError:
            return
        except (OSError, ValueError):
            self.probleme_reprise = "La conversation précédente était illisible : je repars de zéro."
            return
        if isinstance(donnees, dict) and donnees.get("version") != VERSION_CONVERSATION:
            # avant la 0.7, les comptes rendus d'actions étaient mêlés aux réponses du modèle,
            # qui les imitait : on ne reprend pas ces anciennes conversations
            self.note_reprise = "Nouvelle version : je repars d'une conversation vierge (mon cerveau est gardé)."
            return
        propres = []
        for message in messages:
            if not (isinstance(message, dict) and isinstance(message.get("content"), str)):
                break
            attendu = "user" if len(propres) % 2 == 0 else "assistant"
            if message.get("role") != attendu:
                break
            propre = {"role": attendu, "content": message["content"]}
            if "affichage" in message and (message["affichage"] is None or isinstance(message["affichage"], str)):
                propre["affichage"] = message["affichage"]
            for cle in ("resultats", "notes"):   # comptes rendus d'actions (réponses), notes du programme (messages)
                valeur = message.get(cle)
                if isinstance(valeur, list) and valeur and all(isinstance(r, str) for r in valeur):
                    propre[cle] = valeur
            if message.get("web") is True:     # le message contenait des résultats du web
                propre["web"] = True
            propres.append(propre)
        if len(propres) % 2:
            propres.pop()   # une question restée sans réponse
        # Mémoire rechargée du disque : on reprend la conversation telle quelle, car c'est exactement
        # ce que le modèle avait lu (rien à relire). Sinon, seulement la fin : tout serait à relire.
        if self.tokens_recharges:
            # tout ce qui tenait dans la mémoire du modèle (la place sera faite au prochain message si besoin)
            limite = reglages.TAILLE_CONTEXTE - reglages.MARGE_SECURITE
            while propres and compter_tokens(self.modele, [self.historique[0]] + propres) > limite:
                del propres[0:2]
        else:
            while propres and compter_tokens(self.modele, propres) > reglages.REPRISE_MAX_TOKENS:
                del propres[0:2]
        self.historique.extend(propres)
        self.nb_repris = sum(1 for message in propres if message["role"] == "user" and message.get("affichage", "") is not None)

    def sauver_conversation(self):
        """Enregistre la conversation (écriture sûre, lisible par toi seul)."""
        if not (reglages.GARDER_CONVERSATION and self.chemin_conversation):
            return
        donnees = {
            "version": VERSION_CONVERSATION,
            "enregistre_le": datetime.datetime.now().isoformat(timespec="seconds"),
            "messages": self.historique[1:],
        }
        dossier = os.path.dirname(os.path.abspath(self.chemin_conversation))
        try:
            descripteur, temporaire = tempfile.mkstemp(prefix=".conversation-", suffix=".tmp", dir=dossier)
            with os.fdopen(descripteur, "w", encoding="utf-8") as fichier:
                json.dump(donnees, fichier, ensure_ascii=False, indent=1)
            os.chmod(temporaire, 0o600)
            os.replace(temporaire, self.chemin_conversation)
        except OSError as erreur:
            self.interface.erreur(f"Impossible d'enregistrer la conversation : {erreur}")

    def effacer_conversation(self):
        del self.historique[1:]
        self.derniere_heure_donnee = None
        if self.sauvegarde:
            self.sauvegarde.oublier()   # elle contenait la conversation
        if self.chemin_conversation and os.path.exists(self.chemin_conversation):
            try:
                os.remove(self.chemin_conversation)
            except OSError:
                pass

    def messages_affichables(self):
        """La conversation reprise, telle que l'utilisateur l'avait vue."""
        liste = []
        for message in self.historique[1:]:
            if message["role"] == "user":
                texte = message.get("affichage", message["content"])
                if texte:
                    liste.append(("utilisateur", texte))
            else:
                texte = message.get("affichage")
                texte = visible(message["content"]) if texte is None and "affichage" not in message else texte
                if texte:
                    liste.append(("over", texte))
        return liste

    # --- actions sur le Mac ---

    def executer_action(self, action, confirmer=False):
        """Demande confirmation si besoin (toujours si confirmer est vrai), puis exécute. Renvoie un actions.Resultat."""
        question = actions.question_de_confirmation(action, forcer=confirmer)
        if question:
            texte_question, extrait = question
            reponse = self.interface.demander(texte_question, choix=[("Oui", "o"), ("Non", "n")], extrait=extrait)
            decision, _ = interpreter_confirmation(reponse or "")
            if decision != "oui":
                self.interface.info("D'accord, je ne fais rien.")
                return actions.Resultat(False, "Action annulée.", "annulée par l'utilisateur")
        try:
            with zone_interruptible():
                return actions.executer(action, signaler=self.interface.alerte)
        except KeyboardInterrupt:
            return actions.Resultat(False, "Action interrompue.", "interrompue")

    def annoncer(self, resultat):
        if resultat.note == "annulée par l'utilisateur":
            return
        if not resultat.reussie:
            self.interface.erreur(resultat.message)
        elif resultat.contenu:
            self.interface.dire(resultat.message)
        else:
            self.interface.info("✓ " + resultat.message)

    def action_directe(self, saisie, action):
        """Demande simple (« ouvre Safari ») : exécutée tout de suite, sans passer par le modèle."""
        resultat = self.executer_action(action)
        if resultat.reussie and not resultat.contenu:
            self.interface.dire(resultat.message)
        else:
            self.annoncer(resultat)
        # le modèle voit l'échange sous la forme qu'il doit suivre : une phrase, la ligne [ACTION],
        # et le résultat donné par le programme au message suivant
        phrase = PHRASES_ACTIONS_DIRECTES.get(action.nom, "Je m'en occupe.").format(action.argument(0))
        self.historique.append({"role": "user", "content": saisie, "affichage": saisie})
        self.historique.append({"role": "assistant", "content": f"{phrase}\n{balise_action(action)}",
                                "affichage": resultat.message, "resultats": [compte_rendu(action, resultat)]})
        self.sauver_conversation()

    def cerveau_trop_gros(self):
        return self.tokens(self.cerveau.texte_pour_consigne()) > self.limite_cerveau

    def enregistrer_cerveau(self):
        try:
            self.cerveau.sauvegarder()
            return True
        except OSError as erreur:
            self.interface.erreur(f"Impossible d'enregistrer le cerveau : {erreur}")
            return False

    def poser_question_cerveau(self, question, valeur_actuelle="", choix=None):
        """Questions du cerveau : les choix proposés, plus « Passer » et « Plus tard »."""
        boutons = list(choix or []) + [("Passer", ""), ("Plus tard", None)]
        reponse = self.interface.demander(question, valeur_actuelle, boutons)
        if reponse is not None and reponse.strip().startswith("/"):
            return None  # /plus-tard ou toute autre commande : on arrête les questions
        return reponse

    def faire_de_la_place(self, garder_fin):
        if self.consigne_en_retard and compter_tokens(self.modele, self.historique) > self.budget:
            self.rafraichir_consigne()  # le modèle va tout relire de toute façon : on en profite
        oublies = elaguer_historique(self.modele, self.historique, self.budget, garder_fin)
        if oublies:
            self.interface.info(f"(mémoire pleine : {oublies} ancien(s) échange(s) oublié(s))")

    def resultats_qui_tiennent(self, recherche, prefixe, autres_messages):
        """Les résultats web mis en forme, raccourcis si besoin pour tenir dans la mémoire du modèle."""
        taille_max = reglages.TAILLE_RESULTATS
        for taille in (taille_max, taille_max * 2 // 3, taille_max // 3):
            contenu = prefixe + recherche.pour_modele(taille)
            essai = [self.historique[0]] + autres_messages + [{"role": "user", "content": contenu}]
            if compter_tokens(self.modele, essai) <= self.budget:
                return contenu
        return None

    def lancer_recherche(self, requete, message_utilisateur=None):
        """Cherche sur le web. Renvoie None si l'utilisateur annule.
        message_utilisateur : donné quand la recherche est demandée par le modèle. Une adresse qu'il a écrite
        n'est lue directement que si l'utilisateur a cité ce site ; sinon seul le nom du site part, vers le
        moteur de recherche (une page piégée ne peut pas faire envoyer des données dans une adresse)."""
        lecture_directe = True
        if message_utilisateur is not None and web.est_adresse(requete) \
                and not web.adresse_citee(requete, message_utilisateur):
            requete, lecture_directe = web.hote(requete) or requete, False
        self.interface.recherche_debut(requete)
        debut = time.perf_counter()
        try:
            with zone_interruptible():
                recherche = web.rechercher(
                    requete, nombre=reglages.NOMBRE_RESULTATS, max_page=reglages.TAILLE_PAGE,
                    adresse_searxng=reglages.ADRESSE_SEARXNG, lecture_directe=lecture_directe)
        except KeyboardInterrupt:
            self.interface.recherche_fin(None)
            return None
        recherche.duree = time.perf_counter() - debut
        self.interface.recherche_fin(recherche)
        return recherche

    # --- démarrage ---

    def demarrer(self):
        """Accueil : alerte éventuelle, questions si le cerveau est vide, sinon bonjour."""
        cerveau = self.cerveau
        self.verifier_voix()
        self.verifier_demarrage()
        self.reprendre_minuteurs()
        if cerveau.alerte:
            self.interface.erreur(cerveau.alerte)
        if self.parametres.alerte:
            self.interface.erreur(self.parametres.alerte)
        if self.tokens_recharges:
            self.interface.info(f"Mémoire du modèle rechargée : {self.tokens_recharges} tokens déjà lus, "
                                "pas besoin de tout relire.")
        if self.probleme_reprise:
            self.interface.erreur(self.probleme_reprise)
        if self.note_reprise:
            self.interface.info(self.note_reprise)
        if self.nb_repris:
            self.interface.historique(self.messages_affichables())
            self.interface.info(f"Conversation reprise ({self.nb_repris} échange(s)) · /effacer pour repartir de zéro")
        if cerveau.est_vide():
            self.interface.dire("Mon cerveau est vide : je ne sais encore rien de la personne qui me parle. "
                                "Faisons connaissance en quelques questions.")
            if self.interface.astuces_clavier:
                self.interface.info("Entrée : passer une question  ·  /plus-tard : remettre ça au prochain lancement")
            if questionnaire(cerveau, self.poser_question_cerveau, self.interface.dire):
                self.enregistrer_cerveau()
                self.rafraichir_consigne()
            else:
                self.interface.info("Pas de souci : je reposerai les questions au prochain lancement.")
            self.prechauffer()
        else:
            self.prechauffer()   # la lecture démarre tout de suite, pendant le briefing éventuel
            if self.parametres["briefing"] == "ouverture":
                self.faire_briefing()
            elif cerveau.prenom():
                self.interface.dire(f"Bonjour {cerveau.prenom()} !")
        if self.cerveau_trop_gros():
            self.interface.erreur("Mon cerveau est très chargé et laisse peu de place à la conversation. "
                                  "Fais du tri avec /cerveau oublie.")

    def faire_briefing(self):
        """Le point du jour (date, météo, agenda, rappels), assemblé par le programme."""
        self.interface.info("Je prépare ton briefing…")
        try:
            lignes = briefing.composer(self.parametres, self.cerveau)
        except Exception as erreur:   # le briefing ne doit jamais empêcher de discuter
            self.interface.erreur(f"Briefing impossible : {erreur}")
            return
        self.interface.dire("\n".join(lignes))
        # le modèle le saura au prochain message, pour pouvoir en parler (sans le répéter)
        self.notes_en_attente.append("briefing du jour déjà affiché juste avant, ne le répète pas ; "
                                     "sers-t'en seulement si on te pose une question dessus : " + " ".join(lignes[1:]))
        self.parametres.noter_briefing()

    # --- un tour de parole ---

    def recevoir(self, saisie):
        """Point d'entrée : un message ou une commande. Renvoie False pour quitter."""
        saisie = saisie.strip()
        if not saisie:
            return True
        if len(saisie) > reglages.LONGUEUR_MAX_MESSAGE:
            # vérifié avant tout : un texte géant sans espace bloquerait le découpage en tokens plusieurs minutes
            taille, maximum = (f"{n:,}".replace(",", " ") for n in (len(saisie), reglages.LONGUEUR_MAX_MESSAGE))
            self.interface.erreur(f"Message trop long ({taille} caractères, {maximum} au plus) : raccourcis-le.")
            return True
        self.voix.taire()   # un nouveau message coupe la parole
        if saisie.startswith("/"):
            return self.commande(saisie)
        if MOTIF_TAIRE.match(saisie):
            self.interface_brute.info("D'accord, je me tais.")
            return True
        if est_demande_de_briefing(saisie):
            self.faire_briefing()   # assemblé par le programme : une seule fois, et rien d'inventé
            return True
        if self.parametres["briefing"] == "quotidien" and not self.parametres.briefing_deja_fait_aujourdhui():
            self.faire_briefing()   # premier message de la journée
        mots = saisie.split(maxsplit=1)
        if mots[0].lower() == "cerveau" and (len(mots) == 1 or sous_commande_cerveau(mots[1].split()[0])):
            return self.commande("/" + saisie)   # « cerveau add âge : 30 ans » marche aussi sans le « / »
        if reglages.ACTIONS_MAC:
            action = actions.extraire_commande_directe(saisie)
            if action:
                self.action_directe(saisie, action)
                return True
        requete = web.extraire_demande_recherche(saisie) if reglages.RECHERCHE_WEB else None
        self.repondre(saisie, requete_directe=requete)
        return True

    def repondre(self, saisie, requete_directe=None):
        """Répond à un message. Avec requete_directe, la recherche web est faite avant la réponse ;
        sinon le modèle peut en demander une par une ligne [RECHERCHE]."""
        interface = self.interface
        taille_avant = len(self.historique)
        duree_recherche = 0.0
        sources = []

        contenu = saisie
        if requete_directe:
            recherche = self.lancer_recherche(requete_directe)
            if recherche is None:
                return
            contenu = self.resultats_qui_tiennent(recherche, saisie + "\n\n", [])
            if contenu is None:
                interface.erreur("Message trop long pour la mémoire du modèle. Raccourcis-le.")
                return
            duree_recherche = recherche.duree
            sources = recherche.sources()

        message = {"role": "user", "content": contenu, "affichage": saisie}
        if requete_directe:
            message["web"] = True
        if compter_tokens(self.modele, [self.historique[0], message]) > self.budget:
            # vérifié avant d'élaguer : un message refusé ne fait rien oublier
            interface.erreur(f"Message trop long pour la mémoire du modèle "
                             f"(environ {self.budget} tokens maximum). Raccourcis-le.")
            return
        notes = self.notes_du_message()
        if notes:
            message["notes"] = notes
        self.historique.append(message)
        self.faire_de_la_place(garder_fin=1)

        reponse_avant_recherche = None   # 1er passage du modèle, quand il a demandé une recherche
        try:
            texte, filtre, statistiques = generer_reponse(self.modele, self.historique, interface)
            faits = filtre.contenus("cerveau")
            demandes = filtre.contenus("recherche")
            actions_demandees = filtre.contenus("action")

            if reglages.RECHERCHE_WEB and not requete_directe and demandes and not statistiques["interrompu"]:
                # Le modèle demande une recherche : on la fait, puis il répond avec les résultats.
                reponse_avant_recherche = {"role": "assistant", "content": texte, "affichage": visible(texte)}
                self.historique.append(reponse_avant_recherche)
                recherche = self.lancer_recherche(demandes[0], message_utilisateur=saisie)
                if recherche is None:
                    del self.historique[taille_avant:]
                    return
                # place calculée avec la question et la demande seulement : les vieux échanges seront élagués
                contenu = self.resultats_qui_tiennent(recherche, "", self.historique[-2:])
                if contenu is None:
                    contenu = recherche.pour_modele(reglages.TAILLE_RESULTATS // 3)
                self.historique.append({"role": "user", "content": contenu, "affichage": None, "web": True})
                self.faire_de_la_place(garder_fin=3)
                premier = statistiques
                texte, filtre, statistiques = generer_reponse(self.modele, self.historique, interface)
                statistiques["duree_lecture"] += premier["duree_lecture"] + premier["duree_ecriture"]
                statistiques["duree_preparation"] += premier["duree_preparation"]
                duree_recherche = recherche.duree
                sources = recherche.sources()
                faits += [fait for fait in filtre.contenus("cerveau") if fait not in faits]
                actions_demandees += [a for a in filtre.contenus("action") if a not in actions_demandees]
        except Exception as erreur:  # erreur du moteur : on annule ce tour sans quitter
            interface.fin_passage()
            interface.erreur(f"Erreur du moteur : {erreur}")
            del self.historique[taille_avant:]
            return

        if not statistiques["affiche"] and not statistiques["interrompu"] and not actions_demandees:
            interface.dire("Je n'ai pas de réponse à donner.")
        if texte.strip():
            self.historique.append({"role": "assistant", "content": texte, "affichage": visible(texte)})
        else:
            del self.historique[taille_avant:]  # rien n'a été répondu : on annule le tour
        statistiques["duree_recherche"] = duree_recherche
        interface.fin_reponse(sources, statistiques)
        if statistiques["interrompu"]:
            self.voix.taire()
        if self.sauvegarde:
            self.sauvegarde.demander()   # la lecture de cet échange sera gardée pour le prochain lancement

        complete = not (statistiques["interrompu"] or statistiques["coupee"])
        if actions_demandees and texte.strip():
            # actions demandées par le modèle, seulement si le message de l'utilisateur les demande vraiment ;
            # tant que des résultats du web sont dans sa mémoire, une page a pu les lui souffler : on demande
            contexte_web = any(message.get("web") for message in self.historique)
            comptes_rendus, affiches, ecartees = [], [], []
            for numero, contenu_action in enumerate(actions_demandees):
                action = actions.analyser(contenu_action)
                if (not (reglages.ACTIONS_MAC and complete) or not action
                        or numero >= reglages.ACTIONS_MAX_PAR_REPONSE or not actions.est_demandee(action, saisie)):
                    ecartees.append(contenu_action)
                    continue
                site_non_cite = action.nom == "ouvrir_site" and not web.adresse_citee(action.argument(0), saisie)
                resultat = self.executer_action(action, confirmer=contexte_web or site_non_cite)
                self.annoncer(resultat)
                comptes_rendus.append(compte_rendu(action, resultat))
                if resultat.note != "annulée par l'utilisateur":
                    affiches.append(resultat.message if resultat.reussie else "⚠ " + resultat.message)
            dernier = self.historique[-1]
            dernier["content"] = retirer_actions(dernier["content"], ecartees) or dernier["content"]
            if reponse_avant_recherche is not None and reponse_avant_recherche is not dernier:
                reponse_avant_recherche["content"] = (retirer_actions(reponse_avant_recherche["content"], ecartees)
                                                      or reponse_avant_recherche["content"])
            if comptes_rendus:
                # le modèle saura ce qui s'est vraiment passé, au début du prochain message
                dernier["resultats"] = comptes_rendus
                dernier["affichage"] = "\n\n".join(filter(None, [dernier.get("affichage")] + affiches))

        if reglages.APPRENTISSAGE_AUTO and complete:
            # réponse arrêtée ou coupée : la dernière information est peut-être incomplète, on ne propose rien
            faits = [nettoyer_fait(fait) for fait in faits]
            faits = [fait for fait in faits
                     if fait and fait_vient_de_l_utilisateur(fait, saisie, self.cerveau.prenom())]
            if self.proposer_faits(faits):
                self.consigne_en_retard = True
        self.sauver_conversation()

    def proposer_faits(self, faits):
        """Demande confirmation pour chaque information repérée par le modèle.
        Renvoie True si le cerveau a changé."""
        cerveau = self.cerveau
        a_change = False
        aide = "o : oui  ·  n : non  ·  ou écris ta version" if cerveau.tutoie() \
            else "o : oui  ·  n : non  ·  ou écrivez votre version"
        nouveaux = [fait for fait in faits if not cerveau.deja_connu(fait)]
        for fait in nouveaux[:reglages.FAITS_MAX_PAR_REPONSE]:
            reponse = self.interface.demander(
                "Je range ça dans mon cerveau ?", choix=[("Oui", "o"), ("Non", "n")], aide=aide, extrait=fait)
            decision, correction = interpreter_confirmation(reponse or "")
            if decision == "non":
                self.interface.info("D'accord, je n'enregistre rien.")
                continue
            texte = correction if decision == "corrige" else fait
            if cerveau.deja_connu(texte):
                self.interface.dire("Je le savais déjà.")
                continue
            cerveau.ajouter_souvenir(texte, source="conversation")
            if self.cerveau_trop_gros():
                cerveau.souvenirs.pop()
                self.interface.erreur("Mon cerveau est plein : fais de la place avec /cerveau oublie, puis utilise /cerveau ajoute.")
                continue
            if self.enregistrer_cerveau():
                self.interface.dire("C'est rangé.")
                a_change = True
            else:
                cerveau.souvenirs.pop()
        return a_change

    # --- commandes ---

    def fiche_cerveau(self):
        cerveau = self.cerveau
        return {
            "vide": cerveau.est_vide(),
            "lignes": cerveau.lignes_affichage(),
            "souvenirs": [souvenir["texte"] for souvenir in cerveau.souvenirs],
            "taille": self.tokens(cerveau.texte_pour_consigne()),
            "limite": self.limite_cerveau,
            "chemin": cerveau.chemin,
        }

    # --- commandes du cerveau ---

    def cerveau_a_change(self, message):
        """Enregistre, met la consigne à jour (relue en arrière-plan) et confirme."""
        if self.enregistrer_cerveau():
            self.rafraichir_consigne()
            self.prechauffer()
            self.interface.dire(message)
            return True
        return False

    def commande_cerveau(self, argument):
        """/cerveau [voir | ajoute | modifie | oublie | vide | questions | aide] …"""
        cerveau = self.cerveau
        interface = self.interface
        mot, _, reste = argument.strip().partition(" ")
        action = sous_commande_cerveau(mot) if mot else "voir"
        reste = reste.strip()
        if action is None:
            interface.erreur(f"Je ne connais pas « /cerveau {mot} ». Tape /cerveau aide.")
            return

        if action == "voir":
            interface.afficher_cerveau(self.fiche_cerveau())

        elif action == "aide":
            interface.afficher_aide(LIGNES_AIDE_CERVEAU)

        elif action == "ajoute":
            if not reste:
                interface.erreur("Écris ce qu'il faut retenir : /cerveau ajoute j'ai un chat qui s'appelle Filou")
                return
            cle, separateur, valeur = reste.partition(":") if ":" in reste else reste.partition("=")
            champ = trouver_champ(cle) if separateur and valeur.strip() else None
            if champ:
                ancienne = cerveau.utilisateur.get(champ)
                rangee = cerveau.definir(champ, valeur)
                if ancienne == rangee:
                    interface.dire("Je le savais déjà.")
                    return
                self.cerveau_a_change(f"C'est rangé : {LIBELLES[champ]} → {cerveau.valeur_affichee(champ)}")
                return
            texte = f"{cle.strip()[:1].upper()}{cle.strip()[1:]} : {valeur.strip()}" if separateur and valeur.strip() \
                else reste
            if cerveau.deja_connu(texte):
                interface.dire("Je le savais déjà.")
                return
            cerveau.ajouter_souvenir(texte)
            if self.cerveau_trop_gros():
                cerveau.souvenirs.pop()
                interface.erreur("Mon cerveau est plein : fais de la place avec /cerveau oublie avant d'ajouter autre chose.")
                return
            if not self.cerveau_a_change(f"C'est retenu (souvenir n° {len(cerveau.souvenirs)}) : « {texte} »"):
                cerveau.souvenirs.pop()

        elif action == "modifie":
            numero, _, texte = reste.partition(" ")
            if numero.isdigit():
                texte = texte.strip(" :")
                if not texte:
                    interface.erreur("Écris le nouveau texte : /cerveau modifie 2 j'ai deux chats")
                    return
                ancien = cerveau.remplacer_souvenir(int(numero), texte)
                if ancien is None:
                    interface.erreur(f"Pas de souvenir n° {numero}. Tape /cerveau pour voir la liste.")
                    return
                self.cerveau_a_change(f"Souvenir n° {numero} modifié : « {texte} » (avant : « {ancien} »)")
                return
            champ, valeur = self.lire_champ_et_valeur(reste)
            if not champ:
                interface.erreur("Quelle info ? Par exemple : /cerveau modifie ville Lyon  ·  "
                                 "infos possibles : prénom, relation, activité, ville, passions, réponses  ·  "
                                 "pour un souvenir : /cerveau modifie 2 nouveau texte")
                return
            if not valeur:
                interface.erreur(f"Écris la nouvelle valeur : /cerveau modifie {LIBELLES[champ].lower()} …")
                return
            cerveau.definir(champ, valeur)
            self.cerveau_a_change(f"C'est modifié : {LIBELLES[champ]} → {cerveau.valeur_affichee(champ)}")

        elif action == "oublie":
            if not reste:
                interface.erreur("Quoi donc ? /cerveau oublie 2 (numéro du souvenir), /cerveau oublie ville, "
                                 "ou /cerveau oublie garage (un mot du souvenir)")
                return
            if forme_nom(reste) == "tout":
                self.commande_cerveau("vide")
                return
            numeros = reste.replace(",", " ").split()
            if all(n.isdigit() for n in numeros):
                absents = [n for n in numeros if not 1 <= int(n) <= len(cerveau.souvenirs)]
                if absents:
                    interface.erreur(f"Pas de souvenir n° {', '.join(absents)}. Tape /cerveau pour voir la liste.")
                    return
                retires = [cerveau.oublier_souvenir(int(n)) for n in sorted(set(numeros), key=int, reverse=True)]
                self.cerveau_a_change("C'est oublié : " + " ; ".join(f"« {t} »" for t in reversed(retires)))
                return
            champ = trouver_champ(reste)
            if not champ:   # « oublie villages » : la valeur d'une info de la fiche
                champ = next((c for c, v in cerveau.utilisateur.items() if forme_nom(v) == forme_nom(reste)), None)
            if champ:
                if champ not in cerveau.utilisateur:
                    interface.dire(f"Je ne connaissais pas ton info « {LIBELLES[champ]} ».")
                    return
                ancienne = cerveau.oublier_champ(champ)
                self.cerveau_a_change(f"C'est oublié : {LIBELLES[champ]} (« {ancienne} »)")
                return
            trouves = cerveau.chercher_souvenirs(reste)
            if not trouves:
                interface.erreur(f"Aucun souvenir ne contient « {reste} ». Tape /cerveau pour voir la liste.")
            elif len(trouves) > 1:
                interface.erreur("Plusieurs souvenirs correspondent : "
                                 + "  ·  ".join(f"{n}. {t}" for n, t in trouves)
                                 + f"  →  précise le numéro : /cerveau oublie {trouves[0][0]}")
            else:
                numero, texte = trouves[0]
                cerveau.oublier_souvenir(numero)
                self.cerveau_a_change(f"C'est oublié : « {texte} »")

        elif action == "vide":
            if cerveau.est_vide():
                interface.dire("Mon cerveau est déjà vide.")
                return
            reponse = interface.demander("J'efface tout mon cerveau ? Je ne saurai plus rien de toi.",
                                         choix=[("Oui, tout effacer", "o"), ("Non", "n")])
            if interpreter_confirmation(reponse or "")[0] != "oui":
                interface.info("D'accord, je garde tout.")
                return
            sauvegarde_utilisateur, sauvegarde_souvenirs = dict(cerveau.utilisateur), list(cerveau.souvenirs)
            cerveau.vider()
            if not self.cerveau_a_change("Mon cerveau est vide. Tape /cerveau questions pour faire connaissance "
                                         "(sinon je te les poserai au prochain lancement)."):
                cerveau.utilisateur, cerveau.souvenirs = sauvegarde_utilisateur, sauvegarde_souvenirs

        elif action == "questions":
            if interface.astuces_clavier:
                interface.info("Entrée : garder la réponse actuelle  ·  - : l'effacer  ·  /plus-tard : arrêter là")
            if questionnaire(cerveau, self.poser_question_cerveau, interface.dire, modification=True):
                self.cerveau_a_change("Mon cerveau est à jour.")
            else:
                interface.dire("Rien n'a changé.")

    @staticmethod
    def lire_champ_et_valeur(texte):
        """« ville : Lyon », « ville Lyon », « lieu de vie Lyon » → ("lieu", "Lyon")."""
        if ":" in texte or "=" in texte:
            cle, _, valeur = texte.partition(":") if ":" in texte else texte.partition("=")
            return trouver_champ(cle), valeur.strip()
        mots = texte.split()
        for longueur in (4, 3, 2, 1):   # le nom de l'info peut faire plusieurs mots
            if len(mots) >= longueur:
                champ = trouver_champ(" ".join(mots[:longueur]))
                if champ:
                    return champ, " ".join(mots[longueur:]).strip()
        return None, ""

    def envoyer_parametres(self):
        """Tient le panneau Paramètres de l'application à jour."""
        synthese = self.voix.synthese
        self.interface_brute.parametres(self.parametres.pour_application(
            [{cle: fiche[cle] for cle in ("id", "nom", "libelle", "langue", "licence")}
             for fiche in self.voix.voix_installees()],
            {"etat": synthese.etat(), "message": synthese.message, "erreur": synthese.erreur_installation,
             "dossier": synthese.dossier, "choisie": self.voix.voix_choisie() or "",
             "principale": module_voix.VOIX_PRINCIPALE},
            self.etat_ecoute(),
            demarrage.etat(self.parametres["demarrage_discret"])
            if demarrage.SUR_MAC and not demarrage.GERE_PAR_APPLICATION else ""))

    def etat_ecoute(self):
        ecoute, choix = self.ecoute, self.parametres["ecoute_modele"]
        return {"etat": ecoute.etat(choix), "message": ecoute.message, "erreur": ecoute.erreur_installation,
                "modele": choix, "moteur_installe": ecoute.moteur_installe(),
                "modeles": [[cle, info["nom"], info["taille"] // 1_000_000, ecoute.modele_installe(cle)]
                            for cle, info in ecoute.modeles.items()]}

    def appliquer_effets(self, cle, valeur):
        """Ce qu'un changement de paramètre du système implique tout de suite."""
        if cle in ("recherche_web", "actions_mac", "apprentissage_auto"):
            # ce qu'il sait faire est écrit dans sa consigne : il la relit en arrière-plan
            self.rafraichir_consigne()
            self.prechauffer()
        elif cle == "longueur_reponses":
            self.budget = reglages.TAILLE_CONTEXTE - reglages.TOKENS_REPONSE_MAX - reglages.MARGE_SECURITE
            self.limite_cerveau = self.budget // 2
        elif cle in ("garder_conversation", "memoire_sur_disque"):
            if cle == "garder_conversation":
                if valeur:
                    self.sauver_conversation()
                elif self.chemin_conversation and os.path.exists(self.chemin_conversation):
                    try:
                        os.remove(self.chemin_conversation)   # plus rien de gardé : on l'efface
                    except OSError:
                        pass
            if self.sauvegarde:
                self.sauvegarde.actif = memoire_active()
                if self.sauvegarde.actif:
                    self.sauvegarde.demander()
                else:
                    self.sauvegarde.oublier()   # le fichier contient la conversation : on l'efface
        elif cle == "voix_active":
            if valeur:
                self.verifier_voix()          # prévient si aucune voix française n'est disponible
            else:
                self.voix.taire()
                self.voix.arreter_moteur()
        elif cle == "voix_nom":
            self.voix.preparer()
        elif cle == "ecoute_modele":
            # whisper.cpp déjà là, modèle pas encore téléchargé : on le télécharge tout de suite
            if self.ecoute.moteur_installe() and not self.ecoute.modele_installe(valeur):
                self.installer_ecoute()
        elif cle in ("demarrage_session", "demarrage_discret"):
            self.appliquer_demarrage(annoncer=True)

    def changer_parametre(self, cle, valeur, annoncer=True):
        """Change un paramètre, l'enregistre et l'applique. Renvoie True si c'est fait."""
        if cle == "voix_nom" and str(valeur or "").strip():
            fiche = self.voix.synthese.chercher(str(valeur))   # « amelie » → « Amélie »
            if not fiche:
                self.interface.erreur(f"Je n'ai pas de voix « {valeur} ». Tape /voix liste.")
                return False
            valeur = fiche["id"]
        try:
            ok, resultat = self.parametres.definir(cle, valeur)
        except OSError as erreur:
            self.interface.erreur(f"Impossible d'enregistrer les paramètres : {erreur}")
            return False
        if not ok:
            self.interface.erreur(f"Paramètre refusé : {resultat}.")
            return False
        if cle == "briefing_ville":
            self.parametres.etat.pop("meteo_lieu", None)
        self.appliquer_effets(cle, resultat)
        if annoncer:
            affiche = {True: "oui", False: "non"}.get(resultat, resultat) if isinstance(resultat, bool) else resultat
            self.interface.dire(f"C'est réglé : {cle.replace('_', ' ')} → {str(affiche).replace('.', ',') if isinstance(affiche, float) else (affiche or '(vide)')}.")
        self.envoyer_parametres()
        return True

    def commande_reglages(self, argument):
        """/paramètres : tout voir ; /paramètres briefing quotidien : changer (alias : /réglages)."""
        if not argument:
            lignes = [f"- {nom} : {valeur}  ({explication})" for nom, valeur, explication
                      in self.parametres.lignes_affichage()]
            self.interface_brute.dire("Mes paramètres :\n" + "\n".join(lignes)
                                      + "\nPour changer : /paramètres nom valeur, par exemple /paramètres briefing "
                                        "quotidien, /paramètres web non, /paramètres créativité 0,3. Dans "
                                        "l'application : la roue dentée en haut à droite (⌘,).")
            return
        mots = argument.split()
        for longueur in (3, 2, 1):   # le nom du réglage peut faire plusieurs mots (« nom de voix »)
            cle = cle_depuis_nom(" ".join(mots[:longueur])) if len(mots) > longueur else None
            if cle:
                self.changer_parametre(cle, " ".join(mots[longueur:]))
                return
        self.interface.erreur("Je ne connais pas ce paramètre (ou il manque la valeur). Tape /paramètres pour voir la liste.")

    def commande_voix(self, argument):
        """/voix (on/off), /voix liste, /voix test, /voix thomas, /voix auto, /voix plus."""
        mot = forme_nom(argument)
        if mot in ("plus", "ajouter", "ajoute", "add", "installer", "installe", "telecharger", "autres", "dossier"):
            self.ouvrir_reglages_voix()
            return
        if not mot:
            self.changer_parametre("voix_active", not self.parametres["voix_active"], annoncer=False)
        elif mot in ("on", "oui", "active", "off", "non", "coupe", "desactive"):
            self.changer_parametre("voix_active", mot in ("on", "oui", "active"), annoncer=False)
        elif mot in ("liste", "list", "voix"):
            self.lister_voix()
            return
        elif mot in ("test", "essai", "essaie"):
            if self.voix.disponible():
                self.voix.essayer()
            else:
                self.interface.erreur(self.explication_voix_absente())
            return
        elif mot in ("auto", "automatique", "defaut", "principale"):
            self.changer_parametre("voix_nom", "", annoncer=False)
        else:
            fiche = self.voix.synthese.chercher(argument)
            if not fiche:
                self.interface.erreur(f"Je n'ai pas de voix « {argument} ». Tape /voix liste (et /voix plus pour "
                                      "en avoir d'autres).")
                return
            self.changer_parametre("voix_nom", fiche["id"], annoncer=False)
            self.changer_parametre("voix_active", True, annoncer=False)
        self.annoncer_etat_voix()

    def annoncer_etat_voix(self):
        if not self.parametres["voix_active"]:
            self.interface.info("Voix coupée. /voix pour la remettre.")
        elif self.voix.disponible():
            self.interface.dire(f"Voix activée : {self.voix.nom_voix_choisie()}. /voix off pour la couper.")
        else:
            self.interface.erreur(self.explication_voix_absente())

    def explication_voix_absente(self):
        if not self.voix.synthese.moteur_installe():
            return "Je ne parle qu'avec les voix de macOS : ici, la commande say est introuvable."
        return ("Je ne trouve aucune voix française sur ce Mac. Ajoutes-en une : " + module_voix.OU_TROUVER_VOIX + ".")

    def verifier_voix(self):
        """Voix active : prévient si aucune voix française n'est disponible."""
        if self.parametres["voix_active"] and not self.voix.disponible():
            self.interface.info(self.explication_voix_absente())

    def lister_voix(self):
        fiches = self.voix.voix_installees()
        if not fiches:
            self.interface.info(self.explication_voix_absente())
            return
        choisie = self.voix.voix_choisie()
        lignes = [f"- {fiche['libelle']} — /voix {forme_nom(fiche['nom']) or fiche['id']}"
                  f"{'  ← utilisée' if fiche['id'] == choisie else ''}" for fiche in fiches]
        self.interface_brute.dire("Mes voix (celles de macOS, sur ton Mac) :\n" + "\n".join(lignes)
                                  + "\nD'autres voix, plus naturelles (« améliorées », « premium ») : "
                                  + module_voix.OU_TROUVER_VOIX + ". /voix plus ouvre ces réglages.")

    def ouvrir_reglages_voix(self):
        """Les voix se téléchargent dans les réglages de macOS : on les ouvre."""
        if actions.SUR_MAC or actions.SYSTEME:
            actions.ouvrir(module_voix.REGLAGES_VOIX)
        self.interface.info("Pour d'autres voix : " + module_voix.OU_TROUVER_VOIX + ". Elles apparaissent ensuite "
                            "dans /voix liste (et Paramètres ▸ Voix).")

    # --- écoute : le micro de l'application, transcrit sur le Mac par whisper.cpp ---

    def explication_ecoute_absente(self):
        ecoute = self.ecoute
        if ecoute.erreur_installation:
            return (f"Mon écoute n'est pas installée : {ecoute.erreur_installation}. Vérifie la connexion Internet "
                    "puis tape /écoute installer (le détail est dans ecoute/compilation.log).")
        if not ecoute.installation_possible():
            return "Mon écoute locale (whisper.cpp) ne s'installe que sur un Mac."
        morceaux, taille = ecoute.a_telecharger(self.parametres["ecoute_modele"])
        return (f"Mon écoute locale n'est pas encore installée ({' et '.join(morceaux)}, environ {taille} Mo) : "
                "/écoute installer, ou le bouton du micro dans l'application.")

    def installer_ecoute(self):
        """Compile whisper.cpp et télécharge le modèle choisi, en arrière-plan ; prévient quand c'est prêt."""
        ecoute, choix = self.ecoute, self.parametres["ecoute_modele"]
        if ecoute.disponible(choix):
            self.interface.info("Mon écoute locale est déjà installée : micro à gauche du champ de saisie (⌘D).")
            self.envoyer_parametres()
            return
        if not ecoute.installation_possible():
            self.interface.erreur(self.explication_ecoute_absente())
            return
        if ecoute.installation_en_cours:
            self.interface.info("Installation de mon écoute déjà en cours : je te préviens dès qu'elle est prête.")
            return
        morceaux, taille = ecoute.a_telecharger(choix)
        duree = "  La compilation prend 5 à 15 minutes, une seule fois : les réponses seront plus lentes " \
                "pendant ce temps." if not ecoute.moteur_installe() else ""
        self.interface.info(f"J'installe mon écoute locale ({' et '.join(morceaux)}, environ {taille} Mo) : "
                            f"rien ne passera par Apple.{duree} Je te préviens dès qu'elle est prête.")
        ecoute.installer(choix, rapporter=self.interface_brute.annonce, fini=self._ecoute_installee)
        self.envoyer_parametres()

    def _ecoute_installee(self):
        """Fin de l'installation de l'écoute (fil d'installation)."""
        ecoute = self.ecoute
        if ecoute.erreur_installation or not ecoute.disponible(self.parametres["ecoute_modele"]):
            self.interface_brute.annonce("Installation de mon écoute impossible : "
                                         + (ecoute.erreur_installation or "raison inconnue")
                                         + ". Tape /écoute installer pour réessayer.", erreur=True)
        else:
            self.interface_brute.annonce("✓ Mon écoute est prête, 100 % sur ton Mac : micro à gauche du champ de "
                                         "saisie (⌘D), parle puis fais une petite pause.")
        self.envoyer_parametres()

    def transcrire(self, fichier, apercu=False, ident=None):
        """Message « transcrire » de l'application : transcription en arrière-plan, résultat par l'interface.
        apercu : texte provisoire pendant qu'on parle encore (abandonné si whisper est occupé)."""
        choix = self.parametres["ecoute_modele"]

        def travailler():
            try:
                resultat = self.ecoute.transcrire(fichier, choix, apercu=apercu)
            except Exception as erreur:   # jamais d'exception perdue dans un fil
                resultat = {"ok": False, "texte": "", "duree": 0.0, "duree_son": 0.0,
                            "erreur": f"{type(erreur).__name__} : {erreur}"}
            if resultat.get("erreur") == "a_installer":
                resultat["explication"] = self.explication_ecoute_absente()
            resultat.update(fichier=fichier, apercu=bool(apercu), id=ident)
            self.interface_brute.transcription(resultat)

        threading.Thread(target=travailler, name="ecoute-transcription", daemon=True).start()

    def commande_ecoute(self, argument):
        """/écoute : état ; /écoute installer ; /écoute précis | rapide ; /écoute fichier.wav."""
        mot = forme_nom(argument)
        chemin = os.path.expanduser(argument.strip().strip('"').strip("'").replace("\\ ", " "))
        if mot in ("installer", "installe", "installation", "install"):
            self.installer_ecoute()
            return
        if mot in ("precis", "precise", "rapide", "small", "base"):
            self.changer_parametre("ecoute_modele", mot)
            return
        if chemin.lower().endswith(".wav"):
            if not os.path.isfile(chemin):
                self.interface.erreur(f"Fichier introuvable : {chemin}")
                return
            resultat = self.ecoute.transcrire(chemin, self.parametres["ecoute_modele"])
            if resultat["erreur"] == "a_installer":
                self.interface.erreur(self.explication_ecoute_absente())
            elif not resultat["ok"]:
                self.interface.erreur("Transcription impossible : " + resultat["erreur"])
            else:
                self.interface_brute.dire(f"J'ai entendu : « {resultat['texte'] or '(rien)'} »")
                self.interface.info(f"{resultat['duree_son']:.1f} s de son, transcrites en "
                                    f"{resultat['duree']:.1f} s".replace(".", ","))
            return
        if mot:
            self.interface.erreur("Je ne connais pas cette option : /écoute, /écoute installer, /écoute précis, "
                                  "/écoute rapide ou /écoute fichier.wav.")
            return
        ecoute, choix = self.ecoute, self.parametres["ecoute_modele"]
        if ecoute.installation_en_cours:
            self.interface.info("Installation de mon écoute en cours : " + (ecoute.message or "…"))
        elif ecoute.disponible(choix):
            self.interface.info(f"Écoute locale prête (whisper.cpp, modèle {ecoute.modeles[choix]['nom']}) : micro "
                                "de l'application, ⌘D. Autre modèle : /écoute précis ou /écoute rapide.")
        else:
            self.interface.info(self.explication_ecoute_absente())

    # --- démarrage avec la session macOS ---

    def verifier_demarrage(self):
        """Au lancement : la fiche d'ouverture de session suit le dossier (s'il a été déplacé).
        Dans l'application, c'est elle qui gère l'ouverture avec la session (macOS : éléments de connexion)."""
        if demarrage.GERE_PAR_APPLICATION:
            return
        if self.parametres["demarrage_session"] and demarrage.SUR_MAC \
                and demarrage.etat(self.parametres["demarrage_discret"]) != "actif":
            self.appliquer_demarrage(annoncer=False)
            self.envoyer_parametres()

    def appliquer_demarrage(self, annoncer=True):
        actif, discret = self.parametres["demarrage_session"], self.parametres["demarrage_discret"]
        if demarrage.GERE_PAR_APPLICATION:
            # l'application voit le paramètre changer et s'inscrit (ou se retire) auprès de macOS
            if annoncer:
                self.interface.info(demarrage.message_actif(discret) if actif
                                    else "O.V.E.R. ne s'ouvrira plus à l'ouverture de ta session.")
            return
        if not demarrage.SUR_MAC:
            if actif and annoncer:
                self.interface.erreur("L'ouverture avec la session ne marche que sur un Mac.")
            return
        ok, message = demarrage.appliquer(actif, discret)
        if not ok:
            if actif:
                self.parametres.definir("demarrage_session", False)   # le réglage suit la réalité
            self.interface.erreur(message)
        elif annoncer:
            self.interface.info(message)

    def commande_demarrage(self, argument):
        """/démarrage : état ; /démarrage oui | non | discret | fenêtre."""
        mot = forme_nom(argument)
        if mot in ("oui", "on", "active", "activer"):
            self.changer_parametre("demarrage_session", True, annoncer=False)
        elif mot in ("non", "off", "desactive", "desactiver"):
            self.changer_parametre("demarrage_session", False, annoncer=False)
        elif mot in ("discret", "cache", "sans fenetre"):
            self.parametres.definir("demarrage_session", True)
            self.changer_parametre("demarrage_discret", True, annoncer=False)
        elif mot in ("fenetre", "avec fenetre", "visible"):
            self.parametres.definir("demarrage_session", True)
            self.changer_parametre("demarrage_discret", False, annoncer=False)
        elif mot:
            self.interface.erreur("/démarrage oui, non, discret (sans fenêtre) ou fenêtre.")
        else:
            p = self.parametres
            if not p["demarrage_session"]:
                self.interface.info("O.V.E.R. ne s'ouvre pas avec ta session. /démarrage oui pour l'activer "
                                    "(/démarrage discret : sans fenêtre).")
            else:
                self.interface.info(demarrage.message_actif(p["demarrage_discret"])
                                    + " /démarrage non pour l'arrêter.")
            self.interface.info("Arrière-plan (fenêtre fermée, O.V.E.R. reste prêt dans la barre des menus) : "
                                + ("oui" if p["arriere_plan"] else "non")
                                + ". /paramètres arrière-plan oui ou non.")

    def fiche_complete(self):
        """Le cerveau entier, pour le panneau Paramètres : toutes les infos (même vides) et les souvenirs."""
        cerveau = self.cerveau
        return {
            "champs": [[cle, LIBELLES[cle], cerveau.utilisateur.get(cle, "")] for cle, _, _ in QUESTIONS],
            "souvenirs": [souvenir["texte"] for souvenir in cerveau.souvenirs],
            "taille": self.tokens(cerveau.texte_pour_consigne()),
            "limite": self.limite_cerveau,
        }

    def traiter_message_application(self, commande):
        """Messages du panneau Paramètres de l'application (hors conversation). Renvoie True si traité."""
        genre = commande.get("type")
        cerveau = self.cerveau
        if genre == "taire":
            self.voix.taire()
        elif genre == "voix_essai":
            if self.voix.disponible():
                self.voix.essayer()
            else:
                self.voix.synthese.message = self.explication_voix_absente()
                self.envoyer_parametres()
        elif genre == "transcrire":
            self.transcrire(str(commande.get("fichier") or ""), apercu=bool(commande.get("apercu")),
                            ident=commande.get("id"))
        elif genre == "ecoute_installer":
            self.installer_ecoute()
        elif genre == "lire_parametres":
            self.envoyer_parametres()
        elif genre == "reglage":
            cle = str(commande.get("cle", ""))
            if cle in self.parametres.valeurs:
                self.changer_parametre(cle, commande.get("valeur"), annoncer=False)
        elif genre == "lire_cerveau":
            self.interface_brute.fiche_cerveau(self.fiche_complete())
        elif genre == "cerveau":
            action = commande.get("action")
            texte = " ".join(str(commande.get("texte") or "")[:2000].split())
            numero = commande.get("numero")
            if isinstance(numero, bool) or not isinstance(numero, int):
                numero = None   # en Python, true vaut 1 : un booléen ne désigne pas le souvenir n° 1
            avant_utilisateur = dict(cerveau.utilisateur)
            avant_souvenirs = [dict(souvenir) for souvenir in cerveau.souvenirs]
            if action == "definir" and commande.get("champ") in LIBELLES:
                if texte:
                    cerveau.definir(commande["champ"], texte)
                else:
                    cerveau.oublier_champ(commande["champ"])
            elif action == "ajouter" and texte and not cerveau.deja_connu(texte):
                cerveau.ajouter_souvenir(texte)
            elif action == "modifier_souvenir" and numero is not None and texte:
                cerveau.remplacer_souvenir(numero, texte)
            elif action == "oublier_souvenir" and numero is not None:
                cerveau.oublier_souvenir(numero)
            elif action == "vider":
                cerveau.vider()
            if cerveau.utilisateur != avant_utilisateur or cerveau.souvenirs != avant_souvenirs:
                if self.cerveau_trop_gros():
                    cerveau.utilisateur, cerveau.souvenirs = avant_utilisateur, avant_souvenirs
                    self.interface_brute.erreur("Mon cerveau est plein : retire d'abord un souvenir.")
                elif self.enregistrer_cerveau():
                    self.rafraichir_consigne()
                    self.prechauffer()
                else:
                    cerveau.utilisateur, cerveau.souvenirs = avant_utilisateur, avant_souvenirs
            self.interface_brute.fiche_cerveau(self.fiche_complete())
        else:
            return False
        return True

    def commande(self, saisie):
        """Exécute une commande /…  Renvoie False pour quitter."""
        nom, _, argument = saisie.partition(" ")
        nom = nom.lower()
        argument = argument.strip()
        interface = self.interface

        if nom in ("/quitter", "/q", "/quit", "/exit"):
            return False

        if nom == "/aide":
            lignes = ((LIGNES_AIDE_ACTIONS if reglages.ACTIONS_MAC else [])
                      + (LIGNES_AIDE_WEB if reglages.RECHERCHE_WEB else []) + LIGNES_AIDE_REGLAGES + LIGNES_AIDE)
            interface.afficher_aide(lignes)

        elif nom == "/web" and reglages.RECHERCHE_WEB:
            if not argument:
                interface.erreur("Écris ce qu'il faut chercher après la commande : /web iaover.fr")
            else:
                self.repondre(f"Cherche sur le web : {argument}", requete_directe=argument)

        elif nom == "/effacer":
            self.effacer_conversation()
            self.rafraichir_consigne()
            self.prechauffer()
            interface.info("Conversation effacée (le cerveau est gardé).")

        elif nom == "/cerveau":
            self.commande_cerveau(argument)

        # anciennes commandes, gardées : elles passent par /cerveau
        elif nom == "/retiens":
            self.commande_cerveau("ajoute " + argument)
        elif nom == "/oublie":
            self.commande_cerveau("oublie " + argument)
        elif nom == "/modifier":
            self.commande_cerveau("questions")

        elif forme_nom(nom) in ("reglages", "reglage", "parametres", "parametre", "settings", "preferences"):
            self.commande_reglages(argument)

        elif nom == "/voix":
            self.commande_voix(argument)

        elif forme_nom(nom) in ("ecoute", "ecouter", "micro"):
            self.commande_ecoute(argument)

        elif forme_nom(nom) in ("demarrage", "session", "lancement"):
            self.commande_demarrage(argument)

        elif forme_nom(nom) in ("tais toi", "taistoi", "chut", "stop", "silence"):
            interface.info("D'accord, je me tais.")

        elif nom == "/briefing":
            self.faire_briefing()

        else:
            interface.erreur("Commande inconnue. Tape /aide.")
        return True


def memoire_active():
    """La lecture du modèle n'est gardée sur le disque que si la conversation l'est aussi."""
    return bool(reglages.MEMOIRE_SUR_DISQUE and reglages.GARDER_CONVERSATION)


def ouvrir_session(interface, chemin_modele=None, chemin_cerveau=None, nb_fils=0):
    """Charge le cerveau et le modèle. Renvoie (session, infos) ; lève RuntimeError si le modèle manque."""
    chemin_modele = os.path.abspath(chemin_modele or reglages.CHEMIN_MODELE)
    if not os.path.isfile(chemin_modele):
        raise RuntimeError(f"Modèle introuvable : {chemin_modele}. Lance d'abord : bash installer.sh")
    chemin_cerveau = os.path.abspath(chemin_cerveau or reglages.CHEMIN_CERVEAU)
    cerveau = Cerveau.charger(chemin_cerveau)
    # la conversation est rangée à côté du cerveau
    chemin_conversation = os.path.join(os.path.dirname(chemin_cerveau), "conversation.json")
    dossier = os.path.dirname(chemin_cerveau)
    parametres = Parametres.charger(os.path.join(dossier, "parametres.json"))
    parametres.appliquer_systeme()   # tes choix (web, actions, créativité…) passent avant reglages.py
    nb_fils = nb_fils if nb_fils > 0 else nombre_coeurs_physiques()
    modele, duree = charger_modele(chemin_modele, nb_fils)
    # mémoire du modèle gardée sur le disque : seulement si la conversation est gardée elle aussi
    recharges = 0
    carte = memoire_modele.carte_identite(chemin_modele, reglages.TAILLE_CONTEXTE, reglages.TAILLE_LOT_LECTURE)
    sauvegarde = memoire_modele.Sauvegarde(modele, dossier, carte, VERROU_MODELE)
    sauvegarde.actif = memoire_active()
    if sauvegarde.actif:
        debut = time.perf_counter()
        recharges = memoire_modele.recharger(modele, dossier, carte)
        duree += time.perf_counter() - debut
        sauvegarde.connaitre(recharges)
    else:
        memoire_modele.effacer(dossier)
    infos = {
        "nom_modele": os.path.basename(chemin_modele).replace(".gguf", ""),
        "nb_fils": nb_fils,
        "duree_chargement": duree,
        "taille_contexte": reglages.TAILLE_CONTEXTE,
        "recherche_web": reglages.RECHERCHE_WEB,
        "actions": reglages.ACTIONS_MAC,
        "version": identite.VERSION,
        "memoire_rechargee": recharges,
    }
    session = Session(modele, cerveau, interface, chemin_conversation, parametres=parametres, sauvegarde=sauvegarde,
                      tokens_recharges=recharges)
    return session, infos
