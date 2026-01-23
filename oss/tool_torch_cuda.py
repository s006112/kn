import torch
print(torch.__version__, torch.version.cuda)
print(torch.cuda.get_arch_list())