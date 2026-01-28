import torch, time

device = "cuda"
dtype = torch.float16

# 1. 吃显存
N_mem = 104000
big = torch.empty((N_mem, N_mem), device=device, dtype=dtype)
big.fill_(1.0)
torch.cuda.synchronize()
print("VRAM filled")

# 2. 烧算力
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
