#!/bin/bash
set -euo pipefail

# TODO DEBUG
#echo DISABLED FOR DEBUG
#exit 1

# https://stackoverflow.com/a/18216122
if [ "$EUID" -ne 0 ]; then 
	echo "Please run as root"
	exit 1
fi

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

./nord-run.sh
mountpoint -q /mnt/PlexPool/plex || mount -vvv /mnt/PlexPool/plex
mountpoint -q /mnt/MargokPool/home || mount -vvv /mnt/MargokPool/home
./fix-config.sh
./transmission-daemon-keepalive.sh # TODO later in background

