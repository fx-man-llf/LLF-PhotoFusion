# -*- coding: utf-8 -*-
"""
Moteur de LLF PhotoFusion.

Étapes :
  1. Chargement des images (JPG, PNG, TIFF 8/16 bits, BMP, WEBP, RAW) et mise en
     cache sur disque (memmap) pour traiter de très grandes numérisations sans
     saturer la mémoire.
  2. Détection de points caractéristiques SIFT (invariants à la rotation : une
     image numérisée à l'envers ou de travers est retrouvée automatiquement).
  3. Mise en correspondance de toutes les paires + RANSAC (similitude :
     rotation + translation + échelle).
  4. Affinage sub-pixel à pleine résolution par corrélation de patchs.
  5. Résolution globale par moindres carrés (pas d'accumulation d'erreur
     même avec 12 images).
  6. Compensation d'exposition, recherche de couture optimale (graph-cut),
     puis fusion en deux bandes de fréquences, par tuiles, à pleine résolution.
  7. Écriture au même format et avec la même résolution (dpi) que l'original.
"""
import os
import math
import shutil
import struct
import tempfile
import threading
import zlib

import numpy as np
import cv2
from PIL import Image

from .i18n import tr

Image.MAX_IMAGE_PIXELS = None
try:
    import tifffile
except Exception:  # pragma: no cover
    tifffile = None
try:
    import rawpy
except Exception:  # pragma: no cover
    rawpy = None

try:
    cv2.setNumThreads(max(1, os.cpu_count() or 1))
except Exception:
    pass

RAW_EXT = {'.cr2', '.cr3', '.nef', '.nrw', '.arw', '.srf', '.sr2', '.dng', '.orf',
           '.rw2', '.raf', '.pef', '.srw', '.raw', '.erf', '.kdc', '.3fr', '.mef',
           '.mos', '.x3f', '.iiq', '.rwl'}
STD_EXT = {'.jpg', '.jpeg', '.jpe', '.png', '.tif', '.tiff', '.bmp', '.webp', '.jp2'}
SUPPORTED_EXT = STD_EXT | RAW_EXT

FEAT_MAX = 2400        # taille max (px) des images pour la détection de points
LOWRES_MAX = 2600      # taille max du canevas basse résolution (coutures, aperçu)
TILE = 2048            # taille des tuiles de rendu pleine résolution


class Cancelled(Exception):
    pass


class Progress:
    """Rapport de progression + annulation, partagé avec l'interface."""

    def __init__(self, cb=None):
        self.cb = cb
        self.cancel_event = threading.Event()

    def __call__(self, frac, msg):
        if self.cancel_event.is_set():
            raise Cancelled()
        if self.cb:
            self.cb(float(max(0.0, min(1.0, frac))), msg)

    def sub(self, a, b):
        parent = self

        class _Sub(Progress):
            def __init__(self):
                self.cancel_event = parent.cancel_event
                self.cb = None

            def __call__(self, frac, msg):
                parent(a + (b - a) * frac, msg)
        return _Sub()


# --------------------------------------------------------------------------
# Lecture / écriture
# --------------------------------------------------------------------------

def _to_supported_dtype(img):
    if img.dtype == np.uint8 or img.dtype == np.uint16:
        return img
    if img.dtype in (np.float32, np.float64):
        mx = float(np.nanmax(img)) if img.size else 1.0
        if mx <= 1.0 + 1e-6:
            return np.clip(img * 65535.0, 0, 65535).astype(np.uint16)
        return np.clip(img, 0, 65535).astype(np.uint16)
    if img.dtype == np.int16:
        return np.clip(img.astype(np.int32) + 32768, 0, 65535).astype(np.uint16)
    if img.dtype in (np.uint32, np.int32):
        return (np.clip(img, 0, 2**32 - 1).astype(np.float64) / 65537.0).astype(np.uint16)
    if img.dtype == bool:
        return img.astype(np.uint8) * 255
    return img.astype(np.uint8)


def _normalize_channels(img):
    """-> (image BGR ou gris, est_gris)"""
    if img.ndim == 2:
        return img, True
    if img.ndim == 3 and img.shape[2] == 1:
        return img[:, :, 0], True
    if img.ndim == 3 and img.shape[2] == 4:
        # compose l'alpha sur du blanc
        a = img[:, :, 3:4].astype(np.float32) / (65535.0 if img.dtype == np.uint16 else 255.0)
        white = 65535.0 if img.dtype == np.uint16 else 255.0
        rgb = img[:, :, :3].astype(np.float32) * a + white * (1 - a)
        return rgb.astype(img.dtype), False
    if img.ndim == 3 and img.shape[2] >= 3:
        return img[:, :, :3], False
    raise ValueError("Format de pixels non pris en charge")


def read_metadata(path):
    meta = {'dpi': None, 'icc': None}
    try:
        with Image.open(path) as im:
            dpi = im.info.get('dpi')
            if dpi:
                meta['dpi'] = (float(dpi[0]), float(dpi[1]))
            icc = im.info.get('icc_profile')
            if icc:
                meta['icc'] = icc
    except Exception:
        pass
    return meta


def read_image(path):
    """Retourne (image BGR ou gris en uint8/uint16, métadonnées)."""
    ext = os.path.splitext(path)[1].lower()
    if ext in RAW_EXT:
        if rawpy is None:
            raise RuntimeError(tr('no_rawpy'))
        with rawpy.imread(path) as raw:
            rgb = raw.postprocess(output_bps=16, use_camera_wb=True, no_auto_bright=True)
        return np.ascontiguousarray(rgb[:, :, ::-1]), {'dpi': None, 'icc': None, 'raw': True}

    meta = read_metadata(path)
    img = None
    try:
        data = np.fromfile(path, dtype=np.uint8)   # gère les chemins accentués sous Windows
        img = cv2.imdecode(data, cv2.IMREAD_ANYDEPTH | cv2.IMREAD_ANYCOLOR)
        del data
    except Exception:
        img = None
    if img is None and tifffile is not None and ext in ('.tif', '.tiff'):
        try:
            arr = tifffile.imread(path, key=0)
            if arr.ndim == 3 and arr.shape[0] in (3, 4) and arr.shape[2] not in (3, 4):
                arr = np.moveaxis(arr, 0, 2)
            if arr.ndim == 3 and arr.shape[2] >= 3:
                arr = np.ascontiguousarray(arr[:, :, [2, 1, 0] + ([3] if arr.shape[2] == 4 else [])])
            img = arr
        except Exception:
            img = None
    if img is None:
        with Image.open(path) as im:
            if im.mode in ('I;16', 'I;16B', 'I;16L', 'I'):
                img = np.array(im)
            else:
                img = np.array(im.convert('RGB'))[:, :, ::-1]
    img = _to_supported_dtype(img)
    img, gray = _normalize_channels(img)
    meta['gray'] = gray
    return np.ascontiguousarray(img), meta


def _png_insert_chunks(png_bytes, dpi=None, icc=None):
    """Ajoute les blocs pHYs (dpi) et iCCP à un PNG encodé par OpenCV."""
    chunks = b''

    def chunk(tag, data):
        c = struct.pack('>I', len(data)) + tag + data
        return c + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff)
    if icc:
        chunks += chunk(b'iCCP', b'ICC\x00\x00' + zlib.compress(icc))
    if dpi:
        ppm_x = int(round(dpi[0] / 0.0254))
        ppm_y = int(round(dpi[1] / 0.0254))
        chunks += chunk(b'pHYs', struct.pack('>IIB', ppm_x, ppm_y, 1))
    # insertion après IHDR (8 octets signature + 25 octets IHDR)
    return png_bytes[:33] + chunks + png_bytes[33:]


def write_image(path, rgb, fmt, dpi=None, icc=None, progress=None):
    """rgb : tableau (H,W,3) ou (H,W) en ordre RGB, uint8/uint16 (peut être un memmap)."""
    fmt = fmt.lower()
    h, w = rgb.shape[:2]
    if fmt in ('tif', 'tiff'):
        if tifffile is None:
            raise RuntimeError(tr('no_tifffile'))
        nbytes = rgb.size * rgb.itemsize
        kwargs = dict(photometric='rgb' if rgb.ndim == 3 else 'minisblack',
                      bigtiff=nbytes > 3_800_000_000,
                      rowsperstrip=64,
                      software='LLF PhotoFusion - www.lalogitheque.fr')
        if dpi:
            kwargs['resolution'] = (float(dpi[0]), float(dpi[1]))
            kwargs['resolutionunit'] = 'INCH'
        if icc:
            kwargs['extratags'] = [(34675, 7, len(icc), icc, True)]
        try:
            tifffile.imwrite(path, rgb, compression='lzw', predictor=True, **kwargs)
        except Exception:
            # pas d'imagecodecs : compression Deflate (zlib), lue partout (Photoshop, GIMP...)
            if os.path.exists(path):
                os.remove(path)
            tifffile.imwrite(path, rgb, compression='zlib', predictor=True, **kwargs)
        return path
    if fmt in ('jpg', 'jpeg'):
        if max(h, w) > 65500:
            raise RuntimeError(tr('jpeg_too_big'))
        arr = np.asarray(rgb)
        if arr.dtype == np.uint16:
            arr = (arr >> 8).astype(np.uint8)
        im = Image.fromarray(arr)
        kw = dict(quality=95, subsampling=0, optimize=False)
        if dpi:
            kw['dpi'] = (int(round(dpi[0])), int(round(dpi[1])))
        if icc:
            kw['icc_profile'] = icc
        im.save(path, 'JPEG', **kw)
        return path
    if fmt == 'png':
        arr = np.asarray(rgb)
        bgr = arr[:, :, ::-1] if arr.ndim == 3 else arr
        ok, buf = cv2.imencode('.png', np.ascontiguousarray(bgr), [cv2.IMWRITE_PNG_COMPRESSION, 4])
        if not ok:
            raise RuntimeError(tr('png_fail'))
        data = _png_insert_chunks(buf.tobytes(), dpi, icc)
        with open(path, 'wb') as f:
            f.write(data)
        return path
    raise ValueError("Format de sortie inconnu : " + fmt)


# --------------------------------------------------------------------------
# Géométrie
# --------------------------------------------------------------------------

def to33(M):
    A = np.eye(3)
    A[:2, :] = np.asarray(M, float)[:2, :]
    return A


def apply(M, pts):
    pts = np.asarray(pts, float)
    return pts @ M[:2, :2].T + M[:2, 2]


def affine_params(M):
    """-> (échelle moyenne, angle en degrés, rapport d'anisotropie)"""
    L = M[:2, :2]
    sv = np.linalg.svd(L, compute_uv=False)
    ang = math.degrees(math.atan2(L[1, 0] - L[0, 1], L[0, 0] + L[1, 1]))
    return float(math.sqrt(abs(np.linalg.det(L)))), ang, float(sv[0] / max(sv[1], 1e-9))


def rot_matrix(deg, cx=0.0, cy=0.0):
    th = math.radians(deg)
    c, s = math.cos(th), math.sin(th)
    if abs(deg % 90) < 1e-9:
        c, s = round(c), round(s)
    return np.array([[1, 0, cx], [0, 1, cy], [0, 0, 1]]) @ np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]]) @ \
        np.array([[1, 0, -cx], [0, 1, -cy], [0, 0, 1]])


# --------------------------------------------------------------------------
# Données d'une image
# --------------------------------------------------------------------------

class Source:
    def __init__(self, sid, path, name, cache_dir):
        self.id = sid
        self.path = path
        self.name = name
        self.cache_dir = cache_dir
        self.ext = os.path.splitext(name)[1].lower()
        self.w = self.h = 0
        self.dtype = 'uint8'
        self.gray = False
        self.meta = {}
        self.small = None        # BGR uint8 réduit (échelle self.s)
        self.s = 1.0
        self.pts = None          # coordonnées pleine résolution des points SIFT
        self.des = None
        self.thumb_jpeg = None   # vignette pour l'interface
        self.thumb_scale = 1.0
        self.T = np.eye(3)       # affine : coordonnées image -> repère de référence
        self.mesh = None         # déformation locale fine (dans le repère de référence)
        self.placed = False      # position connue (auto ou manuelle)
        self.manual = False      # position modifiée à la main par l'utilisateur
        self.status = 'pending'
        self._mm_path = os.path.join(cache_dir, f'{sid}.npy')
        self._g_path = os.path.join(cache_dir, f'{sid}_g.npy')

    def full(self):
        return np.load(self._mm_path, mmap_mode='r')

    def gray_full(self):
        return np.load(self._g_path, mmap_mode='r')

    def load(self):
        img, meta = read_image(self.path)
        self.meta = meta
        self.gray = bool(meta.get('gray'))
        if img.ndim == 2:
            g = img
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        else:
            g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        if g.dtype == np.uint16:
            g = (g >> 8).astype(np.uint8)
        np.save(self._g_path, g)
        del g
        self.h, self.w = img.shape[:2]
        self.dtype = str(img.dtype)
        np.save(self._mm_path, img)
        self.s = min(1.0, FEAT_MAX / float(max(self.w, self.h)))
        small = cv2.resize(img, (max(1, round(self.w * self.s)), max(1, round(self.h * self.s))),
                           interpolation=cv2.INTER_AREA)
        del img
        if small.dtype == np.uint16:
            small = (small >> 8).astype(np.uint8)
        self.small = small
        ts = min(1.0, 1400.0 / max(self.w, self.h))
        th = cv2.resize(small, (max(1, round(self.w * ts)), max(1, round(self.h * ts))),
                        interpolation=cv2.INTER_AREA)
        self.thumb_scale = th.shape[1] / float(self.w)
        ok, buf = cv2.imencode('.jpg', th, [cv2.IMWRITE_JPEG_QUALITY, 85])
        self.thumb_jpeg = buf.tobytes()

    def compute_features(self):
        if self.des is not None:
            return
        gray = cv2.cvtColor(self.small, cv2.COLOR_BGR2GRAY)
        sift = cv2.SIFT_create(contrastThreshold=0.02)
        kps = sift.detect(gray, None)
        # répartition homogène : chaque zone (y compris les bords, souvent
        # seuls à se recouvrir) garde ses meilleurs points
        if len(kps) > 16000:
            g = 8
            h, w = gray.shape
            cells = {}
            for k in kps:
                c = (min(g - 1, int(k.pt[1] * g / h)), min(g - 1, int(k.pt[0] * g / w)))
                cells.setdefault(c, []).append(k)
            per = 16000 // (g * g)
            sel = []
            for lst in cells.values():
                lst.sort(key=lambda k: -k.response)
                sel += lst[:per]
            kps = sel
        kps, des = sift.compute(gray, kps)
        if des is None or len(kps) == 0:
            self.pts = np.zeros((0, 2), np.float64)
            self.des = np.zeros((0, 128), np.float32)
            return
        p = np.float64([k.pt for k in kps])
        self.pts = (p + 0.5) / self.s - 0.5
        self.des = des.astype(np.float32)

    def corners(self):
        return np.array([[-0.5, -0.5], [self.w - 0.5, -0.5], [-0.5, self.h - 0.5], [self.w - 0.5, self.h - 0.5]])

    def remove_cache(self):
        for p in (self._mm_path, self._g_path):
            try:
                os.remove(p)
            except OSError:
                pass


# --------------------------------------------------------------------------
# Alignement
# --------------------------------------------------------------------------

class Edge:
    def __init__(self, i, j, M, pi, pj):
        self.i, self.j = i, j
        self.M = M            # 3x3, coords i -> coords j
        self.pi = pi          # correspondances (coords pleine résolution)
        self.pj = pj
        self.dense = False
        self.rms = None
        self.rms_affine = None


def _plausible(M):
    sc, ang, aniso = affine_params(M)
    return 0.6 < sc < 1.6 and aniso < 1.2


def _knn(a_des, b_des, ratio):
    if len(a_des) < 8 or len(b_des) < 8:
        return np.zeros(0, int), np.zeros(0, int)
    matcher = cv2.FlannBasedMatcher(dict(algorithm=1, trees=5), dict(checks=64))
    knn = matcher.knnMatch(a_des, b_des, k=2)
    qi, ti = [], []
    for m in knn:
        if len(m) == 2 and m[0].distance < ratio * m[1].distance:
            qi.append(m[0].queryIdx)
            ti.append(m[0].trainIdx)
    return np.array(qi, int), np.array(ti, int)


def _hypotheses(pa, pb, thr, max_h=4, min_inl=12):
    """RANSAC séquentiel : plusieurs transformations candidates (la plus forte
    n'est pas toujours la bonne, ex. deux titres identiques sur deux pages)."""
    hyps = []
    idx = np.arange(len(pa))
    for _ in range(max_h):
        if len(idx) < min_inl:
            break
        M, inl = cv2.estimateAffine2D(pa[idx], pb[idx], method=cv2.RANSAC, ransacReprojThreshold=thr,
                                      maxIters=10000, confidence=0.999, refineIters=20)
        if M is None:
            break
        inl = inl.ravel().astype(bool)
        if inl.sum() < min_inl:
            break
        M = to33(M)
        hyps.append(M)
        r = np.linalg.norm(apply(M, pa[idx]) - pb[idx], axis=1)
        idx = idx[r >= 2.5 * thr]
    return hyps


def photometric_score(a, b, M):
    """Superpose réellement b sur a (basse résolution) et mesure la concordance
    des détails dans la zone commune. Retourne (score NCC, fraction de recouvrement)."""
    sa, sb = a.s, b.s
    # petit(a) -> plein(a) -> plein(b) -> petit(b)
    Pa = np.array([[1 / sa, 0, 0.5 / sa - 0.5], [0, 1 / sa, 0.5 / sa - 0.5], [0, 0, 1]])
    Qb = np.array([[sb, 0, 0.5 * sb - 0.5], [0, sb, 0.5 * sb - 0.5], [0, 0, 1]])
    A = Qb @ M @ Pa
    ga = cv2.cvtColor(a.small, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gb = cv2.cvtColor(b.small, cv2.COLOR_BGR2GRAY).astype(np.float32)
    h, w = ga.shape
    wb = cv2.warpAffine(gb, A[:2], (w, h), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                        borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    vb = cv2.warpAffine(np.full(gb.shape, 255, np.uint8), A[:2], (w, h),
                        flags=cv2.INTER_NEAREST | cv2.WARP_INVERSE_MAP, borderValue=0)
    vb = cv2.erode(vb, np.ones((9, 9), np.uint8)) > 0
    va = np.zeros((h, w), bool)
    va[4:-4, 4:-4] = True
    ov = va & vb
    frac = ov.sum() / float(min(h * w, gb.size))
    if ov.sum() < 400:
        return -1.0, frac
    ha = ga - cv2.GaussianBlur(ga, (0, 0), 3)
    hb = wb - cv2.GaussianBlur(wb, (0, 0), 3)
    x, y = ha[ov], hb[ov]
    tex = (np.abs(x) > 2) | (np.abs(y) > 2)
    if tex.sum() < 200:
        return 0.0, frac
    x, y = x[tex], y[tex]
    ncc = float((x * y).sum() / math.sqrt((x * x).sum() * (y * y).sum() + 1e-9))
    return ncc, frac


def _same(M1, M2, a):
    c = a.corners()
    return np.abs(apply(M1, c) - apply(M2, c)).max() < 0.02 * math.hypot(a.w, a.h)


def match_pair(a, b, hint_M=None, ratio=0.8):
    """Correspondance grossière a -> b. Plusieurs hypothèses sont générées
    (image entière + bandes de bord) puis vérifiées en superposant les pixels."""
    if len(a.des) < 8 or len(b.des) < 8:
        return None
    thr = 3.0 / min(a.s, b.s)
    cands = []
    qi, ti = _knn(a.des, b.des, ratio)
    pa_all, pb_all = a.pts[qi], b.pts[ti]
    if hint_M is not None and len(pa_all):
        pred = apply(hint_M, pa_all)
        tol = 0.22 * math.hypot(max(a.w, b.w), max(a.h, b.h))
        keep = np.linalg.norm(pred - pb_all, axis=1) < tol
        pa_all, pb_all = pa_all[keep], pb_all[keep]
    if len(pa_all) >= 12:
        cands += _hypotheses(pa_all, pb_all, thr)
    # recouvrements étroits : bord contre bord (numérisations par morceaux)
    if hint_M is None:
        def strips(s):
            f = 0.3
            return [s.pts[:, 0] < f * s.w, s.pts[:, 0] > (1 - f) * s.w,
                    s.pts[:, 1] < f * s.h, s.pts[:, 1] > (1 - f) * s.h]
        sa_, sb_ = strips(a), strips(b)
        for ma in sa_:
            ia = np.nonzero(ma)[0]
            if len(ia) < 12:
                continue
            for mb in sb_:
                ib = np.nonzero(mb)[0]
                if len(ib) < 12:
                    continue
                q, t = _knn(a.des[ia], b.des[ib], ratio)
                if len(q) < 12:
                    continue
                cands += _hypotheses(a.pts[ia[q]], b.pts[ib[t]], thr, max_h=2)
    # dédoublonnage + vérification
    uniq = []
    for M in cands:
        if not _plausible(M):
            continue
        if any(_same(M, U, a) for U in uniq):
            continue
        uniq.append(M)
    best = None
    for M in uniq:
        # tous les appariements compatibles avec cette hypothèse
        r = np.linalg.norm(apply(M, pa_all) - pb_all, axis=1) if len(pa_all) else np.zeros(0)
        inl = r < thr
        n = int(inl.sum())
        if n < 12:
            continue
        spread = np.ptp(pa_all[inl], axis=0)
        if spread.min() < 0.012 * min(a.w, a.h):
            continue
        ncc, frac = photometric_score(a, b, M)
        if ncc < 0.45:
            continue
        score = ncc + 0.05 * math.log(n)
        if best is None or score > best[0]:
            best = (score, M, pa_all[inl], pb_all[inl], ncc)
    if best is None:
        return None
    _, M, pi, pj, ncc = best
    M2, _ = cv2.estimateAffine2D(pi, pj, method=cv2.LMEDS)
    if M2 is not None and _plausible(M2):
        M = to33(M2)
    e = Edge(a.id, b.id, M, pi, pj)
    e.ncc = ncc
    return e


def dense_match(a, b, edge, spacing=None, half=40, R=18):
    """Appariement dense sub-pixel à pleine résolution dans la zone de recouvrement."""
    M = edge.M
    ga, gb = a.gray_full(), b.gray_full()
    sp = spacing or int(max(60, min(a.w, a.h, b.w, b.h) / 55))
    m = half + R + 6
    xs = np.arange(m, a.w - m, sp)
    ys = np.arange(m, a.h - m, sp)
    if len(xs) == 0 or len(ys) == 0:
        return edge
    GX, GY = np.meshgrid(xs, ys)
    P = np.stack([GX.ravel(), GY.ravel()], 1).astype(float)
    Q = apply(M, P)
    mb = (half + R) * 1.5 + 6
    ok = (Q[:, 0] > mb) & (Q[:, 1] > mb) & (Q[:, 0] < b.w - mb) & (Q[:, 1] < b.h - mb)
    P = P[ok]
    W = 2 * (half + R)
    out_a, out_b = [], []
    tex_thr = 7.0
    for x0, y0 in P.astype(int):
        tpl = np.asarray(ga[y0 - half:y0 + half, x0 - half:x0 + half], dtype=np.float32)
        if tpl.std() < tex_thr:
            continue
        ox, oy = x0 - half - R, y0 - half - R
        A = M @ np.array([[1, 0, ox], [0, 1, oy], [0, 0, 1]], float)
        cs = apply(A, [[0, 0], [W, 0], [0, W], [W, W]])
        cx0, cy0 = np.floor(cs.min(axis=0)).astype(int) - 3
        cx1, cy1 = np.ceil(cs.max(axis=0)).astype(int) + 3
        if cx0 < 0 or cy0 < 0 or cx1 > b.w or cy1 > b.h:
            continue
        crop = np.asarray(gb[cy0:cy1, cx0:cx1], dtype=np.float32)
        A2 = A.copy()
        A2[0, 2] -= cx0
        A2[1, 2] -= cy0
        win = cv2.warpAffine(crop, A2[:2], (W, W), flags=cv2.INTER_CUBIC | cv2.WARP_INVERSE_MAP,
                             borderMode=cv2.BORDER_REPLICATE)
        res = cv2.matchTemplate(win, tpl, cv2.TM_CCOEFF_NORMED)
        _, mv, _, ml = cv2.minMaxLoc(res)
        if mv < 0.8:
            continue
        mx, my = ml
        if mx <= 0 or my <= 0 or mx >= res.shape[1] - 1 or my >= res.shape[0] - 1:
            continue
        # unicité du pic (évite les confusions sur du texte répétitif)
        r2 = res.copy()
        r2[max(0, my - 3):my + 4, max(0, mx - 3):mx + 4] = -1
        if r2.max() > mv - 0.08:
            continue

        def parab(m1, c0, p1):
            d = (m1 - 2 * c0 + p1)
            return 0.0 if abs(d) < 1e-9 else 0.5 * (m1 - p1) / d
        dx = mx + parab(res[my, mx - 1], res[my, mx], res[my, mx + 1]) - R
        dy = my + parab(res[my - 1, mx], res[my, mx], res[my + 1, mx]) - R
        Pp = np.array([x0 - 0.5, y0 - 0.5])     # centre du patch
        Qp = apply(A, [[half + R + dx - 0.5, half + R + dy - 0.5]])[0]
        out_a.append(Pp)
        out_b.append(Qp)
    if len(out_a) >= 15:
        pa, pb = np.array(out_a), np.array(out_b)
        # rejet grossier des faux appariements (texte répétitif) : écart à un affine robuste
        M2, inl = cv2.estimateAffine2D(pa, pb, method=cv2.RANSAC, ransacReprojThreshold=12.0)
        if M2 is not None and inl.sum() >= 12:
            inl = inl.ravel().astype(bool)
            edge.pi, edge.pj = pa[inl], pb[inl]
            edge.M = to33(M2)
            edge.dense = True
    return edge


def solve_affine(ids, anchors, edges):
    """Moindres carrés : T_k affines telles que T_i(p) = T_j(q) pour toutes les correspondances."""
    var = [k for k in ids if k not in anchors]
    if not var:
        return dict(anchors)
    col = {k: 6 * n for n, k in enumerate(var)}
    S = 1e-3
    nv = 6 * len(var)
    rows, rhs = [], []
    for e in edges:
        if e.i not in ids or e.j not in ids:
            continue
        w = 1.0 / math.sqrt(max(1, len(e.pi)))
        P = e.pi * S
        Q = e.pj * S
        n = len(P)
        for comp in (0, 1):
            Ablk = np.zeros((n, nv))
            c = np.zeros(n)
            for k, pts, sign in ((e.i, P, 1.0), (e.j, Q, -1.0)):
                if k in anchors:
                    T = anchors[k]
                    c -= sign * (T[comp, 0] * pts[:, 0] + T[comp, 1] * pts[:, 1] + T[comp, 2] * S)
                else:
                    o = col[k] + 3 * comp
                    Ablk[:, o] += sign * pts[:, 0]
                    Ablk[:, o + 1] += sign * pts[:, 1]
                    Ablk[:, o + 2] += sign
            rows.append(Ablk * w)
            rhs.append(c * w)
    A = np.vstack(rows)
    b = np.concatenate(rhs)
    sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    out = dict(anchors)
    for k in var:
        o = col[k]
        a0, a1, a2, b0, b1, b2 = sol[o:o + 6]
        out[k] = np.array([[a0, a1, a2 / S], [b0, b1, b2 / S], [0, 0, 1]])
    return out


def components(ids, edges):
    adj = {k: set() for k in ids}
    for e in edges:
        adj[e.i].add(e.j)
        adj[e.j].add(e.i)
    seen, comps = set(), []
    for k in ids:
        if k in seen:
            continue
        stack, comp = [k], []
        seen.add(k)
        while stack:
            u = stack.pop()
            comp.append(u)
            for v in adj[u]:
                if v not in seen:
                    seen.add(v)
                    stack.append(v)
        comps.append(comp)
    return comps


def _mesh_sample(mesh, x, y):
    """Déplacement (dx, dy) du maillage aux points (x, y) (tableaux float32)."""
    u = ((x - mesh['ox']) / mesh['G']).astype(np.float32)
    v = ((y - mesh['oy']) / mesh['G']).astype(np.float32)
    d = cv2.remap(mesh['D'], u, v, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    return d[..., 0], d[..., 1]


def solve_mesh(srcs, edges, Ts, G, lam_bend=1.0, lam_memb=0.05, lam_zero=0.002, progress=None):
    """Déformation locale lisse de chaque image (papier gondolé, étirement du scanner).
    Inconnues : déplacements aux nœuds d'une grille de pas G (repère de référence).
    Contraintes : Y_i + d_i(Y_i) = Y_j + d_j(Y_j), avec lissage (énergie de flexion)."""
    try:
        import scipy.sparse as sp
        import scipy.sparse.linalg as spla
    except Exception:
        return None
    # grille commune
    allc = np.vstack([apply(Ts[s.id], s.corners()) for s in srcs])
    O = allc.min(axis=0) - G
    info = {}
    nvar = 0
    for s in srcs:
        c = apply(Ts[s.id], s.corners())
        ix0 = int(math.floor((c[:, 0].min() - O[0]) / G)) - 1
        iy0 = int(math.floor((c[:, 1].min() - O[1]) / G)) - 1
        ix1 = int(math.ceil((c[:, 0].max() - O[0]) / G)) + 1
        iy1 = int(math.ceil((c[:, 1].max() - O[1]) / G)) + 1
        nx, ny = ix1 - ix0 + 1, iy1 - iy0 + 1
        info[s.id] = (ix0, iy0, nx, ny, nvar)
        nvar += nx * ny

    def bil(sid, Y):
        ix0, iy0, nx, ny, base = info[sid]
        u = (Y[:, 0] - O[0]) / G - ix0
        v = (Y[:, 1] - O[1]) / G - iy0
        u = np.clip(u, 0, nx - 1.0001)
        v = np.clip(v, 0, ny - 1.0001)
        iu, iv = np.floor(u).astype(int), np.floor(v).astype(int)
        fu, fv = u - iu, v - iv
        idx = [base + iv * nx + iu, base + iv * nx + iu + 1, base + (iv + 1) * nx + iu, base + (iv + 1) * nx + iu + 1]
        wts = [(1 - fu) * (1 - fv), fu * (1 - fv), (1 - fu) * fv, fu * fv]
        return idx, wts

    data = []
    for e in edges:
        Yi = apply(Ts[e.i], e.pi)
        Yj = apply(Ts[e.j], e.pj)
        data.append((e, Yi, Yj, np.ones(len(Yi), bool)))

    def build(active):
        r_i, c_i, v_i = [], [], []
        rhs = []
        row = 0
        for (e, Yi, Yj, keep) in active:
            Yi_, Yj_ = Yi[keep], Yj[keep]
            n = len(Yi_)
            if n == 0:
                continue
            idx_i, w_i = bil(e.i, Yi_)
            idx_j, w_j = bil(e.j, Yj_)
            rr = row + np.arange(n)
            for idx, w in zip(idx_i, w_i):
                r_i.append(rr); c_i.append(idx); v_i.append(w)
            for idx, w in zip(idx_j, w_j):
                r_i.append(rr); c_i.append(idx); v_i.append(-w)
            rhs.append(Yj_ - Yi_)
            row += n
        ndata = row
        # régularisation
        for s in srcs:
            ix0, iy0, nx, ny, base = info[s.id]
            ids = base + np.arange(nx * ny).reshape(ny, nx)
            # flexion
            for a, b_, c in ((ids[:, :-2], ids[:, 1:-1], ids[:, 2:]), (ids[:-2, :], ids[1:-1, :], ids[2:, :])):
                n = a.size
                if n == 0:
                    continue
                rr = row + np.arange(n)
                for idx, w in ((a, 1.0), (b_, -2.0), (c, 1.0)):
                    r_i.append(rr); c_i.append(idx.ravel()); v_i.append(np.full(n, w * lam_bend))
                rhs.append(np.zeros((n, 2)))
                row += n
            # membrane
            for a, b_ in ((ids[:, :-1], ids[:, 1:]), (ids[:-1, :], ids[1:, :])):
                n = a.size
                if n == 0:
                    continue
                rr = row + np.arange(n)
                r_i.append(rr); c_i.append(a.ravel()); v_i.append(np.full(n, lam_memb))
                r_i.append(rr); c_i.append(b_.ravel()); v_i.append(np.full(n, -lam_memb))
                rhs.append(np.zeros((n, 2)))
                row += n
            n = ids.size
            rr = row + np.arange(n)
            r_i.append(rr); c_i.append(ids.ravel()); v_i.append(np.full(n, lam_zero))
            rhs.append(np.zeros((n, 2)))
            row += n
        A = sp.csr_matrix((np.concatenate(v_i), (np.concatenate(r_i), np.concatenate(c_i))), shape=(row, nvar))
        B = np.vstack(rhs)
        return A, B, ndata

    sol = None
    for it in range(3):
        A, B, nd = build(data)
        AtA = (A.T @ A).tocsc()
        AtB = A.T @ B
        sol = np.column_stack([spla.spsolve(AtA, AtB[:, 0]), spla.spsolve(AtA, AtB[:, 1])])
        # rejet des correspondances aberrantes
        new = []
        changed = False
        for (e, Yi, Yj, keep) in data:
            idx_i, w_i = bil(e.i, Yi)
            idx_j, w_j = bil(e.j, Yj)
            di = sum(sol[idx] * w[:, None] for idx, w in zip(idx_i, w_i))
            dj = sum(sol[idx] * w[:, None] for idx, w in zip(idx_j, w_j))
            r = np.linalg.norm(Yi + di - Yj - dj, axis=1)
            med = np.median(r[keep]) if keep.any() else 0
            k2 = keep & (r < max(2.0, 4.0 * med))
            if k2.sum() != keep.sum():
                changed = True
            e.rms = float(np.sqrt(np.mean(r[k2] ** 2))) if k2.any() else None
            new.append((e, Yi, Yj, k2))
        data = new
        if not changed:
            break
    meshes = {}
    for s in srcs:
        ix0, iy0, nx, ny, base = info[s.id]
        D = sol[base:base + nx * ny].reshape(ny, nx, 2).astype(np.float32)
        meshes[s.id] = {'ox': float(O[0] + ix0 * G), 'oy': float(O[1] + iy0 * G), 'G': float(G),
                        'D': np.ascontiguousarray(D)}
    return meshes


def align(sources, progress=None, local_warp=True):
    """Calcule la position de toutes les images.
    La première image de la liste sert de référence. Les images déplacées à la main
    servent d'indice quand la détection automatique échoue."""
    progress = progress or Progress()
    srcs = list(sources)
    n = len(srcs)
    byid = {s.id: s for s in srcs}
    ids = [s.id for s in srcs]
    if n == 0:
        return {'edges': [], 'components': 0}
    for k, s in enumerate(srcs):
        progress(0.02 + 0.18 * k / n, tr('analyse', name=s.name))
        s.compute_features()
    pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
    edges = []
    for k, (i, j) in enumerate(pairs):
        a, b = srcs[i], srcs[j]
        progress(0.20 + 0.20 * k / max(1, len(pairs)), tr('overlaps', a=a.name, b=b.name))
        e = match_pair(a, b)
        if e is None and a.placed and b.placed and (a.manual or b.manual):
            hint = np.linalg.inv(b.T) @ a.T
            e = match_pair(a, b, hint_M=hint, ratio=0.85)
        if e is not None:
            edges.append(e)
    for k, e in enumerate(edges):
        progress(0.40 + 0.35 * k / max(1, len(edges)),
                 tr('fine', a=byid[e.i].name, b=byid[e.j].name))
        dense_match(byid[e.i], byid[e.j], e)

    progress(0.76, tr('global'))
    ref = srcs[0]
    ref_T = np.eye(3)
    Ts = {}
    for _ in range(max(1, len(edges))):
        comps = components(ids, edges)
        Ts = {}
        for comp in comps:
            if ref.id in comp:
                anchors = {ref.id: ref_T}
            else:
                cand = [k for k in comp if byid[k].manual] or [k for k in comp if byid[k].placed]
                anchor = cand[0] if cand else comp[0]
                anchors = {anchor: byid[anchor].T}
            cedges = [e for e in edges if e.i in comp]
            Ts.update(solve_affine(comp, anchors, cedges))
        worst, bad = 0.0, None
        for e in edges:
            r = np.linalg.norm(apply(Ts[e.i], e.pi) - apply(Ts[e.j], e.pj), axis=1)
            e.rms_affine = float(np.sqrt(np.mean(r ** 2)))
            med = float(np.median(r))
            if med > worst:
                worst, bad = med, e
        if bad is not None and worst > 20.0 and len(edges) > 1:
            edges.remove(bad)     # paire incohérente avec les autres : on l'ignore
            continue
        break

    comps = components(ids, edges)
    main = next(c for c in comps if ref.id in c)
    for s in srcs:
        s.T = Ts[s.id]
        s.mesh = None
        if s.id in main:
            s.placed = True
            s.status = 'ref' if s is ref else 'aligned'
            s.manual = False
        elif s.manual:
            s.status = 'manual'
        else:
            s.placed = False
            s.status = 'notfound'

    placed = [s for s in srcs if s.placed]
    pedges = [e for e in edges if byid[e.i].placed and byid[e.j].placed]
    if local_warp and pedges:
        progress(0.85, tr('warp'))
        G = max(120.0, min(min(s.w, s.h) for s in placed) / 28.0)
        meshes = solve_mesh(placed, pedges, Ts, G)
        if meshes:
            for s in placed:
                s.mesh = meshes[s.id]
    report = []
    for e in edges:
        report.append({'a': byid[e.i].name, 'b': byid[e.j].name, 'points': int(len(e.pi)),
                       'ecart_affine_px': round(e.rms_affine or 0, 2),
                       'ecart_final_px': round(e.rms if e.rms is not None else (e.rms_affine or 0), 2)})
    progress(1.0, tr('align_done'))
    return {'edges': report, 'components': len(comps)}


# --------------------------------------------------------------------------
# Composition
# --------------------------------------------------------------------------

class Plan:
    pass


def inverse_map(plan, s, X, Y):
    """Coordonnées canevas pleine résolution -> coordonnées pixel de l'image source."""
    Ti = plan.Tci
    Zx = (Ti[0, 0] * X + Ti[0, 1] * Y + Ti[0, 2]).astype(np.float32)
    Zy = (Ti[1, 0] * X + Ti[1, 1] * Y + Ti[1, 2]).astype(np.float32)
    if s.mesh is not None:
        Yx, Yy = Zx, Zy
        for _ in range(3):
            dx, dy = _mesh_sample(s.mesh, Yx, Yy)
            Yx, Yy = Zx - dx, Zy - dy
        Zx, Zy = Yx, Yy
    Si = np.linalg.inv(s.T)
    px = Si[0, 0] * Zx + Si[0, 1] * Zy + Si[0, 2]
    py = Si[1, 0] * Zx + Si[1, 1] * Zy + Si[1, 2]
    return px.astype(np.float32), py.astype(np.float32)


def warp_source(plan, s, ox, oy, w, h, scale, small, interp=cv2.INTER_CUBIC, stride=8):
    """Projette l'image s dans une fenêtre de sortie (w x h) à l'échelle `scale`
    dont le coin haut-gauche est le pixel (ox, oy) de la sortie à cette échelle.
    Retourne (image, masque_valide float32) ou None."""
    nx, ny = w // stride + 2, h // stride + 2
    gu = np.arange(nx, dtype=np.float64) * stride
    gv = np.arange(ny, dtype=np.float64) * stride
    GU, GV = np.meshgrid(gu, gv)
    X = (GU + ox + 0.5) / scale - 0.5
    Y = (GV + oy + 0.5) / scale - 0.5
    px, py = inverse_map(plan, s, X, Y)
    if small:
        img = s.small
        px = (px + 0.5) * s.s - 0.5
        py = (py + 0.5) * s.s - 0.5
        sw, sh = img.shape[1], img.shape[0]
        border = max(1.0, plan.border * s.s)
    else:
        img = s.full()
        sw, sh = s.w, s.h
        border = float(plan.border)
    if px.max() < 0 or py.max() < 0 or px.min() > sw or py.min() > sh:
        return None
    mu, mv = np.meshgrid(np.arange(w, dtype=np.float32) / stride, np.arange(h, dtype=np.float32) / stride)
    mapx = cv2.remap(px, mu, mv, cv2.INTER_LINEAR)
    mapy = cv2.remap(py, mu, mv, cv2.INTER_LINEAR)
    del mu, mv
    valid = (mapx >= border - 0.5) & (mapx <= sw - 0.5 - border) & (mapy >= border - 0.5) & (mapy <= sh - 0.5 - border)
    if not valid.any():
        return None
    vx = mapx[valid]
    vy = mapy[valid]
    x0 = max(0, int(math.floor(vx.min())) - 4)
    y0 = max(0, int(math.floor(vy.min())) - 4)
    x1 = min(sw, int(math.ceil(vx.max())) + 5)
    y1 = min(sh, int(math.ceil(vy.max())) + 5)
    del vx, vy
    crop = np.ascontiguousarray(img[y0:y1, x0:x1])
    mapx -= x0
    mapy -= y0
    if max(crop.shape[:2]) >= 32000:
        # limite d'OpenCV : traitement en deux moitiés
        out = np.empty((h, w, 3), crop.dtype)
        hh = h // 2
        for a, b in ((0, hh), (hh, h)):
            sub = warp_source(plan, s, ox, oy + a, w, b - a, scale, small, interp, stride)
            out[a:b] = sub[0] if sub is not None else 0
        return out, valid.astype(np.float32)
    warped = cv2.remap(crop, mapx, mapy, interp, borderMode=cv2.BORDER_REPLICATE)
    return warped, valid.astype(np.float32)


def _largest_rectangle(mask):
    """Plus grand rectangle aligné entièrement dans mask (bool)."""
    h, w = mask.shape
    heights = np.zeros(w, int)
    best = (0, 0, 0, 0, 0)
    for y in range(h):
        heights = np.where(mask[y], heights + 1, 0)
        stack = []
        for x in range(w + 1):
            cur = heights[x] if x < w else 0
            start = x
            while stack and stack[-1][1] >= cur:
                sx, sh = stack.pop()
                area = sh * (x - sx)
                if area > best[0]:
                    best = (area, sx, y - sh + 1, x - sx, sh)
                start = sx
            stack.append((start, cur))
    _, x, y, rw, rh = best
    return x, y, rw, rh


def make_plan(sources, view_rot=0, rotation_deg=0.0, crop=False, border_px=None, seam='graphcut',
              progress=None):
    progress = progress or Progress()
    srcs = [s for s in sources if s.placed]
    if not srcs:
        raise RuntimeError(tr('none_placed'))
    plan = Plan()
    plan.srcs = srcs
    R = rot_matrix(90 * view_rot + rotation_deg)
    pts = []
    for s in srcs:
        c = s.corners()
        if s.mesh is not None:
            # échantillonne le contour pour tenir compte de la déformation
            t = np.linspace(0, 1, 9)
            w, h = s.w, s.h
            edge = np.vstack([np.c_[t * w, t * 0], np.c_[t * w, t * 0 + h], np.c_[t * 0, t * h], np.c_[t * 0 + w, t * h]]) - 0.5
            Y = apply(s.T, edge)
            dx, dy = _mesh_sample(s.mesh, Y[:, 0].astype(np.float32)[None], Y[:, 1].astype(np.float32)[None])
            Y = Y + np.c_[dx.ravel(), dy.ravel()]
            pts.append(apply(R, Y))
        else:
            pts.append(apply(R @ s.T, c))
    pts = np.vstack(pts)
    x0, y0 = np.floor(pts.min(axis=0) + 0.5)
    x1, y1 = np.ceil(pts.max(axis=0) - 0.5)
    W, H = int(x1 - x0) + 1, int(y1 - y0) + 1
    plan.Tc = np.array([[1, 0, -x0], [0, 1, -y0], [0, 0, 1]]) @ R     # référence -> canevas
    plan.Tci = np.linalg.inv(plan.Tc)
    plan.W, plan.H = W, H
    c = min(min(s.s for s in srcs), LOWRES_MAX / float(max(W, H)))
    plan.c = c
    wl, hl = max(1, int(math.ceil(W * c))), max(1, int(math.ceil(H * c)))
    plan.wl, plan.hl = wl, hl
    if border_px is None:
        border_px = max(4, int(0.0015 * min(min(s.w, s.h) for s in srcs)))
    plan.border = border_px

    low, valid = [], []
    for k, s in enumerate(srcs):
        progress(0.6 * k / len(srcs), tr('seams_prep'))
        r = warp_source(plan, s, 0, 0, wl, hl, c, True, cv2.INTER_LINEAR)
        if r is None:
            low.append(np.zeros((hl, wl, 3), np.uint8))
            valid.append(np.zeros((hl, wl), np.uint8))
        else:
            low.append(r[0])
            valid.append((r[1] > 0).astype(np.uint8) * 255)

    # compensation d'exposition (gain par canal, méthode de Brown & Lowe)
    n = len(srcs)
    gains = np.ones((n, 3))
    if n > 1:
        sig_n, sig_g = 10.0, 0.1
        Nij = np.zeros((n, n))
        Iij = np.zeros((n, n, 3))
        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                ov = (valid[i] > 0) & (valid[j] > 0)
                cnt = int(ov.sum())
                if cnt < 100:
                    continue
                Nij[i, j] = cnt
                Iij[i, j] = low[i][ov].reshape(-1, 3).astype(np.float64).mean(axis=0)
        for ch in range(3):
            A = np.zeros((n, n))
            b = np.zeros(n)
            for i in range(n):
                for j in range(n):
                    if Nij[i, j] == 0:
                        continue
                    A[i, i] += Nij[i, j] * (Iij[i, j, ch] ** 2 / sig_n ** 2 + 1 / sig_g ** 2)
                    A[i, j] -= Nij[i, j] * Iij[i, j, ch] * Iij[j, i, ch] / sig_n ** 2
                    b[i] += Nij[i, j] / sig_g ** 2
                if A[i, i] == 0:
                    A[i, i] = 1
                    b[i] = 1
            try:
                gains[:, ch] = np.linalg.solve(A, b)
            except np.linalg.LinAlgError:
                pass
        gains = np.clip(gains, 0.7, 1.4)
    plan.gains = {s.id: gains[k] for k, s in enumerate(srcs)}

    progress(0.7, tr('seams_calc'))
    dists = [cv2.distanceTransform((v > 0).astype(np.uint8), cv2.DIST_L2, 5) for v in valid]
    stack = np.stack(dists, axis=0)
    label = np.argmax(stack, axis=0)
    anyv = stack.max(axis=0) > 0
    del stack, dists
    masks = [((label == k) & anyv).astype(np.uint8) * 255 for k in range(n)]
    if seam == 'graphcut' and n > 1:
        try:
            masks = _graphcut_scaled(low, valid, masks, gains, label, anyv)
        except Exception:
            pass
    plan.union = anyv
    plan.masks = masks
    if crop:
        sub = 4 if max(wl, hl) > 1400 else 1
        um = cv2.resize(anyv.astype(np.uint8), (max(1, wl // sub), max(1, hl // sub)),
                        interpolation=cv2.INTER_AREA) == 1
        rx, ry, rw, rh = _largest_rectangle(um)
        rx, ry, rw, rh = rx * sub, ry * sub, rw * sub, rh * sub
        fx0 = int(math.ceil((rx + 1) / c))
        fy0 = int(math.ceil((ry + 1) / c))
        fx1 = min(W, int(math.floor((rx + rw - 1) / c)))
        fy1 = min(H, int(math.floor((ry + rh - 1) / c)))
        plan.region = (fx0, fy0, max(1, fx1 - fx0), max(1, fy1 - fy0))
    else:
        plan.region = (0, 0, W, H)
    progress(1.0, tr('seams_ok'))
    return plan


GRAPHCUT_MAX = 1000


def _graphcut_scaled(low, valid, masks, gains, label, anyv):
    """Graph-cut sur une version réduite (rapide), puis remise à l'échelle."""
    hl, wl = valid[0].shape
    f = min(1.0, GRAPHCUT_MAX / float(max(wl, hl)))
    if f >= 0.999:
        return _graphcut_refine(low, valid, masks, gains)
    sz = (max(1, int(round(wl * f))), max(1, int(round(hl * f))))
    low_s = [cv2.resize(im, sz, interpolation=cv2.INTER_AREA) for im in low]
    val_s = [cv2.erode(cv2.resize(v, sz, interpolation=cv2.INTER_NEAREST), np.ones((3, 3), np.uint8))
             for v in valid]
    m_s = [cv2.resize(m, sz, interpolation=cv2.INTER_NEAREST) for m in masks]
    res = _graphcut_refine(low_s, val_s, m_s, gains)
    n = len(low)
    # étiquette réduite -> pleine taille basse résolution
    lab_s = np.full(sz[::-1], -1, np.int32)
    for k in range(n):
        lab_s[res[k] > 0] = k
    lab = cv2.resize(lab_s, (wl, hl), interpolation=cv2.INTER_NEAREST)
    out = []
    for k in range(n):
        v = valid[k] > 0
        out.append(v & (lab == k))
    cover = np.zeros((hl, wl), bool)
    for m in out:
        cover |= m
    for k in range(n):
        out[k] |= (~cover) & (label == k) & anyv
    claimed = np.zeros((hl, wl), bool)
    final = []
    for k in range(n):
        m = out[k] & ~claimed
        claimed |= m
        final.append(m.astype(np.uint8) * 255)
    return final


def _graphcut_refine(low, valid, masks, gains):
    """Coutures passant par les zones où les images se ressemblent le plus
    (fond de papier) plutôt qu'au travers du texte ou des dessins."""
    n = len(low)
    imgs, corners, ms, boxes = [], [], [], []
    for k in range(n):
        ys, xs = np.nonzero(valid[k])
        if len(xs) == 0:
            boxes.append(None)
            imgs.append(np.zeros((1, 1, 3), np.float32))
            corners.append((0, 0))
            ms.append(np.zeros((1, 1), np.uint8))
            continue
        x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
        boxes.append((x0, y0, x1, y1))
        im = low[k][y0:y1, x0:x1].astype(np.float32) * gains[k][None, None, :].astype(np.float32)
        imgs.append(im)
        corners.append((int(x0), int(y0)))
        # départ : partition de Voronoï ; le graph-cut ne re-tranche que les recouvrements
        ms.append(np.ascontiguousarray(valid[k][y0:y1, x0:x1]))
    finder = cv2.detail_GraphCutSeamFinder('COST_COLOR_GRAD')
    umasks = [cv2.UMat(m) for m in ms]
    res = finder.find(imgs, corners, umasks)
    out = []
    H, W = valid[0].shape
    for k in range(n):
        full = np.zeros((H, W), np.uint8)
        if boxes[k] is not None:
            x0, y0, x1, y1 = boxes[k]
            r = res[k].get() if isinstance(res[k], cv2.UMat) else np.asarray(res[k])
            full[y0:y1, x0:x1] = r
        out.append(full)
    cover = np.zeros(valid[0].shape, bool)
    for m in out:
        cover |= m > 0
    for k in range(n):
        hole = (~cover) & (masks[k] > 0)
        out[k][hole] = 255
    # chaque pixel n'appartient qu'à une image
    claimed = np.zeros(valid[0].shape, bool)
    for k in range(n):
        m = (out[k] > 0) & ~claimed
        claimed |= m
        out[k] = m.astype(np.uint8) * 255
    return out


def render(plan, out, progress=None, sigma_n=2.0, sigma_w=110.0, background=(255, 255, 255),
           tile=TILE, preview=False):
    """Rendu par tuiles avec fusion en deux bandes :
      - hautes fréquences (détails) : transition très étroite le long de la couture,
      - basses fréquences (teinte du papier, éclairage) : transition large et douce.
    out : tableau (h, w, 3) RGB (memmap possible)."""
    progress = progress or Progress()
    srcs = plan.srcs
    if preview:
        scale = plan.c
        rx, ry, OW, OH = 0, 0, plan.wl, plan.hl
    else:
        scale = 1.0
        rx, ry, OW, OH = plan.region
    sig_n = max(0.7, sigma_n * scale, 0.5 * scale / plan.c)
    sig_w = max(3.0, sigma_w * scale)
    margin = int(3 * sig_w) + 8
    maxval = 65535.0 if out.dtype == np.uint16 else 255.0
    bg_bgr = (np.array(background, np.float32) * (maxval / 255.0))[::-1]

    Ln = [m.astype(np.float32) / 255.0 for m in plan.masks]
    Lw = []
    for m in Ln:
        sw = sigma_w * plan.c
        Lw.append(cv2.GaussianBlur(m, (0, 0), sw) if sw > 0.3 else m)
    k_ = plan.c / scale

    tiles = [(tx, ty) for ty in range(0, OH, tile) for tx in range(0, OW, tile)]
    for ti, (tx, ty) in enumerate(tiles):
        progress(ti / len(tiles), tr('tile', i=ti + 1, n=len(tiles)))
        tw, th = min(tile, OW - tx), min(tile, OH - ty)
        ox, oy = rx + tx - margin, ry + ty - margin      # pixel de sortie (à cette échelle)
        EW, EH = tw + 2 * margin, th + 2 * margin
        num_n = np.zeros((EH, EW, 3), np.float32)
        den_n = np.zeros((EH, EW), np.float32)
        num_w = np.zeros((EH, EW, 3), np.float32)
        den_w = np.zeros((EH, EW), np.float32)
        Lmat = np.array([[k_, 0, (ox + 0.5) * k_ - 0.5], [0, k_, (oy + 0.5) * k_ - 0.5]], np.float64)
        for k, s in enumerate(srcs):
            # rapide : la tuile touche-t-elle le masque de cette image ?
            r = warp_source(plan, s, ox, oy, EW, EH, scale, preview,
                            cv2.INTER_LINEAR if preview else cv2.INTER_CUBIC)
            if r is None:
                continue
            warped, v = r
            warped = warped.astype(np.float32)
            if s.dtype == 'uint8' and maxval > 255 and not preview:
                warped *= 257.0
            elif (s.dtype == 'uint16' and maxval == 255.0 and not preview):
                warped /= 257.0
            elif preview and maxval > 255:
                warped *= 257.0
            warped *= plan.gains[s.id][None, None, :].astype(np.float32)
            ln = cv2.warpAffine(Ln[k], Lmat, (EW, EH), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                                borderMode=cv2.BORDER_CONSTANT, borderValue=0)
            lw = cv2.warpAffine(Lw[k], Lmat, (EW, EH), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                                borderMode=cv2.BORDER_CONSTANT, borderValue=0)
            if sig_n > 0.3:
                ln = cv2.GaussianBlur(ln, (0, 0), sig_n)
            wn = v * (ln + 1e-4)
            ww = v * (lw + 1e-4)
            f = max(1, int(sig_w // 6))
            sw2, sh2 = max(1, EW // f), max(1, EH // f)
            sm_i = cv2.resize(warped * v[:, :, None], (sw2, sh2), interpolation=cv2.INTER_AREA)
            sm_v = cv2.resize(v, (sw2, sh2), interpolation=cv2.INTER_AREA)
            sg = sig_w / f
            sm_i = cv2.GaussianBlur(sm_i, (0, 0), sg)
            sm_v = cv2.GaussianBlur(sm_v, (0, 0), sg)
            lowf = sm_i / np.maximum(sm_v, 1e-6)[:, :, None]
            lowf = cv2.resize(lowf, (EW, EH), interpolation=cv2.INTER_LINEAR)
            num_n += (warped - lowf) * wn[:, :, None]
            den_n += wn
            num_w += lowf * ww[:, :, None]
            den_w += ww
            del warped, v, lowf
        core = (slice(margin, margin + th), slice(margin, margin + tw))
        dn = den_n[core]
        res = np.empty((th, tw, 3), np.float32)
        res[:] = bg_bgr
        has = dn > 0
        if has.any():
            blended = num_n[core] / np.maximum(dn, 1e-12)[:, :, None] + \
                num_w[core] / np.maximum(den_w[core], 1e-12)[:, :, None]
            res[has] = blended[has]
        res = np.clip(res + 0.5, 0, maxval).astype(out.dtype)
        out[ty:ty + th, tx:tx + tw] = res[:, :, ::-1]
    progress(1.0, tr('blend_done'))
    return out


def preview_image(plan, max_side=1800, background=(255, 255, 255)):
    out = np.zeros((plan.hl, plan.wl, 3), np.uint8)
    render(plan, out, preview=True, tile=4096, background=background)
    if plan.region != (0, 0, plan.W, plan.H):
        rx, ry, rw, rh = plan.region
        c = plan.c
        out = out[int(ry * c):int(math.ceil((ry + rh) * c)), int(rx * c):int(math.ceil((rx + rw) * c))]
    s = min(1.0, max_side / float(max(out.shape[:2])))
    if s < 1:
        out = cv2.resize(out, (int(out.shape[1] * s), int(out.shape[0] * s)), interpolation=cv2.INTER_AREA)
    return out  # RGB


def merge(sources, out_path, fmt, view_rot=0, rotation_deg=0.0, crop=False, background=(255, 255, 255),
          work_dir=None, progress=None):
    progress = progress or Progress()
    plan = make_plan(sources, view_rot=view_rot, rotation_deg=rotation_deg, crop=crop,
                     progress=progress.sub(0, 0.06))
    srcs = plan.srcs
    rx, ry, OW, OH = plan.region
    out_dtype = np.uint16 if any(s.dtype == 'uint16' for s in srcs) else np.uint8
    if fmt in ('jpg', 'jpeg'):
        out_dtype = np.uint8
    all_gray = all(s.gray for s in srcs)
    own_dir = work_dir is None
    work_dir = work_dir or tempfile.mkdtemp(prefix='photofusion_')
    mm_path = os.path.join(work_dir, 'sortie.raw')
    out = np.memmap(mm_path, dtype=out_dtype, mode='w+', shape=(OH, OW, 3))
    try:
        render(plan, out, progress=progress.sub(0.06, 0.86), background=background)
        out.flush()
        progress(0.87, tr('writing'))
        dpi = srcs[0].meta.get('dpi')
        icc = srcs[0].meta.get('icc')
        data = out[:, :, 1] if all_gray else out
        write_image(out_path, data, fmt, dpi=dpi, icc=icc)
        prev = preview_image(plan, background=background)
    finally:
        del out
        try:
            os.remove(mm_path)
        except OSError:
            pass
        if own_dir:
            shutil.rmtree(work_dir, ignore_errors=True)
    progress(1.0, tr('done'))
    return {'path': out_path, 'width': OW, 'height': OH, 'dpi': srcs[0].meta.get('dpi'), 'preview': prev}


def output_format_for(sources):
    """Même format que les originaux (RAW -> TIFF 16 bits)."""
    e = sources[0].ext if sources else '.tif'
    if e in ('.jpg', '.jpeg', '.jpe'):
        return 'jpg'
    if e == '.png':
        return 'png'
    return 'tif'


# --------------------------------------------------------------------------
# Projet (utilisé par l'interface et la ligne de commande)
# --------------------------------------------------------------------------

class Project:
    def __init__(self, work_dir=None):
        self.work_dir = work_dir or tempfile.mkdtemp(prefix='photofusion_')
        self.cache_dir = os.path.join(self.work_dir, 'cache')
        os.makedirs(self.cache_dir, exist_ok=True)
        self.sources = []
        self._next = 1
        self.lock = threading.RLock()
        self.view_rot = 0          # quarts de tour appliqués à tout l'assemblage
        self.last_report = None

    # -- vue : repère affiché = rotation de vue x repère de référence
    def view_matrix(self):
        return rot_matrix(90 * self.view_rot)

    def view_T(self, s):
        return self.view_matrix() @ s.T

    def add(self, path, name=None):
        with self.lock:
            sid = f'img{self._next}'
            self._next += 1
        s = Source(sid, path, name or os.path.basename(path), self.cache_dir)
        s.load()
        with self.lock:
            self.sources.append(s)
            self._initial_layout(s)
        return s

    def _initial_layout(self, s):
        k = len(self.sources) - 1
        if k == 0:
            s.T = np.eye(3)
            s.placed = True
            s.status = 'ref'
            return
        # à droite des images existantes, dans le repère affiché
        V = self.view_matrix()
        xs = []
        for o in self.sources[:-1]:
            xs.append(apply(V @ o.T, o.corners())[:, 0].max())
        x = max(xs) + 0.05 * max(s.w, s.h)
        top = min(apply(V @ o.T, o.corners())[:, 1].min() for o in self.sources[:-1])
        s.T = np.linalg.inv(V) @ np.array([[1, 0, x], [0, 1, top], [0, 0, 1]], float)
        s.placed = False
        s.status = 'pending'

    def get(self, sid):
        for s in self.sources:
            if s.id == sid:
                return s
        raise KeyError(sid)

    def remove(self, sid):
        with self.lock:
            s = self.get(sid)
            self.sources.remove(s)
            s.remove_cache()
            if self.sources and not self.sources[0].placed:
                self.sources[0].placed = True

    def rotate(self, sid, quarter):
        with self.lock:
            s = self.get(sid)
            if s is self.sources[0]:
                # tourner la référence = tourner tout l'assemblage
                self.view_rot = (self.view_rot + quarter) % 4
                return
            V = self.view_matrix()
            Tv = V @ s.T
            cx, cy = apply(Tv, [[(s.w - 1) / 2, (s.h - 1) / 2]])[0]
            Tv = rot_matrix(90 * quarter, cx, cy) @ Tv
            s.T = np.linalg.inv(V) @ Tv
            s.mesh = None
            s.manual = True
            s.placed = True
            s.status = 'manual'

    def set_view_transform(self, sid, T23):
        with self.lock:
            s = self.get(sid)
            Tv = np.eye(3)
            Tv[:2, :] = np.array(T23, float)
            s.T = np.linalg.inv(self.view_matrix()) @ Tv
            s.mesh = None
            if s is not self.sources[0]:
                s.manual = True
                s.placed = True
                s.status = 'manual'

    def make_reference(self, sid):
        with self.lock:
            s = self.get(sid)
            self.sources.remove(s)
            self.sources.insert(0, s)
            s.placed = True

    def reorder(self, ids):
        with self.lock:
            byid = {s.id: s for s in self.sources}
            self.sources = [byid[i] for i in ids if i in byid] + [s for s in self.sources if s.id not in ids]

    def align(self, progress=None):
        self.last_report = align(self.sources, progress)
        return self.last_report

    def plan(self, rotation_deg=0.0, crop=False, progress=None):
        return make_plan(self.sources, view_rot=self.view_rot, rotation_deg=rotation_deg, crop=crop,
                         progress=progress)

    def merge(self, out_path, fmt, rotation_deg=0.0, crop=False, background=(255, 255, 255), progress=None):
        return merge(self.sources, out_path, fmt, view_rot=self.view_rot, rotation_deg=rotation_deg,
                     crop=crop, background=background, work_dir=self.work_dir, progress=progress)

    def info(self, s):
        Tv = self.view_T(s)
        sc, ang, _ = affine_params(Tv)
        return {
            'id': s.id, 'name': s.name, 'w': s.w, 'h': s.h, 'dtype': s.dtype, 'gray': s.gray,
            'dpi': s.meta.get('dpi'), 'T': Tv[:2, :].tolist(), 'placed': s.placed, 'manual': s.manual,
            'status': s.status, 'scale': sc, 'angle': ang, 'thumb_scale': s.thumb_scale,
            'reference': bool(self.sources and s is self.sources[0]),
        }

    def cleanup(self):
        shutil.rmtree(self.work_dir, ignore_errors=True)
