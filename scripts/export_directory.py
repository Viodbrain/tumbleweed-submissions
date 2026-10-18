"""Export two static sites to an empty output folder; requires a deployed API URL."""

import argparse
import html
import shutil
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-api-base", required=True)
    parser.add_argument("--moderator-api-base", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    bases = {"directory": args.public_api_base.rstrip("/"), "maintain": args.moderator_api_base.rstrip("/")}
    for base in bases.values():
        parsed = urlparse(base)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path
        ):
            raise SystemExit("Supply public HTTPS API origins without credentials, paths, query or fragment.")
    if args.output.exists() and any(args.output.iterdir()):
        raise SystemExit("Output must be a new or empty directory.")
    source = ROOT / "backend/app/web"
    for page, name in (("directory", "public"), ("maintain", "maintainer")):
        base = bases[page]
        destination = args.output / name
        (destination / "assets").mkdir(parents=True)
        markup = (source / f"{page}.html").read_text()
        markup = markup.replace(
            'name="api-base" content=""',
            f'name="api-base" content="{html.escape(base, quote=True)}"',
        )
        markup = markup.replace("/directory-assets/", "./assets/")
        markup = markup.replace('href="/directory"', f'href="{html.escape(base, quote=True)}/directory"')
        markup = markup.replace('href="/maintain"', 'href="/"')
        if page == "directory":
            markup = markup.replace(f'href="{html.escape(base, quote=True)}/directory"', 'href="/"')
        (destination / "index.html").write_text(markup)
        for asset in ("site.css", "site.js"):
            shutil.copyfile(source / asset, destination / "assets" / asset)
    print("Exported both sites with separate API origins. Set exact ALLOWED_ORIGINS on the respective services.")


if __name__ == "__main__":
    main()
