#!/usr/bin/env python3
"""Publicación automática de ¡Qué + pollo! en Instagram (@masquepollofrito), en la nube (GitHub Actions).

  publicar.py tick              lo que corre el programador: publica lo que ya tocó hoy
  publicar.py run --slot ID     fuerza un horario (ignora la hora, no la fecha)
  publicar.py check-token       valida el token de Meta y las imágenes del programa
  publicar.py status            resumen en consola

Reglas: el feed de IG siempre en tripartito (3 posts, derecha → centro → izquierda); mínimo 3 historias por día.
Las imágenes viven en rrss/ de este mismo repo y GitHub Pages las sirve en público: de ahí las descarga Instagram
(la API de IG no acepta subir archivos y todavía no hay página de FB para alojarlas).
Secreto: META_TOKEN (usuario del sistema "Qué Pollo Publicador", no vence). Estado en estado/ (el workflow hace commit).
Sin correos: si algo falla después de 3 intentos, el workflow termina en rojo y GitHub avisa al dueño del repo.
"""
import json, os, sys, time, datetime as dt
import urllib.request, urllib.parse, urllib.error

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, 'estado')
IG = '17841451070922781'
G = 'https://graph.facebook.com/v21.0/'
BASE = 'https://derlisbach-lang.github.io/masquepollo/'
CR = dt.timezone(dt.timedelta(hours=-6))  # Costa Rica, sin horario de verano
INTENTOS = 3


def tok():
    return os.environ['META_TOKEN'].strip()

def api(method, path, params=None, ok_error=False):
    params = dict(params or {}, access_token=tok())
    if method == 'GET':
        req = urllib.request.Request(G + path + '?' + urllib.parse.urlencode(params))
    else:
        req = urllib.request.Request(G + path, data=urllib.parse.urlencode(params).encode(), method=method)
    for intento in range(3):
        try:
            return json.load(urllib.request.urlopen(req, timeout=120))
        except urllib.error.HTTPError as e:
            r = json.load(e)
            if ok_error:
                return r
            raise RuntimeError(f'{method} {path}: {r.get("error", r)}')
        except (urllib.error.URLError, TimeoutError):
            if intento == 2:
                raise
            time.sleep(10)

def now():
    return dt.datetime.now(CR)

def log(t):
    line = f'{now():%Y-%m-%d %H:%M:%S} {t}'
    print(line)
    open(f'{DATA}/publicar.log', 'a', encoding='utf-8').write(line + '\n')

def load(name, default):
    p = f'{DATA}/{name}'
    return json.load(open(p, encoding='utf-8')) if os.path.exists(p) else default

def save(name, obj):
    tmp = f'{DATA}/{name}.tmp'
    json.dump(obj, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    os.replace(tmp, f'{DATA}/{name}')

def url(f):
    return BASE + urllib.parse.quote(f)

def url_ok(f):
    try:
        r = urllib.request.urlopen(urllib.request.Request(url(f), method='HEAD'), timeout=30)
        return r.status == 200
    except Exception:
        return False


# ---------- Instagram ----------
def ig_wait(cid, minutos=10):
    fin = time.time() + minutos * 60
    while time.time() < fin:
        s = api('GET', cid, {'fields': 'status_code,status'})
        if s.get('status_code') == 'FINISHED':
            return
        if s.get('status_code') in ('ERROR', 'EXPIRED'):
            raise RuntimeError(f'contenedor {cid}: {s.get("status")}')
        time.sleep(6)
    raise RuntimeError(f'contenedor {cid}: no terminó de procesar')

def ig_image(f, caption=None, story=False):
    if not os.path.exists(os.path.join(ROOT, f)):
        raise RuntimeError(f'no existe {f} en el repo')
    if not url_ok(f):
        raise RuntimeError(f'GitHub Pages todavía no sirve {url(f)}')
    p = {'image_url': url(f)}
    if story:
        p['media_type'] = 'STORIES'
    if caption:
        p['caption'] = caption
    cid = api('POST', f'{IG}/media', p)['id']
    ig_wait(cid)
    return api('POST', f'{IG}/media_publish', {'creation_id': cid})['id']

def run(s, est):
    """Publica un horario. Devuelve True si quedó cerrado con errores definitivos."""
    sid = s['id']
    errores = []
    for i, it in enumerate(s['items']):
        e = est.setdefault(f'{sid}#{i}', {})
        try:
            if it['kind'] == 'ig_tri':
                for n, pc in enumerate(it['pieces']):  # pieces en orden de publicación: derecha, centro, izquierda
                    if not e.get(f'p{n}'):
                        if any(e.get(f'p{m}') for m in range(3)):
                            time.sleep(45)  # que salgan en orden en la cuadrícula
                        e[f'p{n}'] = ig_image(pc['file'], pc['caption'])
                        save('estado.json', est)
                        log(f'IG {sid} pieza {n + 1} -> {e[f"p{n}"]}')
            elif it['kind'] == 'ig_story':
                if not e.get('id'):
                    e['id'] = ig_image(it['file'], story=True)
                    save('estado.json', est)
                    log(f'IG historia {sid} -> {e["id"]}')
        except Exception as ex:
            errores.append(str(ex))
            log(f'ERROR {sid}#{i}: {ex}')
    fin = est.setdefault(f'{sid}#fin', {})
    fin['intentos'] = fin.get('intentos', 0) + 1
    if errores and fin['intentos'] < INTENTOS:
        log(f'{sid}: {len(errores)} error(es), se reintenta en la próxima corrida ({fin["intentos"]}/{INTENTOS})')
        save('estado.json', est)
        return False
    fin['hecho'] = f'{now():%Y-%m-%d %H:%M}'
    if errores:
        fin['errores'] = errores
    save('estado.json', est)
    log(f'{"CON ERRORES" if errores else "OK"} {sid} ({s["titulo"]})')
    return bool(errores)


def tick():
    t = now()
    prog, est = load('programa.json', {'slots': []}), load('estado.json', {})
    fallo = False
    for s in sorted(prog['slots'], key=lambda x: (x['fecha'], x['hora'])):
        fin = est.get(f"{s['id']}#fin", {})
        if fin.get('hecho') or fin.get('tarde'):
            continue
        cuando = dt.datetime.fromisoformat(f"{s['fecha']}T{s['hora']}:00").replace(tzinfo=CR)
        if s['fecha'] < f'{t:%Y-%m-%d}':
            est.setdefault(f"{s['id']}#fin", {})['tarde'] = f'{t:%Y-%m-%d %H:%M}'
            save('estado.json', est)
            log(f"NO SE PUBLICÓ A TIEMPO {s['id']} ({s['titulo']}): el programador no corrió ese día")
            fallo = True
        elif cuando <= t:
            log(f"PUBLICANDO {s['id']} ({s['titulo']})")
            fallo |= run(s, est)
    if fallo:
        sys.exit(1)  # GitHub avisa por correo al dueño cuando un workflow termina en rojo


def status():
    prog, est = load('programa.json', {'slots': []}), load('estado.json', {})
    for s in sorted(prog['slots'], key=lambda x: (x['fecha'], x['hora'])):
        fin = est.get(f"{s['id']}#fin", {})
        marca = '✖' if fin.get('errores') or fin.get('tarde') else ('✔' if fin.get('hecho') else '·')
        print(s['fecha'], s['hora'], marca, s['titulo'])


def check():
    print(api('GET', IG, {'fields': 'username,followers_count,media_count'}))
    print(api('GET', f'{IG}/content_publishing_limit', {'fields': 'quota_usage,config'}))
    malos = []
    for s in load('programa.json', {'slots': []})['slots']:
        for it in s['items']:
            for f in [p['file'] for p in it.get('pieces', [])] + ([it['file']] if it.get('file') else []):
                if not url_ok(f):
                    malos.append(f)
    print('imágenes OK' if not malos else f'NO SE SIRVEN: {malos}')
    if malos:
        sys.exit(1)


if __name__ == '__main__':
    os.makedirs(DATA, exist_ok=True)
    a = sys.argv[1:] or ['tick']
    if a[0] == 'tick':
        tick()
    elif a[0] == 'run':
        sid = a[a.index('--slot') + 1]
        s = next(x for x in load('programa.json', {'slots': []})['slots'] if x['id'] == sid)
        sys.exit(1 if run(s, load('estado.json', {})) else 0)
    elif a[0] == 'check-token':
        check()
    elif a[0] == 'status':
        status()
    else:
        sys.exit(__doc__)
