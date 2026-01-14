#!/usr/bin/env python3
import os
import sys
import time
from dataclasses import dataclass
from typing import Dict, List, Set, Tuple

import requests


@dataclass
class Config:
    sonarr_url: str
    sonarr_api_key: str
    sonarr_timeout: int
    dest_root: str
    sonarr_subdir: str
    dry_run: bool
    min_age_minutes: int
    clean_empty_dirs: bool


def log(msg: str) -> None:
    print(f"[qBitPuller-sonarr] {msg}", flush=True)


def bool_from_env(val: str, default: bool) -> bool:
    if val is None:
        return default
    v = val.strip().lower()
    if v in ("1", "true", "yes", "y", "on"):
        return True
    if v in ("0", "false", "no", "n", "off"):
        return False
    raise SystemExit(f"Config invalide: valeur booleenne attendue, recu '{val}'")


def get_config() -> Config:
    env = dict(os.environ)

    def req(key: str) -> str:
        val = env.get(key)
        if not val:
            raise SystemExit(f"Config manquante: {key} dans les variables d'environnement")
        return val

    try:
        sonarr_timeout = int(env.get("SONARR_TIMEOUT", "30"))
    except ValueError:
        raise SystemExit("Config invalide: SONARR_TIMEOUT doit etre un entier (secondes)")

    try:
        min_age_minutes = int(env.get("SONARR_MIN_AGE_MINUTES", "60"))
    except ValueError:
        raise SystemExit("Config invalide: SONARR_MIN_AGE_MINUTES doit etre un entier (minutes)")

    dest_root = os.path.realpath(req("DEST_ROOT"))
    sonarr_subdir = (env.get("SONARR_SUBDIR") or "sonarr").strip().strip("/")
    dry_run = bool_from_env(env.get("SONARR_CLEANUP_DRY_RUN"), True)
    clean_empty_dirs = bool_from_env(env.get("SONARR_CLEANUP_EMPTY_DIRS"), True)

    return Config(
        sonarr_url=req("SONARR_URL").rstrip("/") + "/",
        sonarr_api_key=req("SONARR_API_KEY"),
        sonarr_timeout=sonarr_timeout,
        dest_root=dest_root,
        sonarr_subdir=sonarr_subdir,
        dry_run=dry_run,
        min_age_minutes=min_age_minutes,
        clean_empty_dirs=clean_empty_dirs,
    )


class SonarrClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/") + "/"
        self.api = self.base_url + "api/v3/"
        self.s = requests.Session()
        self.s.headers.update({"X-Api-Key": api_key})
        self.timeout = timeout

    def get(self, path: str, params: Dict[str, str] | None = None):
        url = self.api + path.lstrip("/")
        r = self.s.get(url, params=params, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def series(self) -> List[Dict]:
        return self.get("series")

    def episode_files_for_series(self, series_id: int) -> List[Dict]:
        return self.get("episodefile", params={"seriesId": str(series_id)})


def build_episodefile_index(client: SonarrClient) -> Set[Tuple[str, int]]:
    series_list = client.series()
    index: Set[Tuple[str, int]] = set()
    for s in series_list:
        sid = s.get("id")
        if sid is None:
            continue
        files = client.episode_files_for_series(int(sid))
        for f in files:
            path = f.get("path") or ""
            size = f.get("size")
            if not path or size is None:
                continue
            base = os.path.basename(path)
            try:
                size_i = int(size)
            except (TypeError, ValueError):
                continue
            index.add((base, size_i))
    return index


def cleanup_empty_dirs(target_root: str, start_dir: str) -> None:
    cur = start_dir
    while True:
        if not cur.startswith(target_root):
            return
        if os.path.samefile(cur, target_root):
            return
        try:
            if os.listdir(cur):
                return
            os.rmdir(cur)
        except FileNotFoundError:
            return
        except OSError:
            return
        cur = os.path.dirname(cur)


def main() -> int:
    cfg = get_config()
    target_root = os.path.realpath(os.path.join(cfg.dest_root, cfg.sonarr_subdir))

    if not os.path.isdir(target_root):
        raise SystemExit(f"DEST_ROOT ou SONARR_SUBDIR invalide: dossier introuvable {target_root}")

    log("Lecture Sonarr...")
    client = SonarrClient(cfg.sonarr_url, cfg.sonarr_api_key, timeout=cfg.sonarr_timeout)
    index = build_episodefile_index(client)
    log(f"Episode files en base: {len(index)}")

    now = time.time()
    min_age_seconds = max(0, cfg.min_age_minutes * 60)

    scanned = 0
    matched = 0
    deleted = 0

    for root, dirs, files in os.walk(target_root, followlinks=False):
        for name in files:
            scanned += 1
            path = os.path.join(root, name)
            try:
                st = os.stat(path)
            except FileNotFoundError:
                continue
            if min_age_seconds and now - st.st_mtime < min_age_seconds:
                continue
            key = (name, int(st.st_size))
            if key not in index:
                continue
            matched += 1
            if cfg.dry_run:
                log(f"DRY_RUN delete: {path}")
                continue
            try:
                os.remove(path)
                deleted += 1
                log(f"Deleted: {path}")
            except FileNotFoundError:
                continue
            except OSError as e:
                log(f"Erreur suppression {path}: {e}")
                continue
            if cfg.clean_empty_dirs:
                cleanup_empty_dirs(target_root, root)

    log(f"Scanned: {scanned}")
    log(f"Matched: {matched}")
    log(f"Deleted: {deleted}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log(f"ERROR: {e}")
        sys.exit(2)