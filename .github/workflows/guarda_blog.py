#!/usr/bin/env python3
"""guarda-blog: reprova pull requests que mudam qualquer coisa fora do blog.

Uso: guarda_blog.py <sha_base> <sha_head>
Só lê arquivos com git; nunca executa nada vindo do pull request.
"""
import json
import re
import subprocess
import sys

BASE, HEAD = sys.argv[1], sys.argv[2]
SLUG = r"[a-z0-9]+(?:-[a-z0-9]+)*"
POST = re.compile(rf"^site/blog/({SLUG})/index\.html$")
IMG = re.compile(rf"^site/assets/img/blog/{SLUG}\.(webp|jpg|jpeg|png)$")
EDITAVEIS = {"site/index.html", "site/blog/index.html", "site/sitemap.xml", "site/_redirects"}
MODELO = "site/blog/como-reduzir-faltas-de-pacientes/index.html"
PERIGOSO = re.compile(r"<script|<style|<iframe|<object|<embed|<form|<link|<meta|javascript:|\son[a-z]+\s*=", re.I)
MAX_IMG = 800 * 1024
# Único atributo de evento aceito: o botão "copiar link" da área de compartilhar.
COPIAR = re.compile(r''' onclick="navigator\.clipboard(?:&amp;&amp;|&&)navigator\.clipboard\.writeText\(this\.href\);this\.textContent='Copiado';return false"''')

erros = []


def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True).stdout


def ler(sha, caminho):
    try:
        return git("show", f"{sha}:{caminho}").decode("utf-8")
    except subprocess.CalledProcessError:
        return None


def regiao_div(texto, marcador):
    """Devolve (inicio, fim) do conteúdo da div que começa em `marcador`."""
    i = texto.find(marcador)
    if i < 0 or texto.find(marcador, i + 1) >= 0:
        return None
    ini = texto.index(">", i) + 1  # logo depois da tag de abertura da div
    prof, pos = 1, ini
    tags = re.compile(r"<div\b|</div>", re.I)
    while prof:
        m = tags.search(texto, pos)
        if not m:
            return None
        prof += 1 if m.group(0).lower().startswith("<div") else -1
        pos = m.end()
    return ini, pos - len("</div>")


def sem_regiao(texto, marcador, nome, arquivo):
    r = regiao_div(texto, marcador)
    if not r:
        erros.append(f"{arquivo}: não encontrei o bloco {nome}")
        return None, None
    ini, fim = r
    miolo = texto[ini:fim]
    if PERIGOSO.search(miolo):
        erros.append(f"{arquivo}: o bloco {nome} tem script, estilo, formulário ou atributo de evento")
    return texto[:ini] + "<<MIOLO>>" + texto[fim:], miolo


def casca_materia(texto, arquivo):
    """Tira o que pode mudar numa matéria e devolve o resto (a casca)."""
    t = texto
    t = re.sub(r"<title>[^<]*</title>", "<title/>", t)
    t = re.sub(r'<meta (name|property)="(description|og:title|og:description|og:url)" content="[^"<>]*">', r"<\2/>", t)
    t = re.sub(r'<link rel="canonical" href="https://lemidi\.com\.br/blog/[a-z0-9-]+/">', "<canonical/>", t)
    for bloco in re.findall(r'<script type="application/ld\+json">(.*?)</script>', t, re.S):
        try:
            json.loads(bloco)
        except ValueError:
            erros.append(f"{arquivo}: JSON-LD inválido")
    t = re.sub(r'<script type="application/ld\+json">.*?</script>', "<ld/>", t, flags=re.S)
    m = re.search(r'<body data-art="(v[A-Za-z]+)">', t)
    ids = re.findall(r'<article id="(v[A-Za-z]+)" class="vart">', t)
    if not m or ids != [m.group(1)]:
        erros.append(f"{arquivo}: body data-art e article id precisam existir e ser iguais")
        return None
    t = t.replace(m.group(0), "<body/>")
    art = re.search(r'<article id="v[A-Za-z]+" class="vart">(.*?)</article>', t, re.S)
    if PERIGOSO.search(COPIAR.sub("", art.group(1))):
        erros.append(f"{arquivo}: o texto da matéria tem script, estilo, formulário ou atributo de evento")
    return t[: art.start()] + "<article/>" + t[art.end():]


mudancas = []
for linha in git("diff", "--no-renames", "--name-status", f"{BASE}...{HEAD}").decode().splitlines():
    status, caminho = linha.split("\t", 1)
    mudancas.append((status[0], caminho))

if not mudancas:
    print("Nenhuma mudança.")
    sys.exit(0)

modelo = casca_materia(ler(BASE, MODELO), MODELO)
posts_apagados, redirects_novos = [], []

for status, caminho in mudancas:
    post = POST.match(caminho)
    if post:
        if status == "D":
            posts_apagados.append(post.group(1))
            continue
        casca = casca_materia(ler(HEAD, caminho), caminho)
        anterior = ler(BASE, caminho)
        referencia = casca_materia(anterior, caminho) if anterior else modelo
        if casca is not None and casca != referencia:
            erros.append(f"{caminho}: mudou algo fora do conteúdo da matéria (CSS, scripts, menu, rodapé ou cabeçalho)")
        continue

    if IMG.match(caminho):
        if status != "D":
            tamanho = int(git("cat-file", "-s", f"{HEAD}:{caminho}"))
            if tamanho > MAX_IMG:
                erros.append(f"{caminho}: imagem com {tamanho // 1024} KB, o limite é 800 KB")
        continue

    if caminho not in EDITAVEIS:
        erros.append(f"{caminho}: arquivo fora do blog, não pode ser alterado por aqui")
        continue
    if status != "M":
        erros.append(f"{caminho}: este arquivo só pode ser editado, nunca criado ou apagado")
        continue

    antes, depois = ler(BASE, caminho), ler(HEAD, caminho)
    if caminho == "site/index.html":
        marcador = '<div class="grid3">\n<div class="pil post"'
        a, _ = sem_regiao(antes, marcador, "cards do blog da home", caminho)
        d, _ = sem_regiao(depois, marcador, "cards do blog da home", caminho)
        if a is not None and d is not None and a != d:
            erros.append(f"{caminho}: mudou algo na home fora dos 3 cards do blog")
    elif caminho == "site/blog/index.html":
        a, _ = sem_regiao(antes, '<div class="grid" id="grid">', "lista de matérias", caminho)
        d, _ = sem_regiao(depois, '<div class="grid" id="grid">', "lista de matérias", caminho)
        if a is not None and d is not None and a != d:
            erros.append(f"{caminho}: mudou algo na página do blog fora da lista de matérias")
    elif caminho == "site/sitemap.xml":
        url = re.compile(rf"^  <url><loc>https://lemidi\.com\.br/blog/{SLUG}/</loc><lastmod>\d{{4}}-\d{{2}}-\d{{2}}</lastmod></url>$")
        la, ld = antes.splitlines(), depois.splitlines()
        if [x for x in la if not url.match(x)] != [x for x in ld if not url.match(x)]:
            erros.append(f"{caminho}: só linhas de matérias do blog podem mudar no sitemap")
    elif caminho == "site/_redirects":
        if not depois.startswith(antes.rstrip("\n")):
            erros.append(f"{caminho}: redirecionamentos existentes não podem ser alterados, só acrescentados no fim")
        else:
            novas = [x for x in depois[len(antes.rstrip("\n")):].splitlines() if x.strip()]
            regra = re.compile(rf"^/blog/({SLUG})/\*?\s+/blog/(?:{SLUG}/)?\s+301$")
            for x in novas:
                m = regra.match(x.strip())
                if not m:
                    erros.append(f"{caminho}: linha não permitida: {x.strip()[:80]}")
                else:
                    redirects_novos.append(m.group(1))

for slug in posts_apagados:
    if slug not in redirects_novos:
        erros.append(f"site/blog/{slug}/: matéria removida sem redirecionamento 301 em site/_redirects")

print(f"Arquivos alterados: {len(mudancas)}")
for status, caminho in mudancas:
    print(f"  {status} {caminho}")
if erros:
    print("\nREPROVADO:")
    for e in erros:
        print(f"  - {e}")
    sys.exit(1)
print("\nAPROVADO: só mudanças permitidas no blog.")
