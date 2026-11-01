// VueDiscussion.swift — la conversation et la barre de saisie avec le bouton Arrêter.

import SwiftUI

struct VueDiscussion: View {
    @EnvironmentObject private var moteur: Moteur
    @ObservedObject private var ecoute = Ecoute.partage   // partagée avec le raccourci clavier
    @State private var saisie = ""
    @State private var proposerInstallation = false    // micro pressé alors que l'écoute n'est pas installée
    @State private var derniereDuree = ""              // « transcrit en 1,8 s » (aide du bouton micro)
    @FocusState private var champActif: Bool

    var body: some View {
        VStack(spacing: 0) {
            ScrollViewReader { lecteur in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 12) {
                        ForEach(moteur.messages) { message in
                            VueBulle(message: message)
                                .id(message.id)
                        }
                        Color.clear
                            .frame(height: 1)
                            .id("bas")
                    }
                    .padding(16)
                }
                .onChange(of: moteur.defilement) { _ in
                    lecteur.scrollTo("bas", anchor: .bottom)
                }
            }

            if case .arrete(let raison) = moteur.etat {
                BandeauArret(raison: raison)
            }

            if proposerInstallation {
                BandeauEcoute(texte: texteInstallation, actionLibelle: "Installer") {
                    proposerInstallation = false
                } action: {
                    proposerInstallation = false
                    moteur.installerEcoute()
                }
            } else if !ecoute.probleme.isEmpty && ecoute.mode == .automatique {
                BandeauEcoute(texte: ecoute.probleme) { ecoute.probleme = "" }
            }

            Divider()
            barreDeSaisie
        }
        .onChange(of: moteur.etat) { nouvelEtat in
            if nouvelEtat == .pret || nouvelEtat == .question { champActif = true }
            if case .arrete = nouvelEtat { ecoute.annuler() }
        }
        .onChange(of: ecoute.resultat?.id) { _ in
            recevoirTranscription()
        }
        .onChange(of: ecoute.texteApercu) { apercu in
            // le texte s'écrit pendant que tu parles (bouton micro de la fenêtre)
            if ecoute.mode == .automatique && ecoute.etat != .repos && !apercu.isEmpty { saisie = apercu }
        }
        .onChange(of: moteur.parametres.etatEcoute) { etat in
            if etat == "prete" { proposerInstallation = false }
        }
    }

    private var barreDeSaisie: some View {
        HStack(spacing: 10) {
            Button(action: basculerEcoute) {
                boutonMicro
                    .frame(width: 18, height: 18)
            }
            .buttonStyle(.borderless)
            .keyboardShortcut("d", modifiers: [.command])
            .disabled(!moteur.peutEcrire && !ecoute.enCours)
            .help(aideMicro)

            TextField(indication, text: $saisie)
                .textFieldStyle(.roundedBorder)
                .font(.body)
                .focused($champActif)
                .disabled(!moteur.peutEcrire)
                .onSubmit(envoyer)

            if moteur.etat == .occupe {
                Button(action: moteur.arreter) {
                    Label("Arrêter", systemImage: "stop.fill")
                }
                .buttonStyle(.borderedProminent)
                .tint(Palette.rouge)
                .keyboardShortcut(".", modifiers: [.command])
                .help("Arrête la réponse ou la recherche en cours (⌘.)")
            } else {
                Button(action: envoyer) {
                    Label("Envoyer", systemImage: "arrow.up.circle.fill")
                }
                .buttonStyle(.borderedProminent)
                .tint(Palette.sarcelle)
                .disabled(!moteur.peutEcrire || saisie.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(12)
    }

    @ViewBuilder private var boutonMicro: some View {
        switch ecoute.etat {
        case .transcription:
            ProgressView()
                .controlSize(.small)
        case .ecoute:
            Image(systemName: "mic.fill")
                .foregroundColor(Palette.rouge)
                .scaleEffect(1 + CGFloat(ecoute.niveau) * 0.35)   // suit le volume de ta voix
        case .repos:
            Image(systemName: "mic")
                .foregroundColor(.primary)
        }
    }

    private var aideMicro: String {
        switch ecoute.etat {
        case .ecoute: return "J'écoute… (un court silence suffit pour finir)"
        case .transcription: return "Je transcris ce que tu as dit, sur ton Mac…"
        case .repos: return "Parler à O.V.E.R. (⌘D) · reconnaissance sur ton Mac (whisper.cpp)" + derniereDuree
        }
    }

    private var texteInstallation: String {
        "Pour t'écouter, j'installe une reconnaissance vocale qui tourne sur ton Mac (whisper.cpp) : rien ne part "
            + "chez Apple. Une seule fois : environ 220 Mo à télécharger et 5 à 15 minutes de compilation, "
            + "pendant lesquelles je réponds plus lentement."
    }

    private func basculerEcoute() {
        switch ecoute.etat {
        case .ecoute:
            ecoute.terminer()
        case .transcription:
            break   // la transcription finit d'elle-même
        case .repos:
            switch moteur.parametres.etatEcoute {
            case "prete":
                proposerInstallation = false
                moteur.taire()   // sinon il s'entendrait parler
                ecoute.commencer(mode: .automatique)
            case "installation":
                ecoute.probleme = "Installation de mon écoute en cours : "
                    + (moteur.parametres.messageEcoute.isEmpty ? "patience…" : moteur.parametres.messageEcoute)
            default:
                ecoute.probleme = ""
                proposerInstallation = true
            }
        }
    }

    /// Le texte entendu arrive du moteur (whisper.cpp). Le raccourci clavier a sa propre fenêtre flottante.
    private func recevoirTranscription() {
        guard let resultat = ecoute.resultat, resultat.mode == .automatique else { return }
        if resultat.duree > 0 {
            derniereDuree = " · dernière phrase transcrite en \(nombreFr(resultat.duree)) s"
        }
        if resultat.erreur == "a_installer" {
            proposerInstallation = true
        } else if !resultat.erreur.isEmpty {
            ecoute.probleme = "Je n'ai pas pu transcrire : \(resultat.erreur)"
        } else if resultat.texte.isEmpty {
            ecoute.probleme = "Je n'ai rien compris. Réessaie en parlant un peu plus près du micro."
        } else {
            finEcoute(resultat.texte)
        }
    }

    private func finEcoute(_ reconnu: String) {
        guard !reconnu.isEmpty else { return }
        if moteur.parametres.ecouteEnvoiAuto && envoyerTexte(reconnu) {
            saisie = ""
        } else {
            saisie = reconnu
            champActif = true   // tu relis, tu corriges, puis Entrée
        }
    }

    private var indication: String {
        if ecoute.etat == .ecoute { return "J'écoute… parle, puis fais une petite pause" }
        if ecoute.etat == .transcription { return "Je transcris ce que tu as dit…" }
        switch moteur.etat {
        case .question: return "Ta réponse… (Entrée pour envoyer)"
        case .pret:
            return moteur.preparation ? "Écris à O.V.E.R.… (il finit de lire ses consignes, il répondra juste après)"
                                      : "Écris à O.V.E.R.… (Entrée pour envoyer)"
        case .occupe: return "O.V.E.R. réfléchit…"
        case .chargement: return "Chargement du modèle…"
        case .installationModele: return "Installation du modèle…"
        case .arrete, .sansDossier: return "Moteur arrêté"
        }
    }

    private func envoyer() {
        if envoyerTexte(saisie) {
            saisie = ""
        }
    }

    /// Envoie un message (ou une réponse à une question). Renvoie false si ce n'est pas le moment.
    @discardableResult
    private func envoyerTexte(_ brut: String) -> Bool {
        let texte = brut.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !texte.isEmpty else { return false }
        switch moteur.etat {
        case .question:
            moteur.repondre(valeur: texte, libelle: texte)
        case .pret:
            moteur.envoyerSaisie(texte)
        default:
            return false
        }
        return true
    }
}

/// Explication quand l'écoute est impossible (autorisation, rien entendu, écoute à installer…).
struct BandeauEcoute: View {
    let texte: String
    var actionLibelle: String? = nil
    let fermer: () -> Void
    var action: (() -> Void)? = nil

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: "mic.slash")
                .foregroundColor(Palette.or)
            Text(texte)
                .font(.callout)
                .frame(maxWidth: .infinity, alignment: .leading)
            if let actionLibelle = actionLibelle, let action = action {
                Button(actionLibelle, action: action)
                    .buttonStyle(.borderedProminent)
                    .tint(Palette.sarcelle)
                Button("Plus tard", action: fermer)
            } else {
                Button("OK", action: fermer)
            }
        }
        .padding(12)
        .background(Palette.or.opacity(0.1))
    }
}

/// Affiché quand le moteur s'est arrêté, avec un bouton pour le relancer.
struct BandeauArret: View {
    @EnvironmentObject private var moteur: Moteur
    let raison: String

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundColor(Palette.rouge)
            Text(raison)
                .font(.callout)
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
            Button("Redémarrer") { moteur.redemarrer() }
        }
        .padding(12)
        .background(Palette.rouge.opacity(0.08))
    }
}
