#!/usr/bin/env python3
"""
Fetches MARVA profiles from the API and commits them to the git repo.

Usage:
    python sync_profiles.py [--config path/to/config.ini]
"""

import argparse
import configparser
import json
import os
import shutil
import subprocess
import sys
import urllib.parse

import requests


def load_config(config_path):
    config = configparser.ConfigParser()
    config.read(config_path)
    return config


def get_workspaces(base_url):
    url = urllib.parse.urljoin(base_url, "serve/workspaces")
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError("API returned success=false")
    return data["data"]


def fetch_content(base_url, relative_url):
    # The relative URLs from the API start with /api/serve/...
    # We need to join them with the base_url which ends in /api/
    # So strip the /api/ prefix from the relative URL
    if relative_url.startswith("/api/"):
        relative_url = relative_url[len("/api/"):]
    url = urllib.parse.urljoin(base_url, relative_url)
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return resp


def sync_profiles(config_path):
    config = load_config(config_path)

    base_url = config.get("api", "base_url")
    if not base_url.endswith("/"):
        base_url += "/"

    git_username = config.get("git", "username")
    git_token = config.get("git", "token")
    commit_name = config.get("git", "commit_name")
    commit_email = config.get("git", "commit_email")

    # Repo root is one level up from scripts/
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    print("Fetching workspaces...")
    workspaces = get_workspaces(base_url)
    workspace_names = set()

    for ws in workspaces:
        name = ws["name"]
        workspace_names.add(name)
        ws_dir = os.path.join(repo_root, name)
        os.makedirs(ws_dir, exist_ok=True)

        print(f"  Syncing {name}...")

        # Fetch CSV
        csv_resp = fetch_content(base_url, ws["csvUrl"])
        csv_path = os.path.join(ws_dir, "dctap.csv")
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write(csv_resp.text)

        # Fetch profile JSON
        profile_resp = fetch_content(base_url, ws["profileUrl"])
        profile_path = os.path.join(ws_dir, "marva-profiles.json")
        with open(profile_path, "w", encoding="utf-8") as f:
            json.dump(profile_resp.json(), f, indent=2, ensure_ascii=False)
            f.write("\n")

        # Fetch starting points JSON
        starting_resp = fetch_content(base_url, ws["startingPointsUrl"])
        starting_path = os.path.join(ws_dir, "marva-starting.json")
        with open(starting_path, "w", encoding="utf-8") as f:
            json.dump(starting_resp.json(), f, indent=2, ensure_ascii=False)
            f.write("\n")

    # Remove directories for profiles no longer in the API
    skip_dirs = {"scripts", ".git", ".github"}
    for entry in os.listdir(repo_root):
        entry_path = os.path.join(repo_root, entry)
        if os.path.isdir(entry_path) and entry not in skip_dirs and entry not in workspace_names:
            print(f"  Removing stale profile: {entry}")
            shutil.rmtree(entry_path)

    # Git operations
    print("Committing changes...")

    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"] = commit_name
    env["GIT_AUTHOR_EMAIL"] = commit_email
    env["GIT_COMMITTER_NAME"] = commit_name
    env["GIT_COMMITTER_EMAIL"] = commit_email

    def git(*args):
        result = subprocess.run(
            ["git"] + list(args),
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
        )
        if result.stdout.strip():
            print(f"  [git {args[0]}] {result.stdout.strip()}")
        if result.returncode != 0:
            print(f"  ERROR [git {args[0]}]: {result.stderr.strip()}", file=sys.stderr)
        return result

    # Stage all changes
    result = git("add", "-A")
    if result.returncode != 0:
        sys.exit(1)

    # Check if there are changes to commit
    status = git("status", "--porcelain")
    if not status.stdout.strip():
        print("No changes to commit.")
        return

    result = git("commit", "-m", "Update MARVA profiles")
    if result.returncode != 0:
        sys.exit(1)

    # Push using token auth
    # Get the current remote URL and inject credentials
    remote_result = git("remote", "get-url", "origin")
    if remote_result.returncode != 0:
        print("ERROR: could not get remote URL. Is 'origin' configured?", file=sys.stderr)
        sys.exit(1)
    remote_url = remote_result.stdout.strip()

    if remote_url.startswith("https://"):
        # Inject credentials into the URL for the push
        parsed = urllib.parse.urlparse(remote_url)
        authed_url = parsed._replace(
            netloc=f"{git_username}:{git_token}@{parsed.hostname}"
            + (f":{parsed.port}" if parsed.port else "")
        ).geturl()
        result = git("push", authed_url, "HEAD")
    elif remote_url.startswith("git@"):
        result = git("push", "origin", "HEAD")
    else:
        print(f"Warning: unrecognized remote URL format: {remote_url}")
        result = git("push", "origin", "HEAD")

    if result.returncode != 0:
        print("ERROR: push failed!", file=sys.stderr)
        sys.exit(1)

    print("Done.")


def main():
    parser = argparse.ArgumentParser(description="Sync MARVA profiles from API to git repo")
    parser.add_argument(
        "--config",
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini"),
        help="Path to config file (default: scripts/config.ini)",
    )
    args = parser.parse_args()

    if not os.path.exists(args.config):
        print(f"Error: config file not found: {args.config}", file=sys.stderr)
        sys.exit(1)

    try:
        sync_profiles(args.config)
    except requests.exceptions.ConnectionError as e:
        print(f"ERROR: could not connect to API: {e}", file=sys.stderr)
        sys.exit(1)
    except requests.exceptions.HTTPError as e:
        print(f"ERROR: API returned an error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
