# -*- coding: utf-8 -*-
"""Petit serveur HTTP local pour les essais : sert un dossier, accepte « Range » (reprise de curl -C -)."""
import http.server
import os
import re
import threading
import time


def demarrer(dossier, coupure=None, lent=None):
    """coupure : {nom: octets} pour simuler une connexion coupée au milieu du premier envoi.
    lent : {nom: secondes d'attente toutes les 64 Ko} pour un envoi lent (pause, fermeture en route).
    serveur.demandes : les en-têtes Range reçus, dans l'ordre."""
    coupures = dict(coupure or {})
    lenteurs = dict(lent or {})
    demandes = []

    class Gestionnaire(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            nom = os.path.basename(self.path.split("?")[0])
            chemin = os.path.join(dossier, nom)
            if not os.path.isfile(chemin):
                self.send_error(404)
                return
            with open(chemin, "rb") as f:
                donnees = f.read()
            debut = 0
            demandes.append((nom, self.headers.get("Range", "")))
            plage = re.match(r"bytes=(\d+)-", self.headers.get("Range", ""))
            if plage:
                debut = int(plage.group(1))
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {debut}-{len(donnees) - 1}/{len(donnees)}")
            else:
                self.send_response(200)
            morceau = donnees[debut:]
            self.send_header("Content-Length", str(len(morceau)))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            if nom in coupures:
                limite = coupures.pop(nom)
                self.wfile.write(morceau[:limite])
                self.wfile.flush()
                self.connection.shutdown(2)   # coupure brutale
                return
            if nom in lenteurs:
                try:
                    for position in range(0, len(morceau), 65536):
                        self.wfile.write(morceau[position:position + 65536])
                        self.wfile.flush()
                        time.sleep(lenteurs[nom])
                except (BrokenPipeError, ConnectionResetError):
                    pass   # le client a arrêté (pause, fermeture)
                return
            self.wfile.write(morceau)

    serveur = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Gestionnaire)
    serveur.demandes = demandes
    threading.Thread(target=serveur.serve_forever, daemon=True).start()
    return serveur, f"http://127.0.0.1:{serveur.server_address[1]}/"
