// OVERApp.swift — point d'entrée de l'application O.V.E.R. pour Mac.
//
// L'application est une interface : elle lance le moteur Python (serveur.py) et lui parle
// par messages JSON. Version emballée : Python et les scripts sont dans l'application, tes
// données dans ~/Library/Application Support/O.V.E.R. ; sinon, tout est dans le dossier O.V.E.R.
//
// Arrière-plan (Paramètres ▸ Démarrage) : la fenêtre se ferme, l'application et le moteur
// restent ouverts, avec une icône dans la barre des menus. Ouverte par macOS à la connexion
// avec le réglage « sans fenêtre » (ou lancée avec --discret), elle démarre cachée et se
// contente de cette icône.

import AppKit
import Combine
import SwiftUI

@main
struct OVERApp: App {
    @NSApplicationDelegateAdaptor(DelegueApplication.self) private var delegue
    @StateObject private var moteur = Moteur.partage

    var body: some Scene {
        WindowGroup("O.V.E.R.") {
            VuePrincipale()
                .environmentObject(moteur)
                .frame(minWidth: 560, minHeight: 520)
                .background(LecteurFenetre { fenetre in delegue.fenetrePrincipale = fenetre })
                .onAppear {
                    moteur.demarrerSiPossible()
                }
        }
        .commands {
            CommandGroup(replacing: .newItem) { }   // une seule fenêtre
            // Menu O.V.E.R. ▸ Paramètres… (⌘,) : ouvre le panneau dans la fenêtre
            CommandGroup(replacing: .appSettings) {
                Button("Paramètres…") { moteur.afficherParametres.toggle() }
                    .keyboardShortcut(",", modifiers: [.command])
            }
            CommandMenu("Conversation") {
                Button("Arrêter la réponse") { moteur.arreter() }
                    .keyboardShortcut(".", modifiers: [.command])
                    .disabled(moteur.etat != .occupe)
                Divider()
                Button("Voir le cerveau") { moteur.envoyerCommande("/cerveau") }
                    .keyboardShortcut("b", modifiers: [.command])
                    .disabled(!moteur.peutEnvoyer)
                Group {   // regroupés : un menu SwiftUI accepte au plus 10 éléments directs
                    Button("Commandes du cerveau") { moteur.envoyerCommande("/cerveau aide") }
                    Button("Repasser les questions du cerveau") { moteur.envoyerCommande("/cerveau questions") }
                }
                .disabled(!moteur.peutEnvoyer)
                Button("Effacer la conversation") { moteur.effacerConversation() }
                    .keyboardShortcut("k", modifiers: [.command])
                    .disabled(!moteur.peutEnvoyer)
                Button("Aide") { moteur.envoyerCommande("/aide") }
                    .disabled(!moteur.peutEnvoyer)
                Divider()
                Button("Redémarrer le moteur") { moteur.redemarrer() }
                if !Moteur.emballee {
                    Button("Choisir le dossier O.V.E.R.…") { moteur.choisirDossier() }
                }
            }
        }

    }
}

/// Retrouve la fenêtre AppKit qui affiche une vue SwiftUI (pour la rouvrir depuis la barre des menus).
struct LecteurFenetre: NSViewRepresentable {
    let quandTrouvee: (NSWindow) -> Void

    func makeNSView(context: Context) -> NSView {
        let vue = NSView()
        DispatchQueue.main.async {
            if let fenetre = vue.window { quandTrouvee(fenetre) }
        }
        return vue
    }

    func updateNSView(_ vue: NSView, context: Context) {
        DispatchQueue.main.async {
            if let fenetre = vue.window { quandTrouvee(fenetre) }
        }
    }
}

/// Cycle de vie de l'application : moteur, arrière-plan, icône de la barre des menus.
final class DelegueApplication: NSObject, NSApplicationDelegate, NSMenuDelegate {
    weak var fenetrePrincipale: NSWindow?
    private var elementBarre: NSStatusItem?
    private var abonnements = Set<AnyCancellable>()
    /// Ouverte sans fenêtre : par macOS à la connexion (réglage « sans fenêtre »), ou avec --discret
    private var lanceeDiscrete = CommandLine.arguments.contains("--discret")

    private var moteur: Moteur { Moteur.partage }

    func applicationWillFinishLaunching(_ notification: Notification) {
        // Lancement discret avec la session : pas d'icône dans le Dock, même un instant, si c'est réglé ainsi.
        if lanceeDiscrete && moteur.parametres.masquerDock {
            NSApp.setActivationPolicy(.accessory)
        }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        // Écrire vers un moteur déjà fermé ne doit pas faire planter l'application.
        _ = signal(SIGPIPE, SIG_IGN)
        // Icône du Dock imposée au lancement : macOS garde parfois en cache une ancienne icône.
        if let icone = NSImage(named: "IconeApplication") {
            NSApplication.shared.applicationIconImage = icone
        }
        // Ouverte par macOS à la connexion, avec « sans fenêtre » : cachée, comme avec --discret.
        if !lanceeDiscrete && moteur.parametres.demarrageSession && moteur.parametres.demarrageDiscret
            && Connexion.lanceeALaConnexion() {
            lanceeDiscrete = true
        }
        // Le moteur démarre tout de suite, même sans fenêtre (lancement discret).
        moteur.demarrerSiPossible()
        // Raccourci clavier pour parler, même fenêtre fermée (Parole.swift).
        ControleurParole.partage.ouvrirFenetrePrincipale = { [weak self] in self?.afficherFenetre() }
        ControleurParole.partage.demarrer()
        if lanceeDiscrete {
            if moteur.parametres.masquerDock {
                NSApp.setActivationPolicy(.accessory)
            }
            NSApp.hide(nil)
        }
        // Ouverture avec la session : quand le réglage change (ici, par une commande ou une question
        // d'O.V.E.R.), l'application s'inscrit auprès de macOS ou s'en retire.
        moteur.$parametres
            .map { $0.demarrageSession }
            .removeDuplicates()
            .dropFirst()
            .receive(on: RunLoop.main)
            .sink { [weak self] actif in self?.moteur.appliquerConnexion(actif) }
            .store(in: &abonnements)
        // L'icône de la barre des menus suit le réglage « arrière-plan ».
        moteur.$parametres
            .map { $0.arrierePlan }
            .removeDuplicates()
            .receive(on: RunLoop.main)
            .sink { [weak self] _ in self?.mettreAJourBarre() }
            .store(in: &abonnements)
        NotificationCenter.default.addObserver(forName: NSWindow.willCloseNotification, object: nil,
                                               queue: .main) { [weak self] notification in
            guard let self = self, let fenetre = notification.object as? NSWindow,
                  fenetre === self.fenetrePrincipale else { return }
            self.fenetrePrincipale = nil
            DispatchQueue.main.async { self.apresFermeture() }
        }
        mettreAJourBarre()
    }

    /// Fenêtre fermée : on quitte, sauf en arrière-plan (ou si l'application a été ouverte sans fenêtre).
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        !(moteur.parametres.arrierePlan || lanceeDiscrete)
    }

    func applicationWillTerminate(_ notification: Notification) {
        moteur.arreterProcessus()
    }

    private func apresFermeture() {
        guard moteur.parametres.arrierePlan || lanceeDiscrete else { return }
        if moteur.parametres.masquerDock && fenetrePrincipale == nil {
            NSApp.setActivationPolicy(.accessory)   // plus d'icône dans le Dock : il reste celle de la barre des menus
        }
    }

    // MARK: - Icône de la barre des menus

    private func mettreAJourBarre() {
        let voulue = moteur.parametres.arrierePlan || lanceeDiscrete
        if voulue && elementBarre == nil {
            let icone = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
            if let bouton = icone.button {
                bouton.image = DelegueApplication.imageBarreMenus()
                bouton.toolTip = "O.V.E.R."
            }
            let menu = NSMenu()
            menu.delegate = self           // le menu est refait à chaque ouverture (état à jour)
            icone.menu = menu
            elementBarre = icone
        } else if !voulue, let icone = elementBarre {
            NSStatusBar.system.removeStatusItem(icone)
            elementBarre = nil
            NSApp.setActivationPolicy(.regular)
        }
    }

    /// Le logo d'O.V.E.R. en petit (18 points) pour la barre des menus ; à défaut, un symbole du système.
    static func imageBarreMenus() -> NSImage? {
        let image = (NSImage(named: "IconeBarreMenus") ?? NSImage(named: "Logo"))?.copy() as? NSImage
            ?? NSImage(systemSymbolName: "smallcircle.filled.circle", accessibilityDescription: "O.V.E.R.")
        image?.size = NSSize(width: 18, height: 18)
        image?.isTemplate = false   // le vrai logo, en couleurs
        return image
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        menu.removeAllItems()
        let etat = NSMenuItem(title: "O.V.E.R. · " + moteur.libelleEtat, action: nil, keyEquivalent: "")
        etat.isEnabled = false
        menu.addItem(etat)
        menu.addItem(.separator())
        menu.addItem(element("Ouvrir O.V.E.R.", #selector(ouvrirFenetre)))
        if moteur.parametres.raccourciParler != "aucun" && ControleurParole.partage.etatRaccourci.isEmpty {
            let astuce = NSMenuItem(title: "Parler : maintiens " + RaccourciClavier.libelle(moteur.parametres.raccourciParler),
                                    action: nil, keyEquivalent: "")
            astuce.isEnabled = false
            menu.addItem(astuce)
        }
        if moteur.parle {
            menu.addItem(element("Tais-toi", #selector(taire)))
        }
        menu.addItem(element("Paramètres…", #selector(ouvrirParametres)))
        menu.addItem(.separator())
        menu.addItem(element("Quitter O.V.E.R.", #selector(quitter)))
    }

    private func element(_ titre: String, _ action: Selector) -> NSMenuItem {
        let article = NSMenuItem(title: titre, action: action, keyEquivalent: "")
        article.target = self
        return article
    }

    @objc private func ouvrirFenetre() {
        afficherFenetre()
    }

    @objc private func ouvrirParametres() {
        moteur.afficherParametres = true
        afficherFenetre()
    }

    @objc private func taire() {
        moteur.taire()
    }

    @objc private func quitter() {
        NSApp.terminate(nil)
    }

    /// Remet la fenêtre devant ; si elle a été fermée, en fait rouvrir une.
    func afficherFenetre() {
        NSApp.setActivationPolicy(.regular)
        NSApp.unhide(nil)
        NSApp.activate(ignoringOtherApps: true)
        if let fenetre = fenetrePrincipale {
            if fenetre.isMiniaturized { fenetre.deminiaturize(nil) }
            fenetre.makeKeyAndOrderFront(nil)
            return
        }
        // Plus de fenêtre : on « rouvre » l'application, comme un clic sur son icône du Dock ;
        // SwiftUI recrée alors la fenêtre.
        let configuration = NSWorkspace.OpenConfiguration()
        configuration.activates = true
        NSWorkspace.shared.openApplication(at: Bundle.main.bundleURL, configuration: configuration,
                                           completionHandler: nil)
        // Solution de secours si macOS n'a pas transmis la demande : nouvelle fenêtre directement.
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { [weak self] in
            guard let self = self, self.fenetrePrincipale == nil else { return }
            NSApp.sendAction(#selector(NSResponder.newWindowForTab(_:)), to: nil, from: nil)
        }
    }
}
