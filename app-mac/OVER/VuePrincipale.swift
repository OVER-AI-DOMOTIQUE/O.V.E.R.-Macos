// VuePrincipale.swift — la fenêtre : en-tête, puis accueil ou discussion.

import SwiftUI

struct VuePrincipale: View {
    @EnvironmentObject private var moteur: Moteur

    var body: some View {
        VStack(spacing: 0) {
            Entete()
            Divider()
            if case .sansDossier = moteur.etat {
                VueAccueil()
            } else if case .installationModele = moteur.etat {
                VueInstallationModele()
            } else {
                // le panneau Paramètres se pose par-dessus la discussion, qui garde son état (texte en cours…)
                ZStack {
                    VueDiscussion()
                    if moteur.afficherParametres {
                        VueParametres()
                    }
                }
            }
        }
    }
}

/// Bandeau du haut : logo, nom, modèle et état.
struct Entete: View {
    @EnvironmentObject private var moteur: Moteur

    var body: some View {
        HStack(spacing: 12) {
            Logo(taille: 34)
            VStack(alignment: .leading, spacing: 2) {
                Text("O.V.E.R.")
                    .font(.headline)
                Text(sousTitre)
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .lineLimit(1)
            }
            Spacer()
            BoutonVoix()
            Button {
                moteur.afficherParametres.toggle()
            } label: {
                Image(systemName: moteur.afficherParametres ? "gearshape.fill" : "gearshape")
                    .foregroundColor(moteur.afficherParametres ? Palette.sarcelle : .secondary)
            }
            .buttonStyle(.borderless)
            .help("Paramètres (⌘,)")
            HStack(spacing: 6) {
                Circle()
                    .fill(couleurEtat)
                    .frame(width: 8, height: 8)
                Text(moteur.libelleEtat)
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
    }

    private var sousTitre: String {
        var morceaux: [String] = []
        if !moteur.version.isEmpty { morceaux.append("version \(moteur.version)") }
        if !moteur.nomModele.isEmpty { morceaux.append(moteur.nomModele) }
        if moteur.rechercheWeb { morceaux.append("web") }
        if moteur.actionsMac { morceaux.append("actions sur le Mac") }
        if !moteur.rechercheWeb && !moteur.actionsMac { morceaux.append("local") }
        return morceaux.joined(separator: "  ·  ")
    }

    private var couleurEtat: Color {
        switch moteur.etat {
        case .pret: return moteur.preparation ? Palette.or : .green
        case .question: return Palette.or
        case .occupe, .chargement: return Palette.sarcelle
        case .arrete: return Palette.rouge
        case .sansDossier: return .gray
        case .installationModele: return Palette.or
        }
    }
}

/// Haut-parleur de l'en-tête : coupe la parole en cours (Échap), sinon active ou coupe la voix.
struct BoutonVoix: View {
    @EnvironmentObject private var moteur: Moteur

    var body: some View {
        if moteur.parle {
            Button(action: moteur.taire) {
                Label("Tais-toi", systemImage: "speaker.slash.fill")
            }
            .buttonStyle(.bordered)
            .tint(Palette.rouge)
            .keyboardShortcut(.escape, modifiers: [])
            .help("Arrête de parler (Échap)")
        } else {
            Button {
                moteur.changerReglage("voix_active", !moteur.parametres.voixActive)
            } label: {
                Image(systemName: moteur.parametres.voixActive ? "speaker.wave.2.fill" : "speaker.slash")
                    .foregroundColor(moteur.parametres.voixActive ? Palette.sarcelle : .secondary)
            }
            .buttonStyle(.borderless)
            .disabled(!moteur.moteurEnMarche)
            .help(moteur.parametres.voixActive ? "Voix active : O.V.E.R. lit ses réponses (clic pour couper)"
                                               : "Voix coupée (clic pour qu'O.V.E.R. lise ses réponses)")
        }
    }
}

/// Le logo d'O.V.E.R. (image « Logo » de l'application) ; à défaut, un logo dessiné.
struct Logo: View {
    let taille: CGFloat

    var body: some View {
        if let image = NSImage(named: "Logo") {
            Image(nsImage: image)
                .resizable()
                .interpolation(.high)
                .scaledToFit()
                .frame(width: taille, height: taille)
        } else {
            LogoDessine(taille: taille)
        }
    }
}

/// Logo de secours : anneau or sur fond sarcelle, point rouge au centre.
struct LogoDessine: View {
    let taille: CGFloat

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: taille * 0.22, style: .continuous)
                .fill(Palette.sarcelle)
            Circle()
                .stroke(Palette.or, lineWidth: taille * 0.1)
                .frame(width: taille * 0.56, height: taille * 0.56)
            Circle()
                .fill(Palette.rouge)
                .frame(width: taille * 0.14, height: taille * 0.14)
        }
        .frame(width: taille, height: taille)
    }
}

/// Premier lancement de l'application : le modèle de langage (2,1 Go) n'est pas encore sur ce Mac.
/// On le télécharge (une seule fois, vérifié) ou on reprend un fichier déjà téléchargé.
struct VueInstallationModele: View {
    @EnvironmentObject private var moteur: Moteur

    var body: some View {
        let etat = moteur.installation
        VStack(spacing: 16) {
            Spacer()
            Logo(taille: 84)
            Text("Dernière étape : le modèle de langage")
                .font(.title2.bold())
            Text("O.V.E.R. réfléchit avec Ministral 3 3B (Mistral AI, licence Apache 2.0). Ce fichier de "
                 + "\(tailleLisible(etat.taille)) se télécharge une seule fois : il reste sur ton Mac, et ensuite "
                 + "O.V.E.R. fonctionne sans Internet.")
                .multilineTextAlignment(.center)
                .foregroundColor(.secondary)
                .frame(maxWidth: 440)
                .fixedSize(horizontal: false, vertical: true)
            if etat.enCours {
                progression(etat)
            } else {
                boutons(etat)
            }
            if !etat.erreur.isEmpty {
                Text(etat.erreur)
                    .font(.callout)
                    .foregroundColor(etat.erreur.hasPrefix("En pause") ? .secondary : Palette.rouge)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: 440)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer()
            Text("Place libre sur le disque : \(tailleLisible(etat.libre))")
                .font(.caption)
                .foregroundColor(.secondary)
        }
        .padding(32)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    @ViewBuilder
    private func progression(_ etat: InstallationModele) -> some View {
        VStack(spacing: 10) {
            if etat.phase == "copie" && etat.fait == 0 {
                ProgressView()   // copie faite par l'application : sa durée n'est pas connue
                    .controlSize(.small)
            } else {
                ProgressView(value: min(etat.fait, max(etat.total, 1)), total: max(etat.total, 1))
                    .frame(maxWidth: 380)
                    .tint(Palette.sarcelle)
            }
            Text(texteProgression(etat))
                .font(.callout)
                .monospacedDigit()
                .foregroundColor(.secondary)
            if etat.phase == "telechargement" {
                Button("Pause") { moteur.pauseInstallation() }
                    .help("Le téléchargement reprendra là où il s'est arrêté")
            }
        }
    }

    @ViewBuilder
    private func boutons(_ etat: InstallationModele) -> some View {
        VStack(spacing: 10) {
            Button(libelleTelechargement(etat)) { moteur.telechargerModele() }
                .buttonStyle(.borderedProminent)
                .tint(Palette.sarcelle)
                .controlSize(.large)
                .disabled(etat.manque > 0)
            if etat.manque > 0 {
                Text("Il manque \(tailleLisible(etat.manque)) sur le disque : fais de la place, puis relance "
                     + "O.V.E.R. (menu Conversation ▸ Redémarrer le moteur).")
                    .font(.callout)
                    .foregroundColor(Palette.rouge)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: 420)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Button("J'ai déjà le fichier…") { moteur.choisirFichierModele() }
                .buttonStyle(.link)
        }
    }

    private func libelleTelechargement(_ etat: InstallationModele) -> String {
        etat.recu > 0 ? "Reprendre le téléchargement (\(tailleLisible(etat.recu)) sur \(tailleLisible(etat.taille)))"
                      : "Télécharger le modèle (\(tailleLisible(etat.taille)))"
    }

    private func texteProgression(_ etat: InstallationModele) -> String {
        let pourcent = etat.total > 0 ? Int(etat.fait / etat.total * 100) : 0
        switch etat.phase {
        case "verification":
            return "Vérification du fichier… \(pourcent) %"
        case "copie":
            return etat.fait == 0 ? "Copie du fichier…" : "Copie… \(pourcent) %"
        default:
            var texte = "\(tailleLisible(etat.fait)) sur \(tailleLisible(etat.total))"
            if etat.vitesse > 0 {
                texte += "  ·  \(nombreFr(etat.vitesse / 1e6)) Mo/s"
                let restant = (etat.total - etat.fait) / etat.vitesse
                if restant >= 90 {
                    texte += "  ·  environ \(Int((restant / 60).rounded())) min"
                } else if restant > 0 {
                    texte += "  ·  moins de 2 min"
                }
            }
            return texte
        }
    }
}

/// Premier lancement : choisir le dossier où O.V.E.R. est installé.
struct VueAccueil: View {
    @EnvironmentObject private var moteur: Moteur

    var body: some View {
        VStack(spacing: 18) {
            Spacer()
            Logo(taille: 96)
            Text("Bienvenue dans O.V.E.R.")
                .font(.title2.bold())
            Text("Je ne trouve pas les scripts d'O.V.E.R. à côté de l'application. Range OVER.app dans le dossier "
                 + "qui contient serveur.py, .venv et modeles (par exemple Téléchargements/test), ou choisis ce dossier.")
                .multilineTextAlignment(.center)
                .foregroundColor(.secondary)
                .frame(maxWidth: 420)
            Button("Choisir le dossier O.V.E.R.…") { moteur.choisirDossier() }
                .buttonStyle(.borderedProminent)
                .tint(Palette.sarcelle)
                .controlSize(.large)
            if !moteur.problemeDossier.isEmpty {
                Text(moteur.problemeDossier)
                    .font(.callout)
                    .foregroundColor(Palette.rouge)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: 420)
            }
            Spacer()
        }
        .padding(32)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
