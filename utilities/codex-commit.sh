#!/usr/bin/env bash
set -euo pipefail

repo_root=$(git rev-parse --show-toplevel)
cd "$repo_root"

tmp_files=()
cleanup() {
	if [ "${#tmp_files[@]}" -gt 0 ]; then
		rm -f "${tmp_files[@]}"
	fi
}
trap cleanup EXIT


# Retry only transient transport timeouts, never commits or authentication errors.
git_network_with_retry() {
	local attempt=1 max_attempts=3 status delay error_log
	error_log=$(mktemp "${TMPDIR:-/tmp}/homelab-git-network.XXXXXX")
	tmp_files+=("$error_log")
	while true; do
		if git "$@" 2>&1 | tee "$error_log"; then
			return 0
		else
			status=${PIPESTATUS[0]}
		fi
		if ! LC_ALL=C grep -Eiq 'operation timed out|connection timed out|connection timeout|banner exchange:.*timed out' "$error_log"; then
			return "$status"
		fi
		if [ "$attempt" -ge "$max_attempts" ]; then
			printf 'Git %s still timed out after %s attempts. Check the network and GitHub SSH access. Your local commit is preserved.\n' "$1" "$max_attempts" >&2
			return "$status"
		fi
		delay=$((attempt * 2))
		printf 'Git %s timed out. Retrying in %s seconds (attempt %s/%s)...\n' "$1" "$delay" "$((attempt + 1))" "$max_attempts" >&2
		sleep "$delay"
		attempt=$((attempt + 1))
	done
}

ui_fallback_explained=0

# COMMIT_AGENT selects the CLI that writes commit messages and resolves pull
# conflicts: "codex" (default) or "claude". utilities/claude-commit.sh sets it.
commit_agent=${COMMIT_AGENT:-codex}
case "$commit_agent" in
	codex) agent_label=Codex ;;
	claude) agent_label=Claude ;;
	*)
		echo "Unsupported COMMIT_AGENT '$commit_agent'; use codex or claude." >&2
		exit 1
		;;
esac
script_name="${commit_agent}-commit"

agent_command=()
resolve_codex_command() {
	if [ -n "${CODEX_BIN:-}" ] && [ -x "${CODEX_BIN}" ]; then
		agent_command=("${CODEX_BIN}")
		return 0
	fi

	if command -v codex >/dev/null 2>&1; then
		agent_command=(codex)
		return 0
	fi

	local app_path relative_path candidate
	for app_path in \
		"/Applications/Codex.app" \
		"/Applications/ChatGPT.app" \
		"$HOME/Applications/ChatGPT.app" \
		"$HOME/Applications/Codex.app"; do
		# Support both the original bundle layout and the nested CLI bundle.
		for relative_path in \
			"Contents/Resources/codex" \
			"Contents/Resources/codex-cli/bin/codex" \
			"Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"; do
			candidate="$app_path/$relative_path"
			if [ -x "$candidate" ]; then
				agent_command=("$candidate")
				return 0
			fi
		done
	done

	return 1
}

resolve_claude_command() {
	if [ -n "${CLAUDE_BIN:-}" ] && [ -x "${CLAUDE_BIN}" ]; then
		agent_command=("${CLAUDE_BIN}")
		return 0
	fi

	if command -v claude >/dev/null 2>&1; then
		agent_command=(claude)
		return 0
	fi

	local candidate
	for candidate in \
		"$HOME/.local/bin/claude" \
		"$HOME/.claude/local/claude" \
		"/opt/homebrew/bin/claude" \
		"/usr/local/bin/claude"; do
		if [ -x "$candidate" ]; then
			agent_command=("$candidate")
			return 0
		fi
	done

	return 1
}

resolve_agent_command() {
	case "$commit_agent" in
		codex) resolve_codex_command ;;
		claude) resolve_claude_command ;;
	esac
}

agent_install_hint() {
	case "$commit_agent" in
		codex) printf "Install the Codex CLI, set CODEX_BIN, or add Codex.app's bundled CLI to PATH." ;;
		claude) printf 'Install Claude Code (https://claude.com/claude-code), set CLAUDE_BIN, or add claude to PATH.' ;;
	esac
}

# Run the agent non-interactively. $1 is "read-only" or "workspace-write",
# $2 is the file that receives the final message, $3 is the prompt.
run_agent() {
	local mode=$1
	local output_file=$2
	local prompt=$3

	if [ "$commit_agent" = codex ]; then
		"${agent_command[@]}" exec \
			-C "$repo_root" \
			--sandbox "$mode" \
			--output-last-message "$output_file" \
			"$prompt"
		return
	fi

	local read_tools="Read,Grep,Glob,Bash(git status:*),Bash(git diff:*),Bash(git log:*),Bash(git show:*),Bash(git ls-files:*)"
	local denied_tools="Bash(git commit:*),Bash(git push:*),Bash(git reset:*),Bash(git rebase:*),Bash(git checkout:*),Bash(git restore:*),Bash(git stash:*)"
	local claude_args=(-p --output-format text --disallowedTools "$denied_tools")

	if [ "$mode" = workspace-write ]; then
		claude_args+=(--permission-mode acceptEdits --allowedTools "$read_tools,Edit,Write")
	else
		claude_args+=(--permission-mode default --allowedTools "$read_tools")
	fi

	# The commit-message skill may live outside the repository.
	if [ -n "${commit_skill_path:-}" ]; then
		claude_args+=(--add-dir "$(dirname "$commit_skill_path")")
	fi

	# --allowedTools/--disallowedTools/--add-dir take variadic values, so the
	# prompt goes on stdin.
	(cd "$repo_root" && printf '%s\n' "$prompt" | "${agent_command[@]}" "${claude_args[@]}") >"$output_file"
}

agent_needs_login() {
	local output_file=$1
	[ "$commit_agent" = claude ] &&
		LC_ALL=C grep -Eiq 'not logged in|please run /login|invalid api key|oauth token (has )?expired' "$output_file"
}

# Run the agent; if Claude reports it is not logged in, offer to run
# `claude auth login` and retry once. Declining keeps the original failure.
run_agent_with_login() {
	local output_file=$2

	if run_agent "$@"; then
		return 0
	fi

	if ! agent_needs_login "$output_file"; then
		return 1
	fi

	if ! confirm "$agent_label login" "$agent_label is not logged in:

$(head -n 3 "$output_file")

Run '${agent_command[*]} auth login' now and retry?"; then
		echo "Skipping login. Run 'claude auth login' and try again." >&2
		return 1
	fi

	# Callers discard stdout, so send the login URL/prompts to stderr.
	if ! "${agent_command[@]}" auth login >&2; then
		echo "$agent_label login failed." >&2
		return 1
	fi

	echo "Logged in. Retrying $agent_label..." >&2
	run_agent "$@"
}

terminal_ui_unavailable_reason() {
	if [ ! -e /dev/tty ]; then
		printf 'no controlling terminal is available'
		return 0
	fi

	if [ ! -r /dev/tty ] || [ ! -w /dev/tty ]; then
		printf '/dev/tty is not readable and writable'
		return 0
	fi

	if [ -z "${TERM:-}" ]; then
		printf 'TERM is not set'
		return 0
	fi

	if [ "${TERM:-}" = "dumb" ]; then
		printf 'TERM=dumb'
		return 0
	fi

	if ! infocmp "$TERM" >/dev/null 2>&1; then
		printf 'no terminfo entry exists for TERM=%s' "$TERM"
		return 0
	fi

	return 1
}

can_use_terminal_ui() {
	! terminal_ui_unavailable_reason >/dev/null
}

can_use_dialog() {
	command -v dialog >/dev/null 2>&1 && can_use_terminal_ui
}

can_use_whiptail() {
	command -v whiptail >/dev/null 2>&1 && can_use_terminal_ui
}

explain_plain_prompt_fallback() {
	local reason

	if [ "$ui_fallback_explained" -eq 1 ]; then
		return
	fi

	ui_fallback_explained=1

	if reason=$(terminal_ui_unavailable_reason); then
		printf 'Using plain terminal prompts because the dialog UI is unavailable: %s.\n' "$reason" >&2
		return
	fi

	if ! command -v dialog >/dev/null 2>&1 && ! command -v whiptail >/dev/null 2>&1; then
		printf 'Using plain terminal prompts because neither dialog nor whiptail is on PATH.\n' >&2
	fi
}

read_prompt_answer() {
	local variable_name=$1

	if [ -r /dev/tty ] && [ -w /dev/tty ]; then
		IFS= read -r "$variable_name" </dev/tty
		return
	fi

	IFS= read -r "$variable_name"
}

prompt_menu() {
	local title=$1
	local text=$2
	shift 2

	if can_use_dialog; then
		dialog --stdout --title "$title" --menu "$text" 15 76 5 "$@" \
			2>/dev/tty </dev/tty
		return
	fi

	if can_use_whiptail; then
		whiptail --title "$title" --menu "$text" 15 76 5 "$@" \
			3>&1 1>/dev/tty 2>&3 </dev/tty
		return
	fi

	explain_plain_prompt_fallback

	local choices=("$@")
	local index=1
	local i
	printf '%s\n%s\n' "$title" "$text" >&2
	for ((i = 0; i < ${#choices[@]}; i += 2)); do
		printf '  %d. %s - %s\n' "$index" "${choices[$i]}" "${choices[$((i + 1))]}" >&2
		index=$((index + 1))
	done
	printf 'Choose [1-%d]: ' "$((index - 1))" >&2
	if ! read_prompt_answer answer; then
		printf '\nNo interactive input was available.\n' >&2
		return 1
	fi
	if ! [[ "$answer" =~ ^[0-9]+$ ]] || [ "$answer" -lt 1 ] || [ "$answer" -ge "$index" ]; then
		return 1
	fi
	printf '%s\n' "${choices[$(((answer - 1) * 2))]}"
}

confirm() {
	local title=$1
	local text=$2

	if can_use_dialog; then
		dialog --title "$title" --yesno "$text" 20 90 >/dev/tty 2>&1 </dev/tty
		return
	fi

	if can_use_whiptail; then
		whiptail --title "$title" --yesno "$text" 20 90 >/dev/tty 2>&1 </dev/tty
		return
	fi

	explain_plain_prompt_fallback

	local answer
	printf '%s\n%s [y/N]: ' "$title" "$text" >&2
	if ! read_prompt_answer answer; then
		printf '\nNo interactive input was available.\n' >&2
		return 1
	fi
	[[ "$answer" =~ ^[Yy]$|^[Yy][Ee][Ss]$ ]]
}

whiptail_supports_extra_button() {
	whiptail --help 2>&1 | grep -q -- '--extra-button'
}

prompt_push_action() {
	local text=$1
	local status

	if can_use_dialog; then
		dialog \
			--title "Push" \
			--yes-button "Push" \
			--extra-button \
			--extra-label "Show diff" \
			--no-button "Cancel" \
			--yesno "$text" 20 90 \
			>/dev/tty 2>&1 </dev/tty
		status=$?
		case "$status" in
			0) printf 'push\n' ;;
			1) printf 'skip\n' ;;
			3) printf 'diff\n' ;;
			*) return 1 ;;
		esac
		return
	fi

	if can_use_whiptail; then
		if whiptail_supports_extra_button; then
			whiptail \
				--title "Push" \
				--yes-button "Push" \
				--extra-button \
				--extra-label "Show diff" \
				--no-button "Cancel" \
				--yesno "$text" 20 90 \
				>/dev/tty 2>&1 </dev/tty
			status=$?
			case "$status" in
				0) printf 'push\n' ;;
				1) printf 'skip\n' ;;
				3) printf 'diff\n' ;;
				*) return 1 ;;
			esac
			return
		fi

		whiptail \
			--title "Push" \
			--yes-button "Push" \
			--no-button "Show diff" \
			--yesno "$text

Press Esc to cancel." 22 90 \
			>/dev/tty 2>&1 </dev/tty
		status=$?
		case "$status" in
			0) printf 'push\n' ;;
			1) printf 'diff\n' ;;
			*) return 1 ;;
		esac
		return
	fi

	prompt_menu \
		"Push" \
		"$text" \
		push "Run git push now" \
		diff "Show diff for HEAD~1..HEAD" \
		skip "Cancel"
}

has_staged_changes() {
	! git diff --cached --quiet
}

has_unmerged_paths() {
	[ -n "$(git diff --name-only --diff-filter=U)" ]
}

view_last_commit_diff() {
	if [ -r /dev/tty ] && [ -w /dev/tty ]; then
		git diff HEAD~1 HEAD -- >/dev/tty </dev/tty
	else
		git diff HEAD~1 HEAD --
	fi
}

resolve_pull_conflict_with_agent() {
	local conflict_output
	local conflicted_files=()
	local file

	conflict_output=$(mktemp "${TMPDIR:-/tmp}/homelab-${commit_agent}-conflict.XXXXXX")
	tmp_files+=("$conflict_output")

	while IFS= read -r file; do
		conflicted_files+=("$file")
	done < <(git diff --name-only --diff-filter=U)

	printf '\nGit pull produced conflicts. Attempting to resolve them with %s...\n' "$agent_label"
	printf 'Conflicted files:\n'
	printf '  - %s\n' "${conflicted_files[@]}"

	if ! run_agent_with_login workspace-write "$conflict_output" \
		'Git is currently stopped on a pull/rebase conflict. Inspect the conflicted files, resolve the conflict markers in the working tree, preserve the intended behavior from both sides where possible, and do not commit, push, reset, abort, or continue the rebase. After editing, report whether all conflict markers and unmerged paths are resolved.'; then
		echo "$agent_label failed while attempting to resolve the pull conflict."
		cat "$conflict_output" >&2
		return 1
	fi

	# Paths stay unmerged in the index until they are staged, so check the
	# files for leftover markers and then stage them.
	for file in "${conflicted_files[@]}"; do
		if [ -f "$file" ] && grep -qE '^(<<<<<<<|>>>>>>>)( |$)|^=======$' -- "$file"; then
			echo "$agent_label was not able to resolve all git conflicts; markers remain in $file."
			cat "$conflict_output" >&2
			return 1
		fi
	done
	git add -A -- "${conflicted_files[@]}"

	if has_unmerged_paths; then
		echo "$agent_label was not able to resolve all git conflicts."
		printf 'Still conflicted:\n'
		git diff --name-only --diff-filter=U | sed 's/^/  - /'
		cat "$conflict_output" >&2
		return 1
	fi

	if GIT_EDITOR=true git rebase --continue; then
		echo "$agent_label resolved the git conflict and the rebase continued successfully."
		return 0
	fi

	echo "$agent_label resolved the files, but git rebase --continue failed."
	return 1
}

extract_commit_message() {
	local output_file=$1
	local message

	message=$(
		awk '
			/^```/ {
				if (!seen) {
					seen = 1
					in_block = 1
					next
				}
				if (in_block) {
					exit
				}
			}
			in_block {
				print
			}
		' "$output_file" | sed '/^[[:space:]]*$/d; s/[[:space:]]*$//'
	)

	if [ -z "$message" ]; then
		message=$(sed '/^[[:space:]]*$/d; /^```/d; s/[[:space:]]*$//' "$output_file" | head -n 1)
	fi

	printf '%s\n' "$message"
}

commit_skill_path=
find_commit_skill() {
	local skill_path

	for skill_path in \
		"$repo_root/doc/codex-skills/SKIL_suggest-commit-message/SKILL.md" \
		"$HOME/.codex/skills/SKIL_suggest-commit-message/SKILL.md" \
		"$HOME/.claude/skills/SKIL_suggest-commit-message/SKILL.md" \
		"$HOME/.claude/skills/suggest-commit-message/SKILL.md"; do
		if [ -f "$skill_path" ]; then
			commit_skill_path=$skill_path
			return
		fi
	done
}

build_commit_prompt() {
	if [ -n "$commit_skill_path" ]; then
		printf 'Use the suggest-commit-message skill at %s to propose a concise commit message for the currently staged changes. If that path is inaccessible, inspect the staged changes directly. Return the recommended commit message as the first fenced code block.\n' "$commit_skill_path"
		return
	fi

	printf 'Inspect the currently staged changes and propose a concise git commit message. Return the recommended commit message as the first fenced code block, with no extra text before it.\n'
}

if ! has_staged_changes; then
	echo "No staged changes to commit. Run git add for the files you want to commit, then try again."
	exit 0
fi

if ! resolve_agent_command; then
	echo "$commit_agent command not found. $(agent_install_hint)" >&2
	exit 1
fi

agent_output=$(mktemp "${TMPDIR:-/tmp}/homelab-${commit_agent}-commit.XXXXXX")
tmp_files+=("$agent_output")
find_commit_skill
commit_prompt=$(build_commit_prompt)

if ! run_agent_with_login read-only "$agent_output" "$commit_prompt" >/dev/null; then
	# claude -p reports errors such as "Not logged in" on stdout, which
	# run_agent captures, so surface it instead of exiting silently.
	echo "$agent_label failed while generating the commit message:" >&2
	cat "$agent_output" >&2
	exit 1
fi

commit_message=$(extract_commit_message "$agent_output")
if [ -z "$commit_message" ]; then
	echo "Could not parse a commit message from $agent_label output:" >&2
	cat "$agent_output" >&2
	exit 1
fi

git commit -m "$commit_message"

printf '\nCommitted with message:\n'
printf '%s\n' "$commit_message"

printf '\nCurrent git status:\n'
git status --short --branch

upstream=
if upstream=$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null); then
	echo "Fetching $upstream before push check..."
	git_network_with_retry fetch --quiet
	read -r ahead behind < <(git rev-list --left-right --count HEAD..."$upstream")

	if [ "$behind" -gt 0 ]; then
		if confirm "Pull before push" "Upstream $upstream has $behind commit(s) not in this branch. Run git pull --rebase before push?"; then
			# Preserve tracked work that was intentionally left unstaged while
			# rebasing the newly created commit onto the updated upstream.
			if ! git_network_with_retry pull --rebase --autostash; then
				if has_unmerged_paths; then
					resolve_pull_conflict_with_agent || {
						echo "Aborting $script_name because the git conflict was not resolved."
						exit 1
					}
				else
					echo "git pull --rebase --autostash failed without unmerged paths. Aborting."
					exit 1
				fi
			fi
		else
			echo "Skipping push because upstream has new commits."
			exit 0
		fi
	fi
else
	echo "No upstream branch is configured; git push will use Git's default behavior."
fi

push_summary=$(
	printf '1. git log message\n'
	git log -1 --format=%B
	printf '\n2. files changed in HEAD~1..HEAD\n'
	changed_files=$(git diff --name-status HEAD~1 HEAD --)
	if [ -n "$changed_files" ]; then
		printf '%s\n' "$changed_files"
	else
		printf 'No files changed in HEAD~1..HEAD.\n'
	fi
	printf '\nChoose the next action.'
)

while true; do
	push_choice=$(
		prompt_push_action "$push_summary"
	) || {
		echo "Push skipped."
		exit 0
	}

	case "$push_choice" in
		push)
			git_network_with_retry push
			break
			;;
		diff)
			view_last_commit_diff
			;;
		skip)
			echo "Push skipped."
			break
			;;
		*)
			echo "Unexpected choice: $push_choice" >&2
			exit 1
			;;
	esac
done
