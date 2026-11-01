// Ecoute.swift — O.V.E.R. écoute : ce que tu dis devient le message.
//
// Le micro enregistre ta voix (WAV 16 kHz, mono) et le moteur Python la transcrit sur le Mac
// avec whisper.cpp : rien ne part chez Apple ni ailleurs.
//
// Deux façons de parler :
//   - automatique : bouton micro de la fenêtre (⌘D). Une petite pause termine l'écoute.
//   - maintenu    : raccourci clavier gardé enfoncé (même fenêtre fermée). Relâcher termine.
// Pendant qu'on parle, un aperçu du texte est demandé toutes les deux secondes environ :
// le texte s'écrit presque en même temps. L'aperçu est abandonné dès que la vraie
// transcription part (il ne la retarde jamais).

import AVFoundation
import Foundation

/// Le texte final d'une écoute (ou son erreur).
struct ResultatEcoute {
    let id = UUID()
    let texte: String
    let erreur: String          // vide si tout va bien ; « a_installer » si l'écoute n'est pas installée
    let explication: String
    let duree: Double           // secondes de calcul
    let mode: Ecoute.Mode
}

final class Ecoute: ObservableObject {
    /// Une seule écoute pour toute l'application (fenêtre, raccourci clavier, fenêtre flottante).
    static let partage = Ecoute()

    enum Etat: Equatable {
        case repos          // micro coupé
        case ecoute         // le micro enregistre
        case transcription  // whisper.cpp transforme l'enregistrement en texte
    }

    enum Mode: Equatable {
        case automatique    // une pause termine l'écoute
        case maintenu       // relâcher le raccourci termine l'écoute
    }

    @Published private(set) var etat: Etat = .repos
    @Published private(set) var mode: Mode = .automatique
    @Published private(set) var niveau: Double = 0     // volume du micro, de 0 à 1 (animations)
    @Published private(set) var texteApercu = ""       // ce qui est déjà compris, pendant qu'on parle
    @Published private(set) var resultat: ResultatEcoute?
    @Published var probleme = ""                       // explication si l'écoute est impossible

    var enCours: Bool { etat != .repos }

    static let frequence: Double = 16_000          // ce qu'attend whisper.cpp
    static let silenceAvantFin: Double = 1.2       // mode automatique : secondes de silence après la parole
    static let attenteMaximum: Double = 8          // mode automatique : secondes sans parole avant abandon
    static let dureeMaximumAutomatique: Double = 30
    static let dureeMaximumMaintenu: Double = 60
    static let margeAvant: Double = 0.3            // son gardé avant le début de la parole
    static let margeApres: Double = 0.4
    static let intervalleApercu: Double = 2.0      // un aperçu au plus toutes les deux secondes
    static let apercuMaximum: Double = 25          // au-delà, plus d'aperçu (trop long à recalculer)

    private enum Fin { case pause, manuelle, rien, annulee }

    private let moteurAudio = AVAudioEngine()
    private let verrou = NSLock()                  // le micro remplit le tampon depuis son propre fil
    private var echantillons: [Float] = []
    private var convertisseur: AVAudioConverter?
    private var formatCible: AVAudioFormat?
    private var bruitDeFond: Float = 0
    private var debutParole: Int?                  // indice du premier échantillon de parole
    private var finParole = 0
    private var fichierEnCours: URL?
    private var session = 0                        // numéro de l'écoute (les réponses en retard sont ignorées)
    private var numeroApercu = 0
    private var apercuEnVol = false
    private var dernierApercu = Date.distantPast
    private var minuteurApercu: Timer?

    // MARK: - Commencer

    func commencer(mode: Mode) {
        probleme = ""
        guard etat == .repos else { return }
        self.mode = mode
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized:
            lancer()
        case .notDetermined:
            AVCaptureDevice.requestAccess(for: .audio) { accorde in
                DispatchQueue.main.async {
                    if accorde {
                        self.lancer()
                    } else {
                        self.probleme = Ecoute.texteAutorisation
                    }
                }
            }
        default:
            probleme = Ecoute.texteAutorisation
        }
    }

    private static let texteAutorisation = "Autorise le micro pour O.V.E.R. dans Réglages Système ▸ "
        + "Confidentialité et sécurité ▸ Microphone."

    private func lancer() {
        let entree = moteurAudio.inputNode
        let format = entree.outputFormat(forBus: 0)
        guard format.sampleRate > 0, format.channelCount > 0 else {
            probleme = "Aucun micro disponible."
            return
        }
        guard let cible = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: Ecoute.frequence,
                                        channels: 1, interleaved: false),
              let convertisseur = AVAudioConverter(from: format, to: cible) else {
            probleme = "Le format du micro n'est pas pris en charge."
            return
        }
        formatCible = cible
        self.convertisseur = convertisseur
        verrou.lock()
        echantillons = []
        echantillons.reserveCapacity(Int(Ecoute.frequence * Ecoute.dureeMaximumMaintenu))
        debutParole = nil
        finParole = 0
        bruitDeFond = 0
        verrou.unlock()
        entree.removeTap(onBus: 0)
        entree.installTap(onBus: 0, bufferSize: 4096, format: format) { [weak self] tampon, _ in
            self?.recevoir(tampon)
        }
        moteurAudio.prepare()
        do {
            try moteurAudio.start()
        } catch {
            entree.removeTap(onBus: 0)
            probleme = "Impossible d'ouvrir le micro : \(error.localizedDescription)"
            return
        }
        session += 1
        numeroApercu = 0
        apercuEnVol = false
        dernierApercu = Date()
        texteApercu = ""
        etat = .ecoute
        minuteurApercu?.invalidate()
        minuteurApercu = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] _ in
            self?.demanderApercu()
        }
    }

    // MARK: - Le son qui arrive (fil du micro)

    private func recevoir(_ tampon: AVAudioPCMBuffer) {
        guard let convertisseur = convertisseur, let formatCible = formatCible, tampon.frameLength > 0 else { return }
        let capacite = AVAudioFrameCount(Double(tampon.frameLength) * Ecoute.frequence / tampon.format.sampleRate) + 32
        guard let sortie = AVAudioPCMBuffer(pcmFormat: formatCible, frameCapacity: capacite) else { return }
        var fourni = false
        var erreur: NSError?
        convertisseur.convert(to: sortie, error: &erreur) { _, statut in
            if fourni {
                statut.pointee = .noDataNow
                return nil
            }
            fourni = true
            statut.pointee = .haveData
            return tampon
        }
        guard erreur == nil, let canaux = sortie.floatChannelData, sortie.frameLength > 0 else { return }
        analyser(Array(UnsafeBufferPointer(start: canaux[0], count: Int(sortie.frameLength))))
    }

    /// Range le son et repère la parole : un son plus fort que le bruit de fond.
    private func analyser(_ morceau: [Float]) {
        let energie = (morceau.reduce(Float(0)) { $0 + $1 * $1 } / Float(max(1, morceau.count))).squareRoot()
        let maintenu = mode == .maintenu
        var fin: Fin?
        verrou.lock()
        let debutMorceau = echantillons.count
        echantillons.append(contentsOf: morceau)
        let total = echantillons.count
        if total <= Int(0.25 * Ecoute.frequence) {
            bruitDeFond = max(bruitDeFond, min(energie, 0.03))   // les premiers instants donnent le bruit de la pièce
        }
        let seuil = max(Float(0.012), bruitDeFond * 3)
        if energie > seuil {
            if debutParole == nil { debutParole = debutMorceau }
            finParole = total
        }
        let secondes = Double(total) / Ecoute.frequence
        if !maintenu {
            if debutParole != nil {
                if Double(total - finParole) / Ecoute.frequence >= Ecoute.silenceAvantFin { fin = .pause }
            } else if secondes >= Ecoute.attenteMaximum {
                fin = .rien
            }
        }
        let maximum = maintenu ? Ecoute.dureeMaximumMaintenu : Ecoute.dureeMaximumAutomatique
        if secondes >= maximum { fin = debutParole == nil ? .rien : .pause }
        verrou.unlock()
        let niveauActuel = min(1, Double(energie) / 0.12)
        DispatchQueue.main.async {
            guard self.etat == .ecoute else { return }
            self.niveau = niveauActuel
            if let fin = fin { self.finir(fin) }
        }
    }

    // MARK: - Aperçu : le texte pendant qu'on parle

    private func demanderApercu() {
        guard etat == .ecoute, !apercuEnVol, Date().timeIntervalSince(dernierApercu) >= Ecoute.intervalleApercu else {
            return
        }
        verrou.lock()
        let debut = debutParole
        let total = echantillons.count
        let copie: [Float] = debut.map { premier in
            Array(echantillons[max(0, premier - Int(Ecoute.margeAvant * Ecoute.frequence))..<total])
        } ?? []
        verrou.unlock()
        let duree = Double(copie.count) / Ecoute.frequence
        guard debut != nil, duree >= 1.0, duree <= Ecoute.apercuMaximum else { return }
        apercuEnVol = true
        dernierApercu = Date()
        numeroApercu += 1
        let ident = session * 1000 + min(numeroApercu, 999)
        DispatchQueue.global(qos: .userInitiated).async {
            guard let fichier = try? Ecoute.ecrireWAV(copie, prefixe: "over-apercu-") else {
                DispatchQueue.main.async { self.apercuEnVol = false }
                return
            }
            DispatchQueue.main.async {
                Moteur.partage.transcrire(fichier, apercu: true, ident: ident)
            }
        }
    }

    // MARK: - Les réponses du moteur

    /// Évènement « transcription » du moteur (aperçu ou texte final).
    func recevoirTranscription(_ transcription: Transcription) {
        if transcription.apercu {
            supprimer(transcription.fichier)
            guard transcription.ident / 1000 == session else { return }
            apercuEnVol = false
            if etat != .repos, transcription.erreur.isEmpty, !transcription.texte.isEmpty {
                texteApercu = transcription.texte
            }
            return
        }
        guard etat == .transcription, transcription.ident == session * 1000 else {
            supprimer(transcription.fichier)   // réponse d'une écoute abandonnée
            return
        }
        transcriptionTerminee()
        resultat = ResultatEcoute(texte: transcription.texte, erreur: transcription.erreur,
                                  explication: transcription.explication, duree: transcription.duree, mode: mode)
    }

    private func supprimer(_ chemin: String) {
        guard !chemin.isEmpty else { return }
        let fichier = URL(fileURLWithPath: chemin)
        if fichier.lastPathComponent.hasPrefix("over-") {
            try? FileManager.default.removeItem(at: fichier)
        }
    }

    // MARK: - Finir

    /// Bouton micro pendant l'écoute, ou raccourci relâché : fin tout de suite, ce qui a été dit est transcrit.
    func terminer() {
        finir(.manuelle)
    }

    /// Abandon sans rien envoyer (moteur arrêté…).
    func annuler() {
        if etat == .ecoute {
            finir(.annulee)
        } else if etat == .transcription {
            transcriptionTerminee()
        }
    }

    /// Le texte est arrivé (ou une erreur) : l'enregistrement ne sert plus.
    private func transcriptionTerminee() {
        if let fichier = fichierEnCours {
            try? FileManager.default.removeItem(at: fichier)
        }
        fichierEnCours = nil
        etat = .repos
    }

    private func finir(_ fin: Fin) {
        guard etat == .ecoute else { return }
        minuteurApercu?.invalidate()
        minuteurApercu = nil
        moteurAudio.stop()
        moteurAudio.inputNode.removeTap(onBus: 0)
        verrou.lock()
        let tout = echantillons
        let debut = debutParole
        let derniere = finParole
        echantillons = []
        verrou.unlock()
        niveau = 0
        etat = .repos
        if fin == .annulee { return }
        var debutGarde = 0
        var finGarde = tout.count
        if let debut = debut {
            debutGarde = max(0, debut - Int(Ecoute.margeAvant * Ecoute.frequence))
            finGarde = min(tout.count, derniere + Int(Ecoute.margeApres * Ecoute.frequence))
        } else if fin != .manuelle || tout.count < Int(0.5 * Ecoute.frequence) {
            probleme = mode == .maintenu
                ? "Je n'ai rien entendu : garde les touches enfoncées pendant que tu parles."
                : "Je n'ai rien entendu. Parle un peu plus fort ou plus près du micro."
            texteApercu = ""
            return
        }
        guard finGarde - debutGarde >= Int(0.3 * Ecoute.frequence) else {
            texteApercu = ""
            return
        }
        do {
            let fichier = try Ecoute.ecrireWAV(Array(tout[debutGarde..<finGarde]), prefixe: "over-ecoute-")
            fichierEnCours = fichier
            etat = .transcription
            Moteur.partage.transcrire(fichier, apercu: false, ident: session * 1000)
        } catch {
            probleme = "Enregistrement impossible : \(error.localizedDescription)"
        }
    }

    /// WAV 16 bits, 16 kHz, mono : ce que lit whisper.cpp.
    static func ecrireWAV(_ echantillons: [Float], prefixe: String) throws -> URL {
        let entiers: [Int16] = echantillons.map { echantillon in
            guard echantillon.isFinite else { return 0 }
            return Int16((max(-1, min(1, echantillon)) * 32767).rounded()).littleEndian
        }
        let tailleDonnees = UInt32(entiers.count * 2)
        var entete = Data()
        func ajouter32(_ valeur: UInt32) { withUnsafeBytes(of: valeur.littleEndian) { entete.append(contentsOf: $0) } }
        func ajouter16(_ valeur: UInt16) { withUnsafeBytes(of: valeur.littleEndian) { entete.append(contentsOf: $0) } }
        entete.append(contentsOf: Array("RIFF".utf8))
        ajouter32(36 + tailleDonnees)
        entete.append(contentsOf: Array("WAVEfmt ".utf8))
        ajouter32(16)                          // taille du bloc « fmt »
        ajouter16(1)                           // PCM
        ajouter16(1)                           // mono
        ajouter32(UInt32(Ecoute.frequence))
        ajouter32(UInt32(Ecoute.frequence) * 2) // octets par seconde
        ajouter16(2)                           // octets par échantillon
        ajouter16(16)                          // bits par échantillon
        entete.append(contentsOf: Array("data".utf8))
        ajouter32(tailleDonnees)
        var donnees = entete
        entiers.withUnsafeBufferPointer { donnees.append($0) }
        let fichier = FileManager.default.temporaryDirectory
            .appendingPathComponent("\(prefixe)\(UUID().uuidString).wav")
        try donnees.write(to: fichier, options: .atomic)
        return fichier
    }
}
