#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fusion en ligne de commande (sans interface) :
    python photofusion_cli.py image1.tif image2.tif ... -o resultat.tif
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from photofusion import core  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description='LLF PhotoFusion - assemblage automatique')
    ap.add_argument('images', nargs='+')
    ap.add_argument('-o', '--output', required=True)
    ap.add_argument('--rotation', type=float, default=0.0, help='redressement en degrés')
    ap.add_argument('--crop', action='store_true', help='rogner les bords vides')
    ap.add_argument('--black', action='store_true', help='fond noir au lieu de blanc')
    a = ap.parse_args()
    print('LLF PhotoFusion — logiciel gratuit, utilisable par tous.')
    print('© 2026 LLF — https://www.laludotheque.fr · https://www.lalogitheque.fr')

    def cb(f, m):
        sys.stdout.write(f'\r[{int(f * 100):3d} %] {m[:70]:<70}')
        sys.stdout.flush()
    prog = core.Progress(cb)
    t0 = time.time()
    P = core.Project()
    try:
        for p in a.images:
            print('Lecture de', p)
            P.add(p)
        rep = P.align(prog)
        print()
        for e in rep['edges']:
            print(f"  {e['a']} <-> {e['b']} : {e['points']} points, écart {e['ecart_final_px']} px")
        lost = [s.name for s in P.sources if not s.placed]
        if lost:
            print('Attention, images non placées :', ', '.join(lost))
        fmt = os.path.splitext(a.output)[1].lower().lstrip('.')
        fmt = {'tiff': 'tif', 'jpeg': 'jpg'}.get(fmt, fmt)
        res = P.merge(a.output, fmt, rotation_deg=a.rotation, crop=a.crop,
                      background=(0, 0, 0) if a.black else (255, 255, 255), progress=prog)
        print(f"\nTerminé : {res['path']} ({res['width']} x {res['height']} px) en {time.time() - t0:.0f} s")
    finally:
        P.cleanup()


if __name__ == '__main__':
    main()
