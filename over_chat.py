#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
O.V.E.R. — version Terminal.

Tu écris, l'IA répond par écrit. Le modèle tourne sur la machine ; seules
les recherches web (si elles sont activées) passent par Internet.

Ce fichier ne fait que l'affichage dans le Terminal : tout le reste est dans
le noyau (noyau.py), partagé avec l'application Mac (serveur.py).

Modules :
  over_chat.py   l'interface Terminal (ce fichier)
  serveur.py     le pont avec l'application Mac
  noyau.py       la conversation, le modèle, les commandes
  identite.py    qui est O.V.E.R.
  actions.py     les actions sur le Mac (apps, sites, son, minuteurs, rappels)
  cerveau.py     la mémoire durable (cerveau.json)
  web.py         la recherche sur Internet et la lecture de pages
  voix.py        la voix (voix françaises de macOS, commande say)
  ecoute.py      l'écoute : micro de l'application → texte, sur le Mac (whisper.cpp)
  demarrage.py   l'ouverture d'O.V.E.R. avec la session macOS
  briefing.py    le point du jour (date, météo, agenda, rappels)
  parametres.py  les choix de l'utilisateur (parametres.json)
  memoire_modele.py  la lecture du modèle gardée sur le disque (démarrage rapide)
  balises.py     les lignes [CERVEAU] / [RECHERCHE] que le modèle adresse au programme
  reglages.py    tous les réglages

Commandes pendant la discussion : /aide, /web, /cerveau (ajoute, modifie, oublie, vide,
questions, aide), /paramètres, /voix, /écoute, /démarrage, /briefing, /effacer, /quitter (ou Ctrl+D).
Ctrl+C arrête une réponse, une recherche, ou la voix.

Essai de la recherche web sans charger le modèle :
  bash lancer.sh --test-web "iaover.fr"
"""

import argparse
import os
import sys
import time

try:
    import readline  # flèches, retour arrière et historique de saisie dans le terminal
except ImportError:
    readline = None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import noyau
except ImportError as erreur:
    print(f"Il manque un élément : {erreur}")
    print("Lance d'abord l'installation :  bash installer.sh")
    sys.exit(1)
import identite
import reglages
import web


# --- Couleurs ---------------------------------------------------------------

def couleurs_actives():
    """Couleurs seulement dans un vrai terminal (pas dans un fichier, ni si NO_COLOR est défini)."""
    return (
        sys.stdout.isatty()
        and os.environ.get("NO_COLOR") is None
        and os.environ.get("TERM") != "dumb"
    )


AVEC_COULEURS = couleurs_actives()

# Palette du logo O.V.E.R., en 256 teintes (le Terminal du Mac ne gère pas toujours plus)
SARCELLE = "38;5;37"
OR = "38;5;179"
ROUGE = "38;5;197"
DISCRET = "2"
GRAS = "1"


def couleur(texte, *codes):
    if not AVEC_COULEURS:
        return texte
    return "\033[" + ";".join(codes) + "m" + texte + "\033[0m"


def invite_saisie():
    """Invite « Toi › ». Les codes couleur sont balisés pour que readline calcule bien la largeur."""
    texte = "Toi › "
    if not AVEC_COULEURS:
        return texte
    if readline is not None and "libedit" in (readline.__doc__ or ""):
        # libedit (Python fourni par Apple) gère mal les couleurs dans l'invite : on reste sobre
        return texte
    debut, fin = ("\001", "\002") if readline is not None else ("", "")
    return debut + "\033[" + OR + "m" + fin + texte + debut + "\033[0m" + fin


def prefixe_over():
    return couleur("O.V.E.R. › ", GRAS, SARCELLE)


def nombre_fr(valeur, decimales=1):
    return f"{valeur:.{decimales}f}".replace(".", ",")


# --- L'interface Terminal ------------------------------------------------------

class InterfaceTerminal(noyau.Interface):
    nom = "dans le Terminal du Mac"

    def __init__(self):
        self.reponse_en_cours = False
        self.a_repondu = False   # une réponse du modèle a eu lieu pendant ce tour (pour aérer l'affichage)
        self.preparation_annoncee = False

    def dire(self, texte):
        print(prefixe_over() + texte)

    def info(self, texte):
        print(couleur("  " + texte, DISCRET))

    def erreur(self, texte):
        print(couleur("  " + texte, ROUGE))

    def annonce(self, texte, erreur=False):
        """Message venu d'un autre fil (installation de la voix) : toujours sur sa propre ligne."""
        if self.reponse_en_cours:
            sys.stdout.write("\n")
        (self.erreur if erreur else self.info)(texte)
        if self.reponse_en_cours:
            sys.stdout.write(prefixe_over())
            sys.stdout.flush()

    def demander(self, question, valeur_actuelle="", choix=None, aide="", extrait=""):
        ligne = prefixe_over() + question
        if extrait:
            ligne += "  " + couleur(f"« {extrait} »", OR)
        if valeur_actuelle:
            ligne += couleur(f"  [{valeur_actuelle}]", DISCRET)
        print(ligne)
        if aide:
            print(couleur("  " + aide, DISCRET))
        try:
            return input(invite_saisie())
        except (EOFError, KeyboardInterrupt):
            print()
            return None

    def texte(self, morceau):
        if not self.reponse_en_cours:
            sys.stdout.write(prefixe_over())
            self.reponse_en_cours = True
        sys.stdout.write(morceau)
        sys.stdout.flush()

    def fin_passage(self):
        if self.reponse_en_cours:
            print()
            self.reponse_en_cours = False

    def fin_reponse(self, sources, statistiques):
        self.a_repondu = True
        if sources:
            print(couleur("  Sources : " + "  ·  ".join(sources[:4]), DISCRET))
        vitesse = 0.0
        if statistiques["duree_ecriture"] > 0:
            vitesse = statistiques["nb_tokens"] / statistiques["duree_ecriture"]
        morceaux = []
        if statistiques.get("duree_preparation", 0) >= 0.5:   # il a fallu attendre la fin du préchauffage
            morceaux.append(f"préparation {nombre_fr(statistiques['duree_preparation'])} s")
        if statistiques.get("duree_recherche"):
            morceaux.append(f"recherche {nombre_fr(statistiques['duree_recherche'])} s")
        morceaux.append(f"lecture {nombre_fr(statistiques['duree_lecture'])} s")
        morceaux.append(f"{statistiques['nb_tokens']} tokens à {nombre_fr(vitesse)} tokens/s")
        ligne = "  " + "  ·  ".join(morceaux)
        if statistiques["interrompu"]:
            ligne += "  ·  réponse arrêtée"
        elif statistiques["coupee"]:
            ligne += "  ·  réponse coupée (longueur maximale atteinte)"
        print(couleur(ligne, DISCRET))

    def recherche_debut(self, requete):
        print(couleur(f"  Recherche sur le web : « {requete} »…", DISCRET), end="", flush=True)

    def recherche_fin(self, recherche):
        if recherche is None:
            print()
            self.info("Recherche annulée.")
        elif recherche.a_trouve():
            if recherche.resultats:
                detail = f"{len(recherche.resultats)} résultat(s)" + (" + 1 page lue" if recherche.page else "")
            else:
                detail = "page lue"
            print(couleur(f" {detail} ({recherche.moteur}, {nombre_fr(recherche.duree)} s)", DISCRET))
        else:
            print()
            self.erreur("Rien trouvé : " + (" ; ".join(recherche.erreurs) or "aucun résultat"))

    def afficher_cerveau(self, fiche):
        if fiche["vide"]:
            self.info("Mon cerveau est vide. Tape /cerveau questions pour le remplir.")
            return
        if fiche["lignes"]:
            largeur = max(len(libelle) for libelle, _ in fiche["lignes"])
            for libelle, valeur in fiche["lignes"]:
                print("  " + couleur(libelle.ljust(largeur), DISCRET) + "  " + valeur)
        if fiche["souvenirs"]:
            print(couleur("  Souvenirs", DISCRET))
            for numero, souvenir in enumerate(fiche["souvenirs"], 1):
                print(f"  {numero:>3}. {souvenir}")
        self.info(f"{fiche['taille']} tokens sur {fiche['limite']} possibles  ·  {fiche['chemin']}")

    def alerte(self, texte):
        """Fin d'un minuteur (arrive d'un autre fil, pendant que tu écris peut-être)."""
        sys.stdout.write("\n" + prefixe_over() + couleur("⏰ " + texte, OR) + "\n")
        if readline is not None:
            sys.stdout.write(invite_saisie() + readline.get_line_buffer())
        sys.stdout.flush()

    def preparation(self, en_cours):
        if en_cours and not self.preparation_annoncee:
            self.preparation_annoncee = True
            self.info("Je lis mes consignes en arrière-plan : tu peux déjà écrire, je réponds dès que j'ai fini.")

    def historique(self, messages):
        # dans le Terminal, on rappelle seulement le dernier échange
        for auteur, texte in messages[-2:]:
            nom = "Toi" if auteur == "utilisateur" else "O.V.E.R."
            resume = " ".join(texte.split())
            self.info(f"{nom} › {resume[:110]}{'…' if len(resume) > 110 else ''}")

    def parole(self, en_cours):
        pass   # dans le Terminal, rien à afficher : on l'entend

    def afficher_aide(self, lignes):
        largeur = max(len(commande) for commande, _ in lignes)
        for commande, explication in lignes:
            self.info(f"{commande.ljust(largeur)}   {explication}")
        self.info(f"{'Ctrl+C'.ljust(largeur)}   pendant une réponse ou une recherche : l'arrête sans quitter")


# --- Programme principal ----------------------------------------------------

def lire_arguments():
    analyseur = argparse.ArgumentParser(description="O.V.E.R. — chat avec un modèle local")
    analyseur.add_argument("--modele", default=reglages.CHEMIN_MODELE,
                           help="chemin vers un fichier .gguf (par défaut : Ministral 3 3B dans modeles/)")
    analyseur.add_argument("--fils", type=int, default=0,
                           help="nombre de cœurs à utiliser (par défaut : tous les cœurs physiques)")
    analyseur.add_argument("--cerveau", default=reglages.CHEMIN_CERVEAU,
                           help="chemin du fichier cerveau (par défaut : cerveau.json à côté du programme)")
    analyseur.add_argument("--test-web", metavar="REQUETE",
                           help="essaie la recherche web sans charger le modèle, puis quitte")
    return analyseur.parse_args()


def tester_web(interface, requete):
    interface.recherche_debut(requete)
    debut = time.perf_counter()
    try:
        recherche = web.rechercher(requete, nombre=reglages.NOMBRE_RESULTATS, max_page=reglages.TAILLE_PAGE,
                                   adresse_searxng=reglages.ADRESSE_SEARXNG)
    except KeyboardInterrupt:
        interface.recherche_fin(None)
        return 1
    recherche.duree = time.perf_counter() - debut
    interface.recherche_fin(recherche)
    if recherche.erreurs and recherche.a_trouve():
        interface.info("En chemin : " + " ; ".join(recherche.erreurs))
    print()
    print(recherche.pour_modele(reglages.TAILLE_RESULTATS))
    return 0 if recherche.a_trouve() else 1


def main():
    arguments = lire_arguments()
    interface = InterfaceTerminal()
    if arguments.test_web:
        return tester_web(interface, arguments.test_web)

    # tes paramètres (web, actions…) passent avant reglages.py, y compris pour l'en-tête ci-dessous
    from parametres import Parametres
    Parametres.charger(os.path.join(os.path.dirname(os.path.abspath(arguments.cerveau)),
                                    "parametres.json")).appliquer_systeme()
    chemin_modele = os.path.abspath(arguments.modele)
    nb_fils = arguments.fils if arguments.fils > 0 else noyau.nombre_coeurs_physiques()
    nom_modele = os.path.basename(chemin_modele).replace(".gguf", "")
    morceaux_mode = ["modèle local"]
    if reglages.RECHERCHE_WEB:
        morceaux_mode.append("web")
    if reglages.ACTIONS_MAC:
        morceaux_mode.append("actions sur le Mac")
    morceaux_mode.append("voix (/voix)")
    mode = "  ·  ".join(morceaux_mode) if len(morceaux_mode) > 1 else "chat 100 % local"
    print()
    print("  " + couleur("O.V.E.R.", GRAS, SARCELLE) + couleur(f"  ·  version {identite.VERSION}  ·  {mode}", DISCRET))
    print(couleur(f"  {nom_modele}  ·  {nb_fils} cœur(s)  ·  contexte {reglages.TAILLE_CONTEXTE} tokens", DISCRET))
    print(couleur("  /aide pour les commandes", DISCRET))
    print()

    print(couleur("  Chargement du modèle…", DISCRET), end="", flush=True)
    try:
        session, infos = noyau.ouvrir_session(interface, chemin_modele, arguments.cerveau, nb_fils)
    except (RuntimeError, ValueError) as erreur:  # modèle absent ou abîmé, mémoire insuffisante…
        print()
        interface.erreur(f"Impossible de charger le modèle : {erreur}")
        return 1
    print(couleur(f" prêt en {nombre_fr(infos['duree_chargement'])} s.", DISCRET))
    print()

    session.demarrer()
    print()

    while True:
        try:
            saisie = input(invite_saisie()).strip()
        except KeyboardInterrupt:
            print()
            if session.voix.parle():   # Ctrl+C pendant qu'il parle : il se tait, on ne quitte pas
                session.voix.taire()
                continue
            break
        except EOFError:
            print()
            break
        if not saisie:
            continue
        interface.a_repondu = False
        try:
            if not session.recevoir(saisie):
                break
        except KeyboardInterrupt:
            print()
            interface.info("Interrompu.")
        if interface.a_repondu:
            print()

    session.terminer()
    print(couleur("À bientôt !", DISCRET))
    return 0


if __name__ == "__main__":
    sys.exit(main())
