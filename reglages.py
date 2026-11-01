# -*- coding: utf-8 -*-
"""
Réglages d'O.V.E.R. — tout ce qu'on peut ajuster, au même endroit.
Utilisés par le Terminal (over_chat.py) comme par l'application Mac (serveur.py).
"""

import os

DOSSIER_PROJET = os.path.dirname(os.path.abspath(__file__))

# Où sont rangées tes données (cerveau, paramètres, conversation, modèle, écoute) :
#  - dossier O.V.E.R. installé avec installer.sh : dans ce dossier, à côté des scripts ;
#  - application emballée (OVER.app avec Python dedans) : l'application indique son dossier,
#    ~/Library/Application Support/O.V.E.R. (lisible par toi seul), et les scripts restent
#    dans l'application, en lecture seule.
DOSSIER_DONNEES = os.path.abspath(os.environ.get("OVER_DONNEES") or DOSSIER_PROJET)
EMBALLE = os.environ.get("OVER_EMBALLE") == "1"   # rien ne s'installe avec pip : tout est déjà dans l'application

# --- Modèle --------------------------------------------------------------------
NOM_MODELE = "Ministral-3-3B-Instruct-2512-Q4_K_M.gguf"
CHEMIN_MODELE = os.path.join(DOSSIER_DONNEES, "modeles", NOM_MODELE)
# Téléchargé une fois (Mistral AI, licence Apache 2.0) ; taille et empreinte SHA-256 vérifiées
ADRESSE_MODELE = "https://huggingface.co/mistralai/Ministral-3-3B-Instruct-2512-GGUF/resolve/main/" + NOM_MODELE
TAILLE_MODELE = 2147023008
EMPREINTE_MODELE = "9ed150d4367e68df0ac8e1540f6ddc65b42d0ee26378329d1ecbca60f93fc5f8"
TAILLE_CONTEXTE = 4096      # mémoire de travail du modèle, en tokens (conversation + réponse)
TOKENS_REPONSE_MAX = 768    # longueur maximale d'une réponse
TEMPERATURE = 0.15          # valeur conseillée par Mistral pour ce modèle
MARGE_SECURITE = 64         # tokens gardés en réserve pour le gabarit de discussion
TAILLE_LOT_LECTURE = 256    # tokens lus d'un coup ; plus petit = bouton Arrêter plus réactif pendant la lecture

# --- Cerveau -------------------------------------------------------------------
CHEMIN_CERVEAU = os.path.join(DOSSIER_DONNEES, "cerveau.json")
APPRENTISSAGE_AUTO = True   # repérer en discutant ce qui mérite d'aller dans le cerveau
FAITS_MAX_PAR_REPONSE = 2   # au plus 2 questions « je range ça ? » après une réponse

# --- Recherche web -------------------------------------------------------------
RECHERCHE_WEB = True        # le modèle peut chercher sur Internet (les mots-clés sortent de la machine)
ADRESSE_SEARXNG = ""        # ex. "https://recherche.iaover.fr" pour passer par ta propre instance SearXNG
NOMBRE_RESULTATS = 4        # résultats de recherche donnés au modèle
TAILLE_PAGE = 1200          # caractères gardés de la page lue
TAILLE_RESULTATS = 2600     # caractères au plus pour tout ce qui vient du web (≈ 700 tokens)
# Sur un i5 2 cœurs, le modèle lit environ 20 à 40 tokens par seconde : des résultats plus longs
# rendent la réponse plus précise mais plus lente à démarrer.

# --- Actions sur le Mac ------------------------------------------------------------
ACTIONS_MAC = True          # ouvrir des apps et des sites, son, heure, minuteurs (notification à la fin), rappels
ACTIONS_MAX_PAR_REPONSE = 2

# --- Conversation --------------------------------------------------------------------
LONGUEUR_MAX_MESSAGE = 20_000   # caractères : au-delà, le message est refusé (≈ 5 000 tokens, plus que la mémoire)
GARDER_CONVERSATION = True  # reprendre la conversation au lancement suivant (fichier conversation.json)
REPRISE_MAX_TOKENS = 800    # au plus ~800 tokens repris (sans la mémoire sur disque, tout est relu au démarrage)
# Ce que le modèle a déjà lu est gardé sur le disque (memoire_modele.bin, 150 à 400 Mo) et rechargé au
# lancement : il ne relit plus sa consigne ni la conversation. Seulement si GARDER_CONVERSATION est vrai.
MEMOIRE_SUR_DISQUE = True

# Les choix de l'utilisateur (briefing, voix, écoute) ne sont pas ici : ils sont dans parametres.json,
# modifiables depuis la fenêtre Réglages de l'application (⌘,) ou avec /réglages.
