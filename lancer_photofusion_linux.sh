#!/usr/bin/env bash
# LLF PhotoFusion - lancement sous Linux (Linux Mint, Ubuntu, Debian...)
cd "$(dirname "$(readlink -f "$0")")" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 est requis : sudo apt install python3 python3-venv"
  exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
  echo "Premier lancement : préparation de LLF PhotoFusion (quelques minutes)..."
  if ! python3 -m venv .venv; then
    echo
    echo "Le module venv manque. Installez-le avec :"
    echo "   sudo apt install python3-venv"
    echo "puis relancez ce script."
    rm -rf .venv
    read -r -p "Appuyez sur Entrée pour fermer..." _
    exit 1
  fi
  .venv/bin/python -m pip install --upgrade pip
  if ! .venv/bin/python -m pip install -r requirements.txt; then
    echo "Nouvel essai sans les modules optionnels..."
    .venv/bin/python -m pip install numpy opencv-python-headless Pillow tifffile scipy
  fi
fi

exec .venv/bin/python PhotoFusion.py "$@"
