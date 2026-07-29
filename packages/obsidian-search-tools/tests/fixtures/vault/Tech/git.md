---
tags: [tech, git, version-control, development]
---
# Git Version Control

## Repository Basics

A Git repository tracks the complete history of changes to a set of files. Every commit captures
a snapshot of the working directory along with author, timestamp, and a message describing the change.
The commit history forms a directed acyclic graph (DAG) where each commit points to its parent.
This graph structure supports non-linear development through branching and merging.

## Branching

Branches are lightweight pointers to specific commits. Creating a branch is nearly instant and
uses minimal storage. Feature branches isolate development work from the main line of code.
Long-running branches diverge from main over time and require periodic merging or rebasing to
stay current. Short-lived branches that are merged and deleted keep the history manageable.

## Merging and Rebasing

A merge commit combines two branch histories, preserving the full record of parallel development.
Rebasing replays a sequence of commits onto a different base, producing a linear history that
is easier to read but rewrites commit SHAs. Never rebase commits that have been pushed to a
shared remote repository, as this forces collaborators to reconcile divergent histories.

## Remote Workflows

Cloning copies a repository from a remote host. Push uploads local commits to the remote.
Pull fetches remote changes and merges them into the current branch. Fetch retrieves remote
changes without applying them, allowing inspection before integration. Pull requests or merge
requests are a workflow convention on hosting platforms for code review before merging.

## Stashing and Resetting

Git stash temporarily shelves uncommitted changes so you can switch context. Stashed changes
can be reapplied later. Git reset moves the HEAD pointer to a different commit. Hard reset
discards uncommitted changes; soft reset preserves them as staged changes.
