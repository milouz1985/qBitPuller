#!/usr/bin/env python3
import os
import sys
import time
import shlex
import subprocess
import fcntl
from dataclasses import dataclass
from typing import List, Dict, Any

import requests


@dataclass
class Config:
    qb_url: str
    qb_user: str
    qb_pass: str

    rclone_remote: str
    rclone_src_root: str
    dest_root: str

    pulled_tag: str = "pulled"
    rclone_config: str = ""


def log(msg: str) -> None:
    print(f"[qBitPuller] {msg}", flush=True)


def load_env(path: str) -> Dict[str, str]:
    env: Dict[str, str] = {}
    if not os.path.exists(path):
        return env

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if "=" not in s:
                continue
            k, v = s.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def get_config() -> Config:
    env_path = "/etc/qBitPuller.env"
    env = load_env(env_path)

    def req(key: str) -> str:
        val = env.get(key) or os.environ.get(key)
        if not val:
            raise SystemExit(f"Config manquante: {key} dans {env_path} ou variables d'environnement")
        return val

    return Config(
        qb_url=req("QB_URL").rstrip("/") + "/",
        qb_user=req("QB_USER"),
        qb_pass=req("QB_PASS"),
        rclone_remote=req("RCLONE_REMOTE"),
        rclone_src_root=req("RCLONE_SRC_ROOT").rstrip("/"),
        dest_root=req("DEST_ROOT").rstrip("/"),
        pulled_tag=(env.get("PULLED_TAG") or os.environ.get("PULLED_TAG") or "pulled"),
        rclone_config=(env.get("RCLONE_CONFIG") or os.environ.get("RCLONE_CONFIG") or ""),
    )


class QbClient:
    def __init__(self, base_url: str, user: str, password: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/") + "/"
        self.api = self.base_url + "api/v2/"
        self.s = requests.Session()
        self.timeout = timeout
        self.user = user
        self.password = password

    def login(self) -> None:
        url = self.api + "auth/login"
        r = self.s.post(url, data={"username": self.user, "password": self.password}, timeout=self.timeout)
        r.raise_for_status()
        if r.text.strip() != "Ok.":
            raise RuntimeError(f"Login qBittorrent refusé: {r.text[:200]}")

    def torrents_info(self) -> List[Dict[str, Any]]:
        url = self.api + "torrents/info"
        r = self.s.get(url, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def add_tags(self, hashes: str, tags: str) -> None:
        url = self.api + "torrents/addTags"
        r = self.s.post(url, data={"hashes": hashes, "tags": tags}, timeout=self.timeout)
        r.raise_for_status()


def is_done(t: Dict[str, Any]) -> bool:
    # progress == 1.0 est le critère le plus simple et fiable
    try:
        return float(t.get("progress", 0.0)) >= 1.0
    except Exception:
        return False


def has_tag(t: Dict[str, Any], tag: str) -> bool:
    tags = (t.get("tags") or "").split(",")
    tags = [x.strip() for x in tags if x.strip()]
    return tag in tags


def build_src_path(cfg: Config, content_path: str) -> str:
    """
    content_path remonte souvent un chemin absolu sur la seedbox.
    On essaye d'en faire un chemin relatif par rapport à RCLONE_SRC_ROOT.
    Si ça ne matche pas, on prend le basename comme fallback.
    """
    cp = (content_path or "").rstrip("/")
    root = cfg.rclone_src_root.rstrip("/")

    if cp.startswith(root + "/"):
        rel = cp[len(root) + 1 :]
        return f"{cfg.rclone_remote}:{root}/{rel}"
    if cp == root:
        return f"{cfg.rclone_remote}:{root}"

    base = os.path.basename(cp)
    return f"{cfg.rclone_remote}:{root}/{base}"


def run_rclone_copy(cfg: Config, src: str, dst: str) -> None:
    os.makedirs(dst, exist_ok=True)

    cmd = [
        "rclone",
        "copy",
        src,
        dst,
        "--transfers",
        "4",
        "--checkers",
        "8",
        "--retries",
        "5",
        "--retries-sleep",
        "10s",
        "--ignore-existing",
        "--log-level",
        "INFO",
    ]
    if cfg.rclone_config:
        cmd += ["--config", cfg.rclone_config]

    log("rclone: " + " ".join(shlex.quote(x) for x in cmd))
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"rclone a échoué (code {p.returncode})\n{p.stdout}")


def main() -> int:
    cfg = get_config()
    lock_path = "/var/lock/qBitPuller.lock"
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    lock_fh = open(lock_path, "w", encoding="utf-8")
    try:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("Deja en cours, on quitte")
        return 0

    qb = QbClient(cfg.qb_url, cfg.qb_user, cfg.qb_pass)
    log("Login qBittorrent")
    qb.login()

    torrents = qb.torrents_info()
    wanted = []
    for t in torrents:
        cat = (t.get("category") or "").strip()
        if cat not in ("radarr", "sonarr"):
            continue
        if not is_done(t):
            continue
        if has_tag(t, cfg.pulled_tag):
            continue
        wanted.append(t)

    if not wanted:
        log("Rien à faire")
        return 0

    log(f"{len(wanted)} torrent(s) à récupérer")

    for t in wanted:
        name = t.get("name") or "unknown"
        cat = t.get("category") or "unknown"
        h = t.get("hash") or ""
        content_path = t.get("content_path") or ""

        if not h:
            log(f"Skip {name} car hash manquant")
            continue

        src = build_src_path(cfg, content_path)
        dst = os.path.join(cfg.dest_root, cat, name)

        log(f"Copy {cat}: {name}")
        run_rclone_copy(cfg, src, dst)

        log(f"Tag {cfg.pulled_tag}: {name}")
        qb.add_tags(hashes=h, tags=cfg.pulled_tag)

    log("Done")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log(f"ERROR: {e}")
        return_code = 2
        sys.exit(return_code)
