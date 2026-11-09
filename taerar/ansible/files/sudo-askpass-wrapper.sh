#!/bin/bash
# Prints the path of a private temporary SUDO_ASKPASS wrapper that calls
# sudo-askpass.sh with the current terminal, TERM and PATH baked in (sudo
# cleans the environment before running an askpass helper). The caller removes
# the wrapper when done. Usage: wrapper=$(sudo-askpass-wrapper.sh STATE_DIR TTY)
here=$(cd "$(dirname "$0")" && pwd)
wrapper=$(mktemp "$1/askpass.XXXXXX") || exit 1
printf '#!/bin/bash\nexec %q %q %q %q "$@"\n' "$here/sudo-askpass.sh" "$2" "${TERM:-xterm-256color}" "$PATH" >| "$wrapper"
chmod 700 "$wrapper"
echo "$wrapper"
