from huggingface_hub import snapshot_download

snapshot_download(
    repo_id="stabilityai/stable-diffusion-3.5-medium",
#    repo_id="stabilityai/stable-diffusion-3.5-medium-tensorrt",
    local_dir="/workspaces/sd35_manual",
#    local_dir="/workspaces/sd35_tensorrt,
    local_dir_use_symlinks=True,
    resume_download=True,
)

print("Done.")
