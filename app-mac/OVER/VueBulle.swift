// VueBulle.swift — une bulle de la discussion, et la mise en forme du Markdown du modèle.

import SwiftUI

struct VueBulle: View {
    @EnvironmentObject private var moteur: Moteur
    let message: Message

    var body: some View {
        switch message.auteur {
        case .utilisateur:
            HStack {
                Spacer(minLength: 80)
                Text(message.texte)
                    .textSelection(.enabled)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 8)
                    .foregroundColor(.white)
                    .background(Palette.sarcelle)
                    .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            }

        case .over:
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 8) {
                    Text("O.V.E.R.")
                        .font(.caption.bold())
                        .foregroundColor(Palette.sarcelle)
                    TexteMarkdown(message.texte + (message.enCours ? " ▍" : ""))
                    if !message.extrait.isEmpty {
                        Text("« \(message.extrait) »")
                            .italic()
                            .foregroundColor(Palette.or)
                            .textSelection(.enabled)
                    }
                    if !message.valeurActuelle.isEmpty && !message.repondue {
                        Text("Réponse actuelle : \(message.valeurActuelle)")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                    if message.genre == .question && !message.repondue {
                        boutonsDeReponse
                    }
                    if !message.sources.isEmpty {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Sources")
                                .font(.caption.bold())
                                .foregroundColor(.secondary)
                            ForEach(message.sources.prefix(4), id: \.self) { adresse in
                                if let url = URL(string: adresse) {
                                    Link(adresse, destination: url)
                                        .font(.caption)
                                        .lineLimit(1)
                                        .truncationMode(.middle)
                                }
                            }
                        }
                    }
                    if !message.statistiques.isEmpty {
                        Text(message.statistiques)
                            .font(.caption2)
                            .foregroundColor(.secondary)
                    }
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 10)
                .background(Color(nsColor: .controlBackgroundColor))
                .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: 14, style: .continuous)
                        .stroke(Color.secondary.opacity(0.15), lineWidth: 1)
                )
                Spacer(minLength: 80)
            }

        case .systeme:
            HStack(spacing: 6) {
                if message.genre == .recherche {
                    Image(systemName: message.enCours ? "magnifyingglass" : "globe")
                }
                Text(message.texte)
                    .textSelection(.enabled)
            }
            .font(.caption)
            .foregroundColor(message.genre == .erreur ? Palette.rouge : .secondary)
            .frame(maxWidth: .infinity, alignment: .center)
            .multilineTextAlignment(.center)
        }
    }

    private var boutonsDeReponse: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                ForEach(message.choix) { choix in
                    Button(choix.libelle) {
                        moteur.repondre(valeur: choix.valeur, libelle: choix.libelle)
                    }
                    .buttonStyle(.bordered)
                    .disabled(moteur.etat != .question)
                }
            }
            if !message.aide.isEmpty {
                Text(message.aide.replacingOccurrences(of: "o : oui  ·  n : non  ·  ", with: ""))
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
    }
}

/// Affiche le Markdown écrit par le modèle : gras, italique, code, liens ; les titres
/// deviennent du gras et les séparateurs « --- » une ligne.
struct TexteMarkdown: View {
    let source: String

    init(_ source: String) {
        self.source = source
    }

    var body: some View {
        Text(TexteMarkdown.convertir(source))
            .textSelection(.enabled)
            .fixedSize(horizontal: false, vertical: true)
    }

    static func convertir(_ texte: String) -> AttributedString {
        let lignes = texte.components(separatedBy: "\n").map { ligne -> String in
            let nette = ligne.trimmingCharacters(in: .whitespaces)
            if nette.hasPrefix("#") {
                let titre = nette.drop(while: { $0 == "#" }).trimmingCharacters(in: .whitespaces)
                    .replacingOccurrences(of: "**", with: "")
                return titre.isEmpty ? "" : "**\(titre)**"
            }
            if nette == "---" || nette == "***" || nette == "___" {
                return "────────────"
            }
            return ligne
        }
        let prepare = lignes.joined(separator: "\n")
        let options = AttributedString.MarkdownParsingOptions(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        if let resultat = try? AttributedString(markdown: prepare, options: options) {
            return resultat
        }
        return AttributedString(prepare)
    }
}
