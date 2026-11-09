#!/bin/bash
# SUDO_ASKPASS helper. Ansible tasks have no controlling terminal and sudo
# runs this helper with a cleaned environment, so it cannot use the terminal:
# it shows the native macOS password dialog (hidden input) instead. The answer
# goes to sudo only; it is never printed, logged or stored. Cancel makes sudo fail.
exec /usr/bin/osascript \
  -e 'on run argv' \
  -e 'set reply to display dialog ((item 1 of argv) & return & return & "Provisioning needs your administrator (sudo) password for this step.") default answer "" with hidden answer with title "Administrator password" buttons {"Cancel", "OK"} default button "OK" with icon caution' \
  -e 'return text returned of reply' \
  -e 'end run' \
  "${1:-Password:}"
