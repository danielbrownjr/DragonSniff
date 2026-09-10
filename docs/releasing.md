# Releasing DragonSniff

DragonSniff releases are created by the **Release** GitHub Actions workflow.
The workflow accepts one canonical input: the Python package version already
committed to `src/dragonsniff/_version.py`. It never edits or commits a version.

Supported forms are deliberately narrow:

| Release | Source/workflow input | Git, GitHub, and GHCR tag | `latest` |
| --- | --- | --- | --- |
| Stable | `X.Y.Z` | `vX.Y.Z` | Promoted after the version release succeeds |
| Release candidate | `X.Y.ZrcN` | `vX.Y.Z-rc.N` | Never changed |

Alpha, beta, development, post-release, local-metadata, and arbitrary SemVer
prerelease forms are not supported.

## RC release

1. Merge a reviewable version bump to `X.Y.ZrcN` into `main`.
2. Wait for CI and the main-branch **Publish container** run to succeed. That
   run creates `ghcr.io/danielbrownjr/dragonsniff:sha-<full-commit-sha>`.
3. Run **Release** from `main` and enter `X.Y.ZrcN`.
4. Verify the resulting GitHub Release is marked as a prerelease and the GHCR
   version tag is `vX.Y.Z-rc.N`.

The workflow does not move `latest` for an RC. The shared promotion validator
also rejects an RC-to-`latest` request independently of the release workflow's
RC branch.

## Stable release

1. Merge a reviewable version bump to `X.Y.Z` into `main`.
2. Wait for CI and the immutable main-branch image publication to succeed.
3. Run **Release** from `main` and enter `X.Y.Z`.
4. Verify the GitHub Release, versioned GHCR image, and `latest` digest.

Stable `latest` promotion is a distinct final job. It runs only after the
version image and GitHub Release have succeeded.

## Automated gates and artifacts

Before creating a tag, the workflow checks that it is running for a `main`
commit and that the requested version exactly matches the authoritative source.
It then runs the Python suite with warnings as errors, the browser tests and
JavaScript syntax checks, Python compilation, dependency checks, and source diff
checks. It builds exactly one wheel, verifies its filename, metadata, and core
runtime content, installs it into a clean environment, and confirms the imported
version. It also verifies and starts the exact immutable SHA-tagged container.

Only after those gates pass does the workflow create an annotated tag. It
promotes the already-built immutable container manifest rather than rebuilding
a release image. The GitHub Release attaches the validated wheel and records
the wheel SHA256 and container manifest digest. GitHub supplies the normal
source archives automatically.

By default, GitHub-generated release notes are used. To curate notes in the
repository, add `docs/releases/vX.Y.Z.md` or
`docs/releases/vX.Y.Z-rc.N.md` in the reviewed version-bump commit. The workflow
uses that file verbatim after the artifact identity header.

## Retry and recovery

The release stages are ordered as follows:

1. preflight, wheel build, and immutable-image verification
2. annotated Git tag creation
3. immutable-image promotion to the version tag
4. GitHub Release publication with the wheel
5. stable-only promotion of the verified version to `latest`

The workflow is safe to rerun for the same version and commit:

- an existing Git tag is accepted only when it resolves to the same commit;
- an existing version container tag is accepted only at the same manifest
  digest and is never overwritten at a different digest;
- an existing GitHub Release must have the expected stable/prerelease state;
- an existing wheel is downloaded and its SHA256 must match before the workflow
  continues;
- stable `latest` may be retried after the release exists.

If version-container promotion succeeds but release creation fails, rerun the
same workflow input. If release creation succeeds but stable `latest` promotion
fails, rerun it and the validated tag, version image, release, and wheel are
reused before retrying `latest`. A tag at another commit, a version image at
another digest, or a release asset with different bytes is a hard stop requiring
manual investigation. Tags and versioned artifacts must never be force-updated;
rollback means deploying a prior immutable SHA or version, not rewriting history.

What remains manual is intentionally small: review and merge the version bump,
optionally curate release notes, wait for the immutable main image, dispatch the
workflow, and verify its published outputs.
