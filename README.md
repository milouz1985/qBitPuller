# qBitPuller

Petit service systemd qui lit les torrents terminés via l'API qBittorrent et copie les contenus avec rclone.

## Installation

```sh
./install.sh
```

Le script n'écrase pas `/etc/qBitPuller.env` s'il existe déjà.

## Configuration

Copiez et adaptez `/etc/qBitPuller.env` :

- `QB_URL` : URL de la WebUI qBittorrent (ex: https://.../qbittorrent)
- `QB_USER` / `QB_PASS` : identifiants WebUI
- `QB_TIMEOUT` : timeout HTTP (secondes)
- `RCLONE_REMOTE` : nom du remote rclone
- `RCLONE_SRC_ROOT` : racine des downloads sur la seedbox
- `DEST_ROOT` : destination locale
- `CATEGORIES` : catégories qBittorrent a traiter (ex: radarr,sonarr)
- `PULLED_TAG` : tag à ajouter après copie
- `LOG_LEVEL` : INFO ou DEBUG
- `RCLONE_CONFIG` : chemin vers le config rclone (optionnel)

## Service

```sh
systemctl enable --now qBitPuller.timer
systemctl status qBitPuller.service
journalctl -fu qBitPuller
```