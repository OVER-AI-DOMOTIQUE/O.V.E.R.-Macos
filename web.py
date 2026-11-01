# -*- coding: utf-8 -*-
"""
Module web d'O.V.E.R. — recherche sur Internet et lecture de pages.

Seule la bibliothèque standard de Python est utilisée : rien à installer.
Compatible avec le Python 3.9 fourni par Apple.

Ce qui sort de la machine : les mots-clés de recherche (vers le moteur de
recherche) et l'adresse des pages lues. Le modèle, la conversation et le
cerveau restent en local.

Moteurs, essayés dans l'ordre jusqu'à obtenir des résultats :
  1. SearXNG, si une adresse est réglée (ex. une instance sur ton VPS) ;
  2. DuckDuckGo (version HTML, sans clé) ;
  3. Mojeek (moteur indépendant, sans clé) ;
  4. Wikipédia en français (en dernier recours).
"""

import ipaddress
import json
import re
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import List, Optional

AGENT_NAVIGATEUR = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/16.6 Safari/605.1.15"
)
DELAI_RECHERCHE = 10        # secondes avant d'abandonner un moteur
DELAI_PAGE = 8              # secondes avant d'abandonner une page
TAILLE_MAX_TELECHARGEMENT = 2_000_000  # octets lus au plus par page
TAILLE_MAX_EXTRAIT = 300    # caractères gardés par extrait de résultat

# Les balises que le modèle utilise : on les retire du texte venu du web, pour qu'une page
# ne puisse ni déclencher une recherche, ni proposer un souvenir, ni souffler une action.
MOTIF_BALISES_INTERNES = re.compile(r"\[\s*(cerveau|recherche|action)\s*\]", re.IGNORECASE)

# Machines joignables même si leur adresse est locale : l'instance SearXNG réglée dans
# reglages.py (souvent sur le réseau de la maison). Tout le reste du réseau local est refusé.
HOTES_PERMIS = set()

MOTIF_ADRESSE = re.compile(
    r"^(https?://)?([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}(:\d{1,5})?(/\S*)?$",
    re.IGNORECASE,
)


class ErreurWeb(Exception):
    """Erreur réseau, déjà formulée pour être affichée."""


@dataclass
class Resultat:
    titre: str
    url: str
    extrait: str = ""


@dataclass
class Page:
    url: str
    titre: str
    texte: str


@dataclass
class Recherche:
    requete: str
    moteur: str = ""
    resultats: List[Resultat] = field(default_factory=list)
    page: Optional[Page] = None
    erreurs: List[str] = field(default_factory=list)
    duree: float = 0.0  # secondes passées à chercher

    def a_trouve(self):
        return bool(self.resultats or self.page)

    def sources(self):
        adresses = []
        if self.page:
            adresses.append(self.page.url)
        for resultat in self.resultats:
            if resultat.url not in adresses:
                adresses.append(resultat.url)
        return adresses

    def pour_modele(self, max_caracteres=3000):
        """Les résultats mis en forme pour le modèle, sans dépasser max_caracteres."""
        lignes = [
            "(Message automatique du programme O.V.E.R., pas de l'utilisateur.)",
            f"Recherche web pour « {self.requete} »" + (f" (moteur : {self.moteur})" if self.moteur else "") + ".",
        ]
        if not self.a_trouve():
            detail = " ; ".join(self.erreurs) or "aucun résultat"
            lignes.append(f"La recherche n'a rien donné ({detail}).")
            lignes.append(
                "Réponds avec ce que tu sais, en précisant clairement que tu n'as pas pu vérifier sur le web. "
                "N'écris pas de ligne [RECHERCHE]."
            )
            return "\n".join(lignes)

        if self.resultats:
            lignes.append("")
            lignes.append("Résultats :")
            for numero, resultat in enumerate(self.resultats, 1):
                lignes.append(f"{numero}. {resultat.titre} — {resultat.url}")
                if resultat.extrait:
                    lignes.append(f"   {resultat.extrait}")
        consigne_finale = (
            "\n\nRéponds maintenant à la question de l'utilisateur en t'appuyant uniquement sur ces informations : "
            "n'ajoute aucun détail (nom, chiffre, technique, compatibilité) qui n'y figure pas. "
            "Indique l'adresse de la source utilisée. Si elles ne répondent pas à la question, dis-le franchement. "
            "N'écris pas de ligne [RECHERCHE]."
        )
        texte = "\n".join(lignes)
        if self.page:
            entete = f"\n\nContenu de la page {self.page.url}" + (f" ({self.page.titre})" if self.page.titre else "") + " :\n"
            place = max_caracteres - len(texte) - len(entete) - len(consigne_finale)
            if place > 200:
                texte += entete + couper(self.page.texte, place)
        return couper(texte, max_caracteres - len(consigne_finale)) + consigne_finale


# --- Outils -----------------------------------------------------------------------

def couper(texte, maximum):
    """Coupe proprement à la fin d'un mot."""
    if len(texte) <= maximum:
        return texte
    coupe = texte[:maximum].rsplit(" ", 1)[0]
    return coupe.rstrip(" ,;:") + "…"


def nettoyer_texte(texte):
    return " ".join(MOTIF_BALISES_INTERNES.sub("", texte).split())


def est_adresse(texte):
    """« iaover.fr », « https://iaover.fr/Contact » → vrai ; « météo Toulouse » → faux."""
    return bool(MOTIF_ADRESSE.match(texte.strip()))


def vers_url(texte):
    texte = texte.strip()
    return texte if re.match(r"^https?://", texte, re.IGNORECASE) else "https://" + texte


def contexte_ssl():
    """Certificats de certifi s'il est installé, sinon ceux du système (ceux du Mac pour le Python d'Apple)."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


CONTEXTE_SSL = contexte_ssl()


def hote(adresse):
    """« https://www.iaover.fr/contact » → « www.iaover.fr » (en minuscules), "" si illisible."""
    try:
        return (urllib.parse.urlsplit(vers_url(adresse)).hostname or "").lower()
    except ValueError:
        return ""


def adresse_citee(adresse, message):
    """Le site de cette adresse figure-t-il dans le message de l'utilisateur ?
    « https://www.iaover.fr/a?b=c » est cité par « va voir iaover.fr »."""
    nom = hote(adresse)
    nom = nom[4:] if nom.startswith("www.") else nom
    return bool(nom) and nom in (message or "").lower()


def ip_interdite(ip):
    """Adresse du Mac lui-même ou du réseau local (box, imprimante, NAS…) : jamais lue par O.V.E.R."""
    ip = ipaddress.ip_address(ip)
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved
            or ip.is_unspecified)


def verifier_adresse(url):
    """Refuse ce qui n'est pas http(s) et les machines du réseau local (même cachées derrière un nom).
    Lève ErreurWeb."""
    morceaux = urllib.parse.urlsplit(url)
    if morceaux.scheme.lower() not in ("http", "https"):
        raise ErreurWeb(f"type d'adresse refusé ({morceaux.scheme or 'aucun'})")
    nom = (morceaux.hostname or "").lower()
    if not nom:
        raise ErreurWeb("adresse incomplète")
    if nom in HOTES_PERMIS:
        return
    try:
        port = morceaux.port or (443 if morceaux.scheme.lower() == "https" else 80)
        adresses = {info[4][0] for info in socket.getaddrinfo(nom, port, proto=socket.IPPROTO_TCP)}
    except (socket.gaierror, UnicodeError, ValueError) as erreur:
        raise ErreurWeb("adresse introuvable ou pas de connexion Internet") from erreur
    if any(ip_interdite(ip.split("%")[0]) for ip in adresses):
        raise ErreurWeb(f"« {nom} » est une adresse du réseau local : je ne la lis pas")


class RedirectionsVerifiees(urllib.request.HTTPRedirectHandler):
    """Chaque redirection est vérifiée comme l'adresse de départ (pas de détour vers le réseau local)."""

    def redirect_request(self, requete, fichier, code, message, entetes, nouvelle_adresse):
        verifier_adresse(urllib.parse.urljoin(requete.full_url, nouvelle_adresse))
        return super().redirect_request(requete, fichier, code, message, entetes, nouvelle_adresse)


OUVREUR = urllib.request.build_opener(RedirectionsVerifiees, urllib.request.HTTPSHandler(context=CONTEXTE_SSL))


def telecharger(url, delai, donnees=None):
    """Télécharge une page. Renvoie (texte, adresse finale, type de contenu). Lève ErreurWeb."""
    entetes = {
        "User-Agent": AGENT_NAVIGATEUR,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.5",
    }
    if donnees is not None:
        entetes["Content-Type"] = "application/x-www-form-urlencoded"
        donnees = urllib.parse.urlencode(donnees).encode("utf-8")
    requete = urllib.request.Request(url, data=donnees, headers=entetes)
    try:
        verifier_adresse(url)
        with OUVREUR.open(requete, timeout=delai) as reponse:
            type_contenu = reponse.headers.get_content_type()
            encodage = reponse.headers.get_content_charset()
            brut = reponse.read(TAILLE_MAX_TELECHARGEMENT)
            adresse_finale = reponse.geturl()
            code = reponse.status
    except ErreurWeb:
        raise
    except urllib.error.HTTPError as erreur:
        raise ErreurWeb(f"le site a répondu « erreur {erreur.code} »") from erreur
    except urllib.error.URLError as erreur:
        raison = erreur.reason
        if isinstance(raison, ssl.SSLCertVerificationError):
            raise ErreurWeb("certificat HTTPS non vérifiable (relance « bash installer.sh » pour installer certifi)") from erreur
        if isinstance(raison, (socket.timeout, TimeoutError)):
            raise ErreurWeb("le site met trop de temps à répondre") from erreur
        if isinstance(raison, socket.gaierror):
            raise ErreurWeb("adresse introuvable ou pas de connexion Internet") from erreur
        raise ErreurWeb(f"connexion impossible ({raison})") from erreur
    except (socket.timeout, TimeoutError) as erreur:
        raise ErreurWeb("le site met trop de temps à répondre") from erreur
    except (OSError, ValueError) as erreur:
        raise ErreurWeb(f"connexion impossible ({erreur})") from erreur
    if code == 202:
        raise ErreurWeb("le moteur a refusé la requête (protection anti-robot)")

    if not encodage:
        trouve = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", brut[:4096], re.IGNORECASE)
        encodage = trouve.group(1).decode("ascii", "ignore") if trouve else "utf-8"
    try:
        texte = brut.decode(encodage, errors="replace")
    except LookupError:
        texte = brut.decode("utf-8", errors="replace")
    return texte, adresse_finale, type_contenu


def decoder_lien(lien):
    """Les liens DuckDuckGo passent parfois par une redirection //duckduckgo.com/l/?uddg=…"""
    if lien.startswith("//"):
        lien = "https:" + lien
    morceaux = urllib.parse.urlparse(lien)
    if morceaux.netloc.endswith("duckduckgo.com") and morceaux.path.startswith("/l/"):
        cible = urllib.parse.parse_qs(morceaux.query).get("uddg")
        if cible:
            return cible[0]
    return lien


# --- Moteurs de recherche -----------------------------------------------------------

def classes(attributs):
    return (dict(attributs).get("class") or "").split()


class LecteurDuckDuckGo(HTMLParser):
    """Résultats de html.duckduckgo.com : un bloc <div class="result__body"> par résultat,
    titre dans <h2>, extrait et lien dans <a class="result__snippet">."""

    def __init__(self):
        super().__init__()
        self.resultats = []
        self.profondeur_bloc = 0     # profondeur des <div> dans le bloc en cours (0 = hors bloc)
        self.courant = None
        self.dans_titre = False
        self.dans_extrait = False

    def handle_starttag(self, balise, attributs):
        noms = classes(attributs)
        if balise == "div":
            if self.profondeur_bloc:
                self.profondeur_bloc += 1
            elif "result__body" in noms or ("links_main" in noms and "result--ad" not in noms):
                self.profondeur_bloc = 1
                self.courant = {"titre": "", "lien_titre": "", "lien_extrait": "", "extrait": ""}
        if not self.profondeur_bloc:
            return
        if balise == "h2":
            self.dans_titre = True
        elif balise == "a":
            lien = dict(attributs).get("href") or ""
            if self.dans_titre and not self.courant["lien_titre"]:
                self.courant["lien_titre"] = lien
            if "result__snippet" in noms:
                self.dans_extrait = True
                self.courant["lien_extrait"] = lien
        elif "result__snippet" in noms:
            self.dans_extrait = True

    def handle_endtag(self, balise):
        if not self.profondeur_bloc:
            return
        if balise == "h2":
            self.dans_titre = False
        elif balise in ("a", "td", "div") and self.dans_extrait:
            self.dans_extrait = False
        if balise == "div":
            self.profondeur_bloc -= 1
            if self.profondeur_bloc == 0:
                self._clore()

    def handle_data(self, texte):
        if not self.profondeur_bloc:
            return
        if self.dans_titre:
            self.courant["titre"] += texte
        elif self.dans_extrait:
            self.courant["extrait"] += texte

    def _clore(self):
        bloc, self.courant = self.courant, None
        lien = decoder_lien(bloc["lien_extrait"] or bloc["lien_titre"])
        titre = nettoyer_texte(bloc["titre"])
        if not titre or not lien.startswith("http") or "duckduckgo.com/y.js" in lien:
            return  # bloc vide ou publicité
        self.resultats.append(Resultat(titre, lien, couper(nettoyer_texte(bloc["extrait"]), TAILLE_MAX_EXTRAIT)))


def chercher_duckduckgo(requete, nombre):
    texte, _, _ = telecharger(
        "https://html.duckduckgo.com/html/", DELAI_RECHERCHE,
        donnees={"q": requete, "b": "", "kl": "fr-fr"},
    )
    if "anomaly-modal" in texte or "bots use DuckDuckGo" in texte:
        raise ErreurWeb("DuckDuckGo a demandé une vérification anti-robot")
    lecteur = LecteurDuckDuckGo()
    lecteur.feed(texte)
    return lecteur.resultats[:nombre]


class LecteurMojeek(HTMLParser):
    """Résultats de mojeek.com : <ul class="results"><li> avec titre dans <h2><a>, extrait dans <p class="s">."""

    def __init__(self):
        super().__init__()
        self.resultats = []
        self.dans_liste = 0
        self.courant = None
        self.dans_titre = False
        self.dans_extrait = False

    def handle_starttag(self, balise, attributs):
        noms = classes(attributs)
        if balise == "ul":
            if self.dans_liste:
                self.dans_liste += 1
            elif any("results" in nom for nom in noms):  # « results », « results-standard »…
                self.dans_liste = 1
        if not self.dans_liste:
            return
        if balise == "li" and self.dans_liste == 1:
            self._clore()
            self.courant = {"titre": "", "lien": "", "extrait": ""}
        elif self.courant is None:
            return
        elif balise == "h2":
            self.dans_titre = True
        elif balise == "a" and self.dans_titre and not self.courant["lien"]:
            self.courant["lien"] = dict(attributs).get("href") or ""
        elif balise == "p" and "s" in noms:
            self.dans_extrait = True

    def handle_endtag(self, balise):
        if not self.dans_liste:
            return
        if balise == "h2":
            self.dans_titre = False
        elif balise == "p":
            self.dans_extrait = False
        elif balise == "ul":
            self.dans_liste -= 1
            if self.dans_liste == 0:
                self._clore()

    def handle_data(self, texte):
        if self.courant is None:
            return
        if self.dans_titre:
            self.courant["titre"] += texte
        elif self.dans_extrait:
            self.courant["extrait"] += texte

    def _clore(self):
        if self.courant and self.courant["lien"].startswith("http") and nettoyer_texte(self.courant["titre"]):
            self.resultats.append(Resultat(
                nettoyer_texte(self.courant["titre"]),
                self.courant["lien"],
                couper(nettoyer_texte(self.courant["extrait"]), TAILLE_MAX_EXTRAIT),
            ))
        self.courant = None


def chercher_mojeek(requete, nombre):
    adresse = "https://www.mojeek.com/search?" + urllib.parse.urlencode({"q": requete, "lb": "fr"})
    texte, _, _ = telecharger(adresse, DELAI_RECHERCHE)
    lecteur = LecteurMojeek()
    lecteur.feed(texte)
    return lecteur.resultats[:nombre]


def chercher_searxng(adresse_instance, requete, nombre):
    """Instance SearXNG avec le format JSON activé (search.formats: [html, json])."""
    adresse = adresse_instance.rstrip("/") + "/search?" + urllib.parse.urlencode(
        {"q": requete, "format": "json", "language": "fr"})
    texte, _, _ = telecharger(adresse, DELAI_RECHERCHE)
    try:
        donnees = json.loads(texte)
    except ValueError as erreur:
        raise ErreurWeb("l'instance SearXNG ne renvoie pas de JSON (format json désactivé ?)") from erreur
    resultats = []
    for element in donnees.get("results", [])[:nombre]:
        if element.get("url", "").startswith("http"):
            resultats.append(Resultat(
                nettoyer_texte(element.get("title", "")) or element["url"],
                element["url"],
                couper(nettoyer_texte(element.get("content", "")), TAILLE_MAX_EXTRAIT),
            ))
    return resultats


def chercher_wikipedia(requete):
    """Le meilleur article de Wikipédia en français, avec son introduction en texte brut."""
    adresse = "https://fr.wikipedia.org/w/api.php?" + urllib.parse.urlencode({
        "action": "query", "format": "json", "generator": "search", "gsrsearch": requete,
        "gsrlimit": 1, "prop": "extracts", "exintro": 1, "explaintext": 1, "redirects": 1,
    })
    texte, _, _ = telecharger(adresse, DELAI_RECHERCHE)
    try:
        pages = json.loads(texte).get("query", {}).get("pages", {})
    except ValueError as erreur:
        raise ErreurWeb("réponse de Wikipédia illisible") from erreur
    for page in pages.values():
        titre = page.get("title", "")
        extrait = nettoyer_texte(page.get("extract", ""))
        if titre and extrait:
            url = "https://fr.wikipedia.org/wiki/" + urllib.parse.quote(titre.replace(" ", "_"))
            return Page(url, titre, extrait)
    return None


# --- Lecture d'une page ---------------------------------------------------------------

BALISES_IGNOREES = {"script", "style", "noscript", "svg", "template", "iframe", "canvas", "select", "button"}
BALISES_DECOR = {"nav", "footer", "aside", "form"}
BALISES_BLOC = {
    "p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table",
    "section", "article", "main", "header", "blockquote", "pre", "dd", "dt", "figcaption", "hr",
}


class LecteurTexte(HTMLParser):
    """Garde le texte lisible d'une page : titre, description, paragraphes."""

    def __init__(self, ignorer_decor=True):
        super().__init__()
        self.ignorees = BALISES_IGNOREES | (BALISES_DECOR if ignorer_decor else set())
        self.profondeur_ignoree = 0
        self.dans_titre = False
        self.titre = ""
        self.description = ""
        self.morceaux = []

    def handle_starttag(self, balise, attributs):
        if balise in self.ignorees:
            self.profondeur_ignoree += 1
            return
        if balise == "title":
            self.dans_titre = True
        elif balise == "meta":
            valeurs = dict(attributs)
            nom = (valeurs.get("name") or valeurs.get("property") or "").lower()
            if nom in ("description", "og:description") and not self.description:
                self.description = valeurs.get("content") or ""
        if balise in BALISES_BLOC:
            self.morceaux.append("\n")

    def handle_endtag(self, balise):
        if balise in self.ignorees:
            self.profondeur_ignoree = max(0, self.profondeur_ignoree - 1)
            return
        if balise == "title":
            self.dans_titre = False
        if balise in BALISES_BLOC:
            self.morceaux.append("\n")

    def handle_data(self, texte):
        if self.dans_titre:
            self.titre += texte
        elif not self.profondeur_ignoree:
            self.morceaux.append(texte)

    def resultat(self):
        lignes = []
        for ligne in "".join(self.morceaux).split("\n"):
            ligne = nettoyer_texte(ligne)
            if ligne and (not lignes or lignes[-1] != ligne):
                lignes.append(ligne)
        description = nettoyer_texte(self.description)
        if description and description not in lignes[:5]:
            lignes.insert(0, description)
        return nettoyer_texte(self.titre), "\n".join(lignes)


def extraire_texte(source_html):
    lecteur = LecteurTexte(ignorer_decor=True)
    lecteur.feed(source_html)
    titre, texte = lecteur.resultat()
    if len(texte) < 200:
        # page mal fermée ou tout dans <nav>/<form> : on recommence sans rien ignorer d'autre que les scripts
        lecteur = LecteurTexte(ignorer_decor=False)
        lecteur.feed(source_html)
        titre, texte = lecteur.resultat()
    return titre, texte


def lire_page(url, max_caracteres=1500):
    """Télécharge une page et renvoie son texte lisible. Lève ErreurWeb."""
    if "wikipedia.org/wiki/" in url:
        titre_article = urllib.parse.unquote(url.split("/wiki/", 1)[1]).replace("_", " ")
        page = chercher_wikipedia(titre_article)
        if page:
            page.texte = couper(page.texte, max_caracteres)
            return page
    texte, adresse_finale, type_contenu = telecharger(url, DELAI_PAGE)
    if type_contenu == "text/plain":
        return Page(adresse_finale, "", couper(nettoyer_texte(texte), max_caracteres))
    if type_contenu not in ("text/html", "application/xhtml+xml"):
        raise ErreurWeb(f"contenu non lisible ({type_contenu})")
    titre, contenu = extraire_texte(texte)
    if not contenu:
        raise ErreurWeb("page vide (site qui a besoin de JavaScript ?)")
    return Page(adresse_finale, titre, couper(contenu, max_caracteres))


# --- Recherche complète -----------------------------------------------------------------

def rechercher(requete, nombre=4, lire_premiere_page=True, max_page=1500, adresse_searxng="", lecture_directe=True):
    """Cherche sur le web. Une adresse (« iaover.fr ») est lue directement si lecture_directe est vrai
    (adresse tapée par l'utilisateur) ; sinon elle passe par les moteurs comme des mots-clés.
    Ne lève pas d'erreur : les problèmes sont rangés dans Recherche.erreurs."""
    requete = " ".join(requete.split())[:200]
    recherche = Recherche(requete)
    if adresse_searxng and hote(adresse_searxng):
        HOTES_PERMIS.add(hote(adresse_searxng))

    if lecture_directe and est_adresse(requete):
        try:
            recherche.page = lire_page(vers_url(requete), max_page)
            recherche.moteur = "lecture directe"
            return recherche
        except ErreurWeb as erreur:
            recherche.erreurs.append(f"lecture de {requete} : {erreur}")

    moteurs = []
    if adresse_searxng:
        moteurs.append(("SearXNG", lambda: chercher_searxng(adresse_searxng, requete, nombre)))
    moteurs.append(("DuckDuckGo", lambda: chercher_duckduckgo(requete, nombre)))
    moteurs.append(("Mojeek", lambda: chercher_mojeek(requete, nombre)))

    for nom, chercher in moteurs:
        try:
            resultats = chercher()
        except ErreurWeb as erreur:
            recherche.erreurs.append(f"{nom} : {erreur}")
            continue
        if resultats:
            recherche.resultats = resultats
            recherche.moteur = nom
            break
        recherche.erreurs.append(f"{nom} : aucun résultat")

    if recherche.resultats and lire_premiere_page:
        for resultat in recherche.resultats[:2]:  # si la première page ne se lit pas, on tente la deuxième
            try:
                recherche.page = lire_page(resultat.url, max_page)
                break
            except ErreurWeb:
                continue

    if not recherche.a_trouve():
        try:
            page = chercher_wikipedia(requete)
            if page:
                page.texte = couper(page.texte, max_page)
                recherche.page = page
                recherche.moteur = "Wikipédia"
        except ErreurWeb as erreur:
            recherche.erreurs.append(f"Wikipédia : {erreur}")
    return recherche


# --- Comprendre la demande de l'utilisateur ------------------------------------------------

MOTIF_DEMANDE_CHERCHE = re.compile(
    r"^(?:(?:est-ce que )?(?:tu peux|peux-tu|pourrais-tu|vous pouvez|pouvez-vous)\s+)?"
    r"(?:re)?cherche[rz]?(?:-moi|-nous| moi)?\b"
    r"(?:\s+sur\s+(?:le\s+web|internet|le\s+net|google|duckduckgo))?"
    r"\s*[:,]?\s*(?P<requete>.+)$",
    re.IGNORECASE,
)
MOTIF_DEMANDE_SITE = re.compile(
    r"^(?:va\s+voir\s+|lis\s+|consulte\s+|regarde\s+|r[ée]sume\s+)(?:le\s+site\s+)?(?P<requete>\S+?)[\s?!.]*$",
    re.IGNORECASE,
)
MOTIF_DEMANDE_WEB = re.compile(
    r"^(?:regarde|trouve)(?:-moi| moi)?\s+sur\s+(?:le\s+web|internet|le\s+net|google)\s*[:,]?\s*(?P<requete>.+)$",
    re.IGNORECASE,
)
MOTIF_PAS_WEB = re.compile(r"^(dans (ton|ta|tes|mon|ma|mes) |en local|dans le cerveau)", re.IGNORECASE)
MOTIF_REMPLISSAGE = re.compile(
    r"^(des infos(?:rmations)? sur|ce que dit|à propos de|a propos de|des renseignements sur)\s+", re.IGNORECASE)


def extraire_demande_recherche(message):
    """« cherche iaover.fr », « recherche sur le web le prix d'une Bambu A1 », « va voir iaover.fr »
    → la requête à lancer ; sinon None (c'est alors le modèle qui décide)."""
    texte = message.strip()
    for motif in (MOTIF_DEMANDE_WEB, MOTIF_DEMANDE_CHERCHE):
        trouve = motif.match(texte)
        if trouve:
            requete = trouve.group("requete").strip(" ?!.:,«»\"'")
            if MOTIF_PAS_WEB.match(requete):
                return None
            requete = MOTIF_REMPLISSAGE.sub("", requete)
            return requete if len(requete) >= 2 else None
    trouve = MOTIF_DEMANDE_SITE.match(texte)
    if trouve and est_adresse(trouve.group("requete")):
        return trouve.group("requete")
    return None


def consigne_recherche():
    """Explique au modèle comment demander une recherche."""
    return (
        "Recherche web : pour l'actualité, un prix, un horaire, la météo, un site, une personne ou ce dont "
        "tu n'es pas sûr, ne devine pas : réponds seulement par la ligne [RECHERCHE] mots-clés "
        "(ex. : [RECHERCHE] météo Paris demain) ; tu recevras les résultats. Sinon, réponds directement."
    )


if __name__ == "__main__":
    # Essai rapide sans le modèle :  python web.py iaover.fr
    import sys
    requete_essai = " ".join(sys.argv[1:]) or "iaover.fr"
    print(rechercher(requete_essai).pour_modele())
