#!/bin/sh
# Move the launch media in demos/launch-assets/ into Git LFS (audit finding H-3).
#
#   sh scripts/migrate_launch_media_to_lfs.sh                      migrate, commit, print next steps
#   sh scripts/migrate_launch_media_to_lfs.sh --verify-site [URL]  check what a deployed site serves
#   sh scripts/migrate_launch_media_to_lfs.sh --help
#
# Run the migration once, from a clean, up-to-date checkout of main, on a machine with
# git-lfs installed that can reach GitHub's LFS endpoint (the push uploads the objects
# there). On main it creates the branch lfs/launch-media, or reuses it when it contains
# main; on any other branch it commits there. Whether it starts on main or on
# lfs/launch-media, a lfs/launch-media that lacks commits of main stops the script before
# it changes anything. It routes the media through Git LFS in .gitattributes, re-adds
# them so the index holds LFS pointers, checks every staged blob against the pointer
# git-lfs computes for the working file, commits, checks the commit, and prints the push
# and verification steps. It never pushes and never rewrites history: the migration is
# one new commit and the working files stay byte-identical. Running it again makes no new
# commit; run from main, it switches to lfs/launch-media.
#
# It writes the Git LFS filter settings into .git/config. The LFS filter that converts the
# files may then add the Git LFS pre-push, post-checkout, post-commit and post-merge hooks
# where no hook of that name exists, as it does in any clone with LFS content: in
# .git/hooks, or in core.hooksPath when that is set, even globally, and every repository
# that uses that directory then runs them. The script prints a note when core.hooksPath is
# set. Hooks of your own stay as they are, and the printed git lfs push uploads the objects
# whatever the pre-push hook does.
#
# --verify-site downloads each media file from a deployed site (default: https:// plus
# demos/CNAME) and compares its SHA-256 and size with the media committed at HEAD. A file
# that arrives as an LFS pointer means the Pages deploy checked out without lfs: true.
# Files that differ and files that could not be downloaded are counted apart, and either
# makes it exit 1.
#
# docs/release-checklist.md ("Launch Media in Git LFS") covers quotas and contributors.

set -eu
# The LFS patterns must reach git verbatim, never as shell globs.
set -f

BRANCH=lfs/launch-media
MEDIA_DIR=demos/launch-assets
# Every tracked file in MEDIA_DIR when H-3 was prepared: six media files, stored in LFS
# by extension, and two JSON manifests that stay plain Git files because
# tests/seo_static_smoke.mjs reads them. Any other tracked file there stops the script,
# so a new asset is never left behind as a plain blob or converted by accident.
MEDIA_EXTENSIONS="gif mp4 png"
MEDIA_FILES="
demos/launch-assets/headliner-prompt-four-outputs.gif
demos/launch-assets/headliner-prompt-four-outputs.mp4
demos/launch-assets/headliner-prompt-four-outputs.png
demos/launch-assets/hn-launch-demo-poster.png
demos/launch-assets/hn-launch-demo.gif
demos/launch-assets/hn-launch-demo.mp4
"
PLAIN_FILES="
demos/launch-assets/headliner-manifest.json
demos/launch-assets/hn-launch-demo-manifest.json
"
POINTER_VERSION="version https://git-lfs.github.com/spec/v1"
SELF=scripts/migrate_launch_media_to_lfs.sh
# Printed after an error once the script has started to change the working tree.
RECOVERY=

say() { printf '%s\n' "$*"; }
die() {
    printf 'error: %s\n' "$*" >&2
    if [ -n "$RECOVERY" ]; then
        printf '%s\n' "$RECOVERY" >&2
    fi
    exit 1
}
# Echo a command that changes the repository, then run it.
run() {
    printf '+ %s\n' "$*"
    "$@"
}

usage() {
    cat <<EOF
usage: sh $SELF [--verify-site [URL]]

Without options, move the media in $MEDIA_DIR/ into Git LFS with one new commit
(on $BRANCH when run on main) and print the push and verification steps.

  --verify-site [URL]  check that the site at URL serves the media committed at HEAD
                       and not Git LFS pointer files (default: https:// + demos/CNAME)
  --help               show this help
EOF
}

# in_list WORD LIST: true when WORD is one of the whitespace-separated words of LIST.
in_list() {
    for item in $2; do
        if [ "$item" = "$1" ]; then
            return 0
        fi
    done
    return 1
}

# True when the blob named by $1 (HEAD:<path> or :<path> for the index) is an LFS pointer.
blob_is_pointer() {
    size=$(git cat-file -s "$1" 2>/dev/null) || return 1
    [ "$size" -lt 1024 ] || return 1
    [ "$(git cat-file blob "$1" | head -n 1)" = "$POINTER_VERSION" ]
}

# True when the file $1 holds an LFS pointer instead of the media itself.
file_is_pointer() {
    [ -f "$1" ] || return 1
    size=$(wc -c <"$1" | tr -d '[:space:]')
    [ "$size" -lt 1024 ] || return 1
    [ "$(head -n 1 "$1")" = "$POINTER_VERSION" ]
}

# True when .gitattributes sends the path $1 through the LFS filter.
routed_through_lfs() {
    [ "$(git check-attr filter -- "$1")" = "$1: filter: lfs" ]
}

require_clean_tree() {
    status=$(git status --porcelain)
    if [ -n "$status" ]; then
        die "the working tree is not clean; commit, stash or remove these first:
$status"
    fi
}

check_inventory() {
    tracked=$(git ls-files -- "$MEDIA_DIR")
    problems=
    for f in $tracked; do
        if ! in_list "$f" "$MEDIA_FILES $PLAIN_FILES"; then
            problems="$problems
  not listed in $SELF: $f"
        fi
    done
    for f in $MEDIA_FILES $PLAIN_FILES; do
        if ! in_list "$f" "$tracked"; then
            problems="$problems
  listed but not tracked: $f"
        elif [ ! -f "$f" ]; then
            problems="$problems
  tracked but missing from the working tree: $f"
        fi
    done
    if [ -n "$problems" ]; then
        die "$MEDIA_DIR/ no longer holds the files this script was written for:$problems
Update MEDIA_FILES, PLAIN_FILES and MEDIA_EXTENSIONS in $SELF, then run it again."
    fi
}

# True when every media blob at HEAD is an LFS pointer that .gitattributes routes through LFS.
head_is_migrated() {
    for f in $MEDIA_FILES; do
        blob_is_pointer "HEAD:$f" || return 1
        routed_through_lfs "$f" || return 1
    done
    return 0
}

require_media_contents() {
    for f in $MEDIA_FILES; do
        if [ ! -s "$f" ]; then
            die "$f is empty; Git LFS keeps empty files as plain blobs, so check the asset first"
        fi
        if file_is_pointer "$f"; then
            die "$f is an LFS pointer, not the media; run 'git lfs pull' to download it first"
        fi
    done
}

# check_blobs PREFIX: the media blobs named PREFIX<path> (":" for the index, "HEAD:" for
# the commit) are LFS pointers, equal to the pointer git-lfs computes for each working
# file, and the manifests are not pointers.
check_blobs() {
    for f in $MEDIA_FILES; do
        blob_is_pointer "$1$f" || die "$1$f is not a Git LFS pointer"
        if ! file_is_pointer "$f"; then
            expected=$(git lfs pointer --file="$f" 2>/dev/null) || die "git lfs pointer failed on $f"
            if [ "$(git cat-file blob "$1$f")" != "$expected" ]; then
                die "$1$f is not the LFS pointer of the working copy of $f"
            fi
        fi
    done
    for f in $PLAIN_FILES; do
        if blob_is_pointer "$1$f"; then
            die "$1$f became an LFS pointer; the manifests must stay plain files"
        fi
    done
    lfs_files=$(git lfs ls-files --name-only)
    for f in $MEDIA_FILES; do
        in_list "$f" "$lfs_files" || die "git lfs ls-files does not list $f"
    done
    for f in $PLAIN_FILES; do
        if in_list "$f" "$lfs_files"; then
            die "git lfs ls-files lists $f, which must stay a plain file"
        fi
    done
}

# The commit is complete: pointers at HEAD, every object present and intact in the local
# LFS store (the push uploads from there), and nothing left uncommitted.
check_head() {
    check_blobs "HEAD:"
    git lfs fsck || die "git lfs fsck found a missing or corrupt LFS object at HEAD; run 'git lfs pull' and try again"
    require_clean_tree
}

origin_url() {
    git remote get-url origin 2>/dev/null || printf '%s\n' "<the GitHub URL of your origin remote>"
}

site_url() {
    if [ -s demos/CNAME ]; then
        printf 'https://%s\n' "$(tr -d '[:space:]' <demos/CNAME)"
    else
        printf '%s\n' "<the Pages site URL>"
    fi
}

print_site_check() {
    cat <<EOF
Once the "Deploy to GitHub Pages" run for this commit has finished, check that the site
serves the media and not LFS pointer files:

    sh $SELF --verify-site $(site_url)
EOF
}

# Git takes the hooks from core.hooksPath when that is set, and the LFS filter adds the Git
# LFS hooks there as well. A global or system setting shares that directory with every
# repository that does not set its own, so say where the hooks may go, and who runs them.
note_hooks_path() {
    [ -n "$(git config --get core.hooksPath)" ] || return 0
    # Scope, origin and value, separated by tabs; --show-scope needs Git 2.26.
    setting=$(git config --show-scope --show-origin --get core.hooksPath 2>/dev/null) || setting=
    scope=$(printf '%s\n' "$setting" | cut -f1)
    origin=$(printf '%s\n' "$setting" | cut -f2 | sed 's/^file://')
    shared=
    case $scope in
        global | system)
            where="your $scope Git config ($origin)"
            shared=", and of every other repository that does not set its
own core.hooksPath,"
            ;;
        local | worktree) where="this repository's Git config ($origin)" ;;
        *) where="your Git configuration" ;;
    esac
    say "Note: core.hooksPath is set in $where.
Git runs the hooks of this repository$shared from
    $(git rev-parse --git-path hooks)
The Git LFS filter may add its pre-push, post-checkout, post-commit and post-merge hooks
there, where no hook of that name exists. Every repository that uses that directory then
runs them, and git push fails in each one whenever git-lfs is not on PATH."
}

# True when $1 is a hook that git runs and that calls git lfs pre-push, which uploads the
# LFS objects of each push.
hook_runs_lfs_pre_push() {
    [ -f "$1" ] && [ -x "$1" ] && grep -Eq '^[^#]*git(-| +)lfs +pre-push' "$1"
}

print_next_steps() {
    # git rev-parse follows core.hooksPath, as git does when it runs the hook.
    hook="$(git rev-parse --git-path hooks)/pre-push"
    cat <<EOF

Next steps. Nothing has been pushed; run these yourself:

  1. Upload the LFS objects, then push the branch. It is a new branch, so a plain push
     is enough:

       git lfs push origin $1
       git push --set-upstream origin $1
EOF
    if ! hook_runs_lfs_pre_push "$hook"; then
        cat <<EOF

     Note: no pre-push hook runs git lfs pre-push here ($hook), so git push
     alone uploads no LFS objects; the git lfs push above does. Before you push other LFS
     content from this clone, add the Git LFS hooks: git lfs update installs them, and
     git lfs update --manual shows how to merge them into hooks you already have.
EOF
    fi
    cat <<EOF

  2. Check that GitHub holds every object. A fresh clone downloads them from GitHub and
     git lfs fsck re-hashes them against their pointers:

       dir=\$(mktemp -d) && git clone --quiet --branch $1 --single-branch $(origin_url) "\$dir" && git -C "\$dir" lfs fetch && git -C "\$dir" lfs fsck

  3. Open a pull request into main and merge it once CI passes:

       gh pr create --base main --head $1 --fill

  4. The merge deploys the site. Once the "Deploy to GitHub Pages" run for the merge
     commit has finished, check that the site serves the media and not LFS pointers:

       sh $SELF --verify-site $(site_url)
EOF
}

commit_migration() {
    git commit -q -F - <<'EOF'
Store the launch media in Git LFS (audit H-3)

The GIF, MP4 and PNG files in demos/launch-assets/ were plain Git blobs,
so every re-encode added its full size to the history that each clone
downloads. They are now Git LFS pointers and the files themselves live
in GitHub's LFS storage, byte-identical. History is not rewritten: the
earlier blobs stay where they are, and later versions go to LFS instead.

The Pages deploy checks out with lfs: true, so the site still serves the
media. Made with scripts/migrate_launch_media_to_lfs.sh.
EOF
}

# require_branch_contains_main REF: $BRANCH must contain main before the script builds on
# it, both when it switches there from main and when it starts there (REF is the ref HEAD
# names). A run that stopped after creating the branch, or a migration that was never
# pushed, leaves it behind once main moves on, and a commit there would hold the media of
# that older main (and conflict with any re-encode).
require_branch_contains_main() {
    git show-ref --verify --quiet refs/heads/main ||
        die "there is no branch main here to check $BRANCH against; run this script from an up-to-date main"
    if git merge-base --is-ancestor refs/heads/main "refs/heads/$BRANCH"; then
        return 0
    fi
    # git branch -d and -D refuse to delete the branch that is checked out.
    leave=
    if [ "$1" = "refs/heads/$BRANCH" ]; then
        leave="
    git switch main"
    fi
    if git merge-base --is-ancestor "refs/heads/$BRANCH" refs/heads/main; then
        die "$BRANCH is behind main and has no commits of its own: an earlier run
probably stopped after creating it. Delete it, then run this script again from main:$leave
    git branch -d $BRANCH"
    fi
    die "$BRANCH does not contain main, so a migration there would hold the media
of an older main. These are its own commits:
$(git log --oneline --no-decorate "refs/heads/main..refs/heads/$BRANCH" | sed 's/^/    /')
If they are an earlier run of this script that you have not pushed, delete the branch,
then run this script again from main:$leave
    git branch -D $BRANCH
Otherwise merge main into it first, then run this script again."
}

migrate() {
    # The full ref name: with a tag called main, --short prints heads/main, and main would
    # pass for an ordinary branch to commit the migration on.
    ref=$(git symbolic-ref --quiet HEAD) || die "HEAD is detached; check out main first"
    branch=${ref#refs/heads/}
    require_clean_tree
    check_inventory

    if [ "$ref" = refs/heads/main ] && head_is_migrated; then
        say "main already stores the launch media in Git LFS ($(git rev-parse --short HEAD)); nothing to migrate."
        print_site_check
        return 0
    fi
    # Whether the run starts on $BRANCH or is about to switch there from main, the branch
    # must contain main.
    if [ "$ref" = "refs/heads/$BRANCH" ] ||
        { [ "$ref" = refs/heads/main ] && git show-ref --verify --quiet "refs/heads/$BRANCH"; }; then
        require_branch_contains_main "$ref"
    fi
    # Before the first command that can run the LFS filter: switching to a migrated branch
    # smudges its pointers, and the renormalizing add cleans the media.
    note_hooks_path

    if [ "$ref" = refs/heads/main ]; then
        if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
            run git switch "$BRANCH"
        else
            run git switch -c "$BRANCH"
        fi
        branch=$BRANCH
        check_inventory
    fi
    if [ "$branch" = "$BRANCH" ]; then
        RECOVERY="Nothing was committed, and $BRANCH is left checked out. To start again,
switch back to main and run this script from there:
    git switch main
    sh $SELF"
    fi

    if head_is_migrated; then
        check_head
        say "$branch already stores the launch media as Git LFS pointers ($(git rev-parse --short HEAD)); nothing to commit."
        print_next_steps "$branch"
        return 0
    fi

    require_media_contents
    # The filter.lfs settings that the renormalizing add needs, in .git/config. --skip-repo
    # leaves the hooks alone: git lfs install refuses to replace a hook it did not write,
    # even one that already calls git lfs pre-push, and the push needs no hook because the
    # next steps upload the objects with git lfs push. (The LFS filter that the add runs
    # still adds the Git LFS hooks where no hook of that name exists, as it does in any
    # clone; note_hooks_path says where when core.hooksPath is set.)
    if ! run git lfs install --local --skip-repo; then
        die "git lfs install --local --skip-repo failed (see its message above). It only writes the
filter.lfs settings that git add needs into .git/config, so check those settings there."
    fi
    base=$(git rev-parse HEAD)
    # shellcheck disable=SC2086 # MEDIA_FILES is a word list; globbing is off (set -f)
    before=$(git hash-object --no-filters -- $MEDIA_FILES)

    restore="git restore --staged --worktree -- .gitattributes $MEDIA_DIR"
    if [ "$branch" = "$BRANCH" ]; then
        RECOVERY="Nothing was committed, and $BRANCH is left checked out. To start again,
return to a clean tree, switch back to main and run this script from there:
    $restore
    git switch main
    sh $SELF"
    else
        RECOVERY="Nothing was committed. To start again, return to a clean tree and run this
script again:
    $restore
    sh $SELF"
    fi
    needs_tracking=
    for f in $MEDIA_FILES; do
        if ! routed_through_lfs "$f"; then
            needs_tracking=yes
        fi
    done
    if [ -n "$needs_tracking" ]; then
        # Append the lines git lfs track would write. git lfs track rewrites the whole file
        # and drops its blank lines; appending keeps every existing byte where it is.
        say "+ append to .gitattributes:"
        printf '\n# Launch media live in Git LFS so re-encodes stay out of the history (audit H-3).\n' >>.gitattributes
        for extension in $MEDIA_EXTENSIONS; do
            pattern="$MEDIA_DIR/*.$extension filter=lfs diff=lfs merge=lfs -text"
            if ! grep -Fqx -e "$pattern" .gitattributes; then
                printf '%s\n' "$pattern" >>.gitattributes
                say "    $pattern"
            fi
        done
    fi
    for f in $MEDIA_FILES; do
        routed_through_lfs "$f" || die "$f is not routed through LFS; a later .gitattributes line overrides the pattern"
    done
    for f in $PLAIN_FILES; do
        if routed_through_lfs "$f"; then
            die "$f is routed through LFS; the manifests must stay plain files"
        fi
    done

    run git add -- .gitattributes || die "git add .gitattributes failed"
    # shellcheck disable=SC2086
    run git add --renormalize -- $MEDIA_FILES || die "git add --renormalize failed"
    for path in $(git diff --cached --name-only); do
        in_list "$path" ".gitattributes $MEDIA_FILES" || die "unexpected staged change: $path"
    done
    check_blobs ":"
    say "+ git commit (message: Store the launch media in Git LFS (audit H-3))"
    commit_migration || die "git commit failed (see its message above)"
    RECOVERY=
    check_head
    # shellcheck disable=SC2086
    after=$(git hash-object --no-filters -- $MEDIA_FILES)
    if [ "$after" != "$before" ]; then
        die "the working media changed during the migration"
    fi

    say ""
    say "Committed $(git rev-parse --short HEAD) on $branch, on top of $(git rev-parse --short "$base"):"
    git lfs ls-files --long --size
    say "The working files are byte-identical; no history was rewritten."
    print_next_steps "$branch"
}

# committed_pointer PATH SCRATCH: print the LFS pointer of the media committed at HEAD for
# PATH, whether HEAD holds the pointer or (before the migration) the file itself.
committed_pointer() {
    if blob_is_pointer "HEAD:$1"; then
        git cat-file blob "HEAD:$1"
    else
        git cat-file blob "HEAD:$1" >"$2"
        git lfs pointer --file="$2" 2>/dev/null
    fi
}

# pointer_field KEY POINTER: the oid (without "sha256:") or size recorded in POINTER.
pointer_field() {
    printf '%s\n' "$2" | sed -n "s/^$1 //p" | sed 's/^sha256://'
}

verify_site() {
    base=${1:-$(site_url)}
    base=${base%/}
    command -v curl >/dev/null 2>&1 || die "--verify-site needs curl"
    # Schemes are case-insensitive. Without one, curl would guess one, so refuse that.
    case $base in
        [Hh][Tt][Tt][Pp]://?* | [Hh][Tt][Tt][Pp][Ss]://?*)
            # A fresh query string skips the CDN copy, which may predate the deploy.
            query="?lfs-verify=$(date -u +%Y%m%d%H%M%S)"
            ;;
        # A copy of the site on disk, as the tests publish it.
        [Ff][Ii][Ll][Ee]://?*) query= ;;
        *) die "--verify-site needs an http:// or https:// URL such as https://viewspec.dev, not '$base'" ;;
    esac
    scratch=$(mktemp -d "${TMPDIR:-/tmp}/viewspec-lfs-verify.XXXXXX")
    trap 'rm -rf "$scratch"' EXIT
    trap 'exit 1' HUP INT TERM

    say "Comparing $base with the launch media committed at HEAD ($(git rev-parse --short HEAD)):"
    total=0
    different=0
    unavailable=0
    for f in $MEDIA_FILES; do
        total=$((total + 1))
        published=${f#demos/}
        expected=$(committed_pointer "$f" "$scratch/expected")
        # --max-time ends an attempt after two minutes, and --retry-max-time starts no retry
        # after that, so a stalled download cannot hang the check.
        if ! curl -fsSL --retry 2 --connect-timeout 20 --max-time 120 --retry-max-time 120 \
            -o "$scratch/download" "$base/$published$query" 2>"$scratch/error"; then
            say "  FAILED    $published: $(head -n 1 "$scratch/error")"
            unavailable=$((unavailable + 1))
        elif file_is_pointer "$scratch/download"; then
            say "  POINTER   $published: the site serves the LFS pointer, not the file; the deploy checked out without lfs: true"
            different=$((different + 1))
        else
            actual=$(git lfs pointer --file="$scratch/download" 2>/dev/null) || die "git lfs pointer failed on $published"
            want="$(pointer_field size "$expected") bytes, sha256 $(pointer_field oid "$expected")"
            if [ "$actual" = "$expected" ]; then
                say "  ok        $published ($want)"
            else
                got="$(pointer_field size "$actual") bytes, sha256 $(pointer_field oid "$actual")"
                say "  MISMATCH  $published: $got; expected $want"
                different=$((different + 1))
            fi
        fi
    done
    problems=
    if [ "$different" -ne 0 ]; then
        problems="$different of $total launch media files on $base differ from HEAD"
    fi
    if [ "$unavailable" -ne 0 ] && [ -n "$problems" ]; then
        problems="$problems, and $unavailable could not be downloaded"
    elif [ "$unavailable" -ne 0 ]; then
        problems="$unavailable of $total launch media files could not be downloaded from $base"
    fi
    if [ -n "$problems" ]; then
        die "$problems"
    fi
    say "All $total launch media files on $base match HEAD."
}

main() {
    case ${1:-} in
        -h | --help)
            usage
            return 0
            ;;
        *) ;;
    esac
    command -v git >/dev/null 2>&1 || die "git is not installed"
    if ! git lfs version >/dev/null 2>&1; then
        die "git-lfs is not installed. Install it from https://git-lfs.com (for example
'brew install git-lfs' or 'sudo apt-get install git-lfs'), then run this script again."
    fi
    top=$(git rev-parse --show-toplevel 2>/dev/null) || die "run this inside the viewspec checkout"
    cd "$top"
    case ${1:-} in
        '')
            migrate
            ;;
        --verify-site)
            if [ $# -gt 2 ]; then
                usage >&2
                exit 2
            fi
            verify_site "${2:-}"
            ;;
        *)
            usage >&2
            exit 2
            ;;
    esac
}

main "$@"
