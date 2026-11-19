#!/usr/bin/env bash
# Shared helpers for SJ oneshot worktree — harus dijalankan sebagai non-root (eternaladmin).
# Tidak mencetak secret.

oneshot_worktree_gitdir() {
  local wt="$1"
  if [[ -f "$wt/.git" ]]; then
    sed -n 's/^gitdir:[[:space:]]*//p' "$wt/.git" | head -n1
  elif [[ -d "$wt/.git" ]]; then
    printf '%s\n' "$wt/.git"
  else
    return 1
  fi
}

oneshot_worktree_index_path() {
  local wt="$1"
  local gitdir
  gitdir="$(oneshot_worktree_gitdir "$wt")" || return 1
  printf '%s/index\n' "$gitdir"
}

oneshot_worktree_index_writable() {
  local wt="$1"
  local idx
  idx="$(oneshot_worktree_index_path "$wt")" || return 1
  [[ -e "$idx" ]] || return 1
  [[ -r "$idx" && -w "$idx" ]]
}

# Perbaiki kepemilikan HANYA path worktree + admin gitdir worktrees/* (bukan seluruh repo).
# Dipanggil sebagai root sebelum drop privilege.
oneshot_repair_worktree_ownership() {
  local wt="$1"
  local user="$2"
  local repo="$3"
  [[ -e "$wt" ]] || return 0
  local gitdir=""
  if [[ -f "$wt/.git" ]]; then
    gitdir="$(sed -n 's/^gitdir:[[:space:]]*//p' "$wt/.git" | head -n1)"
  fi
  if [[ -n "$gitdir" && -d "$gitdir" ]]; then
    case "$gitdir" in
      "$repo"/.git/worktrees/*)
        chown -R "$user:$user" "$gitdir"
        ;;
      *)
        printf 'FAIL: gitdir worktree di luar %s/.git/worktrees/: %s\n' "$repo" "$gitdir" >&2
        return 1
        ;;
    esac
  fi
  chown -R "$user:$user" "$wt"
}

# Pastikan worktree siap: fetch + add/reuse. Skip checkout jika SHA benar, bersih, index writable.
# Environment: REPO_ORIGIN, BRANCH, WT_ROOT, EXPECTED_SHA (opsional)
oneshot_ensure_worktree() {
  local repo="${1:-$REPO_ORIGIN}"
  local branch="${2:-$BRANCH}"
  local wt="${3:-$WT_ROOT}"
  local want_sha="${4:-${EXPECTED_SHA:-}}"

  if [[ "$(id -u)" -eq 0 ]]; then
    printf 'FAIL: oneshot_ensure_worktree dilarang sebagai root\n' >&2
    return 1
  fi

  [[ -d "$repo/.git" ]] || { printf 'FAIL: repo missing\n' >&2; return 1; }

  git -C "$repo" fetch origin "$branch" --quiet
  local tip
  tip="$(git -C "$repo" rev-parse "origin/$branch")"
  if [[ -z "$want_sha" ]]; then
    want_sha="$tip"
  fi
  if [[ "$want_sha" != "$tip" ]]; then
    printf 'FAIL: EXPECTED_SHA %s != origin/%s tip %s\n' "$want_sha" "$branch" "$tip" >&2
    return 1
  fi

  mkdir -p "$(dirname "$wt")"
  local created=0
  if [[ -e "$wt" ]]; then
    if ! git -C "$wt" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
      printf 'FAIL: WT ada tetapi bukan git worktree: %s\n' "$wt" >&2
      return 1
    fi
    if ! oneshot_worktree_index_writable "$wt"; then
      printf 'FAIL: index worktree tidak writable oleh %s — perbaiki ownership admin gitdir lalu ulang\n' \
        "$(id -un)" >&2
      return 1
    fi
    local cur
    cur="$(git -C "$wt" rev-parse HEAD)"
    local dirty
    dirty="$(git -C "$wt" status --porcelain 2>/dev/null || echo DIRTY)"
    if [[ "$cur" == "$want_sha" && -z "$dirty" ]]; then
      printf 'worktree_reuse=%s head=%s checkout=skipped\n' "$wt" "$cur"
      WT_CREATED_BY_ONESHOT=0
      return 0
    fi
    printf 'worktree_reuse=%s checkout=update want=%s have=%s\n' "$wt" "$want_sha" "$cur"
    git -C "$wt" checkout -f "$want_sha" --quiet
  else
    git -C "$repo" worktree add --detach "$wt" "$want_sha"
    created=1
    WT_CREATED_BY_ONESHOT=1
    printf 'worktree_created=%s head=%s\n' "$wt" "$(git -C "$wt" rev-parse HEAD)"
  fi

  if ! oneshot_worktree_index_writable "$wt"; then
    printf 'FAIL: index worktree tidak writable setelah ensure\n' >&2
    return 1
  fi
  local head
  head="$(git -C "$wt" rev-parse HEAD)"
  if [[ "$head" != "$want_sha" ]]; then
    printf 'FAIL: worktree HEAD %s != %s\n' "$head" "$want_sha" >&2
    return 1
  fi
  return 0
}
