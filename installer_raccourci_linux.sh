#!/usr/bin/env bash
# Ajoute LLF PhotoFusion au menu des applications (Linux Mint / Cinnamon, GNOME, Xfce...)
DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
mkdir -p "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/photofusion.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=LLF PhotoFusion
Comment=Assemblage d'images numérisées
Exec="$DIR/lancer_photofusion_linux.sh"
Icon=$DIR/photofusion/web/logo.png
Terminal=true
Categories=Graphics;
DESK
chmod +x "$HOME/.local/share/applications/photofusion.desktop"
echo "LLF PhotoFusion a été ajouté au menu des applications (catégorie Graphisme)."
