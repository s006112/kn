from huggingface_hub import list_repo_files, hf_hub_download

repo = "stabilityai/stable-diffusion-3.5-medium"
local_dir = "/workspaces/sd35_manual"

files = list_repo_files(repo)

for f in files:
    print(f"Downloading: {f}")
    hf_hub_download(
        repo_id=repo,
        filename=f,
        local_dir=local_dir,
        force_download=False,
        resume_download=True,
    )

print("Done.")
