# Agent interaction

- Ask questions that need user input with the ncurses `dialog` utility by
  default. Use appropriate dialog widgets such as `--yesno`, `--menu`, or
  `--inputbox` instead of plain terminal prompts.
- If `dialog` is unavailable or no interactive terminal is available, use a
  clear fallback that preserves safe defaults and does not treat silence as
  approval.

After modifying any Zabbix script, run `sudo ./zabbix/update-zabbix-metadata.sh` from the homelab repository root on the host where you made the change.
