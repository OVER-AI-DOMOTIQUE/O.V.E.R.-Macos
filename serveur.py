#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Serveur d'O.V.E.R. — le pont entre l'application Mac et le noyau.

L'application lance ce programme et lui parle par messages JSON, un par ligne.

Application → moteur (entrée standard) :
  {"type": "message", "texte": "Bonjour"}        message ou commande (/effacer, /cerveau…)
  {"type": "reponse", "texte": "o"}              réponse à une question (null = arrêter les questions)
  {"type": "taire"}                              arrête de parler à voix haute
  {"type": "reglage", "cle": "briefing", "valeur": "quotidien"}   change un paramètre (panneau Paramètres)
  {"type": "lire_parametres"} / {"type": "lire_cerveau"} / {"type": "voix_essai"}
  {"type": "systeme_resultat", "id": 3, "ok": true, "detail": "", "donnees": {…}}
                                                 résultat d'une action Mac demandée par « systeme »
  {"type": "transcrire", "fichier": "/…/son.wav"}  transcrit ce que le micro a enregistré (whisper.cpp)
  {"type": "ecoute_installer"}                   compile whisper.cpp et télécharge le modèle d'écoute
  {"type": "cerveau", "action": "definir|ajouter|modifier_souvenir|oublier_souvenir", "champ", "texte", "numero"}
  {"type": "quitter"}
  Seulement quand le modèle manque (premier lancement de l'application) :
  {"type": "modele_telecharger"}                 télécharge le modèle (2,1 Go), le vérifie et le range
  {"type": "modele_importer", "chemin": "/…"}    le reprend d'un fichier choisi (l'application l'a d'abord
                                                 copié dans le dossier import/ de tes données)
  Le bouton Arrêter envoie le signal SIGINT : il n'agit que pendant une lecture,
  une écriture ou une recherche, jamais au milieu d'une sauvegarde.

Moteur → application (sortie standard) :
  chargement, pret, attente (prêt pour un nouveau message), dire, info, erreur,
  erreur_fatale, question, texte, fin_passage, fin_reponse, recherche_debut,
  recherche_fin, cerveau, aide, alerte (fin d'un minuteur), preparation (lecture
  de la consigne en arrière-plan), historique (conversation reprise), parole (il parle
  à voix haute ou se tait), parametres et fiche_cerveau (panneau Paramètres), transcription
  (texte entendu au micro), fin.
  systeme (id, action, attendre, parametres) : une action sur le Mac, faite par l'application avec les
  outils d'Apple, car le moteur vit dans le bac à sable de l'App Store : ouvrir (cible), volume (genre,
  niveau), notifier (titre, texte), rappel (texte, date), agenda (agenda, rappels). Si « attendre » est
  vrai, l'application répond par systeme_resultat avec le même id (donnees : niveau du son ; evenements
  et rappels du jour pour l'agenda).
  Modèle absent : modele_absent (taille, recu, libre, manque, dossier, erreur),
  modele_progression (phase telechargement|verification|copie, fait, total), modele_pret.

Tout ce qui n'est pas un message du protocole (journaux, avertissements) part
sur la sortie d'erreur, pour ne jamais brouiller la conversation.
"""

import argparse
import collections
import itertools
import json
import os
import signal
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Le protocole garde pour lui la vraie sortie standard ; tout le reste va vers la sortie d'erreur.
_canal = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
os.dup2(2, 1)
sys.stdout = sys.stderr
try:
    sys.stdin.reconfigure(encoding="utf-8")
except AttributeError:
    pass


_verrou_canal = threading.Lock()   # les minuteurs et le préchauffage écrivent depuis d'autres fils


def envoyer(type_evenement, **donnees):
    donnees["type"] = type_evenement
    ligne = json.dumps(donnees, ensure_ascii=False) + "\n"
    try:
        with _verrou_canal:
            _canal.write(ligne)
            _canal.flush()
    except (BrokenPipeError, ValueError):
        os._exit(0)  # l'application est fermée : plus personne à qui parler


# Messages arrivés pendant qu'on attendait le résultat d'une action Mac : traités ensuite, dans l'ordre.
_en_attente = collections.deque()
_compteur_systeme = itertools.count(1)


def _lire_ligne():
    """Lit le prochain message sur l'entrée standard. Quitte si l'application a fermé le tuyau.
    La ligne est lue en octets : un message mal encodé est signalé et ignoré, il n'arrête rien."""
    ligne = sys.stdin.buffer.readline()
    if not ligne:
        raise SystemExit(0)
    try:
        commande = json.loads(ligne.decode("utf-8"))
        return commande if isinstance(commande, dict) else None
    except ValueError:   # JSON abîmé ou octets qui ne sont pas de l'UTF-8 (UnicodeDecodeError en hérite)
        envoyer("erreur", texte="Message illisible reçu de l'application.")
        return None


def lire_commande():
    """Le prochain message de l'application (d'abord ceux mis de côté pendant une action Mac)."""
    while True:
        commande = _en_attente.popleft() if _en_attente else _lire_ligne()
        if commande is None or commande.get("type") != "systeme_resultat":
            return commande
        # résultat arrivé trop tard (action interrompue par le bouton Arrêter) : plus personne ne l'attend


def systeme(action, attendre=True, **parametres):
    """Fait faire une action sur le Mac par l'application (actions.SYSTEME). Renvoie (réussie, détail, données).
    Seul le fil principal lit les messages de l'application : ailleurs (minuteurs), on n'attend pas."""
    numero = next(_compteur_systeme)
    attendre = bool(attendre) and threading.current_thread() is threading.main_thread()
    envoyer("systeme", id=numero, action=action, attendre=attendre, parametres=parametres)
    if not attendre:
        return True, "", {}
    while True:
        commande = _lire_ligne()
        if commande is None:
            continue
        genre = commande.get("type")
        if genre == "systeme_resultat":
            if commande.get("id") == numero:
                donnees = commande.get("donnees")
                return bool(commande.get("ok")), str(commande.get("detail") or ""), \
                    donnees if isinstance(donnees, dict) else {}
            continue   # réponse à une demande abandonnée
        if genre == "quitter":
            raise SystemExit(0)
        _en_attente.append(commande)


try:
    import actions
    import demarrage
    import identite
    import installation
    import noyau
    import reglages
except ImportError as erreur:
    envoyer("erreur_fatale", texte=f"Il manque un élément : {erreur}. " + (
        "Réinstalle l'application O.V.E.R." if os.environ.get("OVER_EMBALLE") == "1"
        else "Lance « bash installer.sh » dans le dossier O.V.E.R."))
    raise SystemExit(1)


class InterfaceApplication(noyau.Interface):
    nom = "dans l'application O.V.E.R. pour Mac"
    astuces_clavier = False
    gestionnaire_annexe = None   # messages du panneau Paramètres, traités même pendant une question

    def dire(self, texte):
        envoyer("dire", texte=texte)

    def info(self, texte):
        envoyer("info", texte=texte)

    def erreur(self, texte):
        envoyer("erreur", texte=texte)

    def demander(self, question, valeur_actuelle="", choix=None, aide="", extrait=""):
        envoyer("question", texte=question, valeur=valeur_actuelle, aide=aide, extrait=extrait,
                choix=[{"libelle": libelle, "valeur": valeur} for libelle, valeur in (choix or [])])
        while True:
            commande = lire_commande()
            if commande is None:
                continue
            genre = commande.get("type")
            if genre == "quitter":
                raise SystemExit(0)
            if genre in ("reponse", "message"):
                return commande.get("texte")
            if self.gestionnaire_annexe:
                self.gestionnaire_annexe(commande)

    def texte(self, morceau):
        envoyer("texte", morceau=morceau)

    def fin_passage(self):
        envoyer("fin_passage")

    def fin_reponse(self, sources, statistiques):
        envoyer("fin_reponse", sources=sources, statistiques=statistiques)

    def recherche_debut(self, requete):
        envoyer("recherche_debut", requete=requete)

    def recherche_fin(self, recherche):
        if recherche is None:
            envoyer("recherche_fin", etat="annulee")
        else:
            envoyer("recherche_fin", etat="trouve" if recherche.a_trouve() else "rien",
                    moteur=recherche.moteur, nb_resultats=len(recherche.resultats),
                    page_lue=recherche.page is not None, duree=recherche.duree, erreurs=recherche.erreurs)

    def afficher_cerveau(self, fiche):
        envoyer("cerveau", **fiche)

    def afficher_aide(self, lignes):
        envoyer("aide", lignes=[{"commande": commande, "explication": explication} for commande, explication in lignes])

    def alerte(self, texte):
        envoyer("alerte", texte=texte)

    def preparation(self, en_cours):
        envoyer("preparation", en_cours=en_cours)

    def historique(self, messages):
        envoyer("historique", messages=[{"auteur": auteur, "texte": texte} for auteur, texte in messages])

    def parole(self, en_cours):
        envoyer("parole", en_cours=en_cours)

    def parametres(self, donnees):
        envoyer("parametres", **donnees)

    def fiche_cerveau(self, fiche):
        envoyer("fiche_cerveau", **fiche)

    def transcription(self, resultat):
        envoyer("transcription", **resultat)


def gestionnaire_arret(numero_signal, cadre):
    """Bouton Arrêter : n'interrompt que pendant une lecture, une écriture ou une recherche."""
    if noyau.ETAT_INTERRUPTION["possible"]:
        raise KeyboardInterrupt
    # en dehors de ces moments (attente d'un message, sauvegarde…), le signal est ignoré


def installer_modele(chemin_modele):
    """Le modèle manque (premier lancement) : l'application propose de le télécharger ou de reprendre un
    fichier déjà téléchargé ; on attend qu'il soit en place. Renvoie False si l'application se ferme avant."""
    modele = installation.Modele(chemin_modele)

    def fermeture(numero_signal, cadre):
        installation.arreter_tout()   # curl ne doit pas continuer seul ; le morceau reçu est gardé
        os._exit(0)

    signal.signal(signal.SIGTERM, fermeture)

    def progression(phase, fait, total):
        envoyer("modele_progression", phase=phase, fait=fait, total=total)

    erreur, prevenir = "", True
    while not modele.present():
        if prevenir:   # au début, puis après chaque essai (erreur ou pause affichée par l'application)
            envoyer("modele_absent", erreur=erreur, **installation.etat(modele))
            prevenir = False
        commande = lire_commande()
        if commande is None:
            continue
        genre = commande.get("type")
        if genre == "quitter":
            return False
        if genre == "modele_telecharger":
            travail = lambda: installation.telecharger(progression, modele)
        elif genre == "modele_importer":
            source = commande.get("chemin") or ""
            travail = lambda: installation.importer(source, progression, modele)
        else:
            continue   # le reste (paramètres, cerveau…) attend que le modèle soit là
        prevenir = True
        noyau.ETAT_INTERRUPTION["possible"] = True   # bouton Pause (SIGINT)
        try:
            _, erreur = travail()   # réussi : la boucle s'arrête (le modèle est là)
        except KeyboardInterrupt:
            erreur = "En pause. Le téléchargement reprendra là où il s'est arrêté."
        finally:
            noyau.ETAT_INTERRUPTION["possible"] = False
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    envoyer("modele_pret")
    return True


def main():
    analyseur = argparse.ArgumentParser(description="Pont JSON entre l'application Mac et O.V.E.R.")
    analyseur.add_argument("--modele", default=None)
    analyseur.add_argument("--cerveau", default=None)
    analyseur.add_argument("--fils", type=int, default=0)
    arguments = analyseur.parse_args()

    signal.signal(signal.SIGINT, gestionnaire_arret)
    # actions sur le Mac (ouvrir, son, notifications, rappels, agenda) et ouverture avec la session :
    # c'est l'application qui s'en charge, avec les outils d'Apple
    actions.SYSTEME = systeme
    demarrage.GERE_PAR_APPLICATION = True
    if reglages.EMBALLE:
        # application emballée : tout ce que le moteur écrit (cerveau, conversation, modèle…) n'est
        # lisible que par toi, et le dossier des données existe toujours
        os.umask(0o077)
        os.makedirs(reglages.DOSSIER_DONNEES, mode=0o700, exist_ok=True)
    interface = InterfaceApplication()
    envoyer("chargement", version=identite.VERSION)
    probleme = installation.explication_processeur()
    if probleme:   # avant de télécharger 2,1 Go pour rien, et au lieu d'un plantage au chargement du modèle
        envoyer("erreur_fatale", texte=probleme)
        return 1
    chemin_modele = arguments.modele or reglages.CHEMIN_MODELE
    if not arguments.modele and not os.path.isfile(chemin_modele):
        if not installer_modele(chemin_modele):
            envoyer("fin")
            return 0
        envoyer("chargement", version=identite.VERSION)
    try:
        session, infos = noyau.ouvrir_session(interface, chemin_modele, arguments.cerveau, arguments.fils)
    except (RuntimeError, ValueError) as erreur:
        envoyer("erreur_fatale", texte=f"Impossible de charger le modèle : {erreur}")
        return 1
    envoyer("pret", **infos)
    interface.gestionnaire_annexe = session.traiter_message_application

    def fermeture(numero_signal, cadre):
        """L'application se ferme (SIGTERM) : on se tait tout de suite (sinon le son en cours continuerait
        seul)."""
        session.voix.fermer()
        os._exit(0)

    signal.signal(signal.SIGTERM, fermeture)
    session.envoyer_parametres()

    try:
        session.demarrer()
        envoyer("attente")
        while True:
            commande = lire_commande()
            if commande is None:
                continue
            genre = commande.get("type")
            if genre == "quitter":
                break
            if genre == "message":
                try:
                    if not session.recevoir(str(commande.get("texte") or "")):
                        break
                except KeyboardInterrupt:
                    session.voix.taire()
                    envoyer("info", texte="Arrêté.")
                envoyer("attente")
            else:
                # réglages, cerveau, voix : pas d'« attente » ensuite, l'application est peut-être
                # déjà en train d'envoyer un message (elle se croirait libre trop tôt)
                session.traiter_message_application(commande)
            # une « reponse » sans question en attente est ignorée
    except SystemExit:
        pass
    session.terminer()
    envoyer("fin")
    return 0


if __name__ == "__main__":
    sys.exit(main())
