#!/bin/bash

cd /home/sup/code/homelab/proxmox/cron;
while true; do
    ./check-if-transmission-is-up-and-restart-vm-otherwise.sh
    script_exit_code=$?
    if [ $script_exit_code -ne 0 ]; then
        echo "$(date), check script exited with code $script_exit_code; continuing test loop"
    fi
    echo "$(date), sleeping";
    sleep 15m;
done


