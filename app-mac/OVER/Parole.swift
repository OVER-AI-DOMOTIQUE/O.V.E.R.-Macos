// Parole.swift — parler à O.V.E.R. de n'importe où, en gardant un raccourci clavier enfoncé.
//
// Le raccourci (par défaut ⌃⌥ Espace, réglable dans Paramètres ▸ Voix) marche même quand la
// fenêtre est fermée ou qu'une autre application est devant : on le garde enfoncé, on parle,
// on relâche. Une petite fenêtre flottante apparaît en bas de l'écran : une animation suit la
// voix, le texte s'écrit pendant qu'on parle, puis la réponse d'O.V.E.R. s'y affiche (et se lit
// à voix haute si la voix est active). Elle disparaît toute seule quelques secondes après.
//
// Le raccourci est enregistré auprès de macOS par RegisterEventHotKey : pas besoin
// d'autorisation d'accessibilité, et rien d'autre que ces touches n'est écouté.

import AppKit
import Carbon
import Combine
import SwiftUI

// MARK: - Le raccourci clavier global

/// Un raccourci global, avec l'appui et le relâchement (pour « maintenir pour parler »).
final class RaccourciClavier {
    var appuye: () -> Void = {}
    var relache: () -> Void = {}
    private var reference: EventHotKeyRef?
    private var gestionnaire: EventHandlerRef?

    /// Les raccourcis proposés : réglage → (touche, modificateurs Carbon, libellé)
    static let choix: [(cle: String, touche: Int, modificateurs: Int, libelle: String)] = [
        ("ctrl_option_espace", kVK_Space, controlKey | optionKey, "⌃⌥ Espace"),
        ("option_espace", kVK_Space, optionKey, "⌥ Espace"),
        ("cmd_shift_espace", kVK_Space, cmdKey | shiftKey, "⇧⌘ Espace"),
        ("ctrl_espace", kVK_Space, controlKey, "⌃ Espace"),
    ]

    static func libelle(_ cle: String) -> String {
        choix.first(where: { $0.cle == cle })?.libelle ?? "aucun"
    }

    /// Raccourcis de macOS qui prennent les mêmes touches (numéros de com.apple.symbolichotkeys) :
    /// 60 = source de saisie précédente (⌃ Espace), 61 = source suivante (⌃⌥ Espace).
    private static let raccourcisMacOS: [String: (numero: String, nom: String)] = [
        "ctrl_espace": ("60", "« Sélectionner la source de saisie précédente »"),
        "ctrl_option_espace": ("61", "« Sélectionner la source suivante du menu Saisie »"),
    ]

    /// Explication si macOS se sert déjà de ces touches (il passe avant O.V.E.R.), "" sinon.
    /// Ces raccourcis de macOS n'agissent que s'il y a au moins deux claviers (ou méthodes de saisie).
    static func conflitMacOS(_ cle: String) -> String {
        guard let systeme = raccourcisMacOS[cle] else { return "" }
        let symboliques = UserDefaults(suiteName: "com.apple.symbolichotkeys")?.dictionary(forKey: "AppleSymbolicHotKeys")
        if let entree = symboliques?[systeme.numero] as? [String: Any], let actif = entree["enabled"] as? Bool, !actif {
            return ""   // désactivé dans Réglages Système ▸ Clavier ▸ Raccourcis clavier
        }
        let sources = UserDefaults(suiteName: "com.apple.HIToolbox")?.array(forKey: "AppleEnabledInputSources") ?? []
        let claviers = sources.compactMap { $0 as? [String: Any] }.filter { source in
            let genre = source["InputSourceKind"] as? String ?? ""
            return genre == "Keyboard Layout" || genre == "Input Mode"
        }
        guard claviers.count >= 2 else { return "" }
        return "\(libelle(cle)) est aussi le raccourci macOS \(systeme.nom), qui passe avant O.V.E.R. : choisis-en "
            + "un autre, ou désactive celui de macOS dans Réglages Système ▸ Clavier ▸ Raccourcis clavier ▸ "
            + "Méthodes de saisie."
    }

    /// Enregistre le raccourci ; renvoie une explication si macOS le refuse (déjà pris), "" sinon.
    func enregistrer(_ cle: String) -> String {
        retirer()
        guard let raccourci = RaccourciClavier.choix.first(where: { $0.cle == cle }) else { return "" }
        var types = [EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed)),
                     EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyReleased))]
        let soi = Unmanaged.passUnretained(self).toOpaque()
        let statut = InstallEventHandler(GetApplicationEventTarget(), { _, evenement, donnees in
            guard let evenement = evenement, let donnees = donnees else { return OSStatus(eventNotHandledErr) }
            let raccourci = Unmanaged<RaccourciClavier>.fromOpaque(donnees).takeUnretainedValue()
            let appui = GetEventKind(evenement) == UInt32(kEventHotKeyPressed)
            DispatchQueue.main.async {
                if appui { raccourci.appuye() } else { raccourci.relache() }
            }
            return OSStatus(noErr)
        }, 2, &types, soi, &gestionnaire)
        guard statut == OSStatus(noErr) else { return "le raccourci n'a pas pu être installé (erreur \(statut))" }
        let identifiant = EventHotKeyID(signature: OSType(0x4F564552), id: 1)   // « OVER »
        let resultat = RegisterEventHotKey(UInt32(raccourci.touche), UInt32(raccourci.modificateurs), identifiant,
                                           GetApplicationEventTarget(), 0, &reference)
        if resultat != OSStatus(noErr) {
            retirer()
            return "\(raccourci.libelle) est déjà pris par une autre application : choisis-en un autre"
        }
        return ""
    }

    func retirer() {
        if let reference = reference {
            UnregisterEventHotKey(reference)
            self.reference = nil
        }
        if let gestionnaire = gestionnaire {
            RemoveEventHandler(gestionnaire)
            self.gestionnaire = nil
        }
    }
}

// MARK: - Le chef d'orchestre : raccourci → micro → message → fenêtre flottante

final class ControleurParole: ObservableObject {
    static let partage = ControleurParole()

    enum Phase: Equatable {
        case cachee        // rien à l'écran
        case ecoute        // touches enfoncées : le micro enregistre
        case transcription // touches relâchées : whisper.cpp écrit le texte
        case reponse       // le message est parti : O.V.E.R. répond
        case probleme      // quelque chose empêche de parler
    }

    @Published private(set) var phase: Phase = .cachee
    @Published private(set) var texteDit = ""          // ce que tu as dit (texte final)
    @Published private(set) var probleme = ""
    @Published private(set) var etatRaccourci = ""     // vide : raccourci actif ; sinon pourquoi il ne l'est pas
    @Published private(set) var conflitRaccourci = ""  // macOS se sert aussi de ces touches (et passe en premier)
    @Published private(set) var debutReponse = 0       // la réponse : messages à partir de cet indice
    @Published private(set) var enAttente = false      // message gardé jusqu'à ce qu'O.V.E.R. soit libre
    @Published private(set) var proposerInstallation = false   // l'écoute n'est pas installée : bouton « Installer »

    var ouvrirFenetrePrincipale: () -> Void = {}

    private let moteur = Moteur.partage
    private let ecoute = Ecoute.partage
    private let raccourci = RaccourciClavier()
    private var fenetre: NSPanel?
    private var abonnements = Set<AnyCancellable>()
    private var minuteurMasquage: Timer?
    private var messageEnAttente: String?
    private var cleRaccourci = ""
    private var toucheEnfoncee = false

    /// Au lancement de l'application.
    func demarrer() {
        raccourci.appuye = { [weak self] in self?.toucheAppuyee() }
        raccourci.relache = { [weak self] in self?.toucheRelachee() }
        moteur.$parametres
            .map { $0.raccourciParler }
            .removeDuplicates()
            .receive(on: RunLoop.main)
            .sink { [weak self] cle in self?.appliquerRaccourci(cle) }
            .store(in: &abonnements)
        ecoute.$resultat
            .compactMap { $0 }
            .filter { $0.mode == .maintenu }
            .receive(on: RunLoop.main)
            .sink { [weak self] resultat in self?.recevoir(resultat) }
            .store(in: &abonnements)
        ecoute.$etat
            .receive(on: RunLoop.main)
            .sink { [weak self] _ in self?.suivreEcoute() }
            .store(in: &abonnements)
        ecoute.$probleme
            .receive(on: RunLoop.main)
            .sink { [weak self] texte in self?.problemeEcoute(texte) }
            .store(in: &abonnements)
        moteur.$etat
            .receive(on: RunLoop.main)
            .sink { [weak self] _ in self?.suivreMoteur() }
            .store(in: &abonnements)
        moteur.$parle
            .receive(on: RunLoop.main)
            .sink { [weak self] _ in self?.suivreMoteur() }
            .store(in: &abonnements)
    }

    private func appliquerRaccourci(_ cle: String) {
        guard cle != cleRaccourci else { return }
        cleRaccourci = cle
        etatRaccourci = raccourci.enregistrer(cle)
        conflitRaccourci = etatRaccourci.isEmpty ? RaccourciClavier.conflitMacOS(cle) : ""
    }

    // MARK: Touches

    private func toucheAppuyee() {
        toucheEnfoncee = true
        guard ecoute.etat == .repos else { return }   // déjà en train d'écouter (touche répétée) ou de transcrire
        minuteurMasquage?.invalidate()
        texteDit = ""
        probleme = ""
        enAttente = false
        proposerInstallation = false
        guard moteur.parametres.etatEcoute == "prete" else {
            if moteur.parametres.etatEcoute == "installation" {
                let etape = moteur.parametres.messageEcoute
                montrerProbleme("Installation de mon écoute en cours" + (etape.isEmpty ? "" : " (" + etape + ")")
                                + " : je te préviens dès qu'elle est prête.")
            } else {
                proposerInstallation = true
                montrerProbleme("Mon écoute locale n'est pas encore installée : whisper.cpp, compilé pour ton Mac "
                                + "(5 à 15 minutes, une seule fois). Rien ne passera par Apple.", duree: 20)
            }
            return
        }
        if moteur.etat == .occupe {
            moteur.arreter()   // une nouvelle question remplace la réponse en cours
        }
        moteur.taire()         // sinon il s'entendrait parler
        phase = .ecoute        // avant de commencer : un problème du micro s'affichera ici
        afficher()
        ecoute.commencer(mode: .maintenu)
    }

    private func toucheRelachee() {
        toucheEnfoncee = false
        if ecoute.etat == .ecoute && ecoute.mode == .maintenu {
            ecoute.terminer()
        }
    }

    // MARK: Ce qui se passe ensuite

    private func suivreEcoute() {
        guard ecoute.mode == .maintenu else { return }
        switch ecoute.etat {
        case .ecoute:
            if !toucheEnfoncee {
                // le micro a démarré après le relâchement (première autorisation du micro) : on n'enregistre pas
                ecoute.annuler()
                montrerProbleme("C'est prêt : garde les touches enfoncées pendant que tu parles.")
                return
            }
            phase = .ecoute
            afficher()
        case .transcription:
            phase = .transcription
        case .repos:
            if phase == .ecoute {
                // fin sans transcription (trop court, rien entendu…) : on se retire bientôt ;
                // s'il y a un problème à dire, il arrive par ecoute.probleme et reste plus longtemps
                programmerMasquage(apres: 2)
            }
        }
    }

    private func problemeEcoute(_ texte: String) {
        guard !texte.isEmpty, ecoute.mode == .maintenu, phase == .ecoute || phase == .transcription else { return }
        montrerProbleme(texte)
        ecoute.probleme = ""
    }

    private func recevoir(_ resultat: ResultatEcoute) {
        if resultat.erreur == "a_installer" {
            montrerProbleme(resultat.explication.isEmpty ? "Mon écoute n'est pas installée." : resultat.explication)
        } else if !resultat.erreur.isEmpty {
            montrerProbleme("Je n'ai pas pu transcrire : " + resultat.erreur)
        } else if resultat.texte.isEmpty {
            montrerProbleme("Je n'ai rien compris. Réessaie en parlant un peu plus près du micro.")
        } else {
            texteDit = resultat.texte
            envoyer(resultat.texte)
        }
    }

    private func envoyer(_ texte: String) {
        minuteurMasquage?.invalidate()
        switch moteur.etat {
        case .pret:
            debutReponse = moteur.messages.count
            moteur.envoyerSaisie(texte)
            phase = .reponse
            enAttente = false
        case .question:
            debutReponse = moteur.messages.count
            moteur.repondre(valeur: texte, libelle: texte)
            phase = .reponse
            enAttente = false
        case .occupe, .chargement:
            messageEnAttente = texte   // parti dès qu'O.V.E.R. est libre
            enAttente = true
            phase = .reponse
        case .arrete, .sansDossier:
            montrerProbleme("Le moteur d'O.V.E.R. est arrêté : ouvre la fenêtre pour le redémarrer.")
        case .installationModele:
            montrerProbleme("O.V.E.R. installe son modèle de langage : ouvre la fenêtre pour suivre l'installation.")
        }
        afficher()
    }

    private func suivreMoteur() {
        if let texte = messageEnAttente, moteur.etat == .pret || moteur.etat == .question {
            messageEnAttente = nil
            envoyer(texte)
            return
        }
        guard phase == .reponse, !enAttente else { return }
        if moteur.etat == .pret && !moteur.parle {
            programmerMasquage(apres: 6)
        } else {
            minuteurMasquage?.invalidate()
        }
    }

    private func montrerProbleme(_ texte: String, duree: TimeInterval = 6) {
        probleme = texte
        phase = .probleme
        afficher()
        programmerMasquage(apres: duree)
    }

    // MARK: La fenêtre flottante

    /// Ce qu'O.V.E.R. a répondu depuis le message envoyé (le dernier passage, la fin seulement si c'est long).
    var texteReponse: String {
        guard phase == .reponse, debutReponse <= moteur.messages.count else { return "" }
        let reponses = moteur.messages[debutReponse...].filter { $0.auteur != .utilisateur }
        guard let derniere = reponses.last else { return "" }
        let texte = derniere.texte.trimmingCharacters(in: .whitespacesAndNewlines)
        return texte.count > 600 ? "…" + String(texte.suffix(600)) : texte
    }

    /// Question Oui / Non posée pendant la réponse (« Je crée ce rappel ? ») : affichée avec ses boutons,
    /// pour qu'une action à confirmer n'attende pas en silence. On peut aussi répondre à la voix.
    var questionEnCours: Message? {
        guard phase == .reponse, moteur.etat == .question, debutReponse <= moteur.messages.count else { return nil }
        return moteur.messages[debutReponse...].last(where: { $0.genre == .question && !$0.repondue })
    }

    func repondre(_ choix: Choix) {
        minuteurMasquage?.invalidate()
        moteur.repondre(valeur: choix.valeur, libelle: choix.libelle)
    }

    func installerEcoute() {
        proposerInstallation = false
        moteur.installerEcoute()
        montrerProbleme("C'est parti : je compile mon écoute pour ton Mac. Je te préviens dès qu'elle est prête.")
    }

    func ouvrirFenetre() {
        masquer()
        ouvrirFenetrePrincipale()
    }

    func masquer() {
        minuteurMasquage?.invalidate()
        if ecoute.etat == .ecoute && ecoute.mode == .maintenu { ecoute.annuler() }
        phase = .cachee
        fenetre?.orderOut(nil)
    }

    private func programmerMasquage(apres secondes: TimeInterval) {
        minuteurMasquage?.invalidate()
        minuteurMasquage = Timer.scheduledTimer(withTimeInterval: secondes, repeats: false) { [weak self] _ in
            guard let self = self else { return }
            let repond = self.phase == .reponse && (self.moteur.etat == .occupe || self.moteur.parle)
            if self.ecoute.etat == .repos && !repond && self.messageEnAttente == nil {
                self.masquer()
            }
        }
    }

    private func afficher() {
        let panneau = fenetre ?? creerFenetre()
        fenetre = panneau
        placer(panneau)
        panneau.orderFrontRegardless()   // devant, sans prendre le clavier ni activer l'application
    }

    private func creerFenetre() -> NSPanel {
        let panneau = NSPanel(contentRect: NSRect(x: 0, y: 0, width: VueParole.largeur, height: 110),
                              styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: true)
        panneau.isFloatingPanel = true
        panneau.level = .statusBar
        panneau.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]
        panneau.hidesOnDeactivate = false
        panneau.isOpaque = false
        panneau.backgroundColor = .clear
        panneau.hasShadow = true
        panneau.isMovableByWindowBackground = true
        panneau.isReleasedWhenClosed = false
        let vue = VueParole(controleur: self, ecoute: ecoute, moteur: moteur)
        panneau.contentView = NSHostingView(rootView: vue)
        return panneau
    }

    /// La vue a changé de hauteur : la fenêtre suit, en gardant son bas au même endroit.
    func hauteurChangee(_ hauteur: CGFloat) {
        guard let panneau = fenetre, hauteur > 0 else { return }
        var cadre = panneau.frame
        cadre.size = NSSize(width: VueParole.largeur, height: ceil(hauteur))
        panneau.setFrame(cadre, display: true)
    }

    private func placer(_ panneau: NSPanel) {
        guard let ecran = NSScreen.main?.visibleFrame else { return }
        let taille = panneau.frame.size
        panneau.setFrameOrigin(NSPoint(x: ecran.midX - taille.width / 2, y: ecran.minY + 70))
    }
}

// MARK: - Ce qu'affiche la fenêtre flottante

private struct HauteurVue: PreferenceKey {
    static var defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) { value = max(value, nextValue()) }
}

struct VueParole: View {
    static let largeur: CGFloat = 560
    @ObservedObject var controleur: ControleurParole
    @ObservedObject var ecoute: Ecoute
    @ObservedObject var moteur: Moteur

    var body: some View {
        HStack(alignment: .top, spacing: 14) {
            AnimationVoix(niveau: ecoute.niveau, ecoute: controleur.phase == .ecoute, travaille: travaille)
                .frame(width: 58, height: 58)
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 8) {
                    Text(titre)
                        .font(.caption.bold())
                        .foregroundColor(.secondary)
                    if controleur.phase == .ecoute {
                        BarresVoix(niveau: ecoute.niveau)
                    }
                }
                if !texteUtilisateur.isEmpty {
                    Text("« " + texteUtilisateur + " »")
                        .font(.body)
                        .foregroundColor(controleur.texteDit.isEmpty ? Color.secondary : Color.primary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if !controleur.texteReponse.isEmpty {
                    Text(controleur.texteReponse)
                        .font(.callout)
                        .lineLimit(8)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let question = controleur.questionEnCours {
                    if !question.extrait.isEmpty {
                        Text(question.extrait)
                            .font(.callout.italic())
                            .foregroundColor(.secondary)
                            .lineLimit(4)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    HStack(spacing: 8) {
                        ForEach(question.choix) { choix in
                            Button(choix.libelle) { controleur.repondre(choix) }
                                .buttonStyle(.bordered)
                        }
                    }
                    Text("ou garde les touches enfoncées et dis « oui » ou « non »")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                if !controleur.probleme.isEmpty {
                    Text(controleur.probleme)
                        .font(.callout)
                        .foregroundColor(Palette.or)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if controleur.proposerInstallation {
                    Button("Installer l'écoute") { controleur.installerEcoute() }
                        .buttonStyle(.borderedProminent)
                        .tint(Palette.sarcelle)
                }
            }
            Spacer(minLength: 0)
            Button {
                controleur.masquer()
            } label: {
                Image(systemName: "xmark.circle.fill")
                    .foregroundColor(.secondary)
            }
            .buttonStyle(.borderless)
            .help("Masquer")
        }
        .padding(16)
        .frame(width: VueParole.largeur, alignment: .topLeading)
        .background(FondFlou())
        .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous)
                    .stroke(Palette.sarcelle.opacity(0.7), lineWidth: 1))
        .contentShape(Rectangle())
        .onTapGesture { controleur.ouvrirFenetre() }
        .background(GeometryReader { geometrie in
            Color.clear.preference(key: HauteurVue.self, value: geometrie.size.height)
        })
        .onPreferenceChange(HauteurVue.self) { hauteur in controleur.hauteurChangee(hauteur) }
    }

    private var travaille: Bool {
        controleur.phase == .transcription || (controleur.phase == .reponse && (moteur.etat == .occupe || controleur.enAttente))
    }

    private var texteUtilisateur: String {
        controleur.texteDit.isEmpty ? ecoute.texteApercu : controleur.texteDit
    }

    private var titre: String {
        switch controleur.phase {
        case .ecoute: return "J'écoute… relâche les touches quand tu as fini"
        case .transcription: return "J'écris ce que tu as dit…"
        case .reponse:
            if controleur.enAttente { return "Message gardé : je l'envoie dès que j'ai fini" }
            if moteur.etat == .occupe { return moteur.parle ? "O.V.E.R. répond…" : "O.V.E.R. réfléchit…" }
            if controleur.questionEnCours != nil { return "O.V.E.R. attend ta réponse" }
            return moteur.parle ? "O.V.E.R. parle… (clic pour ouvrir la fenêtre)" : "O.V.E.R. (clic pour ouvrir la fenêtre)"
        case .probleme: return "O.V.E.R."
        case .cachee: return ""
        }
    }
}

/// Le logo, avec des anneaux qui suivent la voix ; un arc tourne quand O.V.E.R. travaille.
struct AnimationVoix: View {
    let niveau: Double
    let ecoute: Bool
    let travaille: Bool
    @State private var angle = 0.0

    var body: some View {
        ZStack {
            ForEach(0..<3, id: \.self) { rang in
                Circle()
                    .stroke(Palette.or.opacity(ecoute ? 0.55 - Double(rang) * 0.15 : 0), lineWidth: 2)
                    .scaleEffect(0.8 + CGFloat(niveau) * (0.25 + CGFloat(rang) * 0.18))
            }
            if travaille {
                Circle()
                    .trim(from: 0, to: 0.28)
                    .stroke(Palette.sarcelle, style: StrokeStyle(lineWidth: 3, lineCap: .round))
                    .rotationEffect(.degrees(angle))
                    .onAppear {
                        withAnimation(.linear(duration: 1).repeatForever(autoreverses: false)) { angle = 360 }
                    }
                    .onDisappear { angle = 0 }
            }
            Logo(taille: 40)
        }
        .animation(.easeOut(duration: 0.12), value: niveau)
    }
}

/// Petites barres qui dansent avec la voix.
struct BarresVoix: View {
    let niveau: Double

    var body: some View {
        TimelineView(.animation) { contexte in
            let temps = contexte.date.timeIntervalSinceReferenceDate
            HStack(alignment: .center, spacing: 3) {
                ForEach(0..<9, id: \.self) { rang in
                    Capsule()
                        .fill(Palette.or)
                        .frame(width: 3, height: hauteurBarre(rang, temps))
                }
            }
            .frame(height: 22)
        }
    }

    private func hauteurBarre(_ rang: Int, _ temps: Double) -> CGFloat {
        let onde = (sin(temps * 9 + Double(rang) * 0.8) + 1) / 2
        return 3 + CGFloat(niveau) * 19 * CGFloat(0.35 + 0.65 * onde)
    }
}

/// Fond translucide flou, comme les panneaux de macOS.
struct FondFlou: NSViewRepresentable {
    var rayon: CGFloat = 18

    func makeNSView(context: Context) -> NSVisualEffectView {
        let vue = NSVisualEffectView()
        vue.material = .hudWindow
        vue.blendingMode = .behindWindow
        vue.state = .active
        vue.wantsLayer = true
        vue.layer?.cornerRadius = rayon
        vue.layer?.masksToBounds = true
        return vue
    }

    func updateNSView(_ vue: NSVisualEffectView, context: Context) {}
}
