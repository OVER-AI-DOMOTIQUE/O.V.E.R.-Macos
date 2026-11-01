# -*- coding: utf-8 -*-
"""Mise en page de l'image disque d'O.V.E.R. : lu par dmgbuild, appelé par publier_app.sh.

Valeurs données par publier_app.sh (option -D) :
  application  chemin de OVER.app, déjà signée, notarisée et agrafée
  icone        AppIcon.icns de l'application (icône du disque une fois ouvert)
  fond         dmg/fond.png ; dmgbuild y joint tout seul dmg/fond@2x.png (écrans Retina)

Le fond (dmg/fond.html, rendu en 660 × 420) prévoit les icônes à ces emplacements : si on les
déplace ici, il faut refaire le fond, et inversement.
"""
import os.path

import dmgbuild.core


# Fond invisible avec le Finder récent (vu sur macOS 15) : dmgbuild 1.6.5 range DEUX renvois vers
# .background.tiff dans .DS_Store, un alias (icvp) et un marque-page (pBBk). Le Finder préfère le
# marque-page, ne sait pas le suivre et affiche alors une fenêtre grise. dmgbuild 1.6.7 corrige en
# n'écrivant plus le marque-page, mais demande Python 3.10 (celui d'Apple est en 3.9) : on fait
# la même chose ici. dmgbuild.core cherche « Bookmark » au moment de s'en servir, et s'il ne reçoit
# rien, il n'écrit pas pBBk. publier_app.sh vérifie ensuite, dans le DMG, que pBBk est absent.
class _SansMarquePage:
    @staticmethod
    def for_file(_chemin):
        return None


dmgbuild.core.Bookmark = _SansMarquePage

valeurs = globals()["defines"]  # les -D de publier_app.sh, fournis par dmgbuild
application = valeurs["application"]
nom_application = os.path.basename(application)

# Compression lzma : le plus petit téléchargement (lisible par macOS 10.15 et plus récent ;
# O.V.E.R. demande macOS 12)
format = "ULMO"
filesystem = "HFS+"

files = [application]
symlinks = {"Applications": "/Applications"}
# Pas de « .app » masqué de force : ça écrit une information Finder sur le paquet signé, que
# « codesign --strict » refuse. Le Finder masque déjà « .app » par défaut (affiché : « OVER »).
icon = valeurs["icone"]
background = valeurs["fond"]

# Fenêtre : 660 × 400 de contenu, plus 28 px de barre de titre ; ni barre d'outils, ni barre
# latérale, ni barre d'état. Le fond a 20 px de marge en bas si la barre de titre est plus fine.
window_rect = ((200, 140), (660, 428))
default_view = "icon-view"
show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False
show_icon_preview = False
include_list_view_settings = False

# Icônes de 128 px, centrées sur les emplacements prévus dans le fond ; noms en 13 points
icon_size = 128
text_size = 13
label_pos = "bottom"
arrange_by = None
icon_locations = {
    nom_application: (180, 205),
    "Applications": (480, 205),
}
