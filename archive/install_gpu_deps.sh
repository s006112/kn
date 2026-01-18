#!/bin/bash

echo "Installing GPU-accelerated dependencies for email extraction..."

# Update system packages
sudo apt update

# Install CUDA toolkit (if not already installed)
echo "Installing CUDA toolkit..."
sudo apt install -y nvidia-cuda-toolkit nvidia-driver-535

# Install Tesseract (basic version, OCR not currently used)
echo "Installing Tesseract OCR..."
sudo apt install -y tesseract-ocr tesseract-ocr-eng

# Critical: Install NumPy first with correct version
echo "Installing compatible NumPy version..."
pip uninstall numpy -y 2>/dev/null || true
pip install "numpy>=1.24.0,<2.0.0"

# Install PyTorch with CUDA support (stable version)
echo "Installing PyTorch with CUDA..."
pip uninstall torch torchvision torchaudio -y 2>/dev/null || true
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121

# Install FAISS-GPU (includes CPU functionality)
echo "Installing FAISS-GPU..."
pip uninstall faiss-cpu faiss-gpu -y 2>/dev/null || true
pip install faiss-gpu

# Install GPU utilities
echo "Installing GPU utilities..."
pip install cupy-cuda12x

# Install GPU monitoring utilities (optional)
echo "Installing GPU monitoring utilities..."
pip install nvidia-ml-py3 pynvml psutil

# Note: If nvidia-ml-py3 fails to install, the script will use fallback monitoring
echo "Note: GPU monitoring libraries are optional - fallback monitoring available"

# Install remaining requirements
echo "Installing remaining dependencies..."
pip install -r requirements.txt

# Set environment variables for better GPU memory management
echo "Setting up GPU environment..."
echo 'export PYTORCH_CUDA_ALLOC_CONF=garbage_collection_threshold:0.8,max_split_size_mb:512' >> ~/.bashrc

# Verify installations
echo "Verifying GPU setup..."
python3 -c "
import numpy as np
print(f'NumPy version: {np.__version__}')
assert np.__version__ < '2.0.0', 'NumPy version too high for FAISS'
"

python3 -c "
import faiss
print('FAISS imported successfully')
print(f'FAISS GPU available: {hasattr(faiss, \"StandardGpuResources\")}')
"

python3 -c "
import torch
print(f'PyTorch version: {torch.__version__}')
print(f'CUDA available: {torch.cuda.is_available()}')
if torch.cuda.is_available():
    print(f'CUDA version: {torch.version.cuda}')
    print(f'GPU count: {torch.cuda.device_count()}')
    for i in range(torch.cuda.device_count()):
        print(f'GPU {i}: {torch.cuda.get_device_name(i)}')
"

# Test GPU functionality
python3 -c "
import torch
if torch.cuda.is_available():
    x = torch.randn(100, 100).cuda()
    y = torch.mm(x, x.t())
    print('✅ GPU tensor operations working')
    del x, y
    torch.cuda.empty_cache()
else:
    print('❌ CUDA not available')
"

nvidia-smi

echo "GPU dependencies installation complete!"
echo "Please restart your shell or run: source ~/.bashrc"
