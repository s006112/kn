from huggingface_hub import hf_hub_download, list_repo_files

repo = "stabilityai/stable-diffusion-3.5-medium"
local_dir = "/workspaces/sd35_manual"

# 取得 repo 內所有檔案路徑
files = list_repo_files(repo)

# 逐檔下載
for f in files:
    print(f"Downloading: {f}")
    hf_hub_download(
        repo_id=repo,
        filename=f,
        local_dir=local_dir,
        resume_download=True,    # 支援中斷續傳
        local_dir_use_symlinks=False
    )

print("Done.")
