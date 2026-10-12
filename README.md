# GDUE DevPortal

DevPortal is the planned lightweight, locally running entry point for GameDevUE collaborators. Its first use case will be browsing and obtaining test builds; later resource, query, and submission workflows can use the same entry point. **This repository is currently only a repository bootstrap:** there is no runnable client, release, update channel, or DevHub download API yet.

## Repository boundary

- This public repository will own the local client, its own tests, and eventually its independent Windows build and release workflow.
- The private GameDevUE repository pins this source as the `services/dev_portal` Git submodule; the DevHub cloud backend remains in GameDevUE's `services/dev_hub` and will communicate with a future client over a separately reviewed HTTP contract.
- No credentials, `.env` files, account tokens, Baidu OAuth access/refresh tokens, App Secret, packaged games, or developer-machine state belong in this repository or its build artifacts. Future client/device credentials must be provisioned outside the distributed source and executable.

To work on the client standalone, clone this repository normally. To obtain it from the parent project after the submodule pointer is committed, run `git submodule update --init services/dev_portal` in the GameDevUE checkout. Client commits are published here first; the parent submodule pointer is then advanced deliberately to a reviewed commit.

No license has been selected yet. Do not interpret this public source repository as permission to distribute the game, its assets, or account credentials.
