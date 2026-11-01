// Modeles.swift — les données affichées par l'application.

import SwiftUI

/// Où en est le moteur.
enum EtatMoteur: Equatable {
    case sansDossier          // le dossier O.V.E.R. n'est pas encore choisi
    case installationModele   // premier lancement : le modèle de langage n'est pas encore sur ce Mac
    case chargement           // le modèle se charge
    case pret                 // attend un message
    case occupe               // lit, écrit ou cherche sur le web
    case question             // attend une réponse à une question
    case arrete(String)       // le moteur s'est arrêté (avec la raison)
}

/// Premier lancement : le modèle de langage (2,1 Go) se télécharge, ou se reprend d'un fichier que tu as déjà.
struct InstallationModele: Equatable {
    var taille: Double = 0          // octets
    var recu: Double = 0            // déjà téléchargé (un téléchargement interrompu reprend là)
    var libre: Double = 0           // place libre sur le disque
    var manque: Double = 0          // place qui manque (0 : ça tient)
    var erreur = ""
    var enCours = false
    var phase = ""                  // telechargement | verification | copie
    var fait: Double = 0
    var total: Double = 0
    var vitesse: Double = 0         // octets par seconde (téléchargement)

    /// Évènement « modele_absent » : l'état de départ, ou après une erreur ou une pause.
    mutating func lireEtat(_ e: [String: Any]) {
        taille = nombreJSON(e["taille"])
        recu = nombreJSON(e["recu"])
        libre = nombreJSON(e["libre"])
        manque = nombreJSON(e["manque"])
        erreur = e["erreur"] as? String ?? ""
        enCours = false
        phase = ""
        vitesse = 0
    }
}

/// Un nombre lu dans un message JSON (entier ou décimal), 0 sinon.
func nombreJSON(_ valeur: Any?) -> Double {
    (valeur as? NSNumber)?.doubleValue ?? 0
}

/// 2147023008 → « 2,1 Go » ; 63511038 → « 64 Mo ».
func tailleLisible(_ octets: Double) -> String {
    octets >= 1e9 ? "\(nombreFr(octets / 1e9)) Go" : "\(max(1, Int((octets / 1e6).rounded()))) Mo"
}

/// Un bouton de réponse proposé par O.V.E.R. (valeur nil = « arrêter les questions »).
struct Choix: Identifiable, Hashable {
    let id = UUID()
    let libelle: String
    let valeur: String?
}

/// Une ligne de la discussion.
struct Message: Identifiable {
    enum Auteur { case utilisateur, over, systeme }
    enum Genre { case texte, info, erreur, recherche, question }

    let id = UUID()
    var auteur: Auteur
    var genre: Genre = .texte
    var texte: String
    var extrait = ""              // information mise en avant (« Je range ça dans mon cerveau ? »)
    var aide = ""
    var valeurActuelle = ""
    var choix: [Choix] = []
    var sources: [String] = []
    var statistiques = ""
    var enCours = false           // réponse en train de s'écrire
    var repondue = false          // question déjà traitée
}

/// Couleurs du logo O.V.E.R.
enum Palette {
    static let sarcelle = Color(red: 0x15 / 255, green: 0x60 / 255, blue: 0x6e / 255)
    static let or = Color(red: 0xe8 / 255, green: 0xb8 / 255, blue: 0x4b / 255)
    static let rouge = Color(red: 0xf3 / 255, green: 0x2a / 255, blue: 0x56 / 255)
}

/// 1.234 → « 1,2 » (ou « 1,23 » avec decimales: 2)
func nombreFr(_ valeur: Double, decimales: Int = 1) -> String {
    String(format: "%.\(decimales)f", valeur).replacingOccurrences(of: ".", with: ",")
}

/// La ligne discrète sous une réponse : temps de recherche, de lecture, vitesse d'écriture.
func texteStatistiques(_ stats: [String: Any]) -> String {
    let preparation = stats["duree_preparation"] as? Double ?? 0
    let recherche = stats["duree_recherche"] as? Double ?? 0
    let lecture = stats["duree_lecture"] as? Double ?? 0
    let ecriture = stats["duree_ecriture"] as? Double ?? 0
    let tokens = stats["nb_tokens"] as? Int ?? 0
    let vitesse = ecriture > 0 ? Double(tokens) / ecriture : 0
    var morceaux: [String] = []
    // temps passé à attendre la fin du préchauffage (lecture des consignes au démarrage)
    if preparation >= 0.5 { morceaux.append("préparation \(nombreFr(preparation)) s") }
    if recherche > 0 { morceaux.append("recherche \(nombreFr(recherche)) s") }
    morceaux.append("lecture \(nombreFr(lecture)) s")
    morceaux.append("\(tokens) tokens à \(nombreFr(vitesse)) tokens/s")
    var ligne = morceaux.joined(separator: "  ·  ")
    if stats["interrompu"] as? Bool == true {
        ligne += "  ·  réponse arrêtée"
    } else if stats["coupee"] as? Bool == true {
        ligne += "  ·  réponse coupée (longueur maximale)"
    }
    return ligne
}

/// Un choix proposé dans un menu des réglages (valeur envoyée au moteur, libellé affiché).
struct ChoixReglage: Identifiable, Hashable {
    var id: String { valeur }
    let valeur: String
    let libelle: String
}

/// Une voix française de macOS (celles de Réglages Système ▸ Accessibilité ▸ Contenu énoncé).
struct VoixLocale: Identifiable, Hashable {
    let id: String          // le nom exact donné à say (« Thomas », « Amélie (Enhanced) »)
    let nom: String         // « Thomas »
    let libelle: String     // « Amélie (Canada, améliorée) »
    let langue: String      // fr_FR, fr_CA…
    let licence: String

    init?(_ e: [String: Any]) {
        guard let id = e["id"] as? String, !id.isEmpty else { return nil }
        self.id = id
        nom = e["nom"] as? String ?? id
        libelle = e["libelle"] as? String ?? nom
        langue = e["langue"] as? String ?? ""
        licence = e["licence"] as? String ?? ""
    }
}

/// Un modèle d'écoute whisper.cpp (précis = small, rapide = base).
struct ModeleEcoute: Identifiable, Hashable {
    let id: String          // precis | rapide
    let nom: String
    let tailleMo: Int
    let installe: Bool
}

/// Ce que le micro a entendu, transcrit sur le Mac par whisper.cpp (évènement « transcription »).
struct Transcription {
    let texte: String
    let erreur: String          // vide si tout va bien ; « a_installer » si l'écoute n'est pas installée
    let explication: String
    let duree: Double           // secondes de calcul
    let dureeSon: Double
    let apercu: Bool            // texte provisoire, pendant qu'on parle encore
    let ident: Int              // numéro donné par l'application à la demande
    let fichier: String         // l'enregistrement transcrit

    init(_ e: [String: Any]) {
        texte = e["texte"] as? String ?? ""
        erreur = e["erreur"] as? String ?? ""
        explication = e["explication"] as? String ?? ""
        duree = (e["duree"] as? NSNumber)?.doubleValue ?? 0
        dureeSon = (e["duree_son"] as? NSNumber)?.doubleValue ?? 0
        apercu = e["apercu"] as? Bool ?? false
        ident = (e["id"] as? NSNumber)?.intValue ?? 0
        fichier = e["fichier"] as? String ?? ""
    }
}

/// Les paramètres (parametres.json, tenu par le moteur Python) : choix de l'utilisateur et du système.
struct ParametresOVER {
    var briefing = "jamais"
    var briefingMeteo = true
    var briefingRappels = true
    var briefingAgenda = true
    var briefingVille = ""
    var voixActive = false
    var voixNom = ""
    var voixVitesse = 185.0
    var voixDepart = "immediat"
    var ecouteEnvoiAuto = true
    var ecouteModele = "precis"
    var raccourciParler = "ctrl_option_espace"   // touches à maintenir pour parler (voir Parole.swift)
    var arrierePlan = false       // l'application reste active quand on ferme la fenêtre
    var masquerDock = false       // … sans icône dans le Dock tant que la fenêtre est fermée
    var demarrageSession = false
    var demarrageDiscret = true
    var choixBriefing = [ChoixReglage(valeur: "jamais", libelle: "jamais"),
                         ChoixReglage(valeur: "ouverture", libelle: "à chaque ouverture"),
                         ChoixReglage(valeur: "quotidien", libelle: "une fois par jour, au premier message")]
    var voixDisponibles: [VoixLocale] = []
    var etatVoix = ""             // prete | indisponible
    var messageVoix = ""
    var erreurVoix = ""
    var voixChoisie = ""          // voix réellement utilisée (identifiant)
    var voixPrincipale = "Thomas"
    var vitesseMin = 120.0
    var vitesseMax = 320.0
    // système
    var rechercheWeb = true
    var actionsMac = true
    var apprentissageAuto = true
    var garderConversation = true
    var memoireSurDisque = true
    var temperature = 0.15
    var longueurReponses = 768.0
    var longueurMin = 128.0
    var longueurMax = 1536.0
    var etatEcoute = ""           // prete | installation | erreur | a_installer
    var messageEcoute = ""
    var erreurEcoute = ""
    var modelesEcoute: [ModeleEcoute] = []
    var etatDemarrage = ""        // version sans bac à sable : actif | a_mettre_a_jour | inactif (vide : l'application gère)

    /// Applique un réglage (reçu du moteur, ou changé dans la fenêtre avant sa confirmation).
    mutating func appliquer(_ cle: String, _ valeur: Any) {
        switch cle {
        case "briefing": briefing = valeur as? String ?? briefing
        case "briefing_meteo": briefingMeteo = valeur as? Bool ?? briefingMeteo
        case "briefing_rappels": briefingRappels = valeur as? Bool ?? briefingRappels
        case "briefing_agenda": briefingAgenda = valeur as? Bool ?? briefingAgenda
        case "briefing_ville": briefingVille = valeur as? String ?? briefingVille
        case "voix_active": voixActive = valeur as? Bool ?? voixActive
        case "voix_nom": voixNom = valeur as? String ?? voixNom
        case "voix_vitesse": voixVitesse = (valeur as? NSNumber)?.doubleValue ?? voixVitesse
        case "voix_depart": voixDepart = valeur as? String ?? voixDepart
        case "recherche_web": rechercheWeb = valeur as? Bool ?? rechercheWeb
        case "actions_mac": actionsMac = valeur as? Bool ?? actionsMac
        case "apprentissage_auto": apprentissageAuto = valeur as? Bool ?? apprentissageAuto
        case "garder_conversation": garderConversation = valeur as? Bool ?? garderConversation
        case "memoire_sur_disque": memoireSurDisque = valeur as? Bool ?? memoireSurDisque
        case "temperature": temperature = (valeur as? NSNumber)?.doubleValue ?? temperature
        case "longueur_reponses": longueurReponses = (valeur as? NSNumber)?.doubleValue ?? longueurReponses
        case "ecoute_envoi_auto": ecouteEnvoiAuto = valeur as? Bool ?? ecouteEnvoiAuto
        case "ecoute_modele": ecouteModele = valeur as? String ?? ecouteModele
        case "raccourci_parler": raccourciParler = valeur as? String ?? raccourciParler
        case "arriere_plan": arrierePlan = valeur as? Bool ?? arrierePlan
        case "masquer_dock": masquerDock = valeur as? Bool ?? masquerDock
        case "demarrage_session": demarrageSession = valeur as? Bool ?? demarrageSession
        case "demarrage_discret": demarrageDiscret = valeur as? Bool ?? demarrageDiscret
        default: break
        }
    }

    /// Évènement « parametres » du moteur.
    mutating func lire(_ e: [String: Any]) {
        for (cle, valeur) in e["valeurs"] as? [String: Any] ?? [:] {
            appliquer(cle, valeur)
        }
        if let choix = e["choix_briefing"] as? [[String]] {
            choixBriefing = choix.filter { $0.count == 2 }.map { ChoixReglage(valeur: $0[0], libelle: $0[1]) }
        }
        if let fiches = e["voix_disponibles"] as? [[String: Any]] {
            voixDisponibles = fiches.compactMap(VoixLocale.init)
        }
        if let voix = e["etat_voix"] as? [String: Any] {
            etatVoix = voix["etat"] as? String ?? ""
            messageVoix = voix["message"] as? String ?? ""
            erreurVoix = voix["erreur"] as? String ?? ""
            voixChoisie = voix["choisie"] as? String ?? ""
            voixPrincipale = voix["principale"] as? String ?? voixPrincipale
        }
        if let bornes = e["bornes_vitesse"] as? [NSNumber], bornes.count == 2 {
            vitesseMin = bornes[0].doubleValue
            vitesseMax = bornes[1].doubleValue
        }
        if let bornes = e["bornes_longueur"] as? [NSNumber], bornes.count == 2 {
            longueurMin = bornes[0].doubleValue
            longueurMax = bornes[1].doubleValue
        }
        if let ecoute = e["etat_ecoute"] as? [String: Any] {
            etatEcoute = ecoute["etat"] as? String ?? ""
            messageEcoute = ecoute["message"] as? String ?? ""
            erreurEcoute = ecoute["erreur"] as? String ?? ""
            modelesEcoute = (ecoute["modeles"] as? [[Any]] ?? []).compactMap { (ligne: [Any]) -> ModeleEcoute? in
                guard ligne.count == 4, let id = ligne[0] as? String, let nom = ligne[1] as? String else { return nil }
                return ModeleEcoute(id: id, nom: nom, tailleMo: (ligne[2] as? NSNumber)?.intValue ?? 0,
                                    installe: ligne[3] as? Bool ?? false)
            }
        }
        etatDemarrage = e["etat_demarrage"] as? String ?? etatDemarrage
    }

    /// Valeurs lues directement dans parametres.json, avant que le moteur ne démarre
    /// (l'arrière-plan doit être connu dès le lancement de l'application).
    mutating func lireFichier(_ valeurs: [String: Any]) {
        for (cle, valeur) in valeurs {
            appliquer(cle, valeur)
        }
    }
}

/// Une information de la fiche du cerveau (prénom, activité…).
struct ChampCerveau: Identifiable, Hashable {
    var id: String { cle }
    let cle: String
    let libelle: String
    var valeur: String
}

/// Le cerveau complet, pour le panneau Paramètres.
struct FicheCerveau {
    var champs: [ChampCerveau] = []
    var souvenirs: [String] = []
    var taille = 0
    var limite = 0

    init() {}

    init(_ e: [String: Any]) {
        champs = (e["champs"] as? [[Any]] ?? []).compactMap { ligne in
            guard ligne.count == 3, let cle = ligne[0] as? String, let libelle = ligne[1] as? String else { return nil }
            return ChampCerveau(cle: cle, libelle: libelle, valeur: ligne[2] as? String ?? "")
        }
        souvenirs = e["souvenirs"] as? [String] ?? []
        taille = e["taille"] as? Int ?? 0
        limite = e["limite"] as? Int ?? 0
    }
}
