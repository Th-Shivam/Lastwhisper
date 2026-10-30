# Security Policy

## Supported Version

Security fixes target the latest release on the default branch.

## Reporting a Vulnerability

Use GitHub's private vulnerability reporting or security advisory feature for this repository. If it is unavailable, contact the repository owner privately through their GitHub profile. Do not include real recordings, transcripts, notes, database files, model credentials, or filesystem paths in a public issue.

Please include:

- A concise impact statement.
- Reproduction steps using synthetic data.
- Affected commit or release.
- Suggested mitigation, if known.

## Security Boundary

LastWhisper is a single-user localhost application. It has no authentication, authorization, multi-user isolation, encryption at rest, or public-internet hardening. CORS is not access control. The supported deployment binds to loopback and runs one API worker.

Audio, transcripts, summaries, embeddings, saved notes, and filenames are sensitive plaintext local data. Users are responsible for operating-system account security, disk encryption, backups, and physical access.

Model registries are contacted only when uncached model files are requested. A configured Ollama endpoint receives complete transcript text; remote Ollama endpoints are disabled by default.
