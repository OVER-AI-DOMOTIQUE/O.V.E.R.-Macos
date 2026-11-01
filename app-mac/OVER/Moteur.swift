// Moteur.swift — lance le moteur Python (serveur.py) et dialogue avec lui.
//
// Protocole : un message JSON par ligne, dans les deux sens (voir serveur.py).
// Le bouton Arrêter envoie le signal SIGINT au moteur, qui ne l'écoute que
// pendant une lecture, une écriture ou une recherche.
//
// Deux façons de trouver le moteur :
//  - application emballée (bash app-mac/emballer.sh, puis publier_app.sh pour l'installeur DMG, ou une
//    archive Xcode pour l'App Store) : Python et les scripts sont dans OVER.app/Contents/Resources ; tes
//    données (cerveau, paramètres, conversation, modèle) dans ~/Library/Application Support/O.V.E.R.,
//    lisible par toi seul (version App Store, dans son bac à sable : ~/Library/Containers/fr.iaover.over).
//    Rien à installer à la main : au premier lancement, l'application télécharge le modèle (ou reprend
//    un fichier que tu as déjà) ;
//  - sinon (Debug, sans bac à sable), le dossier O.V.E.R. installé avec installer.sh.
//
// Le moteur ne pilote pas le Mac lui-même : il demande à l'application (message « systeme »,
// voir Systeme.swift), qui agit avec les outils d'Apple et lui répond.

import AppKit
import Foundation
import UniformTypeIdentifiers

final class Moteur: ObservableObject {
    /// Un seul moteur pour toute l'application : la fenêtre, la barre des menus et le délégué le partagent
    /// (il vit même quand la fenêtre est fermée, en arrière-plan).
    static let partage = Moteur()

    @Published private(set) var messages: [Message] = []
    @Published private(set) var etat: EtatMoteur = Moteur.emballee ? .chargement : .sansDossier
    @Published private(set) var nomModele = ""
    @Published private(set) var version = ""
    @Published private(set) var rechercheWeb = false
    @Published private(set) var actionsMac = false
    @Published private(set) var preparation = false   // le modèle lit sa consigne en arrière-plan
    @Published private(set) var parle = false         // O.V.E.R. lit sa réponse à voix haute
    @Published private(set) var parametres = ParametresOVER()   // panneau Paramètres
    @Published var afficherParametres = false                  // le panneau Paramètres remplace la discussion
    @Published private(set) var fiche = FicheCerveau()          // panneau Paramètres ▸ Cerveau
    @Published private(set) var problemeConnexion = ""         // ouverture avec la session refusée par macOS
    @Published private(set) var memoireRechargee = 0           // tokens retrouvés sur le disque au lancement
    @Published private(set) var dossier: URL?
    @Published private(set) var problemeDossier = ""
    @Published private(set) var installation = InstallationModele()   // premier lancement : le modèle manque
    @Published private(set) var defilement = 0          // augmente à chaque changement : fait défiler vers le bas

    private var processus: Process?
    private var entree: FileHandle?
    private var tampon = Data()
    private var journalErreurs = ""
    private var numeroLancement = 0
    private var reponseOuverte: Int?                     // message O.V.E.R. en train de s'écrire
    private var rechercheOuverte: Int?                   // ligne « Recherche sur le web… » en cours
    private var requeteEnCours = ""
    private let cleDossier = "dossierOVER"
    private var mesureVitesse: (date: Date, fait: Double)?

    init() {
        if Moteur.emballee {
            dossier = Moteur.dossierDonnees
        } else {
            dossier = Moteur.dossiersCandidats(enregistre: UserDefaults.standard.string(forKey: cleDossier))
                .first(where: Moteur.estValide)
        }
        if let dossier = dossier {
            parametres.lireFichier(Moteur.lireParametresLocaux(dans: dossier))
        }
    }

    // MARK: - Application emballée

    /// Python et les scripts d'O.V.E.R. sont-ils dans l'application ? (Contents/Resources/python et over)
    static let emballee: Bool = {
        guard let ressources = Bundle.main.resourceURL else { return false }
        let fichiers = FileManager.default
        return fichiers.isExecutableFile(atPath: ressources.appendingPathComponent("python/bin/python3").path)
            && fichiers.fileExists(atPath: ressources.appendingPathComponent("over/serveur.py").path)
    }()

    /// Tes données, version emballée : Application Support/O.V.E.R. (version App Store : dans son conteneur)
    static let dossierDonnees: URL = {
        let fichiers = FileManager.default
        let base = fichiers.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? fichiers.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support")
        return base.appendingPathComponent("O.V.E.R.", isDirectory: true)
    }()

    /// Crée le dossier des données, lisible et modifiable par toi seul (droits 700).
    static func preparerDossierDonnees() throws {
        let fichiers = FileManager.default
        try fichiers.createDirectory(at: dossierDonnees, withIntermediateDirectories: true,
                                     attributes: [.posixPermissions: 0o700])
        try fichiers.setAttributes([.posixPermissions: 0o700], ofItemAtPath: dossierDonnees.path)
    }

    /// parametres.json lu directement (le moteur Python le confirmera dès qu'il sera prêt).
    static func lireParametresLocaux(dans dossier: URL) -> [String: Any] {
        guard let donnees = try? Data(contentsOf: dossier.appendingPathComponent("parametres.json")),
              let objet = try? JSONSerialization.jsonObject(with: donnees),
              let valeurs = objet as? [String: Any] else { return [:] }
        return valeurs
    }

    // MARK: - États utiles aux vues

    var peutEnvoyer: Bool { etat == .pret }
    var peutEcrire: Bool { etat == .pret || etat == .question }

    var libelleEtat: String {
        switch etat {
        case .sansDossier: return "Dossier à choisir"
        case .installationModele: return installation.enCours ? "Installation du modèle…" : "Modèle à installer"
        case .chargement: return "Chargement du modèle…"
        case .pret: return preparation ? "Lit ses consignes…" : "Prêt"
        case .occupe: return requeteEnCours.isEmpty ? "Réfléchit…" : "Cherche sur le web…"
        case .question: return "Attend ta réponse"
        case .arrete: return "Moteur arrêté"
        }
    }

    // MARK: - Dossier O.V.E.R.

    /// Où chercher les scripts d'O.V.E.R., dans l'ordre :
    /// 1. le dossier qui contient l'application (OVER.app rangée à côté des scripts) ;
    /// 2. le dossier choisi à la main la dernière fois ;
    /// 3. en Debug seulement, le dossier du code source au moment de la compilation (lancement depuis
    ///    Xcode : l'application est alors dans un dossier de compilation, loin des scripts).
    static func dossiersCandidats(enregistre: String?) -> [URL] {
        var candidats = [Bundle.main.bundleURL.deletingLastPathComponent()]
        if let chemin = enregistre {
            candidats.append(URL(fileURLWithPath: chemin, isDirectory: true))
        }
        #if DEBUG
        // seulement pour les essais depuis Xcode : une version à distribuer ne garde pas le chemin du code source
        let dossierDuCode = URL(fileURLWithPath: #filePath)   // …/app-mac/OVER/Moteur.swift
            .deletingLastPathComponent()                      // …/app-mac/OVER
            .deletingLastPathComponent()                      // …/app-mac
            .deletingLastPathComponent()                      // le dossier d'O.V.E.R.
        candidats.append(dossierDuCode)
        #endif
        return candidats
    }

    /// Le dossier doit contenir serveur.py et l'environnement Python installé par installer.sh.
    static func estValide(_ url: URL) -> Bool {
        let fichiers = FileManager.default
        return fichiers.isExecutableFile(atPath: url.appendingPathComponent(".venv/bin/python").path)
            && fichiers.fileExists(atPath: url.appendingPathComponent("serveur.py").path)
    }

    func choisirDossier() {
        guard !Moteur.emballee else { return }   // version emballée : les scripts sont dans l'application
        let panneau = NSOpenPanel()
        panneau.canChooseDirectories = true
        panneau.canChooseFiles = false
        panneau.allowsMultipleSelection = false
        panneau.prompt = "Choisir"
        panneau.message = "Choisis le dossier d'O.V.E.R. : celui qui contient serveur.py, .venv et modeles."
        panneau.directoryURL = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Downloads")
        guard panneau.runModal() == .OK, let url = panneau.url else { return }
        guard Moteur.estValide(url) else {
            problemeDossier = "« \(url.lastPathComponent) » ne contient pas O.V.E.R. installé "
                + "(serveur.py ou .venv/bin/python introuvable). Lance d'abord « bash installer.sh » dans ce dossier."
            return
        }
        problemeDossier = ""
        UserDefaults.standard.set(url.path, forKey: cleDossier)
        dossier = url
        redemarrer()
    }

    // MARK: - Cycle de vie du moteur

    func demarrerSiPossible() {
        guard processus == nil else { return }
        if Moteur.emballee {
            do {
                try Moteur.preparerDossierDonnees()
            } catch {
                etat = .arrete("Impossible de créer le dossier de tes données (\(Moteur.dossierDonnees.path)) : "
                               + error.localizedDescription)
                return
            }
            dossier = Moteur.dossierDonnees
            demarrer(dans: Moteur.dossierDonnees)
            return
        }
        if dossier == nil || !Moteur.estValide(dossier!) {
            dossier = Moteur.dossiersCandidats(enregistre: UserDefaults.standard.string(forKey: cleDossier))
                .first(where: Moteur.estValide)
        }
        guard let dossier = dossier else {
            etat = .sansDossier
            return
        }
        demarrer(dans: dossier)
    }

    func redemarrer() {
        arreterProcessus()
        messages.removeAll()
        reponseOuverte = nil
        rechercheOuverte = nil
        requeteEnCours = ""
        demarrerSiPossible()
    }

    private func demarrer(dans dossier: URL) {
        numeroLancement += 1
        let lancement = numeroLancement
        journalErreurs = ""
        tampon = Data()

        let nouveau = Process()
        var environnement = ProcessInfo.processInfo.environment
        if Moteur.emballee, let ressources = Bundle.main.resourceURL {
            // Python de l'application, isolé : ni variables PYTHON… ni modules de l'utilisateur (-I),
            // aucun fichier écrit dans l'application (-B : elle resterait signée), sorties en UTF-8
            environnement = environnement.filter { !$0.key.hasPrefix("PYTHON") && !$0.key.hasPrefix("OVER_") }
            nouveau.executableURL = ressources.appendingPathComponent("python/bin/python3")
            nouveau.arguments = ["-I", "-B", "-u", "-X", "utf8", ressources.appendingPathComponent("over/serveur.py").path]
            environnement["PYTHONDONTWRITEBYTECODE"] = "1"   // aussi pour les programmes Python lancés ensuite
            environnement["PYTHONNOUSERSITE"] = "1"
            environnement["PYTHONUTF8"] = "1"
            environnement["OVER_EMBALLE"] = "1"
            environnement["OVER_DONNEES"] = dossier.path
            environnement["OVER_APPLICATION"] = Bundle.main.bundlePath
            let whisper = ressources.appendingPathComponent("bin/whisper-cli")
            if FileManager.default.isExecutableFile(atPath: whisper.path) {
                environnement["OVER_WHISPER_CLI"] = whisper.path
            }
        } else {
            nouveau.executableURL = dossier.appendingPathComponent(".venv/bin/python")
            nouveau.arguments = ["-u", dossier.appendingPathComponent("serveur.py").path]
            UserDefaults.standard.set(dossier.path, forKey: cleDossier)
        }
        nouveau.currentDirectoryURL = dossier
        environnement["PYTHONIOENCODING"] = "utf-8"
        environnement["PYTHONUNBUFFERED"] = "1"
        nouveau.environment = environnement

        let tuyauEntree = Pipe()
        let tuyauSortie = Pipe()
        let tuyauErreurs = Pipe()
        nouveau.standardInput = tuyauEntree
        nouveau.standardOutput = tuyauSortie
        nouveau.standardError = tuyauErreurs

        tuyauSortie.fileHandleForReading.readabilityHandler = { [weak self] lecteur in
            let donnees = lecteur.availableData
            if donnees.isEmpty { lecteur.readabilityHandler = nil; return }
            DispatchQueue.main.async {
                guard let self = self, self.numeroLancement == lancement else { return }
                self.recevoir(donnees)
            }
        }
        tuyauErreurs.fileHandleForReading.readabilityHandler = { [weak self] lecteur in
            let donnees = lecteur.availableData
            if donnees.isEmpty { lecteur.readabilityHandler = nil; return }
            DispatchQueue.main.async {
                guard let self = self, self.numeroLancement == lancement else { return }
                self.journalErreurs += String(decoding: donnees, as: UTF8.self)
                if self.journalErreurs.count > 6000 { self.journalErreurs = String(self.journalErreurs.suffix(4000)) }
            }
        }
        nouveau.terminationHandler = { [weak self] termine in
            let code = termine.terminationStatus
            DispatchQueue.main.async {
                guard let self = self, self.numeroLancement == lancement else { return }
                self.processusTermine(code: code)
            }
        }

        do {
            try nouveau.run()
        } catch {
            etat = .arrete("Impossible de lancer le moteur : \(error.localizedDescription)")
            return
        }
        processus = nouveau
        entree = tuyauEntree.fileHandleForWriting
        etat = .chargement
    }

    /// Ferme le moteur (fin de l'application ou redémarrage).
    func arreterProcessus() {
        numeroLancement += 1   // les derniers messages de l'ancien moteur seront ignorés
        if let encours = processus, encours.isRunning {
            ecrire(["type": "quitter"])
            encours.terminate()
        }
        processus = nil
        entree = nil
    }

    private func processusTermine(code: Int32) {
        processus = nil
        entree = nil
        parle = false
        preparation = false
        if case .arrete = etat { return }
        let fin = journalErreurs.split(separator: "\n").suffix(6).joined(separator: "\n")
        etat = .arrete(code == 0 ? "Le moteur s'est arrêté." : "Le moteur s'est arrêté (code \(code)).\n\(fin)")
    }

    // MARK: - Envoyer

    private func ecrire(_ objet: [String: Any]) {
        guard let entree = entree,
              var donnees = try? JSONSerialization.data(withJSONObject: objet) else { return }
        donnees.append(0x0A)
        do {
            try entree.write(contentsOf: donnees)
        } catch {
            etat = .arrete("Le moteur ne répond plus : \(error.localizedDescription)")
        }
    }

    func envoyerMessage(_ texte: String) {
        guard etat == .pret else { return }
        ajouter(Message(auteur: .utilisateur, texte: texte))
        ecrire(["type": "message", "texte": texte])
        etat = .occupe
    }

    /// Commande lancée depuis un menu (/cerveau, /aide…) : pas de bulle « Toi ».
    /// Sa réponse s'affiche dans la discussion : le panneau Paramètres se referme.
    func envoyerCommande(_ commande: String) {
        guard etat == .pret else { return }
        afficherParametres = false
        ecrire(["type": "message", "texte": commande])
        etat = .occupe
    }

    func effacerConversation() {
        guard etat == .pret else { return }
        messages.removeAll()
        envoyerCommande("/effacer")
    }

    /// Lance une commande du panneau Paramètres qui se déroule dans la discussion (questions…).
    func commandeDepuisParametres(_ commande: String) {
        guard etat == .pret else { return }
        afficherParametres = false
        envoyerCommande(commande)
    }

    // MARK: - Paramètres, cerveau et voix (hors conversation)
    // Ces messages passent même quand O.V.E.R. réfléchit : le moteur les lit dès qu'il est libre.

    var moteurEnMarche: Bool { processus?.isRunning == true }

    func changerReglage(_ cle: String, _ valeur: Any) {
        parametres.appliquer(cle, valeur)   // affiché tout de suite, confirmé par le moteur ensuite
        ecrire(["type": "reglage", "cle": cle, "valeur": valeur])
    }

    func lireReglages() {
        ecrire(["type": "lire_parametres"])
        ecrire(["type": "lire_cerveau"])
    }

    /// action : "definir" (champ + texte, vide = oublier), "ajouter", "modifier_souvenir", "oublier_souvenir" (numéro dès 1)
    func modifierCerveau(_ action: String, champ: String? = nil, texte: String? = nil, numero: Int? = nil) {
        var commande: [String: Any] = ["type": "cerveau", "action": action]
        if let champ = champ { commande["champ"] = champ }
        if let texte = texte { commande["texte"] = texte }
        if let numero = numero { commande["numero"] = numero }
        ecrire(commande)
    }

    func taire() {
        ecrire(["type": "taire"])
    }

    func essayerVoix() {
        ecrire(["type": "voix_essai"])
    }

    /// Fait transcrire un enregistrement du micro (WAV 16 kHz) par whisper.cpp, sur le Mac.
    /// apercu : texte provisoire pendant qu'on parle encore. ident : rendu tel quel avec le résultat.
    func transcrire(_ fichier: URL, apercu: Bool, ident: Int) {
        guard moteurEnMarche else {
            // pas de moteur : on répond tout de suite, sinon l'écoute attendrait pour rien
            Ecoute.partage.recevoirTranscription(Transcription([
                "erreur": "Le moteur est arrêté.", "apercu": apercu, "id": ident, "fichier": fichier.path]))
            return
        }
        ecrire(["type": "transcrire", "fichier": fichier.path, "apercu": apercu, "id": ident])
    }

    /// Télécharge le modèle d'écoute choisi (et, sans emballage, compile whisper.cpp), en arrière-plan côté Python.
    func installerEcoute() {
        ecrire(["type": "ecoute_installer"])
    }

    /// « Plus de voix… » : les voix de macOS se téléchargent dans Réglages Système ▸ Accessibilité ▸ Contenu énoncé.
    func ouvrirReglagesVoix() {
        if let adresse = URL(string: "x-apple.systempreferences:com.apple.preference.universalaccess?SpeakableItems") {
            _ = NSWorkspace.shared.open(adresse)
        }
    }

    /// Ouverture avec la session : l'application s'inscrit elle-même auprès de macOS (éléments de connexion).
    func appliquerConnexion(_ actif: Bool) {
        let probleme = Connexion.appliquer(actif)
        if actif {
            problemeConnexion = probleme ?? ""
            if probleme != nil { changerReglage("demarrage_session", false) }   // le réglage suit la réalité
        } else if let probleme = probleme {
            problemeConnexion = probleme
        }   // sinon : l'explication d'un refus précédent reste affichée
    }

    /// Commande tapée dans le champ : « /effacer » vide aussi l'écran.
    func envoyerSaisie(_ texte: String) {
        if texte.lowercased() == "/effacer" {
            effacerConversation()
        } else {
            envoyerMessage(texte)
        }
    }

    /// Réponse à une question (bouton ou texte). valeur nil = arrêter les questions.
    func repondre(valeur: String?, libelle: String) {
        guard etat == .question else { return }
        if let indice = messages.lastIndex(where: { $0.genre == .question && !$0.repondue }) {
            messages[indice].repondue = true
        }
        ajouter(Message(auteur: .utilisateur, texte: libelle))
        let texte: Any
        if let valeur = valeur { texte = valeur } else { texte = NSNull() }
        ecrire(["type": "reponse", "texte": texte])
        etat = .occupe
    }

    // MARK: - Premier lancement : le modèle de langage

    func telechargerModele() {
        guard etat == .installationModele, !installation.enCours else { return }
        installation.erreur = ""
        installation.enCours = true
        installation.phase = "telechargement"
        installation.fait = installation.recu
        installation.total = installation.taille
        mesureVitesse = nil
        ecrire(["type": "modele_telecharger"])
    }

    /// Reprend un fichier du modèle déjà téléchargé (vérifié par le moteur avant d'être gardé).
    func importerModele(_ chemin: String) {
        guard etat == .installationModele else { return }
        installation.erreur = ""
        installation.enCours = true
        installation.phase = "verification"
        installation.fait = 0
        installation.total = installation.taille
        ecrire(["type": "modele_importer", "chemin": chemin])
    }

    /// « J'ai déjà le fichier… ». Dans le bac à sable, le moteur ne lit que tes données : l'application
    /// (qui a le droit de lire le fichier que tu choisis) le copie d'abord dans import/ ; le moteur le vérifie
    /// puis le range. Sur le même disque, la copie est instantanée (APFS) et ne prend pas de place en plus.
    func choisirFichierModele() {
        guard etat == .installationModele, !installation.enCours, let dossier = dossier else { return }
        let panneau = NSOpenPanel()
        panneau.canChooseFiles = true
        panneau.canChooseDirectories = false
        panneau.allowsMultipleSelection = false
        if let typeModele = UTType(filenameExtension: "gguf") {
            panneau.allowedContentTypes = [typeModele]
        }
        panneau.prompt = "Utiliser"
        panneau.message = "Choisis le fichier Ministral-3-3B-Instruct-2512-Q4_K_M.gguf (2,1 Go) déjà téléchargé : "
            + "il sera vérifié avant d'être gardé."
        guard panneau.runModal() == .OK, let source = panneau.url else { return }
        guard Moteur.emballee else {
            importerModele(source.path)   // sans bac à sable : le moteur lit le fichier lui-même
            return
        }
        let dossierImport = dossier.appendingPathComponent("import", isDirectory: true)
        let copie = dossierImport.appendingPathComponent(source.lastPathComponent)
        installation.erreur = ""
        installation.enCours = true
        installation.phase = "copie"
        installation.fait = 0
        installation.total = installation.taille
        DispatchQueue.global(qos: .userInitiated).async {
            let fichiers = FileManager.default
            var probleme = ""
            do {
                try fichiers.createDirectory(at: dossierImport, withIntermediateDirectories: true,
                                             attributes: [.posixPermissions: 0o700])
                try? fichiers.removeItem(at: copie)
                try fichiers.copyItem(at: source, to: copie)
            } catch {
                try? fichiers.removeItem(at: copie)
                probleme = "Copie impossible : \(error.localizedDescription)"
            }
            DispatchQueue.main.async {
                guard self.etat == .installationModele else { return }
                if probleme.isEmpty {
                    self.installation.enCours = false
                    self.importerModele(copie.path)
                } else {
                    self.installation.enCours = false
                    self.installation.erreur = probleme
                }
            }
        }
    }

    /// Bouton Pause : le téléchargement s'arrête, il reprendra là où il s'est arrêté.
    func pauseInstallation() {
        guard etat == .installationModele, installation.enCours, let encours = processus, encours.isRunning else { return }
        encours.interrupt()   // SIGINT
    }

    private func suivreProgression(_ e: [String: Any]) {
        let phase = e["phase"] as? String ?? ""
        let fait = nombreJSON(e["fait"])
        if phase != installation.phase { mesureVitesse = nil; installation.vitesse = 0 }
        installation.enCours = true
        installation.phase = phase
        installation.fait = fait
        installation.total = nombreJSON(e["total"])
        // vitesse moyenne sur les dernières secondes (pour estimer le temps restant)
        let maintenant = Date()
        if let mesure = mesureVitesse {
            let duree = maintenant.timeIntervalSince(mesure.date)
            if duree >= 3 {
                let vitesse = max(0, fait - mesure.fait) / duree
                installation.vitesse = installation.vitesse == 0 ? vitesse : installation.vitesse * 0.6 + vitesse * 0.4
                mesureVitesse = (maintenant, fait)
            }
        } else {
            mesureVitesse = (maintenant, fait)
        }
    }

    /// Bouton Arrêter : interrompt la lecture, l'écriture ou la recherche en cours.
    func arreter() {
        guard etat == .occupe, let encours = processus, encours.isRunning else { return }
        encours.interrupt()   // SIGINT
    }

    // MARK: - Recevoir

    private func recevoir(_ donnees: Data) {
        tampon.append(donnees)
        while let finLigne = tampon.firstIndex(of: 0x0A) {
            let ligne = tampon.subdata(in: tampon.startIndex..<finLigne)
            tampon.removeSubrange(tampon.startIndex...finLigne)
            guard !ligne.isEmpty,
                  let objet = try? JSONSerialization.jsonObject(with: ligne),
                  let evenement = objet as? [String: Any],
                  let type = evenement["type"] as? String else { continue }
            traiter(type, evenement)
        }
    }

    private func ajouter(_ message: Message) {
        messages.append(message)
        defilement += 1
    }

    private func traiter(_ type: String, _ e: [String: Any]) {
        let texte = e["texte"] as? String ?? ""
        switch type {
        case "chargement":
            etat = .chargement
            version = e["version"] as? String ?? ""

        case "modele_absent":
            installation.lireEtat(e)
            etat = .installationModele

        case "modele_progression":
            suivreProgression(e)

        case "modele_pret":
            installation.enCours = false
            etat = .chargement

        case "pret":
            nomModele = e["nom_modele"] as? String ?? ""
            rechercheWeb = e["recherche_web"] as? Bool ?? false
            actionsMac = e["actions"] as? Bool ?? false
            memoireRechargee = e["memoire_rechargee"] as? Int ?? 0
            etat = .occupe   // l'accueil (questions ou bonjour) arrive

        case "attente":
            fermerReponse()
            requeteEnCours = ""
            etat = .pret

        case "dire":
            fermerReponse()
            ajouter(Message(auteur: .over, texte: texte))

        case "info":
            ajouter(Message(auteur: .systeme, genre: .info, texte: texte))

        case "erreur":
            ajouter(Message(auteur: .systeme, genre: .erreur, texte: texte))

        case "erreur_fatale":
            etat = .arrete(texte)

        case "question":
            fermerReponse()
            let choix = (e["choix"] as? [[String: Any]] ?? []).map {
                Choix(libelle: $0["libelle"] as? String ?? "", valeur: $0["valeur"] as? String)
            }
            ajouter(Message(auteur: .over, genre: .question, texte: texte,
                            extrait: e["extrait"] as? String ?? "", aide: e["aide"] as? String ?? "",
                            valeurActuelle: e["valeur"] as? String ?? "", choix: choix))
            etat = .question

        case "texte":
            let morceau = e["morceau"] as? String ?? ""
            if let indice = reponseOuverte {
                messages[indice].texte += morceau
            } else {
                ajouter(Message(auteur: .over, texte: morceau, enCours: true))
                reponseOuverte = messages.count - 1
            }
            defilement += 1

        case "fin_passage":
            fermerReponse()

        case "fin_reponse":
            let sources = e["sources"] as? [String] ?? []
            let stats = texteStatistiques(e["statistiques"] as? [String: Any] ?? [:])
            if let indice = messages.lastIndex(where: { $0.auteur == .over && $0.genre == .texte }) {
                messages[indice].sources = sources
                messages[indice].statistiques = stats
            } else {
                ajouter(Message(auteur: .systeme, genre: .info, texte: stats))
            }
            requeteEnCours = ""
            defilement += 1

        case "recherche_debut":
            requeteEnCours = e["requete"] as? String ?? ""
            ajouter(Message(auteur: .systeme, genre: .recherche,
                            texte: "Recherche sur le web : « \(requeteEnCours) »…", enCours: true))
            rechercheOuverte = messages.count - 1

        case "recherche_fin":
            guard let indice = rechercheOuverte else { break }
            let etatRecherche = e["etat"] as? String ?? ""
            let duree = nombreFr(e["duree"] as? Double ?? 0)
            let moteurWeb = e["moteur"] as? String ?? ""
            switch etatRecherche {
            case "annulee":
                messages[indice].texte = "Recherche « \(requeteEnCours) » annulée."
            case "trouve":
                let nombre = e["nb_resultats"] as? Int ?? 0
                let page = e["page_lue"] as? Bool ?? false
                var detail = nombre > 0 ? "\(nombre) résultat(s)" : "page lue"
                if nombre > 0 && page { detail += " + 1 page lue" }
                messages[indice].texte = "Recherche « \(requeteEnCours) » : \(detail) (\(moteurWeb), \(duree) s)"
            default:
                let erreurs = (e["erreurs"] as? [String] ?? []).joined(separator: " ; ")
                messages[indice].texte = "Recherche « \(requeteEnCours) » : rien trouvé. \(erreurs)"
                messages[indice].genre = .erreur
            }
            messages[indice].enCours = false
            rechercheOuverte = nil

        case "cerveau":
            ajouter(Message(auteur: .over, texte: Moteur.texteCerveau(e)))

        case "aide":
            let lignes = (e["lignes"] as? [[String: Any]] ?? []).map {
                "- `\($0["commande"] as? String ?? "")` : \($0["explication"] as? String ?? "")"
            }
            ajouter(Message(auteur: .over, texte: "**Commandes**\n" + lignes.joined(separator: "\n")
                            + "\n- Bouton **Arrêter** (⌘.) : arrête une réponse ou une recherche"))

        case "preparation":
            preparation = e["en_cours"] as? Bool ?? false

        case "parole":
            parle = e["en_cours"] as? Bool ?? false

        case "parametres":
            parametres.lire(e)
            rechercheWeb = parametres.rechercheWeb   // l'en-tête suit les paramètres changés en route
            actionsMac = parametres.actionsMac

        case "fiche_cerveau":
            fiche = FicheCerveau(e)

        case "transcription":
            Ecoute.partage.recevoirTranscription(Transcription(e))

        case "historique":
            // conversation reprise du lancement précédent
            for element in e["messages"] as? [[String: Any]] ?? [] {
                let auteur: Message.Auteur = (element["auteur"] as? String) == "utilisateur" ? .utilisateur : .over
                ajouter(Message(auteur: auteur, texte: element["texte"] as? String ?? ""))
            }

        case "alerte":
            ajouter(Message(auteur: .over, texte: "⏰ " + texte))
            if !NSApplication.shared.isActive {
                _ = NSApplication.shared.requestUserAttention(.informationalRequest)   // fait sauter l'icône du Dock
            }

        case "systeme":
            // une action sur le Mac demandée par le moteur (Systeme.swift) ; réponse s'il l'attend
            let lancement = numeroLancement
            let numero = e["id"] ?? NSNull()
            let attendre = e["attendre"] as? Bool ?? false
            Systeme.executer(e["action"] as? String ?? "", e["parametres"] as? [String: Any] ?? [:]) { [weak self] resultat in
                guard attendre, let self = self, self.numeroLancement == lancement else { return }
                self.ecrire(["type": "systeme_resultat", "id": numero, "ok": resultat.ok,
                             "detail": resultat.detail, "donnees": resultat.donnees])
            }

        case "fin":
            fermerReponse()

        default:
            break
        }
    }

    private func fermerReponse() {
        if let indice = reponseOuverte, indice < messages.count {
            messages[indice].enCours = false
        }
        reponseOuverte = nil
    }

    /// Le contenu du cerveau, mis en forme pour une bulle.
    static func texteCerveau(_ e: [String: Any]) -> String {
        if e["vide"] as? Bool == true {
            return "Mon cerveau est vide. Tape /cerveau questions pour le remplir."
        }
        var lignes = ["**Ce que je sais de toi**"]
        for paire in e["lignes"] as? [[Any]] ?? [] where paire.count == 2 {
            lignes.append("- \(paire[0]) : \(paire[1])")
        }
        let souvenirs = e["souvenirs"] as? [String] ?? []
        if !souvenirs.isEmpty {
            lignes.append("\n**Souvenirs**")
            for (numero, souvenir) in souvenirs.enumerated() {
                lignes.append("\(numero + 1). \(souvenir)")
            }
        }
        let taille = e["taille"] as? Int ?? 0
        let limite = e["limite"] as? Int ?? 0
        lignes.append("\n_\(taille) tokens sur \(limite) possibles_")
        lignes.append("_Changer : /cerveau ajoute …  ·  /cerveau modifie …  ·  /cerveau oublie n  ·  /cerveau aide_")
        return lignes.joined(separator: "\n")
    }
}
