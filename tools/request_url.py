"""Download a page's raw HTML source to a local file (no assets)."""

import argparse

import requests

# Parse command-line arguments: URL and output file.
parser = argparse.ArgumentParser(description="Download a URL's raw HTML source to a local file.")
parser.add_argument("-u", "--url", default="http://www.ipp.cas.cn/",
                    help="Web page URL to download (default: http://www.ipp.cas.cn/)")
parser.add_argument("-o", "--output", default="page.html",
                    help="Local file to save to (default: page.html)")
args = parser.parse_args()

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

try:
    response = requests.get(args.url, headers=headers, timeout=10)
    # Auto-detect and apply the correct encoding to avoid garbled text.
    response.encoding = response.apparent_encoding

    # Write the raw HTML source to the local file.
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(response.text)

    print(f"[OK] HTML source downloaded to {args.output} (no image assets).")

except Exception as e:
    print(f"Download failed: {e}")
