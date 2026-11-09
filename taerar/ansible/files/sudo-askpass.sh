#!/bin/bash
# SUDO_ASKPASS helper: Ansible tasks have no controlling terminal, so plain
# sudo cannot ask for the password. Show a hidden password prompt on the
# provisioning terminal and hand the answer to sudo only; it is never printed,
# logged or stored. Cancelling makes sudo fail.
tty=${HOMELAB_PROVISION_TTY:-/dev/tty}
TERM=${TERM:-xterm-256color} dialog --clear --stdout --title 'Administrator password' \
  --ok-label Continue --cancel-label Abort --default-button cancel \
  --passwordbox "${1:-Password:}\n\nProvisioning needs your administrator (sudo) password for this step." 10 70 \
  < "$tty" 2> "$tty"
