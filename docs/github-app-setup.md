# GitHub App setup for hosted TCA

Hosted TCA uses a GitHub App to check repository membership. Members never send their personal GitHub token to TCA. The App itself needs repository Metadata: read only; it does not need Contents, Administration, Issues, or write permissions.

1. In GitHub, open Settings → Developer settings → GitHub Apps → New GitHub App.
2. Give it a name and homepage URL. Disable webhook delivery unless you have separately enabled the optional integration webhook. Do not request extra repository or organization permissions; Metadata: read is mandatory.
3. Install the App on **only selected repositories** that should use this TCA Server. Each owner of a private repository chooses whether to trust this server with repository metadata.
4. In the App settings, note the App ID and generate a private key. Put the PEM in the server's secret manager as TCA_GITHUB_APP_PRIVATE_KEY; set TCA_GITHUB_APP_ID to the App ID. A literal multiline PEM or a value with escaped newline characters is accepted.
5. Configure Postgres and TCA_ALLOWED_HOSTS, deploy, and check /health.
6. Each person adds their dedicated TCA Ed25519 public key to their own GitHub account under Settings → SSH and GPG keys, then runs tca-codex login. A private key stays on the user's computer. The CLI reports the public key if it has not yet been added.
7. A repository admin runs tca-codex start for a repository where the App is installed. Developers run tca-codex join; the server checks current collaborator permissions through the App.

Repository-permission checks use a token scoped to the requested repository with Metadata: read, cached until shortly before expiry. The public SSH-key lookup also uses a metadata-read App installation token, avoiding GitHub's shared anonymous API rate limit. TCA sessions expire after eight hours. Revoking a user's repository permission is checked on the next project operation; removing the user's SSH key prevents new logins, while an already issued TCA session remains valid until it expires.

The App needs no access to source contents for the standard Claim and manual integration flow. The optional legacy GitHub compare webhook has a separate access requirement and is not part of this setup.

GitHub is the current remote provider adapter. The local controller and direct Claim protocol do not depend on GitHub; another host needs its own identity and repository-permission adapter.
