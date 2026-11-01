# -*- coding: utf-8 -*-
"""
Cerveau d'O.V.E.R. — la mémoire durable de l'assistant.

Ce que l'assistant sait de son utilisateur est rangé dans cerveau.json,
à côté du programme. Le fichier est du JSON lisible : on peut l'ouvrir
et le corriger à la main.

Trois façons de remplir le cerveau :
  1. les questions d'accueil, posées quand le cerveau est vide ;
  2. les commandes /cerveau ajoute, modifie, oublie… (voir /cerveau aide) ;
  3. l'apprentissage en discutant : le modèle signale une information
     à retenir par une ligne « [CERVEAU] … » en fin de réponse ; cette
     ligne est cachée à l'affichage (module balises) et l'utilisateur
     confirme ou non.

Ce module ne touche pas au terminal : l'affichage et la saisie sont
fournis par le programme principal (fonctions « dire » et « poser »).
"""

import datetime
import json
import os
import re
import tempfile

VERSION_CERVEAU = 1

# Questions d'accueil, dans l'ordre : (clé, question en tutoiement, question en vouvoiement).
# La première question n'a qu'une seule forme : on ne sait pas encore s'il faut dire « tu » ou « vous ».
QUESTIONS = (
    ("adresse",
     "On se tutoie ou on se vouvoie ? (tu / vous)",
     "On se tutoie ou on se vouvoie ? (tu / vous)"),
    ("prenom",
     "Comment veux-tu que je t'appelle ?",
     "Comment voulez-vous que je vous appelle ?"),
    ("activite",
     "Qu'est-ce que tu fais dans la vie ?",
     "Que faites-vous dans la vie ?"),
    ("lieu",
     "Où habites-tu (ville ou région) ?",
     "Où habitez-vous (ville ou région) ?"),
    ("centres_interet",
     "Quelles sont tes passions ou tes centres d'intérêt ?",
     "Quelles sont vos passions ou vos centres d'intérêt ?"),
    ("style_reponses",
     "Tu préfères des réponses courtes ou détaillées ? (courtes / détaillées)",
     "Préférez-vous des réponses courtes ou détaillées ? (courtes / détaillées)"),
)

# Réponses proposées en boutons dans l'application (libellé, valeur)
CHOIX_QUESTIONS = {
    "adresse": [("Tu", "tu"), ("Vous", "vous")],
    "style_reponses": [("Courtes", "courtes"), ("Détaillées", "détaillées")],
}

QUESTION_LIBRE = (
    "Autre chose que je devrais savoir sur toi ?",
    "Autre chose que je devrais savoir sur vous ?",
)

# Libellés pour l'affichage (/cerveau) et pour la consigne donnée au modèle
LIBELLES = {
    "adresse": "Relation",
    "prenom": "Prénom",
    "activite": "Activité",
    "lieu": "Lieu de vie",
    "centres_interet": "Centres d'intérêt",
    "style_reponses": "Réponses",
}

# Marqueur que le modèle écrit pour signaler une information à retenir
MARQUEUR = "[CERVEAU]"  # repéré dans les réponses par le module balises

# « non », « non je pense pas », « rien de spécial »… : une réponse qui ne dit rien à retenir
MOTIF_REPONSE_NEGATIVE = re.compile(
    r"^(non|nan|nope|no|rien|aucune?|pas (vraiment|grand[- ]chose|pour l'instant|pour le moment|d'idée)"
    r"|je (ne )?(pense|crois|vois) pas)\b[^,;:]*$",
    re.IGNORECASE,
)

TAILLE_MAX_FAIT = 300  # caractères ; au-delà, ce n'est plus une information courte


def maintenant():
    return datetime.datetime.now().isoformat(timespec="seconds")


def est_reponse_negative(reponse):
    texte = reponse.strip().strip(".!…").strip()
    if not texte or len(texte) > 40 or "mais" in texte.lower():
        return False
    return bool(MOTIF_REPONSE_NEGATIVE.match(texte))


def normaliser_adresse(reponse):
    """« tu », « vous », « t », « on se vouvoie »… → "tu" ou "vous" (vide → rien)."""
    texte = reponse.strip().lower()
    if not texte:
        return ""
    if texte.startswith("v") or "vouvo" in texte or "vous" in texte.split():
        return "vous"
    return "tu"


def normaliser_style(reponse):
    """« c », « courtes », « d », « détaillé »… → forme propre ; une phrase libre est gardée telle quelle."""
    texte = reponse.strip()
    minuscules = texte.lower()
    if len(minuscules) <= 12:
        if minuscules.startswith("c"):
            return "courtes"
        if minuscules.startswith("d"):
            return "détaillées"
    return texte


def normaliser_activite(reponse):
    """« je suis apprentie développeur » → « apprentie développeur »."""
    return re.sub(r"^(je suis|je travaille (comme|en tant que|dans)|je bosse (comme|dans)|je fais)\s+(une?\s+)?",
                  "", reponse.strip(), flags=re.IGNORECASE).strip() or reponse.strip()


def normaliser_lieu(reponse):
    """« j'habite à Lyon » → « Lyon »."""
    return re.sub(r"^(j'habite|je vis|je suis)\s+(à|a|en|au|aux|dans|vers|près de|pres de)?\s*",
                  "", reponse.strip(), flags=re.IGNORECASE).strip() or reponse.strip()


def normaliser_prenom(reponse):
    """« appelle-moi Camille », « je m'appelle Camille » → « Camille »."""
    return re.sub(r"^(appelle[- ]moi|appelez[- ]moi|je m'appelle|c'est)\s+", "", reponse.strip(),
                  flags=re.IGNORECASE).strip() or reponse.strip()


NORMALISATIONS = {
    "adresse": normaliser_adresse,
    "style_reponses": normaliser_style,
    "activite": normaliser_activite,
    "lieu": normaliser_lieu,
    "prenom": normaliser_prenom,
}

# Noms acceptés pour chaque information de la fiche, dans les commandes /cerveau
# (comparés sans accents ni majuscules).
NOMS_CHAMPS = {
    "adresse": ("relation", "tutoiement", "vouvoiement", "tu vous", "adresse"),
    "prenom": ("prenom", "nom", "surnom", "pseudo", "name"),
    "activite": ("activite", "metier", "travail", "job", "boulot", "profession", "etudes", "occupation"),
    "lieu": ("lieu", "lieu de vie", "ville", "region", "habite", "domicile", "city"),
    "centres_interet": ("centres d interet", "centre d interet", "interets", "interet", "passions", "passion",
                        "hobbies", "hobby", "loisirs"),
    "style_reponses": ("reponses", "style", "style des reponses", "longueur des reponses"),
}


def forme_nom(texte):
    """« Centres d'intérêt » → « centres d interet » (pour reconnaître le nom d'une information)."""
    import unicodedata
    sans_accents = "".join(c for c in unicodedata.normalize("NFD", texte.lower())
                           if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", sans_accents).split())


def trouver_champ(nom):
    """« ville » → "lieu", « Prénom » → "prenom" ; None si ce n'est pas une information de la fiche."""
    cle = forme_nom(nom)
    for champ, noms in NOMS_CHAMPS.items():
        if cle in noms or cle == forme_nom(LIBELLES[champ]):
            return champ
    return None


def forme_comparable(texte):
    """Minuscules, sans ponctuation ni espaces en trop : pour repérer les doublons."""
    return " ".join(re.sub(r"[^\w\s]", " ", texte.lower()).split())


def nettoyer_fait(texte):
    """Nettoie une information proposée par le modèle ; renvoie "" si elle est inutilisable."""
    fait = " ".join(texte.split())
    fait = re.sub(r"(\*\*|__|\*|`)", "", fait)          # gras, italique, code Markdown
    fait = fait.strip(" -–—•*:\"'«»“”")
    if len(fait) < 4 or len(fait) > TAILLE_MAX_FAIT:
        return ""
    return fait


MOTS_VIDES = {
    "avec", "dans", "pour", "sans", "sous", "chez", "vers", "elle", "elles", "nous", "vous", "leur", "leurs",
    "cette", "celui", "celle", "ceux", "mais", "donc", "comme", "aussi", "très", "plus", "moins", "tout",
    "tous", "toute", "toutes", "être", "avoir", "fait", "faire", "suis", "sont", "était", "utilisateur",
}


def mots_significatifs(texte):
    return {mot for mot in re.findall(r"\w{4,}", texte.lower()) if mot not in MOTS_VIDES}


def fait_vient_de_l_utilisateur(fait, message, prenom=""):
    """Garde-fou : l'information proposée doit reprendre au moins un mot du message de
    l'utilisateur (hors prénom et mots vides). Écarte ce que le modèle tire de sa consigne,
    de son identité ou du web, par exemple après « t'es qui ? »."""
    mots_du_fait = mots_significatifs(fait) - mots_significatifs(prenom)
    return bool(mots_du_fait & mots_significatifs(message))


# Réponses aux questions oui / non (« Je range ça ? », « Je crée ce rappel ? ») : « oui » ou « non »,
# éventuellement suivis de formules de politesse (« Oui, s'il te plaît », « non merci, c'est bon »).
# Un autre mot en fait une correction (« Non, j'ai deux chats »).
MOTS_OUI = ("o", "oui", "ouais", "ouai", "ok", "okay", "y", "yes", "yep", "d accord", "dac", "vas y", "volontiers",
            "bien sur", "absolument", "exactement", "carrement", "parfait", "je veux bien", "avec plaisir", "go")
MOTS_NON = ("n", "non", "nan", "no", "nope", "pas besoin", "laisse tomber", "surtout pas", "pas la peine")
POLITESSE_OUI = MOTS_OUI + ("merci", "beaucoup", "bien", "stp", "svp", "s il te plait", "s il vous plait", "c est bon",
                            "allez", "super", "top", "parfait", "tout a fait", "bien sur", "evidemment", "fais le",
                            "fait le", "c est parti", "nickel", "please", "thanks", "thank you")
POLITESSE_NON = MOTS_NON + ("merci", "beaucoup", "stp", "svp", "s il te plait", "s il vous plait", "c est bon",
                            "ca ira", "ca va", "pas maintenant", "plus tard", "oublie", "non plus")


def _suite_de(mots, expressions):
    """Les mots ne sont-ils qu'une suite de ces expressions (« merci beaucoup », « s il te plait ») ?"""
    expressions = sorted((tuple(e.split()) for e in expressions), key=len, reverse=True)
    position = 0
    while position < len(mots):
        for expression in expressions:
            if tuple(mots[position:position + len(expression)]) == expression:
                position += len(expression)
                break
        else:
            return False
    return True


def interpreter_confirmation(reponse):
    """Réponse à « je retiens ça ? » → ("oui", None), ("non", None) ou ("corrige", texte corrigé)."""
    texte = reponse.strip()
    mots = forme_nom(texte).split()
    if not mots:
        return "non", None
    for decision, debuts, politesse in (("oui", MOTS_OUI, POLITESSE_OUI), ("non", MOTS_NON, POLITESSE_NON)):
        for debut in sorted((tuple(d.split()) for d in debuts), key=len, reverse=True):
            if tuple(mots[:len(debut)]) == debut and _suite_de(mots[len(debut):], politesse):
                return decision, None
    if len(texte) <= 3:
        return "non", None  # réponse trop courte pour être une correction : dans le doute, on n'enregistre rien
    return "corrige", texte


# --- Le cerveau -----------------------------------------------------------------

class Cerveau:
    """Ce qu'O.V.E.R. sait de son utilisateur, rangé dans un fichier JSON."""

    def __init__(self, chemin):
        self.chemin = chemin
        self.utilisateur = {}   # clé → texte (prénom, activité…)
        self.souvenirs = []     # [{"texte": ..., "date": "AAAA-MM-JJ", "source": ...}]
        self.cree_le = None
        self.modifie_le = None
        self.alerte = None      # message à afficher si le fichier n'a pas pu être lu

    # --- Lecture / écriture -------------------------------------------------

    @classmethod
    def charger(cls, chemin):
        cerveau = cls(chemin)
        if not os.path.exists(chemin):
            return cerveau
        try:
            with open(chemin, "r", encoding="utf-8") as fichier:
                contenu = fichier.read()
            if not contenu.strip():
                return cerveau   # fichier vidé exprès : un cerveau vide, sans alerte ni copie de côté
            donnees = json.loads(contenu)
            if not isinstance(donnees, dict):
                raise ValueError("le contenu n'est pas un objet JSON")
        except (OSError, ValueError) as erreur:
            # Fichier abîmé (souvent une virgule oubliée après une modification à la main) :
            # on le met de côté au lieu de l'écraser, pour ne rien perdre.
            horodatage = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            copie = f"{chemin}.abime-{horodatage}"
            try:
                os.replace(chemin, copie)
                cerveau.alerte = (
                    f"Mon cerveau était illisible ({erreur}). "
                    f"Je l'ai mis de côté dans {os.path.basename(copie)} et je repars de zéro."
                )
            except OSError:
                cerveau.alerte = f"Mon cerveau est illisible ({erreur}) et je n'ai pas pu le mettre de côté."
            return cerveau

        utilisateur = donnees.get("utilisateur", {})
        if isinstance(utilisateur, dict):
            for cle, valeur in utilisateur.items():
                if str(cle).strip() and isinstance(valeur, (str, int, float)) and str(valeur).strip():
                    cerveau.utilisateur[str(cle)] = str(valeur).strip()

        souvenirs = donnees.get("souvenirs", [])
        if isinstance(souvenirs, list):
            for souvenir in souvenirs:
                if isinstance(souvenir, str) and souvenir.strip():
                    souvenir = {"texte": souvenir}  # forme simple acceptée si écrite à la main
                if isinstance(souvenir, dict) and str(souvenir.get("texte", "")).strip():
                    propre = {
                        "texte": str(souvenir["texte"]).strip(),
                        "date": str(souvenir.get("date", "")),
                    }
                    if souvenir.get("source"):
                        propre["source"] = str(souvenir["source"])
                    cerveau.souvenirs.append(propre)

        cerveau.cree_le = donnees.get("cree_le")
        cerveau.modifie_le = donnees.get("modifie_le")
        return cerveau

    def sauvegarder(self):
        """Écriture sûre : fichier temporaire puis remplacement, pour ne jamais laisser un cerveau à moitié écrit."""
        instant = maintenant()
        if not self.cree_le:
            self.cree_le = instant
        self.modifie_le = instant
        donnees = {
            "version": VERSION_CERVEAU,
            "cree_le": self.cree_le,
            "modifie_le": self.modifie_le,
            "utilisateur": self.utilisateur,
            "souvenirs": self.souvenirs,
        }
        dossier = os.path.dirname(os.path.abspath(self.chemin))
        descripteur, chemin_temporaire = tempfile.mkstemp(prefix=".cerveau-", suffix=".tmp", dir=dossier)
        try:
            with os.fdopen(descripteur, "w", encoding="utf-8") as fichier:
                json.dump(donnees, fichier, ensure_ascii=False, indent=2)
                fichier.write("\n")
            os.chmod(chemin_temporaire, 0o600)  # infos personnelles : lisible par toi seul
            os.replace(chemin_temporaire, self.chemin)
        except BaseException:
            if os.path.exists(chemin_temporaire):
                os.unlink(chemin_temporaire)
            raise

    # --- Contenu ------------------------------------------------------------

    def est_vide(self):
        return not self.utilisateur and not self.souvenirs

    def tutoie(self):
        return self.utilisateur.get("adresse") != "vous"

    def prenom(self):
        return self.utilisateur.get("prenom", "")

    def ajouter_souvenir(self, texte, source="manuel"):
        """source : "accueil" (questions), "manuel" (/cerveau ajoute) ou "conversation" (appris en discutant)."""
        self.souvenirs.append({
            "texte": texte.strip(),
            "date": datetime.date.today().isoformat(),
            "source": source,
        })

    def oublier_souvenir(self, numero):
        """Retire le souvenir n° numero (en partant de 1). Renvoie son texte, ou None s'il n'existe pas."""
        if 1 <= numero <= len(self.souvenirs):
            return self.souvenirs.pop(numero - 1)["texte"]
        return None

    def definir(self, champ, valeur):
        """Range une information de la fiche (normalisée). Renvoie la valeur rangée."""
        normaliser = NORMALISATIONS.get(champ)
        valeur = normaliser(valeur) if normaliser else valeur.strip()
        self.utilisateur[champ] = valeur
        return valeur

    def oublier_champ(self, champ):
        """Efface une information de la fiche. Renvoie l'ancienne valeur, ou None."""
        return self.utilisateur.pop(champ, None)

    def remplacer_souvenir(self, numero, texte):
        """Remplace le texte du souvenir n° numero. Renvoie l'ancien texte, ou None."""
        if 1 <= numero <= len(self.souvenirs):
            ancien = self.souvenirs[numero - 1]["texte"]
            self.souvenirs[numero - 1]["texte"] = texte.strip()
            self.souvenirs[numero - 1]["date"] = datetime.date.today().isoformat()
            return ancien
        return None

    def chercher_souvenirs(self, mots):
        """Souvenirs qui contiennent ces mots : [(numéro, texte)]."""
        cible = forme_comparable(mots)
        return [(numero, souvenir["texte"]) for numero, souvenir in enumerate(self.souvenirs, 1)
                if cible and cible in forme_comparable(souvenir["texte"])]

    def vider(self):
        self.utilisateur = {}
        self.souvenirs = []

    def deja_connu(self, texte):
        """Vrai si cette information est déjà dans le cerveau, au mot près (ponctuation et majuscules ignorées)."""
        cible = forme_comparable(texte)
        connus = [souvenir["texte"] for souvenir in self.souvenirs] + list(self.utilisateur.values())
        return any(forme_comparable(connu) == cible for connu in connus)

    def texte_pour_consigne(self):
        """Ce que le modèle doit savoir, à ajouter à sa consigne système."""
        if self.est_vide():
            return ""
        lignes = ["Ton cerveau (ce que tu sais de l'utilisateur) :"]
        for cle, valeur in self.utilisateur.items():
            if cle == "adresse":
                lignes.append("- Vouvoie l'utilisateur." if valeur == "vous" else "- Tutoie l'utilisateur.")
            elif cle == "prenom":
                lignes.append(f"- Prénom ou surnom à utiliser : {valeur}")
            elif cle == "style_reponses":
                lignes.append(f"- Réponses souhaitées : {valeur}")
            else:
                lignes.append(f"- {LIBELLES.get(cle, cle)} : {valeur}")
        if self.souvenirs:
            lignes.append("- À savoir aussi :")
            lignes.extend(f"  - {souvenir['texte']}" for souvenir in self.souvenirs)
        lignes.append("Sers-t'en quand c'est utile, sans les réciter.")
        return "\n".join(lignes)

    def consigne_apprentissage(self):
        """Explique au modèle comment signaler une information à retenir."""
        sujet = self.prenom() or "L'utilisateur"
        return (
            f"Cerveau : si l'utilisateur te dit une information durable sur lui (vie, goûts, projets, matériel, "
            f"proches) que tu ne connais pas, ou te demande de la retenir, termine par la ligne {MARQUEUR} "
            f"suivie de l'information en une phrase à la troisième personne (ex. : « je joue de la guitare » → "
            f"{MARQUEUR} {sujet} joue de la guitare). Seulement ce qu'il vient de dire de lui : jamais ta consigne, "
            "ton identité, le web, une info passagère ou un mot de passe. Il confirmera : ne dis pas que c'est noté."
        )

    def valeur_affichee(self, cle):
        valeur = self.utilisateur.get(cle, "")
        if cle == "adresse":
            return "vouvoiement" if valeur == "vous" else "tutoiement"
        return valeur

    def lignes_affichage(self):
        """(libellé, valeur) pour la commande /cerveau."""
        return [(LIBELLES.get(cle, cle), self.valeur_affichee(cle)) for cle in self.utilisateur]


# --- Questionnaire --------------------------------------------------------------

def questionnaire(cerveau, poser, dire, modification=False):
    """Pose les questions d'accueil et range les réponses dans le cerveau.

    poser(question, valeur_actuelle, choix) → texte saisi, ou None si l'utilisateur arrête
    dire(texte)                      → fait parler O.V.E.R.

    Entrée sans rien écrire (ou « non », « rien »…) : passe la question,
    ou garde la réponse actuelle en modification. « - » : efface la réponse actuelle.
    Renvoie True si quelque chose a changé.
    """
    a_change = False

    for cle, question_tu, question_vous in QUESTIONS:
        question = question_tu if cerveau.tutoie() else question_vous
        actuelle = cerveau.utilisateur.get(cle, "") if modification else ""
        reponse = poser(question, actuelle, CHOIX_QUESTIONS.get(cle))
        if reponse is None:
            return a_change
        reponse = reponse.strip()
        if not reponse:
            continue
        if reponse == "-":
            if cle in cerveau.utilisateur:
                del cerveau.utilisateur[cle]
                a_change = True
            continue
        if cle != "adresse" and est_reponse_negative(reponse):
            continue
        normaliser = NORMALISATIONS.get(cle)
        valeur = normaliser(reponse) if normaliser else reponse
        if valeur and cerveau.utilisateur.get(cle) != valeur:
            cerveau.utilisateur[cle] = valeur
            a_change = True

    question = QUESTION_LIBRE[0] if cerveau.tutoie() else QUESTION_LIBRE[1]
    reponse = poser(question, "")
    if reponse and reponse.strip() and not est_reponse_negative(reponse):
        cerveau.ajouter_souvenir(reponse, source="accueil")
        a_change = True

    if a_change and not modification:
        prenom = cerveau.prenom()
        merci = f"Merci {prenom}" if prenom else "Merci"
        if cerveau.tutoie():
            dire(f"{merci}, c'est rangé dans mon cerveau. Tape /cerveau pour voir ce que je sais de toi.")
        else:
            dire(f"{merci}, c'est rangé dans mon cerveau. Tapez /cerveau pour voir ce que je sais de vous.")
    return a_change
