# -*- coding: utf-8 -*-
"""
Paramètres d'O.V.E.R. — les choix de l'utilisateur, modifiables pendant qu'il tourne.

À ne pas confondre avec reglages.py (réglages techniques, à modifier à la main) :
ici, ce sont les préférences de chacun (briefing, voix, écoute), qu'on change
depuis le panneau Paramètres de l'application (roue dentée, ⌘,) ou avec /paramètres.
On y trouve aussi les paramètres du système (web, actions, mémoire, créativité…), dont
la valeur par défaut vient de reglages.py.

Elles sont rangées dans parametres.json, à côté du cerveau. Le fichier est
écrit uniquement par le programme Python (l'application passe par lui).
"""

import datetime
import json
import os
import tempfile

import reglages

# Valeurs par défaut : rien d'envahissant tant que l'utilisateur ne l'a pas choisi.
DEFAUTS = {
    "briefing": "jamais",            # jamais | ouverture (à chaque ouverture) | quotidien (premier message du jour)
    "briefing_meteo": True,
    "briefing_rappels": True,
    "briefing_agenda": True,
    "briefing_ville": "",            # vide : la ville rangée dans le cerveau
    "voix_active": False,            # O.V.E.R. lit ses réponses à voix haute
    "voix_nom": "",                  # vide : la voix principale (Thomas) ; sinon une autre voix française de macOS
    "voix_vitesse": 185,             # débit : 185 = débit naturel de la voix, plus haut = plus rapide
    "voix_depart": "immediat",       # immediat (dès les premiers mots) | phrase (à la fin de chaque phrase)
    "ecoute_envoi_auto": True,       # application : le message dicté part tout seul à la fin
    "ecoute_modele": "precis",       # reconnaissance locale (whisper.cpp) : precis (small) | rapide (base)
    "raccourci_parler": "ctrl_option_espace",   # application : touches à maintenir pour parler, même en arrière-plan
    "arriere_plan": False,           # application : reste active quand on ferme la fenêtre (barre des menus)
    "masquer_dock": False,           # … et son icône quitte le Dock tant que la fenêtre est fermée
    "demarrage_session": False,      # O.V.E.R. s'ouvre à l'ouverture de la session macOS
    "demarrage_discret": True,       # … sans fenêtre (icône dans la barre des menus)
}

# Paramètres du système : leur valeur par défaut vient de reglages.py ; changés ici (fenêtre
# Paramètres ou /paramètres), ils prennent le dessus et s'appliquent tout de suite.
SYSTEME = {
    "recherche_web": "RECHERCHE_WEB",
    "actions_mac": "ACTIONS_MAC",
    "apprentissage_auto": "APPRENTISSAGE_AUTO",
    "garder_conversation": "GARDER_CONVERSATION",
    "memoire_sur_disque": "MEMOIRE_SUR_DISQUE",
    "temperature": "TEMPERATURE",
    "longueur_reponses": "TOKENS_REPONSE_MAX",
}
for _cle, _nom in SYSTEME.items():
    # la valeur de reglages.py est gardée à part : reglages.X sera ensuite modifié en direct
    if not hasattr(reglages, "_DEFAUT_" + _nom):
        setattr(reglages, "_DEFAUT_" + _nom, getattr(reglages, _nom))
    DEFAUTS[_cle] = getattr(reglages, "_DEFAUT_" + _nom)

CHOIX = {
    "briefing": ("jamais", "ouverture", "quotidien"),
    "voix_depart": ("immediat", "phrase"),
    "ecoute_modele": ("precis", "rapide"),
    "raccourci_parler": ("ctrl_option_espace", "option_espace", "cmd_shift_espace", "ctrl_espace", "aucun"),
}
BORNES = {
    "voix_vitesse": (120, 320),
    "temperature": (0.0, 1.0),
    "longueur_reponses": (128, 1536),
}
LIBELLES_BRIEFING = {
    "jamais": "jamais",
    "ouverture": "à chaque ouverture",
    "quotidien": "une fois par jour, au premier message",
}

# Noms acceptés dans la commande /réglages (sans accents ni majuscules) → clé
NOMS = {
    "briefing": "briefing", "meteo": "briefing_meteo", "rappels": "briefing_rappels",
    "agenda": "briefing_agenda", "calendrier": "briefing_agenda", "ville": "briefing_ville",
    "voix": "voix_active", "nom de voix": "voix_nom", "nom voix": "voix_nom", "vitesse": "voix_vitesse",
    "depart": "voix_depart", "depart voix": "voix_depart", "depart de la voix": "voix_depart",
    "envoi auto": "ecoute_envoi_auto", "envoi": "ecoute_envoi_auto",
    "ecoute": "ecoute_modele", "modele ecoute": "ecoute_modele", "micro": "ecoute_modele",
    "reconnaissance": "ecoute_modele",
    "raccourci": "raccourci_parler", "raccourci parler": "raccourci_parler", "touche": "raccourci_parler",
    "arriere plan": "arriere_plan", "fond": "arriere_plan", "barre des menus": "arriere_plan",
    "dock": "masquer_dock", "masquer dock": "masquer_dock", "icone dock": "masquer_dock",
    "demarrage": "demarrage_session", "session": "demarrage_session", "ouverture de session": "demarrage_session",
    "demarrage session": "demarrage_session", "lancement auto": "demarrage_session",
    "discret": "demarrage_discret", "demarrage discret": "demarrage_discret", "sans fenetre": "demarrage_discret",
    "web": "recherche_web", "recherche web": "recherche_web", "recherche": "recherche_web",
    "actions": "actions_mac", "actions mac": "actions_mac",
    "apprentissage": "apprentissage_auto", "apprendre": "apprentissage_auto",
    "conversation": "garder_conversation", "garder conversation": "garder_conversation",
    "memoire": "memoire_sur_disque", "demarrage rapide": "memoire_sur_disque", "memoire disque": "memoire_sur_disque",
    "temperature": "temperature", "creativite": "temperature",
    "longueur": "longueur_reponses", "longueur reponses": "longueur_reponses", "longueur max": "longueur_reponses",
}
SYNONYMES = {
    "briefing": {"non": "jamais", "off": "jamais", "aucun": "jamais", "jamais": "jamais",
                 "ouverture": "ouverture", "lancement": "ouverture", "demarrage": "ouverture", "toujours": "ouverture",
                 "quotidien": "quotidien", "jour": "quotidien", "chaque jour": "quotidien",
                 "par jour": "quotidien", "matin": "quotidien"},
    "voix_depart": {"immediat": "immediat", "tout de suite": "immediat", "mots": "immediat",
                    "premiers mots": "immediat", "rapide": "immediat",
                    "phrase": "phrase", "phrases": "phrase", "fin de phrase": "phrase", "naturel": "phrase"},
    "raccourci_parler": {"ctrl option espace": "ctrl_option_espace", "ctrl alt espace": "ctrl_option_espace",
                         "controle option espace": "ctrl_option_espace", "option espace": "option_espace",
                         "alt espace": "option_espace", "cmd shift espace": "cmd_shift_espace",
                         "commande maj espace": "cmd_shift_espace", "ctrl espace": "ctrl_espace",
                         "controle espace": "ctrl_espace", "aucun": "aucun", "non": "aucun", "off": "aucun"},
    "ecoute_modele": {"precis": "precis", "precise": "precis", "small": "precis", "qualite": "precis",
                      "rapide": "rapide", "base": "rapide", "leger": "rapide", "vite": "rapide"},
}
VRAI = {"oui", "on", "vrai", "true", "1", "active", "activee", "actif", "o", "yes"}
FAUX = {"non", "off", "faux", "false", "0", "desactive", "desactivee", "inactif", "n", "no"}


def forme_simple(texte):
    import re
    import unicodedata
    sans_accents = "".join(c for c in unicodedata.normalize("NFD", str(texte).lower())
                           if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", sans_accents).split())


class Parametres:
    def __init__(self, chemin):
        self.chemin = chemin
        self.valeurs = dict(DEFAUTS)
        self.etat = {}            # mémoire interne : date du dernier briefing, lieu de la météo…
        self.alerte = None

    @classmethod
    def charger(cls, chemin):
        parametres = cls(chemin)
        if not os.path.exists(chemin):
            return parametres
        try:
            with open(chemin, "r", encoding="utf-8") as fichier:
                contenu = fichier.read()
            if not contenu.strip():
                return parametres   # fichier vidé : les valeurs par défaut, sans alerte
            donnees = json.loads(contenu)
            if not isinstance(donnees, dict):
                raise ValueError("le contenu n'est pas un objet JSON")
        except (OSError, ValueError) as erreur:
            parametres.alerte = f"Mes réglages étaient illisibles ({erreur}) : je reprends les valeurs par défaut."
            return parametres
        for cle, valeur in donnees.items():
            if cle in DEFAUTS:
                ok, propre = cls.valider(cle, valeur)
                if ok:
                    parametres.valeurs[cle] = propre
        if isinstance(donnees.get("_etat"), dict):
            parametres.etat = donnees["_etat"]
        return parametres

    def sauvegarder(self):
        """Écriture sûre (fichier temporaire puis remplacement), lisible par toi seul."""
        # les paramètres du système ne sont écrits que s'ils diffèrent de reglages.py : modifier
        # reglages.py garde donc son effet tant qu'on n'a pas changé ce paramètre ici
        donnees = {cle: valeur for cle, valeur in self.valeurs.items()
                   if cle not in SYSTEME or valeur != getattr(reglages, "_DEFAUT_" + SYSTEME[cle], DEFAUTS[cle])}
        donnees["_etat"] = self.etat
        dossier = os.path.dirname(os.path.abspath(self.chemin))
        descripteur, temporaire = tempfile.mkstemp(prefix=".parametres-", suffix=".tmp", dir=dossier)
        try:
            with os.fdopen(descripteur, "w", encoding="utf-8") as fichier:
                json.dump(donnees, fichier, ensure_ascii=False, indent=2)
                fichier.write("\n")
            os.chmod(temporaire, 0o600)
            os.replace(temporaire, self.chemin)
        except BaseException:
            if os.path.exists(temporaire):
                os.unlink(temporaire)
            raise

    def __getitem__(self, cle):
        return self.valeurs[cle]

    @staticmethod
    def valider(cle, valeur):
        """Renvoie (True, valeur propre) ou (False, explication)."""
        defaut = DEFAUTS[cle]
        if valeur is None or not isinstance(valeur, (str, bool, int, float)):
            return False, "valeur manquante ou illisible"   # null, liste, objet… venus de l'application
        if isinstance(defaut, bool):
            if isinstance(valeur, bool):
                return True, valeur
            simple = forme_simple(valeur)
            if simple in VRAI:
                return True, True
            if simple in FAUX:
                return True, False
            return False, "réponds oui ou non"
        if isinstance(defaut, (int, float)):
            try:
                nombre = float(str(valeur).replace(",", ".").replace("%", "").strip())
            except ValueError:
                return False, "il faut un nombre"
            if cle == "temperature" and 1.0 < nombre <= 100:
                nombre = nombre / 100   # « 30 » ou « 30 % » → 0,3
            bas, haut = BORNES.get(cle, (nombre, nombre))
            nombre = max(bas, min(haut, nombre))
            return True, (round(nombre, 2) if isinstance(defaut, float) else int(nombre))
        texte = " ".join(str(valeur).split())[:80]
        if cle in CHOIX:
            simple = forme_simple(texte)
            texte = SYNONYMES.get(cle, {}).get(simple, simple)
            if texte not in CHOIX[cle]:
                return False, "choix possibles : " + ", ".join(CHOIX[cle])
        return True, texte

    def definir(self, cle, valeur):
        """Change un paramètre et l'enregistre. Renvoie (True, valeur) ou (False, explication)."""
        if cle not in DEFAUTS:
            return False, "paramètre inconnu"
        ok, propre = self.valider(cle, valeur)
        if not ok:
            return False, propre
        self.valeurs[cle] = propre
        self.sauvegarder()
        self.appliquer_systeme()
        return True, propre

    def appliquer_systeme(self):
        """Recopie les paramètres du système dans reglages : tout le programme les lit là."""
        for cle, nom in SYSTEME.items():
            setattr(reglages, nom, self.valeurs[cle])

    # --- briefing quotidien ---

    def briefing_deja_fait_aujourdhui(self):
        return self.etat.get("dernier_briefing") == datetime.date.today().isoformat()

    def noter_briefing(self):
        self.etat["dernier_briefing"] = datetime.date.today().isoformat()
        try:
            self.sauvegarder()
        except OSError:
            pass

    def pour_application(self, voix_disponibles=(), etat_voix=None, etat_ecoute=None, etat_demarrage=""):
        """Ce que l'application affiche dans son panneau Paramètres.
        voix_disponibles : fiches des voix françaises de macOS (id, nom, libelle, langue, licence) ;
        etat_voix : la voix (etat, message, erreur, dossier, choisie, principale) ;
        etat_ecoute : installation de l'écoute (etat, message, erreur, modeles) ;
        etat_demarrage : actif | a_mettre_a_jour | inactif (fiche d'ouverture de session)."""
        return {
            "valeurs": dict(self.valeurs),
            "choix_briefing": [[cle, LIBELLES_BRIEFING[cle]] for cle in CHOIX["briefing"]],
            "voix_disponibles": list(voix_disponibles),
            "bornes_vitesse": list(BORNES["voix_vitesse"]),
            "bornes_longueur": list(BORNES["longueur_reponses"]),
            "etat_voix": dict(etat_voix or {}),
            "etat_ecoute": dict(etat_ecoute or {}),
            "etat_demarrage": etat_demarrage,
        }

    def lignes_affichage(self):
        """(nom à taper, valeur, explication) pour /paramètres."""
        v = self.valeurs
        oui_non = {True: "oui", False: "non"}
        return [
            ("briefing", v["briefing"], LIBELLES_BRIEFING[v["briefing"]] + " — jamais, ouverture ou quotidien"),
            ("météo", oui_non[v["briefing_meteo"]], "météo du jour dans le briefing"),
            ("rappels", oui_non[v["briefing_rappels"]], "rappels du jour (app Rappels)"),
            ("agenda", oui_non[v["briefing_agenda"]], "rendez-vous du jour (app Calendrier)"),
            ("ville", v["briefing_ville"] or "(celle du cerveau)", "ville de la météo"),
            ("voix", oui_non[v["voix_active"]], "lire les réponses à voix haute"),
            ("nom de voix", v["voix_nom"] or "(voix principale : Thomas)", "voix de macOS utilisée (/voix liste)"),
            ("vitesse", str(v["voix_vitesse"]), "débit de la voix, de 120 à 320 (185 = naturel)"),
            ("départ", v["voix_depart"], "immediat (dès les premiers mots) ou phrase (à chaque fin de phrase)"),
            ("écoute", v["ecoute_modele"], "reconnaissance du micro, sur le Mac : precis ou rapide (/écoute)"),
            ("envoi auto", oui_non[v["ecoute_envoi_auto"]], "application : le message dicté part tout seul"),
            ("raccourci", v["raccourci_parler"], "application : touches à maintenir pour parler, même en "
                                                 "arrière-plan (ctrl option espace, option espace, cmd shift espace, "
                                                 "ctrl espace, aucun)"),
            ("arrière-plan", oui_non[v["arriere_plan"]], "application : reste active fenêtre fermée (barre des menus)"),
            ("dock", oui_non[v["masquer_dock"]], "en arrière-plan, cacher l'icône du Dock quand la fenêtre est fermée"),
            ("démarrage", oui_non[v["demarrage_session"]], "s'ouvrir à l'ouverture de ta session macOS"),
            ("discret", oui_non[v["demarrage_discret"]], "à l'ouverture de session : sans fenêtre (barre des menus)"),
            ("web", oui_non[v["recherche_web"]], "recherche sur Internet"),
            ("actions", oui_non[v["actions_mac"]], "actions sur le Mac (apps, sites, son, minuteurs, rappels)"),
            ("apprentissage", oui_non[v["apprentissage_auto"]], "proposer de retenir ce que tu dis de toi"),
            ("conversation", oui_non[v["garder_conversation"]], "reprendre la conversation au lancement suivant"),
            ("mémoire", oui_non[v["memoire_sur_disque"]], "démarrage rapide (lecture du modèle gardée sur le disque)"),
            ("créativité", str(v["temperature"]).replace(".", ","), "0 = précis, 1 = inventif (conseillé : 0,15)"),
            ("longueur", str(v["longueur_reponses"]), "longueur maximale d'une réponse, en tokens (128 à 1536)"),
        ]


def cle_depuis_nom(nom):
    """« Météo » → "briefing_meteo" ; None si inconnu."""
    simple = forme_simple(nom)
    if simple in NOMS:
        return NOMS[simple]
    return simple.replace(" ", "_") if simple.replace(" ", "_") in DEFAUTS else None
