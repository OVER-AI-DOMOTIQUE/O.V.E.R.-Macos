// VueReglages.swift — le panneau Paramètres (roue dentée en haut à droite, ou ⌘,).
//
// Cinq onglets : Général (briefing), Voix (parler, écouter), Cerveau (ce qu'O.V.E.R. sait
// de toi), Démarrage (arrière-plan, ouverture avec la session), Système (web, actions,
// mémoire, minuteurs, créativité…). Chaque changement part
// tout de suite au moteur Python, qui l'enregistre (parametres.json, cerveau.json) : rien
// n'est gardé dans l'application elle-même. Chaque paramètre existe aussi en commande
// (/paramètres nom valeur) : la commande est rappelée en bas de chaque onglet.

import SwiftUI

struct VueParametres: View {
    @EnvironmentObject private var moteur: Moteur

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                Image(systemName: "gearshape.fill")
                    .foregroundColor(Palette.sarcelle)
                Text("Paramètres")
                    .font(.title3.bold())
                Spacer()
                Button {
                    moteur.afficherParametres = false
                } label: {
                    Label("Retour à la discussion", systemImage: "bubble.left.and.bubble.right")
                }
                .buttonStyle(.borderedProminent)
                .tint(Palette.sarcelle)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            Divider()
            if !moteur.moteurEnMarche {
                Text("Le moteur est arrêté : les changements seront pris en compte à son redémarrage.")
                    .font(.caption)
                    .foregroundColor(Palette.rouge)
                    .padding(6)
            }
            TabView {
                OngletGeneral()
                    .tabItem { Label("Général", systemImage: "sun.max") }
                OngletVoix()
                    .tabItem { Label("Voix", systemImage: "waveform") }
                OngletCerveau()
                    .tabItem { Label("Cerveau", systemImage: "brain") }
                OngletDemarrage()
                    .tabItem { Label("Démarrage", systemImage: "power") }
                OngletSysteme()
                    .tabItem { Label("Système", systemImage: "cpu") }
            }
            .padding(16)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(nsColor: .windowBackgroundColor))
        .onAppear { moteur.lireReglages() }
    }
}

/// Raccourci : une liaison qui envoie la nouvelle valeur au moteur.
private func liaison<T>(_ moteur: Moteur, _ cle: String, _ lire: @escaping (ParametresOVER) -> T) -> Binding<T> {
    Binding(get: { lire(moteur.parametres) }, set: { moteur.changerReglage(cle, $0) })
}

/// Les commandes équivalentes, en bas de chaque onglet (« on a les deux »).
struct RappelCommandes: View {
    let commandes: [String]

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Divider()
            Text("En commande, dans la discussion :")
                .font(.caption.bold())
                .foregroundColor(.secondary)
            Text(commandes.joined(separator: "   ·   "))
                .font(.system(.caption, design: .monospaced))
                .foregroundColor(.secondary)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.top, 6)
    }
}

/// Petit texte d'explication sous un réglage.
struct Explication: View {
    let texte: String

    init(_ texte: String) {
        self.texte = texte
    }

    var body: some View {
        Text(texte)
            .font(.caption)
            .foregroundColor(.secondary)
            .fixedSize(horizontal: false, vertical: true)
    }
}

// MARK: - Général : le briefing

struct OngletGeneral: View {
    @EnvironmentObject private var moteur: Moteur
    @State private var ville = ""

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                Picker("Briefing :", selection: liaison(moteur, "briefing") { $0.briefing }) {
                    ForEach(moteur.parametres.choixBriefing) { choix in
                        Text(choix.libelle.prefix(1).uppercased() + String(choix.libelle.dropFirst())).tag(choix.valeur)
                    }
                }
                .pickerStyle(.radioGroup)
                Explication("Le point du jour : date, météo, rendez-vous et rappels. Tu peux aussi le demander "
                            + "à tout moment en écrivant « briefing ».")
                Divider()
                Group {
                    Toggle("Météo du jour", isOn: liaison(moteur, "briefing_meteo") { $0.briefingMeteo })
                    HStack {
                        Text("Ville :")
                        TextField("celle de ton cerveau", text: $ville)
                            .textFieldStyle(.roundedBorder)
                            .frame(maxWidth: 220)
                            .onSubmit { moteur.changerReglage("briefing_ville", ville) }
                        Button("Valider") { moteur.changerReglage("briefing_ville", ville) }
                            .disabled(ville == moteur.parametres.briefingVille)
                    }
                    .disabled(!moteur.parametres.briefingMeteo)
                    Toggle("Rendez-vous du jour (app Calendrier)", isOn: liaison(moteur, "briefing_agenda") { $0.briefingAgenda })
                    Toggle("Rappels du jour et en retard (app Rappels)", isOn: liaison(moteur, "briefing_rappels") { $0.briefingRappels })
                    Explication("La météo vient d'Open-Meteo : seul le nom de la ville sort du Mac. Rendez-vous et "
                                + "rappels sont lus sur ton Mac ; macOS demande une fois l'autorisation. Les rendez-vous "
                                + "répétitifs créés avant aujourd'hui peuvent ne pas apparaître.")
                }
                .disabled(moteur.parametres.briefing == "jamais")
                RappelCommandes(commandes: ["/paramètres briefing quotidien", "/paramètres météo non",
                                            "/paramètres ville Lyon", "/briefing"])
            }
            .padding(4)
        }
        .onAppear { ville = moteur.parametres.briefingVille }
        .onChange(of: moteur.parametres.briefingVille) { ville = $0 }
    }
}

// MARK: - Voix : parler et écouter

struct OngletVoix: View {
    @EnvironmentObject private var moteur: Moteur
    @ObservedObject private var parole = ControleurParole.partage
    @State private var vitesse = 185.0
    /// Dernière ligne du menu des voix : ouvre les Réglages Système au lieu de changer de voix.
    private static let choixPlus = "__plus_de_voix__"

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                sectionParler
                Divider()
                sectionEcouter
                sectionRaccourci
                RappelCommandes(commandes: ["/voix", "/voix off", "/voix liste", "/voix thomas", "/voix test",
                                            "/voix plus",
                                            "/paramètres vitesse 200", "/paramètres départ phrase", "/tais-toi",
                                            "/écoute", "/écoute installer", "/écoute rapide",
                                            "/paramètres raccourci option espace"])
            }
            .padding(4)
        }
        .onAppear { vitesse = moteur.parametres.voixVitesse }
        .onChange(of: moteur.parametres.voixVitesse) { vitesse = $0 }
    }

    private var sectionParler: some View {
        VStack(alignment: .leading, spacing: 12) {
            Toggle("Lire les réponses à voix haute", isOn: liaison(moteur, "voix_active") { $0.voixActive })
            etatInstallation
            Picker("Départ de la voix :", selection: liaison(moteur, "voix_depart") { $0.voixDepart }) {
                Text("Dès les premiers mots (réactif)").tag("immediat")
                Text("À la fin de chaque phrase (intonation plus naturelle)").tag("phrase")
            }
            .pickerStyle(.radioGroup)
            choixDeLaVoix
            reglageVitesse
            Explication("Voix de macOS : gratuites, sur ton Mac, sans Internet. O.V.E.R. ne garde aucun son. "
                        + "Pour le faire taire : Échap, le haut-parleur, « chut » ou un nouveau message.")
        }
    }

    /// Aucune voix française sur ce Mac (rare : Thomas est fourni avec macOS).
    @ViewBuilder private var etatInstallation: some View {
        if moteur.parametres.etatVoix == "indisponible" {
            VStack(alignment: .leading, spacing: 6) {
                Text("Aucune voix française trouvée sur ce Mac. Ajoute « Thomas » (ou une autre voix) dans "
                     + "Réglages Système ▸ Accessibilité ▸ Contenu énoncé ▸ Voix du système.")
                    .font(.callout)
                    .foregroundColor(Palette.rouge)
                    .fixedSize(horizontal: false, vertical: true)
                Button("Ouvrir les Réglages Système") { moteur.ouvrirReglagesVoix() }
            }
        }
    }

    private var choixDeLaVoix: some View {
        VStack(alignment: .leading, spacing: 6) {
            Picker("Voix :", selection: selectionVoix) {
                Text("Voix principale (\(moteur.parametres.voixPrincipale))").tag("")
                ForEach(moteur.parametres.voixDisponibles) { voix in
                    Text(voix.libelle).tag(voix.id)
                }
                Divider()
                Text("Plus de voix…").tag(OngletVoix.choixPlus)
            }
            .frame(maxWidth: 380)
            Button("Plus de voix…") { moteur.ouvrirReglagesVoix() }
            Explication(descriptionVoix)
            if !moteur.parametres.messageVoix.isEmpty {
                Text(moteur.parametres.messageVoix)
                    .font(.caption)
                    .foregroundColor(Palette.sarcelle)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    /// Le menu des voix : une voix choisie part au moteur ; « Plus de voix… » ouvre les Réglages Système.
    private var selectionVoix: Binding<String> {
        Binding(get: { moteur.parametres.voixNom },
                set: { nouvelle in
                    if nouvelle == OngletVoix.choixPlus {
                        DispatchQueue.main.async { moteur.ouvrirReglagesVoix() }   // après la fermeture du menu
                    } else {
                        moteur.changerReglage("voix_nom", nouvelle)
                    }
                })
    }

    private var descriptionVoix: String {
        let p = moteur.parametres
        guard let voix = p.voixDisponibles.first(where: { $0.id == p.voixChoisie }) else {
            return p.voixDisponibles.isEmpty ? "Aucune voix française pour l'instant." : ""
        }
        return "Voix utilisée : \(voix.libelle). D'autres voix françaises, dont des voix « améliorées » plus "
            + "naturelles, se téléchargent gratuitement : Réglages Système ▸ Accessibilité ▸ Contenu énoncé ▸ "
            + "Voix du système ▸ Gérer les voix… (puis relance la liste : /voix liste)."
    }

    private var reglageVitesse: some View {
        HStack {
            Text("Vitesse :")
            Slider(value: $vitesse, in: moteur.parametres.vitesseMin...moteur.parametres.vitesseMax,
                   onEditingChanged: { enCours in
                       if !enCours { moteur.changerReglage("voix_vitesse", Int(vitesse)) }
                   })
                .frame(maxWidth: 220)
            Text("\(Int(vitesse))  (185 = naturel)")
                .font(.caption)
                .monospacedDigit()
            Button("Essayer") { moteur.essayerVoix() }
                .disabled(moteur.parametres.etatVoix != "prete")
        }
    }

    private var sectionEcouter: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Écouter : bouton micro à gauche du champ de saisie (⌘D). Parle, puis fais une petite pause.")
                .fixedSize(horizontal: false, vertical: true)
            Picker("Reconnaissance :", selection: liaison(moteur, "ecoute_modele") { $0.ecouteModele }) {
                Text("Précise (modèle « small », " + tailleModele("precis", defaut: 190) + ")").tag("precis")
                Text("Rapide (modèle « base », " + tailleModele("rapide", defaut: 60) + ", plus d'erreurs)").tag("rapide")
            }
            .pickerStyle(.radioGroup)
            etatEcoute
            Toggle("Envoyer tout seul ce que j'ai dit", isOn: liaison(moteur, "ecoute_envoi_auto") { $0.ecouteEnvoiAuto })
            Explication("Reconnaissance whisper.cpp, faite sur ton Mac : rien ne part chez Apple ni ailleurs. "
                        + "Le texte s'écrit pendant que tu parles, avec un peu de retard (quelques secondes avec "
                        + "le modèle précis), puis le texte définitif arrive juste après ta pause. Le temps de "
                        + "transcription de la dernière phrase s'affiche en survolant le micro.")
        }
    }

    /// Le raccourci à maintenir pour parler, même fenêtre fermée (Parole.swift).
    private var sectionRaccourci: some View {
        VStack(alignment: .leading, spacing: 8) {
            Picker("Raccourci pour parler (à maintenir) :",
                   selection: liaison(moteur, "raccourci_parler") { $0.raccourciParler }) {
                ForEach(RaccourciClavier.choix.map { $0.cle }, id: \.self) { cle in
                    Text(RaccourciClavier.libelle(cle)).tag(cle)
                }
                Text("Aucun").tag("aucun")
            }
            .frame(maxWidth: 420)
            if !parole.etatRaccourci.isEmpty {
                Text(parole.etatRaccourci)
                    .font(.caption)
                    .foregroundColor(Palette.rouge)
                    .fixedSize(horizontal: false, vertical: true)
            } else if !parole.conflitRaccourci.isEmpty {
                Text(parole.conflitRaccourci)
                    .font(.caption)
                    .foregroundColor(Palette.or)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Explication("Garde les touches enfoncées, parle, puis relâche : ça marche même dans une autre "
                        + "application, ou fenêtre fermée si O.V.E.R. reste en arrière-plan (Paramètres ▸ "
                        + "Démarrage). Une petite fenêtre en bas de l'écran montre ce que tu dis, puis la réponse. "
                        + "Avec plusieurs claviers, macOS se sert de ⌃ Espace et ⌃⌥ Espace pour en changer : "
                        + "O.V.E.R. te prévient ici si c'est le cas.")
        }
    }

    private func tailleModele(_ id: String, defaut: Int) -> String {
        let modele = moteur.parametres.modelesEcoute.first(where: { $0.id == id })
        let taille = "\(modele?.tailleMo ?? defaut) Mo"
        return modele?.installe == true ? taille + ", installé" : taille
    }

    /// Installation de whisper.cpp et du modèle : en cours, ratée ou à faire.
    @ViewBuilder private var etatEcoute: some View {
        switch moteur.parametres.etatEcoute {
        case "installation":
            HStack(spacing: 8) {
                ProgressView()
                    .controlSize(.small)
                Text(moteur.parametres.messageEcoute.isEmpty ? "Installation de l'écoute…" : moteur.parametres.messageEcoute)
                    .font(.callout)
            }
        case "erreur":
            VStack(alignment: .leading, spacing: 6) {
                Text("Installation de l'écoute impossible : " + moteur.parametres.erreurEcoute)
                    .font(.callout)
                    .foregroundColor(Palette.rouge)
                    .fixedSize(horizontal: false, vertical: true)
                Button("Réessayer") { moteur.installerEcoute() }
            }
        case "a_installer":
            HStack(spacing: 10) {
                Text("L'écoute n'est pas encore installée : whisper.cpp compilé pour ton Mac (5 à 15 min) et le modèle choisi.")
                    .font(.callout)
                    .fixedSize(horizontal: false, vertical: true)
                Button("Installer l'écoute") { moteur.installerEcoute() }
            }
        default:
            EmptyView()
        }
    }
}

// MARK: - Démarrage : arrière-plan et ouverture avec la session

struct OngletDemarrage: View {
    @EnvironmentObject private var moteur: Moteur

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                sectionArrierePlan
                Divider()
                sectionSession
                RappelCommandes(commandes: ["/paramètres arrière-plan oui", "/paramètres dock oui", "/démarrage",
                                            "/démarrage oui", "/démarrage discret", "/démarrage fenêtre",
                                            "/démarrage non"])
            }
            .padding(4)
        }
    }

    private var sectionArrierePlan: some View {
        VStack(alignment: .leading, spacing: 12) {
            Toggle("Rester ouvert en arrière-plan quand je ferme la fenêtre",
                   isOn: liaison(moteur, "arriere_plan") { $0.arrierePlan })
            Toggle("Cacher l'icône du Dock tant que la fenêtre est fermée",
                   isOn: liaison(moteur, "masquer_dock") { $0.masquerDock })
                .disabled(!moteur.parametres.arrierePlan)
                .padding(.leading, 20)
            Explication("En arrière-plan, O.V.E.R. reste prêt : le modèle reste chargé (réponse immédiate en "
                        + "rouvrant), les minuteurs et la voix continuent. Son icône (le logo) est dans la barre des menus, "
                        + "en haut à droite de l'écran : clic pour rouvrir la fenêtre, voir son état ou le quitter. "
                        + "Pour l'arrêter vraiment : « Quitter O.V.E.R. » dans ce menu, ou ⌘Q. Il garde environ "
                        + "3 Go de mémoire tant qu'il tourne.")
        }
    }

    private var sectionSession: some View {
        VStack(alignment: .leading, spacing: 12) {
            Toggle("Ouvrir O.V.E.R. à l'ouverture de ma session",
                   isOn: liaison(moteur, "demarrage_session") { $0.demarrageSession })
            Toggle("Sans fenêtre (directement dans la barre des menus)",
                   isOn: liaison(moteur, "demarrage_discret") { $0.demarrageDiscret })
                .disabled(!moteur.parametres.demarrageSession)
                .padding(.leading, 20)
            etatConnexion
            Explication("O.V.E.R. s'ajoute aux éléments de connexion de macOS (Réglages Système ▸ Général ▸ "
                        + "Ouverture et extensions) : macOS l'ouvre quand tu te connectes, puis plus rien. Tu peux "
                        + "aussi le retirer là-bas.")
        }
    }

    /// Ce que macOS dit vraiment de l'ouverture avec la session.
    @ViewBuilder private var etatConnexion: some View {
        if !moteur.problemeConnexion.isEmpty {
            Text(moteur.problemeConnexion)
                .font(.caption)
                .foregroundColor(Palette.rouge)
                .fixedSize(horizontal: false, vertical: true)
        } else if moteur.parametres.demarrageSession {
            switch Connexion.etat {
            case "a_approuver":
                HStack {
                    Text("macOS attend ton accord dans Réglages Système ▸ Général ▸ Ouverture et extensions.")
                        .font(.caption)
                        .foregroundColor(Palette.or)
                        .fixedSize(horizontal: false, vertical: true)
                    Button("Ouvrir") { Connexion.ouvrirReglages() }
                }
            case "inactif":
                HStack {
                    Text("Désactivé dans les Réglages Système de macOS.")
                        .font(.caption)
                        .foregroundColor(Palette.or)
                    Button("Réactiver") { moteur.appliquerConnexion(true) }
                }
            default:
                EmptyView()
            }
        }
    }
}

// MARK: - Cerveau : la fiche et les souvenirs

struct OngletCerveau: View {
    @EnvironmentObject private var moteur: Moteur
    @State private var valeurs: [String: String] = [:]   // fiche en cours de modification
    @State private var souvenirs: [String] = []
    @State private var nouveau = ""
    @State private var confirmerVider = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Ce que je sais de toi").font(.headline)
            ForEach(moteur.fiche.champs) { champ in
                HStack {
                    Text(champ.libelle + " :")
                        .frame(width: 130, alignment: .trailing)
                    champEditable(champ)
                }
            }
            HStack {
                Button("Repasser les questions") { moteur.commandeDepuisParametres("/cerveau questions") }
                Button("Tout effacer…") { confirmerVider = true }
                    .foregroundColor(Palette.rouge)
                Spacer()
                Button("Enregistrer la fiche") { enregistrerFiche() }
                    .disabled(!ficheModifiee)
            }

            Divider()
            Text("Souvenirs").font(.headline)
            List {
                ForEach(Array(souvenirs.enumerated()), id: \.offset) { numero, _ in
                    HStack {
                        Text("\(numero + 1).")
                            .foregroundColor(.secondary)
                            .monospacedDigit()
                        TextField("souvenir", text: liaisonSouvenir(numero))
                            .textFieldStyle(.plain)
                            .onSubmit {
                                moteur.modifierCerveau("modifier_souvenir", texte: souvenirs[numero], numero: numero + 1)
                            }
                        Button(role: .destructive) {
                            moteur.modifierCerveau("oublier_souvenir", numero: numero + 1)
                        } label: {
                            Image(systemName: "trash")
                        }
                        .buttonStyle(.borderless)
                        .help("Oublier ce souvenir")
                    }
                }
            }
            .frame(minHeight: 90)
            HStack {
                TextField("Nouveau souvenir (ex. : j'ai un chat qui s'appelle Filou)", text: $nouveau)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit(ajouter)
                Button("Ajouter", action: ajouter)
                    .disabled(nouveau.trimmingCharacters(in: .whitespaces).isEmpty)
            }
            Explication("Mémoire utilisée : \(moteur.fiche.taille) tokens sur \(moteur.fiche.limite). "
                        + "Après un changement, O.V.E.R. relit ses consignes en arrière-plan.")
            RappelCommandes(commandes: ["/cerveau", "/cerveau ajoute âge : 30 ans", "/cerveau modifie ville Lyon",
                                        "/cerveau oublie 2", "/cerveau vide", "/cerveau questions"])
        }
        .padding(4)
        .onAppear(perform: recopier)
        .onChange(of: moteur.fiche.souvenirs) { _ in recopier() }
        .onChange(of: moteur.fiche.champs) { _ in recopier() }
        .alert("Effacer tout le cerveau ?", isPresented: $confirmerVider) {
            Button("Tout effacer", role: .destructive) { moteur.modifierCerveau("vider") }
            Button("Annuler", role: .cancel) {}
        } message: {
            Text("O.V.E.R. ne saura plus rien de toi. Il te reposera les questions d'accueil au prochain lancement.")
        }
    }

    @ViewBuilder
    private func champEditable(_ champ: ChampCerveau) -> some View {
        let liaisonValeur = Binding(get: { valeurs[champ.cle] ?? champ.valeur },
                                    set: { valeurs[champ.cle] = $0 })
        switch champ.cle {
        case "adresse":
            Picker("", selection: liaisonValeur) {
                Text("tutoiement").tag("tu")
                Text("vouvoiement").tag("vous")
                if champ.valeur.isEmpty { Text("—").tag("") }
            }
            .labelsHidden()
            .pickerStyle(.segmented)
            .frame(maxWidth: 240)
        case "style_reponses":
            Picker("", selection: liaisonValeur) {
                Text("courtes").tag("courtes")
                Text("détaillées").tag("détaillées")
                if !["courtes", "détaillées"].contains(liaisonValeur.wrappedValue) {
                    Text(liaisonValeur.wrappedValue.isEmpty ? "—" : liaisonValeur.wrappedValue)
                        .tag(liaisonValeur.wrappedValue)
                }
            }
            .labelsHidden()
            .pickerStyle(.segmented)
            .frame(maxWidth: 300)
        default:
            TextField("(vide)", text: liaisonValeur)
                .textFieldStyle(.roundedBorder)
        }
    }

    private var ficheModifiee: Bool {
        moteur.fiche.champs.contains { champ in (valeurs[champ.cle] ?? champ.valeur) != champ.valeur }
    }

    private func enregistrerFiche() {
        for champ in moteur.fiche.champs {
            let valeur = (valeurs[champ.cle] ?? champ.valeur).trimmingCharacters(in: .whitespaces)
            if valeur != champ.valeur {
                moteur.modifierCerveau("definir", champ: champ.cle, texte: valeur)   // vide = oublier
            }
        }
    }

    private func ajouter() {
        let texte = nouveau.trimmingCharacters(in: .whitespaces)
        guard !texte.isEmpty else { return }
        moteur.modifierCerveau("ajouter", texte: texte)
        nouveau = ""
    }

    private func liaisonSouvenir(_ numero: Int) -> Binding<String> {
        Binding(get: { numero < souvenirs.count ? souvenirs[numero] : "" },
                set: { if numero < souvenirs.count { souvenirs[numero] = $0 } })
    }

    /// Recopie la fiche reçue du moteur dans les champs modifiables.
    private func recopier() {
        valeurs = [:]
        souvenirs = moteur.fiche.souvenirs
    }
}

// MARK: - Système : ce qu'O.V.E.R. a le droit de faire, sa mémoire, ses réponses

struct OngletSysteme: View {
    @EnvironmentObject private var moteur: Moteur
    @State private var creativite = 0.15
    @State private var longueur = 768.0

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                sectionCapacites
                Divider()
                sectionMemoire
                Divider()
                sectionReponses
                Divider()
                HStack {
                    Button("Redémarrer le moteur") { moteur.redemarrer() }
                    Explication("Les autres réglages techniques (modèle, taille de la mémoire) sont choisis pour ce Mac.")
                }
                RappelCommandes(commandes: ["/paramètres", "/paramètres web non", "/paramètres actions oui",
                                            "/paramètres apprentissage non",
                                            "/paramètres conversation oui", "/paramètres mémoire oui",
                                            "/paramètres créativité 0,3", "/paramètres longueur 512", "/effacer"])
            }
            .padding(4)
        }
        .onAppear {
            creativite = moteur.parametres.temperature
            longueur = moteur.parametres.longueurReponses
        }
        .onChange(of: moteur.parametres.temperature) { creativite = $0 }
        .onChange(of: moteur.parametres.longueurReponses) { longueur = $0 }
    }

    private var sectionCapacites: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Ce qu'il peut faire").font(.headline)
            Toggle("Chercher sur le web quand il ne sait pas", isOn: liaison(moteur, "recherche_web") { $0.rechercheWeb })
            Toggle("Agir sur le Mac (apps, sites, son, minuteurs, rappels)",
                   isOn: liaison(moteur, "actions_mac") { $0.actionsMac })
            Toggle("Proposer de retenir ce que tu dis de toi (toujours avec ton accord)",
                   isOn: liaison(moteur, "apprentissage_auto") { $0.apprentissageAuto })
        }
    }

    private var sectionMemoire: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Mémoire").font(.headline)
            Toggle("Garder la conversation d'un lancement à l'autre",
                   isOn: liaison(moteur, "garder_conversation") { $0.garderConversation })
            Toggle("Démarrage rapide : garder la lecture du modèle sur le disque (100 à 400 Mo)",
                   isOn: liaison(moteur, "memoire_sur_disque") { $0.memoireSurDisque })
                .disabled(!moteur.parametres.garderConversation)
            HStack {
                Button("Effacer la conversation") {
                    moteur.afficherParametres = false
                    moteur.effacerConversation()
                }
                .disabled(!moteur.peutEnvoyer)
                Explication("Couper l'une de ces options efface aussi ce qui était gardé.")
            }
        }
    }

    private var sectionReponses: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Réponses").font(.headline)
            HStack {
                Text("Créativité :")
                Slider(value: $creativite, in: 0...1, step: 0.05, onEditingChanged: { enCours in
                    if !enCours { moteur.changerReglage("temperature", creativite) }
                })
                .frame(maxWidth: 220)
                Text(nombreFr(creativite, decimales: 2) + (abs(creativite - 0.15) < 0.001 ? " (conseillé)" : ""))
                    .font(.caption)
                    .monospacedDigit()
            }
            Explication("0 : précis et toujours pareil · 1 : plus varié, mais il invente plus facilement.")
            HStack {
                Text("Longueur maximale :")
                Slider(value: $longueur, in: moteur.parametres.longueurMin...moteur.parametres.longueurMax,
                       step: 64, onEditingChanged: { enCours in
                           if !enCours { moteur.changerReglage("longueur_reponses", Int(longueur)) }
                       })
                    .frame(maxWidth: 220)
                Text("\(Int(longueur)) tokens (≈ \(Int(longueur * 0.7)) mots)")
                    .font(.caption)
                    .monospacedDigit()
            }
        }
    }
}

