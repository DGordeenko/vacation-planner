#!/usr/bin/env python3
"""Сборка манифестов для конвейера развёртывания (.github/workflows/deploy.yml).

Берёт базовые манифесты deploy/k8s и подставляет значения площадки из
переменных окружения — в репозитории нет ни адресов реестра, ни домена
(они задаются в GitHub Environment). Результат — три файла в каталоге
вывода, которые конвейер применяет по очереди:

  backup.yaml   — Job резервной копии перед миграциями
  migrate.yaml  — Job миграций с уникальным именем по тегу образа
  app.yaml      — всё остальное, кроме фронтенда: бэкенд, Service, Ingress, CronJob…
  frontend.yaml — Deployment фронтенда: обновляется после готовности бэкенда

Так миграции гарантированно выполняются до обновления подов: при
`kubectl apply -k` Job и Deployment применялись бы одновременно.

Только стандартная библиотека: на раннере GitHub нет гарантии PyYAML.

Переменные:
  IMAGE_TAG          тег образов, например sha-1a2b3c4 (обязательна)
  BACKEND_IMAGE      адрес образа бэкенда без тега (обязательна)
  FRONTEND_IMAGE     адрес образа фронтенда без тега (обязательна)
  APP_DOMAIN         домен приложения (обязательна)
  INGRESS_CLASS      ingressClassName; пусто — класс кластера по умолчанию
  INGRESS_ANNOTATIONS аннотации Ingress, по одной «ключ=значение» в строке
                     (сертификат, класс балансировщика конкретного облака)
  CONFIG_OVERRIDES   значения ConfigMap, по одной «КЛЮЧ=значение» в строке
                     (DB_SSLMODE, TRUSTED_PROXY_CIDRS, ADMIN_ALLOW_CIDRS…)
  PG_CLIENT_IMAGE    образ с pg_dump для копии; по умолчанию postgres:16-alpine

python3 deploy/ci/render.py <каталог вывода>
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # deploy/
NAME_RE = re.compile(r"^(  name: )(\S+)$", re.M)  # первое — metadata.name верхнего уровня
KIND_RE = re.compile(r"^kind: (\S+)$", re.M)


def need(name):
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(f"Не задана переменная {name}")
    return value


def pairs(name):
    """«ключ=значение» по строкам → словарь; пустые строки и # пропускаются."""
    result = {}
    for line in os.environ.get(name, "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            sys.exit(f"{name}: строка без «=»: {line}")
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def base_image_names():
    """Имена образов после базового kustomization (images.newName).

    База уже переименовывает vacation-backend в пример адреса реестра, и
    overlay должен ссылаться на итоговое имя, а не на исходное.
    """
    text = (ROOT / "k8s" / "kustomization.yaml").read_text()
    found = dict(re.findall(r"- name: (vacation-\w+)\n\s+newName: (\S+)", text))
    for name in ("vacation-backend", "vacation-frontend"):
        found.setdefault(name, name)
    return found


def kustomization(out, tag, domain):
    """Временный overlay поверх deploy/k8s. JSON — подмножество YAML."""
    base = os.path.relpath(ROOT / "k8s", out)
    names = base_image_names()
    config = {
        "ALLOWED_HOSTS": f"{domain},backend",
        "CSRF_TRUSTED_ORIGINS": f"https://{domain}",
        "APP_BASE_URL": f"https://{domain}",
        **pairs("CONFIG_OVERRIDES"),
    }
    ingress_ops = [
        {"op": "replace", "path": "/spec/rules/0/host", "value": domain},
        {"op": "replace", "path": "/spec/tls/0/hosts", "value": [domain]},
    ]
    if os.environ.get("INGRESS_CLASS", "").strip():
        ingress_ops.append({"op": "add", "path": "/spec/ingressClassName",
                            "value": os.environ["INGRESS_CLASS"].strip()})
    annotations = pairs("INGRESS_ANNOTATIONS")
    if annotations:
        ingress_ops.append({"op": "add", "path": "/metadata/annotations", "value": annotations})
    return {
        "apiVersion": "kustomize.config.k8s.io/v1beta1",
        "kind": "Kustomization",
        "resources": [base],
        "images": [
            {"name": names["vacation-backend"], "newName": need("BACKEND_IMAGE"), "newTag": tag},
            {"name": names["vacation-frontend"], "newName": need("FRONTEND_IMAGE"), "newTag": tag},
        ],
        "patches": [
            {"target": {"kind": "ConfigMap", "name": "vacation-config"},
             "patch": json.dumps({"apiVersion": "v1", "kind": "ConfigMap",
                                  "metadata": {"name": "vacation-config"}, "data": config})},
            {"target": {"kind": "Ingress", "name": "vacation"},
             "patch": json.dumps(ingress_ops)},
        ],
    }


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    out = Path(sys.argv[1]).resolve()
    tag = need("IMAGE_TAG")
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,40}", tag):
        # Тег входит в имена Job: только то, что допустимо в имени ресурса.
        sys.exit(f"Недопустимый тег: {tag}")
    domain = need("APP_DOMAIN")

    work = out / "overlay"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    (work / "kustomization.yaml").write_text(
        json.dumps(kustomization(work, tag, domain), ensure_ascii=False, indent=2))
    built = subprocess.run(["kustomize", "build", str(work)], check=True,
                           capture_output=True, text=True).stdout

    app, migrate, frontend = [], [], []
    for doc in built.split("\n---\n"):
        kind = KIND_RE.search(doc).group(1)
        if kind == "Namespace":
            continue  # создаёт администратор; у конвейера нет прав на уровне кластера
        if kind == "Job":
            migrate.append(NAME_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}-{tag}", doc, count=1))
        elif kind == "Deployment" and NAME_RE.search(doc).group(2) == "frontend":
            frontend.append(doc)
        else:
            app.append(doc)
    if len(migrate) != 1:
        sys.exit(f"Ожидался один Job миграций, найдено: {len(migrate)}")

    backup = (ROOT / "k8s" / "pipeline" / "pre-deploy-backup.yaml").read_text()
    backup = backup.replace("name: pre-deploy-backup", f"name: pre-deploy-backup-{tag}", 1)
    backup = backup.replace("image: PG_CLIENT_IMAGE",
                            f"image: {os.environ.get('PG_CLIENT_IMAGE') or 'postgres:16-alpine'}")
    backup = backup.replace("value: IMAGE_TAG_VALUE", f'value: "{tag}"')

    (out / "app.yaml").write_text("\n---\n".join(app))
    (out / "frontend.yaml").write_text("\n---\n".join(frontend))
    (out / "migrate.yaml").write_text(migrate[0])
    (out / "backup.yaml").write_text(backup)
    print(f"Готово: {out}/{{backup,migrate,app,frontend}}.yaml, тег {tag}, домен {domain}")


if __name__ == "__main__":
    main()
