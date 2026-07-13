# Publishing the runtime helper libraries

This is the owner-only setup for releasing the two runtime helper
libraries. Both pipelines are already wired in the repo; nothing
publishes until the external accounts, keys, and GitHub secrets below
are in place. The two pipelines are fully decoupled from the linter's
`publish.yml` release: each fires only on its own dedicated tag.

Python `tackbox-report`:

- Path: `py/tackbox_report/`
- Target: PyPI (Trusted Publishing / OIDC)
- Workflow: `.github/workflows/publish-report-py.yml`
- Release tag: `report-py-v*`

Java `report`:

- Path: `java/report/`
- Target: Maven Central (Sonatype Central Portal)
- Workflow: `.github/workflows/publish-report-java.yml`
- Release tag: `report-java-v*`

Coordinates (locked): Python distribution `tackbox-report` `0.1.0`;
Java `io.github.nikitatsym:report:0.1.0`.

Do the one-time setup in sections 1-3, then release with section 4.

---

## 1. Python -> PyPI (Trusted Publishing, no token)

Trusted Publishing lets GitHub Actions authenticate to PyPI over OIDC,
so there is no API token to create or store as a secret. You register a
"pending publisher" on PyPI once, before the first release.

1. Log in to <https://pypi.org> (account `nikitatsym`).
2. Go to <https://pypi.org/manage/account/publishing/> (Account
   settings -> Publishing).
3. Under "Add a new pending publisher", GitHub tab, enter EXACTLY:
   - PyPI Project Name: `tackbox-report`
   - Owner: `nikitatsym`
   - Repository name: `tackbox`
   - Workflow name: `publish-report-py.yml`
   - Environment name: `pypi`
4. Save. The project does not need to exist yet; PyPI creates it on the
   first successful Trusted-Publisher upload.

Note: the environment name `pypi` is shared with the linter's own
thin-package publish (`publish.yml`). That is fine on the PyPI side (the
publisher tuple also includes the workflow filename, which differs). If
a GitHub `pypi` environment already exists with protection rules, those
rules will also gate this release; create or adjust the environment
under repo Settings -> Environments if needed.

No GitHub secret is required for the Python pipeline.

---

## 2. Java -> Maven Central (Sonatype Central Portal)

### 2a. Register the namespace

1. Log in to <https://central.sonatype.com> with GitHub (account
   `nikitatsym`).
2. Go to "View Namespaces" -> "Add Namespace" and add
   `io.github.nikitatsym`.
3. Central shows a verification key. Because the namespace is
   `io.github.<user>`, verify by creating the temporary public GitHub
   repository it names (for example `nikitatsym/<verification-code>`)
   under the `nikitatsym` account, then click "Verify Namespace". Delete
   the temp repo once the namespace shows Verified.

### 2b. Generate a Central Portal user token

1. On <https://central.sonatype.com>, open Account -> "Generate User
   Token".
2. Copy the token username and token password (shown once). These become
   the `CENTRAL_TOKEN_USERNAME` / `CENTRAL_TOKEN_PASSWORD` secrets in
   section 3.

### 2c. Generate and publish a GPG signing key

Central requires every artifact to be GPG-signed and the public key to
be on a public keyserver.

```sh
# Generate a key (RSA 4096 or default). Set a passphrase; remember it.
gpg --full-generate-key

# Find the long key id (16-hex value after "sec   rsa4096/").
gpg --list-secret-keys --keyid-format=long

# Publish the PUBLIC key so Central can verify signatures (both).
gpg --keyserver keys.openpgp.org     --send-keys <KEYID>
gpg --keyserver keyserver.ubuntu.com --send-keys <KEYID>

# Export the PRIVATE key ascii-armored -> the GPG_PRIVATE_KEY secret.
# Keep it out of the repo; delete it after loading the secret.
gpg --armor --export-secret-keys <KEYID> > gpg-private-key.asc
```

---

## 3. Load the GitHub secrets

Run from a clone of `nikitatsym/tackbox` (or add
`--repo nikitatsym/tackbox`). `gh secret set` prompts for the value when
no input is piped.

```sh
# Central Portal user token (from 2b):
gh secret set CENTRAL_TOKEN_USERNAME        # paste token username
gh secret set CENTRAL_TOKEN_PASSWORD        # paste token password

# GPG (from 2c):
gh secret set GPG_PRIVATE_KEY < gpg-private-key.asc
gh secret set GPG_PASSPHRASE                # paste key passphrase

# Clean up the exported private key:
rm gpg-private-key.asc
```

The Java workflow references exactly these four secrets by name:
`CENTRAL_TOKEN_USERNAME`, `CENTRAL_TOKEN_PASSWORD`, `GPG_PRIVATE_KEY`,
`GPG_PASSPHRASE`. The Python workflow references no secrets.

---

## 4. Cut a release

Both workflows accept a manual run (Actions tab -> select the workflow
-> "Run workflow", the `workflow_dispatch` trigger) or a pushed tag.

Python (`tackbox-report`):

```sh
git tag report-py-v0.1.0
git push origin report-py-v0.1.0
```

Java (`report`):

```sh
git tag report-java-v0.1.0
git push origin report-java-v0.1.0
```

The tag only triggers the workflow; the published version comes from
`pyproject.toml` / `pom.xml` (both pinned to `0.1.0`). Bump those and
use a matching tag for later releases.

### Java: finish the publish in the Central UI

The Java pipeline uploads to the Central Portal with `autoPublish=false`
(safer for a first release). After the workflow succeeds, the release
sits in the portal as a validated Deployment:

1. Open <https://central.sonatype.com> -> "Deployments".
2. Confirm validation passed, then click "Publish" to release to Maven
   Central.

To skip the manual click on future releases, set
`<autoPublish>true</autoPublish>` in the `release` profile of
`java/report/pom.xml`.

---

## Notes

- Nothing publishes until sections 1-3 are complete. A tag pushed before
  then fails at the authenticate/deploy step; no partial artifact is
  released (PyPI is atomic per upload; Central holds the deployment until
  you publish).
- The dev/CI build is untouched: `mvn -f java/report/pom.xml package`
  and `python -m build` need no key or credential. Signing, the
  sources/javadoc jars, and the Central deploy live only in the Maven
  `release` profile (`-Prelease`), which the Java workflow activates.
- Propagation: a newly published Maven Central artifact can take up to
  about 30 min to appear in search; PyPI is immediate.
