import torch, time

device = "cuda"
dtype = torch.float16

# ============================================================
# Phase 1 : PCIe transfer stress (no VRAM fill)
# ============================================================

print("Phase 1: PCIe transfer test")

x = torch.randn((1024, 1024, 1024), device=device, dtype=dtype)  # ~2GB

for i in range(10):
    y = x.cpu()
    x = y.cuda()
    torch.cuda.synchronize()
    print(f"PCIe transfer {i+1}/10 OK")

del x, y
torch.cuda.empty_cache()

# ============================================================
# Phase 2 : VRAM fill (memory integrity)
# ============================================================

print("Phase 2: VRAM fill test")

N_mem = 110000   # ~23.8GB
big = torch.empty((N_mem, N_mem), device=device, dtype=dtype)
big.fill_(1.0)
torch.cuda.synchronize()
print("VRAM filled")

# ============================================================
# Phase 3 : Compute burn
# ============================================================

print("Phase 3: Compute burn")

N = 8192
a = torch.randn((N, N), device=device, dtype=dtype)
b = torch.randn((N, N), device=device, dtype=dtype)

i = 0
t0 = time.time()
while True:
    c = a @ b
    c = c + a
    torch.cuda.synchronize()
    i += 1
    if i % 10 == 0:
        print(f"Iter {i}, elapsed {time.time()-t0:.1f}s")
