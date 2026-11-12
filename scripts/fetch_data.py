#!/usr/bin/env python3
"""Refresh data/contributions.json from the GitHub GraphQL API.

Env:
  GITHUB_TOKEN   token with read:user (a PAT also counts private contributions
                 if "Include private contributions" is on in your profile settings)
  GH_LOGIN       user to fetch (default: repo owner / WaqasZafar9)
  FEATURED       optional comma list of repo names; default = your pinned repos
"""
import json, os, sys, urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "contributions.json"
LOGIN = os.environ.get("GH_LOGIN") or "WaqasZafar9"
TOKEN = os.environ.get("GITHUB_TOKEN")

QUERY = """
query($login: String!) {
  user(login: $login) {
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount } }
      }
    }
    pinnedItems(first: 6, types: REPOSITORY) {
      nodes { ... on Repository { ...R } }
    }
    repositories(first: 100, ownerAffiliations: OWNER, privacy: PUBLIC) {
      nodes { ...R }
    }
  }
}
fragment R on Repository {
  name description url stargazerCount forkCount
  primaryLanguage { name color }
}
"""


def gql(query, variables):
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Authorization": f"bearer {TOKEN}", "Content-Type": "application/json",
                 "User-Agent": "profile-cards"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        body = json.load(r)
    if body.get("errors"):
        sys.exit(f"GraphQL error: {body['errors']}")
    return body["data"]


def repo(n):
    lang = n.get("primaryLanguage") or {}
    return {"name": n["name"], "description": n.get("description") or "", "url": n["url"],
            "language": lang.get("name"), "languageColor": lang.get("color"),
            "stars": n["stargazerCount"], "forks": n["forkCount"]}


def main():
    if not TOKEN:
        sys.exit("GITHUB_TOKEN is not set")
    u = gql(QUERY, {"login": LOGIN})["user"]
    cal = u["contributionsCollection"]["contributionCalendar"]
    days = [{"date": d["date"], "count": d["contributionCount"]}
            for w in cal["weeks"] for d in w["contributionDays"]]
    featured = [s.strip() for s in os.environ.get("FEATURED", "").split(",") if s.strip()]
    if featured:
        by_name = {n["name"].lower(): n for n in u["repositories"]["nodes"]}
        repos = [repo(by_name[f.lower()]) for f in featured if f.lower() in by_name]
    else:
        repos = [repo(n) for n in u["pinnedItems"]["nodes"] if n]
    data = {"login": LOGIN, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "total": cal["totalContributions"], "days": days, "repos": repos}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=1) + "\n")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(days)} days, {data['total']} contributions, {len(repos)} repos")


if __name__ == "__main__":
    main()
