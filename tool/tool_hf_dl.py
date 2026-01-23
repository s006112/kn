from huggingface_hub import list_repo_files, hf_hub_download

repo = "openai/gpt-oss-20b"  # Lightricks/LTX-2"
local_dir = "/workspaces/kn/data/openai/gpt-oss-20b"

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