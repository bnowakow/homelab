#!/bin/bash
# SUDO_ASKPASS helper: Ansible tasks have no controlling terminal, so plain
# sudo cannot ask for the password. Show a hidden password prompt on the
# provisioning terminal and hand the answer to sudo only; it is never printed,
# logged or stored. Cancelling makes sudo fail.
# sudo runs this helper with a cleaned environment, so the terminal, TERM and
# PATH are passed by files/sudo-askpass-wrapper.sh as arguments of the call.
# Usage: sudo-askpass.sh TTY TERM PATH [prompt]
tty=$1 term=$2 path=$3
shift 3
PATH=$path TERM=$term dialog --clear --stdout --title 'Administrator password' \
  --ok-label Continue --cancel-label Abort --default-button cancel \
  --passwordbox "${1:-Password:}\n\nProvisioning needs your administrator (sudo) password for this step." 10 70 \
  < "$tty" 2> "$tty"
