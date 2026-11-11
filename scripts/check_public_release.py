#!/usr/bin/env python3
"""Fail closed when a public source tree contains known private data.

Only file names, rule labels and line numbers are reported. Matched values are
never printed, which keeps CI logs safe even when a violation is found.

The rules in this public file are deliberately generic. Site-specific terms
belong in an untracked, newline-delimited denylist passed with ``--denylist``
or ``AGENTSWATCH_RELEASE_DENYLIST``; never commit that file.
"""

from __future__ import annotations

import argparse
import io
import ipaddress
import os
import re
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROHIBITED_PATHS = {
    ".env",
    "cluster/lobsters.json",
    "prometheus/scrape_configs/openclaw-gateways.yml",
}
PROHIBITED_SUFFIXES = {".key", ".p12", ".pfx", ".pem", ".token"}

# Synthetic and documentation ranges intentionally used by the public test
# suite and examples. Keep this list generic; site-specific ranges belong in
# the private denylist.
SYNTHETIC_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in (
        "10.0.0.0/24",
        "10.10.0.0/24",
        "172.30.0.0/24",
        "192.0.2.0/24",
        "198.51.100.0/24",
        "203.0.113.0/24",
    )
)
IPV4_PATTERN = re.compile(rb"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
DENYLIST_RULE = "private denylist term"

CONTENT_RULES = (
    ("private workstation path", re.compile(rb"/Users/[A-Za-z0-9._-]+/")),
    ("private key block", re.compile(rb"-{5}BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-{5}")),
    ("AWS access key", re.compile(rb"(?<![A-Z0-9])AKIA[0-9A-Z]{16}(?![A-Z0-9])")),
    ("GitHub token", re.compile(rb"(?<![A-Za-z0-9_])gh[pousr]_[A-Za-z0-9]{20,}")),
    ("OpenAI-shaped key", re.compile(rb"(?<![A-Za-z0-9_-])sk-[A-Za-z0-9_-]{20,}")),
    ("Google API key", re.compile(rb"(?<![A-Za-z0-9_-])AIza[0-9A-Za-z_-]{30,}")),
    ("Slack token", re.compile(rb"(?<![A-Za-z0-9_-])xox[baprs]-[A-Za-z0-9-]{10,}")),
)


@dataclass(frozen=True)
class Finding:
    location: str
    rule: str
    line: int | None = None


def path_finding(path: str) -> Finding | None:
    normalized = path.replace("\\", "/").removeprefix("./")
    name = normalized.rsplit("/", 1)[-1]
    if normalized in PROHIBITED_PATHS:
        return Finding(normalized, "prohibited release path")
    if Path(name).suffix.lower() in PROHIBITED_SUFFIXES and name != ".gitkeep":
        return Finding(normalized, "credential file extension")
    if name in {"id_rsa", "id_ed25519"}:
        return Finding(normalized, "private SSH key path")
    return None


def _private_network_findings(location: str, content: bytes) -> list[Finding]:
    findings: list[Finding] = []
    for match in IPV4_PATTERN.finditer(content):
        try:
            address = ipaddress.ip_address(match.group().decode("ascii"))
        except ValueError:
            continue
        if (
            not address.is_private
            or address.is_loopback
            or address.is_unspecified
            or address.is_link_local
        ):
            continue
        if any(address in network for network in SYNTHETIC_NETWORKS):
            continue
        findings.append(
            Finding(
                location,
                "private network address",
                content.count(b"\n", 0, match.start()) + 1,
            )
        )
    return findings


def scan_bytes(
    location: str,
    content: bytes,
    deny_terms: tuple[bytes, ...] = (),
) -> list[Finding]:
    findings: list[Finding] = []
    for label, pattern in CONTENT_RULES:
        match = pattern.search(content)
        if match is not None:
            findings.append(
                Finding(location, label, content.count(b"\n", 0, match.start()) + 1)
            )
    findings.extend(_private_network_findings(location, content))
    for index, term in enumerate(deny_terms, start=1):
        position = content.find(term)
        if position >= 0:
            findings.append(
                Finding(
                    location,
                    f"{DENYLIST_RULE} #{index}",
                    content.count(b"\n", 0, position) + 1,
                )
            )
    return findings


def scan_payload(
    location: str,
    content: bytes,
    deny_terms: tuple[bytes, ...] = (),
) -> list[Finding]:
    findings = scan_bytes(location, content, deny_terms)
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except (zipfile.BadZipFile, OSError):
        return findings
    with archive:
        for member in archive.infolist():
            if member.is_dir() or member.file_size > 10 * 1024 * 1024:
                continue
            try:
                payload = archive.read(member)
            except (RuntimeError, zipfile.BadZipFile):
                continue
            findings.extend(scan_bytes(f"{location}:{member.filename}", payload, deny_terms))
    return findings


def load_denylist(path: str) -> tuple[bytes, ...]:
    """Read a private newline-delimited denylist without echoing its values."""
    terms: list[bytes] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if value and not value.startswith("#"):
            terms.append(value.encode("utf-8"))
    return tuple(terms)


def _git(*arguments: str, input_bytes: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout


def tracked_findings(deny_terms: tuple[bytes, ...] = ()) -> list[Finding]:
    findings: list[Finding] = []
    paths = [
        item.decode("utf-8")
        for item in _git(
            "ls-files", "-z", "--cached", "--others", "--exclude-standard"
        ).split(b"\0")
        if item
    ]
    for path in paths:
        source = ROOT / path
        if not source.is_file():
            continue
        if prohibited := path_finding(path):
            findings.append(prohibited)
        try:
            findings.extend(scan_payload(path, source.read_bytes(), deny_terms))
        except OSError:
            findings.append(Finding(path, "tracked file cannot be read"))
    return findings


def history_findings(deny_terms: tuple[bytes, ...] = ()) -> list[Finding]:
    findings: list[Finding] = []
    seen_objects: set[str] = set()
    reported: set[tuple[str, str]] = set()
    for line in _git("rev-list", "--objects", "--all").decode("utf-8").splitlines():
        object_id, _, path = line.partition(" ")
        if not path or object_id in seen_objects:
            continue
        seen_objects.add(object_id)
        if _git("cat-file", "-t", object_id).strip() != b"blob":
            continue
        location = f"history:{path}"
        if prohibited := path_finding(path):
            key = (path, prohibited.rule)
            if key not in reported:
                findings.append(Finding(location, prohibited.rule))
                reported.add(key)
        for finding in scan_payload(location, _git("cat-file", "blob", object_id), deny_terms):
            key = (path, finding.rule)
            if key not in reported:
                findings.append(finding)
                reported.add(key)
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--history",
        action="store_true",
        help="also scan every blob reachable from local branches and tags",
    )
    parser.add_argument(
        "--denylist",
        default=os.environ.get("AGENTSWATCH_RELEASE_DENYLIST"),
        help="optional untracked newline-delimited file of site-specific terms",
    )
    args = parser.parse_args()

    deny_terms: tuple[bytes, ...] = ()
    if args.denylist:
        try:
            deny_terms = load_denylist(args.denylist)
        except OSError:
            print("private denylist could not be read", file=sys.stderr)
            return 2

    try:
        findings = tracked_findings(deny_terms)
        if args.history:
            findings.extend(history_findings(deny_terms))
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"public release check could not run: {type(exc).__name__}", file=sys.stderr)
        return 2
    unique = sorted(set(findings), key=lambda item: (item.location, item.rule, item.line or 0))
    if unique:
        print("public release check failed; matched values were withheld:", file=sys.stderr)
        for finding in unique:
            line = f":{finding.line}" if finding.line is not None else ""
            print(f"- {finding.location}{line}: {finding.rule}", file=sys.stderr)
        return 1
    scope = "tracked files and reachable history" if args.history else "tracked files"
    if deny_terms:
        scope += f" plus {len(deny_terms)} private denylist terms"
    print(f"public release check passed: {scope}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
