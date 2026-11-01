// Systeme.swift — les actions sur le Mac, faites par l'application avec les outils d'Apple.
//
// Le moteur Python ne pilote pas le Mac lui-même : il demande à l'application d'agir (message
// « systeme », voir serveur.py), chaque fois avec ton accord. Une seule façon de faire pour
// l'installeur (DMG) comme pour l'App Store, où le moteur vit dans le « bac à sable » d'Apple et ne
// pourrait pas piloter les autres apps ; et les autorisations de macOS sont au nom d'O.V.E.R. :
//   ouvrir     une app ou une adresse web (NSWorkspace)
//   volume     le son du Mac (Core Audio)
//   notifier   une notification (fin d'un minuteur)
//   rappel     un rappel dans l'app Rappels (EventKit : macOS te demande l'accès la première fois)
//   agenda     les rendez-vous et les rappels du jour, pour le briefing (EventKit)
// Quand le moteur attend le résultat, l'application répond par « systeme_resultat ».
//
// Ici aussi : l'ouverture d'O.V.E.R. avec ta session (éléments de connexion de macOS).

import AppKit
import AudioToolbox
import Carbon
import CoreAudio
import EventKit
import Foundation
import ServiceManagement
import UserNotifications

/// Résultat d'une action, renvoyé au moteur.
struct ResultatSysteme {
    var ok: Bool
    var detail = ""
    var donnees: [String: Any] = [:]

    static let reussi = ResultatSysteme(ok: true)
    static func echec(_ detail: String) -> ResultatSysteme { ResultatSysteme(ok: false, detail: detail) }
}

enum Systeme {
    /// Exécute une action demandée par le moteur ; fin est appelée une fois, sur le fil principal.
    static func executer(_ action: String, _ parametres: [String: Any],
                         fin: @escaping (ResultatSysteme) -> Void) {
        let terminer: (ResultatSysteme) -> Void = { resultat in
            if Thread.isMainThread {
                fin(resultat)
            } else {
                DispatchQueue.main.async { fin(resultat) }
            }
        }
        switch action {
        case "ouvrir":
            ouvrir(parametres, fin: terminer)
        case "volume":
            terminer(Volume.regler(genre: parametres["genre"] as? String ?? "",
                                   niveau: (parametres["niveau"] as? NSNumber)?.intValue ?? -1))
        case "notifier":
            Notifications.envoyer(titre: parametres["titre"] as? String ?? "O.V.E.R.",
                                  texte: parametres["texte"] as? String ?? "")
            terminer(.reussi)
        case "rappel":
            Calendrier.partage.creerRappel(texte: parametres["texte"] as? String ?? "",
                                           date: parametres["date"] as? String ?? "", fin: terminer)
        case "agenda":
            Calendrier.partage.lireJournee(agenda: parametres["agenda"] as? Bool ?? false,
                                           rappels: parametres["rappels"] as? Bool ?? false, fin: terminer)
        default:
            terminer(.echec("action inconnue : \(action)"))
        }
    }

    // MARK: - Ouvrir une app ou une adresse

    /// Paramètres : « cible » (chemin d'une app .app, ou adresse http, https ou des Réglages Système),
    /// ou « noms » (noms d'apps à chercher par macOS, dans l'ordre).
    private static func ouvrir(_ parametres: [String: Any], fin: @escaping (ResultatSysteme) -> Void) {
        if let noms = parametres["noms"] as? [String] {
            for nom in noms where !nom.isEmpty {
                if let chemin = NSWorkspace.shared.fullPath(forApplication: nom) {
                    lancerApplication(URL(fileURLWithPath: chemin), fin: fin)
                    return
                }
            }
            fin(.echec("application introuvable"))
            return
        }
        let cible = parametres["cible"] as? String ?? ""
        if cible.hasPrefix("/") {
            let url = URL(fileURLWithPath: cible)
            guard url.pathExtension.lowercased() == "app" else {
                fin(.echec("seules les applications s'ouvrent ainsi"))
                return
            }
            lancerApplication(url, fin: fin)
            return
        }
        guard let url = URL(string: cible), let schema = url.scheme?.lowercased(),
              ["http", "https", "x-apple.systempreferences"].contains(schema) else {
            fin(.echec("adresse refusée : \(cible)"))
            return
        }
        fin(NSWorkspace.shared.open(url) ? .reussi : .echec("macOS n'a pas pu ouvrir \(cible)"))
    }

    private static func lancerApplication(_ url: URL, fin: @escaping (ResultatSysteme) -> Void) {
        let configuration = NSWorkspace.OpenConfiguration()
        configuration.activates = true
        NSWorkspace.shared.openApplication(at: url, configuration: configuration) { _, erreur in
            var resultat = erreur.map { ResultatSysteme.echec($0.localizedDescription) } ?? .reussi
            resultat.donnees["nom"] = url.deletingPathExtension().lastPathComponent
            fin(resultat)
        }
    }
}

// MARK: - Le son du Mac (Core Audio)

enum Volume {
    /// Volume principal de la sortie (« vmvc », celui de la barre des menus) ; sinon, celui des canaux.
    private static let volumePrincipal: AudioObjectPropertySelector = 0x766D_7663

    private static func adresse(_ selecteur: AudioObjectPropertySelector,
                                _ element: AudioObjectPropertyElement = 0) -> AudioObjectPropertyAddress {
        AudioObjectPropertyAddress(mSelector: selecteur, mScope: kAudioDevicePropertyScopeOutput, mElement: element)
    }

    private static func sortieParDefaut() -> AudioDeviceID? {
        var adresse = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyDefaultOutputDevice,
                                                 mScope: kAudioObjectPropertyScopeGlobal, mElement: 0)
        var appareil = AudioDeviceID(0)
        var taille = UInt32(MemoryLayout<AudioDeviceID>.size)
        let statut = AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &adresse, 0, nil,
                                                &taille, &appareil)
        return statut == noErr && appareil != 0 ? appareil : nil
    }

    private static func modifiable(_ appareil: AudioDeviceID, _ adresse: inout AudioObjectPropertyAddress) -> Bool {
        guard AudioObjectHasProperty(appareil, &adresse) else { return false }
        var reponse: DarwinBoolean = false
        return AudioObjectIsPropertySettable(appareil, &adresse, &reponse) == noErr && reponse.boolValue
    }

    /// Niveau de 0 à 100, ou nil si la sortie ne le dit pas.
    static func niveau(_ appareil: AudioDeviceID) -> Int? {
        let essais: [(AudioObjectPropertySelector, AudioObjectPropertyElement)] =
            [(volumePrincipal, 0), (kAudioDevicePropertyVolumeScalar, 0), (kAudioDevicePropertyVolumeScalar, 1)]
        for (selecteur, element) in essais {
            var a = adresse(selecteur, element)
            guard AudioObjectHasProperty(appareil, &a) else { continue }
            var valeur = Float32(0)
            var taille = UInt32(MemoryLayout<Float32>.size)
            if AudioObjectGetPropertyData(appareil, &a, 0, nil, &taille, &valeur) == noErr {
                return Int((valeur * 100).rounded())
            }
        }
        return nil
    }

    private static func ecrire(_ appareil: AudioDeviceID, _ niveau: Int) -> Bool {
        var valeur = Float32(max(0, min(100, niveau))) / 100
        let taille = UInt32(MemoryLayout<Float32>.size)
        var principal = adresse(volumePrincipal)
        if modifiable(appareil, &principal),
           AudioObjectSetPropertyData(appareil, &principal, 0, nil, taille, &valeur) == noErr {
            return true
        }
        var reussi = false
        for element: AudioObjectPropertyElement in [0, 1, 2] {
            var a = adresse(kAudioDevicePropertyVolumeScalar, element)
            guard modifiable(appareil, &a) else { continue }
            if AudioObjectSetPropertyData(appareil, &a, 0, nil, taille, &valeur) == noErr {
                reussi = true
                if element == 0 { break }   // le canal principal suffit
            }
        }
        return reussi
    }

    private static func couper(_ appareil: AudioDeviceID, _ muet: Bool) -> Bool {
        var a = adresse(kAudioDevicePropertyMute)
        guard modifiable(appareil, &a) else { return false }
        var valeur: UInt32 = muet ? 1 : 0
        return AudioObjectSetPropertyData(appareil, &a, 0, nil, UInt32(MemoryLayout<UInt32>.size), &valeur) == noErr
    }

    /// genre : couper | remettre | plus | moins | regler (niveau de 0 à 100).
    static func regler(genre: String, niveau demande: Int) -> ResultatSysteme {
        guard let appareil = sortieParDefaut() else { return .echec("aucune sortie son") }
        switch genre {
        case "couper":
            return couper(appareil, true) ? .reussi : .echec("cette sortie son ne se coupe pas")
        case "remettre":
            return couper(appareil, false) ? .reussi : .echec("cette sortie son ne se coupe pas")
        default:
            var cible = demande
            if genre == "plus" || genre == "moins" {
                let actuel = niveau(appareil) ?? 45
                cible = genre == "plus" ? min(100, actuel + 15) : max(0, actuel - 15)
            }
            guard (0...100).contains(cible) else { return .echec("niveau incompris") }
            guard ecrire(appareil, cible) else {
                return .echec("le volume de cette sortie ne se règle pas depuis le Mac (écran, enceinte ?)")
            }
            _ = couper(appareil, false)
            var resultat = ResultatSysteme.reussi
            resultat.donnees["niveau"] = cible
            return resultat
        }
    }
}

// MARK: - Notifications

final class Notifications: NSObject, UNUserNotificationCenterDelegate {
    private static let delegue = Notifications()

    static func envoyer(titre: String, texte: String) {
        let centre = UNUserNotificationCenter.current()
        centre.delegate = delegue   // affichée même quand O.V.E.R. est devant
        centre.requestAuthorization(options: [.alert, .sound]) { autorise, _ in
            guard autorise else { return }
            let contenu = UNMutableNotificationContent()
            contenu.title = titre
            contenu.body = texte
            contenu.sound = .default
            centre.add(UNNotificationRequest(identifier: UUID().uuidString, content: contenu, trigger: nil)) { _ in }
        }
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .sound])
    }
}

// MARK: - Rappels et agenda (EventKit)

final class Calendrier {
    static let partage = Calendrier()
    private let magasin = EKEventStore()

    private static let formatMinute: DateFormatter = {
        let format = DateFormatter()
        format.locale = Locale(identifier: "en_US_POSIX")
        format.timeZone = TimeZone.current
        format.dateFormat = "yyyy-MM-dd'T'HH:mm"
        return format
    }()

    private static let formatHeure: DateFormatter = {
        let format = DateFormatter()
        format.locale = Locale(identifier: "en_US_POSIX")
        format.timeZone = TimeZone.current
        format.dateFormat = "HH:mm"
        return format
    }()

    /// Accès complet aux rappels ou au calendrier ; demandé une fois (macOS affiche la question).
    private func acces(_ type: EKEntityType, fin: @escaping (Bool, String) -> Void) {
        let nom = type == .reminder ? "Rappels" : "Calendriers"
        let refuse = "accès refusé : Réglages Système ▸ Confidentialité et sécurité ▸ \(nom) ▸ O.V.E.R."
        // 3 : accès complet (« authorized », « fullAccess » depuis macOS 14) ; 0 : jamais demandé
        switch EKEventStore.authorizationStatus(for: type).rawValue {
        case 3:
            fin(true, "")
        case 0:
            let reponse: (Bool) -> Void = { [weak self] accorde in
                if accorde { self?.magasin.reset() }   // le magasin relit les listes après l'accord
                fin(accorde, accorde ? "" : refuse)
            }
            if #available(macOS 14.0, *) {
                if type == .reminder {
                    magasin.requestFullAccessToReminders { accorde, _ in reponse(accorde) }
                } else {
                    magasin.requestFullAccessToEvents { accorde, _ in reponse(accorde) }
                }
            } else {
                magasin.requestAccess(to: type) { accorde, _ in reponse(accorde) }
            }
        default:
            fin(false, refuse)
        }
    }

    /// Nouveau rappel dans la liste par défaut ; date « AAAA-MM-JJTHH:MM » (vide : sans date).
    func creerRappel(texte: String, date: String, fin: @escaping (ResultatSysteme) -> Void) {
        guard !texte.isEmpty else { fin(.echec("texte du rappel vide")); return }
        acces(.reminder) { [weak self] accorde, explication in
            guard let self = self else { return }
            guard accorde else { fin(.echec(explication)); return }
            guard let liste = self.magasin.defaultCalendarForNewReminders() else {
                fin(.echec("aucune liste de rappels sur ce Mac"))
                return
            }
            let rappel = EKReminder(eventStore: self.magasin)
            rappel.title = texte
            rappel.calendar = liste
            if let moment = Calendrier.formatMinute.date(from: date) {
                rappel.dueDateComponents = Calendar.current.dateComponents(
                    [.year, .month, .day, .hour, .minute], from: moment)
                rappel.addAlarm(EKAlarm(absoluteDate: moment))   // comme « remind me date » : il sonne
            }
            do {
                try self.magasin.save(rappel, commit: true)
                fin(.reussi)
            } catch {
                fin(.echec(error.localizedDescription))
            }
        }
    }

    /// Les rendez-vous du jour et les rappels pas faits jusqu'à ce soir (en retard compris).
    /// Données : evenements [{titre, heure "HH:MM", journee}], rappels [{titre, echeance "AAAA-MM-JJTHH:MM"}] ;
    /// erreur_agenda / erreur_rappels si l'accès manque.
    func lireJournee(agenda: Bool, rappels: Bool, fin: @escaping (ResultatSysteme) -> Void) {
        var resultat = ResultatSysteme.reussi
        let debut = Calendar.current.startOfDay(for: Date())
        let finJour = Calendar.current.date(byAdding: .day, value: 1, to: debut) ?? debut.addingTimeInterval(86_400)

        let lireRappels = { [weak self] in
            guard let self = self, rappels else { fin(resultat); return }
            self.acces(.reminder) { accorde, explication in
                guard accorde else {
                    resultat.donnees["erreur_rappels"] = explication
                    fin(resultat)
                    return
                }
                let predicat = self.magasin.predicateForIncompleteReminders(withDueDateStarting: nil, ending: finJour,
                                                                            calendars: nil)
                _ = self.magasin.fetchReminders(matching: predicat) { trouves in
                    resultat.donnees["rappels"] = (trouves ?? []).compactMap { rappel -> [String: Any]? in
                        guard let composants = rappel.dueDateComponents,
                              let moment = Calendar.current.date(from: composants) else { return nil }
                        var echeance = Calendrier.formatMinute.string(from: moment)
                        if composants.hour == nil {   // sans heure : minuit (le briefing n'affiche pas d'heure)
                            echeance = String(echeance.prefix(10)) + "T00:00"
                        }
                        return ["titre": rappel.title ?? "", "echeance": echeance]
                    }
                    fin(resultat)
                }
            }
        }

        guard agenda else { lireRappels(); return }
        acces(.event) { [weak self] accorde, explication in
            guard let self = self else { return }
            if accorde {
                let predicat = self.magasin.predicateForEvents(withStart: debut, end: finJour, calendars: nil)
                resultat.donnees["evenements"] = self.magasin.events(matching: predicat)
                    .sorted { ($0.startDate ?? debut) < ($1.startDate ?? debut) }
                    .map { evenement -> [String: Any] in
                        let commence = max(evenement.startDate ?? debut, debut)   // commencé hier : minuit
                        return ["titre": evenement.title ?? "", "journee": evenement.isAllDay,
                                "heure": Calendrier.formatHeure.string(from: commence)]
                    }
            } else {
                resultat.donnees["erreur_agenda"] = explication
            }
            lireRappels()
        }
    }
}

// MARK: - Ouverture avec la session (éléments de connexion)

enum Connexion {
    /// actif | a_approuver | inactif | indisponible (macOS 12)
    static var etat: String {
        guard #available(macOS 13.0, *) else { return "indisponible" }
        switch SMAppService.mainApp.status {
        case .enabled: return "actif"
        case .requiresApproval: return "a_approuver"
        default: return "inactif"
        }
    }

    /// Inscrit (ou retire) O.V.E.R. des éléments de connexion. Renvoie l'explication d'un échec, sinon nil.
    static func appliquer(_ actif: Bool) -> String? {
        guard #available(macOS 13.0, *) else {
            return actif ? "L'ouverture avec la session demande macOS 13 ou plus récent." : nil
        }
        let service = SMAppService.mainApp
        do {
            if actif {
                if service.status != .enabled { try service.register() }
            } else if service.status == .enabled || service.status == .requiresApproval {
                try service.unregister()
            }
            return nil
        } catch {
            return "macOS refuse : \(error.localizedDescription)"
        }
    }

    /// Réglages Système ▸ Général ▸ Ouverture et extensions.
    static func ouvrirReglages() {
        if #available(macOS 13.0, *) {
            SMAppService.openSystemSettingsLoginItems()
        }
    }

    /// Ouverte par macOS à la connexion (et non par toi) ? À appeler au lancement.
    static func lanceeALaConnexion() -> Bool {
        let evenement = NSAppleEventManager.shared().currentAppleEvent
        if evenement?.eventID == AEEventID(kAEOpenApplication),
           evenement?.paramDescriptor(forKeyword: AEKeyword(keyAEPropData))?.enumCodeValue
            == OSType(keyAELaunchedAsLogInItem) {
            return true
        }
        return ProcessInfo.processInfo.systemUptime < 120   // juste après le démarrage du Mac
    }
}
