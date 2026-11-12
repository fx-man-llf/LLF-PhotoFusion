# -*- coding: utf-8 -*-
"""Serveur local de LLF PhotoFusion : l'interface s'affiche dans une fenêtre
d'application (Edge / Chrome / Chromium) ou dans le navigateur par défaut.
Rien ne sort de l'ordinateur : le serveur n'écoute que sur 127.0.0.1."""
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np

from . import core, __version__
from . import i18n
from .i18n import tr

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web')


def default_output_dir():
    home = os.path.expanduser('~')
    for name in ('Pictures', 'Images', 'Mes images'):
        p = os.path.join(home, name)
        if os.path.isdir(p):
            return os.path.join(p, 'LLF PhotoFusion')
    return os.path.join(home, 'LLF PhotoFusion')


class App:
    def __init__(self):
        self.project = core.Project()
        self.upload_dir = os.path.join(self.project.work_dir, 'uploads')
        os.makedirs(self.upload_dir, exist_ok=True)
        self.job = {'running': False, 'kind': None, 'frac': 0.0, 'msg': '', 'error': None, 'done': 0}
        self.job_progress = None
        self.preview_jpeg = None
        self.preview_version = 0
        self.result = None
        self.load_lock = threading.Lock()
        self.last_ping = None
        self.output_dir = default_output_dir()

    # ---------------- tâches de fond
    def start_job(self, kind, fn):
        if self.job['running']:
            raise RuntimeError(tr('busy'))
        prog = core.Progress(lambda f, m: self.job.update(frac=f, msg=m))
        self.job_progress = prog
        self.job.update(running=True, kind=kind, frac=0.0, msg=tr('starting'), error=None)

        def run():
            try:
                fn(prog)
                self.job.update(frac=1.0)
            except core.Cancelled:
                self.job.update(error=tr('cancelled'))
            except MemoryError:
                self.job.update(error=tr('memory'))
            except Exception as e:
                traceback.print_exc()
                self.job.update(error=str(e) or e.__class__.__name__)
            finally:
                self.job.update(running=False, done=self.job['done'] + 1)
        threading.Thread(target=run, daemon=True).start()

    def set_preview(self, rgb):
        ok, buf = cv2.imencode('.jpg', rgb[:, :, ::-1], [cv2.IMWRITE_JPEG_QUALITY, 88])
        self.preview_jpeg = buf.tobytes()
        self.preview_version += 1

    def state(self):
        P = self.project
        return {
            'version': __version__,
            'images': [P.info(s) for s in P.sources],
            'view_rot': P.view_rot,
            'job': dict(self.job),
            'report': P.last_report,
            'preview_version': self.preview_version if self.preview_jpeg else 0,
            'result': self.result,
            'output_dir': self.output_dir,
            'default_format': core.output_format_for(P.sources) if P.sources else 'tif',
            'raw_support': core.rawpy is not None,
            'lang': i18n.LANG,
        }


APP = None


def _fmt_dpi(d):
    return None if not d else [round(d[0], 2), round(d[1], 2)]


class Handler(BaseHTTPRequestHandler):
    server_version = 'LLF PhotoFusion'

    def log_message(self, fmt, *args):
        pass

    # ---------------- utilitaires
    def _send(self, code, body, ctype='application/json; charset=utf-8', extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode('utf-8')
        elif isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self):
        n = int(self.headers.get('Content-Length') or 0)
        if n == 0:
            return {}
        return json.loads(self.rfile.read(n).decode('utf-8'))

    def _ok(self):
        self._send(200, APP.state())

    def _err(self, msg, code=400):
        self._send(code, {'error': msg})

    # ---------------- GET
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            if path in ('/', '/index.html'):
                with open(os.path.join(WEB_DIR, 'index.html'), 'rb') as f:
                    return self._send(200, f.read(), 'text/html; charset=utf-8')
            if path in ('/logo.png', '/favicon.png', '/icone.ico', '/favicon.ico'):
                fn = {'/logo.png': 'logo.png', '/favicon.png': 'icone.png'}.get(path, 'icone.ico')
                with open(os.path.join(WEB_DIR, fn), 'rb') as f:
                    ctype = 'image/png' if fn.endswith('.png') else 'image/x-icon'
                    return self._send(200, f.read(), ctype, {'Cache-Control': 'max-age=86400'})
            if path == '/api/state':
                APP.last_ping = time.time()
                return self._ok()
            if path.startswith('/api/thumb/'):
                sid = path.rsplit('/', 1)[-1]
                s = APP.project.get(sid)
                return self._send(200, s.thumb_jpeg, 'image/jpeg', {'Cache-Control': 'max-age=3600'})
            if path == '/api/preview.jpg':
                if not APP.preview_jpeg:
                    return self._err(tr('no_preview'), 404)
                return self._send(200, APP.preview_jpeg, 'image/jpeg')
            if path == '/api/result':
                r = APP.result
                if not r or not os.path.exists(r['path']):
                    return self._err(tr('no_result'), 404)
                size = os.path.getsize(r['path'])
                self.send_response(200)
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Content-Length', str(size))
                fname = urllib.parse.quote(os.path.basename(r['path']))
                self.send_header('Content-Disposition', f"attachment; filename*=UTF-8''{fname}")
                self.end_headers()
                with open(r['path'], 'rb') as f:
                    shutil.copyfileobj(f, self.wfile, 1 << 20)
                return
            self._err(tr('not_found'), 404)
        except KeyError:
            self._err(tr('unknown_image'), 404)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ---------------- POST
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        P = APP.project
        try:
            if path == '/api/upload':
                return self._upload(qs)
            if APP.job['running'] and path not in ('/api/cancel', '/api/lang'):
                return self._err(tr('wait'), 409)
            data = self._json()
            if path == '/api/lang':
                i18n.set_lang(data.get('lang', 'fr'))
                return self._ok()
            if path == '/api/remove':
                P.remove(data['id'])
                APP.preview_jpeg = None
                return self._ok()
            if path == '/api/clear':
                for s in list(P.sources):
                    P.remove(s.id)
                P.view_rot = 0
                P.last_report = None
                APP.preview_jpeg = None
                APP.result = None
                return self._ok()
            if path == '/api/rotate':
                P.rotate(data['id'], int(data.get('q', 1)))
                return self._ok()
            if path == '/api/move':
                P.set_view_transform(data['id'], data['T'])
                return self._ok()
            if path == '/api/reference':
                P.make_reference(data['id'])
                return self._ok()
            if path == '/api/reorder':
                P.reorder(data['ids'])
                return self._ok()
            if path == '/api/align':
                if len(P.sources) < 2:
                    return self._err(tr('need_two'))

                def job(prog):
                    P.align(prog.sub(0, 0.8))
                    if any(s.placed for s in P.sources):
                        plan = P.plan(progress=prog.sub(0.8, 0.95))
                        APP.set_preview(core.preview_image(plan))
                APP.start_job('align', job)
                return self._ok()
            if path == '/api/preview':
                opts = self._opts(data)

                def job(prog):
                    plan = P.plan(rotation_deg=opts['rotation'], crop=opts['crop'], progress=prog.sub(0, 0.8))
                    APP.set_preview(core.preview_image(plan, background=opts['background']))
                APP.start_job('preview', job)
                return self._ok()
            if path == '/api/merge':
                return self._merge(data)
            if path == '/api/cancel':
                if APP.job_progress:
                    APP.job_progress.cancel_event.set()
                return self._ok()
            if path == '/api/open_folder':
                folder = data.get('path') or APP.output_dir
                if APP.result and not data.get('path'):
                    folder = os.path.dirname(APP.result['path'])
                open_folder(folder)
                return self._ok()
            if path == '/api/shutdown':
                self._ok()
                threading.Thread(target=lambda: (time.sleep(0.3), os._exit(0)), daemon=True).start()
                return
            self._err(tr('not_found'), 404)
        except KeyError:
            self._err(tr('unknown_image'), 404)
        except Exception as e:
            traceback.print_exc()
            self._err(str(e))

    def _opts(self, data):
        bg = data.get('background', 'white')
        return {
            'rotation': float(data.get('rotation') or 0.0),
            'crop': bool(data.get('crop')),
            'background': (0, 0, 0) if bg == 'black' else (255, 255, 255),
        }

    def _upload(self, qs):
        name = os.path.basename(qs.get('name', ['image'])[0])
        ext = os.path.splitext(name)[1].lower()
        if ext not in core.SUPPORTED_EXT:
            return self._err(tr('bad_format', ext=ext or tr('no_ext')))
        if ext in core.RAW_EXT and core.rawpy is None:
            return self._err(tr('raw_missing'))
        n = int(self.headers.get('Content-Length') or 0)
        dest_dir = os.path.join(APP.upload_dir, str(int(time.time() * 1000)))
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, name)
        remaining = n
        with open(dest, 'wb') as f:
            while remaining > 0:
                chunk = self.rfile.read(min(remaining, 1 << 22))
                if not chunk:
                    break
                f.write(chunk)
                remaining -= len(chunk)
        if remaining:
            return self._err(tr('transfer'))
        if len(APP.project.sources) >= 24:
            return self._err(tr('max_images'))
        with APP.load_lock:
            try:
                APP.project.add(dest, name)
            except Exception as e:
                traceback.print_exc()
                return self._err(tr('cannot_read', name=name, err=e))
        APP.preview_jpeg = None
        self._ok()

    def _merge(self, data):
        P = APP.project
        if not any(s.placed for s in P.sources):
            return self._err(tr('none_placed'))
        opts = self._opts(data)
        fmt = (data.get('format') or 'auto').lower()
        if fmt == 'auto':
            fmt = core.output_format_for(P.sources)
        ext = {'tif': '.tif', 'png': '.png', 'jpg': '.jpg'}[fmt]
        out_dir = data.get('output_dir') or APP.output_dir
        out_dir = os.path.expanduser(out_dir)
        os.makedirs(out_dir, exist_ok=True)
        APP.output_dir = out_dir
        base = (data.get('filename') or '').strip()
        if not base:
            base = os.path.splitext(P.sources[0].name)[0] + '_fusion'
        base = ''.join(ch for ch in base if ch not in '<>:"/\\|?*').strip() or 'fusion'
        if base.lower().endswith(ext):
            base = base[:-len(ext)]
        out = os.path.join(out_dir, base + ext)
        k = 2
        while os.path.exists(out):
            out = os.path.join(out_dir, f'{base}_{k}{ext}')
            k += 1

        def job(prog):
            t0 = time.time()
            res = P.merge(out, fmt, rotation_deg=opts['rotation'], crop=opts['crop'],
                          background=opts['background'], progress=prog)
            APP.set_preview(res.pop('preview'))
            res['dpi'] = _fmt_dpi(res.get('dpi'))
            res['size_mb'] = round(os.path.getsize(out) / 1e6, 1)
            res['seconds'] = round(time.time() - t0)
            res['name'] = os.path.basename(out)
            res['folder'] = out_dir
            APP.result = res
        APP.result = None
        APP.start_job('merge', job)
        self._ok()


def open_folder(folder):
    try:
        if sys.platform.startswith('win'):
            os.startfile(folder)  # type: ignore[attr-defined]
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', folder])
        else:
            subprocess.Popen(['xdg-open', folder])
    except Exception:
        traceback.print_exc()


def _find_app_browser():
    cands = []
    if sys.platform.startswith('win'):
        for base in (os.environ.get('ProgramFiles(x86)'), os.environ.get('ProgramFiles'),
                     os.environ.get('LOCALAPPDATA')):
            if not base:
                continue
            cands += [os.path.join(base, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
                      os.path.join(base, 'Google', 'Chrome', 'Application', 'chrome.exe')]
    else:
        for name in ('chromium', 'chromium-browser', 'google-chrome', 'google-chrome-stable',
                     'microsoft-edge', 'microsoft-edge-stable', 'brave-browser'):
            p = shutil.which(name)
            if p:
                cands.append(p)
    for c in cands:
        if c and os.path.exists(c):
            return c
    return None


def open_window(url):
    exe = _find_app_browser()
    if exe:
        prof = os.path.join(os.path.expanduser('~'), '.photofusion', 'fenetre')
        os.makedirs(prof, exist_ok=True)
        try:
            subprocess.Popen([exe, f'--app={url}', f'--user-data-dir={prof}', '--window-size=1400,900',
                              '--no-first-run', '--no-default-browser-check'],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except Exception:
            pass
    import webbrowser
    webbrowser.open(url)


def _watchdog():
    """Arrête le programme quand la fenêtre est fermée depuis un moment."""
    while True:
        time.sleep(10)
        if APP.last_ping is None or APP.job['running']:
            continue
        if time.time() - APP.last_ping > 150:
            APP.project.cleanup()
            os._exit(0)


def main(argv=None):
    global APP
    import argparse
    ap = argparse.ArgumentParser(description='LLF PhotoFusion')
    ap.add_argument('--port', type=int, default=0)
    ap.add_argument('--no-window', action='store_true')
    ap.add_argument('--no-watchdog', action='store_true')
    args = ap.parse_args(argv)
    APP = App()
    srv = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    srv.daemon_threads = True
    url = f'http://127.0.0.1:{srv.server_address[1]}/'
    print(f'LLF PhotoFusion {__version__} — interface : {url}')
    print('Logiciel gratuit, utilisable par tous. © 2026 LLF — https://www.laludotheque.fr · https://www.lalogitheque.fr')
    print('Gardez cette fenêtre ouverte pendant l’utilisation (elle se ferme toute seule ensuite).')
    print('Keep this window open while using the program. / 使用中はこの画面を閉じないでください。')
    sys.stdout.flush()
    if not args.no_window:
        threading.Timer(0.6, open_window, args=(url,)).start()
    if not args.no_watchdog:
        threading.Thread(target=_watchdog, daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        APP.project.cleanup()


if __name__ == '__main__':
    main()
