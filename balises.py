# -*- coding: utf-8 -*-
"""
Balises d'O.V.E.R. — comment le modèle parle au programme.

Le modèle peut terminer sa réponse par des lignes spéciales :
  [CERVEAU] information à retenir    → proposée à l'utilisateur pour le cerveau
  [RECHERCHE] mots-clés ou adresse   → le programme cherche sur le web
  [ACTION] nom : arguments           → le programme agit sur le Mac (module actions)

Ces lignes ne sont jamais affichées : le filtre les retire de la réponse au fil
de l'eau et les range à part. Le texte qui pourrait être le début d'une balise,
ainsi que les espaces en fin de morceau, sont retenus un instant, le temps de
voir ce qui suit.
"""

import re

NOMS_BALISES = ("cerveau", "recherche", "action")
MOTIF_BALISE = re.compile(
    r"\*{0,2}\[\s*(" + "|".join(NOMS_BALISES) + r")\s*\]\*{0,2}\s*:?[ \t]*",
    re.IGNORECASE,
)
DEBUTS_POSSIBLES = tuple(prefixe + "[" + nom + "]" for nom in NOMS_BALISES for prefixe in ("", "*", "**"))
LONGUEUR_MAX_DEBUT = max(len(debut) for debut in DEBUTS_POSSIBLES)
TAILLE_MAX_CONTENU = 300


def longueur_debut_balise(texte):
    """Nombre de caractères en fin de texte qui pourraient être le début d'une balise."""
    fin = texte[-LONGUEUR_MAX_DEBUT:].lower()
    for longueur in range(len(fin), 0, -1):
        morceau = fin[-longueur:]
        if any(debut.startswith(morceau) for debut in DEBUTS_POSSIBLES):
            return longueur
    return 0


def nettoyer_contenu(texte):
    contenu = " ".join(texte.split()).strip(" -–—•*:\"'«»“”")
    if len(contenu) < 2 or len(contenu) > TAILLE_MAX_CONTENU:
        return ""
    return contenu


class FiltreBalises:
    """Trie la réponse du modèle : le texte à afficher d'un côté, les balises de l'autre."""

    def __init__(self):
        self.tampon = ""
        self.balise_en_cours = None   # nom de la balise dont on lit le contenu
        self.contenu_en_cours = ""
        self.balises = []             # [(nom, contenu)] dans l'ordre d'arrivée
        self.deja_affiche = False
        self.apres_balise = False

    def ajouter(self, morceau):
        """Reçoit un morceau de réponse, renvoie ce qui peut être affiché tout de suite."""
        self.tampon += morceau
        return self._trier(final=False)

    def terminer(self):
        """Fin de la réponse : renvoie le reste à afficher."""
        visible = self._trier(final=True)
        if self.balise_en_cours:
            self._clore_balise()
        return visible

    def contenus(self, nom):
        return [contenu for balise, contenu in self.balises if balise == nom]

    def _clore_balise(self):
        # plusieurs balises sur une même ligne : un contenu par balise
        nom, texte, position = self.balise_en_cours, self.contenu_en_cours, 0
        morceaux = []
        for trouve in MOTIF_BALISE.finditer(texte):
            morceaux.append((nom, texte[position:trouve.start()]))
            nom, position = trouve.group(1).lower(), trouve.end()
        morceaux.append((nom, texte[position:]))
        for nom, contenu in morceaux:
            contenu = nettoyer_contenu(contenu)
            if contenu and (nom, contenu) not in self.balises:
                self.balises.append((nom, contenu))
        self.balise_en_cours = None
        self.contenu_en_cours = ""
        self.apres_balise = True

    def _trier(self, final):
        visible = []
        while self.tampon:
            if self.balise_en_cours:
                fin_ligne = self.tampon.find("\n")
                if fin_ligne == -1:
                    self.contenu_en_cours += self.tampon
                    self.tampon = ""
                    break
                self.contenu_en_cours += self.tampon[:fin_ligne]
                self.tampon = self.tampon[fin_ligne + 1:]
                self._clore_balise()
                continue

            if self.apres_balise or not self.deja_affiche:
                # pas de blancs en tête de réponse, ni juste après une ligne cachée
                self.tampon = self.tampon.lstrip()
                if not self.tampon:
                    break

            trouve = MOTIF_BALISE.search(self.tampon)
            if trouve:
                avant = self.tampon[:trouve.start()].rstrip()
                if avant:
                    visible.append(self._preparer(avant))
                self.tampon = self.tampon[trouve.end():]
                self.balise_en_cours = trouve.group(1).lower()
                continue

            garde = 0 if final else longueur_debut_balise(self.tampon)
            a_montrer = self.tampon[:len(self.tampon) - garde]
            sans_blancs_finaux = a_montrer.rstrip()
            if sans_blancs_finaux:
                visible.append(self._preparer(sans_blancs_finaux))
            self.tampon = "" if final else self.tampon[len(sans_blancs_finaux):]
            break
        return "".join(visible)

    def _preparer(self, texte):
        """Ajoute un saut de ligne quand le texte reprend après une ligne cachée."""
        if self.apres_balise and self.deja_affiche:
            texte = "\n" + texte
        self.apres_balise = False
        self.deja_affiche = True
        return texte
