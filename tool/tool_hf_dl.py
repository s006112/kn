from huggingface_hub import list_repo_files, hf_hub_download

repo = "Qwen/Qwen3-14B"  # Lightricks/LTX-2"
local_dir = "/workspaces/kn/data/Qwen3-14B"

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
