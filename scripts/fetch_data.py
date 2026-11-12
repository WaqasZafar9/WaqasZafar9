#!/usr/bin/env python3
"""Refresh data/contributions.json and the images in assets/ from GitHub.

Env:
  GITHUB_TOKEN   token for the GraphQL API. The built-in Actions token only sees
                 public repos; set a PROFILE_TOKEN secret (classic PAT with `repo`
                 and `read:org`) so private repos you commit to show up too.
  GH_LOGIN       user to fetch (default WaqasZafar9)

Writes:
  data/contributions.json   calendar + the 6 repos you committed to most recently
  assets/avatar.jpg         your GitHub profile picture
  assets/orgs/<login>.png   logos for the orgs listed in render_cards.WORK_ORGS
Private repos are stored with name, commit count and month only: no description,
language or URL ever lands in this public file.
"""
import json, os, sys, urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "contributions.json"
ASSETS = ROOT / "assets"
LOGIN = os.environ.get("GH_LOGIN") or "WaqasZafar9"
TOKEN = os.environ.get("GITHUB_TOKEN")
RECENT = 6

QUERY = """
query($login: String!) {
  user(login: $login) {
    avatarUrl(size: 320)
    contributionsCollection {
      restrictedContributionsCount
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount } }
      }
      commitContributionsByRepository(maxRepositories: 100) {
        repository {
          nameWithOwner isPrivate description url stargazerCount forkCount
          primaryLanguage { name color }
        }
        contributions(first: 1, orderBy: {field: OCCURRED_AT, direction: DESC}) {
          totalCount
          nodes { occurredAt }
        }
      }
    }
  }
}
"""


def http(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "profile-cards", **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def gql(query, variables):
    body = json.loads(http("https://api.github.com/graphql",
                           json.dumps({"query": query, "variables": variables}).encode(),
                           {"Authorization": f"bearer {TOKEN}", "Content-Type": "application/json"}))
    if body.get("errors"):
        sys.exit(f"GraphQL error: {body['errors']}")
    return body["data"]


def repo_entry(node):
    r, c = node["repository"], node["contributions"]
    e = {"name": r["nameWithOwner"], "private": r["isPrivate"], "commits": c["totalCount"],
         "last": c["nodes"][0]["occurredAt"][:10] if c["nodes"] else ""}
    if not r["isPrivate"]:
        lang = r.get("primaryLanguage") or {}
        e.update(description=r.get("description") or "", url=r["url"], language=lang.get("name"),
                 languageColor=lang.get("color"), stars=r["stargazerCount"], forks=r["forkCount"])
    return e


def save_image(url, path, size):
    """Download an image and store it as a small JPEG/PNG for embedding."""
    try:
        from io import BytesIO
        from PIL import Image
        im = Image.open(BytesIO(http(url))).convert("RGBA")
        im.thumbnail((size, size))
        bg = Image.new("RGB", im.size, (15, 20, 17))
        bg.paste(im, mask=im.split()[3])
        path.parent.mkdir(parents=True, exist_ok=True)
        bg.save(path, quality=88, optimize=True)
    except Exception as e:  # images are optional; never fail the run over one
        print(f"warning: could not fetch {url}: {e}")


def public_calendar():
    """The calendar exactly as visitors see it on the profile page. With "Private
    contributions" turned on in profile settings, it counts private work too, which
    the built-in Actions token can't see through the API."""
    import re
    try:
        html = http(f"https://github.com/users/{LOGIN}/contributions").decode("utf-8", "replace")
    except Exception as e:
        print(f"warning: could not read the public calendar: {e}")
        return []
    by_id = {}                                   # cell id -> date, whatever the attribute order
    for tag in re.findall(r"<td\b[^>]*ContributionCalendar-day[^>]*>", html):
        d, i = re.search(r'data-date="([\d-]+)"', tag), re.search(r'\sid="([^"]+)"', tag)
        if d and i:
            by_id[i.group(1)] = d.group(1)
    out = {}
    for for_id, text in re.findall(r'<tool-tip[^>]*\bfor="([^"]+)"[^>]*>([^<]*)</tool-tip>', html):
        d = by_id.get(for_id)
        m = re.match(r"\s*(\d+|No) contribution", text)
        if d and m:
            out[d] = 0 if m.group(1) == "No" else int(m.group(1))
    return [{"date": d, "count": out[d]} for d in sorted(out)]


def main():
    if not TOKEN:
        sys.exit("GITHUB_TOKEN is not set")
    u = gql(QUERY, {"login": LOGIN})["user"]
    cc = u["contributionsCollection"]
    cal = cc["contributionCalendar"]
    days = [{"date": d["date"], "count": d["contributionCount"]}
            for w in cal["weeks"] for d in w["contributionDays"]]
    public = public_calendar()
    if public and sum(d["count"] for d in public) > sum(d["count"] for d in days):
        days = public          # the profile page includes private contributions; the API token may not
    cal = {"totalContributions": sum(d["count"] for d in days)}

    profile_repo = f"{LOGIN}/{LOGIN}".lower()
    repos = [repo_entry(n) for n in cc["commitContributionsByRepository"]
             if n["repository"]["nameWithOwner"].lower() != profile_repo]
    if not any(r["private"] for r in repos) and OUT.exists():
        # No private repos came back, so this token can't see them (the built-in
        # Actions token never can). Keep the private ones from the last run with a
        # better token, or from the seed, rather than silently dropping them.
        seen = {r["name"] for r in repos}
        old = json.loads(OUT.read_text()).get("repos", [])
        repos += [r for r in old if r.get("private") and r["name"] not in seen]
        print("note: private repos carried over; set PROFILE_TOKEN to refresh their counts")
    repos.sort(key=lambda r: (r.get("last", ""), r.get("commits", 0)), reverse=True)

    data = {"login": LOGIN, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "total": cal["totalContributions"], "days": days, "repos": repos[:RECENT]}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=1) + "\n")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(days)} days, {data['total']} contributions, "
          f"{len(data['repos'])} recent repos")

    save_image(u["avatarUrl"], ASSETS / "avatar.jpg", 320)
    sys.path.insert(0, str(Path(__file__).parent))
    from render_cards import WORK_ORGS
    for org in WORK_ORGS:
        save_image(f"https://github.com/{org['login']}.png?size=96", ASSETS / "orgs" / f"{org['login']}.png", 96)


if __name__ == "__main__":
    main()
