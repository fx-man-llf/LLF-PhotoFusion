LLF PhotoFusion 1.2 - Automatic stitching of scanned images
===========================================================

FREE software, anyone can use it.
(c) 2026 LLF  -  https://www.laludotheque.fr  -  https://www.lalogitheque.fr

LLF PhotoFusion does for scans what Photoshop's "Photomerge" does: it finds
where each piece belongs, turns it the right way up, corrects small paper and
scanner distortions, then blends everything into a single seamless image in
the same format and resolution (dpi) as the originals.
Formats: JPG, PNG, TIFF (8/16-bit), BMP, WEBP, JPEG 2000, camera RAW.

INSTALL AND START
  Windows 11 : double-click "Lancer LLF PhotoFusion (Windows).bat".
               The first start installs Python and the required modules
               automatically (Internet needed, a few minutes).
               Optional: "Creer un raccourci sur le Bureau (Windows).bat"
               puts an LLF PhotoFusion icon on the Desktop.
  Linux Mint : once, in a terminal:  sudo apt install python3-venv
               then run ./lancer_photofusion_linux.sh
               (./installer_raccourci_linux.sh adds it to the menu).
Keep the small console window open while you use the program.
Everything runs on your computer: no image is sent to the Internet.

HOW TO USE
  Language: French, English or Japanese, from the menu at the top right (🌐).
  1. Add your images (button or drag & drop). The first image is the
     reference: it sets the orientation of the result.
  2. "Stitch automatically". If an image stays red, drag it roughly into
     place in the "Layout" tab, rotate it if needed, and run again.
  3. Settings: straighten, crop empty borders, output format.
  4. "Create the final image": saved in the chosen folder, with the same
     bit depth, dpi and colour profile as the originals.
