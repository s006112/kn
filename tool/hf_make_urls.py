#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Generate direct-download URLs for all files in a HuggingFace repo.
Use these URLs with aria2c to support resume on large files.
"""

from huggingface_hub import list_repo_files
import sys
import os

def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python hf_make_urls.py <repo_id> [revision]")
        print("Example:")
        print("  python hf_make_urls.py stabilityai/stable-diffusion-3.5-medium")
        sys.exit(1)

    repo = sys.argv[1]
    revision = sys.argv[2] if len(sys.argv) > 2 else "main"

    print(f"Fetching file list for: {repo} @ {revision}")

    files = list_repo_files(repo)

    # Output filename
    repo_name = repo.replace("/", "--")
    out_file = f"{repo_name}_urls.txt"

    with open(out_file, "w") as f:
        for path in files:
            url = f"https://huggingface.co/{repo}/resolve/{revision}/{path}"
            f.write(url + "\n")

    print(f"✔ URL list written to: {out_file}")
    print("You can now download all files via:")
    print(f"  aria2c -i {out_file} -x 16 -s 16 -k 4M --continue=true --auto-file-renaming=false")

if __name__ == "__main__":
    main()
