#!/bin/bash -x

declare -a root_directories=("/mnt/PlexPool/plex/TV-Series" "/mnt/PlexPool/plex/Movies");

# Ask a yes/no question, defaulting to No. Uses an ncurses dialog (dialog, or
# whiptail when dialog is missing) when a terminal is available; otherwise
# falls back to a text prompt. Silence or any non-yes answer means no.
confirm() {
    local message="$1" dialog_tool=""
    if { : </dev/tty; } 2>/dev/null; then
        if command -v dialog >/dev/null 2>&1; then
            dialog_tool=dialog
        elif command -v whiptail >/dev/null 2>&1; then
            dialog_tool=whiptail
        fi
    fi
    if [ -n "$dialog_tool" ]; then
        "$dialog_tool" --defaultno --yesno "$message" 12 72 </dev/tty >/dev/tty 2>/dev/tty
        return $?
    fi
    local answer=""
    read -r -p "$message [y/N] " answer
    [[ "$answer" =~ ^[Yy]([Ee][Ss])?$ ]]
}

for root_directory in "${root_directories[@]}"; do
    echo "$root_directory";
    for directory in "$root_directory"/*/; do
        echo "$directory";
        file_count=$(find "$directory" ! -name '*.jpg' ! -name '*.nfo' ! -name '*.png' ! -name '.plexmatch' | wc -l)
        echo "$file_count"
        if [ "$file_count" == "1" ]; then
            ls -a "$directory";
            echo -e "\tEMPTY";
            if confirm "Remove directory without media (recursively)?

$directory"; then
                rm -rf -- "$directory";
            else
                echo -e "\tkept";
            fi
        else
            echo -e "\tnon-empty";
        fi
        echo
    done
done
