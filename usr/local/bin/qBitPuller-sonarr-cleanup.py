#!/usr/bin/env python3
import os
import sys
import time
from datetime import datetime, timedelta, timezone
import fcntl
from dataclasses import dataclass
from typing import Dict, List, Set

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
    history_since_days: int


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
    try:
        history_since_days = int(env.get("SONARR_HISTORY_SINCE_DAYS", "14"))
    except ValueError:
        raise SystemExit("Config invalide: SONARR_HISTORY_SINCE_DAYS doit etre un entier (jours)")

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
        history_since_days=history_since_days,
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

    def history(self, page: int, page_size: int) -> Dict:
        return self.get(
            "history",
            params={
                "page": str(page),
                "pageSize": str(page_size),
            },
        )

    def history_since(self, date_iso: str, event_type: str) -> List[Dict]:
        return self.get(
            "history/since",
            params={
                "date": date_iso,
                "eventType": event_type,
            },
        )

def build_imported_paths(client: SonarrClient, since_days: int) -> List[str]:
    paths: Set[str] = set()
    since_dt = datetime.now(timezone.utc) - timedelta(days=since_days)
    date_iso = since_dt.isoformat().replace("+00:00", "Z")
    # On limite volontairement l'historique pour rester dans une fenetre recente.
    log(f"Requete Sonarr: /api/v3/history/since?date={date_iso}&eventType=downloadFolderImported")
    records = client.history_since(date_iso=date_iso, event_type="downloadFolderImported")
    for rec in records:
        rec_data = rec.get("data") or {}
        src = rec_data.get("droppedPath") or rec_data.get("sourcePath") or ""
        if src:
            paths.add(src)
    return sorted(paths)


def is_under_root(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def cleanup_empty_dirs(target_root: str, start_dir: str) -> int:
    cur = start_dir
    deleted = 0
    while True:
        if not is_under_root(cur, target_root):
            return deleted
        if os.path.samefile(cur, target_root):
            return deleted
        try:
            if os.listdir(cur):
                return deleted
            os.rmdir(cur)
            deleted += 1
        except FileNotFoundError:
            return deleted
        except OSError:
            return deleted
        cur = os.path.dirname(cur)


def cleanup_nfo_and_empty_dirs(
    target_root: str,
    start_dir: str,
    min_age_seconds: int,
    dry_run: bool,
    clean_empty_dirs: bool,
) -> tuple[int, int]:
    now = time.time()
    nfo_deleted = 0
    dirs_deleted = 0

    if not is_under_root(start_dir, target_root):
        return 0, 0
    scan_dir = start_dir
    while not os.path.isdir(scan_dir):
        # Remonter jusqu'au premier parent existant pour permettre le nettoyage.
        parent = os.path.dirname(scan_dir)
        if parent == scan_dir:
            scan_dir = ""
            break
        scan_dir = parent
        if not is_under_root(scan_dir, target_root):
            scan_dir = ""
            break

    if scan_dir:
        for root, dirs, files in os.walk(scan_dir, topdown=False):
            if not is_under_root(root, target_root):
                continue
            for name in files:
                if not name.lower().endswith(".nfo"):
                    continue
                path = os.path.join(root, name)
                try:
                    st = os.stat(path)
                except FileNotFoundError:
                    continue
                if min_age_seconds and now - st.st_mtime < min_age_seconds:
                    continue
                if dry_run:
                    log(f"DRY_RUN delete: {path}")
                    continue
                try:
                    os.remove(path)
                    nfo_deleted += 1
                    log(f"Deleted: {path}")
                except FileNotFoundError:
                    continue
                except OSError as e:
                    log(f"Erreur suppression {path}: {e}")
                    continue

            if not clean_empty_dirs:
                continue
            if os.path.samefile(root, target_root):
                continue
            try:
                if os.listdir(root):
                    continue
                if dry_run:
                    log(f"DRY_RUN rmdir: {root}")
                    continue
                os.rmdir(root)
                dirs_deleted += 1
                log(f"Deleted dir: {root}")
            except FileNotFoundError:
                continue
            except OSError:
                continue

    cur = os.path.dirname(start_dir)
    while True:
        if not is_under_root(cur, target_root):
            break
        if os.path.samefile(cur, target_root):
            break
        try:
            for entry in os.scandir(cur):
                if not entry.is_file(follow_symlinks=False):
                    continue
                if not entry.name.lower().endswith(".nfo"):
                    continue
                try:
                    st = entry.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if min_age_seconds and now - st.st_mtime < min_age_seconds:
                    continue
                if dry_run:
                    log(f"DRY_RUN delete: {entry.path}")
                    continue
                try:
                    os.remove(entry.path)
                    nfo_deleted += 1
                    log(f"Deleted: {entry.path}")
                except FileNotFoundError:
                    continue
                except OSError as e:
                    log(f"Erreur suppression {entry.path}: {e}")
                    continue
            if clean_empty_dirs:
                dirs_deleted += cleanup_empty_dirs(target_root, cur)
        except FileNotFoundError:
            break
        except OSError:
            pass
        cur = os.path.dirname(cur)

    return nfo_deleted, dirs_deleted


def main() -> int:
    cfg = get_config()
    lock_path = "/var/lock/qBitPuller-sonarr-cleanup.lock"
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    lock_fh = open(lock_path, "w", encoding="utf-8")
    try:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("Deja en cours, on quitte")
        return 0

    target_root = os.path.realpath(os.path.join(cfg.dest_root, cfg.sonarr_subdir))

    if not os.path.isdir(target_root):
        raise SystemExit(f"DEST_ROOT ou SONARR_SUBDIR invalide: dossier introuvable {target_root}")

    log("Lecture Sonarr...")
    client = SonarrClient(cfg.sonarr_url, cfg.sonarr_api_key, timeout=cfg.sonarr_timeout)
    imported_paths = build_imported_paths(client, cfg.history_since_days)
    log(f"Imports trouves via history/since: {len(imported_paths)}")

    now = time.time()
    min_age_seconds = max(0, cfg.min_age_minutes * 60)

    scanned = 0
    matched = 0
    deleted = 0

    for src in imported_paths:
        scanned += 1
        path = os.path.realpath(src)
        if not is_under_root(path, target_root):
            continue
        try:
            st = os.stat(path)
        except FileNotFoundError:
            continue
        if min_age_seconds and now - st.st_mtime < min_age_seconds:
            continue
        matched += 1
        if cfg.dry_run:
            log(f"DRY_RUN delete: {path}")
            continue
        try:
            if os.path.isdir(path):
                os.rmdir(path)
            else:
                os.remove(path)
            deleted += 1
            log(f"Deleted: {path}")
        except FileNotFoundError:
            continue
        except OSError as e:
            log(f"Erreur suppression {path}: {e}")
            continue
        if cfg.clean_empty_dirs:
            # Nettoyage immediat des parents du chemin supprime.
            cleanup_empty_dirs(target_root, os.path.dirname(path))

    log(f"Scanned: {scanned}")
    log(f"Matched: {matched}")
    log(f"Deleted: {deleted}")

    log("Nettoyage complementaire: .nfo + dossiers vides")
    seen_dirs: Set[str] = set()
    nfo_deleted = 0
    dirs_deleted = 0
    for src in imported_paths:
        path = os.path.realpath(src)
        if not is_under_root(path, target_root):
            continue
        start_dir = path if os.path.isdir(path) else os.path.dirname(path)
        if start_dir in seen_dirs:
            continue
        seen_dirs.add(start_dir)
        nfo_count, dir_count = cleanup_nfo_and_empty_dirs(
            target_root=target_root,
            start_dir=start_dir,
            min_age_seconds=min_age_seconds,
            dry_run=cfg.dry_run,
            clean_empty_dirs=cfg.clean_empty_dirs,
        )
        nfo_deleted += nfo_count
        dirs_deleted += dir_count
    log(f"NFO deleted: {nfo_deleted}")
    log(f"Dirs deleted: {dirs_deleted}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log(f"ERROR: {e}")
        sys.exit(2)
