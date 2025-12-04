# save as gen_sd35_urls.py
from huggingface_hub import list_repo_files, hf_hub_url

repo = "stabilityai/stable-diffusion-3.5-medium-tensorrt"
outfile = "urls.txt"

files = list_repo_files(repo)

with open(outfile, "w") as f:
    for fn in files:
        url = hf_hub_url(repo_id=repo, filename=fn)
        f.write(url + "\n")

print("Generated urls.txt")
