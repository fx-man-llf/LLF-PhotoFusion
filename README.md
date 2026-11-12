<p align="center"><img src="photofusion/web/logo.png" width="120" alt="LLF"></p>

<h1 align="center">LLF PhotoFusion</h1>

<p align="center">
Assemblage automatique d'images numérisées · Automatic stitching of scanned images · スキャン画像の自動結合<br>
<b>Logiciel gratuit, utilisable par tous</b> · Free software, anyone can use it · 無料・どなたでも利用可<br>
© 2026 LLF — <a href="https://www.laludotheque.fr">laludotheque.fr</a> · <a href="https://www.lalogitheque.fr">lalogitheque.fr</a>
</p>

---

## 🇫🇷 Français

LLF PhotoFusion fait l'équivalent de « Photomerge » de Photoshop pour des numérisations : il retrouve où se trouve chaque morceau, le remet dans le bon sens (même à l'envers ou de travers), corrige les petites déformations du papier et du scanner, puis fusionne le tout en une seule image **sans raccord visible**, au même format et à la même résolution (dpi) que les originaux.

- Formats : JPG, PNG, TIFF 8/16 bits, BMP, WEBP, JPEG 2000, RAW d'appareils photo
- Jusqu'à 12 images et plus, en très haute résolution
- Interface simple en français, anglais et japonais
- Fonctionne sous **Windows 11** et **Linux Mint** ; tout reste sur votre ordinateur

### Installation
1. Téléchargez le projet : bouton vert **Code ▸ Download ZIP**, puis décompressez-le.
2. **Windows 11** : double-cliquez sur `Lancer LLF PhotoFusion (Windows).bat` (le premier lancement installe Python et les modules, quelques minutes).
3. **Linux Mint** : `sudo apt install python3-venv`, puis `./lancer_photofusion_linux.sh`.

Mode d'emploi complet : [LISEZMOI.txt](LISEZMOI.txt)

## 🇬🇧 English

LLF PhotoFusion does for scans what Photoshop's "Photomerge" does: it finds where each piece belongs, turns it the right way up, corrects small paper and scanner distortions, and blends everything into one **seamless** image with the same format and resolution (dpi) as the originals.

**Install:** *Code ▸ Download ZIP*, unzip, then run `Lancer LLF PhotoFusion (Windows).bat` on Windows 11, or `./lancer_photofusion_linux.sh` on Linux Mint (after `sudo apt install python3-venv`). Full guide: [README_EN.txt](README_EN.txt)

## 🇯🇵 日本語

LLF PhotoFusion は、Photoshop の「Photomerge」と同じことをスキャン画像で行います。各画像の位置と向きを自動で見つけ、用紙やスキャナーのゆがみを補正し、**継ぎ目の見えない**1枚の画像に合成します。出力は元画像と同じ形式・解像度（dpi）です。

**インストール：** *Code ▸ Download ZIP* でダウンロードして展開し、Windows 11 では `Lancer LLF PhotoFusion (Windows).bat`、Linux Mint では `sudo apt install python3-venv` の後 `./lancer_photofusion_linux.sh` を実行します。詳しくは [README_JA.txt](README_JA.txt) をご覧ください。

---

### Fonctionnement technique / How it works
SIFT (répartition homogène) + RANSAC à hypothèses multiples vérifiées photométriquement · appariement dense sub-pixel · optimisation globale affine · déformation locale par maillage (papier gondolé, étirement du scanner) · compensation d'exposition · coutures graph-cut · fusion deux bandes · rendu par tuiles avec cache disque.

Ligne de commande : `python photofusion_cli.py a.tif b.tif -o fusion.tif [--crop] [--rotation 0.5]`
