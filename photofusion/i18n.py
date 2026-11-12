# -*- coding: utf-8 -*-
"""Traductions des messages du moteur et du serveur (français, anglais, japonais)."""

LANGS = ('fr', 'en', 'ja')
LANG = 'fr'

MSG = {
    # --- progression
    'analyse': {'fr': "Analyse de {name}…", 'en': "Analysing {name}…", 'ja': "{name} を解析中…"},
    'overlaps': {'fr': "Recherche des recouvrements : {a} ↔ {b}", 'en': "Looking for overlaps: {a} ↔ {b}",
                 'ja': "重なりを検索中：{a} ↔ {b}"},
    'fine': {'fr': "Appariement fin : {a} ↔ {b}", 'en': "Fine matching: {a} ↔ {b}", 'ja': "精密照合中：{a} ↔ {b}"},
    'global': {'fr': "Optimisation globale…", 'en': "Global optimisation…", 'ja': "全体を最適化中…"},
    'warp': {'fr': "Correction des déformations du papier…", 'en': "Correcting paper distortion…",
             'ja': "用紙のゆがみを補正中…"},
    'align_done': {'fr': "Alignement terminé", 'en': "Alignment complete", 'ja': "位置合わせ完了"},
    'seams_prep': {'fr': "Préparation des coutures…", 'en': "Preparing seams…", 'ja': "継ぎ目を準備中…"},
    'seams_calc': {'fr': "Calcul des coutures optimales…", 'en': "Computing optimal seams…",
                   'ja': "最適な継ぎ目を計算中…"},
    'seams_ok': {'fr': "Coutures prêtes", 'en': "Seams ready", 'ja': "継ぎ目の準備完了"},
    'tile': {'fr': "Fusion : tuile {i}/{n}", 'en': "Blending: tile {i}/{n}", 'ja': "合成中：タイル {i}/{n}"},
    'blend_done': {'fr': "Fusion terminée", 'en': "Blending complete", 'ja': "合成完了"},
    'writing': {'fr': "Écriture du fichier (cela peut prendre une minute)…",
                'en': "Writing the file (this may take a minute)…",
                'ja': "ファイルを書き込み中（1分ほどかかる場合があります）…"},
    'done': {'fr': "Terminé", 'en': "Done", 'ja': "完了"},
    'starting': {'fr': "Démarrage…", 'en': "Starting…", 'ja': "開始中…"},
    # --- erreurs
    'no_rawpy': {'fr': "Lecture RAW impossible : le module 'rawpy' n'est pas installé.",
                 'en': "Cannot read RAW files: the 'rawpy' module is not installed.",
                 'ja': "RAWファイルを読み込めません：'rawpy' モジュールがインストールされていません。"},
    'no_tifffile': {'fr': "Le module 'tifffile' est requis pour écrire des TIFF.",
                    'en': "The 'tifffile' module is required to write TIFF files.",
                    'ja': "TIFFの書き出しには 'tifffile' モジュールが必要です。"},
    'jpeg_too_big': {'fr': "Image trop grande pour le JPEG (max 65 500 px). Choisissez TIFF ou PNG.",
                     'en': "Image too large for JPEG (max 65,500 px). Choose TIFF or PNG.",
                     'ja': "JPEGには大きすぎる画像です（最大65,500ピクセル）。TIFFまたはPNGを選んでください。"},
    'png_fail': {'fr': "Échec de l'encodage PNG", 'en': "PNG encoding failed", 'ja': "PNGの書き出しに失敗しました"},
    'none_placed': {'fr': "Aucune image positionnée.", 'en': "No image has been positioned.",
                    'ja': "配置された画像がありません。"},
    'busy': {'fr': "Une opération est déjà en cours.", 'en': "An operation is already running.",
             'ja': "別の処理を実行中です。"},
    'wait': {'fr': "Patientez : une opération est en cours.", 'en': "Please wait: an operation is running.",
             'ja': "お待ちください：処理を実行中です。"},
    'cancelled': {'fr': "Opération annulée.", 'en': "Operation cancelled.", 'ja': "処理を取り消しました。"},
    'memory': {'fr': "Mémoire insuffisante. Fermez d'autres programmes ou fusionnez moins d'images à la fois.",
               'en': "Not enough memory. Close other programs or merge fewer images at once.",
               'ja': "メモリが不足しています。他のプログラムを閉じるか、一度に合成する画像を減らしてください。"},
    'need_two': {'fr': "Ajoutez au moins deux images.", 'en': "Add at least two images.",
                 'ja': "画像を2枚以上追加してください。"},
    'bad_format': {'fr': "Format non pris en charge : {ext}", 'en': "Unsupported format: {ext}",
                   'ja': "対応していない形式です：{ext}"},
    'no_ext': {'fr': "(sans extension)", 'en': "(no extension)", 'ja': "（拡張子なし）"},
    'raw_missing': {'fr': "Fichiers RAW : le module 'rawpy' n'est pas installé.",
                    'en': "RAW files: the 'rawpy' module is not installed.",
                    'ja': "RAWファイル：'rawpy' モジュールがインストールされていません。"},
    'transfer': {'fr': "Transfert interrompu.", 'en': "Transfer interrupted.", 'ja': "転送が中断されました。"},
    'max_images': {'fr': "Maximum 24 images.", 'en': "Maximum 24 images.", 'ja': "画像は最大24枚です。"},
    'cannot_read': {'fr': "Impossible de lire {name} : {err}", 'en': "Cannot read {name}: {err}",
                    'ja': "{name} を読み込めません：{err}"},
    'not_found': {'fr': "introuvable", 'en': "not found", 'ja': "見つかりません"},
    'unknown_image': {'fr': "image inconnue", 'en': "unknown image", 'ja': "不明な画像"},
    'no_preview': {'fr': "pas d'aperçu", 'en': "no preview", 'ja': "プレビューがありません"},
    'no_result': {'fr': "aucun résultat", 'en': "no result", 'ja': "結果がありません"},
}


def set_lang(lang):
    global LANG
    if lang in LANGS:
        LANG = lang


def tr(key, **kw):
    d = MSG.get(key)
    if not d:
        return key
    s = d.get(LANG) or d['fr']
    return s.format(**kw) if kw else s
