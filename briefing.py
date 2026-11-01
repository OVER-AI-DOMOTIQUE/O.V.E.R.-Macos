# -*- coding: utf-8 -*-
"""
Briefing d'O.V.E.R. — le point du jour : date, météo, rendez-vous, rappels.

Quand ? C'est un paramètre (panneau Paramètres de l'application, ou /paramètres briefing …) :
  jamais       par défaut ;
  ouverture    à chaque ouverture d'O.V.E.R. ;
  quotidien    une fois par jour, avant de répondre au premier message.
Et à la demande : /briefing.

Le texte est assemblé par le programme, pas par le modèle : c'est instantané et il
n'invente rien. Le modèle le reçoit ensuite, pour pouvoir en parler.

Sources :
  météo     Open-Meteo (gratuit, sans compte) : seul le nom de la ville sort du Mac ;
  agenda    app Calendrier ;
  rappels   app Rappels.
Dans l'application, l'agenda et les rappels sont lus par l'application elle-même (EventKit, avec
l'autorisation demandée par macOS) ; dans le Terminal, par AppleScript.
La météo est cherchée pendant ce temps ; une source qui ne répond pas est simplement
signalée, elle ne bloque pas les autres.
"""

import datetime
import json
import re
import threading
import urllib.parse

import actions
import web

DELAI_METEO = 8
DELAI_APPLESCRIPT = 25

# Codes météo de l'Organisation météorologique mondiale (utilisés par Open-Meteo)
CODES_METEO = {
    0: "ciel dégagé", 1: "plutôt dégagé", 2: "partiellement nuageux", 3: "couvert",
    45: "brouillard", 48: "brouillard givrant",
    51: "bruine légère", 53: "bruine", 55: "bruine forte", 56: "bruine verglaçante", 57: "bruine verglaçante",
    61: "pluie faible", 63: "pluie", 65: "forte pluie", 66: "pluie verglaçante", 67: "pluie verglaçante",
    71: "neige faible", 73: "neige", 75: "forte neige", 77: "grains de neige",
    80: "averses", 81: "averses", 82: "fortes averses", 85: "averses de neige", 86: "averses de neige",
    95: "orage", 96: "orage avec grêle", 99: "orage avec grêle",
}


# --- Météo -------------------------------------------------------------------------------

def trouver_lieu(ville, parametres):
    """Ville → {nom, lat, lon} (gardé en mémoire dans les paramètres pour ne pas redemander)."""
    connu = parametres.etat.get("meteo_lieu")
    if isinstance(connu, dict) and connu.get("recherche") == ville:
        return connu
    adresse = ("https://geocoding-api.open-meteo.com/v1/search?"
               + urllib.parse.urlencode({"name": ville, "count": 1, "language": "fr", "format": "json"}))
    texte, _, _ = web.telecharger(adresse, DELAI_METEO)
    resultats = json.loads(texte).get("results") or []
    if not resultats:
        raise web.ErreurWeb(f"ville « {ville} » introuvable")
    lieu = {"recherche": ville, "nom": resultats[0].get("name", ville),
            "lat": resultats[0]["latitude"], "lon": resultats[0]["longitude"]}
    parametres.etat["meteo_lieu"] = lieu
    return lieu


def meteo(ville, parametres):
    lieu = trouver_lieu(ville, parametres)
    adresse = ("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
        "latitude": lieu["lat"], "longitude": lieu["lon"], "timezone": "auto", "forecast_days": 1,
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
    }))
    texte, _, _ = web.telecharger(adresse, DELAI_METEO)
    jour = json.loads(texte)["daily"]
    ciel = CODES_METEO.get(jour["weather_code"][0], "temps variable")
    mini, maxi = round(jour["temperature_2m_min"][0]), round(jour["temperature_2m_max"][0])
    pluie = jour.get("precipitation_probability_max", [None])[0]
    morceaux = [ciel, f"de {mini} à {maxi} °C"]
    if pluie is not None:
        morceaux.append(f"risque de pluie {round(pluie)} %")
    return f"Météo à {lieu['nom']} : " + ", ".join(morceaux) + "."


# --- Agenda et rappels (AppleScript) -----------------------------------------------------------

SCRIPT_AGENDA = [
    'set debut to current date',
    'set hours of debut to 0', 'set minutes of debut to 0', 'set seconds of debut to 0',
    'set fin to debut + (1 * days)',
    'set lignes to {}',
    'set dejaOuvert to application "Calendar" is running',
    'tell application "Calendar"',
    'repeat with c in calendars',
    'try',
    'repeat with e in (every event of c whose start date is greater than or equal to debut and start date is less than fin)',
    'set d to start date of e',
    'set end of lignes to (summary of e) & tab & ((hours of d) as text) & ":" & ((minutes of d) as text) '
    '& tab & ((allday event of e) as text)',
    'end repeat',
    'end try',
    'end repeat',
    'end tell',
    'if not dejaOuvert then tell application "Calendar" to quit',
    "set AppleScript's text item delimiters to linefeed",
    'return lignes as text',
]

SCRIPT_RAPPELS = [
    'set fin to current date',
    'set hours of fin to 23', 'set minutes of fin to 59', 'set seconds of fin to 59',
    'set lignes to {}',
    'set dejaOuvert to application "Reminders" is running',
    'tell application "Reminders"',
    'try',
    'repeat with r in (every reminder whose completed is false and due date is less than or equal to fin)',
    'set d to due date of r',
    'set end of lignes to (name of r) & tab & ((year of d) as text) & "-" & ((month of d as integer) as text) '
    '& "-" & ((day of d) as text) & " " & ((hours of d) as text) & ":" & ((minutes of d) as text)',
    'end repeat',
    'end try',
    'end tell',
    'if not dejaOuvert then tell application "Reminders" to quit',
    "set AppleScript's text item delimiters to linefeed",
    'return lignes as text',
]


def heure_courte(heures, minutes):
    return f"{int(heures)} h {int(minutes):02d}"


def texte_agenda(evenements):
    """[(cle de tri, texte)] → « Agenda : … »."""
    evenements = sorted(evenements)
    if not evenements:
        return "Agenda : rien de prévu aujourd'hui."
    return "Agenda : " + " · ".join(texte for _, texte in evenements) + "."


def texte_rappels(trouves, maintenant):
    """[(moment, nom)] → « Rappels : … » (en retard, ou l'heure s'il y en a une)."""
    lignes = []
    for moment, nom in sorted(trouves):
        if moment.date() < maintenant.date():
            detail = "en retard"
        elif moment.hour == 0 and moment.minute == 0:
            detail = ""
        else:
            detail = heure_courte(moment.hour, moment.minute)
        lignes.append((moment, nom + (f" ({detail})" if detail else "")))
    if not lignes:
        return "Rappels : aucun pour aujourd'hui."
    return "Rappels : " + " · ".join(texte for _, texte in lignes) + "."


def agenda_et_rappels_application(maintenant, avec_agenda, avec_rappels):
    """Dans l'application : l'agenda et les rappels du jour, lus par l'application (EventKit).
    Renvoie {"agenda": texte, "rappels": texte} pour les sources demandées."""
    ok, detail, donnees = actions.SYSTEME("agenda", agenda=avec_agenda, rappels=avec_rappels)
    resultats = {}
    if not ok:
        raison = (detail or "pas de réponse").strip().splitlines()[0][:120]
        for nom, voulu in (("agenda", avec_agenda), ("rappels", avec_rappels)):
            if voulu:
                resultats[nom] = f"{nom.capitalize()} : indisponible ({raison})."
        return resultats
    donnees = donnees or {}
    for nom, voulu in (("agenda", avec_agenda), ("rappels", avec_rappels)):
        probleme = str(donnees.get("erreur_" + nom) or "").strip()
        if voulu and probleme:   # accès refusé à l'une des deux : l'autre est quand même donnée
            resultats[nom] = f"{nom.capitalize()} : indisponible ({probleme[:160]})."
    if avec_agenda and "agenda" not in resultats:
        evenements = []
        for evenement in donnees.get("evenements") or []:
            titre = str(evenement.get("titre") or "").strip()
            if not titre:
                continue
            if evenement.get("journee"):
                evenements.append(((-1, 0), "toute la journée : " + titre))
            else:
                h, _, m = str(evenement.get("heure") or "0:0").partition(":")
                evenements.append(((int(h or 0), int(m or 0)), heure_courte(h or 0, m or 0) + " " + titre))
        resultats["agenda"] = texte_agenda(evenements)
    if avec_rappels and "rappels" not in resultats:
        trouves = []
        for rappel in donnees.get("rappels") or []:
            nom = str(rappel.get("titre") or "").strip()
            try:
                moment = datetime.datetime.fromisoformat(str(rappel.get("echeance") or ""))
            except ValueError:
                continue
            if nom:
                trouves.append((moment, nom))
        resultats["rappels"] = texte_rappels(trouves, maintenant)
    return resultats


def agenda():
    ok, sortie = actions.applescript(SCRIPT_AGENDA, delai=DELAI_APPLESCRIPT)
    if not ok:
        raise RuntimeError(sortie)
    evenements = []
    for ligne in sortie.splitlines():
        morceaux = ligne.split("\t")
        if len(morceaux) < 3 or not morceaux[0].strip():
            continue
        h, _, m = morceaux[1].partition(":")
        journee = morceaux[2].strip() == "true"
        cle = (-1, 0) if journee else (int(h or 0), int(m or 0))
        evenements.append((cle, ("toute la journée : " if journee else heure_courte(h, m) + " ") + morceaux[0].strip()))
    return texte_agenda(evenements)


def rappels(maintenant):
    ok, sortie = actions.applescript(SCRIPT_RAPPELS, delai=DELAI_APPLESCRIPT)
    if not ok:
        raise RuntimeError(sortie)
    trouves = []
    for ligne in sortie.splitlines():
        nom, _, quand = ligne.partition("\t")
        trouve = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2}) (\d{1,2}):(\d{1,2})", quand.strip())
        if not nom.strip() or not trouve:
            continue
        trouves.append((datetime.datetime(*map(int, trouve.groups())), nom.strip()))
    return texte_rappels(trouves, maintenant)


# --- Le briefing complet ---------------------------------------------------------------------

def composer(parametres, cerveau, maintenant=None):
    """Assemble le briefing. Renvoie la liste de ses lignes."""
    maintenant = maintenant or datetime.datetime.now()
    prenom = cerveau.prenom()
    lignes = [f"Bonjour{' ' + prenom if prenom else ''} ! Nous sommes {actions.date_fr(maintenant)}, "
              f"il est {actions.heure_fr(maintenant)}."]
    taches = []
    if parametres["briefing_meteo"]:
        ville = parametres["briefing_ville"] or cerveau.utilisateur.get("lieu", "")
        if ville:
            taches.append(("météo", lambda: meteo(ville, parametres)))
        else:
            taches.append(("météo", lambda: "Météo : indique ta ville dans les réglages (ou /réglages ville …)."))
    avec_agenda = bool(parametres["briefing_agenda"]) and (actions.SUR_MAC or actions.SYSTEME is not None)
    avec_rappels = bool(parametres["briefing_rappels"]) and (actions.SUR_MAC or actions.SYSTEME is not None)
    if avec_agenda and not actions.SYSTEME:
        taches.append(("agenda", agenda))
    if avec_rappels and not actions.SYSTEME:
        taches.append(("rappels", lambda: rappels(maintenant)))

    resultats = {}

    def lancer(nom, fonction):
        try:
            resultats[nom] = fonction()
        except Exception as erreur:   # une source en panne ne doit pas bloquer le briefing
            raison = str(erreur).strip().splitlines()[0][:120] if str(erreur).strip() else "pas de réponse"
            resultats[nom] = f"{nom.capitalize()} : indisponible ({raison})."

    fils = [threading.Thread(target=lancer, args=tache, daemon=True) for tache in taches]
    for fil in fils:
        fil.start()
    if actions.SYSTEME and (avec_agenda or avec_rappels):
        # l'application répond à ce fil-ci (celui qui lit ses messages) : demandé ici, pendant la météo
        try:
            resultats.update(agenda_et_rappels_application(maintenant, avec_agenda, avec_rappels))
        except Exception as erreur:
            raison = str(erreur).strip().splitlines()[0][:120] if str(erreur).strip() else "pas de réponse"
            for nom, voulu in (("agenda", avec_agenda), ("rappels", avec_rappels)):
                if voulu:
                    resultats[nom] = f"{nom.capitalize()} : indisponible ({raison})."
    for fil in fils:
        fil.join(DELAI_APPLESCRIPT + 5)
    ordre = [nom for nom, _ in taches if nom == "météo"] + [nom for nom, voulu in (("agenda", avec_agenda),
                                                                                  ("rappels", avec_rappels)) if voulu]
    for nom in ordre:
        lignes.append(resultats.get(nom, f"{nom.capitalize()} : trop long, abandonné."))
    try:
        parametres.sauvegarder()   # garde le lieu de la météo pour la prochaine fois
    except OSError:
        pass
    return lignes
